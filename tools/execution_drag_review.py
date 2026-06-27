from __future__ import annotations

"""Execution drag 통계 리뷰 (read-only).

capture_net_review가 gross/net 점추정을 내는 반면, 이 도구는 그 위에 "측정 신뢰도"
레이어를 얹는다 — 6월 단일국면·표본부족 상태에서 net 성과가 0과 구별되는 진짜 엣지인지,
아니면 노이즈인지를 Bootstrap으로 정직하게 판정한다. 산출:

- 시장/모드별 gross / net / cost_drag(net-gross) 점추정 + 비용 분해(fee / fx)
- net 평균의 Bootstrap 95% 신뢰구간 + prob(net_avg>0)  ← 핵심(엣지 유의성)
- 부호전환율: gross>0 인데 net<=0 (비용이 수익을 잡아먹은 거래 비율)  ← execution 개선 타깃
- net 좌측꼬리: p10 / p25 / worst  ← loss_cap 좌측꼬리 진단 연결

입력은 로컬 sqlite(decisions.db)뿐이며 broker/API/Claude 호출이 없다. pnl_pct(gross)·
pnl_pct_net(net)은 실현 체결-청산 라벨이라 측정 전용이며 라이브 게이팅에 쓰지 않는다.
"""

import argparse
import json
import random
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ML_DB = ROOT / "data" / "ml" / "decisions.db"

BOOTSTRAP_SEED = 42  # 재현성 고정(스크립트 재실행 시 동일 CI)


@dataclass
class DragStat:
    market: str
    runtime_mode: str
    n: int
    gross_avg: float
    net_avg: float
    cost_drag_avg: float          # net - gross (음수 = 비용이 깎은 폭)
    fee_avg: float | None
    fx_avg: float | None
    net_win_rate_pct: float
    profit_factor_net: float | str
    net_ci_lo: float | None       # Bootstrap 95% CI 하한
    net_ci_hi: float | None       # 상한
    net_prob_positive: float | None  # net_avg > 0 확률 (Bootstrap)
    sign_flip_rate_pct: float     # gross>0 & net<=0 비율
    net_p10: float | None
    net_p25: float | None
    net_worst: float | None
    verdict: str                  # 신뢰구간 기반 한 줄 판정


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.execute("PRAGMA busy_timeout=8000")
    conn.row_factory = sqlite3.Row
    return conn


def _percentile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = q * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _bootstrap_mean_ci(
    vals: list[float], n_boot: int, conf: float = 0.95
) -> tuple[float | None, float | None, float | None]:
    """net 평균의 Bootstrap 신뢰구간 + 양수 확률. 표본<3이면 None."""
    k = len(vals)
    if k < 3:
        return None, None, None
    rng = random.Random(BOOTSTRAP_SEED)
    means: list[float] = []
    for _ in range(n_boot):
        s = 0.0
        for _ in range(k):
            s += vals[rng.randrange(k)]
        means.append(s / k)
    means.sort()
    lo = _percentile(means, (1 - conf) / 2)
    hi = _percentile(means, (1 + conf) / 2)
    prob_pos = sum(1 for m in means if m > 0) / n_boot
    return (
        round(lo, 4) if lo is not None else None,
        round(hi, 4) if hi is not None else None,
        round(prob_pos, 3),
    )


def _verdict(stat_n: int, ci_lo: float | None, ci_hi: float | None, prob_pos: float | None) -> str:
    if ci_lo is None or ci_hi is None or prob_pos is None:
        return f"표본 부족(n={stat_n}) — 판정 불가"
    if ci_lo > 0:
        return "net>0 유의 (CI 하한 양수)"
    if ci_hi < 0:
        return "net<0 유의 (CI 상한 음수)"
    return f"0과 구별 안 됨 — 노이즈/증거불충분 (prob(net>0)={prob_pos})"


