"""T1 라이브 용량 진단 (2026-09-29, 사전등록 — 결과 계산 전 고정)

질문: 라이브 근사(+0.84%/건, t 1.45)가 I1 전체(+1.50%/건)보다 약한 이유는 선택(어떤 픽을 타나)인가
타이밍(큰 승자가 올 때 슬롯이 차 있나)인가. 같은 100만원·20세션 보유·비용 0.44%에서 더 나은 라이브 규칙이 있나.

[라이브 복제: tools/us_earnings_t1_live.py cmd_buy]
- 후보 = 그 세션 I1 선정(score ≥ 연도 임계; trades_I1.csv.gz) ∩ dvol20 ≥ $5M.
- 게이트: 보유 수 ≥ MAX_SLOTS 또는 사용액 + SLOT > BUDGET 이면 그 세션 매수 없음(한 번만 검사 →
  MAX_NEW>1이면 슬롯 초과 가능, 코드 그대로 복제).
- 점수 내림차순. 이미 보유 티커 건너뜀. qty = floor(SLOT / (close_a × FX)), qty<1 건너뜀. MAX_NEW개까지.
- 청산: 진입 후 20번째 세션 시가(MOO). position 상태는 다음 아침 reconcile까지 OPEN → 만기 세션에도
  슬롯·보유 티커로 잡힌다(즉 슬롯은 만기 다음 세션부터 빈다).
- FX = 1,380 고정(현재 수준). close_a는 수정주가(분할·배당 조정) → 과거 1주 가격이 실제와 다를 수 있음(한계).
- 달러 현금 부족·LOO 미체결·다른 전략 보유 티커는 모델링 안 함.
[수익] 건별 net = gA20 − 0.0044 (open[e]→open[e+20] − IWM 동일 창). 자본가중 연수익 = Σ(notional×net)/100만,
  notional = qty×close_a×FX(정수주) 또는 SLOT(분수주). 판정 t = 진입 세션 평균 net 시계열 NW lag 20.
[변형]
  a  현행: 7슬롯×14만, 하루 1, 최고점
  b  7×14만, 하루 2
  c  10×10만, 하루 1 / c2 하루 2
  d  14×7만, 하루 1 / d2 하루 2
  e  a와 같되 세션 내 순서 무작위(선착순 대용, 20시드 평균)
  f  a와 같되 슬롯이 차면 보유 중 최저점 포지션을 새 픽(점수 더 높을 때만)으로 교체(교체 시 청산 비용 추가 0.44%)
  g  a·c·d를 분수주(정수 반올림 없음)로 → 정수주 효과
  기준선: I1 전체 / I1∩유동 / 유동 세션당 1위(슬롯 무제한)
[구간] IS = 진입 2017–2020, OOS = 2021–2026.
[권고 기준] 변형이 a보다 OOS 자본가중 연수익이 높고 OOS NW t가 a 이상, IS에서도 연수익 방향 일치할 때만
  env 권고(운영자 승인 필요). 아니면 현행 유지.
[진단] 갭 분해: I1 전체 → 유동 필터 → 세션 1위 선택 → 슬롯 제약. 타이밍: a에서 슬롯 가득으로 못 탄
  세션 1위 픽의 net vs 탄 픽. 상위 10% 승자(유동 후보 중 gA20 상위 10%) 놓친 비율.
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

OUT = ROOT / "data" / "analysis" / "T1_live_capacity_20260929"
FEE = 0.0044; FX = 1380.0; BUDGET = 1_000_000; H = 20


def load():
    T = pd.read_csv(ROOT / "data/analysis/model_T1_impl_20260927/trades_I1.csv.gz", parse_dates=["date", "entry_date"])
    T["net"] = T.gA20 - FEE
    T["per"] = np.where(T.year <= 2020, "IS", "OOS")
    return T


def sim(T, slots, slot_krw, max_new, order="score", replace=False, frac=False, seed=0, O=None, ti=None, iw=None):
    C = T[T.dvol20 >= 5e6].copy()
    if order == "random":
        C["_r"] = np.random.default_rng(seed).random(len(C)); C = C.sort_values(["e", "_r"])
    else:
        C = C.sort_values(["e", "M2_lgbm_reg", "ticker"], ascending=[True, False, True])
    groups = {s: g for s, g in C.groupby("e", sort=True)}
    sess_all = np.arange(C.e.min(), C.e.max() + H + 1)
    pos = []  # dict(ticker, e, x, score, notional)
    taken, repl_rows, occ = [], [], []
    skip_full = []
    for s in sess_all:
        pos = [p for p in pos if p["x"] >= s]           # 만기 세션까지 OPEN
        occ.append(len([p for p in pos if p["x"] > s or True]))
        g = groups.get(s)
        if g is None:
            continue
        used = sum(p["notional"] for p in pos)
        full = len(pos) >= slots or used + slot_krw > BUDGET
        if full and not replace:
            skip_full.append(g.index[0]); continue
        held = {p["ticker"] for p in pos}
        n_new = 0
        for idx, r in g.iterrows():
            if r.ticker in held:
                continue
            q = slot_krw / (r.close_a * FX)
            q = q if frac else math.floor(q)
            if q < (1e-9 if frac else 1):
                continue
            if full:  # replace mode
                live = [p for p in pos if p["x"] > s]
                if not live:
                    break
                w = min(live, key=lambda p: p["score"])
                if r.M2_lgbm_reg <= w["score"]:
                    break
                # close weakest at open[s]
                ro = O[ti[w["ticker"]], s] / O[ti[w["ticker"]], w["e"]] - 1
                rb = O[iw, s] / O[iw, w["e"]] - 1
                repl_rows.append({"idx": w["idx"], "net_repl": ro - rb - FEE - FEE, "held": s - w["e"]})
                pos.remove(w)
            pos.append({"ticker": r.ticker, "e": s, "x": s + H, "score": r.M2_lgbm_reg,
                        "notional": q * r.close_a * FX, "idx": idx})
            taken.append((idx, q * r.close_a * FX)); n_new += 1
            if n_new >= max_new:
                break
    tk = pd.DataFrame(taken, columns=["idx", "notional"]).set_index("idx")
    R = T.loc[tk.index].copy(); R["notional"] = tk.notional
    if repl_rows:
        rp = pd.DataFrame(repl_rows).drop_duplicates("idx", keep="last").set_index("idx")
        R.loc[rp.index, "net"] = rp.net_repl
    return R, np.array(occ[:len(sess_all) - H]), C, skip_full


def metrics(R, C, occ, slots, T):
    out = {}
    for p in ("IS", "OOS", "ALL"):
        x = R if p == "ALL" else R[R.per == p]
        c = C if p == "ALL" else C[C.per == p]
        if not len(x):
            continue
        yr = x.groupby("year").apply(lambda g: float((g.notional * g.net).sum() / BUDGET))
        sess = x.groupby("e").net.mean()
        top = c[c.gA20 >= c.gA20.quantile(0.9)]
        out[p] = {"n": int(len(x)), "net": round(float(x.net.mean()), 4),
                  "t_nw20": round(t1.nw_t(sess.values, 20), 2),
                  "annual_ret_mean": round(float(yr.mean()), 4), "by_year": {int(k): round(v, 4) for k, v in yr.items()},
                  "yrs_pos": f"{int((yr > 0).sum())}/{len(yr)}",
                  "avg_notional": round(float(x.notional.mean())),
                  "top10_missed": round(float(1 - top.index.isin(x.index).mean()), 3)}
    out["slot_util"] = round(float(occ.mean() / slots), 3)
    return out


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    T = load()
    cal, ti, W = t1.load_prices(); O = W["open"]; iw = ti["IWM"]
    Res = {}
    # baselines
    L = T[T.dvol20 >= 5e6]
    top1 = L.sort_values(["e", "M2_lgbm_reg"], ascending=[True, False]).groupby("e").head(1)
    base = {}
    for nm, x in (("I1_all", T), ("I1_liquid", L), ("liquid_top1_unlimited", top1), ("I1_illiquid", T[T.dvol20 < 5e6])):
        base[nm] = {p: {"n": int(len(y)), "net": round(float(y.net.mean()), 4),
                        "t_nw20": round(t1.nw_t(y.groupby("e").net.mean().values, 20), 2)}
                    for p, y in list(x.groupby("per")) + [("ALL", x)]}
    Res["baselines"] = base
    V = {"a_7x14_n1": (7, 140000, 1, {}), "b_7x14_n2": (7, 140000, 2, {}),
         "c_10x10_n1": (10, 100000, 1, {}), "c2_10x10_n2": (10, 100000, 2, {}),
         "d_14x7_n1": (14, 70000, 1, {}), "d2_14x7_n2": (14, 70000, 2, {}),
         "f_7x14_replace": (7, 140000, 1, {"replace": True}),
         "g_a_frac": (7, 140000, 1, {"frac": True}), "g_c_frac": (10, 100000, 1, {"frac": True}),
         "g_d_frac": (14, 70000, 1, {"frac": True})}
    for nm, (sl, kr, mn, kw) in V.items():
        R, occ, C, skip = sim(T, sl, kr, mn, O=O, ti=ti, iw=iw, **kw)
        Res[nm] = metrics(R, C, occ, sl, T)
        if nm == "a_7x14_n1":
            # timing diagnosis
            S = T.loc[skip]
            Res["diag_a"] = {p: {"skipped_full_top1_n": int(len(S[S.per == p])),
                                 "skipped_full_top1_net": round(float(S[S.per == p].net.mean()), 4),
                                 "taken_net": round(float(R[R.per == p].net.mean()), 4)} for p in ("IS", "OOS")}
            # qty<1 skips
            Lq = L.copy(); Lq["q"] = np.floor(140000 / (Lq.close_a * FX))
            Res["diag_a"]["qty0_share_of_liquid"] = round(float((Lq.q < 1).mean()), 3)
            Res["diag_a"]["qty0_net"] = round(float(Lq[Lq.q < 1].net.mean()), 4)
            R.to_csv(OUT / "trades_a.csv.gz", index=False, compression="gzip")
    # e: random order, 20 seeds
    ev = []
    for sd in range(20):
        R, occ, C, _ = sim(T, 7, 140000, 1, order="random", seed=sd)
        ev.append(metrics(R, C, occ, 7, T))
    Res["e_7x14_random"] = {p: {k: round(float(np.mean([m[p][k] for m in ev])), 4)
                                for k in ("n", "net", "t_nw20", "annual_ret_mean", "top10_missed")} for p in ("IS", "OOS", "ALL")}
    (OUT / "results.json").write_text(json.dumps(Res, indent=1, default=str))
    print(json.dumps(Res, indent=1, default=str))


if __name__ == "__main__":
    main()
