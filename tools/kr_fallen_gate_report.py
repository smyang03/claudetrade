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
CACHE = ROOT / "data" / "analysis" / "kr_fallen_price_cache.json"
BENCH_TICKER = "069500"  # KODEX200 — 알파·국면 산출용 (사전등록 문서 기준)

GATE_MIN_SESSIONS = 15   # 영업일
GATE_MIN_SETTLED = 15    # 규칙별 정산 건
GATE_MIN_WEEKS = 2       # 주간 분산
# 2026-08-04 운영자 결정: R2는 2025 out-of-sample 재현(+6.43/PF5.07/알파+3.62)으로
# 정산 요건 10건 단축. R1은 같은 검증에서 음수(-1.71/알파-2.49) — 15건 유지.
GATE_MIN_SETTLED_BY_RULE = {"R2_할인저변동": 10}


def _bench_map() -> tuple[list[str], dict[str, float]]:
    try:
        cache = json.loads(CACHE.read_text(encoding="utf-8"))
        bars = cache.get(BENCH_TICKER) or []
        m = {b["d"]: float(b["c"]) for b in bars}
        return sorted(m), m
    except Exception:
        return [], {}


def market_fwd5(session_date: str, bdates: list[str], bench: dict[str, float]) -> float | None:
    """신호일 익일~D5 구간의 벤치마크 수익률 (원장 정산 규약과 동일 창)."""
    if session_date not in bench:
        return None
    i = bdates.index(session_date)
    if i + 1 >= len(bdates):
        return None
    e = bench[bdates[i + 1]]
    x = bench[bdates[min(i + 5, len(bdates) - 1)]]
    return 100 * (x / e - 1) if e else None


# R2 rv20 임계 (2026-08-05 운영자 승인 개정: 6.24 -> 8.0).
# forward 정산 0건 상태의 사전 개정이다(골대 이동 아님 — 데이터 도착 전).
# 근거 스윕(양 캐시, 원장 계약과 동일 규약: next open / TP12 / SL25 우선 / D5 / 비용 0.45%):
#   2025 독립연도: <=6.24 n=18 +6.38 알파+4.28  ->  <=8.0 n=20 +7.09 PF6.40 알파+4.97
#   2026:          <=6.24 n=135 +6.79 알파+6.40  ->  <=8.0 n=266 +5.75 PF3.37 알파+4.84
#   증분(6.24<rv20<=8.0)만: 2026 n=131 +4.67/승률71%/알파+3.23, 2025 n=2 +13.47 — 양 연도 양수.
#   10 초과부터 급감(무제한 증분 +3.30/알파+1.42, 2025 무제한은 PF 5.38->2.88 붕괴) — 10은 불가.
# 6.24는 절벽이 아니라 보수적 지점이었다. 원장 feats에 rv20이 저장되므로
# 6.24 이하 코호트는 사후 분리 분석이 항상 가능하다.
R2_RV20_LE = 8.0
R2_DISC_LE = -25.0


def rule_flags(row: dict) -> dict[str, bool]:
    feats = row.get("feats") or {}
    r1 = bool(row.get("pass_all"))
    disc = feats.get("ma20_disc")
    rv20 = feats.get("rv20")
    r2 = disc is not None and rv20 is not None and float(disc) <= R2_DISC_LE and float(rv20) <= R2_RV20_LE
    return {"R1_8조건": r1, "R2_할인저변동": r2, "R3_합집합": r1 or r2}


