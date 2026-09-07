#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KR 개장 직후 매물 소진 스냅샷 수집기 (2026-09-07, 운영자 "국면 말고 매물 없는 애를 골라라").

일봉 특성으로는 손실 종목을 못 거른다는 것이 전반기 학습→후반기 검증에서 확인됐다(loser_exclusion_study). 매물 소진은 장중
정보에만 있다. 이 수집기는 전일 급락 풀(≤−5%, 거래대금 ≥20억, discovery_pools xkr_fallen3) 전 종목에 대해 개장 후
09:05 / 09:10 / 09:15 / 09:20 / 09:30 시점의 KIS 현재가·체결강도·호가 총잔량을 박제한다. 진입 없음, 읽기 전용.
원장: data/shadow/kr_open_flow.jsonl — 한 행 = (세션, 종목, 시각) 멱등. virtual_books.attach_open_flow가 16:20에 가상 북 meta.flow로 결합한다.
사용: python tools/kr_open_flow_collector.py --loop            # schtask claudetrade_kr_open_flow 09:02 주중
      python tools/kr_open_flow_collector.py --once [--limit 5] # 배관 점검(장외엔 마지막 체결값)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env.live", override=False)

LEDGER = ROOT / "data" / "shadow" / "kr_open_flow.jsonl"
HEARTBEAT = ROOT / "state" / "kr_open_flow_heartbeat.json"
KST = timezone(timedelta(hours=9))
SNAPS = ("09:05", "09:10", "09:15", "09:20", "09:30")
POOL_CHG_LE = -5.0


def now_kst() -> datetime:
    return datetime.now(KST)


def universe_for_today(today: str) -> list[dict]:
    """전일(마지막 완결 세션) 급락 풀. discovery_pools의 KR 세션 키 = 신호일이라 today보다 작은 마지막 키를 쓴다."""
    import discovery_pools as dp
    sess = dp.discovery_sessions("KR").get("xkr_fallen3", {})
    keys = sorted(k for k in sess if k < today)
    if not keys:
        return []
    return [c for c in sess[keys[-1]] if c.get("chg") is not None and c["chg"] <= POOL_CHG_LE]


def fetch_snapshot(ticker: str, token: str) -> dict:
    """현재가·체결강도·호가 총잔량. 실패 필드는 None(행은 남긴다)."""
    import kis_api as k
    out: dict = {"ticker": ticker}
    try:
        p = k._get_price_kr(ticker, token, allow_fallback=False)
        out.update({"price": p.get("price"), "open": p.get("open"), "high": p.get("high"), "low": p.get("low"), "volume": p.get("volume"),
                    "change_rate": p.get("change_rate")})
    except Exception as exc:
        out["price_error"] = str(exc)[:80]
    try:
        payload = k._kis_market_data_get(token=token, market="KR", path="/uapi/domestic-stock/v1/quotations/inquire-ccnl",
                                         tr_id="FHKST01010300", params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
                                         timeout_env="KR_ORDERBOOK_TIMEOUT_SEC", retry_env="KR_ORDERBOOK_MAX_RETRIES",
                                         backoff_env="KR_ORDERBOOK_RETRY_BACKOFF_SEC")
        rows = payload.get("output") or []
        if rows:
            r0 = rows[0]
            out["strength"] = float(r0.get("tday_rltv") or 0) or None      # 체결강도(당일 매수체결/매도체결)
            out["last_trade_hhmmss"] = str(r0.get("stck_cntg_hour") or "")
    except Exception as exc:
        out["ccnl_error"] = str(exc)[:80]
    try:
        payload = k._kis_market_data_get(token=token, market="KR", path="/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
                                         tr_id="FHKST01010200", params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
                                         timeout_env="KR_ORDERBOOK_TIMEOUT_SEC", retry_env="KR_ORDERBOOK_MAX_RETRIES",
                                         backoff_env="KR_ORDERBOOK_RETRY_BACKOFF_SEC")
        o1 = payload.get("output1") or {}
        bid_tot = float(o1.get("total_bidp_rsqn") or 0); ask_tot = float(o1.get("total_askp_rsqn") or 0)
        bid1 = float(o1.get("bidp1") or 0); ask1 = float(o1.get("askp1") or 0)
        out.update({"bid_total": bid_tot or None, "ask_total": ask_tot or None,
                    "imbalance": ((bid_tot - ask_tot) / (bid_tot + ask_tot)) if (bid_tot + ask_tot) > 0 else None,
                    "spread_pct": ((ask1 - bid1) / ((ask1 + bid1) / 2) * 100.0) if ask1 > 0 and bid1 > 0 else None})
    except Exception as exc:
        out["orderbook_error"] = str(exc)[:80]
    return out


