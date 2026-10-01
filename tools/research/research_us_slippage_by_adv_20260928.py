"""US 실체결 슬리피지 × 거래대금(ADV20) 버킷 실측 (2026-09-28, read-only)

목적: T1 US 어닝 규칙(I1)의 판정이 유동성 슬리피지 가정(ADV≥$20M +0.10% / $5~20M +0.30% / <$5M +0.60%,
수수료 0.44% 위)에 걸려 있다. 우리 라이브 US 체결(2026-04~09)로 실제 값을 잰다.

소스
- 주문/체결: data/v2_event_store.db lifecycle_events (runtime_mode='live', market='US') — 읽기 전용 URI.
  매수: ORDER_SENT(side=buy 또는 price_native) → FILLED(execution_id 기준 dedup).
  매도: ORDER_SENT(path_status=SELL_SENT) 시각·지정가, 체결가 = 브로커 기간손익(avg_sll_unpr / frst_bltn_exrt)
        — 같은 (거래일, 종목)에 우리 매도 이벤트가 1건일 때만. CLOSED.price는 주문가라 체결가로 쓰지 않는다.
- 기준가: Alpaca SIP (feed=sip) — 주문 전송 직전 NBBO 호가(bid/ask/mid)와 1분봉(raw, 04:00~20:00 ET, 정규장 시가).
  응답은 data/analysis/us_slippage_by_adv_20260928/alpaca_cache/ 에 캐시.
- ADV20: Alpaca 1Day raw 봉, 체결일 이전 20세션 평균 c×v (lookahead 없음). 가격 버킷 = 전일 종가.

정의(부호: +가 불리)
- slip_mid_bp  = side × (fill − mid_at_send) / mid_at_send × 1e4   ← 주 지표(유효 반스프레드 + 전송~체결 지연)
- slip_open_bp = side × (fill − 정규장 첫 1분봉 시가) / 시가 × 1e4  ← 감도(장중 드리프트 포함)
- aggr_bp      = side × (limit − mid_at_send) / mid × 1e4; 매수 limit ≥ ask 이면 marketable, 아니면 resting.
- qspread_bp   = (ask − bid)/mid × 1e4 (전송 시점 호가 스프레드)

T1 재평가: trades_I1.csv.gz에 버킷별 실측 매수+매도 슬리피지 평균을 수수료 위에 얹어 세션 평균 NW20 t를
T1-impl 스크립트와 같은 방식으로 재계산한다(먼저 c_real → 2.21, c_044 → 2.79 재현 확인).
"""
from __future__ import annotations

import contextlib
import json
import math
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "analysis" / "us_slippage_by_adv_20260928"
CACHE = OUT / "alpaca_cache"
DB = ROOT / "data" / "v2_event_store.db"
BROKER = ROOT / "data" / "analysis" / "live_history_20260924" / "broker_period.json"
TRIPS = ROOT / "data" / "analysis" / "live_history_20260924" / "trips_enriched.json"
T1 = ROOT / "data" / "analysis" / "model_T1_impl_20260927" / "trades_I1.csv.gz"
ENV = ROOT / ".env.alpaca"
BASE = "https://data.alpaca.markets/v2/stocks"
LAG = 20


def headers():
    env = {}
    for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("="); env[k.strip()] = v.strip()
    return {"APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"], "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"]}


HDR = None


def aget(path, params, key):
    """Alpaca GET with file cache."""
    global HDR
    f = CACHE / (key.replace(":", "").replace("/", "_") + ".json")
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    if HDR is None:
        HDR = headers()
    url = f"{BASE}/{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(4):
        try:
            d = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=HDR), timeout=60).read())
            break
        except Exception as exc:  # noqa: BLE001
            print("  alpaca 실패", key, exc, flush=True); time.sleep(2 + 3 * attempt)
    else:
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d), encoding="utf-8")
    time.sleep(0.12)
    return d


def ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def iso(d):
    return d.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------- events
