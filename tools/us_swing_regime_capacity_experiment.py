"""2차 매트릭스: 국면 조건부 모델 + 용량(후보수) 신호 + 구조 진단 (read-only, 봉인 교재).

2026-08-07 운영자 지시 — 사전 등록 15셀 (experiment_registry 참조).
  F1 국면 조건부 모델: 좋은장/나쁜장(past-only: spy_momentum_20d_pct 부호) 분리학습 vs
     단일학습, 국면별 day_losers top1 계약 수익 비교. KR past-only 게이트 4패널 역효과
     이력이 있으므로 결과를 차단 게이트로 비약하지 않는다 — 모델 분리 가치만 잰다.
  F2 US 후보수=신호: 세션별 day_losers 프록시 후보 수 버킷별 후보당 계약 수익 —
     KR 발견(후보 수가 많은 날일수록 후보당 수익↑)의 US 독립 실측(이식 아님).
  F3 US 오버나이트 분해: D5 창의 밤(종가->익일시가) vs 낮(시가->종가) — KR P2 독립 검증.
  F6 US 랭킹 vs 풀: top1 / top3 / 풀균등(후보당) — 랭킹 부가가치의 기준선(A1 예습).
  F7 KR 후보수 버킷: R2∪R4 후보 수 구간별 후보당 수익 — 용량 신호 정량화.

사용: python tools/us_swing_regime_capacity_experiment.py
출력: stdout + docs/reports/us_swing_regime_capacity_<date>.md
"""

from __future__ import annotations

import json
import statistics as stx
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.us_daily_alpha_walkforward import (  # noqa: E402
    YAHOO_FEATURES,
    _block_bootstrap_lcb,
    expanding_month_splits,
)
from tools.us_swing_contract_label_experiment import _load_bars  # noqa: E402
from tools.us_swing_model_matrix_experiment import _alpha, _build_frame  # noqa: E402
from tools.kr_fallen_ranking_experiment import CACHES, _candidates_and_nets  # noqa: E402

PROXY_CHG_LE = -5.0
REGIME_COL = "spy_momentum_20d_pct"   # past-only: 신호일 기준 과거 20일 SPY 모멘텀


def _hgb(seed: int):
    params = dict(learning_rate=0.05, max_iter=160, max_leaf_nodes=15,
                  min_samples_leaf=35, l2_regularization=1.0, random_state=seed)
    return HistGradientBoostingRegressor(**params), HistGradientBoostingClassifier(**params)


def _walk_conditional(frame: pd.DataFrame, seeds: list[int], split_regime: bool) -> pd.DataFrame:
    parts = []
    for w, (train_d, _p, test_d) in enumerate(
        expanding_month_splits(frame, min_train_sessions=120, purge_sessions=5)
    ):
        train = frame[frame["session_date"].isin(train_d)]
        test = frame[frame["session_date"].isin(test_d)].copy()
        if train.empty or test.empty or train["target"].nunique() < 2:
            continue
        preds = np.zeros((len(seeds), len(test)))
        probs = np.zeros((len(seeds), len(test)))
        for si, seed in enumerate(seeds):
            if not split_regime:
                reg, clf = _hgb(seed + w)
                reg.fit(train[YAHOO_FEATURES], train["net_return_pct"])
                clf.fit(train[YAHOO_FEATURES], train["target"])
                preds[si] = reg.predict(test[YAHOO_FEATURES])
                probs[si] = clf.predict_proba(test[YAHOO_FEATURES])[:, 1]
                continue
            for good in (True, False):
                tr = train[(train[REGIME_COL] > 0) == good]
                mask = ((test[REGIME_COL] > 0) == good).to_numpy()
                if not mask.any():
                    continue
                if len(tr) < 200 or tr["target"].nunique() < 2:
                    tr = train  # 국면 표본 부족 시 전체로 폴백 (얇은 국면 과적합 방지)
                reg, clf = _hgb(seed + w)
                reg.fit(tr[YAHOO_FEATURES], tr["net_return_pct"])
                clf.fit(tr[YAHOO_FEATURES], tr["target"])
                preds[si][mask] = reg.predict(test[YAHOO_FEATURES][mask])
                probs[si][mask] = clf.predict_proba(test[YAHOO_FEATURES][mask])[:, 1]
        test["predicted_net_pct"] = preds.mean(axis=0)
        test["probability"] = probs.mean(axis=0)
        parts.append(test)
    return pd.concat(parts, ignore_index=True)


def _top1_stats(scored: pd.DataFrame, *, seed: int) -> dict:
    picked = (scored.sort_values(["session_date", "alpha_score", "predicted_net_pct"],
                                 ascending=[True, False, False])
              .groupby("session_date", sort=False).head(1))
    net = picked.groupby("session_date")["label_contract"].mean().sort_index().to_numpy(float)
    if not len(net):
        return {"sessions": 0}
    pos, neg = float(net[net > 0].sum()), float(-net[net < 0].sum())
    return {"sessions": int(len(net)), "mean": round(float(net.mean()), 3),
            "win": round(float((net > 0).mean()), 3),
            "PF": round(pos / neg, 2) if neg > 0 else None,
            "LCB5": (lambda v: round(v, 3) if v is not None else None)(
                _block_bootstrap_lcb(net, seed=seed))}


