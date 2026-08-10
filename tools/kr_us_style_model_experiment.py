"""E5-V6: KR 급락 풀에 US식 모델 파이프라인 소급 적용 (read-only, 2년 캐시).

2026-08-10 운영자 지시 "한국장을 미국처럼 모델로 돌려봐".
US식 = 당일 급락 풀(day_losers 상당) -> 피처 -> HGB(회귀+분류, 계약 라벨 학습)
     -> alpha_score 랭킹 -> 세션당 top1, 계약(TP12/SL25/D5 cost0.25) 정산.

배경: KR 모델은 기각 이력(08-01, 구 파이프라인 리프트 없음)이 있으나, US식 재현
(계약 라벨 + walk-forward + 급락 풀)은 미실험(E5의 미실행 모델 셀). 두 캐시를 이어
2024-11~2026-08 21개월에서 월 단위 확장 walk-forward(purge 5세션)로 검증한다.

판정: 모델 top1이 (a) 할인깊은순 top1(현행 브리지), (b) 무작위 픽 EV(세션 평균),
(c) 균등 전량(후보당) 을 이기는가 — 연도 분리 재현까지 확인.

사용: python tools/kr_us_style_model_experiment.py
출력: stdout + docs/reports/kr_us_style_model_<date>.md
"""

from __future__ import annotations

import json
import statistics as st
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CACHE_2026 = ROOT / "data" / "analysis" / "kr_fallen_price_cache.json"
CACHE_2025 = ROOT / "data" / "analysis" / "kr_fallen_price_cache_2025.json"
BENCH = "069500"
COST = 0.25
FEATURES = ["chg", "gap", "close_pos", "vol_spike", "mom20", "from_high20",
            "rv20", "ma20_disc", "log_price", "log_amt20", "mkt_mom5", "mkt_mom20"]
SEEDS = [20260710, 20260711, 20260712]


def _merged_cache() -> dict:
    """두 캐시 병합 — 겹치는 날짜(2026-01)는 2026 캐시 우선."""
    old = json.loads(CACHE_2025.read_text(encoding="utf-8"))
    new = json.loads(CACHE_2026.read_text(encoding="utf-8"))
    merged: dict[str, list] = {}
    for code in set(old) | set(new):
        bars = {b["d"]: b for b in old.get(code, [])}
        bars.update({b["d"]: b for b in new.get(code, [])})
        merged[code] = [bars[d] for d in sorted(bars)]
    return merged


def _candidate_table(cache: dict) -> pd.DataFrame:
    bench = {b["d"]: float(b["c"]) for b in cache.get(BENCH, [])}
    bdates = sorted(bench)

    def mkt_mom(day: str, n: int) -> float:
        if day not in bench:
            return 0.0
        i = bdates.index(day)
        if i < n or bench[bdates[i - n]] <= 0:
            return 0.0
        return 100 * (bench[day] / bench[bdates[i - n]] - 1)

    rows = []
    for code, bars in cache.items():
        if code == BENCH or len(bars) < 27:
            continue
        for idx in range(22, len(bars)):
            b = bars[idx]
            prev = bars[idx - 1]["c"]
            if prev <= 0:
                continue
            chg = 100 * (b["c"] / prev - 1)
            if not (-29.7 <= chg <= -5.27) or b["c"] < 7110:
                continue
            w20 = bars[idx - 20:idx]
            amt20 = sum(x["amt"] for x in w20) / 20
            if amt20 < 1e9:
                continue
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
            ma20 = sum(x["c"] for x in w20) / 20
            hi20 = max(x["h"] for x in w20)
            v20 = sum(x["v"] for x in w20) / 20
            rng = b["h"] - b["l"]
            rets = [100 * (w20[m]["c"] / w20[m - 1]["c"] - 1) for m in range(1, 20)
                    if w20[m - 1]["c"] > 0]
            rows.append({
                "session_date": b["d"], "ticker": code, "label_contract": net,
                "chg": chg,
                "gap": 100 * (b["o"] / prev - 1),
                "close_pos": (b["c"] - b["l"]) / rng if rng > 0 else 0.5,
                "vol_spike": b["v"] / v20 if v20 > 0 else 1.0,
                "mom20": 100 * (b["c"] / bars[idx - 21]["c"] - 1) if bars[idx - 21]["c"] > 0 else 0.0,
                "from_high20": 100 * (b["c"] / hi20 - 1) if hi20 > 0 else 0.0,
                "rv20": st.pstdev(rets) if len(rets) > 3 else 99.0,
                "ma20_disc": 100 * (b["c"] / ma20 - 1) if ma20 > 0 else 0.0,
                "log_price": float(np.log10(b["c"])),
                "log_amt20": float(np.log10(max(amt20, 1.0))),
                "mkt_mom5": mkt_mom(b["d"], 5),
                "mkt_mom20": mkt_mom(b["d"], 20),
            })
    frame = pd.DataFrame(rows)
    frame["month"] = frame["session_date"].str[:7]
    frame["target"] = (frame["label_contract"] >= 0.25).astype(int)
    return frame.replace([np.inf, -np.inf], np.nan)


