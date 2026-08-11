"""U 시리즈 — 전체 결정면 점검에서 나온 미검증 축 1차 검증 (read-only).

2026-08-11 운영자 지시("안 본 축 리스트업하고 순차 검증"). 후보 생성·신호 조건·진입·
청산·사이징·비용 전 축을 점검해 아직 실측이 없는 구조 축만 골랐다(예측 계열 제외).

  U1 연속 급락: 전일도 하락이었나 / 며칠째 하락인가 — "떨어지는 칼" 판별 (미검증)
  U2 거래량 급증: 당일 거래량/20일 평균 — KR엔 조건이 있으나 US엔 없음 (US 미검증)
  U3 요일: 급락 요일별 성과 — 월요일 급락과 금요일 급락이 다른가 (미검증)
  U4 eligibility 경계: 가격·거래대금 하한 근처 코호트 — 임계 근거가 없다 (미검증)
  U5 갭 방향: 갭다운 후 하락 vs 갭업 후 하락(장중 붕괴) — 유형이 다른가 (미검증)

라벨=계약(TP12/SL25/D5), 기간 분리 병기. 시장 간 이식 금지(양 시장 독립 표시).
사용: python tools/unexamined_axes_experiment.py
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
from tools.us_swing_contract_label_experiment import _load_bars  # noqa: E402


def _stat(g: pd.DataFrame, label: str, split: str = "period") -> str:
    if len(g) < 15:
        return f"    {label:26s} n={len(g)} (표본 부족)"
    net = g["label_contract"]
    halves = " | ".join(f"{p} {gg['label_contract'].mean():+.2f}"
                        for p, gg in g.groupby(split, observed=True) if len(gg) >= 15)
    return (f"    {label:26s} n={len(g):5d} TP {100*_tp_rate(g['exit_kind']):4.1f}% "
            f"net {net.mean():+5.2f}% 승률 {100*(net>0).mean():3.0f}%  [{halves}]")


def _augment_us(us: pd.DataFrame) -> pd.DataFrame:
    """전일 등락·연속 하락일수·거래량비·요일·거래대금 부착 (OHLC 아카이브 기준)."""
    cache: dict[str, pd.DataFrame | None] = {}
    prior, streak, volr, dollar = [], [], [], []
    for row in us.itertuples(index=False):
        t = str(row.ticker)
        if t not in cache:
            cache[t] = _load_bars(t)
        b = cache[t]
        p = s = v = dv = np.nan
        if b is not None:
            idx = b.index[b["date"] == str(row.session_date)]
            if len(idx):
                i = int(idx[0])
                if i >= 21:
                    closes = b["close"].to_numpy(float)
                    p = 100 * (closes[i - 1] / closes[i - 2] - 1) if closes[i - 2] > 0 else np.nan
                    k = 0
                    while i - k - 1 >= 0 and closes[i - k] < closes[i - k - 1] and k < 10:
                        k += 1
                    s = k
                    vols = b["volume"].to_numpy(float)
                    m = vols[i - 20:i].mean()
                    v = vols[i] / m if m > 0 else np.nan
                    dv = closes[i] * vols[i]
        prior.append(p); streak.append(s); volr.append(v); dollar.append(dv)
    out = us.copy()
    out["prior_chg"], out["down_streak"] = prior, streak
    out["vol_ratio"], out["dollar_vol"] = volr, dollar
    out["dow"] = pd.to_datetime(out["session_date"]).dt.dayofweek
    return out


def _augment_kr(kr: pd.DataFrame) -> pd.DataFrame:
    kr = kr.copy()
    kr["dow"] = pd.to_datetime(kr["session_date"]).dt.dayofweek
    return kr


def main() -> int:
    lines: list[str] = []

    def emit(t: str = "") -> None:
        print(t)
        lines.append(t)

    emit(f"# U 시리즈 미검증 축 1차 검증 ({date.today().isoformat()})")
    emit("\n라벨=계약(TP12/SL25/D5). 기간 분리 병기. 양 시장 독립(이식 금지).")

    us = _augment_us(_us_table())
    emit(f"\n## US day_losers 프록시 (n={len(us)}) — 기준 net {us['label_contract'].mean():+.2f}%")

    emit("\n  [U1 연속 급락]")
    sub = us.dropna(subset=["prior_chg"])
    emit(_stat(sub[sub["prior_chg"] > 0], "전일 상승 후 급락(단발)"))
    emit(_stat(sub[(sub["prior_chg"] <= 0) & (sub["prior_chg"] > -3)], "전일 약하락 후 급락"))
    emit(_stat(sub[sub["prior_chg"] <= -3], "전일도 3%+ 하락(연속)"))
    for k in (1, 2, 3):
        emit(_stat(us[us["down_streak"] == k], f"연속 하락 {k}일째"))
    emit(_stat(us[us["down_streak"] >= 4], "연속 하락 4일+"))

    emit("\n  [U2 거래량 급증 — US 미검증 축]")
    v = us.dropna(subset=["vol_ratio"])
    try:
        v = v.assign(q=pd.qcut(v["vol_ratio"], 4, labels=["Q1(낮음)", "Q2", "Q3", "Q4(폭증)"]))
        for q, g in v.groupby("q", observed=True):
            emit(_stat(g, f"거래량비 {q}"))
    except ValueError:
        emit("    분위 생성 실패")

    emit("\n  [U3 요일]")
    for d, name in enumerate(["월", "화", "수", "목", "금"]):
        emit(_stat(us[us["dow"] == d], f"{name}요일 급락"))

    emit("\n  [U4 eligibility 경계 — 임계 근거 미검증]")
    d = us.dropna(subset=["dollar_vol"])
    emit(_stat(d[d["dollar_vol"] < 5e7], "거래대금 <$50M(하한 근처)"))
    emit(_stat(d[(d["dollar_vol"] >= 5e7) & (d["dollar_vol"] < 2e8)], "거래대금 $50~200M"))
    emit(_stat(d[d["dollar_vol"] >= 2e8], "거래대금 >=$200M"))

    emit("\n  [U5 갭 방향]")
    emit(_stat(us[us["gap_pct"] > 0], "갭업 후 장중 붕괴"))
    emit(_stat(us[(us["gap_pct"] <= 0) & (us["gap_pct"] > -3)], "소폭 갭다운"))
    emit(_stat(us[us["gap_pct"] <= -3], "큰 갭다운"))

    kr = _augment_kr(_kr_table())
    kr = kr[kr["label_contract"] > -90]
    emit(f"\n## KR 급락밴드 (n={len(kr)}) — 기준 net {kr['label_contract'].mean():+.2f}%")
    emit("\n  [U3 요일]")
    for d, name in enumerate(["월", "화", "수", "목", "금"]):
        emit(_stat(kr[kr["dow"] == d], f"{name}요일 급락"))
    emit("\n  [U5 갭 방향]")
    emit(_stat(kr[kr["gap"] > 0], "갭업 후 장중 붕괴"))
    emit(_stat(kr[(kr["gap"] <= 0) & (kr["gap"] > -3)], "소폭 갭다운"))
    emit(_stat(kr[kr["gap"] <= -3], "큰 갭다운"))

    emit("\n## 판정 메모")
    emit("- 계측 전용. 조건 채택은 등록부 갱신 + 기간 재현 + forward 게이트 + 운영자.")
    out = ROOT / "docs" / "reports" / f"unexamined_axes_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
