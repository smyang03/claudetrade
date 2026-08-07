"""계약 정렬 라벨 오프라인 실험 (read-only, 봉인 교재 + OHLC 아카이브).

2026-08-07 운영자 지시 — A1 판정 전 '준비 계측'. 라이브·모델·코호트 무접촉.

가설(모델 메모리 08-06): 현행 학습 라벨은 "5일 net"인데 실제 수확은
TP12/SL25/D5 계약 수익(비대칭·갭 상방 포획)이다. 라벨을 계약 시뮬 net으로
바꾸면 모델이 우리가 걷는 바로 그것을 배운다.

설계:
  라벨 2종을 같은 OHLC 아카이브(us_yahoo_2y)에서 재구성 —
    L0(현행식): t+1 시가 -> t+5 종가, 비용 차감
    L1(계약식): t+1 시가 진입, TP+12%/SL-25%(SL 우선, 갭은 시가 체결)/D5 종가, 비용 차감
  같은 피처·시드·purged walk-forward 로 L0 학습 vs L1 학습 두 암(arm)을 돌리고,
  평가는 두 암 모두 **계약 수익(L1 라벨)** 기준 — 우리가 실제로 걷는 것.
  코호트: 전체 + day_losers 프록시(chg<=-5, 서브셋 내 재랭킹).

사용: python tools/us_swing_contract_label_experiment.py
출력: stdout + docs/reports/us_swing_contract_label_experiment_<date>.md
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.us_daily_alpha_walkforward import (  # noqa: E402
    YAHOO_FEATURES,
    _block_bootstrap_lcb,
    load_yahoo_dataset,
    walk_forward,
)

YAHOO_DB = ROOT / "data" / "analysis" / "us_yahoo_point_in_time.db"
OHLC_DIR = ROOT / "data" / "analysis" / "us_yahoo_2y"
POLICY = ROOT / "config" / "us_swing_accelerated.json"
TP = 0.12
SL = 0.25
HOLD = 5
PROXY_CHG_LE = -5.0


def _load_bars(ticker: str) -> pd.DataFrame | None:
    path = OHLC_DIR / f"us_{ticker}.csv"
    if not path.exists():
        return None
    bars = pd.read_csv(path)
    bars["date"] = bars["date"].astype(str)
    return bars.reset_index(drop=True)


def _contract_labels(bars: pd.DataFrame, signal_date: str, cost: float) -> dict | None:
    """t+1 시가 진입 계약 시뮬. 갭은 시가 체결(상방 보너스/하방 초과손실), 동일일 TP·SL 동시면 SL 우선."""
    idx = bars.index[bars["date"] == signal_date]
    if not len(idx):
        return None
    start = int(idx[0]) + 1
    path = bars.iloc[start:start + HOLD]
    if len(path) < HOLD:
        return None
    entry = float(path.iloc[0]["open"])
    if entry <= 0:
        return None
    tp_px = entry * (1 + TP)
    sl_px = entry * (1 - SL)
    exit_px, kind = float(path.iloc[-1]["close"]), "d5"
    for day_i, (_, bar) in enumerate(path.iterrows()):
        o, h, l = float(bar["open"]), float(bar["high"]), float(bar["low"])
        if day_i > 0 and o <= sl_px:
            exit_px, kind = o, "sl_gap"
            break
        if day_i > 0 and o >= tp_px:
            exit_px, kind = o, "tp_gap"
            break
        if l <= sl_px:
            exit_px, kind = sl_px, "sl"
            break
        if h >= tp_px:
            exit_px, kind = tp_px, "tp"
            break
    label_contract = 100 * (exit_px / entry - 1) - cost
    label_5d = 100 * (float(path.iloc[-1]["close"]) / entry - 1) - cost
    return {"label_contract": label_contract, "label_5d": label_5d, "exit_kind": kind}


def _topk_on(scored: pd.DataFrame, outcome_col: str, k: int, *, seed: int) -> dict:
    picked = (
        scored.sort_values(["session_date", "alpha_score", "predicted_net_pct"],
                           ascending=[True, False, False])
        .groupby("session_date", sort=False).head(k)
    )
    daily = picked.groupby("session_date")[outcome_col].mean()
    net = daily.to_numpy(dtype=float)
    if not len(net):
        return {"sessions": 0}
    pos, neg = float(net[net > 0].sum()), float(-net[net < 0].sum())
    return {
        "sessions": int(len(net)),
        "mean": round(float(net.mean()), 3),
        "win": round(float((net > 0).mean()), 3),
        "PF": round(pos / neg, 2) if neg > 0 else None,
        "LCB5": (lambda v: round(v, 3) if v is not None else None)(
            _block_bootstrap_lcb(net, seed=seed)),
    }


def _rerank(scored: pd.DataFrame) -> pd.DataFrame:
    out = scored.copy()
    out["net_rank"] = out.groupby("session_date")["predicted_net_pct"].rank(pct=True)
    out["prob_rank"] = out.groupby("session_date")["probability"].rank(pct=True)
    out["alpha_score"] = 0.5 * out["net_rank"] + 0.5 * out["prob_rank"]
    return out


def main() -> int:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    seeds = [int(v) for v in policy.get("seeds", [20260710])]
    cost = float(policy.get("cost_pct", 0.50))
    con = sqlite3.connect(f"file:{YAHOO_DB}?mode=ro", uri=True, timeout=10)
    try:
        base = load_yahoo_dataset(con, horizon=5, cost_pct=cost)
    finally:
        con.close()
    base = base[base["change_pct"].abs().le(25.0)].copy()

    bars_cache: dict[str, pd.DataFrame | None] = {}
    labels = []
    for row in base.itertuples(index=False):
        t = str(row.ticker)
        if t not in bars_cache:
            bars_cache[t] = _load_bars(t)
        bars = bars_cache[t]
        rec = _contract_labels(bars, str(row.session_date), cost) if bars is not None else None
        labels.append(rec or {"label_contract": np.nan, "label_5d": np.nan, "exit_kind": ""})
    frame = pd.concat([base.reset_index(drop=True), pd.DataFrame(labels)], axis=1)
    frame = frame.dropna(subset=["label_contract", "label_5d"]).copy()

    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    emit(f"# 계약 정렬 라벨 오프라인 실험 ({date.today().isoformat()})")
    emit(f"\n표본 {len(frame)}행 (라벨 재구성 성공분), seeds={seeds}, cost={cost}, "
         f"계약 TP+{TP:.0%}/SL-{SL:.0%}/D{HOLD}, SL 우선, 갭 시가 체결.")
    corr = float(frame["label_5d"].corr(frame["net_krw_5d_pct"])) if "net_krw_5d_pct" in frame else float("nan")
    emit(f"검증: 재구성 5d 라벨 vs 교재 저장 라벨(KRW·FX 포함) 상관 {corr:.3f} (FX 차이만큼 <1 정상)")
    kinds = frame["exit_kind"].value_counts().to_dict()
    emit(f"계약 라벨 exit 분포: {kinds}")
    emit(f"라벨 평균: 5d {frame['label_5d'].mean():+.2f}% vs 계약 {frame['label_contract'].mean():+.2f}%")

    arms = {}
    for name, label_col in (("L0_5일라벨", "label_5d"), ("L1_계약라벨", "label_contract")):
        work = frame.copy()
        work["net_return_pct"] = work[label_col]
        work["target"] = (work[label_col] >= 0.25).astype(int)
        result = walk_forward(work, feature_columns=YAHOO_FEATURES,
                              model_seeds=seeds, return_scored_frame=True)
        arms[name] = result["_scored_frame"]

    emit("\n## 결과 — 평가는 두 암 모두 계약 수익(label_contract) 기준")
    for cohort, prep in (("전체", lambda s: s),
                         (f"day_losers(chg<={PROXY_CHG_LE})",
                          lambda s: _rerank(s[s["change_pct"].le(PROXY_CHG_LE)]))):
        emit(f"\n### {cohort}")
        for name, scored in arms.items():
            sc = prep(scored)
            emit(f"{name}: top1 {_topk_on(sc, 'label_contract', 1, seed=41)} | "
                 f"top3 {_topk_on(sc, 'label_contract', 3, seed=43)}")

    emit("\n## 판정 메모")
    emit("- 준비 계측이다. 모델 교체·재봉인은 A1 30건 판정 + 운영자 결정 후(모델 메모리 순서).")
    emit("- 실험 등록: 2라벨 x 2코호트 = 4셀 (experiment_registry에 기재).")
    out_path = ROOT / "docs" / "reports" / f"us_swing_contract_label_experiment_{date.today().strftime('%Y%m%d')}.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n리포트 저장: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
