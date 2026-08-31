#!/usr/bin/env python3
"""KR 레인 축 분해 — US에서 무너진 축들이 KR에서는 어떤가 (2026-08-31).

배경: 08-30~31 이틀간 US 축을 다섯 개 검정해 전부 OOS에서 무너뜨렸다. 그런데
"우리 전략"은 두 레인인데 US만 팠다. KR은 구조가 다르다는 실측이 이미 있다 —
"US=장중투매형, KR=갭 과잉반응형(정반대)"(08-01), "KR-US 이식 금지"(08-06).
US 결론을 KR에 옮기지 않기 위해, 그리고 KR 고유 축이 있는지 보기 위해 같은
방법론으로 분해한다.

**US와 다른 점 두 가지**(그래서 분석이 더 단순하다):
  1. KR shadow 원장(`kr_fallen_shadow.jsonl`)은 **계약이 이미 적용돼 있다**
     (exit_kind: tp/time/sl/gap_tp). US shadow가 TP 없는 만기 보유였던 것과
     달라서 contract_exit 재구성이 필요 없다.
  2. 축 피처가 원장에 계산돼 있다(chg·close_pos·gap·vol_spike·mom20·
     from_high20·rv20·ma20_disc). close_pos가 US의 IBS에 해당한다.

**⚠️ 이 표본은 규칙 미통과분이다.** 262건 전부 `pass_all=False`다 — R2/R4 규칙을
통과해 실제 주문이 나간 건은 별도 집계이며(forward 5건 +0.61%, 실거래 1건
+5.00%) 여기 없다. **두 숫자는 모집단이 다르므로 직접 비교하지 않는다.**
kr_fallen_gate_report가 같은 경고를 달고 있다. 08-24에 "KR 규칙 유효 재확인 ·
문턱 완화는 틀린 처방"으로 판정된 사안이므로 이 스크립트는 규칙 자체를
재론하지 않는다.

**한 달 표본(2026-07-31~08-27)이라 검증이 아니라 관측이다.** US에서 배운 것이
정확히 이것이다 — 두 달 표본에서 클러스터 t가 3을 넘어도 21개월 OOS에서는
0이 될 수 있다. 여기서 나오는 어떤 축도 라이브 후보로 승격하지 않는다.
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "shadow" / "kr_fallen_shadow.jsonl"
AXES = [("chg", "등락"), ("close_pos", "IBS"), ("gap", "갭"), ("vol_spike", "거래량비"),
        ("mom20", "모멘텀20"), ("from_high20", "할인깊이"), ("rv20", "변동성20"),
        ("ma20_disc", "MA20이격")]


def cluster_t(pairs: list[tuple[str, float]]) -> float:
    by: dict[str, list[float]] = defaultdict(list)
    for t, v in pairs:
        by[t].append(v)
    m = [st.mean(v) for v in by.values()]
    if len(m) < 2:
        return float("nan")
    sd = st.pstdev(m) * math.sqrt(len(m) / (len(m) - 1))
    return st.mean(m) / (sd / math.sqrt(len(m))) if sd else float("nan")


def tail(nets: list[float]) -> float:
    s = sorted(nets)
    return st.mean(s[: max(1, len(s) // 5)])


def show(label: str, rows: list[dict]) -> float | None:
    if len(rows) < 15:
        print(f"  {label:20s} n={len(rows):3d} (표본부족)")
        return None
    n = [float(r["net_pct"]) for r in rows]
    tp = 100 * sum(1 for r in rows if str(r.get("exit_kind", "")).startswith(("tp", "gap_tp"))) / len(rows)
    print(f"  {label:20s} n={len(n):3d} 평균{st.mean(n):+6.2f}% 꼬리{tail(n):+7.2f}% "
          f"TP율{tp:3.0f}% 승률{100*sum(1 for x in n if x>0)/len(n):3.0f}% "
          f"t={cluster_t([(r['ticker'], float(r['net_pct'])) for r in rows]):+5.2f}")
    return st.mean(n)


def main() -> int:
    if not LEDGER.exists():
        print(f"[ERROR] 원장 없음: {LEDGER}")
        return 1
    rows = [json.loads(l) for l in LEDGER.open(encoding="utf-8")]
    mat = [r for r in rows if r.get("net_pct") is not None]
    dates = [r["session_date"] for r in mat]
    print(f"=== KR 레인 축 분해 (2026-08-31) ===")
    print(f"shadow {len(mat)}건 / 종목 {len({r['ticker'] for r in mat})}개 | "
          f"{min(dates)} ~ {max(dates)} | 계약 적용 원장")
    print("⚠️ 전건 pass_all=False (규칙 미통과분). 실주문 코호트와 모집단이 다르다.\n")
    show("전체", mat)

    print("\n[축별 중앙값 분할]")
    results = []
    for key, label in AXES:
        vals = [r for r in mat if r.get("feats", {}).get(key) is not None]
        if len(vals) < 40:
            print(f"  {label}: 표본부족")
            continue
        med = st.median([r["feats"][key] for r in vals])
        hi = [r for r in vals if r["feats"][key] >= med]
        lo = [r for r in vals if r["feats"][key] < med]
        a = show(f"{label} 상위", hi)
        b = show(f"{label} 하위", lo)
        if a is not None and b is not None:
            print(f"      → 차이 {a-b:+.2f}%p")
            results.append((label, a - b, tail([float(r['net_pct']) for r in hi])
                            - tail([float(r['net_pct']) for r in lo])))

    print("\n[요약] 차이 크기순 (부호: 양수면 상위군 우위)")
    for label, d, td in sorted(results, key=lambda x: -abs(x[1])):
        print(f"  {label:10s} 평균차 {d:+6.2f}%p | 꼬리차 {td:+6.2f}%p")
    print("\n관측 전용 — 한 달 표본이라 어떤 축도 라이브 후보로 승격하지 않는다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
