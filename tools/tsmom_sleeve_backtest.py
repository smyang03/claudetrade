#!/usr/bin/env python
"""
Arm A — 저회전 TSMOM 추세추종 sleeve 오프라인 백테스트 (read-only 분석).

설계: docs/reports/design_tsmom_shadow_sleeve_20260707.md
목적: 트랙① 백테스트를 우리 실제 비용모델·KR 포함·다국면으로 재실행해,
      장기 TSMOM(12-1 모멘텀 + 200d MA, 롱온리, 월 리밸런스)이
      비용 차감 후에도 net 양수 + 벤치 우위 + 하락장 방어인지 판정한다.

라이브 무접촉: 주문·brain·config·state 무변경. yfinance 가격 다운로드 + 리포트 JSON 출력만.
파라미터 서치 금지(표준값 고정). 판정 기준 = 우리 실제 net(시장별 분리).

사용:
  python tools/tsmom_sleeve_backtest.py --market US
  python tools/tsmom_sleeve_backtest.py --market KR
  python tools/tsmom_sleeve_backtest.py --market both   (기본)
캐시: 다운로드는 scratchpad에 parquet/pkl로 캐시 → 재실행 빠름.
"""
import argparse, os, sys, json, math, statistics as st, time, pickle

# ---- 고정 파라미터 (서치 금지) ----
MOM_LOOKBACK = 252      # 12개월
MOM_SKIP = 21           # 최근 1개월 제외 (12-1)
MA_WINDOW = 200         # 200d 추세필터
QUANTILE = 5            # 상위 1/5분위
REB_DAYS = 21           # 월 리밸런스 (거래일)
START = "2015-06-01"    # 워밍업(252+skip) 포함 → 실효 백테스트 2016-07~
# 우리 실측 비용모델 (왕복 %). 편도 = RT/2.
COST_RT = {"US": 0.50, "KR": 0.21}
FX_RT_US = {"base": 0.0, "pess": 0.20}   # US 환전 왕복(달러잔고 회전=0 / 비관 0.20 둘 다 보고)
BENCH_INDEX = {"US": "SPY", "KR": "^KS11"}
OUR_SYSTEM_NET_NOTE = "현 시스템 통산 net: US -36.9 (n252) / KR gross 통산 음수 (참고: gross_alpha_hunt_verdict_20260707.md)"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = os.environ.get("TSMOM_CACHE", os.path.join(ROOT, "state", "tsmom_cache"))
os.makedirs(SCRATCH, exist_ok=True)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


# ---------- 유니버스 ----------
def us_universe():
    """S&P500 구성종목 (datahub GitHub CSV → 위키피디아 백업). 실패 시 대형주 폴백."""
    import urllib.request
    sources = [
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv",
        "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv",
    ]
    for url in sources:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            txt = urllib.request.urlopen(req, timeout=20).read().decode("utf-8")
            import csv, io
            rows = list(csv.DictReader(io.StringIO(txt)))
            col = "Symbol" if "Symbol" in rows[0] else list(rows[0].keys())[0]
            syms = [r[col].replace(".", "-").strip() for r in rows if r.get(col)]
            syms = [s for s in syms if s and s.isascii()]
            if len(syms) > 400:
                log(f"[US] S&P500 {len(syms)}종목 확보 (datahub)")
                return sorted(set(syms))
        except Exception as e:
            log(f"[US] 소스 실패({str(e)[:50]})")
    log("[US] 전 소스 실패 → 폴백 대형주 리스트")
    # 폴백: 유동성 대형주 ~60 (최소 재현용)
    return ("AAPL MSFT NVDA AMZN GOOGL META AVGO TSLA BRK-B JPM LLY V UNH XOM MA JNJ "
            "PG HD COST ABBV MRK CVX WMT KO PEP BAC ADBE CRM NFLX AMD TMO ACN LIN MCD "
            "ABT CSCO WFC DHR INTC TXN DIS QCOM AMGN CAT VZ INTU IBM PFE GE NOW UBER "
            "GS BKNG SPGI ISRG HON PLTR AMAT MU").split()


