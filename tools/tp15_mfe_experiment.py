"""TP12 vs TP15 판단용 MFE 분포 실측 (2026-08-21, read-only).

질문: 밴드 표본에서 **D5 내 MFE가 [12%, 15%)에 드는 비중**이 얼마인가.
그 구간이 TP12와 TP15가 실제로 다르게 행동하는 유일한 슬라이스다.
  - 비중이 크면  → TP15로 올리면 그만큼을 더 먹는다(스윕이 보여준 +0.76%p의 실체)
  - 비중이 작으면 → TP15는 "거의 안 닿는 천장"이라 사실상 TP 폐지와 같아진다.
    그건 TP 상향이 아니라 다른 결정이므로 따로 논해야 한다.

사전등록(계산 전 고정):
  - MFE = t+1 시가 진입 후 D5 내 **고가** 기준 최대 상승률(%). 비용 미차감(도달 판정용).
  - 표본 = US day_losers 프록시 ∩ 거래대금 밴드 100~500M.
    **세션당 1건**(08-20 백테스트와 같은 정의 — 밴드 통과 세션 139건 ≈ 문서 n=137).
    세션 내 선택은 거래대금 밴드 중앙(300M)에 가장 가까운 종목으로 한다
    — 원 rank를 오프라인에서 복원할 수 없어 대리 기준을 쓰며, 이 사실을 결과에 명시한다.
  - 비교를 위해 "밴드 통과 신호 전체(213건)" 표본도 병기한다.
  - 셀 n<15 또는 종목수 k<10이면 판정에 쓰지 않는다.

사용: python tools/tp15_mfe_experiment.py
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

from tools.amihud_oos_experiment import _amihud_and_dollar  # noqa: E402
from tools.tp_capture_structure_experiment import _us_table  # noqa: E402
from tools.us_swing_contract_label_experiment import _load_bars  # noqa: E402

HOLD = 5
BAND_LO, BAND_HI = 100.0, 500.0
BAND_MID = 300.0


def _mfe_pct(ticker: str, session_date: str) -> float:
    """t+1 시가 진입 후 D5 내 고가 기준 최대 상승률(%). 실패 시 nan."""
    bars = _load_bars(ticker)
    if bars is None:
        return np.nan
    idx = bars.index[bars["date"] == str(session_date)]
    if not len(idx):
        return np.nan
    path = bars.iloc[int(idx[0]) + 1: int(idx[0]) + 1 + HOLD]
    if len(path) < HOLD:
        return np.nan
    entry = float(path.iloc[0]["open"])
    if entry <= 0:
        return np.nan
    high = float(path["high"].astype(float).max())
    return (high / entry - 1.0) * 100.0


def _report(frame: pd.DataFrame, label: str, emit) -> None:
    n = len(frame)
    if n < 15:
        emit(f"\n## {label} — n={n} (표본 부족)")
        return
    mfe = frame["mfe"]
    k = frame["ticker"].nunique()
    emit(f"\n## {label} — n={n}, 종목 {k}")
    bands = [
        ("MFE < 12%      (TP12·TP15 둘 다 미도달)", mfe < 12),
        ("12% <= MFE < 15% (**TP12만 도달** — 갈리는 구간)", (mfe >= 12) & (mfe < 15)),
        ("MFE >= 15%     (둘 다 도달)", mfe >= 15),
    ]
    for name, mask in bands:
        sub = frame[mask]
        emit(f"  {name:46s} {len(sub):4d}건 ({100*len(sub)/n:4.1f}%)")
    split = frame[(mfe >= 12) & (mfe < 15)]
    emit(f"  → TP12 도달률 {100*(mfe>=12).mean():.1f}%  /  TP15 도달률 {100*(mfe>=15).mean():.1f}%"
         f"  (차이 {100*((mfe>=12).mean()-(mfe>=15).mean()):.1f}%p)")
    if len(split) >= 5:
        emit(f"  갈리는 구간 {len(split)}건의 계약 net 평균: {split['label_contract'].mean():+.2f}% "
             f"(전체 {frame['label_contract'].mean():+.2f}%)")


def main() -> int:
    lines: list[str] = []

    def emit(t: str = "") -> None:
        print(t)
        lines.append(t)

    frame = _us_table()
    dvol, mfe = [], []
    for r in frame.itertuples(index=False):
        _, d = _amihud_and_dollar(str(r.ticker), str(r.session_date))
        dvol.append(d)
        mfe.append(_mfe_pct(str(r.ticker), str(r.session_date)))
    frame = frame.assign(dollar_m=dvol, mfe=mfe).dropna(subset=["mfe"])

    band = frame[frame["dollar_m"].between(BAND_LO, BAND_HI, inclusive="left")].copy()

    emit(f"# TP12 vs TP15 — MFE 분포 실측 ({date.today().isoformat()})")
    emit()
    emit(f"프록시 {len(frame)}건 / {frame['session_date'].nunique()}세션 "
         f"→ 밴드({BAND_LO:.0f}~{BAND_HI:.0f}M) {len(band)}건 / {band['session_date'].nunique()}세션")
    emit()
    emit("⚠️ 세션당 1건 선택 시 원 rank를 오프라인에서 복원할 수 없어 **거래대금 밴드 중앙"
         "(300M) 근접**을 대리 기준으로 쓴다. 08-20 백테스트의 rank 기준과 다를 수 있다.")

    _report(band, "밴드 통과 신호 전체", emit)

    band["dist_mid"] = (band["dollar_m"] - BAND_MID).abs()
    per_session = band.sort_values("dist_mid").groupby("session_date", as_index=False).first()
    _report(per_session, "세션당 1건 (08-20 백테스트와 같은 표본 크기)", emit)

    emit()
    emit("## 읽는 법")
    emit("  '갈리는 구간' 비중이 TP12→TP15 상향으로 **잃는** 몫이다(TP12는 먹고 TP15는 놓친다).")
    emit("  동시에 그 건들이 D5까지 더 오르면 TP15가 더 먹는다 — 스윕의 +0.76%p는 두 효과의 합이다.")
    emit("  비중이 한 자릿수면 TP15는 거의 안 닿는 천장이라 'TP 상향'이 아니라 'TP 사실상 폐지'다.")

    out = ROOT / "docs" / "reports" / "tp15_mfe_result_20260821.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n[saved] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
