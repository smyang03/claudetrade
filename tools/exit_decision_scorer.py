from __future__ import annotations

"""청산 판단 outcome 채점기 (A: per-decision 단기, read-only).

hold advisor의 HOLD/SELL 재량 결정이 "옳았나"를 결정시점(ts) 앵커 기준 전방 가격경로로 채점한다.
대칭 반사실: 같은 가격경로 위에서 HOLD는 오르면 옳음(+fwd_ret), SELL은 내리면 옳음(-fwd_ret).
이렇게 해야 "SELL=TP실현은 채점, HOLD는 영영 미채점"이던 비대칭(실측 outcome 채움 HOLD 0%)이 풀린다.

설계 확정 사항(검토 반영):
- 강제 가드(exit_driver loss_cap/hard_stop)는 *판단*이 아니라 가드 발화 → 제외(AVOID 버킷 착시 방지).
- 1차 집계축은 decision_stage(잘 채워짐), driver는 83% NULL이라 보조.
- 단기(30/60m)만: cooldown 120분이라 리뷰 간 창이 ~독립. +1~3d 멀티데이는 별도(per-position, mark_closed 원장 필요)로 분리.
- 벤치마크(QQQ/^KS11) 차감으로 베타 제거.

read-only: hold advisor 로그(JSONL) + yfinance만 읽고, 결과는 격리 DB(data/analysis/exit_decision_scoring.db)에
저장한다. 라이브 로그/원장 무수정. prior/brain 자동 반영 없음(측정만).
"""

import argparse
import glob
import json
import sqlite3
import statistics
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG_GLOB = str(ROOT / "logs" / "hold_advisor" / "decisions_*.jsonl")
OUT_DIR = ROOT / "data" / "analysis"
OUT_DB = OUT_DIR / "exit_decision_scoring.db"