def flow_features(prev_close: float | None, snap: dict) -> dict:
    """스냅샷 → 해석 가능한 특성. 갭, 시가 대비 진행, 체결강도, 호가 불균형, 거래량 진행(거래대금 원)."""
    f = {}
    px, op = snap.get("price"), snap.get("open")
    if prev_close and op:
        f["gap_pct"] = round((op / prev_close - 1.0) * 100.0, 3)
    if op and px:
        f["ret_from_open_pct"] = round((px / op - 1.0) * 100.0, 3)
    if prev_close and px:
        f["ret_vs_prev_close_pct"] = round((px / prev_close - 1.0) * 100.0, 3)
    hi, lo = snap.get("high"), snap.get("low")
    if hi and lo and px and hi > lo:
        f["pos_in_range"] = round((px - lo) / (hi - lo), 3)
    f["strength"] = snap.get("strength")
    f["imbalance"] = snap.get("imbalance")
    f["spread_pct"] = snap.get("spread_pct")
    if px and snap.get("volume"):
        f["dvol_so_far_eok"] = round(px * float(snap["volume"]) / 1e8, 2)
    return f


def append_rows(rows: list[dict]) -> int:
    seen = set()
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line); seen.add((r["session_date"], r["ticker"], r["snap"]))
            except (ValueError, KeyError):
                continue
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with LEDGER.open("a", encoding="utf-8") as fh:
        for r in rows:
            if (r["session_date"], r["ticker"], r["snap"]) in seen:
                continue
            fh.write(json.dumps(r, ensure_ascii=False) + "\n"); n += 1
    return n


def _heartbeat(extra: dict) -> None:
    try:
        HEARTBEAT.parent.mkdir(parents=True, exist_ok=True)
        HEARTBEAT.write_text(json.dumps({"written_at": now_kst().isoformat(timespec="seconds"), **extra}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def take_snapshot(universe: list[dict], snap: str, session_date: str, token: str, *, fetch=fetch_snapshot, sleep: float = 0.08) -> list[dict]:
    rows = []
    for c in universe:
        s = fetch(c["ticker"], token)
        rows.append({"session_date": session_date, "signal_date": c.get("signal_date"), "ticker": c["ticker"], "snap": snap,
                     "observed_at": now_kst().isoformat(timespec="seconds"), "prev_close": c.get("price"), "prev_chg": c.get("chg"),
                     "prev_dvol_eok": c.get("dvol"), "raw": {k: v for k, v in s.items() if k != "ticker"},
                     "flow": flow_features(c.get("price"), s)})
        time.sleep(sleep)
    return rows


def loop() -> int:
    import kis_api as k
    today = now_kst().strftime("%Y-%m-%d")
    if now_kst().weekday() >= 5:
        print("[KR-FLOW] 주말 — 종료"); return 0
    uni = universe_for_today(today)
    print(f"[KR-FLOW] {today} 전일 급락 풀 {len(uni)}종목, 스냅 {SNAPS}", flush=True)
    _heartbeat({"session_date": today, "universe_n": len(uni), "done": []})
    if not uni:
        return 0
    token = k.get_access_token(market="KR")
    done: list[str] = []
    for snap in SNAPS:
        target = now_kst().replace(hour=int(snap[:2]), minute=int(snap[3:]), second=0, microsecond=0)
        while now_kst() < target:
            time.sleep(5)
        if now_kst() > target + timedelta(minutes=4):
            print(f"[KR-FLOW] {snap} 지각(현재 {now_kst().strftime('%H:%M')}) — 건너뜀", flush=True); continue
        rows = take_snapshot(uni, snap, today, token)
        n = append_rows(rows)
        done.append(snap)
        ok = sum(1 for r in rows if r["raw"].get("price"))
        print(f"[KR-FLOW] {snap} {len(rows)}행 기록({n} 신규) · 시세 성공 {ok} · 체결강도 수신 {sum(1 for r in rows if r['raw'].get('strength'))}", flush=True)
        _heartbeat({"session_date": today, "universe_n": len(uni), "done": done})
    print("[KR-FLOW] 완료", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--limit", type=int, default=5)
    a = ap.parse_args()
    if a.loop:
        return loop()
    if a.once:
        import kis_api as k
        today = now_kst().strftime("%Y-%m-%d")
        uni = universe_for_today(today)[: a.limit]
        print(f"[KR-FLOW once] 풀 {len(uni)}종목 (limit {a.limit})")
        token = k.get_access_token(market="KR")
        for r in take_snapshot(uni, "test", today, token):
            print(" ", r["ticker"], "prev_chg", r["prev_chg"], "raw", {k: v for k, v in r["raw"].items() if not k.endswith("error")}, "err", [k for k in r["raw"] if k.endswith("error")], "flow", r["flow"])
        return 0
    ap.print_help(); return 1


if __name__ == "__main__":
    sys.exit(main())
