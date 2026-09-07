# -*- coding: utf-8 -*-
"""KR 매도 체결 흡수 쉐도우 (Codex N2, 2026-09-08) — 관측 틱 원장(kr_ws_ticks) → 09:05~09:15 흡수 판정 → 09:16 매도호가1 진입.

H0STCNT0 필드(KIS 스펙): 0 종목코드 1 체결시각 2 현재가 7 시가 10 매도호가1 11 매수호가1 12 체결량 13 누적거래량 15 매도체결건수 16 매수체결건수
18 체결강도 21 체결구분(1 매수·5 매도) 38 총매도호가잔량 39 총매수호가잔량.
흡수 조건(전부, 09:05:00~09:15:00 창): ① 매도 체결량 비중 ≥55% ② 창 마지막 가격 ≥ 창 첫 가격×0.995(비하락) ③ 총매수잔량 창 끝 ≥ 창 시작×0.9(재충전)
④ 창 끝 체결강도 <100 ⑤ 매수호가1 창 끝 ≥ 창 시작(호가 유지). 통과 → 09:16 이후 첫 틱 매도호가1(없으면 현재가) 진입, 계약 TP12/SL25/D7(일봉 정산).
원장 data/shadow/kr_absorption.jsonl (session / trade). 정산은 21:05 래퍼 `settle`. schtask claudetrade_kr_absorption 주중 09:25.
사용: python tools/kr_absorption_shadow.py [signal [--date YYYY-MM-DD] | settle]
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TICK_DIR = ROOT / "data" / "shadow" / "kr_ws_ticks"
OUT = ROOT / "data" / "shadow" / "kr_absorption.jsonl"
KR_DIR = ROOT / "data" / "price" / "kr"
W0, W1, ENTRY_FROM = "090500", "091500", "091600"
TP, SL, HOLD, COST = 12.0, -25.0, 7, 0.21
CONTRACT = "absorption_v1:TP12/SL25/D7/entry0916ask"
F = {"code": 0, "t": 1, "px": 2, "ask1": 10, "bid1": 11, "vol": 12, "acml": 13, "cttr": 18, "dvsn": 21, "tot_ask": 38, "tot_bid": 39}


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


def _append(row: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _f(fields: list[str], k: str) -> float | None:
    i = F[k]
    try:
        return float(fields[i]) if i < len(fields) and fields[i] != "" else None
    except ValueError:
        return None


def signal(day: str) -> int:
    rows = _jsonl(OUT)
    if any(r.get("kind") == "session" and r.get("date") == day for r in rows):
        print(f"[ABSORB] {day} 이미 기록"); return 0
    src = TICK_DIR / f"{day}.jsonl"
    ticks = _jsonl(src)
    if not ticks:
        _append({"kind": "session", "date": day, "n_ticks": 0, "n_tickers": 0, "n_pass": 0, "note": "틱 원장 없음(관측 구독 0 또는 봇 미가동)"})
        print(f"[ABSORB] {day} 틱 원장 없음"); return 0
    by: dict[str, list[list[str]]] = {}
    for r in ticks:
        f = str(r.get("raw", "")).split("^")
        if len(f) >= 14:
            by.setdefault(f[0], []).append(f)
    n_pass = 0
    for code, fs in by.items():
        fs.sort(key=lambda x: x[F["t"]])
        win = [f for f in fs if W0 <= f[F["t"]][:6] < W1]
        if len(win) < 20:
            continue
        sell = sum(_f(f, "vol") or 0 for f in win if (f[F["dvsn"]] if F["dvsn"] < len(f) else "") == "5")
        tot = sum(_f(f, "vol") or 0 for f in win)
        px0, px1 = _f(win[0], "px"), _f(win[-1], "px")
        tb0, tb1 = _f(win[0], "tot_bid"), _f(win[-1], "tot_bid")
        bid0, bid1 = _f(win[0], "bid1"), _f(win[-1], "bid1")
        cttr = _f(win[-1], "cttr")
        feat = {"sell_share": round(sell / tot, 3) if tot else None, "px_ret_win": round((px1 / px0 - 1) * 100, 3) if px0 and px1 else None,
                "tot_bid_ratio": round(tb1 / tb0, 3) if tb0 and tb1 else None, "bid1_kept": (bid1 is not None and bid0 is not None and bid1 >= bid0),
                "cttr_end": cttr, "n_ticks_win": len(win)}
        ok = (feat["sell_share"] is not None and feat["sell_share"] >= 0.55 and feat["px_ret_win"] is not None and feat["px_ret_win"] >= -0.5
              and feat["tot_bid_ratio"] is not None and feat["tot_bid_ratio"] >= 0.9 and cttr is not None and cttr < 100 and feat["bid1_kept"])
        _append({"kind": "observation", "date": day, "ticker": code, **feat, "pass": bool(ok)})
        if not ok:
            continue
        ent = next((f for f in fs if f[F["t"]][:6] >= ENTRY_FROM), None)
        if not ent:
            continue
        px = _f(ent, "ask1") or _f(ent, "px")
        if not px:
            continue
        _append({"kind": "trade", "date": day, "ticker": code, "contract": CONTRACT, "status": "OPEN", "entry_price": px, "entry_rule": "ask1_0916",
                 "entry_tick_t": ent[F["t"]], **feat, "opened_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        n_pass += 1
    _append({"kind": "session", "date": day, "n_ticks": len(ticks), "n_tickers": len(by), "n_pass": n_pass})
    print(f"[ABSORB] {day} 틱 {len(ticks)} 종목 {len(by)} 통과 {n_pass}")
    return n_pass


def settle() -> int:
    rows = _jsonl(OUT); n = 0
    for r in rows:
        if r.get("kind") != "trade" or r.get("status") != "OPEN":
            continue
        p = KR_DIR / f"kr_{r['ticker']}.csv"
        if not p.exists():
            continue
        bars = [x for x in csv.reader(p.open(encoding="utf-8-sig")) if x and x[0][:2] == "20"]
        dates = [x[0] for x in bars]
        if r["date"] not in dates:
            continue
        i = dates.index(r["date"]); entry = float(r["entry_price"]); reason = None; px = None; held = 0
        # 진입 당일 잔여(09:16~종가)는 당일 고저로 SL/TP 확인, 이후 D7까지 일봉
        for k in range(0, HOLD + 1):
            if i + k >= len(bars):
                break
            o, h, l, c = (float(bars[i + k][j]) for j in (1, 2, 3, 4)); held = k
            if k > 0 and o <= entry * (1 + SL / 100):
                reason, px = "SL", o; break
            if l <= entry * (1 + SL / 100):
                reason, px = "SL", entry * (1 + SL / 100); break
            if k > 0 and o >= entry * (1 + TP / 100):
                reason, px = "TP", o; break
            if h >= entry * (1 + TP / 100):
                reason, px = "TP", entry * (1 + TP / 100); break
            if k == HOLD:
                reason, px = "D_MAT", c
        if reason:
            r.update({"status": "CLOSED", "exit_reason": reason, "exit_price": round(px, 2), "held": held,
                      "net_pct": round((px / entry - 1) * 100 - COST, 3), "settled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}); n += 1
    if n:
        tmp = OUT.with_suffix(".tmp"); tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8"); tmp.replace(OUT)
    print(f"[ABSORB] 정산 {n}행")
    return n


def main() -> int:
    args = sys.argv[1:]; cmd = args[0] if args else "signal"
    if cmd == "signal":
        day = args[args.index("--date") + 1] if "--date" in args else date.today().isoformat()
        if date.fromisoformat(day).weekday() >= 5:
            return 0
        signal(day)
    elif cmd == "settle":
        settle()
    else:
        print("사용: kr_absorption_shadow.py [signal [--date D]|settle]"); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