def kr_universe(top_n=250):
    """로컬 data/price/kr 중 최근 거래대금 상위 top_n (우리 tradable 유동주). 코드→.KS/.KQ는 다운로드시 판별."""
    d = os.path.join(ROOT, "data", "price", "kr")
    scored = []
    for fn in os.listdir(d):
        if not (fn.startswith("kr_") and fn.endswith(".csv")):
            continue
        code = fn[3:-4]
        try:
            import csv
            rows = list(csv.DictReader(open(os.path.join(d, fn), encoding="utf-8-sig")))
            rows = rows[-20:]
            if len(rows) < 10:
                continue
            dv = st.mean([float(r["close"]) * float(r["volume"]) for r in rows if r["close"] and r["volume"]])
            scored.append((dv, code))
        except Exception:
            continue
    scored.sort(reverse=True)
    codes = [c for _, c in scored[:top_n]]
    log(f"[KR] 로컬 유동성 상위 {len(codes)}종목 (거래대금 기준)")
    return codes


# ---------- 다운로드 (캐시) ----------
def download(market, symbols):
    cache = os.path.join(SCRATCH, f"px_{market}.pkl")
    data = {}
    if os.path.exists(cache):
        try:
            data = pickle.load(open(cache, "rb"))
            log(f"[{market}] 캐시 로드 {len(data)}종목")
        except Exception:
            data = {}
    import yfinance as yf
    if market == "US":
        need = [s for s in symbols if s not in data]
        _dl_batch(yf, need, data, suffix="")
    else:
        # KR: .KS 시도 후 실패분 .KQ
        need = [c for c in symbols if c not in data]
        ks = [c + ".KS" for c in need]
        tmp = {}
        _dl_batch(yf, ks, tmp, suffix="")
        for c in need:
            if (c + ".KS") in tmp and len(tmp[c + ".KS"]) > MOM_LOOKBACK:
                data[c] = tmp[c + ".KS"]
        still = [c for c in need if c not in data]
        kq = {}
        _dl_batch(yf, [c + ".KQ" for c in still], kq, suffix="")
        for c in still:
            if (c + ".KQ") in kq and len(kq[c + ".KQ"]) > MOM_LOOKBACK:
                data[c] = kq[c + ".KQ"]
    # 인덱스도
    idx = BENCH_INDEX[market]
    if idx not in data:
        tmp = {}
        _dl_batch(yf, [idx], tmp, suffix="")
        if idx in tmp:
            data[idx] = tmp[idx]
    pickle.dump(data, open(cache, "wb"))
    log(f"[{market}] 캐시 저장 {len(data)}종목")
    return data


def _dl_batch(yf, syms, out, suffix, chunk=100):
    for i in range(0, len(syms), chunk):
        part = syms[i:i + chunk]
        if not part:
            continue
        for attempt in range(3):
            try:
                df = yf.download(part, start=START, progress=False, auto_adjust=True,
                                 threads=True, group_by="ticker")
                break
            except Exception as e:
                log(f"  다운로드 재시도 {attempt+1}: {str(e)[:50]}")
                time.sleep(2)
        else:
            continue
        for s in part:
            try:
                if len(part) == 1:
                    col = df["Close"] if "Close" in df else df[s]["Close"]
                else:
                    col = df[s]["Close"]
                ser = [(d.strftime("%Y-%m-%d"), float(v)) for d, v in col.items() if v == v and v > 0]
                if len(ser) > MOM_LOOKBACK:
                    out[s] = ser
            except Exception:
                continue
        log(f"  [{i+len(part)}/{len(syms)}] 누적 {len(out)}")


# ---------- 백테스트 ----------
def to_map(ser):
    return {d: p for d, p in ser}