def _stats(nets: list[float], label: str) -> str:
    if not nets:
        return f"{label}: n=0"
    pos = sum(x for x in nets if x > 0)
    neg = -sum(x for x in nets if x <= 0)
    pf = round(pos / neg, 2) if neg > 0 else float("inf")
    return (f"{label}: n={len(nets)} 평균 {st.mean(nets):+.2f}% 승률 "
            f"{100 * sum(1 for x in nets if x > 0) / len(nets):.0f}% PF {pf}")


def main() -> int:
    frame = _candidate_table(_merged_cache())
    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    emit(f"# E5-V6 KR US식 모델 ({date.today().isoformat()}) — HGB 계약라벨 walk-forward")
    emit(f"\n급락밴드 풀 {len(frame)}행 / {frame['session_date'].nunique()}세션 "
         f"({frame['session_date'].min()}~{frame['session_date'].max()}), 피처 {len(FEATURES)}종.")

    dates = sorted(frame["session_date"].unique())
    months = sorted(frame["month"].unique())
    picks = {"model": [], "disc": [], "rand": [], "pool": []}
    by_year = {}
    for month in months:
        test = frame[frame["month"] == month]
        first_idx = dates.index(sorted(test["session_date"].unique())[0])
        train_dates = dates[:max(0, first_idx - 5)]
        train = frame[frame["session_date"].isin(train_dates)]
        if len(train) < 300 or train["target"].nunique() < 2:
            continue
        preds, probs = [], []
        for seed in SEEDS:
            reg = HistGradientBoostingRegressor(learning_rate=0.05, max_iter=160,
                                                max_leaf_nodes=15, min_samples_leaf=35,
                                                l2_regularization=1.0, random_state=seed)
            clf = HistGradientBoostingClassifier(learning_rate=0.05, max_iter=160,
                                                 max_leaf_nodes=15, min_samples_leaf=35,
                                                 l2_regularization=1.0, random_state=seed)
            reg.fit(train[FEATURES], train["label_contract"])
            clf.fit(train[FEATURES], train["target"])
            preds.append(reg.predict(test[FEATURES]))
            probs.append(clf.predict_proba(test[FEATURES])[:, 1])
        scored = test.copy()
        scored["predicted"] = np.mean(preds, axis=0)
        scored["probability"] = np.mean(probs, axis=0)
        scored["net_rank"] = scored.groupby("session_date")["predicted"].rank(pct=True)
        scored["prob_rank"] = scored.groupby("session_date")["probability"].rank(pct=True)
        scored["alpha"] = 0.5 * scored["net_rank"] + 0.5 * scored["prob_rank"]
        year = month[:4]
        for d, g in scored.groupby("session_date"):
            m_pick = float(g.sort_values(["alpha", "predicted"], ascending=False).iloc[0]["label_contract"])
            d_pick = float(g.sort_values("ma20_disc").iloc[0]["label_contract"])
            r_ev = float(g["label_contract"].mean())
            picks["model"].append(m_pick)
            picks["disc"].append(d_pick)
            picks["rand"].append(r_ev)
            picks["pool"].extend(g["label_contract"].tolist())
            by_year.setdefault(year, {"model": [], "disc": [], "rand": []})
            by_year[year]["model"].append(m_pick)
            by_year[year]["disc"].append(d_pick)
            by_year[year]["rand"].append(r_ev)

    emit("\n## 전체 (walk-forward 시험구간)")
    emit(_stats(picks["model"], "모델 top1(US식)      "))
    emit(_stats(picks["disc"], "할인깊은순 top1(현행) "))
    emit(_stats(picks["rand"], "무작위 픽 EV(세션평균)"))
    emit(_stats(picks["pool"], "균등 전량(후보당)     "))
    emit("\n## 연도 분리")
    for year, d in sorted(by_year.items()):
        emit(f"[{year}] " + " | ".join(
            _stats(v, k) for k, v in (("모델", d["model"]), ("할인순", d["disc"]), ("무작위", d["rand"]))))

    emit("\n## 판정 메모")
    emit("- 계측 전용. KR 모델 재소환은 (a) 모델이 할인순·무작위를 양 연도에서 이기고")
    emit("  (b) 후보>슬롯 일상화 (c) forward 재현 + 운영자 승인이 모두 있어야 한다.")
    out_path = ROOT / "docs" / "reports" / f"kr_us_style_model_{date.today().strftime('%Y%m%d')}.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n리포트 저장: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
