"""Amihud 비유동성 축 OOS 검증 (2026-08-21, read-only).

08-20 리서치에서 Amihud는 밴드 위 in-sample 발견이었다(밴드+Amihud상위 +5.85%,
클러스터t 4.18). OOS 미확인이라 라이브 적용을 보류했고, 밴드가 27세션 발견 →
228세션 비겹침 확인으로 승격한 그 방식으로 검증한다.

사전등록: docs/reports/prereg_amihud_oos_20260821.md (계산 전 작성, 경계 고정)
사용: python tools/amihud_oos_experiment.py
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

from tools.tp_capture_structure_experiment import _us_table  # noqa: E402
from tools.us_swing_contract_label_experiment import _load_bars  # noqa: E402

BAND_LO_M, BAND_HI_M = 100.0, 500.0
LOOKBACK = 21
MIN_BARS = 12


def _amihud_and_dollar(ticker: str, session_date: str) -> tuple[float, float]:
    """(ILLIQ, 신호일 거래대금 백만$). no-lookahead: session_date 미만 바만 쓴다.

    ILLIQ = mean(|일간수익률%| / 일간거래대금(백만$)) over 직전 21거래일.
    """
    bars = _load_bars(ticker)
    if bars is None or "volume" not in bars.columns:
        return np.nan, np.nan
    same = bars.index[bars["date"] == str(session_date)]
    if not len(same):
        return np.nan, np.nan
    row = bars.iloc[int(same[0])]
    signal_dollar_m = float(row["close"]) * float(row["volume"]) / 1e6

    past = bars[bars["date"] < str(session_date)].tail(LOOKBACK)
    if len(past) < MIN_BARS:
        return np.nan, signal_dollar_m
    close = past["close"].astype(float).to_numpy()
    vol = past["volume"].astype(float).to_numpy()
    dollar_m = close * vol / 1e6
    ret_pct = np.abs(np.diff(close) / close[:-1]) * 100.0
    denom = dollar_m[1:]
    ok = (denom > 0) & np.isfinite(ret_pct)
    if ok.sum() < MIN_BARS - 1:
        return np.nan, signal_dollar_m
    return float(np.mean(ret_pct[ok] / denom[ok])), signal_dollar_m


def _cluster_t(g: pd.DataFrame) -> tuple[float, int]:
    """종목 단위 클러스터 t. 건수 t는 반복 종목 때문에 부풀려진다(밴드 4.80 vs 2.63)."""
    per = g.groupby("ticker")["label_contract"].mean()
    if len(per) < 2:
        return float("nan"), len(per)
    sd = per.std(ddof=1)
    if not np.isfinite(sd) or sd == 0:
        return float("nan"), len(per)
    return float(per.mean() / (sd / np.sqrt(len(per)))), len(per)


def _naive_t(g: pd.DataFrame) -> float:
    net = g["label_contract"]
    sd = net.std(ddof=1)
    if len(net) < 2 or not np.isfinite(sd) or sd == 0:
        return float("nan")
    return float(net.mean() / (sd / np.sqrt(len(net))))


def _row(label: str, g: pd.DataFrame) -> str:
    if len(g) < 15:
        return f"  {label:22s} n={len(g):4d}  (표본 부족 — 판정 제외)"
    ct, k = _cluster_t(g)
    net = g["label_contract"]
    return (f"  {label:22s} n={len(g):4d}  net {net.mean():+6.2f}%  "
            f"승률 {100 * (net > 0).mean():3.0f}%  클러스터t {ct:+5.2f}(k={k})  "
            f"건수t {_naive_t(g):+5.2f}")


def main() -> int:
    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    frame = _us_table()
    illiq, dollar = [], []
    for r in frame.itertuples(index=False):
        a, d = _amihud_and_dollar(str(r.ticker), str(r.session_date))
        illiq.append(a)
        dollar.append(d)
    frame = frame.assign(amihud=illiq, dollar_m=dollar)

    band = frame[
        frame["dollar_m"].between(BAND_LO_M, BAND_HI_M, inclusive="left")
    ].dropna(subset=["amihud"]).copy()

    emit(f"# Amihud OOS 검증 ({date.today().isoformat()})")
    emit()
    emit(f"사전등록: docs/reports/prereg_amihud_oos_20260821.md")
    emit(f"전체 프록시 {len(frame)}건 → 밴드({BAND_LO_M:.0f}~{BAND_HI_M:.0f}M) "
         f"{len(frame[frame['dollar_m'].between(BAND_LO_M, BAND_HI_M, inclusive='left')])}건 "
         f"→ Amihud 계산 가능 {len(band)}건")
    if len(band) < 40:
        emit("\n표본이 40건 미만이다. 판정하지 않는다.")
        _write(lines)
        return 1

    # 3분위 경계는 전체 표본에서 한 번만 끊는다(사전등록). 구간별 재분할 금지.
    try:
        band["tercile"] = pd.qcut(band["amihud"], 3, labels=["하위(유동)", "중위", "상위(비유동)"])
    except ValueError:
        emit("\nAmihud 분포가 3분위로 안 쪼개진다. 판정 불가.")
        _write(lines)
        return 1

    # session_date는 문자열이라 median()이 안 된다. 정렬 후 중앙 원소를 쓴다
    # (사전등록의 "표본 session_date 중앙값"과 같은 의미 — 건수 기준 중앙).
    cut = str(band["session_date"].sort_values().iloc[len(band) // 2])
    band["half"] = np.where(band["session_date"].astype(str) < cut, "전반", "후반")

    emit(f"\n기간 2분할 경계(중앙값): {cut}")
    emit(f"  전반 {int((band['half'] == '전반').sum())}건 "
         f"({band[band['half'] == '전반']['session_date'].min()} ~ "
         f"{band[band['half'] == '전반']['session_date'].max()})")
    emit(f"  후반 {int((band['half'] == '후반').sum())}건 "
         f"({band[band['half'] == '후반']['session_date'].min()} ~ "
         f"{band[band['half'] == '후반']['session_date'].max()})")

    emit(f"\n## 전체 (in-sample 재현 확인) — 밴드 기준선 {band['label_contract'].mean():+.2f}%")
    for name, g in band.groupby("tercile", observed=True):
        emit(_row(str(name), g))

    verdict_parts = []
    for half in ("전반", "후반"):
        sub = band[band["half"] == half]
        emit(f"\n## {half} (n={len(sub)}) — 기준선 {sub['label_contract'].mean():+.2f}%")
        cells = {}
        for name, g in sub.groupby("tercile", observed=True):
            emit(_row(str(name), g))
            cells[str(name)] = g
        lo, hi = cells.get("하위(유동)"), cells.get("상위(비유동)")
        if lo is None or hi is None or len(lo) < 15 or len(hi) < 15:
            verdict_parts.append((half, None, None))
            continue
        gap = hi["label_contract"].mean() - lo["label_contract"].mean()
        ct, _ = _cluster_t(hi)
        emit(f"  → 상위−하위 {gap:+.2f}%p, 상위 클러스터t {ct:+.2f}")
        verdict_parts.append((half, gap, ct))

    emit("\n## 판정 (사전등록 기준)")
    usable = [(h, g, t) for h, g, t in verdict_parts if g is not None]
    if len(usable) < 2:
        emit("  **판정 불가** — 한쪽 구간의 셀 표본이 15건 미만이다.")
    elif any(g <= 0 for _, g, _ in usable):
        flipped = [h for h, g, _ in usable if g <= 0]
        emit(f"  **기각** — {', '.join(flipped)} 구간에서 방향이 뒤집혔다. 재론 금지 목록으로.")
    else:
        late_t = [t for h, _, t in usable if h == "후반"]
        strong = bool(late_t) and np.isfinite(late_t[0]) and late_t[0] >= 2.0
        if strong:
            emit("  **통과** — 양 구간 동일 방향 + 후반 클러스터t ≥ 2.0.")
        else:
            emit("  **약한 통과** — 양 구간 방향은 같으나 후반 클러스터t < 2.0.")
        emit("  단 사전등록대로 즉시 라이브 적용은 하지 않는다. 밴드+MAX 위에 세 번째")
        emit("  필터를 얹으면 진입 빈도가 18~24%로 떨어져 판정 도달이 더 느려진다.")
        emit("  → 30건 판정 이후 다음 계약의 후보로 등록한다.")

    _write(lines)
    return 0


def _write(lines: list[str]) -> None:
    out = ROOT / "docs" / "reports" / "amihud_oos_result_20260821.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n[saved] {out}")


if __name__ == "__main__":
    raise SystemExit(main())