def load_orders():
    with contextlib.closing(sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)) as c:
        R = c.execute("select event_id,event_type,session_date,ticker,occurred_at,execution_id,decision_id,payload_json "
                      "from lifecycle_events where runtime_mode='live' and market='US' and event_type in "
                      "('ORDER_SENT','FILLED','PARTIAL_FILLED','CLOSED') order by event_id").fetchall()
    buys_sent, buys_fill = {}, defaultdict(list)
    sells_sent, closes = defaultdict(list), []
    for eid, et, sd, tk, occ, ex, dec, pj in R:
        p = json.loads(pj)
        side = p.get("side")
        if et == "ORDER_SENT":
            if side == "sell" or p.get("path_status") == "SELL_SENT":
                sells_sent[(tk, sd)].append({"ts": occ, "limit": p.get("price"), "qty": p.get("qty"),
                                             "path_run_id": p.get("path_run_id"), "exec": ex, "reason": p.get("close_reason")})
            else:
                px = p.get("price") if side == "buy" else p.get("price_native")
                if ex and ex not in buys_sent:
                    buys_sent[ex] = {"ticker": tk, "session_date": sd, "sent_ts": occ, "limit": px, "qty": p.get("qty"),
                                     "strategy": p.get("path_type") or p.get("strategy_used") or p.get("source_strategy") or "",
                                     "route": p.get("entry_route") or ("path_b" if p.get("path_run_id") else ""),
                                     "decision": dec}
        elif et == "FILLED":
            fp = p.get("fill_price_native", p.get("price"))
            if side == "sell":
                continue
            if ex and fp:
                buys_fill[ex].append({"ts": occ, "px": float(fp), "qty": p.get("qty"), "ticker": tk, "sd": sd,
                                      "strategy": p.get("path_type") or p.get("strategy_used") or ""})
        elif et == "CLOSED":
            closes.append({"ticker": tk, "sd": sd, "ts": occ, "exec": ex, "p": p})
    return buys_sent, buys_fill, sells_sent, closes


def build_fills():
    buys_sent, buys_fill, sells_sent, closes = load_orders()
    rows = []
    n_sent_buy = len(buys_sent)
    for ex, fl in buys_fill.items():
        f0 = min(fl, key=lambda z: z["ts"])
        s = buys_sent.get(ex)
        rows.append({"side": "buy", "ticker": f0["ticker"], "session_date": f0["sd"], "order_no": ex,
                     "sent_ts": s["sent_ts"] if s else None, "limit": s["limit"] if s else None,
                     "fill_ts": f0["ts"], "fill_px": f0["px"], "qty": f0["qty"],
                     "strategy": (s or {}).get("strategy") or f0["strategy"], "route": (s or {}).get("route", ""),
                     "fill_px_source": "event_FILLED"})
    # sells: broker avg per (trad_day, ticker)
    b = json.load(open(BROKER, encoding="utf-8"))
    brow = defaultdict(list)
    for r in b["US_ALL"]["rows"]:
        sq = float(r["slcl_qty"] or 0)
        if sq > 0 and float(r["frst_bltn_exrt"] or 0) > 0:
            brow[(r["ovrs_pdno"], f"{r['trad_day'][:4]}-{r['trad_day'][4:6]}-{r['trad_day'][6:]}")].append(r)
    # one close per (ticker, sd): group CLOSED events by path_run_id/exec to count distinct positions
    pos = defaultdict(dict)
    for c in closes:
        key = c["p"].get("path_run_id") or c["p"].get("parent_decision_id") or c["exec"] or c["ts"]
        d = pos[(c["ticker"], c["sd"])].setdefault(key, {"ts": c["ts"], "qty": None, "exit_native": None, "reason": None})
        d["ts"] = min(d["ts"], c["ts"])
        d["qty"] = d["qty"] or c["p"].get("qty")
        d["exit_native"] = d["exit_native"] or c["p"].get("exit_price_native")
        d["reason"] = d["reason"] or c["p"].get("close_reason")
    sell_excl = defaultdict(int)
    for (tk, sd), P in pos.items():
        br = brow.get((tk, sd), [])
        if len(P) != 1:
            sell_excl["multi_position_same_day"] += len(P); continue
        (k, d), = P.items()
        if len(br) == 1:
            r = br[0]; px = float(r["avg_sll_unpr"]) / float(r["frst_bltn_exrt"]); src = "broker_avg_sll"
            bq = float(r["slcl_qty"])
        elif d["exit_native"]:
            px = float(d["exit_native"]); src = "event_exit_price_native"; bq = None
        else:
            sell_excl["no_broker_row" if not br else "multi_broker_rows"] += 1; continue
        ss = sells_sent.get((tk, sd), [])
        ss = [x for x in ss if (x["path_run_id"] == k) or len(ss) == 1] or ss
        s0 = min(ss, key=lambda z: z["ts"]) if ss else None
        rows.append({"side": "sell", "ticker": tk, "session_date": sd, "order_no": s0["exec"] if s0 else None,
                     "sent_ts": s0["ts"] if s0 else None, "limit": s0["limit"] if s0 else None,
                     "fill_ts": d["ts"], "fill_px": px, "qty": d["qty"] or bq, "broker_qty": bq,
                     "n_sell_orders": len(ss), "strategy": "", "route": "", "reason": d["reason"],
                     "fill_px_source": src})
    return pd.DataFrame(rows), n_sent_buy, dict(sell_excl)


