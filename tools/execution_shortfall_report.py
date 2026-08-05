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
# 2026-08-06 실측 결함: 체결이 즉시 확인되지 않으면 매도가 [LIVE SELL PENDING]으로
# 남고 가격이 로그에 없다(브로커 truth가 나중에 확정). FRMI TP 청산이 이 경로여서
# 원장에서 통째로 빠졌다. 확정 로그(close_position)에서 손익률을 주워 보완한다.
# 체결가는 진입가와 손익률로 역산하지 않는다 — KRW net(FX 포함)이라 USD 체결가와
# 다르다. 대신 realized_pnl_pct로 기록하고 shortfall은 미측정으로 둔다.
_SELL_PENDING_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[LIVE SELL PENDING\] "
    r"(?P<ticker>\S+) order_no=(?P<no>\S+)"
)
_CLOSE_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*close_position:\d+ \| "
    r"\[(?P<reason>[a-z_]+)\] (?P<ticker>\S+) (?P<pnl_krw>[+-][\d,]+) \((?P<pnl_pct>[+-][\d.]+)%\)"
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
        pending_sells: dict[str, dict] = {}
        for line in text.splitlines():
            # 확정 청산 로그 — PENDING으로 나간 매도의 실현 손익을 여기서 얻는다.
            close_m = _CLOSE_RE.match(line)
            if close_m:
                cg = close_m.groupdict()
                held = pending_sells.pop(cg["ticker"], None)
                if held:
                    market = "US" if _is_us_ticker(cg["ticker"]) else "KR"
                    rows.append({
                        "order_no": held["no"], "side": "SELL", "ticker": cg["ticker"],
                        "market": market, "qty": 0, "fill_px": 0.0,
                        "source": "", "ts": cg["ts"],
                        "session_date": _us_session_date(cg["ts"]) if market == "US" else cg["ts"][:10],
                        "exit_reason": cg["reason"],
                        "realized_pnl_pct": float(cg["pnl_pct"]),
                        "realized_pnl_krw": _num(cg["pnl_krw"]),
                        "fill_px_unavailable": "pending_path_no_price_in_log",
                    })
                continue
            pend_m = _SELL_PENDING_RE.match(line)
            if pend_m:
                pending_sells[pend_m.group("ticker")] = pend_m.groupdict()
                continue
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
        # 체결가가 없는 행(PENDING 경로 확정분)은 shortfall 미측정 — 0으로 계산하면
        # -100%가 되어 통계를 오염시킨다. 실현 손익만 기록한다.
        if f.get("fill_px_unavailable"):
            open_px = None
        if open_px and open_px > 0:
            raw = 100 * (f["fill_px"] / open_px - 1)
            # BUY: 시가보다 싸게 = 유리(음수). SELL: 시가보다 비싸게 = 유리(양수 -> 부호 뒤집어 통일).
            shortfall = round(raw if f["side"] == "BUY" else -raw, 4)
        row = {**f, "open_px": open_px, "shortfall_pct_vs_open": shortfall,
               "convention": "음수=시뮬(시가 체결) 가정보다 유리"}
        enriched.append(row)
        if f.get("fill_px_unavailable"):
            print(f"{f['session_date']:<11}{f['side']:<5}{f['ticker']:<8}{'':>5} "
                  f"{'가격없음':>10} {'':>10} {'미측정':>10}"
                  f"  실현 {f.get('realized_pnl_pct'):+.2f}% ({f.get('realized_pnl_krw'):+,.0f}원) "
                  f"{f.get('exit_reason','')}")
        else:
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
