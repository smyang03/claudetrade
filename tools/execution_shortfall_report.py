"""실행 품질(implementation shortfall) 원장 — 체결가 vs 당일 시가 실측.

P0 (2026-08-05 운영자 승인): 시뮬은 "시가 체결 + 비용 0.45~0.5%"를 가정한다.
그 가정이 실제와 얼마나 다른지가 엣지의 분모다. 실체결 4건 수동 대조에서
전부 시가와 같거나 유리했다(평균 -0.37%p — 진입창 5~30분이 개장 직후 추가
투매를 잡는 효과). 이 도구는 그 대조를 자동화·상시화한다.

설계: 봇 핫패스를 건드리지 않는다. 주문 로그 라인이 진실이다 —
  [LIVE MICRO_PROBE BUY] {ticker} {qty}@{px} | source={strategy} | order_no={no}
  [LIVE SELL] {ticker} {qty}@{px} | 주문번호={no}
(FRMI 체결 5.5225가 브로커 평단과 정확히 일치함을 실측 — 로그 px = 체결가.)

당일 시가 소스: US=yfinance 일봉, KR=급락 레인 가격 캐시(매일 갱신).
결과는 data/shadow/execution_shortfall_ledger.jsonl 에 append(멱등, order_no 기준).

사용:
  python tools/execution_shortfall_report.py            # 전체 로그 스캔 + 리포트
  python tools/execution_shortfall_report.py --days 30  # 최근 30일 로그만
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "data" / "shadow" / "execution_shortfall_ledger.jsonl"
KR_CACHE = ROOT / "data" / "analysis" / "kr_fallen_price_cache.json"

_BUY_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[LIVE MICRO_PROBE BUY\] "
    r"(?P<ticker>\S+) (?P<qty>[\d,]+)@(?P<px>[\d,.]+) \| source=(?P<source>\S+) \| order_no=(?P<no>\S+)"
)
_SELL_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[LIVE SELL\] "
    r"(?P<ticker>\S+) (?P<qty>[\d,]+)@(?P<px>[\d,.]+) \| 주문번호=(?P<no>\S+)"
)


def _num(text: str) -> float:
    return float(str(text).replace(",", ""))


def _is_us_ticker(ticker: str) -> bool:
    return not ticker.isdigit()


def _us_session_date(ts: str) -> str:
    """KST 타임스탬프 -> US 세션 날짜. 22:30 개장이므로 자정 전은 당일, 자정 후는 전일."""
    dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    return (dt - timedelta(days=1)).date().isoformat() if dt.hour < 9 else dt.date().isoformat()


def scan_logs(days: int) -> list[dict]:
    rows: list[dict] = []
    cutoff = (date.today() - timedelta(days=days)).strftime("%Y%m%d") if days else ""
    for path in sorted((ROOT / "logs" / "system").glob("live_trading_*.log")):
        stamp = "".join(ch for ch in path.stem if ch.isdigit())
        if cutoff and stamp < cutoff:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = _BUY_RE.match(line)
            side = "BUY"
            if not m:
                m = _SELL_RE.match(line)
                side = "SELL"
            if not m:
                continue
            g = m.groupdict()
            ticker = g["ticker"]
            market = "US" if _is_us_ticker(ticker) else "KR"
            session = _us_session_date(g["ts"]) if market == "US" else g["ts"][:10]
            rows.append({
                "order_no": g["no"], "side": side, "ticker": ticker, "market": market,
                "qty": int(_num(g["qty"])), "fill_px": _num(g["px"]),
                "source": g.get("source", ""), "ts": g["ts"], "session_date": session,
            })
    return rows


def _load_opens_us(pairs: set[tuple[str, str]]) -> dict[tuple[str, str], float]:
    if not pairs:
        return {}
    import yfinance as yf

    out: dict[tuple[str, str], float] = {}
    tickers = sorted({t for t, _ in pairs})
    start = min(d for _, d in pairs)
    end = (date.fromisoformat(max(d for _, d in pairs)) + timedelta(days=1)).isoformat()
    try:
        data = yf.download(tickers, start=start, end=end, progress=False,
                           auto_adjust=False, group_by="ticker", threads=False)
    except Exception:
        return {}
    for ticker, day in pairs:
        try:
            frame = data[ticker] if len(tickers) > 1 else data
            out[(ticker, day)] = float(frame.loc[day]["Open"])
        except Exception:
            continue
    return out


def _load_opens_kr(pairs: set[tuple[str, str]]) -> dict[tuple[str, str], float]:
    try:
        cache = json.loads(KR_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out: dict[tuple[str, str], float] = {}
    for ticker, day in pairs:
        for bar in cache.get(ticker, []):
            if bar.get("d") == day:
                out[(ticker, day)] = float(bar.get("o") or 0)
                break
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Execution shortfall report")
    parser.add_argument("--days", type=int, default=0, help="최근 N일 로그만 (0=전체)")
    args = parser.parse_args()

    fills = scan_logs(args.days)
    known: set[str] = set()
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                known.add(json.loads(line).get("order_no", ""))
            except ValueError:
                continue

    us_pairs = {(f["ticker"], f["session_date"]) for f in fills if f["market"] == "US"}
    kr_pairs = {(f["ticker"], f["session_date"]) for f in fills if f["market"] == "KR"}
    opens = {**_load_opens_us(us_pairs), **_load_opens_kr(kr_pairs)}

    new_rows = 0
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    print(f"{'세션':<11}{'구분':<5}{'종목':<8}{'수량':>5} {'체결가':>10} {'시가':>10} {'shortfall':>10}  source")
    enriched: list[dict] = []
    for f in sorted(fills, key=lambda x: x["ts"]):
        open_px = opens.get((f["ticker"], f["session_date"]))
        shortfall = None
        if open_px and open_px > 0:
            raw = 100 * (f["fill_px"] / open_px - 1)
            # BUY: 시가보다 싸게 = 유리(음수). SELL: 시가보다 비싸게 = 유리(양수 -> 부호 뒤집어 통일).
            shortfall = round(raw if f["side"] == "BUY" else -raw, 4)
        row = {**f, "open_px": open_px, "shortfall_pct_vs_open": shortfall,
               "convention": "음수=시뮬(시가 체결) 가정보다 유리"}
        enriched.append(row)
        print(f"{f['session_date']:<11}{f['side']:<5}{f['ticker']:<8}{f['qty']:>5} "
              f"{f['fill_px']:>10,.4f} "
              + (f"{open_px:>10,.4f} {shortfall:>+9.3f}%" if shortfall is not None else f"{'?':>10} {'?':>10}")
              + f"  {f['source']}")
        if f["order_no"] not in known:
            with open(LEDGER, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            new_rows += 1

    # 요약은 sleeve 계약 체결에만 의미가 있다 — 시뮬 가정(시가 체결)이 그 계약에만
    # 적용되기 때문이다. 장중 임의 시점의 일반 Path-A 매도를 시가와 비교하면
    # 실행 품질이 아니라 그날의 장중 드리프트를 재게 된다(실측: MARA 매도 -12.5%p 등).
    sleeve_sources = {"us_swing_5d", "kr_fallen_5d"}
    sleeve_tickers = {r["ticker"] for r in enriched if r["source"] in sleeve_sources}
    print()
    for label, rows_sel in (
        ("sleeve 매수(계약: 개장창)", [r for r in enriched if r["side"] == "BUY" and r["source"] in sleeve_sources]),
        ("sleeve 매도(TP/SL/D5)", [r for r in enriched if r["side"] == "SELL" and r["ticker"] in sleeve_tickers]),
        ("전체(참고용)", enriched),
    ):
        vals = [r["shortfall_pct_vs_open"] for r in rows_sel if r["shortfall_pct_vs_open"] is not None]
        if not vals:
            print(f"{label}: 측정 0건")
            continue
        print(f"{label}: {len(vals)}건 | 평균 {sum(vals)/len(vals):+.3f}%p (음수=유리) "
              f"| 유리 {sum(1 for v in vals if v <= 0)} / 불리 {sum(1 for v in vals if v > 0)}")
    print("판정 참고: sleeve 매수가 지속 음수면 진입창(5~30분)이 시뮬 가정 대비 엣지를 더한다.")
    print(f"원장 신규 기록 {new_rows}건 -> {LEDGER}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