# ---------------------------------------------------------------- market data
def nbbo_before(tk, t_utc):
    for win in (3, 30, 300):
        q = aget(f"{tk}/quotes", {"start": iso(t_utc - timedelta(seconds=win)), "end": iso(t_utc), "limit": 10000,
                                  "feed": "sip"}, f"q_{tk}_{t_utc:%Y%m%dT%H%M%S}_{win}")
        qs = [x for x in (q or {}).get("quotes") or [] if x.get("bp", 0) > 0 and x.get("ap", 0) > 0 and x["ap"] >= x["bp"]]
        if qs:
            z = qs[-1]
            return z["bp"], z["ap"], (t_utc - ts(z["t"][:26] + "Z" if len(z["t"]) > 27 else z["t"])).total_seconds()
    return None, None, None


def minute_bars(tk, day):
    d = aget(f"{tk}/bars", {"start": f"{day}T04:00:00-04:00", "end": f"{day}T20:00:00-04:00", "timeframe": "1Min",
                            "limit": 10000, "feed": "sip", "adjustment": "raw"}, f"m_{tk}_{day}")
    bars = (d or {}).get("bars") or []
    return {b["t"][:16]: b for b in bars}


def daily_bars(tk, day):
    start = (datetime.fromisoformat(day) - timedelta(days=50)).strftime("%Y-%m-%d")
    d = aget(f"{tk}/bars", {"start": start, "end": day, "timeframe": "1Day", "limit": 100, "feed": "sip",
                            "adjustment": "raw"}, f"d_{tk}_{day}")
    return [b for b in (d or {}).get("bars") or [] if b["t"][:10] < day], [b for b in (d or {}).get("bars") or [] if b["t"][:10] == day]


