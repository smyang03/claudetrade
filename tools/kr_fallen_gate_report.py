"""KR 급락 레인 승격 게이트 3파전 리포트 (read-only).

spec §5-1(2026-08-03 운영자 승인): shadow 원장의 정산 건을 세 규칙의 가상
포트폴리오로 재구성해 같은 기준으로 비교한다. 판정은 forward만 — 이 도구는
집계·표시만 하고 아무것도 바꾸지 않는다.

  R1 현행 8조건  = pass_all
  R2 대안A       = 급락밴드(원장 수록 전제) AND ma20_disc<=-25 AND rv20<=6.24
  R3 대안B       = R1 OR R2

사용: python tools/kr_fallen_gate_report.py
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "shadow" / "kr_fallen_shadow.jsonl"

GATE_MIN_SESSIONS = 15   # 영업일
GATE_MIN_SETTLED = 15    # 규칙별 정산 건
GATE_MIN_WEEKS = 2       # 주간 분산


def rule_flags(row: dict) -> dict[str, bool]:
    feats = row.get("feats") or {}
    r1 = bool(row.get("pass_all"))
    disc = feats.get("ma20_disc")
    rv20 = feats.get("rv20")
    r2 = disc is not None and rv20 is not None and float(disc) <= -25.0 and float(rv20) <= 6.24
    return {"R1_8조건": r1, "R2_할인저변동": r2, "R3_합집합": r1 or r2}


def main() -> int:
    if not LEDGER.exists():
        print("원장 없음:", LEDGER)
        return 1
    rows = [json.loads(x) for x in LEDGER.read_text(encoding="utf-8").splitlines() if x.strip()]
    sessions = sorted({r["session_date"] for r in rows})
    print(f"원장 {len(rows)}행 / 관측 세션 {len(sessions)}일 ({sessions[0]} ~ {sessions[-1]})")
    print(f"게이트 기준: {GATE_MIN_SESSIONS}영업일 AND 규칙별 정산 {GATE_MIN_SETTLED}건 AND {GATE_MIN_WEEKS}개 주간 분산\n")

    stats: dict[str, dict] = {}
    for r in rows:
        flags = rule_flags(r)
        for rule, hit in flags.items():
            if not hit:
                continue
            s = stats.setdefault(rule, {"cand": 0, "settled": [], "weeks": defaultdict(list)})
            s["cand"] += 1
            if r.get("status") == "SETTLED" and r.get("net_pct") is not None:
                net = float(r["net_pct"])
                s["settled"].append(net)
                wk = datetime.strptime(r["session_date"], "%Y-%m-%d").strftime("%G-W%V")
                s["weeks"][wk].append(net)

    for rule in ("R1_8조건", "R2_할인저변동", "R3_합집합"):
        s = stats.get(rule) or {"cand": 0, "settled": [], "weeks": {}}
        nets = s["settled"]
        n = len(nets)
        line = f"{rule:12s} 후보 {s['cand']:4d} | 정산 {n:3d}"
        if n:
            g = sum(x for x in nets if x > 0)
            l = -sum(x for x in nets if x <= 0)
            pf = round(g / l, 2) if l > 0 else float("inf")
            wr = 100 * sum(1 for x in nets if x > 0) / n
            line += f" | 평균 {sum(nets)/n:+.2f}% | 승률 {wr:.0f}% | PF {pf}"
            wk = {k: round(sum(v)/len(v), 2) for k, v in sorted(s["weeks"].items())}
            line += f" | 주간 {wk}"
        gate_ok = (
            len(sessions) >= GATE_MIN_SESSIONS
            and n >= GATE_MIN_SETTLED
            and len(s["weeks"]) >= GATE_MIN_WEEKS
        )
        print(line + ("  <<GATE 충족>>" if gate_ok else ""))

    print("\n(판정은 운영자 — 이 리포트는 집계만 한다. in-sample 참고치와 비교 금지, forward만 본다.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
