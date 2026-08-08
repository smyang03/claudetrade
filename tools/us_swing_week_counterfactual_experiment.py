"""W1 주간 카운터팩추얼 — 이번 주 실제 후보 풀에 모델 변형 6종을 소급 채점 (read-only).

2026-08-08 운영자 지시: "여러 모델로 이번 주 아쉬운 승자를 잡을 수 있었는지 시뮬".
설계 원칙:
  - 평가판 = 이번 주 실제 preopen 스냅샷 5세션 + 실제 가격 경로의 계약 정산.
  - 학습은 **봉인 교재만**(as-of 계약 적용) — 주간 데이터로 학습하면 5세션 과적합이라
    금지. 변형이 주간 승자를 잡는지는 순수 out-of-sample 채점으로만 판정한다.
  - 변형은 기존 실험(계약 라벨·결합·국면분리·RF)에서 온 것 — 새 탐색이 아니라
    기존 후보들의 "이번 주" 성적표다.

변형: M0 현행(5d라벨) / M1 계약라벨 / M2 계약라벨+분류rank단독 / M3 계약라벨+회귀rank단독
     / M4 계약라벨+나쁜장분리 / M5 RF계약라벨(기각 패밀리 대조군)

사용: python tools/us_swing_week_counterfactual_experiment.py
출력: stdout + docs/reports/us_swing_week_counterfactual_<date>.md
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("US_SWING_ALLOWED_SOURCES", "day_losers")

from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor  # noqa: E402

from tools.us_daily_alpha_walkforward import YAHOO_FEATURES  # noqa: E402
from tools.us_swing_model_matrix_experiment import _build_frame, _make_models  # noqa: E402
from tools.us_swing_shadow_runner import load_candidate_features  # noqa: E402

PRICE_DIR = ROOT / "data" / "price" / "us"
SESSIONS = ["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"]
SEEDS = [20260710, 20260711, 20260712]
REGIME_COL = "spy_momentum_20d_pct"
BOUGHT = {"2026-08-03": "FRMI", "2026-08-04": "CVI"}


def _hgb(seed: int):
    return _make_models("HGB", seed)


def _train_variant(frame: pd.DataFrame, name: str):
    """변형별 (예측함수) 반환 — candidates frame -> (pred, prob) 배열."""
    y5 = frame["label_5d"]
    yc = frame["label_contract"]
    t5 = (y5 >= 0.25).astype(int)
    tc = (yc >= 0.25).astype(int)
    fitted = []
    for seed in SEEDS:
        if name == "M5_RF계약":
            reg, clf = _make_models("RF", seed)
        else:
            reg, clf = _hgb(seed)
        if name == "M0_현행5d":
            reg.fit(frame[YAHOO_FEATURES], y5)
            clf.fit(frame[YAHOO_FEATURES], t5)
            fitted.append((reg, clf))
        elif name == "M4_나쁜장분리":
            pair = {}
            for good in (True, False):
                sub = frame[(frame[REGIME_COL] > 0) == good]
                if len(sub) < 200 or sub["label_contract"].ge(0.25).nunique() < 2:
                    sub = frame
                r2, c2 = _hgb(seed)
                r2.fit(sub[YAHOO_FEATURES], sub["label_contract"])
                c2.fit(sub[YAHOO_FEATURES], (sub["label_contract"] >= 0.25).astype(int))
                pair[good] = (r2, c2)
            fitted.append(pair)
        else:
            reg.fit(frame[YAHOO_FEATURES], yc)
            clf.fit(frame[YAHOO_FEATURES], tc)
            fitted.append((reg, clf))

    def predict(cands: pd.DataFrame):
        preds, probs = [], []
        for item in fitted:
            if name == "M4_나쁜장분리":
                good = bool(np.nanmean(cands[REGIME_COL].to_numpy(float)) > 0)
                reg, clf = item[good]
            else:
                reg, clf = item
            preds.append(reg.predict(cands[YAHOO_FEATURES]))
            probs.append(clf.predict_proba(cands[YAHOO_FEATURES])[:, 1])
        return np.mean(preds, axis=0), np.mean(probs, axis=0)

    return predict


def _rank(cands: pd.DataFrame, pred, prob, mode: str) -> pd.DataFrame:
    out = cands.copy()
    out["predicted_net_pct"] = pred
    out["probability"] = prob
    out["net_rank"] = out["predicted_net_pct"].rank(pct=True)
    out["prob_rank"] = out["probability"].rank(pct=True)
    out["alpha_score"] = {"combo": 0.5 * out["net_rank"] + 0.5 * out["prob_rank"],
                          "clf": out["prob_rank"], "reg": out["net_rank"]}[mode]
    return out.sort_values(["alpha_score", "predicted_net_pct"], ascending=False)


def _contract_outcome(ticker: str, session_date: str) -> tuple[float, str] | None:
    path = PRICE_DIR / f"us_{ticker}.csv"
    if not path.exists():
        return None
    bars = pd.read_csv(path)
    bars["date"] = bars["date"].astype(str)
    idx = bars.index[bars["date"] == session_date]
    if not len(idx):
        return None
    # US 규약: 신호는 개장 전(preopen) 생성 -> 진입은 signal_date **당일** 시가
    # (라이브 FRMI 08-03 22:35 KST = 08-03 09:35 ET 체결 실측과 일치).
    after = bars.iloc[int(idx[0]):int(idx[0]) + 5]
    if after.empty:
        return None
    e = float(after.iloc[0]["open"])
    if e <= 0:
        return None
    tp, sl = e * 1.12, e * 0.75
    for k, bar in enumerate(after.itertuples()):
        if k > 0 and bar.open <= sl:
            return 100 * (bar.open / e - 1), "sl_gap"
        if k > 0 and bar.open >= tp:
            return 100 * (bar.open / e - 1), "tp_gap"
        if bar.low <= sl:
            return 100 * (sl / e - 1), "sl"
        if bar.high >= tp:
            return 100 * (tp / e - 1), "tp"
    kind = "d5" if len(after) >= 5 else f"진행중({len(after)}일)"
    return 100 * (float(after.iloc[-1]["close"]) / e - 1), kind


VARIANTS = {
    "M0_현행5d": "combo", "M1_계약라벨": "combo", "M2_계약clf단독": "clf",
    "M3_계약reg단독": "reg", "M4_나쁜장분리": "combo", "M5_RF계약": "combo",
}


def main() -> int:
    frame = _build_frame()
    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    emit(f"# W1 주간 카운터팩추얼 ({date.today().isoformat()}) — 변형 6 × 실풀 5세션")
    emit("\n학습=봉인 교재만(주간 데이터 학습 금지). 평가=실 스냅샷 풀·실가격 계약 정산(금요일까지, 미완주는 진행중 표기).")

    predictors = {name: _train_variant(frame, name) for name in VARIANTS}

    outcomes_cache: dict[tuple, tuple | None] = {}

    def outcome(t, d):
        if (t, d) not in outcomes_cache:
            outcomes_cache[(t, d)] = _contract_outcome(t, d)
        return outcomes_cache[(t, d)]

    weekly = {name: [] for name in VARIANTS}
    for d in SESSIONS:
        snap = ROOT / "state" / f"preopen_US_{d.replace('-', '')}.json"
        veto_path = ROOT / "state" / f"us_swing_veto_{d.replace('-', '')}.json"
        vetoes = {}
        if veto_path.exists():
            try:
                vetoes = json.loads(veto_path.read_text(encoding="utf-8")).get("vetoes") or {}
            except ValueError:
                pass
        cands, _errors = load_candidate_features(
            snapshot_path=snap, price_dir=PRICE_DIR, session_date=d, vetoes=vetoes)
        cands = cands[cands["veto_reason"].astype(str).eq("")].copy()
        if cands.empty:
            emit(f"\n[{d}] 후보 없음")
            continue
        emit(f"\n[{d}] 풀 {len(cands)}종 (실매수: {BOUGHT.get(d, '없음')})")
        for name, mode in VARIANTS.items():
            ranked = _rank(cands, *predictors[name](cands), mode)
            top = ranked.iloc[0]
            res = outcome(str(top["ticker"]), d)
            net, kind = (res if res else (float("nan"), "?"))
            weekly[name].append((d, str(top["ticker"]), net, kind))
            emit(f"  {name:12s} rank1 = {top['ticker']:5s} -> {net:+6.1f}% ({kind})")

    emit("\n## 주간 합산 (rank1 5픽, 계약 정산·미완주 포함)")
    for name, picks in weekly.items():
        nets = [n for _, _, n, _ in picks if n == n]
        emit(f"{name:12s}: 합 {sum(nets):+6.1f}%p | 픽 {[(t, f'{n:+.1f}') for _, t, n, _ in picks]}")
    # 실제 라이브(허들 있던 세계): FRMI+CVI만 진입
    live = []
    for d, t in BOUGHT.items():
        res = outcome(t, d)
        if res:
            live.append((t, res[0]))
    emit(f"{'실제(허들)':12s}: 합 {sum(n for _, n in live):+6.1f}%p | 픽 {[(t, f'{n:+.1f}') for t, n in live]} (차단 3일 미진입)")

    emit("\n## 판정 메모")
    emit("- 5세션 표본 — 변형 선택 근거가 아니라 '이번 주 아쉬움을 잡았을 변형이 있었나'의 확인.")
    emit("- 채택 결정은 여전히 293세션 오프라인 + forward 게이트 + 운영자.")
    out_path = ROOT / "docs" / "reports" / f"us_swing_week_counterfactual_{date.today().strftime('%Y%m%d')}.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n리포트 저장: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