def enrich(F):
    out = []
    for i, r in F.iterrows():
        tk, day = r.ticker, r.session_date
        rec = r.to_dict()
        prev, today = daily_bars(tk, day)
        prev = prev[-20:]
        rec["adv20"] = float(np.mean([b["c"] * b["v"] for b in prev])) if len(prev) >= 10 else np.nan
        rec["n_adv_days"] = len(prev)
        rec["prev_close"] = prev[-1]["c"] if prev else np.nan
        rec["drange20"] = float(np.mean([(b["h"] - b["l"]) / b["c"] for b in prev])) if prev else np.nan
        mb = minute_bars(tk, day)
        ob = mb.get(f"{day}T13:30")
        rec["open_px"] = ob["o"] if ob else (today[0]["o"] if today else np.nan)
        rec["open_src"] = "sip_1min_0930" if ob else ("sip_1day" if today else "none")
        ft = ts(r.fill_ts)
        rec["min_after_open_fill"] = (ft - ts(f"{day}T13:30:00Z")).total_seconds() / 60
        st = ts(r.sent_ts) if isinstance(r.sent_ts, str) else None
        rec["min_after_open_sent"] = (st - ts(f"{day}T13:30:00Z")).total_seconds() / 60 if st else np.nan
        ref_t = st or ft
        bid, ask, age = nbbo_before(tk, ref_t)
        rec["bid"], rec["ask"], rec["quote_age_s"] = bid, ask, age
        rec["ref_time_src"] = "sent" if st else "fill_detect"
        fm = mb.get(ft.strftime("%Y-%m-%dT%H:%M"))
        # fill in [low, high] of detection minute or the 2 preceding minutes
        lo = [mb.get((ft - timedelta(minutes=k)).strftime("%Y-%m-%dT%H:%M")) for k in range(0, 3)]
        lo = [b for b in lo if b]
        rec["fill_in_bar"] = bool(lo) and (min(b["l"] for b in lo) - 1e-6 <= r.fill_px <= max(b["h"] for b in lo) + 1e-6)
        rec["fill_minute_range_bp"] = (fm["h"] - fm["l"]) / fm["c"] * 1e4 if fm else np.nan
        out.append(rec)
        if (i + 1) % 25 == 0:
            print(f"  enrich {i + 1}/{len(F)}", flush=True)
    E = pd.DataFrame(out)
    sgn = np.where(E.side == "buy", 1.0, -1.0)
    E["mid"] = (E.bid + E.ask) / 2
    E["qspread_bp"] = (E.ask - E.bid) / E.mid * 1e4
    E["slip_mid_bp"] = sgn * (E.fill_px - E.mid) / E.mid * 1e4
    E["slip_open_bp"] = sgn * (E.fill_px - E.open_px) / E.open_px * 1e4
    E["aggr_bp"] = sgn * (pd.to_numeric(E.limit, errors="coerce") - E.mid) / E.mid * 1e4
    lim = pd.to_numeric(E.limit, errors="coerce")
    E["marketable"] = np.where(E.side == "buy", lim >= E.ask - 1e-9, lim <= E.bid + 1e-9)
    E.loc[lim.isna() | E.mid.isna(), "marketable"] = np.nan
    E["adv_bucket"] = pd.cut(E.adv20, [-1, 5e6, 20e6, 1e15], labels=["<5M", "5-20M", ">=20M"], right=False).astype(str)
    E["px_bucket"] = pd.cut(E.prev_close, [0, 10, 50, 1e9], labels=["<$10", "$10-50", ">=$50"], right=False).astype(str)
    m = E.min_after_open_sent.fillna(E.min_after_open_fill)
    E["tod"] = np.select([m < 0, m < 15, m < 60, m < 360], ["premarket", "0-15m", "15-60m", "mid"], "last30m")
    E["notional_usd"] = E.fill_px * pd.to_numeric(E.qty, errors="coerce")
    return E


# ---------------------------------------------------------------- stats
def desc(x):
    x = pd.Series(x).dropna()
    if len(x) == 0:
        return {"n": 0}
    return {"n": int(len(x)), "median": round(float(x.median()), 2), "mean": round(float(x.mean()), 2),
            "p75": round(float(x.quantile(.75)), 2), "p90": round(float(x.quantile(.9)), 2),
            "se_mean": round(float(x.std(ddof=1) / math.sqrt(len(x))), 2) if len(x) > 1 else None}


def nw_t(x, lags=LAG):  # T1-impl 스크립트와 동일
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    n = len(x)
    if n < 5:
        return float("nan")
    u = x - x.mean()
    s = (u @ u) / n
    for L in range(1, min(lags, n - 1) + 1):
        w = 1 - L / (lags + 1)
        s += 2 * w * (u[L:] @ u[:-L]) / n
    return float(x.mean() / math.sqrt(s / n)) if s > 0 else float("nan")


