"""hold advisor profit_guard outcome A/B 리뷰 (#2/#3, 2026-06-23).

profit_guard prior ON(2026-06-16~) 전후로 hold advisor의 HOLD/SELL 판단과 그 후속
실현 net을 비교한다. '익절 우선' prior가 net+인지(좋은 익절) 아니면 좋은 러너를
조기 절단했는지 라이브로 검증·kill 판정하는 모니터.

- 정식 lesson_validation score_cell 자동편입은 청산-outcome 차원 재작업이 필요(TODO #4).
  이 도구는 그 전까지 2주 단위 수동 A/B kill 판정 근거를 제공한다.
- read-only. 봇/대시보드 실행 안 함. 로그 + decisions.db만 읽는다.
"""
from __future__ import annotations

import argparse
import glob
import json
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"
PROFIT_GUARD_ON_DATE = "2026-06-16"  # _PROFIT_GUARD_PRIOR ON 분기일


def load_decisions(market: str = "") -> list[dict]:
    rows: list[dict] = []
    for f in sorted(glob.glob(str(ROOT / "logs" / "hold_advisor" / "decisions_2026-*.jsonl"))):
        for line in open(f, encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if market and d.get("market") != market:
                continue
            votes = d.get("votes") or {}
            hm = ""
            for k in ("neutral", "bull", "bear"):
                v = votes.get(k) or {}
                if v.get("hold_mode"):
                    hm = v["hold_mode"]
                    break
            ctx = d.get("pathb_revenue_path_context") or {}
            rows.append({
                "ts": d.get("ts", ""),
                "ticker": d.get("ticker"),
                "decision": d.get("decision"),
                "hold_mode": hm,
                "judge_pnl": d.get("pnl_pct"),
                "path_run_id": ctx.get("path_run_id") or "",
                "market": d.get("market"),
            })
    return rows


def load_realized() -> dict:
    c = sqlite3.connect(f"file:{ML_DB}?mode=ro", uri=True)
    real = {}
    for r in c.execute(
        "SELECT path_run_id,pnl_pct,close_reason,market FROM v2_learning_performance "
        "WHERE closed=1 AND pnl_pct IS NOT NULL"
    ):
        if r[0]:
            real[r[0]] = {"rp": r[1], "cr": r[2], "mkt": r[3]}
    return real


def main() -> int:
    ap = argparse.ArgumentParser(description="hold advisor profit_guard outcome A/B")
    ap.add_argument("--market", default="", help="US/KR 필터(기본 전체)")
    args = ap.parse_args()

    decs = load_decisions(args.market)
    real = load_realized()

    # path_run별 '마지막' 판단(최종 청산 직전 의사) 채택
    last: dict[str, dict] = {}
    for d in decs:
        prid = d["path_run_id"]
        if not prid:
            continue
        if prid not in last or d["ts"] > last[prid]["ts"]:
            last[prid] = d

    print(f"# hold advisor profit_guard A/B (분기일 {PROFIT_GUARD_ON_DATE}, market={args.market or 'ALL'})")
    print(f"# 표본: path_run {len(last)}개 중 실현 매칭만 집계\n")

    buckets = defaultdict(lambda: {"HOLD": [], "SELL": []})
    for prid, d in last.items():
        rr = real.get(prid)
        if not rr:
            continue
        era = "post(ON)" if (d["ts"][:10] >= PROFIT_GUARD_ON_DATE) else "pre(OFF)"
        if d["decision"] in ("HOLD", "SELL"):
            buckets[era][d["decision"]].append(rr["rp"])

    print("## profit_guard ON/OFF 전후 — 판단별 실현 net")
    for era in ("pre(OFF)", "post(ON)"):
        for dec in ("HOLD", "SELL"):
            v = buckets[era][dec]
            if v:
                wr = sum(1 for x in v if x > 0) / len(v) * 100
                print(f"  {era:9} {dec}: n={len(v):3} 실현평균 {statistics.mean(v):+.2f}% 승률 {wr:.0f}%")

    print("\n## hold_mode별 outcome (전체)")
    bym = defaultdict(lambda: defaultdict(list))
    for prid, d in last.items():
        rr = real.get(prid)
        if not rr:
            continue
        bym[d["hold_mode"] or "(none)"][d["decision"]].append(rr["rp"])
    for m, dd in sorted(bym.items(), key=lambda x: -sum(len(v) for v in x[1].values())):
        parts = []
        for dec in ("HOLD", "SELL"):
            v = dd.get(dec, [])
            if v:
                parts.append(f"{dec} n={len(v)} {statistics.mean(v):+.2f}%/{sum(1 for x in v if x>0)/len(v)*100:.0f}%")
        if parts:
            print(f"  {m:18} " + " | ".join(parts))

    print(
        "\n## kill 판정 가이드\n"
        "- post(ON) SELL 실현이 음수이거나 같은 시기 HOLD보다 나쁘면 → profit_guard가 좋은 포지션을\n"
        "  조기 절단(역효과) 의심 → HOLD_ADVISOR_PROFIT_GUARD_ENABLED=false 롤백 검토.\n"
        "- post(ON) SELL 실현이 양수이고 HOLD 대비 우위면 → 익절 규율 유효, 유지.\n"
        "- 표본 < ~15(시장별)이면 판정 보류(축적 계속). 6/16 이후 표본은 강세장 confound 분리 위해\n"
        "  국면(bull/bear)과 교차 확인 권장."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
