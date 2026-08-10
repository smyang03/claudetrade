"""T1 TP 적중 구조 탐색 — "FRMI형"을 집중 포획하는 구조 조건 찾기 (read-only).

2026-08-11 운영자 지시. 문제의식: 계약(TP+12%)이 수확 구조인데 오프라인에서 TP 적중은
전체의 ~12%뿐이고 나머지는 D5 시간청산이다. 라이브 큰 승자(FRMI +12.3%, AXTI +20.9%)는
둘 다 소형·고변동이었다. **어떤 구조 조건이 TP 적중률을 올리는가**를 양 시장에서
독립 탐색한다(예측 아님 — 조건 필터, 구조 5전5승 카테고리).

규율:
- 라벨은 계약(TP12/SL25/D5). 지표는 **TP 적중률**과 **후보당 계약 net** 두 축.
- 단변량 4분위 스캔 → 단조성 있는 축만 2변수 교차(다중검정 최소화, 등록부 52셀 기재).
- **기간 분리 재현 필수**(US 전반/후반, KR 2025/2026). 한 기간만 좋으면 기각.
- KR 임계를 US로, US 임계를 KR로 옮기지 않는다(구조 정반대 반복 실측).

사용: python tools/tp_capture_structure_experiment.py
출력: stdout + docs/reports/tp_capture_structure_<date>.md
"""

from __future__ import annotations

import json
import statistics as st
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.kr_fallen_ranking_experiment import CACHES  # noqa: E402
from tools.us_swing_contract_label_experiment import _load_bars  # noqa: E402
from tools.us_swing_model_matrix_experiment import _build_frame  # noqa: E402

PROXY_CHG_LE = -5.0
COST_KR = 0.25
US_FEATURES = [
    ("realized_vol_20d_pct", "실현변동성20d"),
    ("atr_pct", "ATR%"),
    ("change_pct", "당일낙폭"),
    ("ma20_distance_pct", "MA20할인"),
    ("gap_pct", "갭"),
    ("from_high_20d_pct", "20일고점대비"),
    ("entry_px", "진입가($)"),
]
KR_FEATURES = [
    ("rv20", "실현변동성20d"),
    ("chg", "당일낙폭"),
    ("ma20_disc", "MA20할인"),
    ("gap", "갭"),
    ("from_high20", "20일고점대비"),
    ("price", "가격(원)"),
]


def _tp_rate(kinds: pd.Series) -> float:
    return float(kinds.astype(str).str.startswith("tp").mean())


def _bucket_report(frame: pd.DataFrame, col: str, label: str, split_col: str,
                   emit) -> dict[str, list[float]]:
    """4분위 버킷별 TP율·평균 net·표본, 기간 분리 병기. 단조성 판정용 값 반환."""
    work = frame.dropna(subset=[col, "label_contract", "exit_kind"]).copy()
    if len(work) < 80:
        emit(f"  {label:14s}: 표본 부족 n={len(work)}")
        return {}
    try:
        work["q"] = pd.qcut(work[col], 4, labels=["Q1(낮음)", "Q2", "Q3", "Q4(높음)"], duplicates="drop")
    except ValueError:
        emit(f"  {label:14s}: 분위 생성 실패(값 편중)")
        return {}
    lines, tp_by_q = [], []
    for q, g in work.groupby("q", observed=True):
        tp = 100 * _tp_rate(g["exit_kind"])
        net = float(g["label_contract"].mean())
        parts = []
        for period, gp in g.groupby(split_col, observed=True):
            if len(gp) >= 20:
                parts.append(f"{period} TP{100 * _tp_rate(gp['exit_kind']):.0f}%/{gp['label_contract'].mean():+.1f}")
        lines.append(f"{q} n={len(g)} TP {tp:4.1f}% net {net:+5.2f}%"
                     + (f" [{' | '.join(parts)}]" if parts else ""))
        tp_by_q.append(tp)
    emit(f"  {label}")
    for line in lines:
        emit(f"    {line}")
    if len(tp_by_q) >= 3:
        mono_up = all(b >= a - 1.0 for a, b in zip(tp_by_q, tp_by_q[1:]))
        mono_dn = all(b <= a + 1.0 for a, b in zip(tp_by_q, tp_by_q[1:]))
        spread = max(tp_by_q) - min(tp_by_q)
        if (mono_up or mono_dn) and spread >= 5:
            emit(f"    → 단조성 {'상승' if mono_up else '하락'} (폭 {spread:.1f}%p) — 교차 후보")
            return {"col": col, "label": label, "direction": "up" if mono_up else "down"}
    return {}