def backtest(market, data, fx_rt=0.0):
    idx_sym = BENCH_INDEX[market]
    idx = data.get(idx_sym)
    if not idx:
        raise RuntimeError(f"{market} 인덱스 {idx_sym} 없음")
    dates = [d for d, _ in idx]  # 공통 달력 = 인덱스 날짜
    tickers = [t for t in data if t != idx_sym]
    pmap = {t: to_map(data[t]) for t in tickers}
    imap = to_map(idx)

    one_way = COST_RT[market] / 2.0 + (fx_rt / 2.0 if market == "US" else 0.0)

    rebs = list(range(MOM_LOOKBACK + MOM_SKIP, len(dates) - REB_DAYS, REB_DAYS))
    strat_r, ew_r, idx_r, turn = [], [], [], []
    prev_w = {}
    hold_dates = []

    for i in rebs:
        d0, d1 = dates[i], dates[i + REB_DAYS]
        elig, feats = [], {}
        for t in tickers:
            p = pmap[t]
            p0 = p.get(d0)
            pf = p.get(d1)
            if p0 is None or pf is None:
                continue
            d_mom = dates[i - MOM_LOOKBACK]
            d_skip = dates[i - MOM_SKIP]
            p_mom, p_skip = p.get(d_mom), p.get(d_skip)
            if not p_mom or not p_skip:
                continue
            mom = p_skip / p_mom - 1.0  # 12-1 모멘텀
            ma_px = [p.get(dates[j]) for j in range(i - MA_WINDOW, i)]
            ma_px = [x for x in ma_px if x]
            if len(ma_px) < MA_WINDOW * 0.8:
                continue
            above = p0 > (sum(ma_px) / len(ma_px))
            elig.append(t)
            feats[t] = (mom, above, pf / p0 - 1.0)
        if len(elig) < 40:
            continue
        # TSMOM: 모멘텀 상위 분위 AND 200d MA 위 (롱온리 추세추종)
        ranked = sorted(elig, key=lambda t: feats[t][0], reverse=True)
        qn = max(1, len(elig) // QUANTILE)
        sel = [t for t in ranked[:qn] if feats[t][1]]
        if not sel:
            continue
        w = {t: 1.0 / len(sel) for t in sel}
        # 회전·비용
        allk = set(w) | set(prev_w)
        dturn = sum(abs(w.get(t, 0) - prev_w.get(t, 0)) for t in allk)
        cost = dturn * one_way / 100.0
        r = st.mean([feats[t][2] for t in sel]) - cost
        strat_r.append(r)
        ew_r.append(st.mean([feats[t][2] for t in elig]))
        ir = imap.get(d1, imap.get(d0)) and (imap[d1] / imap[d0] - 1.0) if imap.get(d0) and imap.get(d1) else None
        idx_r.append(ir if ir is not None else 0.0)
        turn.append(dturn)
        prev_w = w
        hold_dates.append(d0)
    return {"strat": strat_r, "ew": ew_r, "idx": idx_r, "turn": turn, "dates": hold_dates}


def metrics(rets):
    if not rets:
        return {}
    n = len(rets)
    tot = 1.0
    for r in rets:
        tot *= (1 + r)
    yrs = n / 12.0
    cagr = (tot ** (1 / yrs) - 1) * 100 if tot > 0 else -100
    mean = st.mean(rets)
    sd = st.pstdev(rets) or 1e-9
    sharpe = mean / sd * math.sqrt(12)
    # MDD
    eq = 1.0
    peak = 1.0
    mdd = 0.0
    for r in rets:
        eq *= (1 + r)
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    return {"n_reb": n, "cagr": round(cagr, 2), "sharpe": round(sharpe, 2),
            "mdd": round(mdd * 100, 1), "total_ret": round((tot - 1) * 100, 1),
            "mean_mo": round(mean * 100, 3)}


def excess_t(strat, ew):
    m = min(len(strat), len(ew))
    if m < 3:
        return None
    diff = [strat[j] - ew[j] for j in range(m)]
    mean = st.mean(diff)
    se = (st.pstdev(diff) / math.sqrt(m)) or 1e-9
    return {"ann_excess_pp": round(mean * 12 * 100, 2), "t": round(mean / se, 2),
            "win_mo_pct": round(100 * sum(1 for d in diff if d > 0) / m, 0)}


def regime_slices(res, market):
    """하락국면 방어 확인: 알려진 조정기 구간의 strat vs ew vs idx 누적."""
    slices = {
        "2018_bear": ("2018-01-01", "2018-12-31"),
        "2020_covid": ("2020-02-01", "2020-04-30"),
        "2022_bear": ("2022-01-01", "2022-10-31"),
        "2020_23_up": ("2020-05-01", "2021-12-31"),
        "2023_25_up": ("2023-01-01", "2025-12-31"),
    }
    out = {}
    for name, (a, b) in slices.items():
        s = e = x = 1.0
        cnt = 0
        for k, d in enumerate(res["dates"]):
            if a <= d <= b:
                s *= (1 + res["strat"][k])
                e *= (1 + res["ew"][k])
                x *= (1 + res["idx"][k])
                cnt += 1
        if cnt >= 2:
            out[name] = {"n": cnt, "strat_pct": round((s-1)*100, 1),
                         "ew_pct": round((e-1)*100, 1), "idx_pct": round((x-1)*100, 1),
                         "excess_vs_ew_pp": round((s-e)*100, 1)}
    return out


def run_market(market):
    log(f"\n===== {market} =====")
    syms = us_universe() if market == "US" else kr_universe()
    data = download(market, syms)
    n_names = len([t for t in data if t != BENCH_INDEX[market]])
    log(f"[{market}] 백테스트 유니버스 {n_names}종목")
    report = {"market": market, "universe_n": n_names,
              "params": {"mom_lookback": MOM_LOOKBACK, "mom_skip": MOM_SKIP,
                         "ma": MA_WINDOW, "quantile": QUANTILE, "reb_days": REB_DAYS},
              "cost_rt_pct": COST_RT[market]}
    fx_variants = FX_RT_US if market == "US" else {"base": 0.0}
    for fxname, fx in fx_variants.items():
        res = backtest(market, data, fx_rt=fx)
        tag = f"net_fx_{fxname}" if market == "US" else "net"
        report[tag] = {
            "TSMOM": metrics(res["strat"]),
            "EW_bench": metrics(res["ew"]),
            "INDEX_bench": metrics(res["idx"]),
            "excess_vs_ew": excess_t(res["strat"], res["ew"]),
            "avg_turnover_pct": round(st.mean(res["turn"]) * 100, 0) if res["turn"] else None,
            "regime": regime_slices(res, market),
            "sample_period": f"{res['dates'][0]}~{res['dates'][-1]}" if res["dates"] else "n/a",
        }
    return report


def verdict(report):
    """pre-registered 통과 판정."""
    market = report["market"]
    key = "net_fx_base" if market == "US" else "net"
    r = report.get(key, {})
    ts = r.get("TSMOM", {})
    ex = r.get("excess_vs_ew") or {}
    reg = r.get("regime", {})
    net_pos = ts.get("cagr", -99) > 0
    defends = sum(1 for k, v in reg.items() if k.endswith("bear") or "covid" in k
                  and v.get("excess_vs_ew_pp", -99) > 0)
    down_regimes = [k for k in reg if k.endswith("bear") or "covid" in k]
    defend_ok = all(reg[k]["excess_vs_ew_pp"] > 0 for k in down_regimes) if down_regimes else None
    return {
        "net_cagr_positive": net_pos,
        "beats_or_defends_ew": (ex.get("ann_excess_pp", -99) > 0) or bool(defend_ok),
        "drawdown_defense_all": defend_ok,
        "excess_t": ex.get("t"),
        "note": "통과=net양수 AND (EW초과 OR 하락장 방어재현). t<2면 forward 필수(경계).",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", choices=["US", "KR", "both"], default="both")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "reports", "tsmom_sleeve_backtest_result.json"))
    args = ap.parse_args()
    markets = ["US", "KR"] if args.market == "both" else [args.market]
    full = {"generated": "offline", "note": OUR_SYSTEM_NET_NOTE, "results": {}}
    for m in markets:
        try:
            rep = run_market(m)
            rep["verdict"] = verdict(rep)
            full["results"][m] = rep
        except Exception as e:
            full["results"][m] = {"error": str(e)}
            log(f"[{m}] ERROR {e}")
    json.dump(full, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    # 콘솔 요약
    print("\n" + "=" * 60)
    for m, rep in full["results"].items():
        if "error" in rep:
            print(f"[{m}] ERROR: {rep['error']}")
            continue
        key = "net_fx_base" if m == "US" else "net"
        r = rep[key]
        print(f"\n### {m}  (유니버스 {rep['universe_n']}, {r['sample_period']}, 회전 {r['avg_turnover_pct']}%/월)")
        for name in ["TSMOM", "EW_bench", "INDEX_bench"]:
            mm = r[name]
            print(f"  {name:11s} CAGR={mm.get('cagr'):>6}% Sharpe={mm.get('sharpe'):>5} MDD={mm.get('mdd'):>6}% tot={mm.get('total_ret')}%")
        ex = r["excess_vs_ew"]
        print(f"  TSMOM vs EW: 초과 {ex['ann_excess_pp']:+}%/yr  t={ex['t']}  승월={ex['win_mo_pct']}%")
        if m == "US" and "net_fx_pess" in rep:
            pe = rep["net_fx_pess"]["TSMOM"]
            print(f"  (FX 0.20 비관: TSMOM CAGR={pe.get('cagr')}%)")
        print(f"  하락장 방어(EW 대비 초과pp):")
        for k, v in r["regime"].items():
            print(f"    {k:12s} strat={v['strat_pct']:+6}% ew={v['ew_pct']:+6}% idx={v['idx_pct']:+6}%  excess={v['excess_vs_ew_pp']:+}pp")
        print(f"  판정: {rep['verdict']}")
    print(f"\n리포트: {args.out}")


if __name__ == "__main__":
    main()
