"""US swing day_losers 정렬 검증 + 확률 calibration 리포트 (read-only, 봉인 교재).

2026-08-07 외부 검토 P0/P1 반영 — 모델·코호트는 건드리지 않는 오프라인 계측:
  [1] 학습 분포 vs 운용 분포: 학습은 교재 전체, 실주문 후보는 day_losers만.
      A: 전체학습 -> 전체평가 (기존 기준선)
      B: 전체학습 -> day_losers 프록시 평가 (현행 라이브 미러)
      C: day_losers 프록시 학습 -> 동일 평가 (분포 정렬 대안)
  [2] 확률 calibration: predict_proba가 실제 성공률(net>=0.25)과 맞는지 —
      구간별 실측 성공률·평균 net·Brier. min_probability=0.55 허들의 근거 자료.
  [3] forward 원장 교차: 라이브 신호의 확률 구간별 실측 (표본 소수, 참고용).

day_losers 프록시 = change_pct <= -5 (교재에 소스 라벨이 없어 급락밴드로 근사.
실제 Finviz day_losers 와 문턱이 다를 수 있음 — 재봉인 때 소스 라벨 부착이 정본).

사용: python tools/us_swing_dayloser_calibration_report.py
출력: stdout + docs/reports/us_swing_dayloser_calibration_<date>.md
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
SHADOW_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
POLICY = ROOT / "config" / "us_swing_accelerated.json"
PROXY_CHG_LE = -5.0
PROB_BINS = [0.0, 0.45, 0.50, 0.55, 0.60, 0.65, 1.0]


def _topk_metrics(scored: pd.DataFrame, k: int, *, seed: int) -> dict:
    picked = (
        scored.sort_values(["session_date", "alpha_score", "predicted_net_pct"],
                           ascending=[True, False, False])
        .groupby("session_date", sort=False).head(k)
    )
    daily = picked.groupby("session_date")["net_return_pct"].mean()
    net = daily.to_numpy(dtype=float)
    if not len(net):
        return {"sessions": 0}
    pos = float(net[net > 0].sum())
    neg = float(-net[net < 0].sum())
    return {
        "sessions": int(len(net)),
        "mean_net_pct": round(float(net.mean()), 3),
        "median_net_pct": round(float(np.median(net)), 3),
        "win_rate": round(float((net > 0).mean()), 3),
        "profit_factor": round(pos / neg, 2) if neg > 0 else None,
        "lcb5_pct": (lambda v: round(v, 3) if v is not None else None)(
            _block_bootstrap_lcb(net, seed=seed)),
    }


def _rerank_within(scored: pd.DataFrame) -> pd.DataFrame:
    """서브셋 안에서 세션별 순위 재계산 — '오늘 day_losers 중 1등' 관점."""
    out = scored.copy()
    out["net_rank"] = out.groupby("session_date")["predicted_net_pct"].rank(pct=True)
    out["prob_rank"] = out.groupby("session_date")["probability"].rank(pct=True)
    out["alpha_score"] = 0.5 * out["net_rank"] + 0.5 * out["prob_rank"]
    return out


def _calibration_table(scored: pd.DataFrame) -> list[dict]:
    frame = scored.dropna(subset=["probability", "target", "net_return_pct"])
    rows = []
    for lo, hi in zip(PROB_BINS[:-1], PROB_BINS[1:]):
        part = frame[(frame["probability"] >= lo) & (frame["probability"] < hi)]
        if part.empty:
            rows.append({"bin": f"[{lo:.2f},{hi:.2f})", "n": 0})
            continue
        rows.append({
            "bin": f"[{lo:.2f},{hi:.2f})",
            "n": int(len(part)),
            "predicted_mean": round(float(part["probability"].mean()), 3),
            "realized_rate": round(float(part["target"].mean()), 3),
            "mean_net_pct": round(float(part["net_return_pct"].mean()), 3),
        })
    brier = float(((frame["probability"] - frame["target"]) ** 2).mean())
    base = float(frame["target"].mean())
    rows.append({"bin": "TOTAL", "n": int(len(frame)),
                 "brier": round(brier, 4), "base_rate": round(base, 3)})
    return rows


def _forward_ledger_bins() -> list[dict]:
    con = sqlite3.connect(f"file:{SHADOW_DB}?mode=ro", uri=True, timeout=10)
    try:
        frame = pd.read_sql_query(
            """SELECT probability, net_krw_pct FROM signals
               WHERE status='MATURED' AND probability IS NOT NULL""", con)
    finally:
        con.close()
    if frame.empty:
        return []
    frame["target"] = (frame["net_krw_pct"] >= 0.25).astype(int)
    frame["net_return_pct"] = frame["net_krw_pct"]
    return _calibration_table(frame)


def main() -> int:
    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    seeds = [int(v) for v in policy.get("seeds", [20260710])]
    cost = float(policy.get("cost_pct", 0.50))
    con = sqlite3.connect(f"file:{YAHOO_DB}?mode=ro", uri=True, timeout=10)
    try:
        frame = load_yahoo_dataset(con, horizon=5, cost_pct=cost)
    finally:
        con.close()
    frame = frame[frame["change_pct"].abs().le(25.0)].copy()  # 라이브 eligibility 미러
    proxy_mask = frame["change_pct"].le(PROXY_CHG_LE)
    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    emit(f"# US swing day_losers 정렬 검증 + calibration ({date.today().isoformat()})")
    emit(f"\n교재 {len(frame)}행 ({frame['session_date'].min()}~{frame['session_date'].max()}), "
         f"day_losers 프록시(chg<={PROXY_CHG_LE}) {int(proxy_mask.sum())}행. "
         f"seeds={seeds}, cost={cost}, horizon=5, purge=5.")

    # A: 전체학습 -> 전체평가
    result_a = walk_forward(frame, feature_columns=YAHOO_FEATURES,
                            model_seeds=seeds, return_scored_frame=True)
    scored_a = result_a["_scored_frame"]
    emit("\n## [1] 분포 정렬 비교 (세션당 top-k 평균 net, %)")
    emit(f"\nA. 전체학습->전체평가: top1 {_topk_metrics(scored_a, 1, seed=1)}")
    emit(f"   top3 {_topk_metrics(scored_a, 3, seed=3)}")

    # B: 전체학습 -> day_losers 평가 (라이브 미러: 채점은 전체 모델, 후보만 프록시)
    scored_b = _rerank_within(scored_a[scored_a["change_pct"].le(PROXY_CHG_LE)])
    emit(f"\nB. 전체학습->day_losers평가(라이브 미러): top1 {_topk_metrics(scored_b, 1, seed=11)}")
    emit(f"   top3 {_topk_metrics(scored_b, 3, seed=13)}")

    # C: day_losers 학습 -> day_losers 평가 (표본 얇음 — min_train_sessions 완화)
    frame_c = frame[proxy_mask].copy()
    result_c = walk_forward(frame_c, feature_columns=YAHOO_FEATURES,
                            model_seeds=seeds, min_train_sessions=60,
                            return_scored_frame=True)
    scored_c = result_c.get("_scored_frame")
    if scored_c is not None and len(scored_c):
        emit(f"\nC. day_losers학습->day_losers평가: top1 {_topk_metrics(scored_c, 1, seed=21)}")
        emit(f"   top3 {_topk_metrics(scored_c, 3, seed=23)}")
        emit(f"   (학습 표본 {int(proxy_mask.sum())}행 — 얇음. 판정보다 방향 참고)")
    else:
        emit("\nC. day_losers 학습: 표본 부족으로 walk-forward 창 미형성")

    emit("\n## [2] 확률 calibration (walk-forward 시험구간, target=net>=0.25)")
    for label, sc in (("전체 시험행", scored_a), ("day_losers 프록시 행", scored_b)):
        emit(f"\n### {label}")
        for row in _calibration_table(sc):
            emit(f"  {row}")

    emit("\n## [3] forward 원장 교차 (라이브 신호, 5d 원장 회계 — 참고용 소표본)")
    fwd = _forward_ledger_bins()
    if fwd:
        for row in fwd:
            emit(f"  {row}")
    else:
        emit("  성숙 표본 없음")

    emit("\n판정 메모: 이 리포트는 계측이다 — 허들·모델 변경은 운영자 결정+재봉인 절차로만.")
    out_path = ROOT / "docs" / "reports" / f"us_swing_dayloser_calibration_{date.today().strftime('%Y%m%d')}.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n리포트 저장: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
