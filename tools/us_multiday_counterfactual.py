"""멀티데이 볼록트랙 반사실 — 실제 청산 트레이드를 "N일 더 보유했으면"으로 재평가.

근거: d1 분해(2026-07-11) — US 적자는 전부 당일청산, 멀티데이는 흑자.
이 도구는 그 명제를 우리 실체결 원장(v2_learning_performance, live·closed)에
직접 검증한다. 읽기 전용·주문경로 무접촉(shadow 분석 전용).

--market으로 US/KR 분리(2026-07-21 코덱스 검토 반영): KR은 정반대 결론을 재현
가능하게 하기 위해 일반화. KR 결과(전 코호트 5일 연장 음수 = 출구 near-optimal)는
ultimate 리포트의 핵심 문장이므로 이 스크립트로 고정한다.

- 반사실 가격: data/price/{mkt}/{mkt}_TICKER.csv (일봉). 청산일 다음 거래일 시가/종가,
  +2일 종가, +5일 종가로 청산했다면의 추가 수익률(청산가 대비 %)을 계산.
- 동일 1회 매도이므로 왕복 수수료 추가 없음. FX 드리프트는 미반영(명시 한계).
- 코호트: close_reason별 · 청산시점 승/패별. 평균 뒤 분포(양/음 비율)도 표시.

사용: python tools/us_multiday_counterfactual.py [--market US|KR] [--start 2026-06-01]
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "ml" / "decisions.db"

HORIZONS = ("next_open", "next_close", "plus2_close", "plus5_close")


def _price_ticker(market: str, ticker: str) -> str:
    """가격 CSV 파일명 티커 정규화 — KR은 6자리 zero-fill, US는 대문자."""
    raw = str(ticker or "").strip()
    return raw.zfill(6) if str(market).upper() == "KR" else raw.upper()


def _load_price_series(market: str, ticker: str) -> list[dict]:
    sub = "kr" if str(market).upper() == "KR" else "us"
    path = ROOT / "data" / "price" / sub / f"{sub}_{_price_ticker(market, ticker)}.csv"
    if not path.exists():
        return []
    out = []
    try:
        with path.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                try:
                    out.append({
                        "date": str(row["date"]).strip(),
                        "open": float(row["open"]),
                        "close": float(row["close"]),
                    })
                except (KeyError, TypeError, ValueError):
                    continue
    except OSError:
        return []
    out.sort(key=lambda r: r["date"])
    return out


def _counterfactuals(series: list[dict], exit_date: str, exit_price: float) -> dict[str, float] | None:
    if not series or exit_price <= 0:
        return None
    idx = None
    for i, row in enumerate(series):
        if row["date"] > exit_date:
            idx = i
            break
    if idx is None:
        return None  # 청산 이후 데이터 없음(최근 청산)
    out: dict[str, float] = {}
    nxt = series[idx]
    out["next_open"] = (nxt["open"] - exit_price) / exit_price * 100
    out["next_close"] = (nxt["close"] - exit_price) / exit_price * 100
    if idx + 1 < len(series):
        out["plus2_close"] = (series[idx + 1]["close"] - exit_price) / exit_price * 100
    if idx + 4 < len(series):
        out["plus5_close"] = (series[idx + 4]["close"] - exit_price) / exit_price * 100
    return out


def _summ(vals: list[float]) -> str:
    if not vals:
        return "n=0"
    m = statistics.fmean(vals)
    med = statistics.median(vals)
    pos = sum(1 for v in vals if v > 0) / len(vals)
    return f"n={len(vals):>3} mean={m:+.3f}% med={med:+.3f}% pos={pos:.0%}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="US", choices=["US", "KR"])
    ap.add_argument("--start", default="")
    args = ap.parse_args()
    market = args.market.upper()
    start = args.start or ("2026-06-01" if market == "US" else "2026-05-01")

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("pragma busy_timeout=5000")
    rows = con.execute(
        """select session_date, ticker, close_reason, closed_at, exit_price,
        coalesce(pnl_pct_net, pnl_pct) as pnl
        from v2_learning_performance
        where runtime_mode='live' and closed=1 and market=?
          and session_date>=? and exit_price is not null""",
        (market, start),
    ).fetchall()
    con.close()

    joined = []
    missing_price = 0
    for r in rows:
        exit_date = str(r["closed_at"] or "")[:10] or str(r["session_date"])
        series = _load_price_series(market, str(r["ticker"]))
        cf = _counterfactuals(series, exit_date, float(r["exit_price"] or 0))
        if cf is None:
            missing_price += 1
            continue
        joined.append({**dict(r), "exit_date": exit_date, **cf})

    print(f"{market} closed 트레이드 {len(rows)}건 (start {start}) — 반사실 계산 {len(joined)}건, 가격누락 {missing_price}건\n")
    if not joined:
        return 0

    for label, pred in (
        ("전체", lambda t: True),
        ("청산시점 승자(pnl>0)", lambda t: (t["pnl"] or 0) > 0),
        ("청산시점 패자(pnl<=0)", lambda t: (t["pnl"] or 0) <= 0),
    ):
        sub = [t for t in joined if pred(t)]
        print(f"=== {label} (n={len(sub)}) — 청산가 대비 추가수익률 ===")
        for h in HORIZONS:
            vals = [t[h] for t in sub if h in t]
            print(f"  {h:12} {_summ(vals)}")
        print()

    print("=== close_reason별 (next_close / plus5_close) ===")
    by_reason = defaultdict(list)
    for t in joined:
        by_reason[str(t["close_reason"] or "?")].append(t)
    for reason, ts in sorted(by_reason.items(), key=lambda x: -len(x[1])):
        nc = [t["next_close"] for t in ts if "next_close" in t]
        p5 = [t["plus5_close"] for t in ts if "plus5_close" in t]
        print(f"  {reason:32} n={len(ts):>3} next_close {_summ(nc)}")
        print(f"  {'':32}        plus5_close {_summ(p5)}")
    print("\n한계: FX 드리프트 미반영·생존 청산만(미청산 보유 제외)·가격 CSV 커버리지 의존.")
    print("판정 규율: shadow 분석 전용. 보유연장 정책 변경은 운영자 확인 필수.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
