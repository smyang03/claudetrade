"""BUY_READY 성숙 게이트 관측기 — 시장별 30건 양수 전 확대금지 판정 (Codex P0-③). read-only.

Codex 오프라인 검토 권고: "시장별 고유 성숙 거래 최소 30건, 비용 후 평균·중앙값 모두
양수가 될 때까지 실주문 확대 금지." 라이브 게이트로 차단하면 운영자의 도전 지시와 상충하고
위험하므로, 여기서는 그 판정을 데이터로 자동 집계만 한다(실주문 무영향). 확대 여부는 운영자가
이 판정을 보고 결정한다.

즉시매수(BUY_READY)=strategy 'claude_price_a'. 눌림(PULLBACK_WAIT)=strategy 'claude_price'.
성숙=pnl_pct_net(비용 후 net)이 채워진 청산 완료 건. 게이트 통과=n>=MIN AND 평균>0 AND 중앙>0.
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECISIONS_DB = ROOT / "data" / "ml" / "decisions.db"


def _net_rows(conn: sqlite3.Connection, strategy_like: str, market: str) -> list[float]:
    rows = conn.execute(
        "SELECT pnl_pct_net FROM v2_learning_performance "
        "WHERE strategy LIKE ? AND market=? AND pnl_pct_net IS NOT NULL",
        (strategy_like, market),
    ).fetchall()
    return [float(r[0]) for r in rows if r[0] is not None]


def _judge(nets: list[float], min_n: int) -> dict:
    n = len(nets)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "positive_rate": None,
                "gate_pass": False, "verdict": f"성숙 0건 — 확대금지(최소 {min_n})"}
    mean = statistics.mean(nets)
    median = statistics.median(nets)
    pos = sum(1 for x in nets if x > 0) / n
    gate = n >= min_n and mean > 0 and median > 0
    if gate:
        verdict = f"게이트 통과 — 확대 검토 가능(운영자)"
    elif n < min_n:
        verdict = f"성숙 {n}<{min_n}건 — 확대금지(표본 부족)"
    else:
        verdict = f"평균 {mean:+.3f}·중앙 {median:+.3f} 중 음수 — 확대금지"
    return {"n": n, "mean": round(mean, 4), "median": round(median, 4),
            "positive_rate": round(pos, 3), "gate_pass": gate, "verdict": verdict}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--min-n", type=int, default=30)
    p.add_argument("--db", default=str(DECISIONS_DB))
    args = p.parse_args()

    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA busy_timeout=5000")
    print(f"=== BUY_READY 성숙 게이트 (최소 {args.min_n}건, 비용후 net 평균·중앙 모두 양수) ===\n")
    for label, strat in [("BUY_READY 즉시매수", "%claude_price_a%"),
                          ("PULLBACK_WAIT 눌림", "claude_price")]:
        print(f"[{label}] strategy LIKE '{strat}'")
        for market in ("US", "KR"):
            # 눌림은 claude_price_a도 잡히지 않도록 정확 매칭
            if strat == "claude_price":
                nets = [float(r[0]) for r in conn.execute(
                    "SELECT pnl_pct_net FROM v2_learning_performance "
                    "WHERE strategy='claude_price' AND market=? AND pnl_pct_net IS NOT NULL",
                    (market,)).fetchall() if r[0] is not None]
            else:
                nets = _net_rows(conn, strat, market)
            j = _judge(nets, args.min_n)
            mean_s = f"{j['mean']:+.3f}" if j["mean"] is not None else "  -  "
            med_s = f"{j['median']:+.3f}" if j["median"] is not None else "  -  "
            pos_s = f"{j['positive_rate']*100:.0f}%" if j["positive_rate"] is not None else " - "
            gate = "✓통과" if j["gate_pass"] else "✗금지"
            print(f"  {market}: n={j['n']:>3} 평균net={mean_s} 중앙={med_s} 양수율={pos_s} [{gate}] {j['verdict']}")
        print()
    conn.close()
    print("※ 판정만 한다 — 실주문 확대는 운영자 결정. 강세 세션으로 성숙 표본이 쌓이면 재실행.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
