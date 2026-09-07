# -*- coding: utf-8 -*-
"""US 패닉일 마감 진입 쉐도우 원장 — 신규 전략 P1(급락주 15:45 ET 마감 매수)·P2(TQQQ/IWM/SPY 1주) (2026-09-07).

구조: 시장 전체 급락일(하락 종목 비율 ≥65%) 마감에는 레버리지 ETF 리밸런싱·마진콜·손절이 시각에 묶여 판다. 그 반대편에
15:45 ET에 선다. 09-07 실측(분봉 캐시 6세션): 오버나이트 +2.33%(세션 t 3.2) — 판정 아님.

모드
- backfill : virtual_books.db(xus_fallen3, 신호일 breadth_eod ≥65)의 패닉 세션마다 Alpaca SIP 1분봉으로 15:40 등락률·15:45 가격을
             복원. 후보는 일봉 CSV "종가 ≤−1% & 전일 거래대금 ≥50M" 상위집합(전 종목 분봉 대신) → 15:40 시점 ≤−5%만 통과. breadth는 EOD 태그(근사, 명시).
- live     : 15:40 ET에 Alpaca 스냅샷(iex, 무료)으로 유니버스 breadth_1540·등락률 → ≥65%면 ≤−5% & 전일 거래대금 ≥50M 통과자와
             ETF 3종을 15:45 스냅샷 가격으로 OPEN 기록. 휴장·조건 미충족도 세션 행을 남긴다(관측 완전성).
- settle   : OPEN 행을 일봉으로 정산(TP20/SL25/D10, 비용 0.50%). breadth_eod도 일봉으로 계산해 live 값과 괴리를 남긴다.
원장: data/shadow/us_panic_close.jsonl — 행 종류 session / trade. (session_date, ticker) 멱등.
schtask: claudetrade_us_panic_close 매일 04:35 KST → `live`(스크립트가 15:40 ET까지 대기, EST면 1시간) 후 `settle`.
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
DB = ROOT / "data" / "shadow" / "virtual_books.db"
OUT = ROOT / "data" / "shadow" / "us_panic_close.jsonl"
ENV = ROOT / ".env.alpaca"
ET = ZoneInfo("America/New_York")
BARS = "https://data.alpaca.markets/v2/stocks/bars"
SNAP = "https://data.alpaca.markets/v2/stocks/snapshots"
CAL = "https://paper-api.alpaca.markets/v2/calendar"
ETFS = ("TQQQ", "IWM", "SPY")
BREADTH_MIN = 65.0
CHG_MAX = -5.0
DVOL_MIN_USD = 50_000_000
TP, SL, HOLD, COST = 20.0, -25.0, 10, 0.50
CONTRACT = "panic_close_v1:TP20/SL25/D10/entry15:45"


def _headers() -> dict[str, str]:
    env = {}
    for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("="); env[k.strip()] = v.strip()
    return {"APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"], "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"]}


def _get(url: str, headers: dict, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(url, headers=headers)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


_DAILY: dict[str, tuple[dict, list[str]] | None] = {}


def daily(tk: str):
    if tk not in _DAILY:
        p = US_DIR / f"us_{tk}.csv"
        if not p.exists():
            _DAILY[tk] = None
        else:
            rows = [r for r in csv.reader(p.open(encoding="utf-8-sig")) if r and r[0][:2] == "20"]
            try:
                _DAILY[tk] = ({r[0]: (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])) for r in rows}, [r[0] for r in rows])
            except (ValueError, IndexError):
                _DAILY[tk] = None
    return _DAILY[tk]


def _load() -> list[dict]:
    if not OUT.exists():
        return []
    out = []
    for line in OUT.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _rewrite(rows: list[dict]) -> None:
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(OUT)


def _append(row: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


# ── backfill ────────────────────────────────────────────────────────────────
SUPERSET_CLOSE_MAX = -1.0   # 후보 상위집합: 종가 ≤−1%(15:40 ≤−5%인데 마감에 +4%p 이상 되돌린 종목만 누락 — 잔여 편향, 문서화)


def panic_sessions() -> dict[str, dict]:
    """신호일 → {breadth_eod, tickers}. 패닉 세션은 virtual_books xus_fallen3 meta.regime(EOD breadth ≥65)으로 고르고,
    후보 상위집합은 일봉 CSV에서 종가 ≤ SUPERSET_CLOSE_MAX(−1%)로 넓힌다(Codex P0: DB의 ≤−3% 상위집합은 마감 되돌림 종목을 빠뜨림)."""
    import sqlite3
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); con.execute("PRAGMA busy_timeout=5000")
    out: dict[str, dict] = {}
    for meta, in con.execute("SELECT meta FROM trades WHERE strategy_id='xus_fallen3'"):
        try:
            m = json.loads(meta)
        except ValueError:
            continue
        b = (m.get("regime") or {}).get("breadth_down_pct"); sd = m.get("signal_date")
        if b is None or sd is None or b < BREADTH_MIN:
            continue
        out.setdefault(sd, {"breadth_eod": b, "tickers": set()})
    con.close()
    if not out:
        return out
    for pth in US_DIR.glob("us_*.csv"):
        tk = pth.stem[3:].upper(); d = daily(tk)
        if not d:
            continue
        bars, dates = d
        for sd in out:
            if sd in bars:
                i = dates.index(sd)
                if i >= 1 and bars[dates[i - 1]][3] > 0 and (bars[sd][3] / bars[dates[i - 1]][3] - 1) * 100 <= SUPERSET_CLOSE_MAX \
                        and bars[dates[i - 1]][3] * bars[dates[i - 1]][4] >= DVOL_MIN_USD:
                    out[sd]["tickers"].add(tk)
    return out


def fetch_minutes(headers: dict, symbols: list[str], day: str) -> dict[str, list[dict]]:
    start = f"{day}T09:30:00-04:00"; end = f"{day}T16:05:00-04:00"
    got: dict[str, list[dict]] = {}
    for i in range(0, len(symbols), 25):
        chunk = symbols[i:i + 25]; token = None
        while True:
            q = {"symbols": ",".join(chunk), "timeframe": "1Min", "start": start, "end": end, "limit": 10000,
                 "feed": "sip", "adjustment": "raw"}
            if token:
                q["page_token"] = token
            try:
                d = _get(f"{BARS}?{urllib.parse.urlencode(q)}", headers)
            except Exception as exc:  # noqa: BLE001
                print(f"[PANIC] bars 실패 {day} {chunk[:3]}… {exc}"); time.sleep(2.0); break
            for sym, bars in (d.get("bars") or {}).items():
                got.setdefault(sym, []).extend(bars)
            token = d.get("next_page_token")
            if not token:
                break
            time.sleep(0.2)
        time.sleep(0.3)
    return got


def _px_at(bars: list[dict], hh: int, mm: int) -> float | None:
    """hh:mm ET 이전 마지막 분봉 종가."""
    best = None
    for b in bars:
        t = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
        if (t.hour, t.minute) <= (hh, mm):
            best = float(b["c"])
        else:
            break
    return best


def _raw_close(headers: dict, tk: str, day: str) -> float | None:
    q = {"symbols": tk, "timeframe": "1Day", "start": f"{day}T00:00:00-05:00", "end": f"{day}T23:59:00-05:00", "feed": "sip", "adjustment": "raw"}
    try:
        d = _get(f"{BARS}?{urllib.parse.urlencode(q)}", headers)
        b = (d.get("bars") or {}).get(tk) or []
        return float(b[-1]["c"]) if b else None
    except Exception:  # noqa: BLE001
        return None


def settle_row(r: dict, headers: dict | None = None) -> dict:
    d = daily(r["ticker"])
    if not d:
        return r
    bars, dates = d
    sd = r["session_date"]
    if sd not in bars:
        return r
    i = dates.index(sd)
    if r.get("mode") == "live" and r.get("adj_factor") is None:
        # Codex P0: live 진입가는 raw(iex latestTrade), 정산은 CSV(수정주가) → 세션일 raw 종가 대비 CSV 종가 비율로 변환
        rc = _raw_close(headers or _headers(), r["ticker"], sd)
        if not rc:
            return r   # 변환 불가면 정산 보류(다음 실행)
        r["adj_factor"] = round(bars[sd][3] / rc, 6); r["entry_price_raw"] = r["entry_price"]
        r["entry_price"] = round(r["entry_price"] * r["adj_factor"], 4)
    entry = r["entry_price"]
    nxt = dates[i + 1] if i + 1 < len(dates) else None
    if nxt:
        r["next_open"] = bars[nxt][0]; r["overnight_pct"] = round((bars[nxt][0] / entry - 1) * 100, 3)
    reason = None; exit_px = None; held = 0
    for k in range(1, HOLD + 1):
        if i + k >= len(dates):
            break
        o, h, l, c, _ = bars[dates[i + k]]; held = k
        sl_px = entry * (1 + SL / 100); tp_px = entry * (1 + TP / 100)
        if o <= sl_px:
            reason, exit_px = "SL", o; break        # 갭 하락: 시가 체결(Codex P1 — SL가 체결 가정 금지)
        if l <= sl_px:
            reason, exit_px = "SL", sl_px; break
        if o >= tp_px:
            reason, exit_px = "TP", o; break
        if h >= tp_px:
            reason, exit_px = "TP", tp_px; break
        if k == HOLD:
            reason, exit_px = "D_MAT", c
    if reason:
        r.update({"status": "CLOSED", "exit_reason": reason, "exit_price": round(exit_px, 4), "held": held,
                  "net_pct": round((exit_px / entry - 1) * 100 - COST, 3),
                  "hold_d5_pct": round((bars[dates[i + 5]][3] / entry - 1) * 100 - COST, 3) if i + 5 < len(dates) else None,
                  "hold_d10_pct": round((bars[dates[i + 10]][3] / entry - 1) * 100 - COST, 3) if i + 10 < len(dates) else None})
    return r


def breadth_eod(day: str) -> float | None:
    tot = dn = 0
    for p in US_DIR.glob("us_*.csv"):
        d = daily(p.stem[3:])
        if not d or day not in d[0]:
            continue
        bars, dates = d; i = dates.index(day)
        if i < 1:
            continue
        tot += 1; dn += 1 if bars[day][3] < bars[dates[i - 1]][3] else 0
    return round(100.0 * dn / tot, 1) if tot else None


def backfill(max_sessions: int | None = None) -> int:
    headers = _headers()
    have = {(r.get("session_date"), r.get("ticker")) for r in _load() if r.get("kind") == "trade"}
    sess_done = {r.get("session_date") for r in _load() if r.get("kind") == "session" and r.get("mode") == "backfill"}
    n = 0
    for sd, s in sorted(panic_sessions().items()):
        if sd in sess_done:
            continue
        if max_sessions is not None and n >= max_sessions:
            break
        syms = sorted(s["tickers"]) + list(ETFS)
        mins = fetch_minutes(headers, syms, sd)
        n_pass = 0
        for tk in syms:
            bars = sorted(mins.get(tk, []), key=lambda b: b["t"])
            if not bars or (sd, tk) in have:
                continue
            d = daily(tk)
            if not d or sd not in d[0]:
                continue
            i = d[1].index(sd)
            if i < 1:
                continue
            pc = d[0][d[1][i - 1]][3]
            p1540 = _px_at(bars, 15, 40); p1545 = _px_at(bars, 15, 45); p_last = _px_at(bars, 16, 5)
            if p1540 is None or p1545 is None or not p_last:
                continue
            # Alpaca raw → 일봉 CSV 기준(수정주가)으로 변환: 같은 날 종가 비율(분할·배당 조정 흡수). 정산은 CSV 기준이므로 필수.
            factor = d[0][sd][3] / p_last
            p1540 *= factor; p1545 *= factor
            chg1540 = (p1540 / pc - 1) * 100
            is_etf = tk in ETFS
            if not is_etf and chg1540 > CHG_MAX:
                continue
            row = {"kind": "trade", "mode": "backfill", "session_date": sd, "ticker": tk, "instrument": "etf" if is_etf else "stock",
                   "entry_rule": "close_1545", "entry_price": round(p1545, 4), "chg_1540_pct": round(chg1540, 3), "adj_factor": round(factor, 6),
                   "close_pct": round((d[0][sd][3] / pc - 1) * 100, 3), "breadth_eod": s["breadth_eod"], "breadth_1540": None,
                   "contract": CONTRACT, "status": "OPEN", "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            _append(settle_row(row)); have.add((sd, tk)); n_pass += 0 if is_etf else 1
        _append({"kind": "session", "mode": "backfill", "session_date": sd, "breadth_eod": s["breadth_eod"], "breadth_1540": None,
                 "n_candidates": len(s["tickers"]), "n_pass": n_pass, "note": "후보=종가≤−3% 상위집합 근사, breadth=EOD 태그"})
        n += 1; print(f"[PANIC] {sd} breadth_eod {s['breadth_eod']} 후보 {len(s['tickers'])} 통과 {n_pass}")
    return n


# ── live ────────────────────────────────────────────────────────────────────
def _calendar(headers: dict, day: str) -> dict | None:
    """{'date','open','close'} 또는 None(휴장). 조회 실패는 {'date': day, 'close': None}(거래일 가정, 폐장 시각 미상)."""
    try:
        d = _get(f"{CAL}?start={day}&end={day}", headers)
        return next((x for x in d if x.get("date") == day), None)
    except Exception as exc:  # noqa: BLE001
        print(f"[PANIC] 캘린더 실패 {exc} — 거래일로 가정"); return {"date": day, "close": None}


def snapshots(headers: dict, symbols: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for i in range(0, len(symbols), 200):
        chunk = symbols[i:i + 200]
        try:
            d = _get(f"{SNAP}?{urllib.parse.urlencode({'symbols': ','.join(chunk), 'feed': 'iex'})}", headers)
            out.update({k: v for k, v in d.items() if isinstance(v, dict)})
        except Exception as exc:  # noqa: BLE001
            print(f"[PANIC] snapshot 실패 {exc}"); time.sleep(1.0)
        time.sleep(0.25)
    return out


def _wait_until(hh: int, mm: int) -> None:
    while True:
        now = datetime.now(ET)
        if (now.hour, now.minute) >= (hh, mm):
            return
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        time.sleep(min(300, max(1, (target - now).total_seconds())))


def live() -> int:
    headers = _headers()
    today = datetime.now(ET).date().isoformat()
    cal = _calendar(headers, today)
    if cal is None:
        print(f"[PANIC] {today} 휴장 — 스킵"); return 0
    if cal.get("close") and str(cal["close"]) < "15:45":
        _append({"kind": "session", "mode": "live", "session_date": today, "breadth_1540": None, "breadth_eod": None,
                 "note": f"조기 폐장 {cal['close']} — 15:45 진입 불가, 스킵"}); print(f"[PANIC] {today} 조기 폐장 — 스킵"); return 0
    existing = _load()
    if any(r.get("kind") == "session" and r.get("session_date") == today and r.get("mode") == "live" for r in existing):
        print(f"[PANIC] {today} 이미 기록"); return 0
    have = {(r.get("session_date"), r.get("ticker")) for r in existing if r.get("kind") == "trade"}
    if datetime.now(ET).strftime("%H:%M") > "15:44":
        _append({"kind": "session", "mode": "live", "session_date": today, "breadth_1540": None, "breadth_eod": None,
                 "note": "15:40 이후 실행 — 신호 시각 놓침, 스킵"}); print(f"[PANIC] {today} 늦은 실행 — 스킵"); return 0
    _wait_until(15, 40)
    universe = sorted(p.stem[3:] for p in US_DIR.glob("us_*.csv"))
    snap = snapshots(headers, universe)
    chg: dict[str, float] = {}; dvol: dict[str, float] = {}
    for tk, s in snap.items():
        lt = (s.get("latestTrade") or {}).get("p"); pv = s.get("prevDailyBar") or {}
        if not lt or not pv.get("c"):
            continue
        chg[tk] = (float(lt) / float(pv["c"]) - 1) * 100; dvol[tk] = float(pv["c"]) * float(pv.get("v") or 0)
    n = len(chg); dn = sum(1 for v in chg.values() if v < 0)
    b1540 = round(100.0 * dn / n, 1) if n else None
    panic = (b1540 or 0) >= BREADTH_MIN
    picks = [tk for tk, v in chg.items() if v <= CHG_MAX and dvol.get(tk, 0) >= DVOL_MIN_USD] if panic else []
    print(f"[PANIC] {today} 15:40 breadth {b1540} (n={n}) 통과 {len(picks)}")
    n_open = 0
    if panic:   # Codex P1: 통과 종목이 없어도 패닉일이면 ETF 행은 기록
        _wait_until(15, 45)
        snap2 = snapshots(headers, sorted(picks) + list(ETFS))
        now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for tk in sorted(picks) + list(ETFS):
            s = snap2.get(tk) or {}; lt = (s.get("latestTrade") or {}).get("p")
            if not lt or (today, tk) in have:
                continue
            _append({"kind": "trade", "mode": "live", "session_date": today, "ticker": tk, "instrument": "etf" if tk in ETFS else "stock",
                     "entry_rule": "close_1545", "entry_price": round(float(lt), 4), "chg_1540_pct": round(chg.get(tk, 0.0), 3),
                     "breadth_eod": None, "breadth_1540": b1540, "contract": CONTRACT, "status": "OPEN", "opened_at": now_iso,
                     "quote_ask": ((s.get("latestQuote") or {}).get("ap")), "quote_bid": ((s.get("latestQuote") or {}).get("bp"))})
            n_open += 1
    _append({"kind": "session", "mode": "live", "session_date": today, "breadth_1540": b1540, "breadth_eod": None,
             "universe_n": n, "n_pass": len(picks), "n_open": n_open, "feed": "iex",
             "note": "iex 스냅샷(무료 피드) — SIP 대비 커버리지·체결 지연 편향 있음, breadth_eod와 괴리 기록"})
    return n_open


def settle() -> int:
    rows = _load(); n = 0; headers = None
    for r in rows:
        if r.get("kind") == "trade" and r.get("status") == "OPEN":
            if r.get("mode") == "live" and r.get("adj_factor") is None and headers is None:
                headers = _headers()
            before = r.get("status"); settle_row(r, headers)
            if r.get("status") != before or r.get("overnight_pct") is not None:
                n += 1
        if r.get("kind") in ("trade", "session") and r.get("breadth_eod") is None and r.get("mode") == "live":
            b = breadth_eod(r["session_date"])
            if b is not None:
                r["breadth_eod"] = b
    _rewrite(rows)
    print(f"[PANIC] 정산 갱신 {n}행")
    return n


def report() -> None:
    import statistics as st
    rows = [r for r in _load() if r.get("kind") == "trade" and r.get("status") == "CLOSED"]
    for inst in ("stock", "etf"):
        sub = [r for r in rows if r.get("instrument") == inst]
        if not sub:
            continue
        by: dict[str, list[float]] = {}
        for r in sub:
            by.setdefault(r["session_date"], []).append(r["net_pct"])
        sm = [st.mean(v) for v in by.values()]
        ov = [r["overnight_pct"] for r in sub if r.get("overnight_pct") is not None]
        t = (st.mean(sm) / (st.pstdev(sm) / len(sm) ** 0.5)) if len(sm) > 1 and st.pstdev(sm) else 0.0
        print(f"[PANIC] {inst}: n={len(sub)} sessions={len(sm)} net={st.mean(r['net_pct'] for r in sub):+.2f}% "
              f"session_mean={st.mean(sm):+.2f}% session_t={t:.2f} overnight={st.mean(ov) if ov else 0:+.2f}% "
              f"TP={sum(1 for r in sub if r['exit_reason']=='TP')} SL={sum(1 for r in sub if r['exit_reason']=='SL')}")


def main() -> int:
    args = sys.argv[1:]
    cmd = args[0] if args else "settle"
    if cmd == "backfill":
        mx = int(args[args.index("--max") + 1]) if "--max" in args else None
        backfill(mx); settle(); report()
    elif cmd == "live":
        live(); settle()
    elif cmd == "settle":
        settle(); report()
    elif cmd == "report":
        report()
    else:
        print("사용: us_panic_close_shadow.py [backfill [--max N]|live|settle|report]"); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
