"""T1 공급 확장 진단 (2026-09-30, 사전등록 — 결과 계산 전 고정)

질문: 라이브 계약(7슬롯×14만, 하루 1, 최고점, LOO 매수/MOO 매도, 20세션)은 I1 선정 연 270~750건 중
연 55~65건만 탄다. 공급(탈 수 있는 후보)을 엣지 손상 없이 얼마나 늘릴 수 있고, 비용은 얼마인가.

[입력 — 재학습 없음]
- I1 = data/analysis/model_T1_impl_20260927/trades_I1.csv.gz (M2_lgbm_reg OOS 점수 ≥ 전년도까지 OOS 90분위).
- D용 전체 점수 = data/analysis/model_T1_robust_20260927/scores_A_open_H20.csv.gz. 임계는 impl.thresholds
  논리를 분위 인자로 재구현(2017 burn-in 200 동일). q=90이 trades_I1 4,580건을 재현하는지 먼저 확인.
- net = gA20 − 0.0044 (gA20 = open[e]→open[e+20] 종목 − IWM 동일 창). FX 1,380 고정, 수정주가.
- 가격 행렬 로드 없음(교체 모드 안 씀).

[통계]
- t = 진입 세션 평균 net 시계열 NW lag 20(t1.nw_t) — 선행 보고서와 동일 단위(날짜 클러스터).
- IS = 진입 2017–2020, OOS = 2021–2026. 판정은 OOS 중심, IS는 방향 확인.
- KRW 초과 = Σ(notional × net). 연환산 = OOS 합 / OOS 기간(2021-01-01 ~ 마지막 진입일, 년).

[재현 게이트] 자체 sim이 선행 보고서 a(IS 230/+1.47%/t1.50, OOS 333/+2.54%/t3.17)를 재현해야 이후 비교 유효.
  sim은 라이브 코드 그대로: 세션당 1회 게이트(보유 ≥ 슬롯 or 사용액+SLOT > 예산), 점수순, 보유 티커 건너뜀,
  qty=floor(SLOT/(close_a×FX)), qty<1 건너뜀, 만기 세션까지 OPEN. 후보마다 태그 부여.

[A. 워터폴] I1 → dvol20 ≥ $5M → 1주가 ≤ 슬롯(close_a×FX ≤ 14만) → 보유 티커 스킵 → 슬롯 가득(게이트 닫힘)
  → 하루 1건 상한. 각 단계 연평균 건수와 "제거된 그룹"의 net/건·t(IS/OOS).

[B. 가격 상한]
 (i) 슬롯 14만 유지, 1주가 14만 초과면 1주를 28만/42만까지 허용(예산 100만 고정, 후보별 예산 검사 used+notional
     ≤ 100만 — 라이브 코드와의 이탈로 표기). 사전 기대: 선행 보고서에서 >$101 종목 OOS −0.98%/IS +2.47%
     (부호 역전)이라 통과 가능성 낮음.
 (ii) 분수주: 선행 보고서 g 변형(OOS +2.02% vs 정수주 +2.54%) 재계산. 저장소 kis_api에 소수점 TR 없음
     (US_ORD_DVSN_ALLOWED buy {00,32,34}) — KIS 지원 여부는 외부 확인 필요, 동시호가(LOO/MOO)와 병행 불가 가능성.

[C. 유동성 밴드 $1–5M] I1 ∩ 1M ≤ dvol20 < 5M. net/건·t·연도별 부호. 비용 스트레스(왕복):
  0.44% 평탄 / 0.44+0.33×95bp / 0.44+0.33×195bp / 0.44+1×95bp / 0.44+1×195bp
  (us_slippage_by_adv_20260928: <$5M I1 선택 종목 스프레드 중앙 09:45 95bp, 09:31 195bp).
  라이브는 LOO/MOO(동시호가) → 이론상 스프레드 비용 ≈ 0이지만 실측 0건. 스트레스는 비동시호가 대안 비용.
  라이브 sim: 풀 dvol ≥ $1M, 밴드 건만 스트레스 비용 적용. 생존편향: 유니버스 = 현재 상장 1,592 티커,
  가격 결합 탈락 비율만 정량(상폐 종목은 원천 부재 — 정량 불가).

[D. 임계 완화] 85·80분위. 한계 그룹(q ≤ 점수 < 90분위 임계) OOS net/건·t, dvol≥5M 한정판 병기, 라이브 sim.

[E. 결합 계약] 예산 100/200/300만. 격자: (슬롯수×14만: 7/14/21) × 하루 {1,2,3}, 그리고 (7슬롯 × 14/28/42만,
  하루 1). 후보별 게이트(이탈 표기). 풀 = 기본(+ 통과 레버만). 지표: OOS KRW/년, 예산 대비 %, t, 양수 연도,
  경로 운 밴드(세션 내 순서 무작위 20시드 — 랭킹을 파괴하므로 비관 밴드).

[권고 기준 — 다중검정(레버 약 15개 변형)]
  레버 채택 = OOS 한계 net > 0 AND 한계 t ≥ 2.0 AND 라이브 sim OOS KRW/년 ≥ a AND OOS t(건별) ≥ 2.0
  AND IS 한계 net 부호 비역전(> 0). 한계 = (Σpnl_v − Σpnl_a)/(n_v − n_a)(KRW 가중)와
  집합 차(v에만 있는 (ticker,e)) net/t 둘 다 보고, 판정은 집합 차 t와 KRW 증분.
  예산 확대(E)는 자본 증가라 "레버"가 아니라 운영자 배분 결정 재료로 보고(권고 아님).
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
import research_model_T1_earnings_20260927 as t1  # noqa: E402

OUT = ROOT / "data" / "analysis" / "T1_supply_20260930"
FEE = 0.0044; FX = 1380.0; H = 20; BURN = 200
SCORE = "M2_lgbm_reg"


def nwt(x):
    return round(float(t1.nw_t(np.asarray(x, float), 20)), 2)


def grp(x, col="net"):
    if not len(x):
        return {"n": 0}
    s = x.groupby("e")[col].mean()
    return {"n": int(len(x)), "net": round(float(x[col].mean()), 4), "t": nwt(s.values)}


def split(x, col="net"):
    return {p: grp(x[x.per == p], col) for p in ("IS", "OOS")}


def prep(T):
    T = T.copy()
    T["net"] = T.gA20 - FEE
    T["per"] = np.where(T.year <= 2020, "IS", "OOS")
    return T


def load_I1():
    return prep(pd.read_csv(ROOT / "data/analysis/model_T1_impl_20260927/trades_I1.csv.gz",
                            parse_dates=["date", "entry_date"], dtype={"ticker": str}))


def thresholds_q(P, q):
    s = P[SCORE].values; e = P.e.values; yr = P.year.values
    thr = np.full(len(P), np.nan)
    for Y in range(2018, 2027):
        thr[yr == Y] = np.percentile(s[yr < Y], q)
    m17 = np.where(yr == 2017)[0]
    order = m17[np.argsort(e[m17], kind="stable")]
    ss = s[order]; ee = e[order]
    for sess in np.unique(ee):
        prior = ss[ee < sess]
        if len(prior) >= BURN:
            thr[order[ee == sess]] = np.percentile(prior, q)
    return thr


def sim(C, slots, slot_krw, max_new, budget, price_cap_krw=None, frac=False, order="score", seed=0,
        per_cand=False, cost_col="net"):
    """C = 후보 풀. 반환 taken DataFrame(notional 포함), tags Series(C 인덱스)."""
    C = C.copy()
    if order == "random":
        C["_r"] = np.random.default_rng(seed).random(len(C)); C = C.sort_values(["e", "_r"])
    else:
        C = C.sort_values(["e", SCORE, "ticker"], ascending=[True, False, True])
    tags = pd.Series("", index=C.index, dtype=object)
    groups = {s: g for s, g in C.groupby("e", sort=True)}
    pos = []; taken = []
    for s in range(int(C.e.min()), int(C.e.max()) + 1):
        pos = [p for p in pos if p[2] >= s]
        g = groups.get(s)
        if g is None:
            continue
        used = sum(p[3] for p in pos)
        held = {p[0] for p in pos}
        full = len(pos) >= slots or used + slot_krw > budget
        n_new = 0
        for idx, tk, px in zip(g.index, g.ticker.values, g.close_a.values):
            if tk in held:
                tags[idx] = "held"; continue
            unit = px * FX
            q = slot_krw / unit
            q = q if frac else math.floor(q)
            if not frac and q < 1:
                if price_cap_krw is not None and unit <= price_cap_krw:
                    q = 1
                else:
                    tags[idx] = "price"; continue
            notional = q * unit
            if per_cand:
                if len(pos) >= slots or used + notional > budget:
                    tags[idx] = "full"; continue
            elif full:
                tags[idx] = "full"; continue
            if n_new >= max_new:
                tags[idx] = "daycap"; continue
            pos.append((tk, s, s + H, notional)); used += notional; held.add(tk)
            taken.append((idx, notional)); tags[idx] = "taken"; n_new += 1
    tk = pd.DataFrame(taken, columns=["idx", "notional"]).set_index("idx")
    R = C.loc[tk.index].copy(); R["notional"] = tk.notional.values
    R["pnl"] = R.notional * R[cost_col]
    return R, tags


def span_years(R):
    x = R[R.per == "OOS"]
    return (x.entry_date.max() - pd.Timestamp("2021-01-01")).days / 365.25


def met(R, budget, oos_years):
    out = {}
    for p in ("IS", "OOS"):
        x = R[R.per == p]
        if not len(x):
            continue
        yrs = oos_years if p == "OOS" else 4.0
        yk = x.groupby("year").pnl.sum()
        out[p] = {"n": int(len(x)), "n_yr": round(len(x) / yrs, 1), "net": round(float(x.net.mean()), 4),
                  "t": nwt(x.groupby("e").net.mean().values),
                  "krw_total": round(float(x.pnl.sum())), "krw_yr": round(float(x.pnl.sum() / yrs)),
                  "ret_on_budget_yr": round(float(x.pnl.sum() / yrs / budget), 4),
                  "yrs_pos": f"{int((yk > 0).sum())}/{len(yk)}",
                  "krw_by_year": {int(k): round(float(v)) for k, v in yk.items()},
                  "avg_notional": round(float(x.notional.mean()))}
    return out


def marginal(Rv, Ra, oos_years):
    out = {}
    ka = set(zip(Ra.ticker, Ra.e))
    for p in ("IS", "OOS"):
        v = Rv[Rv.per == p]; a = Ra[Ra.per == p]
        dn = len(v) - len(a)
        extra = v[[(t, e) not in ka for t, e in zip(v.ticker, v.e)]]
        yrs = oos_years if p == "OOS" else 4.0
        out[p] = {"d_n": dn, "d_krw_yr": round(float((v.pnl.sum() - a.pnl.sum()) / yrs)),
                  "krw_per_added_trade": round(float((v.pnl.sum() - a.pnl.sum()) / dn)) if dn else None,
                  "setdiff": grp(extra)}
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    Res = {}
    T = load_I1()
    L = T[T.dvol20 >= 5e6]
    oy = span_years(T)
    Res["oos_years"] = round(oy, 3)

    # ---------------- 재현 게이트
    Ra, tags = sim(L, 7, 140000, 1, 1_000_000)
    Res["a"] = met(Ra, 1_000_000, oy)
    print("A replica", json.dumps({p: {k: Res['a'][p][k] for k in ('n', 'net', 't', 'krw_total')} for p in ('IS', 'OOS')}))

    # ---------------- A 워터폴
    nyr = {"IS": 4.0, "OOS": oy}
    W = []
    def row(name, kept, removed):
        r = {"stage": name}
        for p in ("IS", "OOS"):
            k = kept[kept.per == p]; rm = removed[removed.per == p] if removed is not None else None
            r[p] = {"kept_n_yr": round(len(k) / nyr[p], 1), "kept": grp(k),
                    "removed": grp(rm) if rm is not None else None}
        W.append(r)
    row("I1 전체", T, None)
    row("dvol20 ≥ $5M", L, T[T.dvol20 < 5e6])
    pf = L.close_a * FX <= 140000
    L2 = L[pf]
    row("1주 ≤ 14만", L2, L[~pf])
    tg = tags.reindex(L2.index)
    assert (tags.reindex(L[~pf].index) == "price").all()
    row("보유 티커 스킵", L2[tg != "held"], L2[tg == "held"])
    r2 = L2[(tg != "held") & (tg != "full")]
    row("슬롯 가득(게이트)", r2, L2[tg == "full"])
    row("하루 1건 상한 → 체결", L2[tg == "taken"], L2[tg == "daycap"])
    Res["A_waterfall"] = W
    # 제거 그룹 연도별(OOS 판단 보조)
    Res["A_removed_by_year"] = {nm: {int(k): round(float(v), 4) for k, v in x.groupby("year").net.mean().items()}
                                for nm, x in (("full", L2[tg == "full"]), ("daycap", L2[tg == "daycap"]),
                                              ("price", L[~pf]), ("illiquid", T[T.dvol20 < 5e6]))}

    # ---------------- B 가격 상한
    B = {}
    for cap in (280000, 420000):
        Rv, _ = sim(L, 7, 140000, 1, 1_000_000, price_cap_krw=cap, per_cand=True)
        B[f"cap_{cap}"] = {"m": met(Rv, 1_000_000, oy), "marg": marginal(Rv, Ra, oy)}
    for cap_lo, cap_hi in ((140000, 280000), (280000, 420000)):
        g = L[(L.close_a * FX > cap_lo) & (L.close_a * FX <= cap_hi)]
        B[f"pool_{cap_lo}_{cap_hi}"] = split(g)
    Rf, _ = sim(L, 7, 140000, 1, 1_000_000, frac=True)
    B["frac"] = {"m": met(Rf, 1_000_000, oy), "marg": marginal(Rf, Ra, oy)}
    Res["B"] = B

    # ---------------- C 유동성 밴드
    band = T[(T.dvol20 >= 1e6) & (T.dvol20 < 5e6)].copy()
    costs = {"flat044": 0.0044, "s0945_033": 0.0044 + 0.33 * 0.0095, "s0931_033": 0.0044 + 0.33 * 0.0195,
             "s0945_1": 0.0044 + 0.0095, "s0931_1": 0.0044 + 0.0195}
    Cc = {"pool_all_years": {}, "sub1M": split(T[T.dvol20 < 1e6])}
    for nm, c in costs.items():
        band["nc"] = band.gA20 - c
        Cc["pool_all_years"][nm] = {"round_trip": round(c, 4), **split(band, "nc"),
                                    "by_year": {int(k): round(float(v), 4) for k, v in band.groupby("year").nc.mean().items()}}
    Cc["band_share_of_I1_by_year"] = {int(k): round(float(v), 3) for k, v in
                                      T.assign(b=(T.dvol20 >= 1e6) & (T.dvol20 < 5e6)).groupby("year").b.mean().items()}
    L1 = T[T.dvol20 >= 1e6].copy()
    isb = (L1.dvol20 < 5e6)
    for nm in ("flat044", "s0945_033", "s0931_033", "s0931_1"):
        L1["net"] = L1.gA20 - np.where(isb, costs[nm], FEE)
        Rv, _ = sim(L1, 7, 140000, 1, 1_000_000)
        Cc[f"live_{nm}"] = {"m": met(Rv, 1_000_000, oy), "marg": marginal(Rv, Ra, oy),
                            "band_share_taken": round(float((Rv.dvol20 < 5e6).mean()), 3)}
    Cc["survivorship"] = {"universe_tickers": 1592, "events_since_2013_06": 58993, "after_price_join": 58249,
                          "price_join_drop_share": round(1 - 58249 / 58993, 4),
                          "note": "상폐 종목은 유니버스에 없음 — 정량 불가"}
    Res["C"] = Cc

    # ---------------- D 임계 완화
    P = pd.read_csv(ROOT / "data/analysis/model_T1_robust_20260927/scores_A_open_H20.csv.gz",
                    usecols=["ticker", "date", "entry_date", "e", "year", "close_a", "dvol20", "gA20", SCORE],
                    dtype={"ticker": str}, parse_dates=["entry_date"])
    P = P[P.gA20.notna()].reset_index(drop=True)
    P = prep(P)
    P["thr90"] = thresholds_q(P, 90)
    q90 = P[P.thr90.notna() & (P[SCORE] >= P.thr90)]
    k90 = set(zip(q90.ticker, q90.e)); kT = set(zip(T.ticker, T.e))
    Dd = {"repro_q90_vs_I1": {"n_q90": len(q90), "n_I1": len(T), "sym_diff": len(k90 ^ kT)}}
    for q in (85, 80):
        th = thresholds_q(P, q)
        sel = P[~np.isnan(th) & (P[SCORE] >= th)]
        m = sel[~((P.loc[sel.index, "thr90"].notna()) & (sel[SCORE] >= P.loc[sel.index, "thr90"]))]
        mL = m[m.dvol20 >= 5e6]
        Dd[f"q{q}"] = {"n_sel": len(sel), "marginal_all": split(m), "marginal_liquid": split(mL),
                       "marginal_liquid_by_year": {int(k): round(float(v), 4) for k, v in mL.groupby("year").net.mean().items()}}
        pool = sel[sel.dvol20 >= 5e6]
        Rv, _ = sim(pool, 7, 140000, 1, 1_000_000)
        Dd[f"q{q}"]["live"] = {"m": met(Rv, 1_000_000, oy), "marg": marginal(Rv, Ra, oy)}
    Res["D"] = Dd

    # ---------------- E 예산 격자
    E = {}
    grid = []
    for bud, sl in ((1_000_000, 7), (2_000_000, 14), (3_000_000, 21)):
        for mn in (1, 2, 3):
            grid.append((f"B{bud//10000}_{sl}x14_n{mn}", bud, sl, 140000, mn))
    grid += [("B200_7x28_n1", 2_000_000, 7, 280000, 1), ("B300_7x42_n1", 3_000_000, 7, 420000, 1)]
    for nm, bud, sl, kr, mn in grid:
        Rv, _ = sim(L, sl, kr, mn, bud, per_cand=True)
        E[nm] = {"m": met(Rv, bud, oy), "marg": marginal(Rv, Ra, oy)}
        print(nm, E[nm]["m"]["OOS"]["n"], E[nm]["m"]["OOS"]["net"], E[nm]["m"]["OOS"]["t"], E[nm]["m"]["OOS"]["krw_yr"])
    Res["E"] = E
    # 경로 밴드: a + 예산별 OOS KRW/년 최대 후보(사후 선택 표기)
    band_cfg = {"a": (1_000_000, 7, 140000, 1, False)}
    for bud in (2_000_000, 3_000_000):
        cand = {k: v for k, v in E.items() if k.startswith(f"B{bud//10000}_")}
        best = max(cand, key=lambda k: cand[k]["m"]["OOS"]["krw_yr"])
        g = [x for x in grid if x[0] == best][0]
        band_cfg[best] = (g[1], g[2], g[3], g[4], True)
    PB = {}
    for nm, (bud, sl, kr, mn, pc) in band_cfg.items():
        vals = []
        for sd in range(20):
            Rv, _ = sim(L, sl, kr, mn, bud, order="random", seed=sd, per_cand=pc)
            mm = met(Rv, bud, oy)["OOS"]
            vals.append((mm["krw_yr"], mm["net"], mm["t"]))
        v = np.array(vals)
        PB[nm] = {"krw_yr_min_med_max": [int(v[:, 0].min()), int(np.median(v[:, 0])), int(v[:, 0].max())],
                  "net_min_med_max": [round(float(v[:, 1].min()), 4), round(float(np.median(v[:, 1])), 4), round(float(v[:, 1].max()), 4)],
                  "t_med": round(float(np.median(v[:, 2])), 2)}
    Res["E_path_band_random_order"] = PB

    (OUT / "results.json").write_text(json.dumps(Res, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    print(json.dumps(Res, indent=1, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