# 강제 가드 — 판단이 아니라 자동 발화. 채점 제외.
GUARD_DRIVERS = {"loss_cap", "hard_stop"}
BENCH = {"US": "QQQ", "KR": "^KS11"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS exit_decision_fwd (
    decision_key TEXT PRIMARY KEY,
    ts TEXT, market TEXT, ticker TEXT, yf_symbol TEXT,
    decision TEXT, decision_stage TEXT, exit_driver TEXT,
    confidence REAL, regime TEXT, anchor_price REAL, currency TEXT,
    fwd_ret_m30 REAL, fwd_ret_m60 REAL,
    bench_ret_m30 REAL, bench_ret_m60 REAL,
    gain_m30 REAL, gain_m60 REAL,
    excess_m30 REAL, excess_m60 REAL,
    bars_m60 INTEGER, source TEXT, synced_at TEXT
)
"""


KST = timezone(timedelta(hours=9))


def decision_gain(decision: str, fwd_ret: float | None) -> float | None:
    """대칭 반사실 점수. HOLD는 올랐으면(+ret) 옳음, SELL은 내렸으면(-ret) 옳음."""
    if fwd_ret is None:
        return None
    return (1.0 if str(decision).upper() == "HOLD" else -1.0) * float(fwd_ret)


def parse_utc(s: str) -> datetime:
    # hold advisor 로그 ts는 datetime.now()(naive 로컬=KST 운영머신) → naive는 KST로 해석해 UTC 변환.
    # UTC로 오해하면 9시간 어긋나 yfinance UTC 바와 안 맞아 전부 no_bars가 된다.
    dt = datetime.fromisoformat(str(s))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(timezone.utc)


def _market_of(ticker: str, rec: dict) -> str:
    m = str(rec.get("market", "") or "").upper()
    if m in ("US", "KR"):
        return m
    t = str(ticker or "")
    return "KR" if t.isdigit() else "US"


def _confidence(rec: dict) -> float | None:
    votes = rec.get("votes")
    if isinstance(votes, dict) and votes:
        vals = [float(v.get("confidence")) for v in votes.values()
                if isinstance(v, dict) and v.get("confidence") is not None]
        if vals:
            return round(statistics.mean(vals), 3)
    tri = rec.get("triage")
    if isinstance(tri, dict) and tri.get("confidence") is not None:
        try:
            return round(float(tri["confidence"]), 3)
        except (TypeError, ValueError):
            return None
    return None


def _regime(rec: dict) -> str:
    for src_key in ("advisor_context_v2", "pathb_revenue_path_context"):
        ctx = rec.get(src_key)
        if isinstance(ctx, dict):
            for k in ("regime", "market_regime", "consensus_mode", "mode"):
                v = str(ctx.get(k, "") or "").strip()
                if v:
                    return v.lower()
    return "unknown"


def _series_1d(df, col):
    s = df[col]
    if hasattr(s, "columns"):
        s = s.iloc[:, 0]
    return s


def _last_close(idx, close, start: datetime, end: datetime):
    mask = (idx >= start) & (idx <= end)
    n = int(mask.sum())
    if n == 0:
        return None, 0
    return float(close[mask][-1]), n


def _first_close(idx, close, start: datetime, end: datetime):
    mask = (idx >= start) & (idx <= end)
    if int(mask.sum()) == 0:
        return None
    return float(close[mask][0])


def _load_decisions(market: str) -> list[dict]:
    out = []
    for f in sorted(glob.glob(LOG_GLOB)):
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            decision = str(r.get("decision", "") or "").upper()
            if decision not in ("HOLD", "SELL"):
                continue
            ticker = str(r.get("ticker", "") or "").strip()
            anchor = float(r.get("current") or 0)
            ts = str(r.get("ts") or "")
            if not ticker or anchor <= 0 or not ts:
                continue
            mk = _market_of(ticker, r)
            if market != "ALL" and mk != market:
                continue
            tri = r.get("triage") if isinstance(r.get("triage"), dict) else {}
            driver = str(tri.get("exit_driver", "") or "")
            if driver in GUARD_DRIVERS:
                continue  # 강제 가드 제외(판단 아님)
            out.append({
                "decision_key": f"{mk}|{ticker}|{ts}",
                "ts": ts, "market": mk, "ticker": ticker, "decision": decision,
                "decision_stage": str(r.get("decision_stage", "") or "") or "UNKNOWN",
                "exit_driver": driver or None,
                "confidence": _confidence(r),
                "regime": _regime(r),
                "anchor": anchor,
                "currency": str(r.get("price_currency", "") or "") or ("USD" if mk == "US" else "KRW"),
            })
    return out


def _min_5m_start():
    # yfinance 5분봉은 최근 ~60일만 제공. 시작일을 now-59d로 clamp해야 요청 전체가 거부되지 않는다.
    # 60일 이전 결정은 그 창에 바가 없어 no_bars로 빠진다(문서화된 한계).
    return (datetime.now(timezone.utc) - timedelta(days=59)).date()


def _yf_symbols(market: str, ticker: str) -> list[str]:
    return [ticker] if market == "US" else [f"{ticker}.KS", f"{ticker}.KQ"]


def _fetch(yf, symbol: str, start, end, interval: str):
    try:
        d = yf.download(symbol, start=start, end=end, interval=interval,
                        progress=False, auto_adjust=False)
    except Exception:
        return None
    if d is None or len(d) == 0:
        return None
    idx = d.index
    try:
        idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    except Exception:
        pass
    return idx, _series_1d(d, "Close").values


def _bench_ret(bench_cache, market: str, ts: datetime, minutes: int):
    data = bench_cache.get(market)
    if not data:
        return None
    idx, close = data
    a = _first_close(idx, close, ts, ts + timedelta(minutes=minutes))
    last, _ = _last_close(idx, close, ts, ts + timedelta(minutes=minutes))
    if a is None or last is None or a <= 0:
        return None
    return (last / a - 1.0) * 100.0


def main() -> int:
    ap = argparse.ArgumentParser(description="청산 판단 outcome 채점기 A(per-decision 단기, read-only)")
    ap.add_argument("--market", default="ALL", choices=["ALL", "US", "KR"])
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--limit", type=int, default=0, help="종목 수 제한(테스트용)")
    ap.add_argument("--sleep", type=float, default=0.25)
    args = ap.parse_args()

    import yfinance as yf

    decisions = _load_decisions(args.market)
    if not decisions:
        print("채점할 재량 청산 결정이 없다(가드 제외 후 0).")
        return 0

    by_tk: dict[tuple, list] = defaultdict(list)
    for d in decisions:
        by_tk[(d["market"], d["ticker"])].append(d)
    # 최신 활동 종목 우선(5분봉 60일 한계 — 오래된 종목은 어차피 no_bars). --limit이 도달가능분을 잡게.
    items = sorted(by_tk.items(), key=lambda kv: max(p["ts"] for p in kv[1]), reverse=True)
    if args.limit:
        items = items[: args.limit]

    # 벤치마크 바 1회 페치(시장별, 전체 기간) — 베타 차감용. 5분봉 60일 한계로 시작일 clamp.
    min_start = _min_5m_start()
    all_ts = [parse_utc(d["ts"]) for d in decisions]
    bspan_s = max((min(all_ts) - timedelta(days=1)).date(), min_start)
    bspan_e = (max(all_ts) + timedelta(days=2)).date()
    bench_cache: dict[str, tuple] = {}
    markets = {m for (m, _t) in (k for k, _ in items)}
    for mk in markets:
        got = _fetch(yf, BENCH[mk], bspan_s, bspan_e, args.interval)
        if got:
            bench_cache[mk] = got
        time.sleep(args.sleep)

    out = []
    for (market, ticker), pl in items:
        starts = [parse_utc(p["ts"]) for p in pl]
        s = max((min(starts) - timedelta(days=1)).date(), min_start)
        e = (max(starts) + timedelta(days=2)).date()
        got = None
        sym = None
        for c in _yf_symbols(market, ticker):
            got = _fetch(yf, c, s, e, args.interval)
            if got:
                sym = c
                break
        if not got:
            for p in pl:
                out.append((p, sym, "no_data", {}))
            continue
        idx, close = got
        for p in pl:
            t = parse_utc(p["ts"])
            anchor = float(p["anchor"])
            res = {}
            ok = False
            for name, mins in (("m30", 30), ("m60", 60)):
                last, n = _last_close(idx, close, t, t + timedelta(minutes=mins))
                if last is None:
                    res[name] = (None, 0, None)
                    continue
                ok = True
                stock_ret = (last / anchor - 1.0) * 100.0
                bench = _bench_ret(bench_cache, market, t, mins)
                res[name] = (stock_ret, n, bench)
            out.append((p, sym, "yfinance_est" if ok else "no_bars", res))
        time.sleep(args.sleep)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    w = sqlite3.connect(str(OUT_DB), timeout=30)
    try:
        w.executescript(SCHEMA)
        for p, sym, src, res in out:
            def metrics(name):
                stock_ret, n, bench = res.get(name, (None, 0, None))
                if stock_ret is None:
                    return None, bench, None, None, n
                g = decision_gain(p["decision"], stock_ret)
                ex = None if bench is None else decision_gain(p["decision"], stock_ret - bench)
                return stock_ret, bench, g, ex, n

            r30, b30, g30, e30, n30 = metrics("m30")
            r60, b60, g60, e60, n60 = metrics("m60")
            w.execute(
                "INSERT OR REPLACE INTO exit_decision_fwd VALUES ("
                "?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    p["decision_key"], p["ts"], p["market"], p["ticker"], sym,
                    p["decision"], p["decision_stage"], p["exit_driver"],
                    p["confidence"], p["regime"], p["anchor"], p["currency"],
                    r30, r60, b30, b60, g30, g60, e30, e60, n60, src, now,
                ),
            )
        w.commit()
    finally:
        w.close()

    _report(out)
    return 0


def _report(out: list) -> None:
    scored = [(p, res) for (p, sym, src, res) in out
              if src == "yfinance_est" and res.get("m60", (None,))[0] is not None]
    print(f"\n=== 청산 판단 채점 (per-decision 단기, 가드 제외) ===")
    print(f"채점 결정 {len(scored)} / 전체 로드 {len(out)} (no_data/no_bars 제외)")
    if not scored:
        return

    def gain60(p, res):
        return decision_gain(p["decision"], res["m60"][0])

    def excess60(p, res):
        sr, _n, bench = res["m60"]
        if sr is None or bench is None:
            return None
        return decision_gain(p["decision"], sr - bench)

    # 비독립 경고: 유효 표본 = distinct (ticker,일)
    units = {(p["ticker"], p["ts"][:10]) for p, _ in scored}
    print(f"⚠ 비독립: per-decision {len(scored)}건 = distinct (종목,일) {len(units)}유닛 (반복 리뷰 포함)")

    def agg(rows, label):
        gains = [gain60(p, r) for p, r in rows if gain60(p, r) is not None]
        exs = [excess60(p, r) for p, r in rows if excess60(p, r) is not None]
        if not gains:
            return
        win = sum(1 for g in gains if g > 0) / len(gains) * 100
        gm = statistics.mean(gains)
        em = statistics.mean(exs) if exs else float("nan")
        print(f"  {label:<34} n={len(gains):<4} gain60 평균 {gm:+.2f}% | excess {em:+.2f}%p | 승률 {win:.0f}%")

    print("\n[decision]")
    for dec in ("HOLD", "SELL"):
        agg([(p, r) for p, r in scored if p["decision"] == dec], dec)
    print("\n[decision × stage]")
    keyset = sorted({(p["decision"], p["decision_stage"]) for p, _ in scored})
    for dec, stg in keyset:
        rows = [(p, r) for p, r in scored if p["decision"] == dec and p["decision_stage"] == stg]
        if len(rows) >= 3:
            agg(rows, f"{dec} / {stg}")
    print("\n해석: gain>0 = 옳은 판단(HOLD는 올랐다/SELL은 내렸다). excess<0이면 베타 빼면 손해.")
    print("주의: 단기 30/60m·yfinance 5분봉·in-sample·소표본 — 신호 약하면 더 쌓고 판단.")


if __name__ == "__main__":
    raise SystemExit(main())
