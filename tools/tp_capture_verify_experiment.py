"""T1-V 검증 — TP 적중 구조 후보의 임계 스윕·현행 대비·용량 (read-only).

2026-08-11. T1 단변량에서 나온 축을 실제 운용 형태로 검증한다.
  V1 US 변동성 하한 스윕: rv20/ATR 백분위 임계별 net·TP율·**일평균 후보 수**(용량)
  V2 US 현행 대비: 같은 세션에서 (a) 모델 rank1 (b) 조건 통과 전량 (c) 조건∩rank1
  V3 KR net 기준 재교차: TP율이 아니라 **net 최대** 조합 — R2/R4와 같은가 다른가
     (T1의 자동 교차는 TP율 단조성으로 골라 net을 놓쳤다 — 그 교정)

사용: python tools/tp_capture_verify_experiment.py
출력: stdout + docs/reports/tp_capture_verify_<date>.md
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


def _fmt(g: pd.DataFrame, label: str, sessions: int | None = None) -> str:
    if not len(g):
        return f"{label}: n=0"
    net = g["label_contract"]
    per_day = f" 일평균 {len(g)/sessions:.2f}종" if sessions else ""
    return (f"{label}: n={len(g)} TP {100*_tp_rate(g['exit_kind']):.1f}% "
            f"net {net.mean():+.2f}% 승률 {100*(net>0).mean():.0f}% "
            f"최악 {net.min():+.0f}%{per_day}")


def main() -> int:
    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    emit(f"# T1-V TP 적중 구조 검증 ({date.today().isoformat()})")

    # ---------------- V1: US 변동성 임계 스윕 ----------------
    us = _us_table()
    sessions_us = us["session_date"].nunique()
    emit(f"\n## V1 US 변동성 하한 스윕 (day_losers 프록시 {len(us)}행 / {sessions_us}세션)")
    emit(f"  기준선 전체: {_fmt(us, '전체', sessions_us)}")
    for col, name in (("realized_vol_20d_pct", "rv20"), ("atr_pct", "ATR%")):
        emit(f"\n  [{name} 하한]")
        for pct in (50, 60, 70, 75, 80, 90):
            thr = us[col].quantile(pct / 100)
            sub = us[us[col] >= thr]
            per = _fmt(sub, f"상위 {100-pct}% (>= {thr:.2f})", sessions_us)
            halves = " | ".join(
                f"{p} net {g['label_contract'].mean():+.2f}/TP {100*_tp_rate(g['exit_kind']):.0f}%"
                for p, g in sub.groupby("period", observed=True) if len(g) >= 25)
            emit(f"    {per}" + (f"  [{halves}]" if halves else ""))

    # ---------------- V2: 현행(모델 rank1) 대비 ----------------
    emit("\n## V2 US 현행 대비 (같은 세션, 계약 수익 기준)")
    frame = _build_frame()
    scored = _walk_conditional(frame, SEEDS, split_regime=False)
    scored = _alpha(scored[scored["change_pct"].le(PROXY_CHG_LE)], "combo")
    _dates = sorted(scored["session_date"].unique())
    _mid = _dates[len(_dates) // 2]
    scored["period"] = np.where(scored["session_date"] < _mid, "전반", "후반")
    # T1 조건: rv20·ATR 동시 상위 25% (교차 검증 통과분)
    rv_thr = scored["realized_vol_20d_pct"].quantile(0.75)
    atr_thr = scored["atr_pct"].quantile(0.75)
    scored["_hit"] = (scored["realized_vol_20d_pct"] >= rv_thr) & (scored["atr_pct"] >= atr_thr)
    rank1 = (scored.sort_values(["session_date", "alpha_score", "predicted_net_pct"],
                                ascending=[True, False, False])
             .groupby("session_date", sort=False).head(1))
    hits = scored[scored["_hit"]]
    hit_sessions = hits["session_date"].nunique()
    both = rank1[rank1["_hit"]]
    emit(f"  (a) 모델 rank1 전량        : {_fmt(rank1, '', scored['session_date'].nunique())}")
    emit(f"  (b) 조건 통과 전량         : {_fmt(hits, '', scored['session_date'].nunique())}"
         f"  — 조건 발생 세션 {hit_sessions}/{scored['session_date'].nunique()}")
    emit(f"  (c) 조건∩rank1            : {_fmt(both, '')}")
    # 조건 세션에서 조건 통과분 중 모델 1등을 산다면
    top_in_hit = (hits.sort_values(["session_date", "alpha_score"], ascending=[True, False])
                  .groupby("session_date", sort=False).head(1))
    emit(f"  (d) 조건 통과분 중 모델1등 : {_fmt(top_in_hit, '')}")
    for period, g in hits.groupby("period", observed=True):
        if len(g) >= 25:
            emit(f"      [{period}] 조건 통과 n={len(g)} net {g['label_contract'].mean():+.2f}% "
                 f"TP {100*_tp_rate(g['exit_kind']):.1f}%")

    # ---------------- V3: KR net 기준 재교차 ----------------
    kr = _kr_table()
    emit(f"\n## V3 KR net 기준 재교차 (급락밴드 {len(kr)}행)")
    emit("  T1 자동 교차는 TP율 단조성으로 골라 net −1.36%였다 — net 기준으로 다시 본다.")
    disc_q1 = kr["ma20_disc"].quantile(0.25)
    gap_q1 = kr["gap"].quantile(0.25)
    hi_q1 = kr["from_high20"].quantile(0.25)
    rv_lo = kr["rv20"].quantile(0.50)
    combos = {
        "할인 하위25%(깊은할인)": kr["ma20_disc"] <= disc_q1,
        "갭 하위25%(깊은갭하락)": kr["gap"] <= gap_q1,
        "할인Q1 AND 갭Q1": (kr["ma20_disc"] <= disc_q1) & (kr["gap"] <= gap_q1),
        "할인Q1 AND rv20 하위50%": (kr["ma20_disc"] <= disc_q1) & (kr["rv20"] <= rv_lo),
        "할인Q1 AND 갭Q1 AND rv하위50%": (kr["ma20_disc"] <= disc_q1) & (kr["gap"] <= gap_q1) & (kr["rv20"] <= rv_lo),
        "고점대비 하위25%": kr["from_high20"] <= hi_q1,
    }
    for label, mask in combos.items():
        sub = kr[mask]
        halves = " | ".join(
            f"{p} net {g['label_contract'].mean():+.2f}/TP {100*_tp_rate(g['exit_kind']):.0f}%"
            for p, g in sub.groupby("period", observed=True) if len(g) >= 20)
        emit(f"  {_fmt(sub, label)}" + (f"  [{halves}]" if halves else ""))

    emit("\n## 판정 메모")
    emit("- 계측 전용. 조건 채택은 등록부 갱신 + 기간 재현 + forward 게이트 + 운영자 승인.")
    out = ROOT / "docs" / "reports" / f"tp_capture_verify_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