def t1_eval(cost):
    T = pd.read_csv(T1)
    net = T.gA20.values - cost(T)
    sess = pd.Series(net).groupby(T.e.values).mean()
    half = np.where(T.year.values <= 2021, "h1", "h2")
    return {"n": int(len(T)), "mean_pct": round(float(net.mean()) * 100, 3), "t_session_nw20": round(nw_t(sess.values), 3),
            "h1_pct": round(float(net[half == "h1"].mean()) * 100, 3), "h2_pct": round(float(net[half == "h2"].mean()) * 100, 3),
            "avg_cost_pct": round(float(cost(T).mean()) * 100, 3)}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    F, n_sent_buy, sell_excl = build_fills()
    print("fills", F.side.value_counts().to_dict(), "buy ORDER_SENT", n_sent_buy, "sell excluded", sell_excl, flush=True)
    E = enrich(F)
    E.to_csv(OUT / "fills_enriched.csv", index=False, encoding="utf-8")
    res = {"n_buy_order_sent": n_sent_buy, "sell_excluded": sell_excl, "counts": {}}
    trips = [t for t in json.load(open(TRIPS, encoding="utf-8")) if t["market"] == "US"]
    res["counts"]["trips_enriched_US"] = len(trips)
    res["counts"]["fills"] = E.side.value_counts().to_dict()
    res["counts"]["tod"] = E.groupby(["side", "tod"]).size().unstack(fill_value=0).to_dict()
    res["counts"]["adv_bucket"] = E.groupby(["side", "adv_bucket"]).size().unstack(fill_value=0).to_dict()
    res["counts"]["marketable"] = E.groupby(["side", E.marketable.astype(str)]).size().unstack(fill_value=0).to_dict()
    res["counts"]["fill_in_bar_share"] = E.groupby("side").fill_in_bar.mean().round(3).to_dict()
    res["counts"]["quote_found_share"] = E.groupby("side").mid.apply(lambda s: s.notna().mean()).round(3).to_dict()
    res["counts"]["fill_px_source"] = E.groupby(["side", "fill_px_source"]).size().to_dict()
    res["counts"]["fill_px_source"] = {f"{a}|{b}": int(v) for (a, b), v in res["counts"]["fill_px_source"].items()}
    res["counts"]["notional_usd_median"] = E.groupby("side").notional_usd.median().round(0).to_dict()
    res["counts"]["strategy"] = E[E.side == "buy"].strategy.value_counts().to_dict()
    # quality filter: quote age ≤ 30s, fill within ±2 minute bar range
    ok = E.mid.notna() & (E.quote_age_s <= 30)
    res["counts"]["ok_quote_age30"] = E[ok].groupby("side").size().to_dict()
    tab = {}
    for side in ("buy", "sell"):
        for sub_name, sub in (("all", ok), ("marketable", ok & (E.marketable == True)),  # noqa: E712
                              ("resting", ok & (E.marketable == False)),  # noqa: E712
                              ("regular_0-15m", ok & (E.tod == "0-15m")),
                              ("regular_0-15m_marketable", ok & (E.tod == "0-15m") & (E.marketable == True)),  # noqa: E712
                              ("premarket", ok & (E.tod == "premarket"))):
            for b in ("ALL", ">=20M", "5-20M", "<5M"):
                m = sub & (E.side == side) & ((E.adv_bucket == b) if b != "ALL" else True)
                tab[f"{side}|{sub_name}|{b}"] = {"slip_mid_bp": desc(E.loc[m, "slip_mid_bp"]),
                                                 "slip_open_bp": desc(E.loc[m & (E.tod != "premarket"), "slip_open_bp"]),
                                                 "qspread_bp": desc(E.loc[m, "qspread_bp"]),
                                                 "drange20_pct": desc(E.loc[m, "drange20"] * 100)}
    res["table"] = tab
    # price bucket
    res["by_px"] = {f"{s}|{p}": desc(E.loc[ok & (E.side == s) & (E.px_bucket == p), "slip_mid_bp"])
                    for s in ("buy", "sell") for p in ("<$10", "$10-50", ">=$50")}
    # fees: broker sell-side fee / sell amount (sell side only visible)
    b = json.load(open(BROKER, encoding="utf-8"))["US_ALL"]["rows"]
    fee = sum(float(r["stck_sll_tlex"] or 0) for r in b); amt = sum(float(r["frcr_sll_amt_smtl1"] or 0) for r in b)
    res["broker_sell_fee_pct"] = round(fee / amt * 100, 4) if amt else None
    fprt = []
    with contextlib.closing(sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)) as c:
        for (pj,) in c.execute("select payload_json from lifecycle_events where runtime_mode='live' and market='US' "
                               "and event_type='CLOSED' and payload_json like '%fee_pct_round_trip%'"):
            v = json.loads(pj).get("fee_pct_round_trip")
            if v:
                fprt.append(float(v))
    res["event_fee_pct_round_trip"] = desc(fprt)
    (OUT / "summary.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(json.dumps(res["counts"], ensure_ascii=False, default=str))
    return E, res


def t1_part(res):
    """측정 버킷 비용을 T1 I1에 적용. 버킷 표본 < 10이면 fallback을 기록."""
    tab = res["table"]
    out = {"repro": {"c_real": t1_eval(lambda T: T.c_real.values), "c_044": t1_eval(lambda T: T.c_044.values),
                     "c_010": t1_eval(lambda T: T.c_010.values)}}

    def bucket_cost(stat, sub, fee):
        cost, used = {}, {}
        for bk in (">=20M", "5-20M", "<5M"):
            parts = []
            for side in ("buy", "sell"):
                d = tab[f"{side}|{sub}|{bk}"]["slip_mid_bp"]
                src = f"{side}|{sub}|{bk}"
                if d.get("n", 0) < 10:  # fallback: pooled bucket of all subs, then side ALL p75
                    d2 = tab[f"{side}|all|{bk}"]["slip_mid_bp"]
                    if d2.get("n", 0) >= 10:
                        d, src = d2, f"{side}|all|{bk}(fallback)"
                    else:
                        d, src = tab[f"{side}|all|ALL"]["slip_mid_bp"], f"{side}|all|ALL p75 (fallback, n<10 in bucket)"
                        parts.append((d["p75"], src)); continue
                parts.append((d[stat], src))
            cost[bk] = fee + sum(p[0] for p in parts) / 1e4
            used[bk] = {"rt_cost_pct": round(cost[bk] * 100, 3), "components": parts}
        return cost, used

    def apply(cost):
        return lambda T: np.where(T.dvol20.values >= 20e6, cost[">=20M"], np.where(T.dvol20.values >= 5e6, cost["5-20M"], cost["<5M"]))

    fee_meas = res["event_fee_pct_round_trip"].get("median")
    for fee_name, fee in (("fee044", 0.0044), ("fee_event_median", (fee_meas or 44) / 100)):
        for stat in ("mean", "p75"):
            for sub in ("marketable", "all"):
                cost, used = bucket_cost(stat, sub, fee)
                out[f"{fee_name}|{sub}|{stat}"] = {"costs": used, "I1": t1_eval(apply(cost))}
    # ladder reference
    out["ladder_original"] = t1_eval(lambda T: T.c_real.values)
    return out


def open_spread_sample(per_bucket=150, since="2024-01-01", seed=7, minute=31):
    """보강: 우리 체결은 전부 ADV≥$20M이라 $5~20M·<$5M은 우리 체결로 잴 수 없다.
    T1 I1 실제 선택 종목(진입일 ≥ since)에서 버킷별 표본을 뽑아 진입일 09:31:00~09:31:30 ET SIP NBBO의
    호가 스프레드(중앙값)를 잰다. 시가 직후 marketable 주문이 내는 편도 비용 ≈ 반스프레드, 왕복 ≈ 스프레드 1개(가정)."""
    T = pd.read_csv(T1)
    T = T[T.entry_date >= since].copy()
    T["bk"] = np.where(T.dvol20 >= 20e6, ">=20M", np.where(T.dvol20 >= 5e6, "5-20M", "<5M"))
    rows = []
    for bk, g in T.groupby("bk"):
        for _, r in g.sample(min(per_bucket, len(g)), random_state=seed).iterrows():
            day = r.entry_date
            dst = 4 if (pd.Timestamp(day).month, ) and _is_dst(day) else 5
            st = datetime.fromisoformat(day).replace(hour=9 + dst, minute=minute, tzinfo=timezone.utc)
            q = aget(f"{r.ticker}/quotes", {"start": iso(st), "end": iso(st + timedelta(seconds=30)), "limit": 10000,
                                            "feed": "sip"}, f"os_{r.ticker}_{day}" + ("" if minute == 31 else f"_m{minute}"))
            qs = [x for x in (q or {}).get("quotes") or [] if x.get("bp", 0) > 0 and x.get("ap", 0) > 0 and x["ap"] >= x["bp"]]
            sp = [(x["ap"] - x["bp"]) / ((x["ap"] + x["bp"]) / 2) * 1e4 for x in qs]
            rows.append({"ticker": r.ticker, "entry_date": day, "bucket": bk, "dvol20": r.dvol20, "close_a": r.close_a,
                         "n_quotes": len(sp), "qspread_bp_med": float(np.median(sp)) if sp else np.nan})
    S = pd.DataFrame(rows)
    S.to_csv(OUT / f"open_spread_sample_T1_0931.csv" if minute == 31 else OUT / f"open_spread_sample_T1_09{minute}.csv", index=False, encoding="utf-8")
    return S


def _is_dst(day):
    d = pd.Timestamp(day)
    y = d.year
    start = pd.Timestamp(f"{y}-03-08") + pd.Timedelta(days=(6 - pd.Timestamp(f"{y}-03-08").weekday()) % 7)
    end = pd.Timestamp(f"{y}-11-01") + pd.Timedelta(days=(6 - pd.Timestamp(f"{y}-11-01").weekday()) % 7)
    return start <= d < end


def open_spread_part(res, minute=31):
    S = open_spread_sample(minute=minute)
    out = {"minute_et": f"09:{minute}", "sample": {bk: desc(S.loc[S.bucket == bk, "qspread_bp_med"]) for bk in (">=20M", "5-20M", "<5M")},
           "no_quote": S.groupby("bucket").qspread_bp_med.apply(lambda s: int(s.isna().sum())).to_dict()}
    for stat in ("median", "mean"):
        for fee_name, fee in (("fee044", 0.0044), ("fee050", 0.005)):
            cost = {bk: fee + out["sample"][bk][stat] / 1e4 for bk in out["sample"]}
            f = lambda T, c=cost: np.where(T.dvol20.values >= 20e6, c[">=20M"], np.where(T.dvol20.values >= 5e6, c["5-20M"], c["<5M"]))  # noqa: E731
            out[f"{fee_name}|spread_{stat}"] = {"rt_cost_pct": {k: round(v * 100, 3) for k, v in cost.items()}, "I1": t1_eval(f)}
    return out


if __name__ == "__main__":
    if "--open-spread" in sys.argv:
        res = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
        mn = int(sys.argv[sys.argv.index("--minute") + 1]) if "--minute" in sys.argv else 31
        key = "open_spread_T1" if mn == 31 else f"open_spread_T1_09{mn}"
        res[key] = open_spread_part(res, mn)
        (OUT / "summary.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(json.dumps(res[key], ensure_ascii=False, indent=1, default=str))
        sys.exit(0)
    E, res = main()
    if "--no-t1" not in sys.argv:
        res["t1"] = t1_part(res)
        (OUT / "summary.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        for k, v in res["t1"].items():
            print(k, json.dumps(v, ensure_ascii=False, default=str)[:400])
