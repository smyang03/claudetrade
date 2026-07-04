#!/usr/bin/env python3
"""reward_risk 위생 enforce(A+옵션1+#3~5) 효과 리뷰 (read-only).

2026-07-01 10:21 재시작으로 6개 변경 활성: reward_risk 분모정직화·sell_target캡4%·
워크드예시·feedback lookahead·RR경로1.5·교훈중립화. 매수 감소가 net 개선인지 추적.

측정: (a) before/after PathB plan 등록 vs reward_risk 거부 = 매수율 변화
      (b) reward_risk_below_minimum로 거부된 셋업의 forward_3d = 거른게 본전이하였나(가설검증)
      (c) 청산 net before/after (누적되며 채워짐)
로그(KST)+ticker_selection_log(forward)+decisions.db(net). 매매·config 무접촉.
"""
from __future__ import annotations
import argparse
import glob
import re
import sqlite3
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOGS = ROOT / "logs" / "system"
TSDB = ROOT / "data" / "ticker_selection_log.db"
MLDB = ROOT / "data" / "ml" / "decisions.db"

RE_REJECT = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}).*\[PathB 플랜 무효\] (\w+) (\w+).*사유=(\S+)")
RE_PLAN = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}).*\[PathB plan\]")


def parse_logs(since_date: str):
    rejects = []  # (ts, market, ticker, reasons)
    plans = []    # ts
    for lf in sorted(glob.glob(str(LOGS / "live_trading_2026*.log"))):
        d = Path(lf).stem.replace("live_trading_", "")
        if d < since_date.replace("-", ""):
            continue
        with open(lf, encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = RE_REJECT.match(line)
                if m:
                    dt, tm, mkt, tk, reasons = m.groups()
                    rejects.append((f"{dt} {tm}", mkt, tk, reasons.split(",")))
                    continue
                p = RE_PLAN.match(line)
                if p:
                    plans.append(f"{p.group(1)} {p.group(2)}")
    return rejects, plans


def forward_of(rejected):
    """거부된 (date,ticker)의 forward_3d 조회."""
    if not TSDB.exists():
        return []
    c = sqlite3.connect(f"file:{TSDB}?mode=ro", uri=True, timeout=10)
    c.execute("PRAGMA busy_timeout=5000")
    out = []
    for ts, mkt, tk, reasons in rejected:
        d = ts[:10]
        r = c.execute(
            "SELECT AVG(forward_3d) FROM ticker_selection_log WHERE date=? AND ticker=? AND forward_3d IS NOT NULL",
            (d, tk),
        ).fetchone()
        if r and r[0] is not None:
            out.append(r[0])
    c.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--activation", default="2026-07-01 10:21:08", help="enforce 활성 시각(KST, 봇재시작)")
    ap.add_argument("--since", default="2026-06-24", help="분석 시작일")
    args = ap.parse_args()
    act = args.activation

    rejects, plans = parse_logs(args.since)
    print(f"enforce 활성: {act} (봇 재시작)\n")

    def split(items, key=lambda x: x):
        return [x for x in items if key(x) < act], [x for x in items if key(x) >= act]

    rej_b, rej_a = split(rejects, key=lambda x: x[0])
    pln_b, pln_a = split(plans)

    print("=== (a) 매수율: PathB plan 등록 vs 거부 (before/after) ===")
    for lbl, rj, pl in [("before", rej_b, pln_b), ("after ", rej_a, pln_a)]:
        rr_rej = sum(1 for _, _, _, rs in rj if "reward_risk_below_minimum" in rs)
        tot = len(pl) + len(rj)
        rate = 100 * len(pl) / tot if tot else 0
        print(f"  {lbl}: 등록 {len(pl):3} / 거부 {len(rj):3}(그중 reward_risk {rr_rej}) → 등록율 {rate:.0f}%")

    print("\n=== (b) 거부 사유 분포 (after) ===")
    ca = Counter(r for _, _, _, rs in rej_a for r in rs)
    for reason, n in ca.most_common(8):
        print(f"  {reason}: {n}")

    print("\n=== (c) reward_risk로 거부된 셋업의 forward_3d (거른게 본전이하였나) ===")
    for lbl, rj in [("before", rej_b), ("after", rej_a)]:
        rr_only = [(ts, m, t, rs) for ts, m, t, rs in rj if "reward_risk_below_minimum" in rs]
        fwd = forward_of(rr_only)
        if fwd:
            neg = 100 * sum(1 for x in fwd if x < 0) / len(fwd)
            print(f"  {lbl}: reward_risk 거부 {len(rr_only)}건, forward 확보 {len(fwd)} | 평균 {st.mean(fwd):+.2f}% 음수 {neg:.0f}%")
            print(f"         → 음수면 '거른게 옳음'(net개선 지지), 양수면 '기회손실'")
        else:
            print(f"  {lbl}: reward_risk 거부 {len(rr_only)}건, forward 미확보(3일 후 채워짐)")

    print("\n=== (d) 청산 net before/after (누적 대기) ===")
    if MLDB.exists():
        c = sqlite3.connect(f"file:{MLDB}?mode=ro", uri=True, timeout=10)
        c.execute("PRAGMA busy_timeout=5000")
        act_utc = "2026-07-01T01:21:00"  # 10:21 KST = 01:21 UTC (closed_at은 UTC)
        for lbl, op in [("before", "<"), ("after", ">=")]:
            r = c.execute(
                f"SELECT COUNT(*), AVG(pnl_pct_net), AVG(pnl_pct) FROM v2_learning_performance "
                f"WHERE closed=1 AND closed_at IS NOT NULL AND closed_at {op} ? AND closed_at>=? ",
                (act_utc, "2026-06-24T00:00:00"),
            ).fetchone()
            n = r[0] or 0
            print(f"  {lbl}: 청산 {n}건 net평균 {r[1] if r[1] is not None else 'N/A'} gross {r[2] if r[2] is not None else 'N/A'}")
        c.close()
    print("\n주: after는 재시작 직후라 표본 적음. (a)매수율↓·(c)거부forward음수·(d)net개선이 3지표. 며칠 누적 후 판독.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
