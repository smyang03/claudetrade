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
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean, variance
from typing import Any

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
    ap.add_argument("--sel-db", default=str(DEFAULT_SEL_DB))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    sel_db = Path(args.sel_db)
    if not sel_db.exists():
        print(f"[ERR] DB 없음: {sel_db}")
        return 2

    mode = None if args.bot_mode == "all" else args.bot_mode
    markets = ["KR", "US"] if args.market == "both" else [args.market]
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
