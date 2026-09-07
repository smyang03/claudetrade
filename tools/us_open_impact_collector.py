# -*- coding: utf-8 -*-
"""US 개장 15분 가격충격 쉐도우 (Codex N8, 2026-09-08) — 09:30~09:45 ET 급락 종목을 09:46 ask로 진입, 30분 만기(TP6/SL6).

신호(가격만 — iex 실시간 거래량은 통합 거래량의 일부라 분모로 쓰지 않는다, advisor): 09:30 시가 → 09:45 종가 수익률 ≤ −3% AND
직전 20세션 같은 창(09:30→09:45) 수익률의 최저값보다 낮다(종목별, sip 백필). 전일 거래대금 ≥50M.
진입: 09:46 iex 스냅샷 ask(없으면 latestTrade). 청산: 10:16 bid(없으면 latestTrade) 또는 그 사이 TP6/SL6(1분봉 고저, sip 15분 지연 허용 → 10:32 이후 정산).
특성: 거래대금(sip 09:30~09:45 누적) 은 정산 때 채운다(백필과 같은 정의).
모드: backfill(직전 20세션 창 수익률 캐시, sip) / live(09:46 대기 → 신호·진입 기록) / settle(10:32 이후 sip 분봉으로 정산).
원장: data/shadow/us_open_impact.jsonl. schtask claudetrade_us_open_impact 화~토 22:40 KST(EST면 스크립트가 09:46 ET까지 대기), PT2H.
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
US_DIR = ROOT / "data" / "price" / "us"
OUT = ROOT / "data" / "shadow" / "us_open_impact.jsonl"
WIN_CACHE = ROOT / "data" / "analysis" / "us_open_window_returns.jsonl"   # (date, ticker, ret_0930_0945, dvol_usd)
ENV = ROOT / ".env.alpaca"
ET = ZoneInfo("America/New_York")
BARS = "https://data.alpaca.markets/v2/stocks/bars"
SNAP = "https://data.alpaca.markets/v2/stocks/snapshots"
CAL = "https://paper-api.alpaca.markets/v2/calendar"
DVOL_MIN_USD = 50_000_000
RET_MAX = -3.0
TP, SL, HOLD_MIN, COST = 6.0, -6.0, 30, 0.50
LOOKBACK = 20
CONTRACT = "open_impact_v1:TP6/SL6/30min/entry09:46ask"


def _headers() -> dict[str, str]:
    env = {}
    for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("="); env[k.strip()] = v.strip()
    return {"APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"], "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"]}


def _get(url: str, headers: dict, timeout: float = 60.0) -> dict:
    return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout).read())


def _jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _append(p: Path, row: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def universe_prev_dvol(day: str) -> dict[str, float]:
    """전일(day 직전 봉) 거래대금 ≥ 기준인 종목 → 전일 거래대금."""
    out = {}
    for p in US_DIR.glob("us_*.csv"):
        rows = [r for r in csv.reader(p.open(encoding="utf-8-sig")) if r and r[0][:2] == "20"]
        prev = None
        for r in rows:
            if r[0] >= day:
                break
            prev = r
        if prev:
            try:
                dv = float(prev[4]) * float(prev[5])
            except ValueError:
                continue
            if dv >= DVOL_MIN_USD:
                out[p.stem[3:].upper()] = dv
    return out


def fetch_bars(headers: dict, symbols: list[str], day: str, start_hm: str, end_hm: str, feed: str) -> dict[str, list[dict]]:
    got: dict[str, list[dict]] = {}
    for i in range(0, len(symbols), 200):
        chunk = symbols[i:i + 200]; token = None
        while True:
            q = {"symbols": ",".join(chunk), "timeframe": "1Min", "start": f"{day}T{start_hm}:00-04:00", "end": f"{day}T{end_hm}:00-04:00",
                 "limit": 10000, "feed": feed, "adjustment": "raw"}
            if token:
                q["page_token"] = token
            try:
                d = _get(f"{BARS}?{urllib.parse.urlencode(q)}", headers)
            except Exception as exc:  # noqa: BLE001
                print(f"[IMPACT] bars 실패 {day} {exc}"); time.sleep(2.0); break
            for sym, bars in (d.get("bars") or {}).items():
                got.setdefault(sym, []).extend(bars)
            token = d.get("next_page_token")
            if not token:
                break
            time.sleep(0.2)
        time.sleep(0.25)
    return got


def _window_ret(bars: list[dict]) -> tuple[float | None, float]:
    """09:30 시가 → 09:45 이전 마지막 봉 종가 수익률(%), 창 누적 거래대금."""
    bars = sorted(bars, key=lambda b: b["t"])
    o = None; c = None; dv = 0.0
    for b in bars:
        t = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
        if (t.hour, t.minute) < (9, 30) or (t.hour, t.minute) >= (9, 45):
            continue
        if o is None:
            o = float(b["o"])
        c = float(b["c"]); dv += float(b["c"]) * float(b["v"])
    return ((c / o - 1) * 100 if o and c else None), dv


def backfill(headers: dict, days: list[str]) -> int:
    have = {(r["date"], r["ticker"]) for r in _jsonl(WIN_CACHE)}
    n = 0
    for day in days:
        uni = universe_prev_dvol(day)
        syms = [s for s in sorted(uni) if (day, s) not in have]
        if not syms:
            continue
        bars = fetch_bars(headers, syms, day, "09:30", "09:46", "sip")
        for s in syms:
            ret, dv = _window_ret(bars.get(s, []))
            if ret is None:
                continue
            _append(WIN_CACHE, {"date": day, "ticker": s, "ret": round(ret, 3), "dvol_win": round(dv), "feed": "sip"}); n += 1
        print(f"[IMPACT] {day} 창 수익률 {len(syms)}종목")
    return n


def _sessions_before(day: str, n: int) -> list[str]:
    ds = sorted({r[0] for r in csv.reader((US_DIR / "us_SPY.csv").open(encoding="utf-8-sig")) if r and r[0][:2] == "20" and r[0] < day})
    return ds[-n:]


def _wait_until(hh: int, mm: int) -> None:
    while True:
        now = datetime.now(ET)
        if (now.hour, now.minute) >= (hh, mm):
            return
        time.sleep(min(300, max(1, (now.replace(hour=hh, minute=mm, second=0, microsecond=0) - now).total_seconds())))


def live(headers: dict) -> int:
    today = datetime.now(ET).date().isoformat()
    try:
        cal = _get(f"{CAL}?start={today}&end={today}", headers)
        if not any(x.get("date") == today for x in cal):
            print(f"[IMPACT] {today} 휴장"); return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[IMPACT] 캘린더 실패 {exc} — 거래일 가정")
    if any(r.get("kind") == "session" and r.get("session_date") == today for r in _jsonl(OUT)):
        print(f"[IMPACT] {today} 이미 기록"); return 0
    if datetime.now(ET).strftime("%H:%M") > "09:50":
        _append(OUT, {"kind": "session", "session_date": today, "n_pass": 0, "note": "09:50 이후 실행 — 신호 시각 놓침"}); return 0
    prior = _sessions_before(today, LOOKBACK)
    backfill(headers, prior)   # 직전 20세션 창 수익률 캐시(멱등)
    floor: dict[str, float] = {}
    for r in _jsonl(WIN_CACHE):
        if r["date"] in prior:
            floor[r["ticker"]] = min(floor.get(r["ticker"], 999.0), float(r["ret"]))
    _wait_until(9, 46)
    uni = universe_prev_dvol(today)
    bars = fetch_bars(headers, sorted(uni), today, "09:30", "09:46", "iex")
    picks = []
    for s in sorted(uni):
        ret, _ = _window_ret(bars.get(s, []))
        if ret is not None and ret <= RET_MAX and s in floor and ret < floor[s]:
            picks.append((s, ret))
    n_open = 0
    if picks:
        snap = _get(f"{SNAP}?{urllib.parse.urlencode({'symbols': ','.join(s for s, _ in picks), 'feed': 'iex'})}", headers)
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for s, ret in picks:
            q = snap.get(s) or {}; ask = (q.get("latestQuote") or {}).get("ap"); lt = (q.get("latestTrade") or {}).get("p")
            px = float(ask) if ask else (float(lt) if lt else None)
            if not px:
                continue
            _append(OUT, {"kind": "trade", "session_date": today, "ticker": s, "entry_rule": "ask_0946", "entry_price": round(px, 4),
                          "entry_at": now_iso, "ret_0930_0945": round(ret, 3), "floor20": round(floor[s], 3), "prev_dvol_usd": round(uni[s]),
                          "contract": CONTRACT, "status": "OPEN", "quote_bid": (q.get("latestQuote") or {}).get("bp"), "quote_ask": ask, "feed": "iex"})
            n_open += 1
    _append(OUT, {"kind": "session", "session_date": today, "universe_n": len(uni), "n_pass": len(picks), "n_open": n_open, "feed": "iex"})
    print(f"[IMPACT] {today} 유니버스 {len(uni)} 통과 {len(picks)} 진입 {n_open}")
    return n_open


def settle(headers: dict) -> int:
    rows = _jsonl(OUT); n = 0
    opens = [r for r in rows if r.get("kind") == "trade" and r.get("status") == "OPEN"]
    by_day: dict[str, list[dict]] = {}
    for r in opens:
        by_day.setdefault(r["session_date"], []).append(r)
    for day, trs in by_day.items():
        if datetime.now(ET).date().isoformat() == day and datetime.now(ET).strftime("%H:%M") < "10:32":
            continue   # sip 15분 지연
        bars = fetch_bars(headers, sorted({r["ticker"] for r in trs}), day, "09:46", "10:17", "sip")
        win = fetch_bars(headers, sorted({r["ticker"] for r in trs}), day, "09:30", "09:46", "sip")
        for r in trs:
            bs = sorted(bars.get(r["ticker"], []), key=lambda b: b["t"]); entry = r["entry_price"]
            if not bs:
                continue
            reason = None; px = None
            for b in bs:
                if float(b["l"]) <= entry * (1 + SL / 100):
                    reason, px = "SL", entry * (1 + SL / 100); break
                if float(b["h"]) >= entry * (1 + TP / 100):
                    reason, px = "TP", entry * (1 + TP / 100); break
            if reason is None:
                reason, px = "TIME", float(bs[-1]["c"])
            _, dv = _window_ret(win.get(r["ticker"], []))
            r.update({"status": "CLOSED", "exit_reason": reason, "exit_price": round(px, 4), "net_pct": round((px / entry - 1) * 100 - COST, 3),
                      "dvol_win_sip": round(dv), "settled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}); n += 1
    if n:
        tmp = OUT.with_suffix(".tmp"); tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8"); tmp.replace(OUT)
    print(f"[IMPACT] 정산 {n}행")
    return n


def main() -> int:
    args = sys.argv[1:]; cmd = args[0] if args else "settle"
    h = _headers()
    if cmd == "backfill":
        days = _sessions_before(datetime.now(ET).date().isoformat(), int(args[args.index("--days") + 1]) if "--days" in args else LOOKBACK)
        backfill(h, days)
    elif cmd == "live":
        live(h); settle(h)
    elif cmd == "settle":
        settle(h)
    else:
        print("사용: us_open_impact_collector.py [backfill [--days N]|live|settle]"); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
