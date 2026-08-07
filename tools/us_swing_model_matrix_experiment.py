"""모델·데이터 매트릭스 오프라인 실험 (read-only, 봉인 교재 — 사전 등록 9셀).

2026-08-07 운영자 지시. experiment_registry_20260807.md 에 사전 등록된 축:
  E1 모델 패밀리: HGB(현행) / RandomForest / ElasticNet(선형)
  E2 결합 방식:   회귀+분류 50:50(현행) / 회귀 단독 / 분류 단독  (base 예측 재가중 — 추가 학습 없음)
  E3 목표 변환:   raw net / 횡단면 rank (회귀 목표만 변환, 분류 목표 불변)
  E4 확률 보정:   isotonic (랭킹 불변 — 허들 진단 전용)

고정: 계약 라벨(TP12/SL25/D5, 08-07 실험 승자), 피처 27종, purged expanding walk-forward,
      seeds=정책 3종. 평가축: 계약 수익 기준 day_losers 프록시(chg<=-5) top1 + 전/후반 재현.

사용: python tools/us_swing_model_matrix_experiment.py
출력: stdout + docs/reports/us_swing_model_matrix_<date>.md
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import (
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.us_daily_alpha_walkforward import (  # noqa: E402
    YAHOO_FEATURES,
    _block_bootstrap_lcb,
    expanding_month_splits,
    load_yahoo_dataset,
)
from tools.us_swing_contract_label_experiment import _contract_labels, _load_bars  # noqa: E402

YAHOO_DB = ROOT / "data" / "analysis" / "us_yahoo_point_in_time.db"
POLICY = ROOT / "config" / "us_swing_accelerated.json"
PROXY_CHG_LE = -5.0
PROB_BINS = [0.0, 0.45, 0.50, 0.55, 0.60, 0.65, 1.0]


def _build_frame() -> pd.DataFrame:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    cost = float(policy.get("cost_pct", 0.50))
    con = sqlite3.connect(f"file:{YAHOO_DB}?mode=ro", uri=True, timeout=10)
    try:
        base = load_yahoo_dataset(con, horizon=5, cost_pct=cost)
    finally:
        con.close()
    base = base[base["change_pct"].abs().le(25.0)].copy()
    cache: dict[str, pd.DataFrame | None] = {}
    recs = []
    for row in base.itertuples(index=False):
        t = str(row.ticker)
        if t not in cache:
            cache[t] = _load_bars(t)
        rec = _contract_labels(cache[t], str(row.session_date), cost) if cache[t] is not None else None
        recs.append(rec or {"label_contract": np.nan, "label_5d": np.nan, "exit_kind": ""})
    frame = pd.concat([base.reset_index(drop=True), pd.DataFrame(recs)], axis=1)
    frame = frame.dropna(subset=["label_contract"]).copy()
    frame["net_return_pct"] = frame["label_contract"]
    frame["target"] = (frame["label_contract"] >= 0.25).astype(int)
    return frame


def _make_models(family: str, seed: int):
    params = dict(learning_rate=0.05, max_iter=160, max_leaf_nodes=15,
                  min_samples_leaf=35, l2_regularization=1.0, random_state=seed)
    if family == "HGB":
        return HistGradientBoostingRegressor(**params), HistGradientBoostingClassifier(**params)
    if family == "RF":
        common = dict(n_estimators=200, max_depth=6, min_samples_leaf=35,
                      random_state=seed, n_jobs=-1)
        pre = SimpleImputer(strategy="median")
        return (make_pipeline(pre, RandomForestRegressor(**common)),
                make_pipeline(SimpleImputer(strategy="median"), RandomForestClassifier(**common)))
    if family == "ENet":
        return (
            make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                          ElasticNet(alpha=0.01, l1_ratio=0.5, random_state=seed, max_iter=5000)),
            make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                          LogisticRegression(C=1.0, max_iter=1000, random_state=seed)),
        )
    raise ValueError(family)


def _walk(frame: pd.DataFrame, *, family: str, seeds: list[int],
          reg_target: str = "raw", calibrate: bool = False) -> pd.DataFrame:
    parts = []
    for w, (train_d, _purge, test_d) in enumerate(
        expanding_month_splits(frame, min_train_sessions=120, purge_sessions=5)
    ):
        train = frame[frame["session_date"].isin(train_d)]
        test = frame[frame["session_date"].isin(test_d)].copy()
        if train.empty or test.empty or train["target"].nunique() < 2:
            continue
        y_reg = (train.groupby("session_date")["net_return_pct"].rank(pct=True)
                 if reg_target == "csrank" else train["net_return_pct"])
        preds, probs = [], []
        for seed in seeds:
            reg, clf = _make_models(family, seed + w)
            if calibrate:
                clf = CalibratedClassifierCV(clf, method="isotonic", cv=3)
            reg.fit(train[YAHOO_FEATURES], y_reg)
            clf.fit(train[YAHOO_FEATURES], train["target"])
            preds.append(reg.predict(test[YAHOO_FEATURES]))
            probs.append(clf.predict_proba(test[YAHOO_FEATURES])[:, 1])
        test["predicted_net_pct"] = np.mean(preds, axis=0)
        test["probability"] = np.mean(probs, axis=0)
        parts.append(test)
    return pd.concat(parts, ignore_index=True)


def _alpha(scored: pd.DataFrame, mode: str) -> pd.DataFrame:
    out = scored.copy()
    out["net_rank"] = out.groupby("session_date")["predicted_net_pct"].rank(pct=True)
    out["prob_rank"] = out.groupby("session_date")["probability"].rank(pct=True)
    out["alpha_score"] = {"combo": 0.5 * out["net_rank"] + 0.5 * out["prob_rank"],
                          "reg": out["net_rank"], "clf": out["prob_rank"]}[mode]
    return out


def _top1(scored: pd.DataFrame, *, seed: int) -> dict:
    picked = (scored.sort_values(["session_date", "alpha_score", "predicted_net_pct"],
                                 ascending=[True, False, False])
              .groupby("session_date", sort=False).head(1))
    daily = picked.groupby("session_date")["label_contract"].mean().sort_index()
    net = daily.to_numpy(dtype=float)
    if not len(net):
        return {"sessions": 0}
    pos, neg = float(net[net > 0].sum()), float(-net[net < 0].sum())
    half = len(net) // 2
    return {
        "sessions": int(len(net)),
        "mean": round(float(net.mean()), 3),
        "win": round(float((net > 0).mean()), 3),
        "PF": round(pos / neg, 2) if neg > 0 else None,
        "LCB5": (lambda v: round(v, 3) if v is not None else None)(
            _block_bootstrap_lcb(net, seed=seed)),
        "전반": round(float(net[:half].mean()), 3),
        "후반": round(float(net[half:].mean()), 3),
    }


def _proxy(scored: pd.DataFrame, mode: str) -> pd.DataFrame:
    return _alpha(scored[scored["change_pct"].le(PROXY_CHG_LE)], mode)


def main() -> int:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    seeds = [int(v) for v in policy.get("seeds", [20260710])]
    frame = _build_frame()
    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    emit(f"# 모델·데이터 매트릭스 실험 ({date.today().isoformat()}) — 사전 등록 9셀")
    emit(f"\n표본 {len(frame)}행, 계약 라벨 고정, seeds={seeds}. "
         f"평가: day_losers(chg<={PROXY_CHG_LE}) top1, 계약 수익 기준, 전/후반 재현 병기.")

    runs: dict[str, pd.DataFrame] = {}
    emit("\n## E1 모델 패밀리 (combo, raw)")
    for family in ("HGB", "RF", "ENet"):
        runs[family] = _walk(frame, family=family, seeds=seeds)
        emit(f"E1 {family:4s}: {_top1(_proxy(runs[family], 'combo'), seed=51)}")

    emit("\n## E2 결합 방식 (HGB base 예측 재가중 — 추가 학습 없음)")
    for mode in ("combo", "reg", "clf"):
        emit(f"E2 {mode:5s}: {_top1(_proxy(runs['HGB'], mode), seed=61)}")

    emit("\n## E3 목표 변환 (HGB, combo)")
    emit(f"E3 raw   : {_top1(_proxy(runs['HGB'], 'combo'), seed=71)} (E1 HGB와 동일 셀)")
    csr = _walk(frame, family="HGB", seeds=seeds, reg_target="csrank")
    emit(f"E3 csrank: {_top1(_proxy(csr, 'combo'), seed=72)}")

    emit("\n## E4 확률 보정 진단 (HGB + isotonic — 랭킹 아님, 허들 진단)")
    cal = _walk(frame, family="HGB", seeds=[seeds[0]], calibrate=True)
    sub = cal[cal["change_pct"].le(PROXY_CHG_LE)].dropna(subset=["probability", "target"])
    for lo, hi in zip(PROB_BINS[:-1], PROB_BINS[1:]):
        part = sub[(sub["probability"] >= lo) & (sub["probability"] < hi)]
        if len(part):
            emit(f"  [{lo:.2f},{hi:.2f}) n={len(part)} 예측 {part['probability'].mean():.3f} "
                 f"실측 {part['target'].mean():.3f} net {part['label_contract'].mean():+.2f}%")
    brier = float(((sub["probability"] - sub["target"]) ** 2).mean())
    raw_pass = int((runs["HGB"][runs["HGB"]["change_pct"].le(PROXY_CHG_LE)]["probability"] >= 0.55).sum())
    cal_pass = int((sub["probability"] >= 0.55).sum())
    emit(f"  Brier {brier:.4f} (raw 대비 개선 여부 확인) | 0.55 통과: raw {raw_pass} -> isotonic {cal_pass}")

    emit("\n## 판정 메모")
    emit("- 전부 계측 — 채택은 A1 30건 판정 + 재봉인 + 운영자. 승자 선택 시 전/후반 재현 미달 셀은 제외.")
    out_path = ROOT / "docs" / "reports" / f"us_swing_model_matrix_{date.today().strftime('%Y%m%d')}.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n리포트 저장: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