def main() -> int:
    if not LEDGER.exists():
        print("원장 없음:", LEDGER)
        return 1
    rows = [json.loads(x) for x in LEDGER.read_text(encoding="utf-8").splitlines() if x.strip()]
    sessions = sorted({r["session_date"] for r in rows})
    print(f"원장 {len(rows)}행 / 관측 세션 {len(sessions)}일 ({sessions[0]} ~ {sessions[-1]})")
    print(f"게이트 기준: {GATE_MIN_SESSIONS}영업일 AND 규칙별 정산 {GATE_MIN_SETTLED}건 AND {GATE_MIN_WEEKS}개 주간 분산\n")

    bdates, bench = _bench_map()
    stats: dict[str, dict] = {}
    for r in rows:
        flags = rule_flags(r)
        for rule, hit in flags.items():
            if not hit:
                continue
            s = stats.setdefault(rule, {
                "cand": 0, "settled": [], "weeks": defaultdict(list),
                "alpha": [], "regime": defaultdict(list),
            })
            s["cand"] += 1
            if r.get("status") == "SETTLED" and r.get("net_pct") is not None:
                net = float(r["net_pct"])
                s["settled"].append(net)
                wk = datetime.strptime(r["session_date"], "%Y-%m-%d").strftime("%G-W%V")
                s["weeks"][wk].append(net)
                mkt = market_fwd5(r["session_date"], bdates, bench) if bench else None
                if mkt is not None:
                    s["alpha"].append(net - mkt)
                    regime = "상승" if mkt > 1 else ("하락" if mkt < -1 else "횡보")
                    s["regime"][regime].append(net)

    for rule in ("R1_8조건", "R2_할인저변동", "R3_합집합"):
        s = stats.get(rule) or {"cand": 0, "settled": [], "weeks": {}, "alpha": [], "regime": {}}
        nets = s["settled"]
        n = len(nets)
        line = f"{rule:12s} 후보 {s['cand']:4d} | 정산 {n:3d}"
        if n:
            g = sum(x for x in nets if x > 0)
            l = -sum(x for x in nets if x <= 0)
            pf = round(g / l, 2) if l > 0 else float("inf")
            wr = 100 * sum(1 for x in nets if x > 0) / n
            line += f" | 평균 {sum(nets)/n:+.2f}% | 승률 {wr:.0f}% | PF {pf}"
            if s["alpha"]:
                line += f" | 알파(vs KODEX200) {sum(s['alpha'])/len(s['alpha']):+.2f}%"
            wk = {k: round(sum(v)/len(v), 2) for k, v in sorted(s["weeks"].items())}
            line += f" | 주간 {wk}"
        need = GATE_MIN_SETTLED_BY_RULE.get(rule, GATE_MIN_SETTLED)
        gate_ok = (
            len(sessions) >= GATE_MIN_SESSIONS
            and n >= need
            and len(s["weeks"]) >= GATE_MIN_WEEKS
        )
        print(line + ("  <<GATE 충족>>" if gate_ok else ""))
        # 사전등록 국면 조건(2026-08-04): 승격 규칙은 하락 국면에서도 견뎌야 한다
        if s["regime"]:
            parts = []
            for reg in ("상승", "횡보", "하락"):
                v = s["regime"].get(reg) or []
                if v:
                    parts.append(f"{reg} {sum(v)/len(v):+.2f}%({len(v)})")
            print(f"{'':12s} 국면별: " + " / ".join(parts))

    # 원장 전체 축 — 규칙 통과와 무관하게 관측·가상정산된 건.
    # 2026-08-04: 이 구분이 리포트에 없어서 "규칙 정산 0"과 "원장 정산 있음"이 충돌하는
    # 것처럼 읽혔다(002995 pass_all=False·R2 미통과인데 net +11.75로 SETTLED).
    # 원장은 관측 전용이고 주문은 kr_fallen_order_bridge가 규칙 통과분만 낸다 —
    # 두 숫자는 서로 다른 모집단이며, 원장 정산은 실매수가 아니다.
    all_settled = [
        float(r["net_pct"]) for r in rows
        if r.get("status") == "SETTLED" and r.get("net_pct") is not None
    ]
    no_rule = [
        float(r["net_pct"]) for r in rows
        if r.get("status") == "SETTLED" and r.get("net_pct") is not None
        and not any(rule_flags(r).values())
    ]
    print(
        f"\n[대조] 원장 전체(규칙 무관, 관측 전용·실매수 아님) 정산 {len(all_settled)}건"
        + (f" | 평균 {sum(all_settled)/len(all_settled):+.2f}%" if all_settled else "")
        + f" | 그중 어떤 규칙도 통과 못한 건 {len(no_rule)}건"
        + (f" (평균 {sum(no_rule)/len(no_rule):+.2f}%)" if no_rule else "")
    )
    print("        규칙별 집계는 이 중 해당 규칙 통과분만 센다 — 두 숫자는 모집단이 다르다.")

    print("\n(판정은 운영자 — 이 리포트는 집계만 한다. in-sample 참고치와 비교 금지, forward만 본다.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
