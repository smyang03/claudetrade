"""KR 규칙(R2/R4)이 **급락 집합 안에서** 추가 변별력이 있는가 (2026-08-24).

## 왜 이 질문인가

08-16 진단은 "규칙 강함"이었다 — 기저 −0.59% vs R4 +6.27%. 그런데 거기서 말한 **기저는
전 종목 유니버스 27,409건**이다. 오늘 원장(급락 스캐너 통과분 15세션)에서는 부호가 뒤집혔다:

    규칙 통과   n=4    중앙 -0.65
    규칙 미통과 n=94   중앙 +11.75   <- 미통과가 더 좋다

두 숫자는 **모집단이 다르다.** 08-16 F2에 단서가 있다 — 규칙 적중분을 쪼개니
급락 가시 576건 +5.77% / 급락 없는 204건 −0.59%. 오늘의 미통과 +5.66%와 거의 같다.

→ 가설: **알파는 "급락" 자체에서 나오고, R2/R4의 추가 조건(할인 깊이·갭·저변동)은
급락 집합 안에서는 변별력이 거의 없다.** 08-16이 못 본 숫자는 "급락 가시 ∩ 규칙 미통과"다.

이게 사실이면 처방이 달라진다:
  · 규칙이 값을 깎는 게 아니라 **좁히기만 한다** → 문턱 완화가 빈도를 올리되 성과는 유지
  · 반대로 규칙이 급락 집합 안에서도 우월하면 → 완화는 성과를 희석한다

## 방법 — 프로덕션 정의 재사용

스캐너(`tools/kr_fallen_shadow_scan.py`)와 **같은** 필터·피처·정산을 쓴다. 직접 구현하면
재현이 깨진다(us_swing replay가 정책값 하드코딩으로 0/6 일치였던 전례).
  · 급락 필터: -29.7 <= chg <= -5.27, 종가 >= 7,110, 20일 평균 거래대금 >= 10억
  · 정산: 익일 시가 진입, TP+12%/SL−25%/5일, 비용 0.25%p, 갭스루 반영
  · 규칙: `tools/kr_fallen_gate_report.rule_flags` (R2/R4 정본)

사용:
    python tools/kr_rule_discrimination_backtest.py
    python tools/kr_rule_discrimination_backtest.py --cache 2025
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics as st
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kr_fallen_gate_report import rule_flags  # noqa: E402

CACHES = {
    "2026": ROOT / "data" / "analysis" / "kr_fallen_price_cache.json",
    "2025": ROOT / "data" / "analysis" / "kr_fallen_price_cache_2025.json",
}
# 스캐너 CONDS_DOC와 같은 값 (tools/kr_fallen_shadow_scan.py:111)
DROP_GE, PRICE_GE, AMT20_MIN = 5.27, 7110.0, 1e9
COST = 0.25
TP_MULT, SL_MULT, HOLD = 1.12, 0.75, 5


def _scan(cache: dict) -> list[dict]:
    """급락 가시 집합 + 피처. 스캐너의 drop_capture 경로만 재현한다(사각 제외)."""
    out: list[dict] = []
    for code, bars in cache.items():
        if not bars or len(bars) < 25:
            continue
        for idx in range(22, len(bars)):
            b, prev = bars[idx], bars[idx - 1]["c"]
            if prev <= 0 or b["o"] <= 0:
                continue
            chg = 100 * (b["c"] / prev - 1)
            if not (-29.7 <= chg <= -DROP_GE) or b["c"] < PRICE_GE:
                continue
            w20 = bars[idx - 20:idx]
            if sum(x["amt"] for x in w20) / 20 < AMT20_MIN:
                continue
            ma20 = sum(x["c"] for x in w20) / 20
            rets = [100 * (w20[m]["c"] / w20[m - 1]["c"] - 1)
                    for m in range(1, 20) if w20[m - 1]["c"] > 0]
            out.append({
                "ticker": code,
                "session_date": b["d"],
                "idx": idx,
                "pass_all": False,  # rule_flags의 R1은 여기서 안 쓴다
                "feats": {
                    "gap": 100 * (b["o"] / prev - 1),
                    "rv20": st.pstdev(rets) if len(rets) > 3 else 99.0,
                    "ma20_disc": 100 * (b["c"] / ma20 - 1) if ma20 > 0 else 0.0,
                    "chg": chg,
                },
            })
    return out


def _settle(bars: list[dict], idx: int) -> tuple[float | None, str]:
    """스캐너 _settle_file과 동일한 정산. 갭스루를 시가로 반영한다."""
    after = bars[idx + 1:]
    if not after:
        return None, "no_entry_bar"
    entry = after[0]["o"]
    if not entry or entry <= 0:
        return None, "entry_invalid"
    tp, sl = entry * TP_MULT, entry * SL_MULT
    win = after[:HOLD]
    for k, b in enumerate(win):
        if k > 0:
            if b["o"] <= sl:
                return 100 * (b["o"] / entry - 1) - COST, "gap_sl"
            if b["o"] >= tp:
                return 100 * (b["o"] / entry - 1) - COST, "gap_tp"
        if b["l"] <= sl:
            return 100 * (sl / entry - 1) - COST, "sl"
        if b["h"] >= tp:
            return 100 * (tp / entry - 1) - COST, "tp"
    if len(win) < HOLD:
        return None, "not_matured"
    return 100 * (win[-1]["c"] / entry - 1) - COST, "time"


def _describe(rows: list[dict], label: str) -> str:
    if not rows:
        return f"  {label:16s} n=0"
    v = [r["net"] for r in rows]
    sessions = len({r["session_date"] for r in rows})
    tickers = len({r["ticker"] for r in rows})
    tp = sum(1 for r in rows if r["kind"] in ("tp", "gap_tp"))
    return (f"  {label:16s} n={len(v):5d} 세션{sessions:4d} 종목{tickers:4d} | "
            f"평균 {st.mean(v):+6.2f} 중앙 {st.median(v):+6.2f} "
            f"승률 {100 * sum(1 for x in v if x > 0) / len(v):3.0f}% | "
            f"TP {100 * tp / len(v):3.0f}%")


def _cluster_t(rows: list[dict]) -> float | None:
    """종목 클러스터 t — 같은 종목 반복이 t를 부풀리는 것을 막는다(08-20 규율)."""
    by: dict[str, list[float]] = {}
    for r in rows:
        by.setdefault(r["ticker"], []).append(r["net"])
    means = [st.mean(v) for v in by.values()]
    if len(means) < 3:
        return None
    sd = st.pstdev(means)
    return (st.mean(means) / (sd / len(means) ** 0.5)) if sd > 0 else None


def main() -> int:
    parser = argparse.ArgumentParser(description="KR 규칙의 급락 집합 내 변별력")
    parser.add_argument("--cache", choices=["2026", "2025", "both"], default="both")
    args = parser.parse_args()

    keys = ["2026", "2025"] if args.cache == "both" else [args.cache]
    for key in keys:
        path = CACHES[key]
        if not path.exists():
            print(f"[{key}] 캐시 없음: {path}")
            continue
        cache = json.loads(path.read_text(encoding="utf-8"))
        dates = sorted({b["d"] for bars in cache.values() for b in bars})
        print(f"\n=== {key} 캐시 — 종목 {len(cache)} · 세션 {len(dates)} "
              f"({dates[0]} ~ {dates[-1]}) ===")

        rows = _scan(cache)
        settled: list[dict] = []
        for row in rows:
            net, kind = _settle(cache[row["ticker"]], row["idx"])
            if net is None:
                continue
            flags = rule_flags(row)
            settled.append({
                **row, "net": net, "kind": kind,
                "r2": flags["R2_할인저변동"], "r4": flags["R4_갭할인"],
                "hit": flags["R2_할인저변동"] or flags["R4_갭할인"],
            })
        if not settled:
            print("  정산 표본 없음")
            continue

        hit = [r for r in settled if r["hit"]]
        miss = [r for r in settled if not r["hit"]]
        print(f"  급락 가시 정산 {len(settled):,}건")
        print(_describe(settled, "전체(급락)"))
        print(_describe(hit, "R2∪R4 통과"))
        print(_describe(miss, "  미통과"))
        print(_describe([r for r in settled if r["r2"]], "  R2만"))
        print(_describe([r for r in settled if r["r4"]], "  R4만"))

        if hit and miss:
            diff = st.mean([r["net"] for r in hit]) - st.mean([r["net"] for r in miss])
            th, tm = _cluster_t(hit), _cluster_t(miss)
            print(f"\n  통과 − 미통과 = {diff:+.2f}%p"
                  f" | 클러스터t 통과 {th if th is None else round(th, 2)}"
                  f" / 미통과 {tm if tm is None else round(tm, 2)}")
            print("  → 규칙이 급락 집합 안에서 " +
                  ("**추가 변별력 있음**" if diff > 1.0 else
                   "**추가 변별력 없음**" if abs(diff) <= 1.0 else "**오히려 해로움**"))
    print("\n※ 판정은 운영자. 일봉 근사·슬리피지 미반영, R2/R4 도출 창과 겹칠 수 있다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
