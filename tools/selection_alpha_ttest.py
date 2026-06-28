from __future__ import annotations

"""Selection 알파 유의성 검정 (Welch t-test, read-only).

ticker_selection_log의 forward return으로 "선택이 무선택 대비 유의한 알파가 있나"를
Welch t-test(등분산 미가정)로 정직하게 판정한다. 두 비교를 제공:

  A) selection 판정 알파 : trade_ready=1  vs  trade_ready=0
     -> Claude selection의 trade_ready 승격이 실제로 더 나은 종목을 고르나?
  B) execution 체결 알파 : traded=1  vs  (trade_ready=1 & traded=0)=watched
     -> trade_ready 중 실제 체결된 게 미체결보다 나았나? (표본 작으면 노이즈)

forward_* 는 사후 감사 라벨이라 측정 전용(라이브 게이팅 금지). 주의: trade_ready 종목은
본질적으로 더 좋게 선별됐을 수 있어(selection bias) A의 차이를 "추가 알파"로 단정하지
말 것 — 검정은 차이의 통계적 유의성만 보고한다. 입력은 로컬 sqlite뿐, 외부 호출 없음.
"""

import argparse
import json
import math
import random
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean, variance
from typing import Any

SPREAD_SEED = 42  # 부트스트랩 재현성 고정

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEL_DB = ROOT / "data" / "ticker_selection_log.db"
MIN_N = 5  # 그룹당 최소 표본(미만이면 판정 불가)

try:
    import scipy.stats as _st  # type: ignore
    _HAVE_SCIPY = True
except Exception:  # pragma: no cover
    _st = None
    _HAVE_SCIPY = False


def _t_sf(t_abs: float, df: float) -> float:
    """t분포 우측 꼬리. scipy 있으면 정확, 없으면 정규근사."""
    if _HAVE_SCIPY:
        return float(_st.t.sf(t_abs, df))
    # 정규근사: df 작으면 부정확하므로 verdict에 근사 표기
    return 0.5 * math.erfc(t_abs / math.sqrt(2))


@dataclass
class TTest:
    label: str
    group_a: str
    group_b: str
    n_a: int
    n_b: int
    mean_a: float | None
    mean_b: float | None
    diff: float | None        # mean_a - mean_b
    t_stat: float | None
    df: float | None
    p_value: float | None
    cohens_d: float | None
    p_method: str             # scipy / normal-approx / n/a
    verdict: str


def welch(a: list[float], b: list[float], label: str, ga: str, gb: str) -> TTest:
    na, nb = len(a), len(b)
    if na < MIN_N or nb < MIN_N:
        return TTest(label, ga, gb, na, nb, None, None, None, None, None, None, None,
                     "n/a", f"표본 부족(n_a={na}, n_b={nb}, 최소 {MIN_N}) — 판정 불가")
    ma, mb = mean(a), mean(b)
    va, vb = variance(a), variance(b)
    se2 = va / na + vb / nb
    if se2 <= 0:
        return TTest(label, ga, gb, na, nb, round(ma, 4), round(mb, 4), round(ma - mb, 4),
                     None, None, None, None, "n/a", "분산 0 — 판정 불가")
    t = (ma - mb) / math.sqrt(se2)
    df = (se2 ** 2) / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    p = 2.0 * _t_sf(abs(t), df)
    pooled_sd = math.sqrt((va * (na - 1) + vb * (nb - 1)) / (na + nb - 2))
    d = (ma - mb) / pooled_sd if pooled_sd > 0 else None
    method = "scipy" if _HAVE_SCIPY else "normal-approx"
    if p < 0.05:
        direction = ga if ma > mb else gb
        verdict = f"유의 (p={p:.4f}) — {direction} 우위"
    else:
        verdict = f"유의차 없음 — 증거불충분 (p={p:.4f})"
    return TTest(label, ga, gb, na, nb, round(ma, 4), round(mb, 4), round(ma - mb, 4),
                 round(t, 4), round(df, 1), round(p, 4),
                 round(d, 4) if d is not None else None, method, verdict)


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


def _fetch(conn: sqlite3.Connection, fwd: str, where: str, params: list[Any]) -> list[float]:
    sql = (
        f"SELECT {fwd} FROM ticker_selection_log "
        f"WHERE {fwd} IS NOT NULL AND {where}"
    )
    return [float(r[0]) for r in conn.execute(sql, params).fetchall()]


