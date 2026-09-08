# -*- coding: utf-8 -*-
"""코어 ETF 북 쉐도우 — KR 상장 무헤지 5자산 월 1회 배분, 등가중(ew) vs 12-1 절대모멘텀(absmom) 두 arm (2026-09-09, 수익성 검토 §3 코어).

근거(연구 스크립트 tools/research/research_tsmom_kr_etf.py, 2007~2026 KRW 환산 프록시): 5자산 등가중 CAGR +12.9% maxDD −17.7% Sharpe 1.18 /
12-1 절대모멘텀 +14.0% −21.7% 1.09 / 12-1&200MA(TSMOM) +13.0% −21.4% 0.93. 등가중의 낙폭 방어는 위기 때 오르는 USDKRW(무헤지) 효과라
헤지형 ETF로는 성립하지 않는다 → 유니버스는 전부 무헤지. 이 층은 알파가 아니라 베타 배분이다. 쉐도우 목적 = 백테스트 대비 추적 확인(정수주·세금·괴리),
알파 게이트가 아니다. 판정 규약은 docs/reports/preregistration_core_etf_and_kq_panic_20260909.md.

가상 자본 2,600,000원(계좌 432만의 60%). 정수주만. 수수료 편도 0.015%. 매매차익 배당소득세 15.4%(069500 제외, 실현 이익에만) 반영.
모드
- backfill : 5종목 전부 13개월 이력이 있는 첫 달부터 지난달까지 월초(그 달 첫 거래일 종가) 리밸런스·월말 MTM. mode=backfill.
- run      : 21:05 래퍼. 캐시 갱신 → 최신 종가일 D. arm별 이번 달 리밸런스가 없으면 D 종가로 리밸런스(월초 종가 근사) → D MTM(멱등).
- report   : data/analysis/core_etf_book_report.json — 대시보드 카드 입력.
원장: data/shadow/core_etf_book.jsonl — kind rebalance / mtm, (arm, date, kind) 멱등. 실주문 경로 없음.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "shadow" / "core_etf_book.jsonl"
CACHE = ROOT / "data" / "analysis" / "core_etf_prices.csv"
REPORT = ROOT / "data" / "analysis" / "core_etf_book_report.json"
KST = timezone(timedelta(hours=9))
UNIVERSE = {"379810": "KODEX 미국나스닥100", "360750": "TIGER 미국S&P500", "411060": "ACE KRX금현물",
            "305080": "TIGER 미국채10년선물", "069500": "KODEX 200"}
TAX_FREE = {"069500"}          # 국내주식형 ETF 매매차익 비과세
BENCH = "069500"
CAPITAL = 2_600_000.0
FEE_SIDE = 0.00015
TAX = 0.154
ARMS = ("ew", "absmom")


# ── 순수 함수 (테스트 대상) ─────────────────────────────────────────────────
def month_key(d: str) -> str:
    return d[:7]


def month_end_closes(dates: list[str], closes: dict[str, dict[str, float]], before_month: str) -> list[tuple[str, dict[str, float]]]:
    """before_month(YYYY-MM) 이전 달들의 월말 종가 [(YYYY-MM, {tk: close}), ...] 오름차순."""
    out: dict[str, tuple[str, dict[str, float]]] = {}
    for d in dates:
        m = month_key(d)
        if m >= before_month:
            break
        out[m] = (d, closes[d])
    return [(m, out[m][1]) for m in sorted(out)]


def absmom_signal(mends: list[tuple[str, dict[str, float]]], tickers: list[str]) -> dict[str, bool]:
    """12-1 모멘텀: 직전 달을 건너뛴 12개월 수익률 > 0. mends는 결정 월 이전 월말 종가(오름차순). 13개 미만이면 전부 False."""
    if len(mends) < 13:
        return {t: False for t in tickers}
    recent = mends[-2][1]    # 결정 월 −2 월말 (직전 달 스킵)
    base = mends[-13][1]     # 결정 월 −13 월말
    out = {}
    for t in tickers:
        a, b = recent.get(t), base.get(t)
        out[t] = bool(a and b and a / b - 1.0 > 0.0)
    return out


def target_weights(arm: str, signal: dict[str, bool], tickers: list[str]) -> dict[str, float]:
    if arm == "ew":
        return {t: 1.0 / len(tickers) for t in tickers}
    passing = [t for t in tickers if signal.get(t)]
    return {t: (1.0 / len(passing) if t in passing else 0.0) for t in tickers}


def size_orders(nav: float, weights: dict[str, float], prices: dict[str, float], holdings: dict[str, dict]) -> tuple[dict[str, int], float]:
    """목표 정수주와 정수주 오차(Σ|목표비중−실제비중|, %)."""
    target_qty = {t: int(math.floor(nav * w / prices[t])) if prices.get(t) else 0 for t, w in weights.items()}
    err = 0.0
    for t, w in weights.items():
        actual = target_qty[t] * prices[t] / nav if nav > 0 else 0.0
        err += abs(w - actual)
    return target_qty, round(err * 100.0, 3)


def apply_orders(cash: float, holdings: dict[str, dict], target_qty: dict[str, int], prices: dict[str, float]) -> tuple[float, dict[str, dict], list[dict]]:
    """평균단가 기준 실현손익·세금·수수료 반영. holdings: {tk: {qty, avg_cost}}."""
    orders = []
    h = {t: dict(v) for t, v in holdings.items()}
    # 매도 먼저(현금 확보)
    for t, tq in target_qty.items():
        cur = h.get(t, {"qty": 0, "avg_cost": 0.0}); dq = tq - cur["qty"]
        if dq >= 0:
            continue
        px = prices[t]; value = -dq * px; fee = value * FEE_SIDE
        gain = (px - cur["avg_cost"]) * (-dq)
        tax = gain * TAX if (gain > 0 and t not in TAX_FREE) else 0.0
        cash += value - fee - tax
        cur["qty"] = tq
        h[t] = cur
        orders.append({"ticker": t, "qty_delta": dq, "price": px, "value": round(value), "fee": round(fee, 1), "tax": round(tax, 1), "realized": round(gain)})
    for t, tq in target_qty.items():
        cur = h.get(t, {"qty": 0, "avg_cost": 0.0}); dq = tq - cur["qty"]
        if dq <= 0:
            continue
        px = prices[t]; value = dq * px; fee = value * FEE_SIDE
        if value + fee > cash + 1e-6:
            dq = int(math.floor(cash / (px * (1 + FEE_SIDE)))); value = dq * px; fee = value * FEE_SIDE
            if dq <= 0:
                continue
        cash -= value + fee
        new_qty = cur["qty"] + dq
        cur["avg_cost"] = (cur["avg_cost"] * cur["qty"] + px * dq) / new_qty if new_qty else 0.0
        cur["qty"] = new_qty
        h[t] = cur
        orders.append({"ticker": t, "qty_delta": dq, "price": px, "value": round(value), "fee": round(fee, 1), "tax": 0.0, "realized": 0})
    return cash, h, orders


def nav_of(cash: float, holdings: dict[str, dict], prices: dict[str, float]) -> float:
    return cash + sum(v["qty"] * prices.get(t, 0.0) for t, v in holdings.items())


# ── 원장·캐시 ───────────────────────────────────────────────────────────────
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


def _append(row: dict) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_cache() -> dict[str, dict[str, float]]:
    if not CACHE.exists():
        return {}
    out: dict[str, dict[str, float]] = {}
    with CACHE.open(encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            out[r["date"]] = {k: float(v) for k, v in r.items() if k != "date" and v not in ("", None)}
    return out


def refresh_cache(force: bool = False) -> dict[str, dict[str, float]]:
    cache = _read_cache()
    today = datetime.now(KST).date()
    last = max(cache) if cache else None
    if cache and not force and last and last >= (today - timedelta(days=1)).isoformat():
        return cache
    try:
        import yfinance as yf
    except ImportError:
        print("[COREBOOK] yfinance 없음 — 캐시만 사용"); return cache
    start = "2018-01-01" if force or not cache else (datetime.fromisoformat(last) - timedelta(days=7)).date().isoformat()
    try:
        for tk in UNIVERSE:
            d = yf.download(f"{tk}.KS", start=start, auto_adjust=False, progress=False, threads=False)
            if hasattr(d.columns, "levels"):
                d.columns = d.columns.get_level_values(0)
            for ts, row in d.dropna().iterrows():
                ds = ts.date().isoformat()
                if ds >= today.isoformat():
                    continue
                cache.setdefault(ds, {})[tk] = float(row["Close"])
    except Exception as exc:  # noqa: BLE001
        print(f"[COREBOOK] yfinance 실패 {exc} — 캐시만 사용"); return cache
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    with CACHE.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh); w.writerow(["date"] + list(UNIVERSE))
        for d in sorted(cache):
            w.writerow([d] + [cache[d].get(t, "") for t in UNIVERSE])
    return cache


def _complete_dates(cache: dict[str, dict[str, float]]) -> list[str]:
    return sorted(d for d, c in cache.items() if all(c.get(t) for t in UNIVERSE))


def _state(rows: list[dict], arm: str) -> dict | None:
    rb = [r for r in rows if r.get("arm") == arm and r.get("kind") == "rebalance"]
    return rb[-1] if rb else None


def _rebalance(arm: str, d: str, dates: list[str], cache: dict, rows: list[dict], mode: str) -> dict:
    prev = _state(rows, arm)
    prices = cache[d]
    cash = prev["cash_after"] if prev else CAPITAL
    holdings = {t: dict(v) for t, v in (prev["holdings"] if prev else {}).items()}
    nav = nav_of(cash, holdings, prices)
    mends = month_end_closes(dates, cache, month_key(d))
    sig = absmom_signal(mends, list(UNIVERSE))
    w = target_weights(arm, sig, list(UNIVERSE))
    tq, err = size_orders(nav, w, prices, holdings)
    cash2, h2, orders = apply_orders(cash, holdings, tq, prices)
    row = {"kind": "rebalance", "mode": mode, "arm": arm, "date": d, "month": month_key(d), "nav_before": round(nav), "targets": {t: round(x, 3) for t, x in w.items()},
           "signal": sig, "orders": orders, "holdings": {t: {"qty": v["qty"], "avg_cost": round(v["avg_cost"], 2)} for t, v in h2.items() if v["qty"]},
           "cash_after": round(cash2, 2), "nav_after": round(nav_of(cash2, h2, prices)), "int_share_err_pct": err,
           "fees": round(sum(o["fee"] for o in orders), 1), "taxes": round(sum(o["tax"] for o in orders), 1),
           "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    rows.append(row); _append(row)
    return row


def _mtm(arm: str, d: str, cache: dict, rows: list[dict], mode: str, bench_qty: int, bench_cash: float) -> dict:
    st_ = _state(rows, arm)
    prices = cache[d]
    holdings = st_["holdings"] if st_ else {}
    cash = st_["cash_after"] if st_ else CAPITAL
    nav = nav_of(cash, {t: v for t, v in holdings.items()}, prices)
    row = {"kind": "mtm", "mode": mode, "arm": arm, "date": d, "nav": round(nav), "cash": round(cash), "ret_pct": round((nav / CAPITAL - 1) * 100, 3),
           "bench_nav": round(bench_cash + bench_qty * prices[BENCH]), "weights": {t: round(v["qty"] * prices[t] / nav, 3) for t, v in holdings.items()} if nav else {}}
    rows.append(row); _append(row)
    return row


def _bench(dates: list[str], cache: dict, start: str) -> tuple[int, float]:
    px = cache[start][BENCH]; q = int(math.floor(CAPITAL / px)); return q, CAPITAL - q * px


def backfill() -> int:
    cache = refresh_cache(force=not CACHE.exists())
    dates = _complete_dates(cache)
    rows = _load()
    if any(r.get("mode") == "backfill" for r in rows):
        print("[COREBOOK] backfill 이미 존재 — 스킵"); return 0
    # 첫 결정 월: 13개 월말 이력이 있는 첫 달
    months = sorted({month_key(d) for d in dates})
    first = next((m for m in months if len(month_end_closes(dates, cache, m)) >= 13), None)
    if not first:
        print("[COREBOOK] 이력 부족"); return 0
    this_month = month_key(datetime.now(KST).date().isoformat())
    start_day = next(d for d in dates if month_key(d) == first)
    bq, bc = _bench(dates, cache, start_day)
    n = 0
    for m in months:
        if m < first or m >= this_month:
            continue
        mdays = [d for d in dates if month_key(d) == m]
        for arm in ARMS:
            _rebalance(arm, mdays[0], dates, cache, rows, "backfill"); n += 1
            _mtm(arm, mdays[-1], cache, rows, "backfill", bq, bc)
    print(f"[COREBOOK] backfill 리밸런스 {n}행 (첫 달 {first}, 벤치 {BENCH} {bq}주)"); return n


def run() -> None:
    cache = refresh_cache()
    dates = _complete_dates(cache)
    if not dates:
        print("[COREBOOK] 가격 없음"); return
    rows = _load()
    if not any(r.get("kind") == "rebalance" for r in rows):
        backfill(); rows = _load()
    d = dates[-1]
    first_rb = next((r for r in rows if r.get("kind") == "rebalance"), None)
    bq, bc = _bench(dates, cache, first_rb["date"] if first_rb else d)
    for arm in ARMS:
        st_ = _state(rows, arm)
        if not st_ or st_["month"] < month_key(d):
            r = _rebalance(arm, d, dates, cache, rows, "live")
            print(f"[COREBOOK] {arm} 리밸런스 {d} nav {r['nav_after']:,} 주문 {len(r['orders'])} 정수주 오차 {r['int_share_err_pct']}%")
        if not any(r.get("kind") == "mtm" and r.get("arm") == arm and r.get("date") == d for r in rows):
            m = _mtm(arm, d, cache, rows, "live", bq, bc)
            print(f"[COREBOOK] {arm} MTM {d} nav {m['nav']:,} ({m['ret_pct']:+.2f}%) bench {m['bench_nav']:,}")


def report() -> dict:
    rows = _load()
    out: dict = {"generated_at": datetime.now(KST).isoformat(timespec="seconds"), "capital": CAPITAL, "universe": UNIVERSE, "arms": {}}
    for arm in ARMS:
        mt = [r for r in rows if r.get("kind") == "mtm" and r.get("arm") == arm]
        rb = [r for r in rows if r.get("kind") == "rebalance" and r.get("arm") == arm]
        if not mt:
            out["arms"][arm] = {"n": 0}; continue
        navs = [r["nav"] for r in mt]; peak = navs[0]; dd = 0.0
        for v in navs:
            peak = max(peak, v); dd = min(dd, v / peak - 1)
        last = mt[-1]
        out["arms"][arm] = {"start": rb[0]["date"] if rb else None, "last": last["date"], "nav": last["nav"], "ret_pct": last["ret_pct"],
                            "bench_ret_pct": round((last["bench_nav"] / CAPITAL - 1) * 100, 2), "max_dd_pct": round(dd * 100, 2),
                            "rebalances": len(rb), "live_rebalances": sum(1 for r in rb if r.get("mode") == "live"),
                            "last_targets": rb[-1]["targets"] if rb else None, "last_weights": last.get("weights"),
                            "int_share_err_pct": rb[-1]["int_share_err_pct"] if rb else None,
                            "fees_total": round(sum(r["fees"] for r in rb)), "taxes_total": round(sum(r["taxes"] for r in rb))}
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    for arm, a in out["arms"].items():
        if a.get("n") == 0:
            print(f"[COREBOOK] {arm}: 없음"); continue
        print(f"[COREBOOK] {arm}: {a['start']}~{a['last']} nav {a['nav']:,} ({a['ret_pct']:+.2f}%) bench {a['bench_ret_pct']:+.2f}% maxDD {a['max_dd_pct']}% "
              f"리밸 {a['rebalances']}(live {a['live_rebalances']}) 정수주 오차 {a['int_share_err_pct']}% 세금 {a['taxes_total']:,}")
    return out


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "backfill":
        backfill(); report()
    elif cmd == "run":
        run(); report()
    elif cmd == "report":
        report()
    else:
        print("사용: core_etf_book_shadow.py [backfill|run|report]"); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