def load_closed(
    ml_db: Path, runtime_mode: str | None, since: str | None
) -> list[dict[str, Any]]:
    conn = _connect_ro(ml_db)
    try:
        sql = (
            "SELECT market, runtime_mode, session_date, ticker, "
            "pnl_pct, pnl_pct_net, fee_pct_round_trip, fx_change_pct "
            "FROM v2_canonical_performance "
            "WHERE closed=1 AND pnl_pct IS NOT NULL AND pnl_pct_net IS NOT NULL"
        )
        params: list[Any] = []
        if runtime_mode:
            sql += " AND runtime_mode=?"
            params.append(runtime_mode)
        if since:
            sql += " AND session_date >= ?"
            params.append(since)
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def compute(rows: list[dict[str, Any]], n_boot: int) -> DragStat | None:
    if not rows:
        return None
    market = str(rows[0].get("market") or "?")
    mode = str(rows[0].get("runtime_mode") or "?")
    gross = [float(r["pnl_pct"]) for r in rows]
    net = [float(r["pnl_pct_net"]) for r in rows]
    fees = [float(r["fee_pct_round_trip"]) for r in rows if r.get("fee_pct_round_trip") is not None]
    fxs = [float(r["fx_change_pct"]) for r in rows if r.get("fx_change_pct") is not None]
    drag = [n - g for g, n in zip(gross, net)]
    net_pos = [x for x in net if x > 0]
    net_neg = [x for x in net if x <= 0]
    pf = round(sum(net_pos) / abs(sum(net_neg)), 2) if net_neg and sum(net_neg) != 0 else "inf"
    flips = sum(1 for g, n in zip(gross, net) if g > 0 and n <= 0)
    ci_lo, ci_hi, prob_pos = _bootstrap_mean_ci(net, n_boot)
    net_sorted = sorted(net)
    return DragStat(
        market=market,
        runtime_mode=mode,
        n=len(rows),
        gross_avg=round(mean(gross), 4),
        net_avg=round(mean(net), 4),
        cost_drag_avg=round(mean(drag), 4),
        fee_avg=round(mean(fees), 4) if fees else None,
        fx_avg=round(mean(fxs), 4) if fxs else None,
        net_win_rate_pct=round(len(net_pos) / len(net) * 100, 1),
        profit_factor_net=pf,
        net_ci_lo=ci_lo,
        net_ci_hi=ci_hi,
        net_prob_positive=prob_pos,
        sign_flip_rate_pct=round(flips / len(rows) * 100, 1),
        net_p10=round(_percentile(net_sorted, 0.10), 4) if net_sorted else None,
        net_p25=round(_percentile(net_sorted, 0.25), 4) if net_sorted else None,
        net_worst=round(min(net), 4) if net else None,
        verdict=_verdict(len(rows), ci_lo, ci_hi, prob_pos),
    )


def _print_human(stat: DragStat) -> None:
    print(f"\n[{stat.market} / {stat.runtime_mode}] n={stat.n}")
    print(f"  gross avg : {stat.gross_avg:+.4f}%")
    print(f"  net   avg : {stat.net_avg:+.4f}%   (cost drag {stat.cost_drag_avg:+.4f}%p)")
    fee_s = f"{stat.fee_avg:+.4f}%" if stat.fee_avg is not None else "n/a"
    fx_s = f"{stat.fx_avg:+.4f}%" if stat.fx_avg is not None else "n/a"
    print(f"  비용분해  : fee {fee_s} / fx {fx_s}")
    print(f"  net 승률  : {stat.net_win_rate_pct}%   PF(net) {stat.profit_factor_net}")
    if stat.net_ci_lo is not None:
        print(
            f"  net 95%CI : [{stat.net_ci_lo:+.4f}, {stat.net_ci_hi:+.4f}]%   "
            f"prob(net>0)={stat.net_prob_positive}"
        )
    else:
        print("  net 95%CI : 표본 부족(<3) — 산출 불가")
    print(f"  부호전환  : {stat.sign_flip_rate_pct}%  (gross>0 이나 net<=0 = 비용이 수익 잡아먹음)")
    p10 = f"{stat.net_p10:+.4f}%" if stat.net_p10 is not None else "n/a"
    p25 = f"{stat.net_p25:+.4f}%" if stat.net_p25 is not None else "n/a"
    worst = f"{stat.net_worst:+.4f}%" if stat.net_worst is not None else "n/a"
    print(f"  net 좌측꼬리: p10 {p10} / p25 {p25} / worst {worst}")
    print(f"  판정      : {stat.verdict}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Execution drag 통계 리뷰 (read-only)")
    ap.add_argument("--market", choices=["KR", "US", "both"], default="both")
    ap.add_argument("--runtime-mode", default="live", help="live/paper/all (기본 live)")
    ap.add_argument("--since", default=None, help="세션일 하한 YYYY-MM-DD (옵션)")
    ap.add_argument("--bootstrap", type=int, default=1000, help="Bootstrap 반복 횟수")
    ap.add_argument("--ml-db", default=str(DEFAULT_ML_DB))
    ap.add_argument("--json", action="store_true", help="JSON 출력")
    args = ap.parse_args(argv)

    ml_db = Path(args.ml_db)
    if not ml_db.exists():
        print(f"[ERR] DB 없음: {ml_db}")
        return 2

    mode = None if args.runtime_mode == "all" else args.runtime_mode
    markets = ["KR", "US"] if args.market == "both" else [args.market]

    all_rows = load_closed(ml_db, mode, args.since)
    results: list[DragStat] = []
    for mkt in markets:
        rows = [r for r in all_rows if str(r.get("market") or "").upper() == mkt]
        stat = compute(rows, args.bootstrap)
        if stat is not None:
            results.append(stat)

    if args.json:
        print(json.dumps([asdict(s) for s in results], ensure_ascii=False, indent=2))
    else:
        scope = f"mode={args.runtime_mode}" + (f", since={args.since}" if args.since else "")
        print(f"=== Execution drag 통계 리뷰 ({scope}, bootstrap={args.bootstrap}) ===")
        if not results:
            print("해당 조건의 청산 표본 없음.")
        for s in results:
            _print_human(s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
