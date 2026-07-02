from __future__ import annotations
"""트렌드 방어 오버레이 신호 갱신 — 지수 10개월 SMA → state/trend_overlay_signal.json.

라이브 루프 밖에서 일 1회(수동/cron) 실행. yfinance로 지수 월봉 받아
월말 종가 vs 10개월 SMA → below_sma 계산. 게이트(bot/trend_overlay_gate.py)가 이 캐시를 읽음.

지수: US=SPY(브로드 마켓), KR=^KS11(KOSPI). 검증과 동일 룰(Faber 10mo).
read-only(쓰는 건 신호 캐시 1개뿐). 봇/주문 무관.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "state" / "trend_overlay_signal.json"
INDEX = {"US": "SPY", "KR": "^KS11"}


def compute(sym: str) -> dict | None:
    import yfinance as yf
    df = yf.download(sym, period="5y", interval="1mo", progress=False, auto_adjust=True)
    if df is None or df.empty:
        return None
    if df.columns.nlevels > 1:
        df.columns = df.columns.get_level_values(0)
    c = df["Close"].dropna()
    if len(c) < 11:
        return None
    sma10 = float(c.tail(10).mean())
    close = float(c.iloc[-1])
    as_of = c.index[-1]
    return {
        "index_sym": sym,
        "index_close": round(close, 4),
        "sma": round(sma10, 4),
        "below_sma": bool(close < sma10),
        "as_of": as_of.strftime("%Y-%m-%d"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="트렌드 오버레이 신호 갱신")
    ap.add_argument("--dry-run", action="store_true", help="파일 안 쓰고 출력만")
    args = ap.parse_args()

    markets = {}
    for mkt, sym in INDEX.items():
        try:
            r = compute(sym)
            if r:
                markets[mkt] = r
                state = "하락추세(below)" if r["below_sma"] else "상승추세(above)"
                print(f"{mkt} {sym}: close={r['index_close']} sma10={r['sma']} "
                      f"=> {state}  as_of={r['as_of']}")
            else:
                print(f"{mkt} {sym}: 데이터 없음(skip — 게이트는 fail-open)")
        except Exception as e:
            print(f"{mkt} {sym}: 오류 {str(e)[:80]} (skip — fail-open)")

    if not markets:
        print("신호 0개 — 파일 미갱신")
        return 1
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rule": "monthly_close_vs_10mo_sma",
        "markets": markets,
    }
    if args.dry_run:
        print("\n[dry-run] 미저장:\n" + json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"\n저장: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
