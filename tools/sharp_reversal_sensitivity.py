#!/usr/bin/env python3
"""sharp_reversal block 민감도 누적 측정 (read-only).

MARKET_SHARP_REVERSAL_BLOCK은 시장 지수가 세션 고점 대비 급반전할 때 신규 진입을 막는
시장 전체 게이트(enforce)다. "막은 게 득(손실 회피)인지 실(기회 손실)인지"를 두 층위로 본다:

  ① 지수 층위: 블록 ON 구간(감지→해제) 동안 지수가 회복(delta>0 = 기회손실 방향)했는지,
     추가 하락(delta<0 = 손실 회피)했는지. 블록은 시장 전체 게이트이므로 지수가 1차 프록시.
  ② 종목 층위: 블록 로그에 찍힌 차단 종목(date,ticker)을 ticker_selection_log의
     forward_1d/3d/5d·max_runup/drawdown과 매칭. forward가 +면 그 종목이 이후 올랐다는
     뜻(막은 게 기회손실), -면 손실 회피.

로그(logs/system/live_trading_*.log)와 ticker_selection_log.db를 읽기만 한다 —
주문·상태·brain 변경 없음. blocked_reason에는 SHARP_REVERSAL이 기록되지 않으므로
(scope=market 게이트) 로그의 종목코드로 매칭한다.
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import sys
from statistics import mean

_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_DETECT = re.compile(r"\[시장 급반전 감지\]\s+(KR|US)\s+지수\s+([+-]?\d+\.\d+)%.*?mode=(\w+)")
_CLEAR = re.compile(r"\[시장 급반전 해제\]\s+(KR|US)\s+지수\s+([+-]?\d+\.\d+)%")
_BLOCK = re.compile(r"MARKET_SHARP_REVERSAL_BLOCK\s+(KR|US)\s+(\S+)")


def _hhmm_to_min(ts: str) -> int:
    # 'YYYY-MM-DD HH:MM:SS' → 분 단위(세션 내 경과 측정용)
    hh = int(ts[11:13])
    mm = int(ts[14:16])
    return hh * 60 + mm


def parse_logs(logs_dir: str, since: str, market_filter: str):
    """로그에서 감지/해제/차단 이벤트를 시간순으로 추출."""
    events = []  # dict(ts,date,kind,market,value,mode,ticker)
    paths = sorted(glob.glob(os.path.join(logs_dir, "live_trading_*.log")))
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    tm = _TS.match(line)
                    if not tm:
                        continue
                    ts = tm.group(1)
                    date = ts[:10]
                    if since and date < since:
                        continue
                    d = _DETECT.search(line)
                    if d:
                        mk = d.group(1)
                        if market_filter in ("ALL", mk):
                            events.append({"ts": ts, "date": date, "kind": "detect",
                                           "market": mk, "value": float(d.group(2)),
                                           "mode": d.group(3), "ticker": None})
                        continue
                    c = _CLEAR.search(line)
                    if c:
                        mk = c.group(1)
                        if market_filter in ("ALL", mk):
                            events.append({"ts": ts, "date": date, "kind": "clear",
                                           "market": mk, "value": float(c.group(2)),
                                           "mode": None, "ticker": None})
                        continue
                    b = _BLOCK.search(line)
                    if b:
                        mk = b.group(1)
                        if market_filter in ("ALL", mk):
                            events.append({"ts": ts, "date": date, "kind": "block",
                                           "market": mk, "value": None,
                                           "mode": None, "ticker": b.group(2)})
        except Exception as exc:  # pragma: no cover
            print(f"[warn] log read fail {path}: {exc}", file=sys.stderr)
    events.sort(key=lambda e: e["ts"])
    return events


def build_on_intervals(events):
    """(date,market)별로 감지→해제 ON 구간을 페어링. 해제 없이 마감하면 open 구간."""
    by_sess = {}
    for e in events:
        by_sess.setdefault((e["date"], e["market"]), []).append(e)

    intervals = []  # dict(date,market,start,end,detect_val,clear_val,delta_pp,dur_min,closed,mode)
    for (date, market), evs in sorted(by_sess.items()):
        cur = None  # 진행 중 ON 구간
        for e in evs:
            if e["kind"] == "detect":
                if cur is None:
                    cur = {"date": date, "market": market, "start": e["ts"],
                           "detect_val": e["value"], "mode": e["mode"]}
                # ON 중 재감지는 같은 구간 유지(갱신 안 함 — 최초 감지값 보존)
            elif e["kind"] == "clear" and cur is not None:
                cur["end"] = e["ts"]
                cur["clear_val"] = e["value"]
                cur["delta_pp"] = round(e["value"] - cur["detect_val"], 4)
                cur["dur_min"] = _hhmm_to_min(e["ts"]) - _hhmm_to_min(cur["start"])
                cur["closed"] = True
                intervals.append(cur)
                cur = None
        if cur is not None:
            # 해제 없이 마감(또는 로그 종료) — open 구간, delta 미측정
            cur["end"] = None
            cur["clear_val"] = None
            cur["delta_pp"] = None
            cur["dur_min"] = None
            cur["closed"] = False
            intervals.append(cur)
    return intervals


def match_blocked_forward(events, db_path, market_filter):
    """차단된 (date,market,ticker)를 ticker_selection_log의 forward와 매칭."""
    blocked = {}  # (date,market,ticker) → count
    for e in events:
        if e["kind"] == "block" and e["ticker"] and e["ticker"] != "*":
            key = (e["date"], e["market"], e["ticker"])
            blocked[key] = blocked.get(key, 0) + 1

    rows = []
    if not blocked or not os.path.exists(db_path):
        return rows, blocked

    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    con.execute("PRAGMA busy_timeout=4000")
    try:
        for (date, market, ticker), n in sorted(blocked.items()):
            cur = con.execute(
                "SELECT avg(forward_1d), avg(forward_3d), avg(forward_5d), "
                "avg(max_runup_3d), avg(max_drawdown_3d), avg(change_pct), max(traded) "
                "FROM ticker_selection_log WHERE date=? AND market=? AND ticker=?",
                (date, market, ticker),
            ).fetchone()
            f1, f3, f5, runup3, dd3, chg, traded = cur if cur else (None,) * 7
            rows.append({
                "date": date, "market": market, "ticker": ticker, "block_log_count": n,
                "forward_1d": f1, "forward_3d": f3, "forward_5d": f5,
                "max_runup_3d": runup3, "max_drawdown_3d": dd3,
                "change_pct_at_select": chg, "traded": traded,
                "matched_in_selection_log": f1 is not None or f3 is not None,
            })
    finally:
        con.close()
    return rows, blocked


def summarize(intervals, blocked_rows):
    closed = [iv for iv in intervals if iv["closed"]]
    deltas = [iv["delta_pp"] for iv in closed if iv["delta_pp"] is not None]
    durs = [iv["dur_min"] for iv in closed if iv["dur_min"] is not None]

    idx_summary = {
        "on_intervals_total": len(intervals),
        "on_intervals_closed": len(closed),
        "on_intervals_open_at_close": len(intervals) - len(closed),
        "mean_index_delta_pp": round(mean(deltas), 4) if deltas else None,
        "recovery_rate": round(sum(1 for d in deltas if d > 0) / len(deltas), 3) if deltas else None,
        "mean_on_duration_min": round(mean(durs), 1) if durs else None,
        "note": "delta_pp>0 = 블록 ON 동안 지수 회복(진입 막은 게 기회손실 방향), <0 = 추가하락(손실 회피)",
    }

    matched = [r for r in blocked_rows if r["matched_in_selection_log"]]
    f1 = [r["forward_1d"] for r in matched if r["forward_1d"] is not None]
    f3 = [r["forward_3d"] for r in matched if r["forward_3d"] is not None]
    tkr_summary = {
        "blocked_distinct_tickers": len(blocked_rows),
        "matched_in_selection_log": len(matched),
        "mean_forward_1d": round(mean(f1), 4) if f1 else None,
        "mean_forward_3d": round(mean(f3), 4) if f3 else None,
        "forward_3d_positive_rate": round(sum(1 for x in f3 if x > 0) / len(f3), 3) if f3 else None,
        "note": "forward_3d>0 = 차단 종목이 이후 상승(막은 게 기회손실), <0 = 하락(손실 회피)",
    }
    return idx_summary, tkr_summary


def main():
    ap = argparse.ArgumentParser(description="sharp_reversal block 민감도 누적 측정 (read-only)")
    ap.add_argument("--logs-dir", default="logs/system")
    ap.add_argument("--db", default="data/ticker_selection_log.db")
    ap.add_argument("--market", default="KR", choices=["KR", "US", "ALL"])
    ap.add_argument("--since", default="", help="YYYY-MM-DD 이후만 (기본 전체)")
    ap.add_argument("--detail", action="store_true", help="구간·종목 상세 포함")
    args = ap.parse_args()

    events = parse_logs(args.logs_dir, args.since, args.market)
    intervals = build_on_intervals(events)
    blocked_rows, blocked_map = match_blocked_forward(events, args.db, args.market)
    idx_summary, tkr_summary = summarize(intervals, blocked_rows)

    sessions = sorted({(e["date"], e["market"]) for e in events if e["kind"] == "detect"})
    out = {
        "scope": {"market": args.market, "since": args.since or "all", "logs_dir": args.logs_dir},
        "sessions_with_sharp_reversal": [f"{d} {m}" for d, m in sessions],
        "block_log_events_total": sum(blocked_map.values()),
        "index_level": idx_summary,
        "ticker_level": tkr_summary,
    }
    if args.detail:
        out["intervals"] = intervals
        out["blocked_tickers"] = blocked_rows
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
