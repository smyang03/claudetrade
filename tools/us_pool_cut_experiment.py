"""US 수집기 컷(top_k)이 밴드 후보를 얼마나 가리는가 — 실익 실측 (2026-08-21, read-only).

배경: 메모리에 "수집기 컷 10이 풀 크기 신호의 천장, 관측만 넓히는 변경 1순위"로
적혀 있으나 **얼마나 자르는지 실측이 없었다**.

`candidate_pool_all`은 컷 밖까지 기록하고 `in_pool`로 컷 통과 여부를 표시한다.
그래서 "컷을 넓히면 무엇이 더 보이는가"를 라이브 위험 0으로 답할 수 있다.

⚠️ 주의: rank는 모델 점수 순인데 이 저장소에는 **모델 점수 역선별 기록이 4차례** 있다.
따라서 "가린다"가 곧 "손해"는 아니다 — 컷 밖 성과를 함께 봐야 판단이 선다.

라벨은 `data/price/us`(1,633종목) 기준. `_load_bars`가 보는 `us_yahoo_2y`는 170종목뿐이라
컷 밖 후보 대부분이 없다. **BOM 필수(utf-8-sig)** — 08-21 MAX 하한 사고와 같은 함정.

사용: python tools/us_pool_cut_experiment.py
"""

from __future__ import annotations

import sqlite3
import statistics
import sys
from contextlib import closing
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.us_swing_order_bridge import _max_daily_return_21d  # noqa: E402

DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
PRICE = ROOT / "data" / "price" / "us"
TP, SL, HOLD, COST = 0.12, 0.25, 5, 0.50
BAND_LO, BAND_HI = 100_000_000, 500_000_000

_CACHE: dict[str, pd.DataFrame | None] = {}


def _bars(ticker: str) -> pd.DataFrame | None:
    key = ticker.upper()
    if key in _CACHE:
        return _CACHE[key]
    path = PRICE / f"us_{key}.csv"
    frame = None
    if path.exists():
        try:
            # utf-8-sig 필수 — 이 CSV는 전부 BOM을 달고 있다(08-21 MAX 하한 사고).
            frame = pd.read_csv(path, encoding="utf-8-sig")
            frame["date"] = frame["date"].astype(str)
        except Exception:
            frame = None
    _CACHE[key] = frame
    return frame


def _contract_net(ticker: str, session_date: str) -> float | None:
    """t+1 시가 진입 계약 net(%). 동일일 TP·SL 동시 터치면 SL 우선(보수)."""
    bars = _bars(ticker)
    if bars is None:
        return None
    idx = bars.index[bars["date"] == str(session_date)]
    if not len(idx):
        return None
    path = bars.iloc[int(idx[0]) + 1: int(idx[0]) + 1 + HOLD]
    if len(path) < HOLD:
        return None
    try:
        entry = float(path.iloc[0]["open"])
    except (TypeError, ValueError):
        return None
    if entry <= 0:
        return None
    tp_px, sl_px = entry * (1 + TP), entry * (1 - SL)
    for _, row in path.iterrows():
        low, high = float(row["low"]), float(row["high"])
        if low <= sl_px:
            return -SL * 100 - COST
        if high >= tp_px:
            return TP * 100 - COST
    return (float(path.iloc[-1]["close"]) / entry - 1) * 100 - COST


def _stat(label: str, vals: list[float], tickers: list[str]) -> str:
    if not vals:
        return f"  {label:26s} (표본 없음)"
    k = len(set(tickers))
    return (f"  {label:26s} n={len(vals):3d}  종목 {k:3d}  평균 {statistics.mean(vals):+6.2f}%  "
            f"승률 {100*sum(1 for x in vals if x > 0)/len(vals):3.0f}%")


def main() -> int:
    lines: list[str] = []

    def emit(t: str = "") -> None:
        print(t)
        lines.append(t)

    with closing(sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=8)) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT session_date, ticker, rank, in_pool, dollar_vol "
            "FROM candidate_pool_all WHERE dollar_vol>=? AND dollar_vol<?",
            (BAND_LO, BAND_HI),
        )]

    emit(f"# US 수집기 컷 실익 실측 ({date.today().isoformat()})")
    emit()
    emit(f"밴드(100~500M) 통과 후보 {len(rows)}건 / "
         f"{len({r['session_date'] for r in rows})}세션 (라이브 러너 기록분)")

    buckets: dict[str, list] = {"in": [], "out": [], "in_max8": [], "out_max8": []}
    skipped = 0
    for r in rows:
        net = _contract_net(str(r["ticker"]), str(r["session_date"]))
        if net is None:
            skipped += 1
            continue
        key = "in" if int(r["in_pool"] or 0) == 1 else "out"
        buckets[key].append((net, str(r["ticker"])))
        mx = _max_daily_return_21d(str(r["ticker"]), str(r["session_date"]))
        if mx is not None and mx >= 8.0:
            buckets[key + "_max8"].append((net, str(r["ticker"])))

    emit(f"라벨 계산 가능 {len(rows)-skipped}건 / 결손 {skipped}건 (가격 CSV 부재·기간 부족)")
    emit()
    emit("## 계약 net (TP12/SL25/D5, 비용 0.5%)")
    for key, label in (("in", "컷 안 (in_pool=1)"), ("out", "컷 밖 (in_pool=0)")):
        v = buckets[key]
        emit(_stat(label, [x[0] for x in v], [x[1] for x in v]))
    emit()
    emit("## 밴드 + MAX>=8 (현행 라이브 계약)")
    for key, label in (("in_max8", "컷 안"), ("out_max8", "컷 밖")):
        v = buckets[key]
        emit(_stat(label, [x[0] for x in v], [x[1] for x in v]))

    emit()
    emit("## 읽는 법")
    emit("  컷 밖이 컷 안보다 **나쁘지 않다면** 컷 10은 알파를 거르는 게 아니라 그냥 표본을 줄인다")
    emit("  → 컷 확대는 '더 좋은 걸 찾는' 변경이 아니라 **표본 축적 속도를 올리는** 변경이다.")
    emit("  컷 밖이 뚜렷이 나쁘면 모델 rank가 실제로 변별하고 있다는 뜻이라 확대는 위험하다.")
    emit()
    emit("⚠️ 세션 7개뿐(라이브 러너가 08-12부터 기록). 방향 참고용이며 판정 근거가 아니다.")

    out = ROOT / "docs" / "reports" / "us_pool_cut_result_20260821.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n[saved] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
