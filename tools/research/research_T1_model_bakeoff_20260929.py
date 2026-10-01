"""T1 모델 베이크오프 — LightGBM(M2)보다 나은 모델이 있나 (2026-09-29, 사전등록 — 결과 계산 전 고정)

질문(운영자): T1(US 실적 서프라이즈, A_open·H20, M2 LightGBM 회귀)에 LightGBM보다 새롭거나 나은 모델이 있나.
연구 전용. 라이브 코드·config·.env·예약작업·state·모델 파일은 건드리지 않는다. Anthropic API 없음.

=== 사전등록 ===

[데이터 — 동결]
- 이벤트: data/analysis/us_earnings_dates.jsonl 에서 fetched_at < 2026-09-28 행만(원 점수 생성 시점 09-27 22:40 KST
  이후 추가된 100행 제외) → OUT/events_frozen.jsonl. 원 모듈(research_model_T1_earnings_20260927)의
  load_events/load_prices/build 를 그대로 import해 쓴다(EVT만 동결 파일로 바꿈).
- 피처 14개(FEATS_A), 타깃 gA20(open[e]→open[e+20] − IWM 동일 창), 학습 타깃은 학습 1/99 분위 winsorize.
- 워크포워드: 테스트 연도 Y=2017..2026, 학습 = 진입연도 < Y & x20 < Y 첫 세션 인덱스(퍼지). 연 1회 확장 재학습.
  모든 모델이 같은 폴드·같은 피처·같은 행을 쓴다(피처 추가 모델은 chronos 1종만, 아래).

[재현 게이트 — 통과 전 진행 금지]
- (a) 재구축 테이블이 캐시 점수(model_T1_robust_20260927/scores_A_open_H20.csv.gz)와 (ticker,date,e) 1:1, gA20 동일.
- (b) LGBMRegressor(n_jobs=2, random_state=0) 재학습 점수 vs 캐시 M2 최대 |차| ≤ 1e-6.
- (c) 캐시 M2 로 I1(연도 임계 = 진입연도<Y OOS 점수 90분위, 2017 확장·burn-in 200; impl.thresholds 그대로)
  평탄 0.44% → 4,580건 · +1.50%/건 · 세션 NW20 t 2.78±0.02. 라이브 복제(a_7x14_n1) OOS 연 +18.34% 재현.
- 기준선(baseline) = 캐시 M2 컬럼(= 현재 쉐도우/라이브가 쓰는 파이프라인의 연구 산출물).

[도전자 — 전부 같은 폴드. 튜닝은 명시한 것만, 내부 CV는 학습 연도 안에서만]
 1 lgb_base     재학습 M2(재현 확인용 — 판정에서는 캐시 baseline과 동일물)
 2 lgb_tuned    LightGBM 소형 격자 18개: num_leaves{7,15,31} × min_child_samples{50,200,800} ×
                (learning_rate,n_estimators){(0.05,100),(0.02,400)}. 내부 폴드 = 학습 연도 Y−1, Y−2 를 검증
                (각각 x20 < 검증연도 첫 세션 퍼지). 내부 지표 = 검증 연도 주간(W-FRI, N≥10) top-10% 평균 gA20의
                두 폴드 평균. 최고 설정으로 학습 전체 재적합.
 3 cat          CatBoostRegressor 기본값(1000 iter, depth 6, RMSE), seed 0.
 4 xgb          XGBRegressor 기본값(hist), seed 0.
 5 ridge_std    Ridge(alpha=1) — 학습 중앙값 대치 + 표준화.
 6 ridge_rank   Ridge(alpha=1) — 대치 후 QuantileTransformer(normal, 학습에만 적합).
 7 enet_rank    ElasticNet(l1_ratio .5), alpha∈{1e-4,1e-3,1e-2} 를 2번과 같은 내부 폴드·지표로 선택. rank 변환.
 8 lgb_rank     LGBMRanker(lambdarank) 기본값, query = 진입 주(W-FRI), relevance = 주 내부 gA20 5분위(원 M3 동일).
 9 mlp_m4       원 M4 MLPRegressor (캐시 컬럼 — 참고).
10 tabm         TabM(k=32, 기본 make), AdamW lr 2e-3 wd 3e-4, batch 512, 최대 40 epoch, 조기종료(학습의 시간상
                마지막 15% 검증, patience 5), 대치+표준화, 타깃 winsorize 후 표준화, seed 0.
11 realmlp      pytabkit RealMLP_TD_Regressor 기본값(내부 검증 20% 무작위 — 패키지 기본), seed 0.
12 ftt          FT-Transformer(rtdl_revisiting_models 기본 kwargs n_blocks=3), AdamW lr 1e-4 wd 1e-5, 나머지 tabm과 동일 루프.
13 tabnet       pytorch-tabnet TabNetRegressor 기본값, 최대 50 epoch, patience 5, 검증 = 학습 시간상 마지막 15%.
14 tabpfn2      TabPFN v2 회귀(로컬 v2_default 체크포인트, Prior Labs License = Apache-2.0 파생+귀속 조항),
                CPU, n_estimators 4, **학습 컨텍스트 = 시간상 가장 최근 10,000행으로 부분표본**(CPU 시간 제약).
15 tabicl       TabICL v2 회귀(BSD-3), CPU, n_estimators 4, **컨텍스트 = 가장 최근 20,000행**.
16 lgb_q90      LightGBM quantile(alpha .9) — 상단 꼬리 순위 점수.
17 lgb_clf10    LGBMClassifier, 라벨 = gA20 ≥ 학습 90분위(학습 행만으로 계산), 점수 = P(라벨).
18 ngboost      NGBRegressor(Normal, 기본 500 iter), 점수 = loc + 1.2816·scale(예측 0.9분위).
19 lgb_chronos  (조건부) Chronos-Bolt(small) 가격경로 피처 2개를 14피처에 추가한 lgb_base: 입력 = close[a−119..a]
                (진입 전 종가만), h=20 예측 → chr_mu = log(q50[h20]/close[a]), chr_sp = (q90−q10)[h20]/close[a].
20 ag_bq        (조건부) AutoGluon best_quality, 폴드당 time_limit 600s — "AutoML 천장".
21 ag_mitra     (조건부) AutoGluon Mitra(파운데이션) 단독.
22 ens_top3     등가중 앙상블: 각 모델 점수를 **인과 백분위**(그 모델의 진입연도<Y OOS 점수 ECDF; 2017은 그 세션
                이전 2017 점수 확장, burn-in 200)로 바꿔 평균. 구성원 = IS(2017–2020) I1 net/건 상위 3개(OOS 오염 없음).
 차단 예상(기록만): TabPFN-2.5(계정 라이선스 수락 필요·비상업 조건 → 운영자 결정), TabDPT, tabpfn-client(계정).

[지표 — 모델마다 자기 점수로 I1 임계를 다시 계산(차용 금지)]
- I1 net/건(평탄 0.44%), 세션 NW20 t, IS(≤2020)/OOS(≥2021) 분리, 연도별 부호.
- rank IC: 주간(N≥10) Spearman(점수, gA20) 평균·NW4 t. top-decile: 주간 top max(3,⌊.1N⌋) 평균 gA20−0.44%·NW4 t.
- 라이브 복제: research_T1_live_capacity_20260929.sim/metrics 그대로(7×14만, 하루 1, 최고점, dvol≥$5M, 정수주, FX 1380).
  OOS 연 초과(100만 기준). 경로 운 대역 = 세션 내 무작위 순서 20시드의 OOS 연수익 최소/중앙/최대(참고, 판정 불포함).
- 학습·추론 벽시계 시간(폴드 합), 배포 요건.

[교체 판정 — 도전자 수가 늘어 문턱 상향(t 2.5)]
 R1 OOS(≥2021) I1 net/건 > baseline.
 R2 주간 쌍대 차이(W-FRI 주별 I1 건별 net 평균, 두 모델 모두 ≥1건인 주, 도전자−baseline) NW lag4 t ≥ 2.5 (전 구간
    2017–2026 워크포워드) AND OOS(≥2021) 쌍대 차이 평균 ≥ 0. OOS만의 t도 병기.
 R3 라이브 복제 OOS 연 초과 ≥ baseline. (경로 운 대역이 넓어 이 게이트의 검정력은 거의 없다 — 사전 인지.)
 판정: 셋 다 → 교체 / R1·R3 통과 & R2 t∈[2.0,2.5) → 앙상블·관찰 후보 / 그 외 → LightGBM 유지.
 다중검정: 도전자 ~20개. t 2.5도 가족 단위 보정은 아니다 — 통과해도 "후보" 이상으로 올리지 않고 forward 확인 필요.

[추가 사전등록 — 2026-09-29 오후, TabPFN v3 점수 계산 전 고정. 운영자가 Prior Labs 가중치 연구용 사용 승인]
- 토큰: .env.live 의 TABPFN_TOKEN 만 os.environ 에 올림(출력 금지), TABPFN_NO_BROWSER=1.
- 23 tabpfn3      TabPFN v3 회귀(패키지 8.0.7 기본 = repo tabpfn_3), CPU, n_estimators 4, 컨텍스트 = 시간상 최근
                 10,000행(v2와 동일 프로토콜), 중앙값 대치 없음(패키지 NaN 처리), random_state 0.
- 24 tabpfn3_ctx30k  같은 설정, 컨텍스트 최근 30,000행 — 단 합성 30k×5k 타이밍 프로브가 폴드당 ≤ 20분일 때만 실행.
- 25 tabpfn25     TabPFN v2.5 1회 시도(컨텍스트 10k). TabPFNLicenseError면 기록 후 건너뜀.
                 (1차 시도 = TabPFNLicenseError. 이후 운영자가 v2.5·v2.6 라이선스 수락 → 재실행, 점수 보기 전 추가)
- 25b tabpfn26   TabPFN v2.6, 동일 프로토콜(컨텍스트 10k, n_estimators 4). 모든 버전을 v2를 기준으로 한 표로 보고.
- 26 ens_top3_v3  앙상블 재검: 후보 풀에 위 v3(와 v2.5) 추가, 구성원 = IS(2017–2020) I1 net/건 상위 3(동일 인과 백분위 평균).
- 판정 기준 동일(R1, R2 쌍대 t ≥ 2.5 & OOS 쌍대 평균 ≥ 0, R3 라이브 복제). 가중치 약관(비상업)은 보고서에 명기.

[한계(사전 인지)] 생존편향(현재 상장 종목), eps_est 비PIT 가능, 부분표본 컨텍스트(TabPFN/TabICL), NN 단일 시드.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
import research_model_T1_earnings_20260927 as t1  # noqa: E402

OUT = ROOT / "data" / "analysis" / "T1_model_bakeoff_20260929"
SC = OUT / "scores"
CACHED = ROOT / "data" / "analysis" / "model_T1_robust_20260927" / "scores_A_open_H20.csv.gz"
FROZEN = OUT / "events_frozen.jsonl"
TAB = OUT / "table.pkl.gz"
CHR = OUT / "chronos_feat.csv.gz"
FEATS = t1.FEATS_A
FEE = 0.0044
KEY = ["ticker", "date", "e"]
TABPFN_CKPT = (r"C:\Users\Unknown\.cache\huggingface\hub\models--Prior-Labs--TabPFN-v2-reg\snapshots"
               r"\4972a65a1b30806315c6f92499959ffbfc69a673\tabpfn-v2-regressor-v2_default.ckpt")


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    with open(OUT / "run.log", "a", encoding="utf-8") as fh:
        fh.write(s + "\n")


# ---------------------------------------------------------------- prep
def freeze_events():
    if FROZEN.exists():
        return
    n = k = 0
    with open(t1.EVT, encoding="utf-8") as fi, open(FROZEN, "w", encoding="utf-8") as fo:
        for line in fi:
            n += 1
            if json.loads(line).get("fetched_at", "") < "2026-09-28":
                fo.write(line); k += 1
    log("frozen events", k, "of", n)


def build_table():
    freeze_events()
    t1.EVT = FROZEN
    ev, info = t1.load_events()
    cal, ti, W = t1.load_prices()
    tab, filt = t1.build(ev, cal, ti, W)
    tab = tab[tab.gA20.notna()].reset_index(drop=True)
    tab["date"] = pd.to_datetime(tab.date).dt.strftime("%Y-%m-%d")
    tab["entry_date"] = pd.to_datetime(tab.entry_date).dt.strftime("%Y-%m-%d")
    tab["week"] = pd.to_datetime(tab.week).dt.strftime("%Y-%m-%d")
    first_idx = {y: int(np.searchsorted(cal.values, np.datetime64(f"{y}-01-01"))) for y in range(2014, 2028)}
    tab.to_pickle(TAB)
    (OUT / "first_idx.json").write_text(json.dumps(first_idx))
    log("table", len(tab), filt)
    return tab


def load_tab():
    tab = pd.read_pickle(TAB)
    fi = {int(k): v for k, v in json.loads((OUT / "first_idx.json").read_text()).items()}
    if CHR.exists():
        c = pd.read_csv(CHR, dtype={"ticker": str})
        tab = tab.merge(c, on=KEY, how="left")
    return tab, fi


def folds(tab, fi, years=range(2017, 2027)):
    for Y in years:
        tr = tab[(tab.year < Y) & (tab.x20 < fi[Y])]
        te = tab[tab.year == Y]
        yield Y, tr, te


# ---------------------------------------------------------------- helpers
def wins(y):
    lo, hi = np.nanpercentile(y, [1, 99]); return np.clip(y, lo, hi)


def imp_std(Xtr, Xte, rank=False):
    from sklearn.preprocessing import StandardScaler, QuantileTransformer
    med = np.nanmedian(Xtr, axis=0)
    Xtr = np.where(np.isnan(Xtr), med, Xtr); Xte = np.where(np.isnan(Xte), med, Xte)
    if rank:
        q = QuantileTransformer(n_quantiles=1000, output_distribution="normal", random_state=0).fit(Xtr)
        return q.transform(Xtr).astype(np.float32), q.transform(Xte).astype(np.float32)
    s = StandardScaler().fit(Xtr)
    return s.transform(Xtr).astype(np.float32), s.transform(Xte).astype(np.float32)


def weekly_top_metric(df, score):
    """주간(N≥10) top max(3,⌊.1N⌋) 평균 gA20의 주 평균."""
    d = df.assign(_s=score)
    vals = []
    for _, g in d.groupby("week"):
        N = len(g)
        if N < 10:
            continue
        n = max(3, int(math.floor(0.1 * N)))
        vals.append(g.nlargest(n, "_s").gA20.mean())
    return float(np.mean(vals)) if vals else float("nan")


def inner_select(tr, fi, Y, configs, fitfn):
    """학습 연도 Y−1, Y−2 검증. fitfn(cfg, tr2, va) -> 점수."""
    res = []
    for cfg in configs:
        sc = []
        for V in (Y - 1, Y - 2):
            tr2 = tr[(tr.year < V) & (tr.x20 < fi[V])]
            va = tr[tr.year == V]
            if len(tr2) < 500 or len(va) < 100:
                continue
            sc.append(weekly_top_metric(va, fitfn(cfg, tr2, va)))
        res.append((float(np.mean(sc)) if sc else -np.inf, cfg))
    res.sort(key=lambda r: -r[0])
    return res[0][1], res


# ---------------------------------------------------------------- models
def m_lgb_base(tr, te, feats=FEATS, **kw):
    import lightgbm as lgb
    m = lgb.LGBMRegressor(n_jobs=2, random_state=0, verbose=-1, **kw)
    m.fit(tr[feats].values.astype(np.float32), wins(tr.gA20.values))
    return m.predict(te[feats].values.astype(np.float32))


LGB_GRID = [dict(num_leaves=nl, min_child_samples=mc, learning_rate=lr, n_estimators=ne)
            for nl in (7, 15, 31) for mc in (50, 200, 800) for lr, ne in ((0.05, 100), (0.02, 400))]


def m_lgb_tuned(tr, te, fi=None, Y=None, info=None):
    cfg, res = inner_select(tr, fi, Y, LGB_GRID, lambda c, a, b: m_lgb_base(a, b, **c))
    info[Y] = {"chosen": cfg, "inner": [(round(s, 5), c) for s, c in res[:3]]}
    return m_lgb_base(tr, te, **cfg)


def m_cat(tr, te):
    from catboost import CatBoostRegressor
    m = CatBoostRegressor(random_seed=0, verbose=0, thread_count=4)
    m.fit(tr[FEATS].values.astype(np.float32), wins(tr.gA20.values))
    return m.predict(te[FEATS].values.astype(np.float32))


def m_xgb(tr, te):
    from xgboost import XGBRegressor
    m = XGBRegressor(random_state=0, n_jobs=4, tree_method="hist")
    m.fit(tr[FEATS].values.astype(np.float32), wins(tr.gA20.values))
    return m.predict(te[FEATS].values.astype(np.float32))


def m_ridge(tr, te, rank=False):
    from sklearn.linear_model import Ridge
    Xtr, Xte = imp_std(tr[FEATS].values.astype(np.float64), te[FEATS].values.astype(np.float64), rank)
    return Ridge(alpha=1.0).fit(Xtr, wins(tr.gA20.values)).predict(Xte)


def _enet(alpha, tr, te):
    from sklearn.linear_model import ElasticNet
    Xtr, Xte = imp_std(tr[FEATS].values.astype(np.float64), te[FEATS].values.astype(np.float64), True)
    y = wins(tr.gA20.values); mu, sd = y.mean(), y.std()
    return ElasticNet(alpha=alpha, l1_ratio=0.5, max_iter=5000).fit(Xtr, (y - mu) / sd).predict(Xte)


def m_enet_rank(tr, te, fi=None, Y=None, info=None):
    a, res = inner_select(tr, fi, Y, [1e-4, 1e-3, 1e-2], lambda c, x, v: _enet(c, x, v) + 1e-12 * np.arange(len(v)))
    info[Y] = {"alpha": a, "inner": [(round(s, 5), c) for s, c in res]}
    return _enet(a, tr, te)


def m_lgb_rank(tr, te):
    import lightgbm as lgb
    trs = tr.sort_values("week", kind="stable")
    pct = trs.groupby("week").gA20.rank(pct=True, method="first").values
    rel = np.clip(np.floor(pct * 5 - 1e-9), 0, 4).astype(int)
    grp = trs.groupby("week", sort=False).size().values
    m = lgb.LGBMRanker(objective="lambdarank", n_jobs=2, random_state=0, verbose=-1)
    m.fit(trs[FEATS].values.astype(np.float32), rel, group=grp)
    return m.predict(te[FEATS].values.astype(np.float32))


def m_lgb_q90(tr, te):
    import lightgbm as lgb
    m = lgb.LGBMRegressor(objective="quantile", alpha=0.9, n_jobs=2, random_state=0, verbose=-1)
    m.fit(tr[FEATS].values.astype(np.float32), wins(tr.gA20.values))
    return m.predict(te[FEATS].values.astype(np.float32))


def m_lgb_clf10(tr, te):
    import lightgbm as lgb
    y = tr.gA20.values
    lab = (y >= np.nanpercentile(y, 90)).astype(int)
    m = lgb.LGBMClassifier(n_jobs=2, random_state=0, verbose=-1)
    m.fit(tr[FEATS].values.astype(np.float32), lab)
    return m.predict_proba(te[FEATS].values.astype(np.float32))[:, 1]


def m_ngboost(tr, te):
    from ngboost import NGBRegressor
    Xtr, Xte = imp_std(tr[FEATS].values.astype(np.float64), te[FEATS].values.astype(np.float64))
    y = wins(tr.gA20.values); mu, sd = y.mean(), y.std()
    m = NGBRegressor(random_state=0, verbose=False).fit(Xtr, (y - mu) / sd)
    d = m.pred_dist(Xte)
    return d.params["loc"] + 1.2816 * d.params["scale"]


def _torch_fit(tr, te, make, lr, wd, k_heads=False, seed=0, max_epochs=40, patience=5, bs=512):
    import torch
    torch.manual_seed(seed); np.random.seed(seed)
    torch.set_num_threads(int(os.environ.get("BK_THREADS", "4")))
    trs = tr.sort_values("e", kind="stable")
    Xtr, Xte = imp_std(trs[FEATS].values.astype(np.float64), te[FEATS].values.astype(np.float64))
    y = wins(trs.gA20.values); mu, sd = y.mean(), y.std(); y = ((y - mu) / sd).astype(np.float32)
    nv = int(len(Xtr) * 0.15)
    Xa, ya, Xv, yv = Xtr[:-nv], y[:-nv], Xtr[-nv:], y[-nv:]
    model = make()
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=wd)

    def fwd(X):
        o = model(torch.as_tensor(X))
        return o.squeeze(-1)  # tabm: (B,k), else (B,)

    def loss_fn(o, t):
        if o.dim() == 2:
            return ((o - t[:, None]) ** 2).mean()
        return ((o - t) ** 2).mean()

    def pred(X):
        model.eval(); out = []
        with torch.no_grad():
            for i in range(0, len(X), 4096):
                o = fwd(X[i:i + 4096])
                out.append((o.mean(1) if o.dim() == 2 else o).numpy())
        return np.concatenate(out)

    best, bstate, bad = np.inf, None, 0
    g = np.random.default_rng(seed)
    for ep in range(max_epochs):
        model.train(); idx = g.permutation(len(Xa))
        for i in range(0, len(idx), bs):
            b = idx[i:i + bs]
            opt.zero_grad()
            loss_fn(fwd(Xa[b]), torch.as_tensor(ya[b])).backward()
            opt.step()
        vl = float(((pred(Xv) - yv) ** 2).mean())
        if vl < best - 1e-6:
            best, bad = vl, 0
            bstate = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            bad += 1
            if bad >= patience:
                break
    model.load_state_dict(bstate)
    return pred(Xte), {"epochs": ep + 1, "best_val": best}


def m_tabm(tr, te, info=None, Y=None):
    import tabm

    def make():
        return tabm.TabM.make(n_num_features=len(FEATS), cat_cardinalities=[], d_out=1)
    p, i = _torch_fit(tr, te, make, 2e-3, 3e-4)
    info[Y] = i
    return p


def m_ftt(tr, te, info=None, Y=None):
    import torch
    from rtdl_revisiting_models import FTTransformer

    class W(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.m = FTTransformer(n_cont_features=len(FEATS), cat_cardinalities=[], d_out=1,
                                   **FTTransformer.get_default_kwargs())

        def forward(self, x):
            return self.m(x, None)
    p, i = _torch_fit(tr, te, W, 1e-4, 1e-5)
    info[Y] = i
    return p


def m_tabnet(tr, te):
    import torch
    from pytorch_tabnet.tab_model import TabNetRegressor
    torch.set_num_threads(int(os.environ.get("BK_THREADS", "4")))
    trs = tr.sort_values("e", kind="stable")
    Xtr, Xte = imp_std(trs[FEATS].values.astype(np.float64), te[FEATS].values.astype(np.float64))
    y = wins(trs.gA20.values); mu, sd = y.mean(), y.std(); y = ((y - mu) / sd).reshape(-1, 1)
    nv = int(len(Xtr) * 0.15)
    m = TabNetRegressor(seed=0, verbose=0, device_name="cpu")
    m.fit(Xtr[:-nv], y[:-nv], eval_set=[(Xtr[-nv:], y[-nv:])], max_epochs=50, patience=5,
          batch_size=1024, virtual_batch_size=128)
    return m.predict(Xte).ravel()


def m_realmlp(tr, te):
    from pytabkit import RealMLP_TD_Regressor
    y = wins(tr.gA20.values); mu, sd = y.mean(), y.std()
    m = RealMLP_TD_Regressor(random_state=0, n_threads=int(os.environ.get("BK_THREADS", "4")), device="cpu",
                             verbosity=0)
    Xtr = tr[FEATS].values.astype(np.float64); Xte = te[FEATS].values.astype(np.float64)
    med = np.nanmedian(Xtr, axis=0)  # 학습 중앙값 대치(패키지가 NaN 불허), 스케일링은 패키지 기본
    Xtr = np.where(np.isnan(Xtr), med, Xtr).astype(np.float32); Xte = np.where(np.isnan(Xte), med, Xte).astype(np.float32)
    m.fit(Xtr, (y - mu) / sd)
    return np.asarray(m.predict(Xte)).ravel()


def _recent(tr, n):
    return tr.sort_values("e", kind="stable").iloc[-n:]


def m_tabpfn2(tr, te):
    import torch
    from tabpfn import TabPFNRegressor
    torch.set_num_threads(int(os.environ.get("BK_THREADS", "6")))
    c = _recent(tr, 10000)
    m = TabPFNRegressor(device="cpu", n_estimators=4, model_path=TABPFN_CKPT, ignore_pretraining_limits=True,
                        random_state=0)
    m.fit(c[FEATS].values.astype(np.float32), wins(c.gA20.values))
    return m.predict(te[FEATS].values.astype(np.float32))


def _tabpfn_env():
    from dotenv import dotenv_values
    tok = dotenv_values(ROOT / ".env.live").get("TABPFN_TOKEN")
    if tok:
        os.environ["TABPFN_TOKEN"] = tok  # 출력 금지
    os.environ["TABPFN_NO_BROWSER"] = "1"


def _tabpfn_ver(tr, te, version, ctx):
    _tabpfn_env()
    import torch
    from tabpfn import TabPFNRegressor
    from tabpfn.constants import ModelVersion
    torch.set_num_threads(int(os.environ.get("BK_THREADS", "6")))
    c = _recent(tr, ctx)
    m = TabPFNRegressor.create_default_for_version(ModelVersion(version), device="cpu", n_estimators=4,
                                                   ignore_pretraining_limits=True, random_state=0)
    m.fit(c[FEATS].values.astype(np.float32), wins(c.gA20.values))
    return m.predict(te[FEATS].values.astype(np.float32))


def m_tabicl(tr, te):
    import torch
    from tabicl import TabICLRegressor
    torch.set_num_threads(int(os.environ.get("BK_THREADS", "6")))
    c = _recent(tr, 20000)
    m = TabICLRegressor(n_estimators=4, device="cpu", random_state=0,
                        n_jobs=int(os.environ.get("BK_THREADS", "6")))
    # 이탈(1차 실행 오류 후, 결과 보기 전): TabICL은 NaN 입력 불가 → 컨텍스트 중앙값 대치
    Xc = c[FEATS].values.astype(np.float64); Xt = te[FEATS].values.astype(np.float64)
    med = np.nanmedian(Xc, axis=0)
    Xc = np.where(np.isnan(Xc), med, Xc).astype(np.float32); Xt = np.where(np.isnan(Xt), med, Xt).astype(np.float32)
    m.fit(Xc, wins(c.gA20.values))
    return m.predict(Xt)


def m_lgb_chronos(tr, te):
    return m_lgb_base(tr, te, feats=FEATS + ["chr_mu", "chr_sp"])


def _ag(tr, te, Y, kw, tag):
    from autogluon.tabular import TabularPredictor
    d = tr[FEATS].copy(); d["y"] = wins(tr.gA20.values)
    path = OUT / "ag_tmp" / f"{tag}_{Y}"
    p = TabularPredictor(label="y", problem_type="regression", path=str(path), verbosity=0)
    p.fit(d, num_cpus=int(os.environ.get("BK_THREADS", "8")), **kw)
    return p.predict(te[FEATS]).values


def m_ag_bq(tr, te, Y=None, info=None):
    return _ag(tr, te, Y, dict(presets="best_quality", time_limit=600), "bq")


def m_ag_mitra(tr, te, Y=None, info=None):
    return _ag(_recent(tr, 10000), te, Y, dict(hyperparameters={"MITRA": {"fine_tune": False}}, time_limit=1800), "mitra")


MODELS = {
    "lgb_base": m_lgb_base, "lgb_tuned": m_lgb_tuned, "cat": m_cat, "xgb": m_xgb,
    "ridge_std": lambda tr, te: m_ridge(tr, te, False), "ridge_rank": lambda tr, te: m_ridge(tr, te, True),
    "enet_rank": m_enet_rank, "lgb_rank": m_lgb_rank, "tabm": m_tabm, "realmlp": m_realmlp, "ftt": m_ftt,
    "tabnet": m_tabnet, "tabpfn2": m_tabpfn2, "tabicl": m_tabicl, "lgb_q90": m_lgb_q90,
    "lgb_clf10": m_lgb_clf10, "ngboost": m_ngboost, "lgb_chronos": m_lgb_chronos,
    "ag_bq": m_ag_bq, "ag_mitra": m_ag_mitra,
    "tabpfn3": lambda tr, te: _tabpfn_ver(tr, te, "v3", 10000),
    "tabpfn3_ctx30k": lambda tr, te: _tabpfn_ver(tr, te, "v3", 30000),
    "tabpfn25": lambda tr, te: _tabpfn_ver(tr, te, "v2.5", 10000),
    "tabpfn26": lambda tr, te: _tabpfn_ver(tr, te, "v2.6", 10000),
}
NEEDS_CTX = {"lgb_tuned", "enet_rank", "tabm", "ftt", "ag_bq", "ag_mitra"}


def fit_model(name, years=None):
    tab, fi = load_tab()
    fn = MODELS[name]
    rows, tm, info = [], {}, {}
    for Y, tr, te in folds(tab, fi, years or range(2017, 2027)):
        t0 = time.time()
        if name in NEEDS_CTX:
            kw = {"info": info, "Y": Y}
            if name in ("lgb_tuned", "enet_rank"):
                kw["fi"] = fi
            s = fn(tr, te, **kw)
        else:
            s = fn(tr, te)
        tm[Y] = round(time.time() - t0, 1)
        p = te[KEY].copy(); p[name] = np.asarray(s, float)
        rows.append(p)
        log(f"[fit {name}] {Y} ntr={len(tr)} nte={len(te)} sec={tm[Y]}")
    P = pd.concat(rows, ignore_index=True)
    SC.mkdir(parents=True, exist_ok=True)
    if years is None:
        P.to_csv(SC / f"{name}.csv.gz", index=False, compression="gzip")
        (SC / f"{name}.json").write_text(json.dumps({"sec_by_year": tm, "sec_total": round(sum(tm.values()), 1),
                                                     "info": info}, default=str, indent=1))
    return P


# ---------------------------------------------------------------- chronos feature
def chronos_feature():
    import torch
    from chronos import BaseChronosPipeline
    torch.set_num_threads(int(os.environ.get("BK_THREADS", "8")))
    tab, _ = load_tab()
    t1.EVT = FROZEN
    cal, ti, W = t1.load_prices()
    C = W["close"]
    pipe = BaseChronosPipeline.from_pretrained("amazon/chronos-bolt-small", device_map="cpu", torch_dtype=torch.float32)
    r = tab.ticker.map(ti).values; a = tab.e.values - 1
    idx = a[:, None] + np.arange(-119, 1)[None, :]
    X = C[r[:, None], np.maximum(idx, 0)].astype(np.float64)
    X[idx < 0] = np.nan
    X = pd.DataFrame(X.T).ffill().values.T  # 진입 전 값만 전진 채움
    ca = X[:, -1]
    mu = np.full(len(X), np.nan); sp = np.full(len(X), np.nan)
    t0 = time.time()
    B = 512
    for i in range(0, len(X), B):
        ctx = torch.tensor(X[i:i + B], dtype=torch.float32)
        q, _ = pipe.predict_quantiles(ctx, prediction_length=20, quantile_levels=[0.1, 0.5, 0.9])
        q = q.numpy()[:, -1, :]
        mu[i:i + B] = np.log(q[:, 1] / ca[i:i + B]); sp[i:i + B] = (q[:, 2] - q[:, 0]) / ca[i:i + B]
        if i % (B * 20) == 0:
            log("chronos", i, len(X), round(time.time() - t0, 1))
    out = tab[KEY].copy(); out["chr_mu"] = mu; out["chr_sp"] = sp
    out.to_csv(CHR, index=False, compression="gzip")
    log("chronos done sec", round(time.time() - t0, 1), "nan", int(np.isnan(mu).sum()))


# ---------------------------------------------------------------- eval
def nw(x, lags):
    return t1.nw_t(x, lags) if lags <= 4 else __import__("research_model_T1_impl_20260927").nw_t(x, lags)


def causal_pct(P, col):
    """그 모델의 진입연도<Y OOS 점수 ECDF 백분위. 2017은 세션 이전 2017 점수 확장(burn-in 200)."""
    s = P[col].values; yr = P.year.values; e = P.e.values
    out = np.full(len(P), np.nan)
    for Y in range(2018, 2027):
        prior = np.sort(s[yr < Y]); m = yr == Y
        out[m] = np.searchsorted(prior, s[m], side="right") / len(prior)
    m17 = np.where(yr == 2017)[0]
    order = m17[np.argsort(e[m17], kind="stable")]
    ee = e[order]
    for sess in np.unique(ee):
        prior = np.sort(s[order[ee < sess]])
        if len(prior) >= 200:
            tgt = order[ee == sess]
            out[tgt] = np.searchsorted(prior, s[tgt], side="right") / len(prior)
    return out


def evaluate_one(P, col, O, ti, iw, base_weekly=None, seeds=20):
    import research_model_T1_impl_20260927 as impl
    import research_T1_live_capacity_20260929 as cap
    Q = P[["ticker", "date", "e", "year", "week", "close_a", "dvol20", "gA20"]].copy()
    Q["M2_lgbm_reg"] = P[col].values
    Q = Q[Q.M2_lgbm_reg.notna()].reset_index(drop=True)
    Q["thr"] = impl.thresholds(Q, "primary")
    Q["qual"] = Q.thr.notna() & (Q.M2_lgbm_reg >= Q.thr)
    T = impl.select(Q, "I1").copy()
    T["net"] = T.gA20 - FEE
    T["per"] = np.where(T.year <= 2020, "IS", "OOS")
    r = {}
    for p in ("ALL", "IS", "OOS"):
        x = T if p == "ALL" else T[T.per == p]
        r[p] = {"n": int(len(x)), "net": float(x.net.mean()),
                "t_sess_nw20": nw(x.groupby("e").net.mean().values, 20)}
    yr = T.groupby("year").net.mean()
    r["by_year"] = {int(k): round(float(v), 4) for k, v in yr.items()}
    r["years_pos"] = f"{int((yr > 0).sum())}/{len(yr)}"
    r["sel_share_by_year"] = {int(k): round(float(v), 3) for k, v in Q.groupby("year").qual.mean().items()}
    # rank IC / top decile (weekly, N>=10)
    from scipy.stats import spearmanr
    ics, tops, wk = [], [], []
    for w, g in Q.groupby("week"):
        N = len(g)
        if N < 10:
            continue
        n = max(3, int(math.floor(0.1 * N)))
        ics.append(spearmanr(g.M2_lgbm_reg.values, g.gA20.values).correlation if g.M2_lgbm_reg.std() > 0 else np.nan)
        tops.append(g.nlargest(n, "M2_lgbm_reg").gA20.mean() - FEE); wk.append(w)
    ics = np.array(ics, float); tops = np.array(tops, float); wk = pd.to_datetime(pd.Series(wk))
    oos = (wk.dt.year >= 2021).values
    r["ic"] = {"mean": float(np.nanmean(ics)), "t": nw(ics, 4), "oos_mean": float(np.nanmean(ics[oos]))}
    r["topdec"] = {"mean": float(tops.mean()), "t": nw(tops, 4), "oos_mean": float(tops[oos].mean()),
                   "is_mean": float(tops[~oos].mean())}
    # weekly I1 series for paired diff
    Wk = T.groupby("week").net.mean()
    r["_weekly"] = Wk
    if base_weekly is not None:
        j = pd.concat([Wk.rename("c"), base_weekly.rename("b")], axis=1, join="inner").sort_index()
        d = (j.c - j.b)
        o = pd.to_datetime(j.index).year >= 2021
        r["paired"] = {"weeks": int(len(d)), "mean": float(d.mean()), "t_full": nw(d.values, 4),
                       "oos_mean": float(d[o].mean()), "t_oos": nw(d[o].values, 4),
                       "is_mean": float(d[~o].mean())}
    # live replica
    R, occ, C, _ = cap.sim(T, 7, 140000, 1, O=O, ti=ti, iw=iw)
    lm = cap.metrics(R, C, occ, 7, T)
    r["live"] = {p: {k: lm[p][k] for k in ("n", "net", "t_nw20", "annual_ret_mean", "yrs_pos")} for p in ("IS", "OOS", "ALL")}
    r["live"]["slot_util"] = lm["slot_util"]
    band = []
    for sd in range(seeds):
        R2, occ2, C2, _ = cap.sim(T, 7, 140000, 1, order="random", seed=sd)
        band.append(cap.metrics(R2, C2, occ2, 7, T)["OOS"]["annual_ret_mean"])
    r["live_random_oos_annual"] = {"min": float(np.min(band)), "median": float(np.median(band)), "max": float(np.max(band))}
    return r, T


def gather():
    B = pd.read_csv(CACHED, dtype={"ticker": str})
    B = B.rename(columns={"M2_lgbm_reg": "baseline", "M4_mlp": "mlp_m4"})
    B["date"] = B.date.str[:10]
    for f in sorted(SC.glob("*.csv.gz")):
        nm = f.name[:-7]
        s = pd.read_csv(f, dtype={"ticker": str})
        s["date"] = s.date.str[:10]
        B = B.merge(s, on=KEY, how="left")
    return B


def cmd_eval(seeds=20):
    t1.EVT = FROZEN
    cal, ti, W = t1.load_prices(); O = W["open"]; iw = ti["IWM"]; del W
    B = gather()
    names = ["baseline"] + [c for c in B.columns if c in MODELS or c == "mlp_m4"]
    res = {}
    base, _ = evaluate_one(B, "baseline", O, ti, iw, seeds=seeds)
    bw = base.pop("_weekly"); res["baseline"] = base
    log("baseline", json.dumps({k: base[k] for k in ("ALL", "IS", "OOS")}), base["live"]["OOS"])
    for nm in names[1:]:
        if B[nm].isna().all():
            continue
        r, _ = evaluate_one(B, nm, O, ti, iw, base_weekly=bw, seeds=seeds)
        r.pop("_weekly"); res[nm] = r
        log(nm, f"ALL {r['ALL']['net']*100:.2f}% t{r['ALL']['t_sess_nw20']:.2f} | OOS {r['OOS']['net']*100:.2f}% "
            f"| paired t {r['paired']['t_full']:.2f} | live OOS {r['live']['OOS']['annual_ret_mean']*100:.1f}%")
    # ensemble: IS I1 net 상위 3
    late = ("tabpfn3", "tabpfn3_ctx30k", "tabpfn25", "tabpfn26")  # 오후 추가분 — 원 앙상블 풀에서 제외
    cands_all = [n for n in res if n not in ("baseline", "lgb_base")] + ["baseline"]
    for ens, pool in (("ens_top3", [n for n in cands_all if n not in late]), ("ens_top3_v3", cands_all)):
        if ens == "ens_top3_v3" and not any(n in res for n in late):
            continue
        top3 = sorted(pool, key=lambda n: -res[n]["IS"]["net"])[:3]
        B[ens] = np.nanmean(np.column_stack([causal_pct(B, c) for c in top3]), axis=1)
        r, _ = evaluate_one(B, ens, O, ti, iw, base_weekly=bw, seeds=seeds)
        r.pop("_weekly"); r["members"] = top3; res[ens] = r
        log(ens, top3, f"OOS {r['OOS']['net']*100:.2f}% paired t {r['paired']['t_full']:.2f}")
    # verdict
    b = res["baseline"]
    for nm, r in res.items():
        if nm == "baseline":
            continue
        R1 = r["OOS"]["net"] > b["OOS"]["net"]
        R2 = r["paired"]["t_full"] >= 2.5 and r["paired"]["oos_mean"] >= 0
        R2w = 2.0 <= r["paired"]["t_full"] < 2.5 and r["paired"]["oos_mean"] >= 0
        R3 = r["live"]["OOS"]["annual_ret_mean"] >= b["live"]["OOS"]["annual_ret_mean"]
        r["gate"] = {"R1": bool(R1), "R2": bool(R2), "R3": bool(R3),
                     "verdict": "replace" if (R1 and R2 and R3) else ("candidate" if (R1 and R3 and R2w) else "keep")}
    for nm in res:
        tj = SC / f"{nm}.json"
        if tj.exists():
            res[nm]["cost"] = {k: v for k, v in json.loads(tj.read_text()).items() if k.startswith("sec")}
    (OUT / "results.json").write_text(json.dumps(res, indent=1, default=float, ensure_ascii=False), encoding="utf-8")
    log("eval done")


# ---------------------------------------------------------------- reproduction
def cmd_repro():
    import research_model_T1_impl_20260927 as impl
    tab, fi = load_tab()
    C = pd.read_csv(CACHED, dtype={"ticker": str}); C["date"] = C.date.str[:10]
    tt = tab[tab.year >= 2017]
    j = tt[KEY + ["gA20"]].merge(C[KEY + ["gA20", "M2_lgbm_reg"]], on=KEY, how="outer", suffixes=("", "_c"),
                                indicator=True)
    rep = {"rebuilt_test_rows": int(len(tt)), "cached_rows": int(len(C)),
           "join": j._merge.value_counts().to_dict(),
           "gA20_max_abs_diff": float(np.nanmax(np.abs(j.gA20 - j.gA20_c)))}
    tr26 = tab[(tab.year < 2026) & (tab.x20 < fi[2026])]
    rep["n_train_2026"] = int(len(tr26)); rep["n_test_2026"] = int((tab.year == 2026).sum())
    P = fit_model("lgb_base")
    k = P.merge(C[KEY + ["M2_lgbm_reg"]], on=KEY)
    rep["lgb_refit_max_abs_diff"] = float(np.max(np.abs(k.lgb_base - k.M2_lgbm_reg)))
    Q = C.copy(); Q["thr"] = impl.thresholds(Q, "primary"); Q["qual"] = Q.thr.notna() & (Q.M2_lgbm_reg >= Q.thr)
    T = impl.select(Q, "I1"); net = T.gA20 - FEE
    rep["I1_cached"] = {"n": int(len(T)), "net": float(net.mean()),
                        "t": impl.nw_t(net.groupby(T.e.values).mean().values)}
    log("REPRO", json.dumps(rep, default=str))
    (OUT / "repro.json").write_text(json.dumps(rep, indent=1, default=str))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["prep", "repro", "fit", "chronos", "eval"])
    ap.add_argument("names", nargs="*")
    ap.add_argument("--years", type=int, nargs="*")
    ap.add_argument("--seeds", type=int, default=20)
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.cmd == "prep":
        build_table()
    elif a.cmd == "repro":
        cmd_repro()
    elif a.cmd == "fit":
        for n in a.names:
            fit_model(n, a.years)
    elif a.cmd == "chronos":
        chronos_feature()
    elif a.cmd == "eval":
        cmd_eval(a.seeds)