def run(sel_db: Path, market: str, fwd: str, bot_mode: str | None, since: str | None) -> list[TTest]:
    conn = _connect_ro(sel_db)
    try:
        base = "market=?"
        params: list[Any] = [market]
        if bot_mode:
            base += " AND bot_mode=?"
            params.append(bot_mode)
        if since:
            base += " AND date>=?"
            params.append(since)

        ready = _fetch(conn, fwd, base + " AND trade_ready=1", list(params))
        not_ready = _fetch(conn, fwd, base + " AND trade_ready=0", list(params))
        traded = _fetch(conn, fwd, base + " AND traded=1", list(params))
        watched = _fetch(conn, fwd, base + " AND trade_ready=1 AND traded=0", list(params))

        return [
            welch(ready, not_ready, f"[{market}] A) selection 판정 알파 ({fwd})",
                  "trade_ready=1", "trade_ready=0"),
            welch(traded, watched, f"[{market}] B) execution 체결 알파 ({fwd})",
                  "traded=1", "watched(ready&!traded)"),
        ]
    finally:
        conn.close()


@dataclass
class StratumSpread:
    market: str
    stratify: str          # month / regime
    stratum: str
    n_ready: int
    n_pool: int
    mean_ready: float | None
    mean_pool: float | None
    spread: float | None       # mean_ready - mean_pool (풀 대비 상대 선별력)
    abs_ready: float | None    # ready 절대수익(레짐/베타 노출 — spread와 분리해서 본다)
    ci_lo: float | None        # spread Bootstrap 95% CI 하한
    ci_hi: float | None
    prob_spread_pos: float | None
    verdict: str


def _bootstrap_spread_ci(
    ready: list[float], pool: list[float], n_boot: int, conf: float = 0.95
) -> tuple[float | None, float | None, float | None]:
    """spread(mean_ready - mean_pool)의 Bootstrap CI + 양수확률. 각 그룹<3이면 None."""
    kr, kp = len(ready), len(pool)
    if kr < 3 or kp < 3:
        return None, None, None
    rng = random.Random(SPREAD_SEED)
    diffs: list[float] = []
    for _ in range(n_boot):
        ra = sum(ready[rng.randrange(kr)] for _ in range(kr)) / kr
        pa = sum(pool[rng.randrange(kp)] for _ in range(kp)) / kp
        diffs.append(ra - pa)
    diffs.sort()
    lo = diffs[int((1 - conf) / 2 * n_boot)]
    hi = diffs[int((1 + conf) / 2 * n_boot)]
    pp = sum(1 for d in diffs if d > 0) / n_boot
    return round(lo, 4), round(hi, 4), round(pp, 3)


def _fetch_grouped(conn: sqlite3.Connection, fwd: str, group_expr: str,
                   where: str, params: list[Any]) -> dict[str, list[float]]:
    sql = (f"SELECT {group_expr} AS g, {fwd} FROM ticker_selection_log "
           f"WHERE {fwd} IS NOT NULL AND {where}")
    out: dict[str, list[float]] = defaultdict(list)
    for g, v in conn.execute(sql, params).fetchall():
        out[str(g)].append(float(v))
    return out


def run_stratified(sel_db: Path, market: str, fwd: str, bot_mode: str | None,
                   since: str | None, stratify: str, n_boot: int) -> list[StratumSpread]:
    group_expr = "substr(date,1,7)" if stratify == "month" else "consensus_mode"
    conn = _connect_ro(sel_db)
    try:
        base = "market=?"
        params: list[Any] = [market]
        if bot_mode:
            base += " AND bot_mode=?"
            params.append(bot_mode)
        if since:
            base += " AND date>=?"
            params.append(since)
        ready_g = _fetch_grouped(conn, fwd, group_expr, base + " AND trade_ready=1", list(params))
        pool_g = _fetch_grouped(conn, fwd, group_expr, base + " AND trade_ready=0", list(params))
    finally:
        conn.close()

    out: list[StratumSpread] = []
    for g in sorted(set(ready_g) | set(pool_g)):
        r, p = ready_g.get(g, []), pool_g.get(g, [])
        mr = round(mean(r), 4) if r else None
        mp = round(mean(p), 4) if p else None
        spread = round(mr - mp, 4) if (mr is not None and mp is not None) else None
        lo, hi, pp = _bootstrap_spread_ci(r, p, n_boot)
        if lo is None:
            verdict = f"표본부족(ready{len(r)}/pool{len(p)})"
        elif lo > 0:
            verdict = "선별력 유의 (spread CI 하한 양수)"
        elif hi < 0:
            verdict = "역선별 (CI 상한 음수)"
        else:
            verdict = f"증거불충분 (prob spread>0={pp})"
        out.append(StratumSpread(market, stratify, g, len(r), len(p), mr, mp, spread, mr, lo, hi, pp, verdict))
    return out


