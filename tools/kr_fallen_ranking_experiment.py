"""KR 다발일 랭킹 오프라인 실험 E5 (read-only, 2년 가격 캐시 — 사전 등록).

2026-08-07 운영자 지시. 배경: 08-06 실측 26후보 vs 일1건 — "고르기"가 성과를
좌우하는데 현행 랭킹(할인깊은순)은 오프라인 검증(08-04)만 있고 대안 비교가 없다.

설계: 스캔 도구(kr_fallen_shadow_scan)의 후보 밴드·피처·계약 정산을 그대로 복제해
2025·2026 캐시에서 R2∪R4 통과 후보를 재구성하고, 세션당 1픽 랭킹 6종을 비교한다.
  V0 할인깊은순(현행 브리지)  V1 갭깊은순  V2 겹침우선(R2∩R4 -> 할인순)
  V3 rv20낮은순  V4 낙폭깊은순  V5 균등전량(베이스라인)
평가: 픽당 계약 net(TP12/SL25/D5, cost 0.25, 스캔 도구와 동일 규약), 연도 분리,
      전체 세션 + 다발일(후보>=2)만 분리 — 랭킹이 갈리는 곳은 다발일뿐이다.

사용: python tools/kr_fallen_ranking_experiment.py
출력: stdout + docs/reports/kr_fallen_ranking_experiment_<date>.md
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

from tools.kr_fallen_gate_report import rule_flags  # noqa: E402

CACHES = {
    "2026": ROOT / "data" / "analysis" / "kr_fallen_price_cache.json",
    "2025": ROOT / "data" / "analysis" / "kr_fallen_price_cache_2025.json",
}
COST = 0.25          # 스캔 도구 COST와 동일
DROP_GE, DROP_FLOOR = 5.27, -29.7
PRICE_GE, AMT20_GE = 7110.0, 1e9


def _candidates_and_nets(cache: dict) -> list[dict]:
    """세션×티커 후보 재구성 + 계약 정산 — 스캔/정산 로직 복제(동일 규약)."""
    out = []
    for code, bars in cache.items():
        if not bars or len(bars) < 27:
            continue
        for idx in range(22, len(bars)):
            b = bars[idx]
            prev = bars[idx - 1]["c"]
            if prev <= 0:
                continue
            chg = 100 * (b["c"] / prev - 1)
            if not (DROP_FLOOR <= chg <= -DROP_GE) or b["c"] < PRICE_GE:
                continue
            w20 = bars[idx - 20:idx]
            if sum(x["amt"] for x in w20) / 20 < AMT20_GE:
                continue
            ma20 = sum(x["c"] for x in w20) / 20
            rets = [100 * (w20[m]["c"] / w20[m - 1]["c"] - 1) for m in range(1, 20)
                    if w20[m - 1]["c"] > 0]
            feats = {
                "chg": chg,
                "gap": 100 * (b["o"] / prev - 1),
                "rv20": st.pstdev(rets) if len(rets) > 3 else 99.0,
                "ma20_disc": 100 * (b["c"] / ma20 - 1) if ma20 > 0 else 0.0,
            }
            flags = rule_flags({"feats": feats, "pass_all": False})
            if not (flags["R2_할인저변동"] or flags["R4_갭할인"]):
                continue
            # 계약 정산 (settle()와 동일: 갭 시가 체결, SL 우선, D5 종가)
            after = bars[idx + 1:idx + 6]
            if len(after) < 5:
                continue
            e = after[0]["o"]
            if not e or e <= 0:
                continue
            tp, sl = e * 1.12, e * 0.75
            net = None
            for k, bar in enumerate(after):
                if k > 0 and bar["o"] <= sl:
                    net = 100 * (bar["o"] / e - 1) - COST
                    break
                if k > 0 and bar["o"] >= tp:
                    net = 100 * (bar["o"] / e - 1) - COST
                    break
                if bar["l"] <= sl:
                    net = 100 * (sl / e - 1) - COST
                    break
                if bar["h"] >= tp:
                    net = 100 * (tp / e - 1) - COST
                    break
            if net is None:
                net = 100 * (after[-1]["c"] / e - 1) - COST
            out.append({"session": b["d"], "ticker": code, "net": net,
                        "both": flags["R2_할인저변동"] and flags["R4_갭할인"], **feats})
    return out


VARIANTS = {
    "V0_할인깊은순(현행)": lambda c: sorted(c, key=lambda r: r["ma20_disc"]),
    "V1_갭깊은순": lambda c: sorted(c, key=lambda r: r["gap"]),
    "V2_겹침우선": lambda c: sorted(c, key=lambda r: (not r["both"], r["ma20_disc"])),
    "V3_rv20낮은순": lambda c: sorted(c, key=lambda r: r["rv20"]),
    "V4_낙폭깊은순": lambda c: sorted(c, key=lambda r: r["chg"]),
}


def _stats(nets: list[float]) -> str:
    if not nets:
        return "n=0"
    pos = sum(x for x in nets if x > 0)
    neg = -sum(x for x in nets if x <= 0)
    pf = round(pos / neg, 2) if neg > 0 else float("inf")
    return (f"n={len(nets)} 평균 {st.mean(nets):+.2f}% 승률 "
            f"{100 * sum(1 for x in nets if x > 0) / len(nets):.0f}% PF {pf}")


def main() -> int:
    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    emit(f"# KR 다발일 랭킹 실험 E5 ({date.today().isoformat()}) — 5랭킹+균등 × 2연도")
    emit("\n후보 = R2∪R4 통과(rv20 8.0 개정 반영), 계약 = TP12/SL25/D5 cost 0.25 (스캔 도구 동일 규약).")
    for year, path in CACHES.items():
        cache = json.loads(path.read_text(encoding="utf-8"))
        cands = _candidates_and_nets(cache)
        by_session: dict[str, list[dict]] = {}
        for c in cands:
            by_session.setdefault(c["session"], []).append(c)
        multi = {s: v for s, v in by_session.items() if len(v) >= 2}
        emit(f"\n## {year} 캐시 — 후보 {len(cands)}건 / 세션 {len(by_session)}일 / "
             f"다발일 {len(multi)}일 (다발일 후보 {sum(len(v) for v in multi.values())}건)")
        for scope, sessions in (("전체 세션", by_session), ("다발일만(랭킹이 갈리는 곳)", multi)):
            emit(f"\n### {scope}")
            for name, rank in VARIANTS.items():
                picks = [rank(v)[0]["net"] for v in sessions.values()]
                emit(f"{name:18s}: {_stats(picks)}")
            equal = [x["net"] for v in sessions.values() for x in v]
            emit(f"{'V5_균등전량':18s}: {_stats(equal)} (픽 없이 전량 균등 — 랭킹 부가가치의 기준선)")

    emit("\n## 판정 메모")
    emit("- 계측 전용. 브리지 랭킹 변경은 forward [랭킹] 뷰 + 운영자 결정으로만.")
    emit("- in-sample 탐색이므로 승자는 2025 독립 재현이 있어야 후보 자격(등록부 규칙).")
    out_path = ROOT / "docs" / "reports" / f"kr_fallen_ranking_experiment_{date.today().strftime('%Y%m%d')}.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n리포트 저장: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
