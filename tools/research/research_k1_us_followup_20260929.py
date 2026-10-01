"""K1 US 후속 (2026-09-29) — (3) 내부자 군집 소유자 재키잉, (4) 13D/13G 회피 필터 겹침·검정.

연구 전용, 캐시만 사용(신규 SEC 호출 없음). 본체: research_k1_us_smartmoney_20260929.py (가격 행렬·벤치·통계 재사용).

================================ 사전등록 (결과 계산 전 고정) ================================
(3) 소유자 재키잉
- T3 캐시 zip의 REPORTINGOWNER.tsv에서 T3 매수 accession(Form 4, P/A)의 보고자 CIK 전부를 읽는다.
- 같은 accession에 함께 등장한 CIK는 한 그룹(union-find, 전 기간 공동신고 기준 — 신원 정의이며 수익 정보 없음).
- 군집 = 30일 내 서로 다른 소유자 그룹 ≥ 3. 신호일 = 3번째 그룹 도달 filing일, 같은 티커 90일 내 재신호 없음.
- 나머지(가격 일치 필터, $3·$1M/$5M, 60거래일 dedupe, H20/60/120, 비용 0.44%, 벤치, 통계)는 본체와 동일.
- 표: U5r(재키잉 군집 ∩ 바닥권 ≤0.35) = 사전등록 U5의 재키잉판, U5r_all(바닥권 조건 없음) = 비사전등록 진단.
- 보조 진단: U5r_do_all — 이사·임원(10% 주주 단독 제외) 매수만으로 센 군집.
(4) 회피 필터
- 플래그 A: 대상 티커에 '1회성 신고인 첫 13D'(U1 규칙의 첫 13D 중 신고인이 이전 다른 대상 0곳, PIT)가
  거래 신호일 이전 60일(캘린더, 신호일 당일 제외) 안에 있음. 가격 필터 없음(원 이벤트).
- 플래그 B: 대상 티커에 'U4 첫 13G'가 이전 60일 안에 있고 그 13G 신고일의 252일 위치 ≤ 0.35.
- 대상 거래: (a) US swing 후보 풀 — 가용 이력은 R1 재구성 풀(scored.csv, 2025-09~2026-09)과 라이브
  candidate_pool_all(2026-08~09)뿐이라 2012~2019 분할 불가 → 겹침 비율과 풀 내 net 차이만 기술 통계로 보고.
  (b) T1 I1 거래: T1_live_capacity trades_a(net 칼럼, 2017~) 와 jev_8k_t1 oos_scores is_i1=True(net 칼럼).
- '의미 있는 겹침' 문턱: 플래그 거래 비율 ≥ 3% 이고 플래그 거래 ≥ 20건. 미달이면 검정하지 않는다.
- 검정(문턱 통과 시): 인샘플 ≤2019 / OOS ≥2020. 지표 = 플래그 거래 net 평균 − 비플래그 net 평균(Welch t),
  제거 후 거래당 net 변화, 거래 수. 합격 = 인샘플 차이 < 0 이고 OOS 차이 < 0, OOS Welch t ≤ −2.0, OOS 플래그 ≥ 10건.
==========================================================================================
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_k1_us_smartmoney_20260929 as K  # noqa: E402

OUT = K.OUT
ROOT = K.ROOT


def log(*a):
    K.log("[followup]", *a)


# ------------------------------------------------------------------ (3)
def owners():
    fp = OUT / "t3_owners.csv.gz"
    if fp.exists():
        return pd.read_csv(fp)
    tr = pd.concat([pd.read_csv(f, usecols=["ACCESSION_NUMBER"]) for f in sorted((K.T3 / "trans").glob("*.csv.gz"))])
    accs = set(tr.ACCESSION_NUMBER)
    parts = []
    for zp in sorted((K.T3 / "zips").glob("*.zip")):
        z = zipfile.ZipFile(zp)
        nm = next(n for n in z.namelist() if n.upper().endswith("REPORTINGOWNER.TSV"))
        with z.open(nm) as fh:
            d = pd.read_csv(fh, sep="\t", dtype=str, usecols=["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNER_RELATIONSHIP"],
                            quoting=3, on_bad_lines="skip", encoding_errors="replace", keep_default_na=False)
        d = d[d.ACCESSION_NUMBER.isin(accs)]
        parts.append(d)
    o = pd.concat(parts).drop_duplicates(["ACCESSION_NUMBER", "RPTOWNERCIK"])
    o["cik"] = pd.to_numeric(o.RPTOWNERCIK, errors="coerce")
    rel = o.RPTOWNER_RELATIONSHIP.fillna("")
    o["do"] = (rel.str.contains("Director") | rel.str.contains("Officer")).astype(int)
    o = o.dropna(subset=["cik"])[["ACCESSION_NUMBER", "cik", "do"]]
    o.to_csv(fp, index=False, compression="gzip")
    return o


def groups(o):
    par = {}

    def f(x):
        while par.get(x, x) != x:
            par[x] = par.get(par[x], par[x]); x = par[x]
        return x
    for _, g in o.groupby("ACCESSION_NUMBER"):
        c = list(g.cik.astype(int))
        for x in c[1:]:
            a, b = f(c[0]), f(x)
            if a != b:
                par[b] = a
    return {int(c): f(int(c)) for c in o.cik.unique()}


def ev_rekey(do_only=False):
    fr = [pd.read_csv(f, dtype={"ticker": str}, parse_dates=["fdate"]) for f in sorted((K.T3 / "trans").glob("*.csv.gz"))]
    a = pd.concat(fr, ignore_index=True).drop_duplicates("ACCESSION_NUMBER")
    a = a[a.fdate >= "2010-01-01"]
    o = owners()
    gm = groups(o)
    o["grp"] = o.cik.astype(int).map(gm)
    if do_only:
        o = o[o["do"] == 1]
    og = o.groupby("ACCESSION_NUMBER").grp.apply(lambda s: frozenset(s))
    a = a[a.ACCESSION_NUMBER.isin(og.index)].copy()
    a["grps"] = a.ACCESSION_NUMBER.map(og)
    a = a.sort_values(["ticker", "fdate"])
    ev = []
    for t, g in a.groupby("ticker"):
        fd = g.fdate.values
        gs = g.grps.values
        last = None
        for i in range(len(g)):
            F = fd[i]
            m = (fd >= F - np.timedelta64(29, "D")) & (fd <= F)
            u = set()
            for s in gs[m]:
                u |= s
            if len(u) >= 3:
                if last is None or (F - last) / np.timedelta64(1, "D") > 90:
                    vw = g.value.values[m].sum() / max(g.bought.values[m].sum(), 1e-9)
                    ev.append((t, pd.Timestamp(F), vw))
                last = F
    info = {"accessions": int(len(a)), "owner_ciks": int(len(gm)), "owner_groups": int(len(set(gm.values())))}
    return pd.DataFrame(ev, columns=["ticker", "date", "vwap"]).drop_duplicates(["ticker", "date"]), info


def load_env():
    M = K.build_matrices()
    Rs = {H: K.fwd_ret(M, H) for H in K.HS}
    z = np.load(OUT / "size_bench.npz")
    Bs = {H: (z[f"B{H}"], z[f"E{H}"], z[f"R{H}"]) for H in K.HS}
    Bs5 = {H: (z[f"B5_{H}"], z[f"E5_{H}"], z[f"R5_{H}"]) for H in K.HS}
    return M, Rs, Bs, Bs5


def part3():
    M, Rs, Bs, Bs5 = load_env()
    res, meta = {}, {}
    for tag_do, do_only in (("", False), ("_do", True)):
        ev, info = ev_rekey(do_only)
        meta["info" + tag_do] = info
        meta["raw_events" + tag_do] = int(len(ev))
        ev = ev[ev.date >= "2012-01-01"]
        for dvmin, Bx, tag in ((1e6, Bs, ""), (5e6, Bs5, "_5M")):
            at, drop = K.attach(ev, M, Rs, Bx, dvmin)
            at = at[(at.vwap / at.close).between(0.67, 1.5)]
            at = K.dedupe60(at, M)
            sets = {f"U5r{tag_do}_all": at} if do_only else {"U5r": at[at.rpos <= 0.35], "U5r_all": at}
            for sn, sa in sets.items():
                k = sn + tag
                res[k] = {"drop": drop}
                for H in K.HS:
                    for b in ("sz", "iwm", "spy"):
                        res[k][f"H{H}_{b}"] = K.stats(sa, H, b)
                res[k]["verdict"] = K.verdict(res[k]["H60_sz"]) if sn == "U5r" else "진단"
                sa.to_csv(OUT / f"events_{k}.csv.gz", index=False, compression="gzip")
    (OUT / "results_rekey.json").write_text(json.dumps({"meta": meta, "results": res}, ensure_ascii=False, indent=1, default=str))
    for k, r in res.items():
        s, i = r["H60_sz"], r["H60_iwm"]
        log(f"{k:14s} n={s['n']} d={s['dates']} sz={s['mean_coh']*100:+.2f}%(t {s['nw_t']:.2f}) "
            f"oos={100*(s['oos_mean'] or 0):+.2f}%(t {s['oos_t'] or 0:.2f}) iwm={i['mean_coh']*100:+.2f}%(t {i['nw_t']:.2f}) "
            f"iwm_oos={100*(i['oos_mean'] or 0):+.2f}%(t {i['oos_t'] or 0:.2f}) med={s['median_evt']*100:+.2f}% "
            f"yrs {s['years_pos']}/{s['years']} top10={s['top10_share']} -> {r['verdict']}")
    log("meta", meta)


# ------------------------------------------------------------------ (4)
def flags():
    tmap = K.ticker_map()
    M = K.build_matrices()
    cal = pd.to_datetime(M["cal"])
    tix = {t: i for i, t in enumerate(M["tick"])}
    e1 = K.ev_13d(tmap)
    A = e1[(e1.filer_prior == 0) & e1.ticker.notna()][["ticker", "date"]].assign(kind="A")
    g = K.ev_13g(tmap).dropna(subset=["ticker"])
    rp = []
    for t, d in zip(g.ticker, g.date):
        j = tix.get(t)
        s = cal.searchsorted(pd.Timestamp(d), side="right") - 1
        rp.append(float(M["rpos"][s, j]) if (j is not None and s >= 0) else np.nan)
    g["rpos"] = rp
    B = g[g.rpos <= 0.35][["ticker", "date"]].assign(kind="B")
    F = pd.concat([A, B])
    F["date"] = pd.to_datetime(F.date)
    return F


def flag_trades(tr, F, dcol):
    by = {k: {t: np.sort(g.date.values) for t, g in F[F.kind == k].groupby("ticker")} for k in ("A", "B")}
    d = pd.to_datetime(tr[dcol]).values
    for k in ("A", "B"):
        out = np.zeros(len(tr), bool)
        for i, (t, x) in enumerate(zip(tr.ticker.values, d)):
            arr = by[k].get(t)
            if arr is None:
                continue
            lo = x - np.timedelta64(60, "D")
            j = np.searchsorted(arr, lo, side="left")
            out[i] = j < len(arr) and arr[j] < x
        tr[f"flag{k}"] = out
    tr["flagAB"] = tr.flagA | tr.flagB
    return tr


def welch(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 2 or len(b) < 2:
        return np.nan, np.nan
    d = a.mean() - b.mean()
    return d, d / np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))


def summarize(tr, name, ncol="net", split=True):
    out = {}
    for fk in ("flagA", "flagB", "flagAB"):
        r = {"n": int(len(tr)), "flagged": int(tr[fk].sum()), "flag_pct": round(100 * tr[fk].mean(), 2)}
        r["meaningful"] = bool(r["flag_pct"] >= 3 and r["flagged"] >= 20)
        per = {"all": tr}
        if split:
            per["IS"] = tr[tr.year <= 2019]
            per["OOS"] = tr[tr.year >= 2020]
        for p, x in per.items():
            fl, nf = x[x[fk]][ncol].dropna(), x[~x[fk]][ncol].dropna()
            dd, t = welch(fl, nf)
            r[p] = {"n": int(len(x)), "flagged": int(len(fl)), "mean_all": float(x[ncol].mean()) if len(x) else None,
                    "mean_flag": float(fl.mean()) if len(fl) else None, "mean_keep": float(nf.mean()) if len(nf) else None,
                    "diff": None if dd != dd else float(dd), "welch_t": None if t != t else float(t)}
            if len(x):
                r[p]["delta_per_trade"] = float(nf.mean() - x[ncol].mean()) if len(nf) else None
        if split and r["meaningful"]:
            i, o = r["IS"], r["OOS"]
            r["verdict"] = ("합격" if (i["diff"] is not None and i["diff"] < 0 and o["diff"] is not None and o["diff"] < 0
                                     and (o["welch_t"] or 0) <= -2.0 and o["flagged"] >= 10) else "기각")
        elif split:
            r["verdict"] = "검정 안 함(겹침 문턱 미달)"
        out[fk] = r
        log(name, fk, json.dumps({k: v for k, v in r.items() if k in ("n", "flagged", "flag_pct", "verdict")}, ensure_ascii=False))
    return out


def part4():
    F = flags()
    log("flags", F.kind.value_counts().to_dict())
    res = {}
    # (a) US swing 풀
    sc = pd.read_csv(ROOT / "data/analysis/model_R1_us_swing_s1_20260927/scored.csv")
    sc = flag_trades(sc, F, "session_date")
    res["swing_R1_pool"] = summarize(sc, "swing_R1_pool", "net", split=False)
    pk = pd.read_csv(ROOT / "data/analysis/model_R1_us_swing_s1_20260927/s1_top3_picks.csv")
    pk = flag_trades(pk, F, "session_date")
    res["swing_R1_top3"] = summarize(pk, "swing_R1_top3", "net", split=False)
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(f"file:{ROOT / 'data/analysis/us_swing_shadow.db'}?mode=ro", uri=True)) as c:
        lp = pd.read_sql("select session_date, ticker, in_pool from candidate_pool_all", c)
    lp = flag_trades(lp, F, "session_date")
    res["swing_live_pool"] = {fk: {"n": int(len(lp)), "flagged": int(lp[fk].sum()), "flag_pct": round(100 * lp[fk].mean(), 2)}
                              for fk in ("flagA", "flagB", "flagAB")}
    log("swing_live_pool", res["swing_live_pool"])
    # (b) T1
    ta = pd.read_csv(ROOT / "data/analysis/T1_live_capacity_20260929/trades_a.csv.gz")
    ta = flag_trades(ta, F, "date")
    res["T1_trades_a"] = summarize(ta, "T1_trades_a", "net")
    os_ = pd.read_csv(ROOT / "data/analysis/jev_8k_t1_20260929/oos_scores.csv.gz")
    i1 = os_[os_.is_i1 == True].copy()  # noqa: E712
    i1 = flag_trades(i1, F, "date")
    res["T1_oos_i1"] = summarize(i1, "T1_oos_i1", "net")
    for nm, d in (("T1_trades_a", ta), ("T1_oos_i1", i1)):
        res[nm]["years"] = [int(d.year.min()), int(d.year.max())]
    (OUT / "results_avoid.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    st = sys.argv[1] if len(sys.argv) > 1 else "all"
    if st in ("3", "all"):
        part3()
    if st in ("4", "all"):
        part4()
