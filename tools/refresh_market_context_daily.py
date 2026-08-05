"""시장 컨텍스트 일일 갱신기 — breadth/VIX/등락비율 5개 파일의 상시 생산자.

2026-08-05 조사 결과: 아래 파일들은 07-10경 일회성 스크립트로 생성된 뒤
갱신 주체가 저장소에 없었다("끊긴" 게 아니라 애초에 파이프라인이 아니었다).
integrity_check의 신선도 게이트가 25일 정체로 탐지했고, 이 도구가 생산자가 된다.

  data/analysis/us_breadth_proxy_daily.csv   RSP/SPY (+^NYA,^MID)      yfinance
  data/analysis/kr_breadth_proxy_daily.csv   KODEX200/KOSPI/KOSDAQ     캐시+yfinance
  data/analysis/vix_term_daily.csv           ^VIX/^VIX3M/^VVIX         yfinance
  data/analysis/us_adv_dec_breadth_daily.csv data/price/us/*.csv 집계   로컬
  data/analysis/kr_adv_dec_breadth_daily.csv kr_fallen_price_cache 집계 로컬

원칙: 없는 날짜만 append(멱등). 기존 행은 고치지 않는다. 소스별 실패는
해당 파일만 건너뛰고 나머지는 계속한다(부분 실패가 전체를 막지 않는다).

사용:
  python tools/refresh_market_context_daily.py --once        # 1회 갱신
  python tools/refresh_market_context_daily.py --loop --interval-sec 21600
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # 단독 실행 시 tools.* import 보장
    sys.path.insert(0, str(ROOT))
ANALYSIS = ROOT / "data" / "analysis"


def _existing(path: Path, columns: list[str]) -> pd.DataFrame:
    if path.exists():
        frame = pd.read_csv(path)
        frame["date"] = frame["date"].astype(str)
        return frame
    return pd.DataFrame(columns=columns)


def _append_rows(path: Path, existing: pd.DataFrame, rows: list[dict], columns: list[str]) -> int:
    if not rows:
        return 0
    merged = pd.concat([existing, pd.DataFrame(rows, columns=columns)], ignore_index=True)
    merged = merged.drop_duplicates(subset=["date"], keep="first").sort_values("date")
    merged.to_csv(path, index=False)
    return len(rows)


def _yf_close_series(symbol: str, start: str, end_exclusive: str) -> pd.Series | None:
    import yfinance as yf

    try:
        frame = yf.download(symbol, start=start, end=end_exclusive, progress=False,
                            auto_adjust=False, threads=False)
    except Exception as exc:
        print(f"[warn] {symbol} download failed: {exc}", file=sys.stderr)
        return None
    if frame is None or frame.empty or "Close" not in frame:
        return None
    close = frame["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    close.index = pd.to_datetime(close.index).strftime("%Y-%m-%d")
    return close.astype(float)


def _fetch_window(existing: pd.DataFrame) -> tuple[str, str] | None:
    last = max(existing["date"]) if len(existing) else "2015-01-01"
    until = (date.today() - timedelta(days=1)).isoformat()
    start = (date.fromisoformat(last) + timedelta(days=1)).isoformat()
    if start > until:
        return None
    return start, (date.fromisoformat(until) + timedelta(days=1)).isoformat()


def refresh_us_breadth() -> dict:
    columns = ["date", "RSP", "SPY", "NYSE_comp", "SP400_mid", "RSP_SPY_ratio"]
    path = ANALYSIS / "us_breadth_proxy_daily.csv"
    existing = _existing(path, columns)
    window = _fetch_window(existing)
    if window is None:
        return {"file": path.name, "status": "UP_TO_DATE"}
    start, end = window
    series = {name: _yf_close_series(sym, start, end)
              for name, sym in (("RSP", "RSP"), ("SPY", "SPY"), ("NYSE_comp", "^NYA"), ("SP400_mid", "^MID"))}
    if series["RSP"] is None or series["SPY"] is None:
        return {"file": path.name, "status": "FAILED", "reason": "RSP/SPY missing"}
    have = set(existing["date"])
    rows = []
    for day in sorted(set(series["RSP"].index) & set(series["SPY"].index) - have):
        rsp, spy = float(series["RSP"].loc[day]), float(series["SPY"].loc[day])
        rows.append({
            "date": day, "RSP": round(rsp, 4), "SPY": round(spy, 4),
            "NYSE_comp": round(float(series["NYSE_comp"].loc[day]), 4)
            if series["NYSE_comp"] is not None and day in series["NYSE_comp"].index else "",
            "SP400_mid": round(float(series["SP400_mid"].loc[day]), 4)
            if series["SP400_mid"] is not None and day in series["SP400_mid"].index else "",
            "RSP_SPY_ratio": round(rsp / spy, 6) if spy else "",
        })
    return {"file": path.name, "status": "APPENDED", "added": _append_rows(path, existing, rows, columns)}


def refresh_kr_breadth() -> dict:
    columns = ["date", "KODEX200", "KOSPI", "KOSDAQ"]
    path = ANALYSIS / "kr_breadth_proxy_daily.csv"
    existing = _existing(path, columns)
    window = _fetch_window(existing)
    if window is None:
        return {"file": path.name, "status": "UP_TO_DATE"}
    start, end = window
    kospi = _yf_close_series("^KS11", start, end)
    kosdaq = _yf_close_series("^KQ11", start, end)
    if kospi is None:
        return {"file": path.name, "status": "FAILED", "reason": "^KS11 missing"}
    # KODEX200(069500)은 급락 레인 가격 캐시가 이미 매일 갱신한다 — 그걸 1차 소스로 쓴다.
    kodex: dict[str, float] = {}
    try:
        cache = json.loads((ANALYSIS / "kr_fallen_price_cache.json").read_text(encoding="utf-8"))
        kodex = {b["d"]: float(b["c"]) for b in cache.get("069500", [])}
    except (OSError, ValueError):
        pass
    if not kodex:
        series = _yf_close_series("069500.KS", start, end)
        kodex = {d: float(v) for d, v in series.items()} if series is not None else {}
    have = set(existing["date"])
    rows = []
    for day in sorted(set(kospi.index) - have):
        rows.append({
            "date": day,
            "KODEX200": round(kodex[day], 2) if day in kodex else "",
            "KOSPI": round(float(kospi.loc[day]), 2),
            "KOSDAQ": round(float(kosdaq.loc[day]), 2)
            if kosdaq is not None and day in kosdaq.index else "",
        })
    return {"file": path.name, "status": "APPENDED", "added": _append_rows(path, existing, rows, columns)}


def refresh_vix_term() -> dict:
    columns = ["date", "VIX", "VIX3M", "VVIX", "term_ratio_3m_1m"]
    path = ANALYSIS / "vix_term_daily.csv"
    existing = _existing(path, columns)
    window = _fetch_window(existing)
    if window is None:
        return {"file": path.name, "status": "UP_TO_DATE"}
    start, end = window
    vix = _yf_close_series("^VIX", start, end)
    vix3m = _yf_close_series("^VIX3M", start, end)
    vvix = _yf_close_series("^VVIX", start, end)
    if vix is None:
        return {"file": path.name, "status": "FAILED", "reason": "^VIX missing"}
    have = set(existing["date"])
    rows = []
    for day in sorted(set(vix.index) - have):
        v1 = float(vix.loc[day])
        v3 = float(vix3m.loc[day]) if vix3m is not None and day in vix3m.index else None
        rows.append({
            "date": day, "VIX": round(v1, 2),
            "VIX3M": round(v3, 2) if v3 is not None else "",
            "VVIX": round(float(vvix.loc[day]), 2)
            if vvix is not None and day in vvix.index else "",
            "term_ratio_3m_1m": round(v3 / v1, 4) if v3 is not None and v1 > 0 else "",
        })
    return {"file": path.name, "status": "APPENDED", "added": _append_rows(path, existing, rows, columns)}


_ADV_COLUMNS = ["date", "advancers", "decliners", "unchanged", "total_active",
                "adv_pct", "adv_minus_dec", "cum_adv_dec_line"]


def _adv_dec_rows(closes_by_ticker: dict[str, dict[str, float]], existing: pd.DataFrame) -> list[dict]:
    """일별 종가 맵 → 등락 집계. 마지막 cum 라인을 이어서 계산한다."""

    all_days: set[str] = set()
    for series in closes_by_ticker.values():
        all_days |= set(series)
    have = set(existing["date"])
    last_day = max(have) if have else ""
    new_days = sorted(d for d in all_days if d > last_day)
    cum = int(existing["cum_adv_dec_line"].iloc[-1]) if len(existing) else 0
    rows = []
    for day in new_days:
        adv = dec = unch = 0
        for series in closes_by_ticker.values():
            days = sorted(series)
            if day not in series:
                continue
            idx = days.index(day)
            if idx == 0:
                continue
            prev = series[days[idx - 1]]
            cur = series[day]
            if prev <= 0:
                continue
            if cur > prev:
                adv += 1
            elif cur < prev:
                dec += 1
            else:
                unch += 1
        total = adv + dec + unch
        if total < 20:  # 데이터가 덜 모인 날(휴장 등)은 기록하지 않는다
            continue
        cum += adv - dec
        rows.append({
            "date": day, "advancers": adv, "decliners": dec, "unchanged": unch,
            "total_active": total, "adv_pct": round(adv / total, 6) if total else 0,
            "adv_minus_dec": adv - dec, "cum_adv_dec_line": cum,
        })
    return rows


def refresh_kr_adv_dec() -> dict:
    path = ANALYSIS / "kr_adv_dec_breadth_daily.csv"
    existing = _existing(path, _ADV_COLUMNS)
    try:
        cache = json.loads((ANALYSIS / "kr_fallen_price_cache.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"file": path.name, "status": "FAILED", "reason": str(exc)}
    closes = {code: {b["d"]: float(b["c"]) for b in bars} for code, bars in cache.items()}
    rows = _adv_dec_rows(closes, existing)
    return {"file": path.name, "status": "APPENDED", "added": _append_rows(path, existing, rows, _ADV_COLUMNS)}


def refresh_us_adv_dec() -> dict:
    path = ANALYSIS / "us_adv_dec_breadth_daily.csv"
    existing = _existing(path, _ADV_COLUMNS)
    price_dir = ROOT / "data" / "price" / "us"
    if not price_dir.exists():
        return {"file": path.name, "status": "FAILED", "reason": "price dir missing"}
    last_day = max(existing["date"]) if len(existing) else "2024-01-01"
    closes: dict[str, dict[str, float]] = {}
    for csv_path in price_dir.glob("us_*.csv"):
        try:
            frame = pd.read_csv(csv_path, usecols=[0, 4], names=["date", "close"], header=0)
        except (OSError, ValueError):
            continue
        frame["date"] = frame["date"].astype(str)
        recent = frame[frame["date"] >= (date.fromisoformat(last_day) - timedelta(days=10)).isoformat()]
        if len(recent) < 2:
            continue
        closes[csv_path.stem] = dict(zip(recent["date"], recent["close"].astype(float)))
    rows = _adv_dec_rows(closes, existing)
    return {"file": path.name, "status": "APPENDED", "added": _append_rows(path, existing, rows, _ADV_COLUMNS)}


def refresh_execution_shortfall() -> dict:
    """실행 품질 원장 갱신(P0의 일일 자동화, 2026-08-05).

    수동 실행에만 의존하면 안 돌린 날의 체결이 원장에서 빠진다. 최근 5일
    로그만 스캔해 신규 체결을 append한다(멱등 — order_no 기준 중복 제거).
    """
    import contextlib
    import io
    import sys as _sys

    from tools.execution_shortfall_report import main as shortfall_main

    argv_backup = _sys.argv
    _sys.argv = ["execution_shortfall_report.py", "--days", "5"]
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            shortfall_main()
    finally:
        _sys.argv = argv_backup
    tail = [l for l in buffer.getvalue().splitlines() if l.startswith("원장 신규")]
    return {"file": "execution_shortfall_ledger.jsonl", "status": "OK",
            "detail": tail[-1] if tail else ""}


def run_once() -> list[dict]:
    results = []
    for job in (refresh_us_breadth, refresh_kr_breadth, refresh_vix_term,
                refresh_kr_adv_dec, refresh_us_adv_dec, refresh_execution_shortfall):
        try:
            results.append(job())
        except Exception as exc:  # 한 소스 실패가 나머지를 막지 않는다
            results.append({"file": job.__name__, "status": "ERROR", "reason": str(exc)})
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Market context daily refresher")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-sec", type=int, default=21600)
    args = parser.parse_args()
    if not args.loop:
        print(json.dumps(run_once(), ensure_ascii=False))
        return 0
    while True:
        print(json.dumps(run_once(), ensure_ascii=False), flush=True)
        time.sleep(max(600, args.interval_sec))


if __name__ == "__main__":
    raise SystemExit(main())