def main() -> int:
    frame = _build_frame()
    seeds = [20260710, 20260711, 20260712]
    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    emit(f"# 2차 매트릭스: 국면·용량·구조 ({date.today().isoformat()}) — 사전 등록 15셀")
    proxy_all = frame[frame["change_pct"].le(PROXY_CHG_LE)]
    emit(f"\n표본 {len(frame)}행 / day_losers 프록시 {len(proxy_all)}행. "
         f"국면 = {REGIME_COL} 부호(past-only).")

    # F1 국면 조건부 모델
    emit("\n## F1 국면 조건부 모델 (day_losers top1, 계약 수익)")
    single = _walk_conditional(frame, seeds, split_regime=False)
    split = _walk_conditional(frame, seeds, split_regime=True)
    for label, scored in (("단일학습(현행)", single), ("국면분리학습", split)):
        sub = _alpha(scored[scored["change_pct"].le(PROXY_CHG_LE)], "combo")
        for regime, mask in (("좋은장", sub[REGIME_COL] > 0), ("나쁜장", sub[REGIME_COL] <= 0)):
            emit(f"F1 {label} × {regime}: {_top1_stats(sub[mask], seed=81)}")
        emit(f"F1 {label} × 전체 : {_top1_stats(sub, seed=82)}")

    # F2 후보수=신호 (US)
    emit("\n## F2 US 후보수=신호 (프록시 후보 수 버킷별 후보당 계약 수익)")
    counts = proxy_all.groupby("session_date")["ticker"].count()
    for name, lo, hi in (("1~2개", 1, 2), ("3~5개", 3, 5), ("6개+", 6, 10 ** 6)):
        days = counts[(counts >= lo) & (counts <= hi)].index
        rows = proxy_all[proxy_all["session_date"].isin(days)]
        if len(rows):
            v = rows["label_contract"]
            emit(f"F2 {name}: 일수 {len(days)} 후보 {len(rows)} | 후보당 평균 {v.mean():+.2f}% "
                 f"승률 {100 * (v > 0).mean():.0f}%")

    # F3 오버나이트 분해 (D5 고정창, gross)
    emit("\n## F3 US 오버나이트 분해 (진입 후 5일: 밤=종가->익일시가, 낮=시가->종가, gross 합)")
    cache: dict[str, pd.DataFrame | None] = {}
    for label, cohort in (("day_losers 프록시", proxy_all), ("전체", frame)):
        nights, days_ = [], []
        for row in cohort.itertuples(index=False):
            t = str(row.ticker)
            if t not in cache:
                cache[t] = _load_bars(t)
            bars = cache[t]
            if bars is None:
                continue
            idx = bars.index[bars["date"] == str(row.session_date)]
            if not len(idx):
                continue
            path = bars.iloc[int(idx[0]) + 1:int(idx[0]) + 6]
            if len(path) < 5:
                continue
            day_sum = sum(100 * (r.close / r.open - 1) for r in path.itertuples() if r.open > 0)
            night_sum = sum(100 * (path.iloc[k + 1]["open"] / path.iloc[k]["close"] - 1)
                            for k in range(len(path) - 1) if path.iloc[k]["close"] > 0)
            days_.append(day_sum)
            nights.append(night_sum)
        if nights:
            emit(f"F3 {label}: 밤 {stx.mean(nights):+.2f}% / 낮 {stx.mean(days_):+.2f}% (n={len(nights)})")

    # F6 랭킹 vs 풀 (HGB 단일학습 기준)
    emit("\n## F6 US 랭킹 vs 풀균등 (계약 수익)")
    sub = _alpha(single[single["change_pct"].le(PROXY_CHG_LE)], "combo")
    emit(f"F6 top1  : {_top1_stats(sub, seed=91)}")
    top3 = (sub.sort_values(["session_date", "alpha_score"], ascending=[True, False])
            .groupby("session_date", sort=False).head(3))
    v3 = top3.groupby("session_date")["label_contract"].mean().to_numpy(float)
    emit(f"F6 top3  : 세션 {len(v3)} 평균 {v3.mean():+.3f}% 승률 {100 * (v3 > 0).mean():.0f}%")
    pool = sub.groupby("session_date")["label_contract"].mean().to_numpy(float)
    emit(f"F6 풀균등: 세션 {len(pool)} 평균 {pool.mean():+.3f}% 승률 {100 * (pool > 0).mean():.0f}% "
         f"| 후보당 평균 {sub['label_contract'].mean():+.3f}%")

    # F7 KR 후보수 버킷
    emit("\n## F7 KR 후보수 버킷 (R2∪R4, 후보당 계약 수익)")
    for year, path in CACHES.items():
        cands = _candidates_and_nets(json.loads(path.read_text(encoding="utf-8")))
        by: dict[str, list] = {}
        for c in cands:
            by.setdefault(c["session"], []).append(c["net"])
        for name, lo, hi in (("1개", 1, 1), ("2~9개", 2, 9), ("10개+", 10, 10 ** 6)):
            nets = [x for v in by.values() if lo <= len(v) <= hi for x in v]
            days_n = sum(1 for v in by.values() if lo <= len(v) <= hi)
            if nets:
                emit(f"F7 {year} {name}: 일수 {days_n} 후보 {len(nets)} | 후보당 "
                     f"{stx.mean(nets):+.2f}% 승률 {100 * sum(1 for x in nets if x > 0) / len(nets):.0f}%")

    emit("\n## 판정 메모")
    emit("- 계측 전용. F1 결과의 게이트화 비약 금지(KR past-only 게이트 4패널 역효과 이력).")
    out_path = ROOT / "docs" / "reports" / f"us_swing_regime_capacity_{date.today().strftime('%Y%m%d')}.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n리포트 저장: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
