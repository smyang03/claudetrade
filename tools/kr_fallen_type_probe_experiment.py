"""KR 신규 유형 탐사 G1(느린 침식)·G2(저가 밴드) — read-only, 2년 캐시, 사전 등록 6셀.

2026-08-08 운영자 지시 ("그물 폭 관찰"의 후속 검증 — candidate-net-width 메모리):
  G1 느린 침식: 같은 할인 깊이(<=-15%)에 도달한 두 경로 —
     급락형(하루 낙폭 <=-5.27, 현행 트리거) vs 침식형(낙폭 -3~-5.27, 현행 밖).
     계약 성과가 같은가? 같으면 현행 트리거가 후보를 절반 놓치는 것.
  G2 저가 밴드: 가격 2,000~7,110원(현행 하한 밖)에서 R2 조건(할인<=-25 & rv20<=8.0)
     성과 — 001210(할인 -30, 'R2급', 가격 배제) 사례의 일반화 검증.
     ⚠️ 저가주 체결·유동성·조작 리스크는 시뮬에 없음 — 결과 해석 시 필수 단서.

계약: next open / TP12 / SL25 우선 / D5 / cost 0.25 (스캔 도구 동일 규약).
사용: python tools/kr_fallen_type_probe_experiment.py
출력: stdout + docs/reports/kr_fallen_type_probe_<date>.md
"""

from __future__ import annotations

import json
import statistics as st
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kr_fallen_ranking_experiment import CACHES  # noqa: E402

COST = 0.25


def _settle(bars: list[dict], idx: int) -> float | None:
    after = bars[idx + 1:idx + 6]
    if len(after) < 5:
        return None
    e = after[0]["o"]
    if not e or e <= 0:
        return None
    tp, sl = e * 1.12, e * 0.75
    for k, b in enumerate(after):
        if k > 0 and b["o"] <= sl:
            return 100 * (b["o"] / e - 1) - COST
        if k > 0 and b["o"] >= tp:
            return 100 * (b["o"] / e - 1) - COST
        if b["l"] <= sl:
            return 100 * (sl / e - 1) - COST
        if b["h"] >= tp:
            return 100 * (tp / e - 1) - COST
    return 100 * (after[-1]["c"] / e - 1) - COST


def _scan(cache: dict, *, chg_lo: float, chg_hi: float, price_lo: float, price_hi: float,
          amt_ge: float, cond) -> list[float]:
    nets = []
    for code, bars in cache.items():
        if len(bars) < 27:
            continue
        for idx in range(22, len(bars)):
            b = bars[idx]
            prev = bars[idx - 1]["c"]
            if prev <= 0:
                continue
            chg = 100 * (b["c"] / prev - 1)
            if not (chg_lo <= chg <= chg_hi):
                continue
            if not (price_lo <= b["c"] < price_hi):
                continue
            w20 = bars[idx - 20:idx]
            if sum(x["amt"] for x in w20) / 20 < amt_ge:
                continue
            ma20 = sum(x["c"] for x in w20) / 20
            disc = 100 * (b["c"] / ma20 - 1) if ma20 > 0 else 0.0
            rets = [100 * (w20[m]["c"] / w20[m - 1]["c"] - 1) for m in range(1, 20)
                    if w20[m - 1]["c"] > 0]
            rv = st.pstdev(rets) if len(rets) > 3 else 99.0
            if not cond(disc, rv):
                continue
            net = _settle(bars, idx)
            if net is not None:
                nets.append(net)
    return nets


def _fmt(nets: list[float]) -> str:
    if not nets:
        return "n=0"
    pos = sum(x for x in nets if x > 0)
    neg = -sum(x for x in nets if x <= 0)
    pf = round(pos / neg, 2) if neg > 0 else float("inf")
    return (f"n={len(nets)} 평균 {st.mean(nets):+.2f}% 승률 "
            f"{100 * sum(1 for x in nets if x > 0) / len(nets):.0f}% PF {pf} 최악 {min(nets):+.1f}")


def main() -> int:
    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    emit(f"# KR 유형 탐사 G1 느린 침식·G2 저가 밴드 ({date.today().isoformat()}) — 사전 등록 6셀")
    emit("\n계약 TP12/SL25/D5 cost0.25. G2는 체결·유동성 비용 미반영 — 해석 단서 필수.")
    for year, path in CACHES.items():
        cache = json.loads(path.read_text(encoding="utf-8"))
        emit(f"\n## {year} 캐시")
        # G1: 같은 할인(<=-15) — 도달 경로 비교 (현행 가격·유동성 유지)
        fast = _scan(cache, chg_lo=-29.7, chg_hi=-5.27, price_lo=7110, price_hi=1e12,
                     amt_ge=1e9, cond=lambda d, r: d <= -15)
        slow = _scan(cache, chg_lo=-5.27, chg_hi=-3.0, price_lo=7110, price_hi=1e12,
                     amt_ge=1e9, cond=lambda d, r: d <= -15)
        emit(f"G1 급락형(낙폭<=-5.27 & 할인<=-15): {_fmt(fast)}")
        emit(f"G1 침식형(낙폭 -3~-5.27 & 할인<=-15): {_fmt(slow)}")
        # G2: 저가 밴드 R2 조건 (현행 낙폭 밴드 유지)
        lowpx = _scan(cache, chg_lo=-29.7, chg_hi=-5.27, price_lo=2000, price_hi=7110,
                      amt_ge=5e8, cond=lambda d, r: d <= -25 and r <= 8.0)
        ref = _scan(cache, chg_lo=-29.7, chg_hi=-5.27, price_lo=7110, price_hi=1e12,
                    amt_ge=1e9, cond=lambda d, r: d <= -25 and r <= 8.0)
        emit(f"G2 저가 R2(2천~7110원, 유동성 5억~): {_fmt(lowpx)}")
        emit(f"G2 참조 현행 R2(7110원~):            {_fmt(ref)}")

    emit("\n## 판정 메모")
    emit("- 계측 전용. 신규 레인·문턱 변경은 등록부 갱신 + 양 연도 재현 + 운영자 승인으로만.")
    out_path = ROOT / "docs" / "reports" / f"kr_fallen_type_probe_{date.today().strftime('%Y%m%d')}.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n리포트 저장: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