def _us_table() -> pd.DataFrame:
    frame = _build_frame()
    frame = frame[frame["change_pct"].le(PROXY_CHG_LE)].copy()
    cache: dict[str, pd.DataFrame | None] = {}
    prices = []
    for row in frame.itertuples(index=False):
        t = str(row.ticker)
        if t not in cache:
            cache[t] = _load_bars(t)
        bars = cache[t]
        px = np.nan
        if bars is not None:
            idx = bars.index[bars["date"] == str(row.session_date)]
            if len(idx):
                sl = bars.iloc[int(idx[0]) + 1:int(idx[0]) + 2]
                if len(sl):
                    px = float(sl.iloc[0]["open"])
        prices.append(px)
    frame["entry_px"] = prices
    dates = sorted(frame["session_date"].unique())
    mid = dates[len(dates) // 2]
    frame["period"] = np.where(frame["session_date"] < mid, "전반", "후반")
    return frame


def _kr_table() -> pd.DataFrame:
    rows = []
    for year, path in CACHES.items():
        cache = json.loads(path.read_text(encoding="utf-8"))
        for code, bars in cache.items():
            if len(bars) < 27:
                continue
            for idx in range(22, len(bars)):
                b = bars[idx]
                prev = bars[idx - 1]["c"]
                if prev <= 0:
                    continue
                chg = 100 * (b["c"] / prev - 1)
                if not (-29.7 <= chg <= -5.27) or b["c"] < 7110:
                    continue
                w20 = bars[idx - 20:idx]
                if sum(x["amt"] for x in w20) / 20 < 1e9:
                    continue
                after = bars[idx + 1:idx + 6]
                if len(after) < 5:
                    continue
                e = after[0]["o"]
                if not e or e <= 0:
                    continue
                tp, sl = e * 1.12, e * 0.75
                net = kind = None
                for k, bar in enumerate(after):
                    if k > 0 and bar["o"] <= sl:
                        net, kind = 100 * (bar["o"] / e - 1) - COST_KR, "sl_gap"
                        break
                    if k > 0 and bar["o"] >= tp:
                        net, kind = 100 * (bar["o"] / e - 1) - COST_KR, "tp_gap"
                        break
                    if bar["l"] <= sl:
                        net, kind = 100 * (sl / e - 1) - COST_KR, "sl"
                        break
                    if bar["h"] >= tp:
                        net, kind = 100 * (tp / e - 1) - COST_KR, "tp"
                        break
                if net is None:
                    net, kind = 100 * (after[-1]["c"] / e - 1) - COST_KR, "d5"
                ma20 = sum(x["c"] for x in w20) / 20
                hi20 = max(x["h"] for x in w20)
                rets = [100 * (w20[m]["c"] / w20[m - 1]["c"] - 1) for m in range(1, 20)
                        if w20[m - 1]["c"] > 0]
                rows.append({
                    "session_date": b["d"], "ticker": code, "period": year,
                    "label_contract": net, "exit_kind": kind, "chg": chg,
                    "gap": 100 * (b["o"] / prev - 1),
                    "rv20": st.pstdev(rets) if len(rets) > 3 else 99.0,
                    "ma20_disc": 100 * (b["c"] / ma20 - 1) if ma20 > 0 else 0.0,
                    "from_high20": 100 * (b["c"] / hi20 - 1) if hi20 > 0 else 0.0,
                    "price": b["c"],
                })
    frame = pd.DataFrame(rows)
    # 2026 캐시가 2026-01 이후를 덮으므로 중복 세션×티커 제거
    return frame.drop_duplicates(subset=["session_date", "ticker"], keep="last")


def _cross_check(frame: pd.DataFrame, specs: list[dict], emit, market: str) -> None:
    """단조성 통과 축 2개를 교차 — 상위/하위 극단 조합만 본다(셀 최소화)."""
    if len(specs) < 2:
        emit("  단조 축 2개 미만 — 교차 생략")
        return
    a, b = specs[0], specs[1]
    fa, fb = a["col"], b["col"]
    work = frame.dropna(subset=[fa, fb, "label_contract"]).copy()
    qa = work[fa].quantile(0.75 if a["direction"] == "up" else 0.25)
    qb = work[fb].quantile(0.75 if b["direction"] == "up" else 0.25)
    mask = ((work[fa] >= qa) if a["direction"] == "up" else (work[fa] <= qa)) & \
           ((work[fb] >= qb) if b["direction"] == "up" else (work[fb] <= qb))
    sub, rest = work[mask], work[~mask]
    emit(f"  교차: {a['label']} {'상위' if a['direction']=='up' else '하위'}25% "
         f"AND {b['label']} {'상위' if b['direction']=='up' else '하위'}25%")
    for name, g in (("조건 통과", sub), ("나머지", rest)):
        if len(g):
            emit(f"    {name}: n={len(g)} TP {100*_tp_rate(g['exit_kind']):.1f}% "
                 f"net {g['label_contract'].mean():+.2f}% 승률 {100*(g['label_contract']>0).mean():.0f}%")
    for period, g in sub.groupby("period", observed=True):
        if len(g) >= 20:
            emit(f"    [{period}] n={len(g)} TP {100*_tp_rate(g['exit_kind']):.1f}% "
                 f"net {g['label_contract'].mean():+.2f}%")


def main() -> int:
    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    emit(f"# T1 TP 적중 구조 탐색 ({date.today().isoformat()}) — FRMI형 포획 조건")
    emit("\n라벨=계약(TP12/SL25/D5). 지표=TP 적중률 + 후보당 계약 net. 기간 분리 병기.")

    for market, frame, features in (
        ("US day_losers 프록시", _us_table(), US_FEATURES),
        ("KR 급락밴드", _kr_table(), KR_FEATURES),
    ):
        emit(f"\n## {market} — 전체 n={len(frame)}, "
             f"TP 적중 {100*_tp_rate(frame['exit_kind']):.1f}%, "
             f"후보당 net {frame['label_contract'].mean():+.2f}%")
        specs = []
        for col, label in features:
            if col not in frame.columns:
                continue
            spec = _bucket_report(frame, col, label, "period", emit)
            if spec:
                specs.append(spec)
        emit(f"\n  [교차 검증] {market}")
        _cross_check(frame, specs, emit, market)

    emit("\n## 판정 메모")
    emit("- 계측 전용. 조건 채택은 등록부 갱신 + 기간 재현 + forward 게이트 + 운영자.")
    emit("- 시장 간 임계 이식 금지(구조 정반대 반복 실측).")
    out = ROOT / "docs" / "reports" / f"tp_capture_structure_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