def _print_stratum(s: StratumSpread) -> None:
    mr = f"{s.mean_ready:+.2f}%" if s.mean_ready is not None else "n/a"
    mp = f"{s.mean_pool:+.2f}%" if s.mean_pool is not None else "n/a"
    sp = f"{s.spread:+.2f}%p" if s.spread is not None else "n/a"
    ci = f"[{s.ci_lo:+.2f}, {s.ci_hi:+.2f}]" if s.ci_lo is not None else "표본부족"
    print(f"  [{s.market}] {s.stratum:14} ready n={s.n_ready:<4} pool n={s.n_pool:<5} "
          f"| ready절대 {mr} pool {mp} | spread {sp} CI {ci} | {s.verdict}")


def _print_human(t: TTest) -> None:
    print(f"\n{t.label}")
    print(f"  {t.group_a}: n={t.n_a}" + (f" mean={t.mean_a:+.4f}%" if t.mean_a is not None else ""))
    print(f"  {t.group_b}: n={t.n_b}" + (f" mean={t.mean_b:+.4f}%" if t.mean_b is not None else ""))
    if t.diff is not None:
        print(f"  차이(a-b): {t.diff:+.4f}%p" + (f"  Cohen's d={t.cohens_d}" if t.cohens_d is not None else ""))
    if t.t_stat is not None:
        print(f"  Welch t={t.t_stat}  df={t.df}  p={t.p_value} ({t.p_method})")
    print(f"  판정: {t.verdict}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Selection 알파 Welch t-test (read-only)")
    ap.add_argument("--market", choices=["KR", "US", "both"], default="both")
    ap.add_argument("--horizon", choices=["forward_1d", "forward_3d", "forward_5d"], default="forward_3d")
    ap.add_argument("--bot-mode", default="live", help="live/paper/all (기본 live)")
    ap.add_argument("--since", default=None, help="날짜 하한 YYYY-MM-DD (옵션)")
    ap.add_argument("--stratify", choices=["none", "month", "regime"], default="none",
                    help="층화: month(월별)/regime(consensus_mode)별 풀 대비 상대 spread + Bootstrap CI")
    ap.add_argument("--bootstrap", type=int, default=2000, help="spread Bootstrap 반복(층화 모드)")
    ap.add_argument("--sel-db", default=str(DEFAULT_SEL_DB))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    sel_db = Path(args.sel_db)
    if not sel_db.exists():
        print(f"[ERR] DB 없음: {sel_db}")
        return 2

    mode = None if args.bot_mode == "all" else args.bot_mode
    markets = ["KR", "US"] if args.market == "both" else [args.market]

    if args.stratify != "none":
        strata: list[StratumSpread] = []
        for mkt in markets:
            strata.extend(run_stratified(sel_db, mkt, args.horizon, mode, args.since,
                                         args.stratify, args.bootstrap))
        if args.json:
            print(json.dumps([asdict(s) for s in strata], ensure_ascii=False, indent=2))
        else:
            scope = f"bot_mode={args.bot_mode}, horizon={args.horizon}, stratify={args.stratify}"
            print(f"=== Selection 상대 spread 층화 ({scope}) ===")
            print("  (spread=풀 대비 선별력, 절대수익은 레짐/베타 — 분리해서 본다)")
            for s in strata:
                _print_stratum(s)
        return 0

    results: list[TTest] = []
    for mkt in markets:
        results.extend(run(sel_db, mkt, args.horizon, mode, args.since))

    if args.json:
        print(json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2))
    else:
        scope = f"bot_mode={args.bot_mode}, horizon={args.horizon}"
        if args.since:
            scope += f", since={args.since}"
        if not _HAVE_SCIPY:
            scope += " [scipy 없음 → 정규근사 p값]"
        print(f"=== Selection 알파 Welch t-test ({scope}) ===")
        for r in results:
            _print_human(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
