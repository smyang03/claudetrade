"""T1-V2 2차 검증 — lookahead 제거 + 단독축 재검증 + 이상치 감사 (read-only).

2026-08-11. 1차 검증에서 나온 두 후보를 실제 운용 가능한 형태로 못박는다.
  W1 US: ATR% 단독 조건(rv20 교차는 전반 음수 — 기각)으로 walk-forward 세션 비교.
         모델 rank1 vs 조건 통과 전량 vs 조건∩rank1, 기간 분리.
  W2 KR: 분위(Q1) 조건의 **절대 임계값** 산출 + **past-only 확장창 분위**로 재검증
         — 전 기간 분위는 미래 정보다(lookahead). 절대 임계로 고정 가능한지 본다.
  W3 이상치 감사: KR 갭Q1 코호트의 최악 −100% 정체(데이터 오염 여부).

사용: python tools/tp_capture_verify2_experiment.py
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.tp_capture_structure_experiment import _kr_table, _tp_rate, _us_table  # noqa: E402
from tools.us_swing_model_matrix_experiment import _alpha, _build_frame  # noqa: E402
from tools.us_swing_regime_capacity_experiment import _walk_conditional  # noqa: E402

SEEDS = [20260710, 20260711, 20260712]
PROXY_CHG_LE = -5.0


def _fmt(g: pd.DataFrame, label: str) -> str:
    if not len(g):
        return f"{label}: n=0"
    net = g["label_contract"]
    return (f"{label}: n={len(g)} TP {100*_tp_rate(g['exit_kind']):.1f}% net {net.mean():+.2f}% "
            f"승률 {100*(net>0).mean():.0f}% 최악 {net.min():+.0f}%")


def main() -> int:
    lines: list[str] = []

    def emit(t: str = "") -> None:
        print(t)
        lines.append(t)

    emit(f"# T1-V2 2차 검증 ({date.today().isoformat()}) — lookahead 제거·단독축·이상치")

    # ---------- W1: US ATR 단독 ----------
    emit("\n## W1 US ATR% 단독 조건 (rv20 교차는 전반 음수 — 기각됨)")
    frame = _build_frame()
    scored = _walk_conditional(frame, SEEDS, split_regime=False)
    scored = _alpha(scored[scored["change_pct"].le(PROXY_CHG_LE)], "combo")
    dates = sorted(scored["session_date"].unique())
    mid = dates[len(dates) // 2]
    scored["period"] = np.where(scored["session_date"] < mid, "전반", "후반")
    n_sessions = scored["session_date"].nunique()
    rank1 = (scored.sort_values(["session_date", "alpha_score", "predicted_net_pct"],
                                ascending=[True, False, False])
             .groupby("session_date", sort=False).head(1))
    emit(f"  기준 (a) 모델 rank1: {_fmt(rank1, '')}")
    for p, g in rank1.groupby("period", observed=True):
        emit(f"      [{p}] net {g['label_contract'].mean():+.2f}% TP {100*_tp_rate(g['exit_kind']):.0f}%")
    # ATR 임계는 **과거 확장창**으로 산출(lookahead 제거): 각 세션 t의 임계 = t 이전 데이터의 분위
    ordered = scored.sort_values("session_date").reset_index(drop=True)
    for pct in (0.70, 0.75, 0.80):
        thr_series, prev = [], []
        for _, row in ordered.iterrows():
            thr_series.append(np.quantile(prev, pct) if len(prev) >= 100 else np.inf)
            prev.append(float(row["atr_pct"]))
        ordered["_thr"] = thr_series
        hit = ordered[ordered["atr_pct"] >= ordered["_thr"]]
        emit(f"\n  [ATR 상위 {int((1-pct)*100)}% — past-only 확장창 임계]")
        emit(f"    (b) 조건 통과 전량: {_fmt(hit, '')} | 발생세션 {hit['session_date'].nunique()}/{n_sessions} "
             f"일평균 {len(hit)/n_sessions:.2f}종")
        for p, g in hit.groupby("period", observed=True):
            if len(g) >= 15:
                emit(f"        [{p}] n={len(g)} net {g['label_contract'].mean():+.2f}% "
                     f"TP {100*_tp_rate(g['exit_kind']):.0f}%")
        top = (hit.sort_values(["session_date", "alpha_score"], ascending=[True, False])
               .groupby("session_date", sort=False).head(1))
        emit(f"    (c) 조건 중 모델1등: {_fmt(top, '')}")

    # ---------- W2: KR past-only 분위 + 절대 임계 ----------
    kr = _kr_table().sort_values("session_date").reset_index(drop=True)
    emit(f"\n## W2 KR 분위 조건의 절대값·past-only 재검증 (n={len(kr)})")
    emit(f"  전 기간 분위(1차 검증에 쓴 값): 할인 Q1 = {kr['ma20_disc'].quantile(0.25):.2f}%, "
         f"갭 Q1 = {kr['gap'].quantile(0.25):.2f}%, rv20 중앙 = {kr['rv20'].quantile(0.50):.2f}")
    emit("  → 이 값들은 전 기간을 본 것이므로 lookahead. past-only 확장창으로 다시 판정한다.")
    thr_d, thr_g, prev_d, prev_g = [], [], [], []
    for _, row in kr.iterrows():
        thr_d.append(np.quantile(prev_d, 0.25) if len(prev_d) >= 300 else -np.inf)
        thr_g.append(np.quantile(prev_g, 0.25) if len(prev_g) >= 300 else -np.inf)
        prev_d.append(float(row["ma20_disc"]))
        prev_g.append(float(row["gap"]))
    kr["_thr_d"], kr["_thr_g"] = thr_d, thr_g
    past_hit = kr[(kr["ma20_disc"] <= kr["_thr_d"]) & (kr["gap"] <= kr["_thr_g"])]
    emit(f"  past-only 할인Q1 AND 갭Q1: {_fmt(past_hit, '')}")
    for p, g in past_hit.groupby("period", observed=True):
        if len(g) >= 20:
            emit(f"      [{p}] n={len(g)} net {g['label_contract'].mean():+.2f}% "
                 f"TP {100*_tp_rate(g['exit_kind']):.0f}%")
    # 절대 임계 고정안 (past-only 임계의 안정 구간을 대표값으로)
    stable = kr[kr["_thr_d"] > -np.inf]
    if len(stable):
        emit(f"  past-only 임계 분포: 할인 중앙 {np.median(stable['_thr_d']):.1f}% "
             f"(범위 {stable['_thr_d'].min():.1f}~{stable['_thr_d'].max():.1f}), "
             f"갭 중앙 {np.median(stable['_thr_g']):.1f}% "
             f"(범위 {stable['_thr_g'].min():.1f}~{stable['_thr_g'].max():.1f})")
    for d_thr, g_thr in ((-15.0, -2.0), (-15.0, -4.0), (-20.0, -2.0), (-20.0, -4.0), (-25.0, -4.0)):
        sub = kr[(kr["ma20_disc"] <= d_thr) & (kr["gap"] <= g_thr)]
        halves = " | ".join(f"{p} {g['label_contract'].mean():+.2f}"
                            for p, g in sub.groupby("period", observed=True) if len(g) >= 20)
        emit(f"  절대 고정 할인<={d_thr} & 갭<={g_thr}: {_fmt(sub, '')}" + (f"  [{halves}]" if halves else ""))

    # ---------- W3: 이상치 감사 ----------
    emit("\n## W3 이상치 감사 (KR 갭 하위25% 최악 −100%)")
    gap_q1 = kr["gap"].quantile(0.25)
    worst = kr[kr["gap"] <= gap_q1].nsmallest(5, "label_contract")
    for r in worst.itertuples(index=False):
        emit(f"  {r.session_date} {r.ticker} net {r.label_contract:+.1f}% "
             f"(낙폭 {r.chg:+.1f} 갭 {r.gap:+.1f} 가격 {r.price:,.0f} exit={r.exit_kind})")
    emit("  → net −90% 이하가 있으면 가격 데이터 오염(액면분할·병합 미조정) 의심 — 조건 채택 전 제거 필요")

    out = ROOT / "docs" / "reports" / f"tp_capture_verify2_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
