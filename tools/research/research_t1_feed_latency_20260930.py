# -*- coding: utf-8 -*-
"""T1 실적치 수집 지연(BMO 누락) 진단 (2026-09-30, 연구 전용 — 라이브 코드·원장 무변경)

문제: tools/us_earnings_t1_shadow.py score(22:15 KST = 09:15 ET)가 yfinance get_earnings_dates로 실적치를 받는데
09-30 세션 후보 12 중 1건만 채점(CAG·FDS no_actual, CALM·JBL·NG·NBTX date_not_found), 09-29도 CCL 누락.
백테스트는 BMO·AMC 모두 발표 후 첫 시가 진입을 가정 → BMO가 구조적으로 빠지면 라이브 표본이 백테스트와 다르다.

서브커맨드 (연구 venv: C:/Users/Unknown/research_venv/Scripts/python)
  stake      라이브 규칙(a: 7슬롯×14만, 하루 1) 복제 sim에서 BMO 누락 시나리오별 OOS 성과
             (i) 전체 (ii) AMC만 (iii) BMO는 8-K 접수가 09:15/09:25 ET 이전일 때만 (iv) BMO 무작위 25/50/75% 누락(20시드)
  sechist    I1 BMO 이벤트의 8-K 2.02 접수 시각(ET) 분포 — 진실 정보가 공개된 시각의 상한(jev_8k match.csv 재사용)
  fhcomp     Finnhub 실적치 vs yfinance(EVT) 실적치 비교성: 일치율, |Δact|, Δsue1, 2026 임계에서 I1 선택 뒤집힘
  basis      yfinance Reported EPS 기준(조정 vs GAAP) 판별: 캐시된 EX-99 본문에서 그 숫자 주변 문맥
  rescore    오늘 누락분(CALM·FDS·JBL 등)을 Finnhub 실적치로 오프라인 재채점(원장 기록 없음)
  calendar   EVT 스냅샷 간 미확정 발표일 이동(발표일 정정) 빈도·일수 — date_not_found 원인 분리
  poll       다중 소스 폴링 1회(yfinance·Finnhub calendar/stock·Nasdaq). --day, --tickers. 전향 지연 측정용
  pollsum    poll 로그에서 소스별 첫 실적치 출현 시각 요약
키는 .env.live에서 dotenv로만 읽고 출력하지 않는다. SEC는 초당 8건 이하.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
sys.path.insert(0, str(ROOT / "tools"))
OUT = ROOT / "data" / "analysis" / "t1_feed_latency_20260930"
EVT = ROOT / "data" / "analysis" / "us_earnings_dates.jsonl"
JEV = ROOT / "data" / "analysis" / "jev_8k_t1_20260929"
MODEL = ROOT / "state" / "models" / "t1_earnings_m2.txt"
FEATS = ["sue1", "sue2", "sgn", "beats4", "nprev4", "dsue", "days_since", "ret20", "ret60", "vol20",
         "ldvol20", "lsize", "month_sin", "month_cos"]
THR26 = 0.02


def _key() -> str:
    from dotenv import dotenv_values
    return (dotenv_values(ROOT / ".env.live").get("FINNHUB_API_KEY") or "").strip()


def _get(url: str, hdr: dict | None = None, key: str = ""):
    req = urllib.request.Request(url, headers=hdr or {"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(type(exc).__name__ + ":" + str(exc)[:80].replace(key, "***") if key else str(exc)[:80])


def _save(name: str, obj) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


# ------------------------------------------------------------------ stake
def cmd_stake() -> dict:
    import research_T1_live_capacity_20260929 as lc
    import research_model_T1_earnings_20260927 as t1
    T = lc.load()
    cal, ti, W = t1.load_prices(); O = W["open"]; iw = ti["IWM"]
    m = pd.read_csv(JEV / "match.csv", parse_dates=["date"])
    m["acc_tod"] = pd.to_datetime(m.accept, errors="coerce")
    acc = {(r.ticker, r.date): r.acc_tod for r in m.itertuples()}
    T["acc"] = [acc.get((a, b)) for a, b in zip(T.ticker, T.date)]

    def run(TT, label, seeds=None):
        R, occ, C, _ = lc.sim(TT, 7, 140000, 1, O=O, ti=ti, iw=iw)
        mt = lc.metrics(R, C, occ, 7, TT)
        o = mt["OOS"]; nyr = o["n"] / 5.75            # OOS 2021-01 ~ 2026-09 ≈ 5.75년
        bmo = float((R[R.per == "OOS"].hour == "BMO").mean())
        return {"label": label, "oos_n": o["n"], "oos_n_per_yr": round(nyr, 1), "oos_net": o["net"],
                "oos_t_nw20": o["t_nw20"], "oos_annual_ret": o["annual_ret_mean"], "oos_by_year": o["by_year"],
                "oos_bmo_share_taken": round(bmo, 3), "is_net": mt["IS"]["net"], "is_annual_ret": mt["IS"]["annual_ret_mean"]}

    res = {"full": run(T, "전체(백테스트 가정)"), "amc_only": run(T[T.hour == "AMC"], "AMC만(BMO 전부 누락)")}
    for cut in ("09:15", "09:25"):
        hh, mm = map(int, cut.split(":"))
        ok = T.acc.notna() & [(a is not None and not pd.isna(a) and a.normalize() == d.normalize()
                               and (a.hour, a.minute) < (hh, mm)) for a, d in zip(T.acc, T.entry_date)]
        keep = (T.hour == "AMC") | ((T.hour == "BMO") & ok)
        r = run(T[keep], f"BMO는 8-K가 진입일 {cut} ET 전 접수된 건만(2019+ 매칭분, 미매칭 BMO는 누락 처리)")
        r["bmo_kept_frac_oos"] = round(float(ok[(T.hour == "BMO") & (T.year >= 2021)].mean()), 3)
        res[f"bmo_8k_before_{cut}"] = r
    for p in (0.25, 0.5, 0.75):
        runs = []
        for sd in range(20):
            rng = np.random.default_rng(sd)
            drop = (T.hour == "BMO") & (rng.random(len(T)) < p)
            runs.append(run(T[~drop], ""))
        res[f"bmo_drop_{int(p*100)}"] = {"label": f"BMO 무작위 {int(p*100)}% 누락(20시드 평균)",
                                         **{k: round(float(np.mean([x[k] for x in runs])), 4)
                                            for k in ("oos_n", "oos_n_per_yr", "oos_net", "oos_t_nw20", "oos_annual_ret",
                                                      "oos_bmo_share_taken")}}
    # I1 전체 BMO/AMC 몫 (슬롯 무제한 기준 참고치)
    Tn = T.copy()
    share = {}
    for nm, x in (("I1_all", Tn), ("I1_oos", Tn[Tn.year >= 2021])):
        g = x.groupby("hour").net.agg(["count", "mean", "sum"])
        share[nm] = {h: {"n": int(g.loc[h, "count"]), "net": round(float(g.loc[h, "mean"]), 4),
                         "pnl_share": round(float(g.loc[h, "sum"] / g["sum"].sum()), 3)} for h in g.index}
    res["i1_share"] = share
    _save("stake.json", res)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return res


# ------------------------------------------------------------------ sechist
def cmd_sechist() -> dict:
    m = pd.read_csv(JEV / "match.csv", parse_dates=["date"])
    tr = pd.read_csv(ROOT / "data/analysis/model_T1_impl_20260927/trades_I1.csv.gz", parse_dates=["date", "entry_date"])
    key = {(a, b): e for a, b, e in zip(tr.ticker, tr.date, tr.entry_date)}
    b = m[m.hour == "BMO"].copy()
    b["i1"] = [(a, d) in key for a, d in zip(b.ticker, b.date)]
    b["acc"] = pd.to_datetime(b.accept, errors="coerce")
    res = {"bmo_rows": int(len(b)), "status": b.status.value_counts().to_dict()}
    mm = b[b.status == "matched"].copy()
    same = mm[mm.acc.dt.normalize() == mm.date]
    tod = same.acc.dt.hour * 60 + same.acc.dt.minute
    cuts = {"<06:00": 360, "<07:00": 420, "<07:30": 450, "<08:00": 480, "<08:30": 510, "<09:00": 540,
            "<09:15": 555, "<09:25": 565, "<09:30": 570}
    res["matched_same_day"] = int(len(same))
    res["cum_share_by_cutoff"] = {k: round(float((tod < v).mean()), 3) for k, v in cuts.items()}
    res["i1_only"] = {k: round(float((tod[same.i1] < v).mean()), 3) for k, v in cuts.items()}
    res["i1_only_n"] = int(same.i1.sum())
    res["median_accept"] = str(same.acc.dt.strftime("%H:%M").sort_values().iloc[len(same) // 2])
    res["note"] = ("status 분모: matched만. after_cutoff(진입 09:30 뒤 접수)·no_8k는 별도 — "
                   "BMO 표본 중 8-K가 09:30 전에 없는 건은 어떤 공시 기반 소스로도 개장 전 확보 불가")
    _save("sechist.json", res)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return res


# ------------------------------------------------------------------ fhcomp
def _evt_confirmed() -> pd.DataFrame:
    d = pd.read_json(EVT, lines=True)
    c = d[d.confirmed & d.eps_act.notna() & d.eps_est.notna()].copy()
    c = c.sort_values("fetched_at").drop_duplicates(["ticker", "date"], keep="last")
    c["date"] = pd.to_datetime(c.date)
    return c[["ticker", "date", "hour", "eps_act", "eps_est"]].reset_index(drop=True)


def cmd_fhcomp(n_sym: int = 350) -> dict:
    k = _key()
    ev = _evt_confirmed()
    # (A) 캘린더(날짜 조인) — 무료 티어 깊이 약 1개월
    rows = []
    for a, b in (("2026-09-01", "2026-09-07"), ("2026-09-08", "2026-09-14"), ("2026-09-15", "2026-09-21"),
                 ("2026-09-22", "2026-09-30")):
        x = _get(f"https://finnhub.io/api/v1/calendar/earnings?from={a}&to={b}&token={k}", key=k)
        rows += x.get("earningsCalendar", []); time.sleep(1.1)
    fc = pd.DataFrame(rows)
    fc = fc[fc.epsActual.notna()].rename(columns={"symbol": "ticker"})
    fc["date"] = pd.to_datetime(fc.date)
    A = fc.merge(ev[ev.date >= "2026-08-25"], on="ticker", suffixes=("_fh", "_yf"))
    A = A[(A.date_fh - A.date_yf).abs().dt.days <= 1].copy()
    A["src"] = "calendar"
    A["hour"] = A["hour_yf"]
    A = A.rename(columns={"epsActual": "act_fh", "epsEstimate": "est_fh", "eps_act": "act_yf", "eps_est": "est_yf"})
    # (B) stock/earnings(최근 4분기) — 2026 I1 티커 + 무작위, 순서 조인 + 추정치 일관성 필터
    tr = pd.read_csv(ROOT / "data/analysis/model_T1_impl_20260927/trades_I1.csv.gz", parse_dates=["date"])
    t26 = list(dict.fromkeys(tr[tr.year >= 2025].ticker))
    rest = [t for t in ev[ev.date >= "2025-10-01"].ticker.unique() if t not in set(t26)]
    rs = list(np.random.default_rng(0).choice(rest, size=min(len(rest), max(0, n_sym - len(t26[:200]))), replace=False))
    syms = t26[:200] + rs
    brow = []
    cache_fp = OUT / "fh_stock_earnings_cache.json"
    cache = json.loads(cache_fp.read_text(encoding="utf-8")) if cache_fp.exists() else {}
    for i, s in enumerate(syms):
        if s in cache:
            x = cache[s]
        else:
            try:
                x = _get(f"https://finnhub.io/api/v1/stock/earnings?symbol={s}&token={k}", key=k)
            except RuntimeError:
                x = []
            cache[s] = x
            time.sleep(1.05)
        e = ev[(ev.ticker == s) & (ev.date >= "2025-06-01")].sort_values("date", ascending=False).head(len(x))
        for fr, er in zip(x, e.itertuples()):
            if fr.get("actual") is None:
                continue
            brow.append({"ticker": s, "date_yf": er.date, "hour": er.hour, "act_fh": fr["actual"], "est_fh": fr.get("estimate"),
                         "act_yf": er.eps_act, "est_yf": er.eps_est, "period_fh": fr.get("period"), "src": "stock_earnings"})
    cache_fp.write_text(json.dumps(cache), encoding="utf-8")
    B = pd.DataFrame(brow)
    B = B[(B.est_fh.notna()) & ((B.est_fh - B.est_yf).abs() <= np.maximum(0.1 * B.est_yf.abs(), 0.03))]  # 순서 오정렬 제거
    D = pd.concat([A[["ticker", "date_yf", "hour", "act_fh", "est_fh", "act_yf", "est_yf", "src"]], B], ignore_index=True)
    D = D.drop_duplicates(["ticker", "date_yf"], keep="first")
    D["dact"] = (D.act_fh - D.act_yf).round(4)
    s1 = lambda a, e: np.clip((a - e) / np.maximum(np.abs(e), 0.05), -5, 5)  # noqa: E731
    D["sue1_yf"] = s1(D.act_yf, D.est_yf)
    D["sue1_mix"] = s1(D.act_fh, D.est_yf)          # Finnhub 실적 + yf 추정
    D["sue1_fh"] = s1(D.act_fh, D.est_fh)           # Finnhub 실적 + Finnhub 추정
    D["dsue_mix"] = D.sue1_mix - D.sue1_yf
    D["dsue_fh"] = D.sue1_fh - D.sue1_yf
    D.to_csv(OUT / "fhcomp_pairs.csv", index=False)

    def summ(x):
        return {"n": int(len(x)), "act_exact": round(float((x.dact.abs() < 0.005).mean()), 3),
                "act_within_0.02": round(float((x.dact.abs() <= 0.02).mean()), 3),
                "dact_abs_p50_p90_p99": [round(float(v), 3) for v in x.dact.abs().quantile([.5, .9, .99])],
                "sign_flip_surprise_mix": round(float((np.sign(x.act_fh - x.est_yf) != np.sign(x.act_yf - x.est_yf)).mean()), 3),
                "dsue_mix_abs_p50_p90": [round(float(v), 3) for v in x.dsue_mix.abs().quantile([.5, .9])],
                "dsue_fh_abs_p50_p90": [round(float(v), 3) for v in x.dsue_fh.abs().quantile([.5, .9])],
                "big_mismatch_share(|dsue_mix|>0.2)": round(float((x.dsue_mix.abs() > 0.2).mean()), 3)}
    res = {"all": summ(D), "calendar_join": summ(D[D.src == "calendar"]), "stock_earnings_join": summ(D[D.src != "calendar"]),
           "bmo": summ(D[D.hour == "BMO"]), "amc": summ(D[D.hour == "AMC"]),
           "abs_est_ge_0.5": summ(D[D.est_yf.abs() >= 0.5]), "abs_est_lt_0.2": summ(D[D.est_yf.abs() < 0.2]),
           "worst": D.reindex(D.dact.abs().sort_values(ascending=False).index).head(12)[
               ["ticker", "date_yf", "act_yf", "act_fh", "est_yf", "est_fh"]].astype(str).values.tolist()}
    # (C) 선택 뒤집힘: 연구 build 테이블의 피처에서 sue 계열만 교체해 저장 모델로 재채점
    try:
        res["flip"] = _flip(D)
    except Exception as exc:  # noqa: BLE001
        res["flip"] = f"산출 실패: {type(exc).__name__}: {str(exc)[:120]}"
    _save("fhcomp.json", res)
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    return res


def _flip(D: pd.DataFrame) -> dict:
    import lightgbm as lgb
    import research_model_T1_earnings_20260927 as t1
    ev, _ = t1.load_events()
    cal, ti, W = t1.load_prices()
    tab, _ = t1.build(ev, cal, ti, W)
    tab["date"] = pd.to_datetime(tab.date)
    J = tab.merge(D.rename(columns={"date_yf": "date"}), on=["ticker", "date"], how="inner")
    if not len(J):
        return {"n": 0, "note": "build 테이블과 조인 0건(최근 이벤트는 가격 캐시 범위 밖일 수 있음)"}
    bst = lgb.Booster(model_file=str(MODEL))
    base = bst.predict(J[FEATS].values.astype(np.float32))
    out = {"n_joined": int(len(J))}
    for nm, act, est in (("mix", J.act_fh, J.est_yf), ("fh", J.act_fh, J.est_fh)):
        X = J[FEATS].copy()
        surp = act - est
        X["sue1"] = np.clip(surp / np.maximum(est.abs(), 0.05), -5, 5)
        X["sue2"] = np.clip(surp / J.close_a, -0.2, 0.2) if "close_a" in J else X["sue2"]
        X["sgn"] = np.sign(surp)
        X["dsue"] = J.dsue + (X.sue1 - J.sue1)
        p = bst.predict(X.values.astype(np.float32))
        sel0, sel1 = base >= THR26, p >= THR26
        out[nm] = {"sel_base": int(sel0.sum()), "sel_new": int(sel1.sum()), "flip_in": int((~sel0 & sel1).sum()),
                   "flip_out": int((sel0 & ~sel1).sum()), "flip_rate": round(float((sel0 != sel1).mean()), 4),
                   "score_abs_diff_p50_p90": [round(float(v), 5) for v in np.quantile(np.abs(p - base), [.5, .9])]}
    return out


# ------------------------------------------------------------------ basis
def cmd_basis(n: int = 400) -> dict:
    m = pd.read_csv(JEV / "match.csv", parse_dates=["date"])
    ev = _evt_confirmed()
    mm = m[(m.status == "matched") & m.acc.notna()].merge(ev, on=["ticker", "date"], suffixes=("", "_e"))
    mm = mm.sample(min(n, len(mm)), random_state=0)
    cnt = {"doc_found": 0, "num_found": 0, "near_adjusted": 0, "near_gaap_only": 0, "gaap_diluted_equals_yf": 0}
    ex = []
    for r in mm.itertuples():
        fp = JEV / "docs" / f"{r.acc}.txt"
        if not fp.exists():
            continue
        cnt["doc_found"] += 1
        txt = re.sub(r"\s+", " ", fp.read_text(encoding="utf-8", errors="ignore"))
        v = r.eps_act
        pats = {f"{v:.2f}", f"({abs(v):.2f})" if v < 0 else f"{v:.2f}"}
        hits = [mt.start() for p in pats for mt in re.finditer(re.escape("$" + p.strip("()")) + r"\b|" + re.escape(p) + r"\b", txt)]
        if not hits:
            continue
        cnt["num_found"] += 1
        adj = gaap = False
        for h in hits[:6]:
            w = txt[max(0, h - 220): h + 60].lower()
            if re.search(r"adjusted|non-gaap|excluding|core|comparable|normalized|pro forma", w):
                adj = True
            elif re.search(r"diluted|gaap|net income per|earnings per share", w):
                gaap = True
        cnt["near_adjusted"] += adj
        cnt["near_gaap_only"] += (gaap and not adj)
        if len(ex) < 8 and adj:
            ex.append([r.ticker, str(r.date.date()), v])
    res = {"sample": int(len(mm)), **cnt,
           "adjusted_share_of_found": round(cnt["near_adjusted"] / max(cnt["num_found"], 1), 3),
           "gaap_only_share_of_found": round(cnt["near_gaap_only"] / max(cnt["num_found"], 1), 3),
           "examples_adjusted": ex,
           "note": "문맥창(앞 220자) 휴리스틱. GAAP=조정 동일인 회사는 어느 쪽에도 잡힐 수 있다 — 방향 판별용"}
    _save("basis.json", res)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return res


# ------------------------------------------------------------------ rescore
def cmd_rescore(day: str, tickers: list[str]) -> dict:
    import lightgbm as lgb
    import us_earnings_t1_shadow as sh
    k = _key()
    cal = _get(f"https://finnhub.io/api/v1/calendar/earnings?from={day}&to={day}&token={k}", key=k).get("earningsCalendar", [])
    fcal = {r["symbol"]: r for r in cal}
    evt = {}
    with EVT.open(encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if r["ticker"] in tickers and r["date"] == day:
                if r["ticker"] not in evt or r["fetched_at"] > evt[r["ticker"]]["fetched_at"]:
                    evt[r["ticker"]] = r
    today = pd.Timestamp(day)
    bars = sh._bars(tickers, (today - pd.Timedelta(days=130)).strftime("%Y-%m-%d"))
    bst = lgb.Booster(model_file=str(MODEL))
    out = []
    for tk in tickers:
        f = fcal.get(tk)
        e = evt.get(tk)
        if f is None or f.get("epsActual") is None:
            out.append({"ticker": tk, "status": "finnhub_no_actual"}); continue
        px = bars.get(tk)
        rec = {"ticker": tk, "act_fh": f["epsActual"], "est_fh": f.get("epsEstimate"), "est_yf": (e or {}).get("eps_est")}
        for nm, est in (("mix", rec["est_yf"]), ("fh", rec["est_fh"])):
            if est is None or px is None:
                rec[nm] = None; continue
            ft = sh._features({"eps_act": float(f["epsActual"]), "eps_est": float(est), "date": day},
                              sh._history(tk), px, today)
            if ft is None:
                rec[nm] = "no_price_history"; continue
            sc = float(bst.predict(np.array([[ft[x] for x in FEATS]], dtype=np.float32))[0])
            rec[nm] = {"score": round(sc, 5), "sue1": round(ft["sue1"], 3), "tradable": ft["_close_a"] >= 3 and ft["_dvol20"] >= 1e6,
                       "liquid_5m": ft["_dvol20"] >= 5e6, "selected_I1": sc >= THR26}
        out.append(rec)
    _save(f"rescore_{day}.json", out)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return {"rows": out}


# ------------------------------------------------------------------ calendar
def cmd_calendar() -> dict:
    rows, lastf, alld = [], {}, []
    with EVT.open(encoding="utf-8") as fh:
        for line in fh:
            if '"fetched_at": "2026-' in line:
                r0 = json.loads(line)
                lastf[r0["ticker"]] = max(lastf.get(r0["ticker"], ""), r0["fetched_at"])
                alld.append((r0["ticker"], r0["date"], r0["fetched_at"]))
            if '"confirmed": false' in line and '"fetched_at": "2026-' in line:
                r = json.loads(line)
                if r["date"] >= "2026-09-01":
                    rows.append(r)
    d = pd.DataFrame(rows)
    d["date"] = pd.to_datetime(d.date)
    snaps = sorted(d.fetched_at.str[:10].unique())
    # 티커별 스냅샷 시계열: 연속 스냅샷 간 가장 가까운 미래 이벤트 날짜 변경
    shifts = []
    for tk, g in d.groupby("ticker"):
        g = g.sort_values("fetched_at")
        seq = g.groupby("fetched_at").date.min().sort_index()
        for (f0, d0), (f1, d1) in zip(seq.items(), list(seq.items())[1:]):
            if d0 != d1 and d0 >= pd.Timestamp(f1[:10]):        # 아직 오지 않은 예정일이 바뀐 경우만
                shifts.append({"ticker": tk, "from": str(d0.date()), "to": str(d1.date()), "days": int((d1 - d0).days),
                               "seen_at": f1})
    S = pd.DataFrame(shifts)
    n_tk = d.ticker.nunique()
    res = {"snapshots": snaps, "tickers_with_upcoming": int(n_tk), "date_shifts": int(len(S)),
           "shift_share": round(len(S) / max(n_tk, 1), 3),
           "shift_days_dist": S.days.value_counts().sort_index().to_dict() if len(S) else {},
           "abs_le1_share": round(float((S.days.abs() <= 1).mean()), 3) if len(S) else None,
           "examples": S.head(15).values.tolist() if len(S) else []}
    # 오늘/어제 스코어러 관점: 옛 스냅샷에만 남은 (ticker,date) 행 = 스테일 후보
    d["is_latest"] = d.fetched_at.values == d.ticker.map(lastf).values   # 확정 행 포함 티커별 최신 스냅샷
    L = pd.DataFrame([(t, pd.Timestamp(x)) for t, x, f in alld if f == lastf[t]], columns=["ticker", "date"]).drop_duplicates()
    stale = d[~d.is_latest].merge(L, on=["ticker", "date"], how="left", indicator=True)
    stale = stale[stale._merge == "left_only"].drop_duplicates(["ticker", "date"])
    res["stale_rows_(ticker,date)_not_in_latest"] = int(len(stale))
    res["stale_in_0930_0929"] = stale[stale.date.isin([pd.Timestamp("2026-09-29"), pd.Timestamp("2026-09-30")])][
        ["ticker", "date", "fetched_at"]].astype(str).values.tolist()
    _save("calendar.json", res)
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    return res


# ------------------------------------------------------------------ poll
def cmd_poll(day: str, tickers: list[str]) -> dict:
    import yfinance as yf
    k = _key()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rec = {"polled_utc": now, "day": day, "yf": {}, "fh_cal": {}, "fh_stock": {}, "nasdaq": {}}
    for t in tickers:
        try:
            ed = yf.Ticker(t.replace(".", "-")).get_earnings_dates(limit=6)
            hit = None
            for ts, r in ed.iterrows():
                if str(ts.date()) == day:
                    hit = {"est": None if pd.isna(r.get("EPS Estimate")) else float(r["EPS Estimate"]),
                           "act": None if pd.isna(r.get("Reported EPS")) else float(r["Reported EPS"])}
            rec["yf"][t] = hit if hit else "row_absent"
        except Exception as exc:  # noqa: BLE001
            rec["yf"][t] = f"err:{type(exc).__name__}"
    try:
        cal = _get(f"https://finnhub.io/api/v1/calendar/earnings?from={day}&to={day}&token={k}", key=k)
        m = {r["symbol"]: r for r in cal.get("earningsCalendar", [])}
        rec["fh_cal"] = {t: ({"act": m[t].get("epsActual"), "est": m[t].get("epsEstimate"), "hour": m[t].get("hour")}
                             if t in m else "row_absent") for t in tickers}
    except RuntimeError as exc:
        rec["fh_cal"] = str(exc)
    for t in tickers:
        try:
            x = _get(f"https://finnhub.io/api/v1/stock/earnings?symbol={t}&limit=1&token={k}", key=k)
            rec["fh_stock"][t] = {"act": x[0].get("actual"), "period": x[0].get("period")} if x else None
        except RuntimeError as exc:
            rec["fh_stock"][t] = str(exc)
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / f"poll_{day}.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(json.dumps(rec, ensure_ascii=False))
    return rec


def cmd_pollsum() -> dict:
    res = {}
    for fp in sorted(OUT.glob("*poll*.jsonl")):
        res[fp.name] = sum(1 for _ in fp.open(encoding="utf-8"))
    first = {}
    # multi_poll.jsonl(임시 루프 형식)
    fp = OUT / "multi_poll.jsonl"
    if fp.exists():
        for line in fp.open(encoding="utf-8"):
            r = json.loads(line)
            for t, v in (r.get("finnhub_cal") or {}).items():
                if v.get("epsActual") is not None:
                    first.setdefault(("finnhub_cal", r["day"], t), r["polled_utc"])
            for t, v in (r.get("nasdaq") or {}).items():
                if v.get("eps"):
                    first.setdefault(("nasdaq", r["day"], t), r["polled_utc"])
    fp = OUT / "yf_poll.jsonl"
    if fp.exists():
        for line in fp.open(encoding="utf-8"):
            r = json.loads(line)
            for row in r["rows"] if isinstance(r["rows"], list) else []:
                if row["act"] is not None and row["ts"][:10] in ("2026-09-29", "2026-09-30"):
                    first.setdefault(("yfinance", row["ts"][:10], r["ticker"]), r["polled_utc"])
    res["first_actual_seen_utc"] = {"|".join(k): v for k, v in sorted(first.items())}
    _save("pollsum.json", res)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["stake", "sechist", "fhcomp", "basis", "rescore", "calendar", "poll", "pollsum"])
    ap.add_argument("--day", default=None)
    ap.add_argument("--tickers", default="")
    a = ap.parse_args()
    tk = [t for t in a.tickers.split(",") if t]
    if a.cmd == "stake":
        cmd_stake()
    elif a.cmd == "sechist":
        cmd_sechist()
    elif a.cmd == "fhcomp":
        cmd_fhcomp()
    elif a.cmd == "basis":
        cmd_basis()
    elif a.cmd == "rescore":
        cmd_rescore(a.day, tk)
    elif a.cmd == "calendar":
        cmd_calendar()
    elif a.cmd == "poll":
        cmd_poll(a.day, tk)
    else:
        cmd_pollsum()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
