#!/usr/bin/env python3
"""Jev 2라운드 — 추출 정확도가 아니라 '예측 쓸모'로 최적화 (2026-09-29, 사전등록)

read-only 연구. 라이브 코드·config·.env 변경 없음. Claude/Anthropic API 호출 없음. Jev(TypeSafe)만.
모델은 alias가 아니라 버전 고정 `jev-1.13.0`(docs: alias는 움직인다). 응답의 model 필드 전량 기록.

=== TypeSafe 문서 재점검 결과(2026-09-29, docs.typesafe.ai/llms.txt 전 페이지 중 API·models·state·primitives·
    advanced·confidence·jaggedness·fan-out·composite·consistency·rerank·autoresearch·SDK 타입) ===
- 요청 필드는 state / model / questions 3개뿐. system·few-shot·temperature·logprob·batch·ensemble 필드 없음.
  파인튜닝·LoRA·커스텀 라벨 학습 없음("same weights serve every account"). 모델은 jev-1.13.0 하나(jev-preview 동일).
- 한도 1,200 rpm / 250k tok/s, 컨텍스트 64k(state+가장 긴 질문 32k). 과금은 입력 토큰만($42/1B), 출력 무료.
- 미사용이던 기능: (a) instructions/criteria를 **객체**로 — 질문 옆에 데이터·예시 필드를 두고 backtick으로 가리킴
  (few-shot의 공식 경로 = criteria.{true,false}.examples), (b) fan-out — state 1회 과금으로 질문 다수(패러프레이즈 거의 공짜),
  (c) Score 확률벡터 → (기대수준, 분산) 2개 피처로 GBM에 투입(autoresearch cookbook), (d) rerank = (query,candidate)별 Noul.
- 동일 입력 반복 호출은 무의미(consistency cookbook: 라벨 반복률 90.8%) → self-consistency 축은 '문구 패러프레이즈'뿐.

=== 사전등록 (Jev 호출·결과 확인 전 고정. 이탈은 보고서에 '이탈'로 기록) ===
[표본] data/analysis/jev_8k_t1_20260929/oos_scores.csv.gz 의 8-K 2.02 매칭 5,021건(2019+; I1 2,786 · 무작위 2,443).
  T1 OOS 점수 T1S = scores_A_open_H20.csv.gz 의 M2_lgbm_reg. 결과 = gA20(open e → open e+20, IWM 초과), net = gA20 − 0.44%.
  IS = 2019–2020, OOS = 2021–2026. 판정은 OOS에서만.
[입력 추출] 회사명·약칭·티커 가림(round1 F1 mask). 헤드라인 앞 1,500자 + 전문에서 KW 창([-250,+550]) — KW =
  guidance|outlook|record|accelerat|margin|backlog|bookings|contract|customer|launch|approv|award|raise|expan|momentum|demand.
  safe-harbor 창·숫자 비중 > 30% 창 제외. 합 4,500자 상한(pairwise는 1,800자 상한). 이전 분기 릴리스도 동일 추출기.
[레버1 꼬리형 질문] (모든 변형에 공통, 원자 질문 fan-out)
  transformational(Score 5단계, **L1 1차 피처**), new_driver(Noul), margin_step(Noul), growth_accel(Choice 4),
  guide_far(Score 5), beat_quality(Choice 4), record(Noul).
[레버5 자기일관성] 꼬리 확률 Noul 패러프레이즈 P1..P4 동일 호출. **1차 = mean(P1..P4) vs P1 단독** 의 증분 t 비교.
[레버4 few-shot] (a) criteria형: Noul fs_crit, criteria.true/false에 각 3개 과거 헤드라인(300자) 예시.
  예시 풀 = 표본 중 x20 < 해당 연도 첫 세션인 이벤트, gA20 ≥ +15% (true) / ≤ −15% (false), 연도별 seed=Y 고정 추출.
  2019는 풀이 없어 결측. (b) state형(**L4 1차**): state = 현재 릴리스(2,500자) + 과거 예시 6개(헤드라인 500자, 결과 라벨),
  예시 조건 x20 < 현재 e, 같은 컷오프, seed=acc 해시. Noul fs_state. 대상 = 매칭 2020+.
[레버2 데이터] V0 = 텍스트만(round1의 beat/miss 플래그 제거). V1 = V0 + facts(고정 컷오프 단어 버킷:
  EPS 서프라이즈 sue1 {<−0.05 miss, −0.05~0.05 inline, 0.05~0.15 small beat, 0.15~0.5 solid beat, ≥0.5 large beat},
  beat streak beats4/4, 진입 전 20일 수익 ret20 {≤−10% fell sharply, −10~−3 fell, −3~3 flat, 3~10 rose, ≥10 rose sharply},
  60일 ret60 {≤−20, −20~−5, −5~5, 5~20, ≥20 동일 문구}, 규모 lsize {<16.5 micro, 16.5~18 small, 18~19.5 mid, ≥19.5 large},
  변동성 vol20 {<0.015 low, 0.015~0.03 moderate, ≥0.03 high}). V2 = V0 + previous_quarter_release(직전 2.02 8-K,
  acceptance가 이벤트 50–130일 전, 같은 추출기) + diff 질문 d_momentum(Choice), d_new(Noul, **V2 1차**), d_outlook(Choice).
  V2 대상 = I1 매칭 2,786 + 무작위 매칭 1,000(seed 0) 중 이전 릴리스 확보분.
  **L2 1차**: V1의 transformational, V2의 d_new. (V0 대비 같은 질문의 증분 차이도 보고)
[레버3 pairwise] 세션(e) 단위, I1 ∩ dvol20 ≥ $5M ∩ 매칭, 후보 ≥2 세션, T1S 상위 5개. state = company_A..E(텍스트만),
  Choice "어느 회사가 다음 한 달 가장 큰 지속 초과수익" 3 셔플(seed), 확률벡터 평균 → Jev pick = argmax.
  **L3 1차 = 세션별 net(Jev pick) − net(T1 pick = T1S 최고), 세션 시계열 NW(4) t, OOS 2021+.**
  비교: 절대 점수 tie-breaker(V0 mean P, V0 transformational)도 같은 지표. Bradley-Terry 대신 확률 평균(≤5 동시 비교라 동치에 가까움).
[레버6 사용처]
  (a) 증분: OOS 전체 매칭, OOS I1 각각 — ① OLS gw(1/99 winsor) ~ T1S + f (표준화), ② LPM top10 ~ T1S + f.
      top10 = 같은 연도 매칭 이벤트 gA20 상위 10%(I1은 I1 안에서). 날짜(e) 클러스터 t.
  (b) LightGBM(M2_lgbm_reg, T1 동일) 연도 walk-forward: FEATS_A vs FEATS_A + V1 Jev 벡터(Score는 기대·분산, Choice 확률, Noul),
      직전연도 OOS 90분위 선정, 주별 짝 차이 NW4 t (round1 E1과 동일 절차).
  (c) tie-breaker = 레버3 지표. (d) 사이징 틸트: I1 ∩ dvol≥5M 세션 1위 픽, w = clip(1 + 0.5·z, 0.5, 1.5),
      z = V1 transformational을 IS(2019–20) 평균·표준편차로 표준화. 지표 = 세션별 (w−1)·net, NW4 t.
[채택 기준] 다중검정 바 t ≥ 2.64 (OOS) AND IS 같은 부호 AND OOS 연도 부호 ≥ 4/6 양수. 1차 피처 외는 보조(같은 바).
  꼬리 지표: f 상위 20% 안 top10 적중률 vs 기저 10%, I1 OOS f 상/하 절반 net 차(클러스터 t), 중앙값·상위10% 몫.
[예산] 누적 입력토큰 × $42/1B 가 $4.5 되면 수집 중단(총 한도 $5). SEC는 ≤ 3.3 req/s(0.30s 간격, 단일 락), 캐시 우선.
[추가 사전등록 — dry run 후·본 수집 전] (1) V1 질문 문구는 V0와 동일(데이터만 추가, 문구 교란 제거).
  (2) pairwise 셔플 = seed 고정 기본 순열의 회전, 셔플 수 = min(3, 후보수)(2후보 세션에서 같은 순서 중복 방지).
  (3) 같은 행 비교: V2 커버 행에서 V0/V1/V2 같은 질문 증분을 나란히, FS 커버 행에서 fs_crit vs fs_state를 나란히 보고.
  (4) 가림 누설(약칭·자회사명)은 한계로 기록. PW가 양(+)이면 승리 픽 10건 이름 누설 수동 점검 후 판정.
[KR 사전등록 — 본 수집 중·KR 호출 전 고정] 후보 = model_T2_dart_20260927/events_scored(2025-09~2026-09) 중
  kind ∈ {supply(단일판매·공급계약), rights(유상증자)}, 문서 있음, ex5 존재, 같은 종목·같은 유형의 **직전 공시**(표본 안) 있음.
  state = current_disclosure(가림 원문 앞 1,500자, 한국어 그대로 — round1: 서식 공시는 번역 불필요) +
  previous_disclosure(동일) + size_context(코드 계산 단어 버킷: 금액 현재/직전 배율, 매출액 대비 %, 20일 거래대금, 직전 이후 일수).
  질문: k_signif(Score 5, **KR 1차**), k_new_cp(Noul), k_new_scope(Noul), k_follow_on(Noul).
  평가: elig 행, ex5(1차)·ex1, 1/99 winsor OLS ~ 유형 더미 + log 금액배율 + sup_ratio(log1p) + ret0 + ret5 + log dv20 + f,
  공시일(D) 클러스터 t. 바 t ≥ 2.64 + 전·후반(날짜 중앙값) 같은 부호. (KR 레인 후보 집합 = T2 적격 이벤트로 대체)
[KR] 예산·시간이 남으면 별도 사전등록 후 진행, 아니면 '미실행 + 사유'.
=== 후속 사전등록 H1718 (2026-09-29, 2라운드 결과 확인 후·2017–18 데이터 수집/호출 전 고정) ===
[동결] 피처 = V2의 d_outlook_improved(1차), d_momentum_stronger(2차) — 질문 문구·state 구성(press_release +
  previous_quarter_release, extract() 4,500자, mask)·모델 jev-1.13.0 을 지금 코드 그대로 사용(DIFF_Q·tail_questions 무수정).
[표본] trades_I1.csv.gz 중 year ∈ {2017, 2018} (895건). 8-K 매칭 규칙은 round1과 동일: submissions_full의 8-K items∋2.02,
  |acceptance일(ET) − 이벤트일| ≤ 3, acceptance < entry_date 09:30 ET, 가장 가까운 것. 이전 분기 = acceptance가 이벤트 50–130일 전인
  2.02 8-K 중 가장 최근. 문서 = 인덱스 EX-99*(없으면 8-K 본문). 둘 다 확보된 건만 호출.
[검정] I1 안에서만(무작위 표본 없음): ① OLS gw(1/99 winsor gA20) ~ T1S(M2_lgbm_reg) + f 표준화(1차 지표),
  ② LPM top10_i1(같은 해 I1 상위 10%) ~ T1S + f, ③ f 상위 절반 − 하위 절반 net 차(net = gA20 − 0.44%).
  전부 날짜(e) 클러스터 t. 연도별(2017, 2018) OLS 계수 부호.
[판정 — 고정] 비교 대상 = 2라운드 I1 OOS(2021–26) 부호(d_outlook_improved +0.82 t 2.12, d_momentum_stronger +0.62 t 1.62, 모두 +).
  후보 유지 = 2017–18 ① OLS 계수 부호가 OOS와 같음(+) AND t ≥ 1.65. 아니면 후보 탈락(기각). ②③·연도 부호는 보고만.
  d_momentum_stronger는 2차(같은 규칙, 보고용).
[SEC] 0.30초 간격 단일 락(≤3.3 req/s), 기존 UA, 429면 30초 대기 후 재시도.
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import html
import json
import math
import random
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import warnings
warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
R1 = ROOT / "data" / "analysis" / "jev_8k_t1_20260929"
OUT = ROOT / "data" / "analysis" / "jev_round2_20260929"
CACHE = OUT / "cache"; PDOC = OUT / "prior_docs"
URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"
PRICE = 42e-9; BUDGET = 4.5; FEE = 0.0044
UA = {"User-Agent": "claudetrade research smyang03@gmail.com", "Accept-Encoding": "gzip, deflate"}
_slock = threading.Lock(); _slast = [0.0]
_blk = threading.Lock(); _spent = [0.0]


def jl(p):
    p = Path(p)
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def spent_so_far():
    tot = 0
    for f in CACHE.glob("*.jsonl"):
        for r in jl(f):
            tot += (r.get("usage") or {}).get("input_tokens", 0) or 0
    return tot * PRICE


# ------------------------------------------------------------ SEC (own pacing, ≤3.3 req/s)
def sec_get(url):
    for att in range(4):
        with _slock:
            w = 0.30 - (time.time() - _slast[0])
            if w > 0:
                time.sleep(w)
            _slast[0] = time.time()
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                time.sleep(30)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2 + 3 * att)
    return None


def strip(h):
    h = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(h)).strip()


def fetch_doc(cik, acc):
    for d in (R1 / "docs", PDOC):
        fp = d / f"{acc}.txt"
        if fp.exists():
            return fp.read_text(encoding="utf-8")
    nd = acc.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{nd}/"
    r = sec_get(base + f"{acc}-index.htm")
    if r is None:
        return None
    name = None; prim = None
    for row in re.findall(r'(?is)<tr[^>]*>(.*?)</tr>', r.text):
        m = re.search(r'href="([^"]+)"', row)
        tds = [strip(x) for x in re.findall(r'(?is)<td[^>]*>(.*?)</td>', row)]
        if m and len(tds) >= 4:
            fn = m.group(1).split("/")[-1]; typ = tds[3].upper()
            if fn.lower().endswith((".pdf", ".jpg", ".gif", ".png")):
                continue
            if name is None and typ.startswith("EX-99"):
                name = fn
            if prim is None and typ.startswith("8-K"):
                prim = fn
    name = name or prim
    if not name:
        return None
    d = sec_get(base + name)
    if d is None:
        return None
    txt = strip(d.text)
    PDOC.mkdir(parents=True, exist_ok=True)
    (PDOC / f"{acc}.txt").write_text(txt, encoding="utf-8")
    return txt


# ------------------------------------------------------------ text
def _r1():
    import research_jev_8k_t1_20260929 as r1
    return r1


KW = re.compile(r"(?i)guidance|outlook|record|accelerat|margin|backlog|bookings|contract|customer|launch|approv|award|"
                r"raise|expan|momentum|demand")
BOILER = re.compile(r"(?i)forward[- ]looking statement|safe harbor|private securities litigation")


def extract(t, cap=4500, head=1500):
    h = t[:head]; wins = []
    for mm in KW.finditer(t, head):
        a, b = max(head, mm.start() - 250), min(len(t), mm.start() + 550)
        seg = t[a:b]
        if BOILER.search(seg):
            continue
        ns = re.sub(r"\s", "", seg)
        if ns and sum(c.isdigit() for c in ns) / len(ns) > 0.30:
            continue
        if wins and a <= wins[-1][1]:
            wins[-1][1] = b
        else:
            wins.append([a, b])
        if sum(y - x for x, y in wins) > cap - head:
            break
    body = " ... ".join(t[a:b] for a, b in wins)
    return (h + (" ... " + body if body else ""))[:cap]


# ------------------------------------------------------------ base table
def base():
    fp = OUT / "base.csv.gz"
    if fp.exists():
        return pd.read_csv(fp, parse_dates=["date", "entry_date", "week"])
    o = pd.read_csv(R1 / "oos_scores.csv.gz", parse_dates=["date", "entry_date", "week"])
    keep = ["ticker", "date", "hour", "entry_date", "e", "x20", "week", "year", "gA20", "dvol20", "close_a", "is_i1",
            "is_rand", "acc", "dd", "sue1", "sue2", "sgn", "beats4", "nprev4", "dsue", "days_since", "ret20", "ret60",
            "vol20", "ldvol20", "lsize", "month_sin", "month_cos"]
    o = o[keep]
    sc = pd.read_csv(ROOT / "data/analysis/model_T1_robust_20260927/scores_A_open_H20.csv.gz",
                     usecols=["ticker", "date", "M2_lgbm_reg"], parse_dates=["date"]).rename(columns={"M2_lgbm_reg": "T1S"})
    o = o.merge(sc, on=["ticker", "date"], how="left")
    m = pd.read_csv(R1 / "match.csv", parse_dates=["date"])[["ticker", "date", "cik"]]
    o = o.merge(m, on=["ticker", "date"], how="left")
    cm = json.loads((R1 / "company_tickers.json").read_text())
    title = {int(v["cik_str"]): v["title"] for v in cm.values()}
    o["cname"] = [title.get(int(c), "") if pd.notna(c) else "" for c in o.cik]
    rs = np.random.RandomState(0)
    rnd = o[o.is_rand & ~o.is_i1].index
    o["v2set"] = o.is_i1 | o.index.isin(rs.choice(rnd, 1000, replace=False))
    OUT.mkdir(parents=True, exist_ok=True)
    o.to_csv(fp, index=False, compression="gzip")
    return o


def masked(acc, names, which="r1"):
    t = fetch_doc(0, acc) if which == "prior" else (R1 / "docs" / f"{acc}.txt").read_text(encoding="utf-8")
    return _r1().mask(t, names)


# ------------------------------------------------------------ facts (fixed cut-offs, pre-registered)
def _b(x, cuts, labels):
    if pd.isna(x):
        return "unknown"
    for c, l in zip(cuts, labels):
        if x < c:
            return l
    return labels[-1]


def facts(r):
    return {
        "eps_vs_consensus": _b(r.sue1, [-0.05, 0.05, 0.15, 0.5], ["missed consensus", "in line with consensus",
                               "small beat", "solid beat", "large beat", "large beat"]),
        "consecutive_beats_last_4_quarters": f"{int(r.beats4) if pd.notna(r.beats4) else 'unknown'} of 4",
        "stock_move_20_days_before": _b(r.ret20, [-0.10, -0.03, 0.03, 0.10], ["fell sharply", "fell", "flat", "rose",
                                        "rose sharply", "rose sharply"]),
        "stock_move_60_days_before": _b(r.ret60, [-0.20, -0.05, 0.05, 0.20], ["fell sharply", "fell", "flat", "rose",
                                        "rose sharply", "rose sharply"]),
        "company_size": _b(r.lsize, [16.5, 18, 19.5], ["micro-cap", "small-cap", "mid-cap", "large-cap"]),
        "stock_volatility": _b(r.vol20, [0.015, 0.03], ["low", "moderate", "high"]),
    }


# ------------------------------------------------------------ questions
P_Q = {
    "P1": "Will this company's stock strongly outperform the market over the next month after this earnings release?",
    "P2": ("Is this earnings release among the best 10% of quarterly reports, the kind that leads to a large and "
           "sustained rise in the stock price?"),
    "P3": "Would an experienced investor, reading this, expect the stock to keep outperforming the market for several weeks?",
    "P4": ("Does this quarter show a positive surprise large enough that the market is likely to keep re-rating the "
           "stock upward over the next month?"),
}


def tail_questions(ref="`press_release`"):
    q = {
        "transformational": {"type": "score",
                             "instructions": f"How much does the quarter described in {ref} change the company's growth or profitability trajectory?",
                             "criteria": ["Routine quarter; nothing changes the outlook",
                                          "Slightly better than usual; no real change in trajectory",
                                          "Notable improvement in trajectory",
                                          "Major positive inflection in growth or profitability",
                                          "Transformational quarter that resets the company's growth trajectory"]},
        "new_driver": {"type": "noul",
                       "instructions": f"Does {ref} announce a new product, major contract, large customer win, or regulatory approval that could materially accelerate future revenue?"},
        "margin_step": {"type": "noul",
                        "instructions": f"Does {ref} report a large step-up in gross or operating margin versus the prior year, not just a small change?"},
        "growth_accel": {"type": "choice",
                         "instructions": f"According to {ref}, is revenue growth accelerating or decelerating compared with recent quarters?",
                         "criteria": {"accelerating": "growth rate is clearly higher than in recent quarters",
                                      "stable": "growth rate is about the same",
                                      "decelerating": "growth rate is lower, or revenue is declining faster",
                                      "unclear": "the text does not allow a comparison"}},
        "guide_far": {"type": "score",
                      "instructions": f"How does the forward guidance in {ref} compare with prior guidance or expectations?",
                      "criteria": ["Guidance cut", "No guidance, or guidance unchanged", "Guidance raised slightly",
                                   "Guidance raised substantially", "Guidance raised far above the prior range or expectations"]},
        "beat_quality": {"type": "choice",
                         "instructions": f"What mainly drove the results in {ref}?",
                         "criteria": {"recurring_operational": "recurring operating improvement: demand, pricing, volume, efficiency",
                                      "one_time_items": "one-time gains, tax items, asset sales or other non-recurring items",
                                      "mixed": "both recurring improvement and one-time items matter",
                                      "weak_or_unclear": "results were weak, or the driver is unclear"}},
        "record": {"type": "noul", "instructions": f"Does management in {ref} describe the results as record or exceptional?"},
    }
    for k, v in P_Q.items():
        q[k] = {"type": "noul", "instructions": v.replace("this earnings release", ref).replace("reading this", f"reading {ref}")
                .replace("this quarter", f"the quarter in {ref}")}
    return q


DIFF_Q = {
    "d_momentum": {"type": "choice",
                   "instructions": "Compared with `previous_quarter_release`, is the business momentum described in `press_release` stronger, about the same, or weaker?",
                   "criteria": {"stronger": "clearly stronger momentum than the previous quarter",
                                "same": "about the same momentum",
                                "weaker": "clearly weaker momentum than the previous quarter"}},
    "d_new": {"type": "noul",
              "instructions": ("Does `press_release` contain a major new positive development (new product, contract, customer, "
                               "margin step-up, or raised outlook) that was not present in `previous_quarter_release`?")},
    "d_outlook": {"type": "choice",
                  "instructions": "Compared with `previous_quarter_release`, did the company's outlook in `press_release` improve, stay the same, or worsen?",
                  "criteria": {"improved": "outlook is better than in the previous quarter's release",
                               "same": "outlook is essentially the same",
                               "worsened": "outlook is worse than in the previous quarter's release",
                               "not_comparable": "one of the releases gives no outlook"}},
}


# ------------------------------------------------------------ few-shot pools
def headline(acc, names, n):
    t = masked(acc, names)
    return t[:n]


def fs_crit_by_year(B):
    """연도별 criteria 예시: x20 < 해당 연도 첫 e, gA20 ≥ +15% / ≤ −15%, seed=Y."""
    out = {}
    first_e = B.groupby("year").e.min()
    for Y in sorted(B.year.unique()):
        pool = B[B.x20 < first_e[Y]]
        top = pool[pool.gA20 >= 0.15]; bot = pool[pool.gA20 <= -0.15]
        if len(top) < 3 or len(bot) < 3:
            continue
        rs = np.random.RandomState(int(Y))
        tp = top.iloc[rs.choice(len(top), 3, replace=False)]; bt = bot.iloc[rs.choice(len(bot), 3, replace=False)]
        out[int(Y)] = {"true": [headline(r.acc, [r.cname, r.ticker], 300) for r in tp.itertuples()],
                       "false": [headline(r.acc, [r.cname, r.ticker], 300) for r in bt.itertuples()]}
    return out


def fs_crit_q(ex):
    return {"type": "noul",
            "instructions": "Is the quarter in `press_release` the kind that leads to a large, sustained rise in the stock over the next month?",
            "criteria": {"true": {"what": "a release like those that were followed by strong, sustained outperformance",
                                  "examples": ex["true"]},
                         "false": {"what": "a release like those that were followed by clear underperformance",
                                   "examples": ex["false"]}}}


# ------------------------------------------------------------ Jev call
def post(body, key, tag):
    err = ""
    for att in range(5):
        t0 = time.time()
        try:
            r = requests.post(URL, json=body, headers={"Authorization": f"Bearer {key}"}, timeout=120)
            if r.status_code == 200:
                j = r.json()
                with _blk:
                    _spent[0] += ((j.get("usage") or {}).get("input_tokens", 0) or 0) * PRICE
                return {"key": tag, "ans": j["answers"], "usage": j.get("usage"), "model": j.get("model"),
                        "sec": round(time.time() - t0, 3)}
            err = f"HTTP {r.status_code} {r.text[:150]}"
            if r.status_code in (429, 529):
                time.sleep(3 * (att + 1)); continue
            if r.status_code in (401, 422):
                break
        except Exception as exc:  # noqa: BLE001
            err = type(exc).__name__
        time.sleep(2 * (att + 1))
    return {"key": tag, "error": err}


def run(variant, jobs, threads=4):
    """jobs: list of (tag, body_fn). body_fn lazily builds body. append-only cache."""
    CACHE.mkdir(parents=True, exist_ok=True)
    fp = CACHE / f"{variant}.jsonl"
    done = {r["key"] for r in jl(fp) if "ans" in r}
    todo = [j for j in jobs if j[0] not in done]
    _spent[0] = spent_so_far()
    print(f"[{variant}] todo {len(todo)} / {len(jobs)} · spent ${_spent[0]:.3f}", flush=True)
    key = _r1().jkey(); t0 = time.time(); n = 0

    def work(j):
        if _spent[0] >= BUDGET:
            return {"key": j[0], "error": "budget"}
        try:
            b = j[1]()
        except Exception as exc:  # noqa: BLE001
            return {"key": j[0], "error": "build:" + type(exc).__name__ + str(exc)[:80]}
        if b is None:
            return {"key": j[0], "error": "nodata"}
        return post(b, key, j[0])

    with cf.ThreadPoolExecutor(threads) as ex, fp.open("a", encoding="utf-8") as fh:
        for res in ex.map(work, todo):
            if res.get("error") == "budget":
                continue
            fh.write(json.dumps(res) + "\n"); fh.flush(); n += 1
            if n % 500 == 0:
                print(f"  {n} · {time.time()-t0:.0f}s · ${_spent[0]:.3f}", flush=True)
    print(f"[{variant}] wrote {n} in {time.time()-t0:.0f}s · spent ${_spent[0]:.3f}", flush=True)


# ------------------------------------------------------------ stages
def jobs_v(variant, limit=None):
    B = base()
    if variant == "V2":
        pr = priors()
        B = B[B.v2set].merge(pr, on="acc", how="inner")
        B = B[B.prior_ok]
    if variant == "FS":
        B = B[B.year >= 2020]
    crit = fs_crit_by_year(base()) if variant == "V0" else None
    allb = base() if variant == "FS" else None
    jobs = []
    for r in B.itertuples():
        names = [r.cname, r.ticker]

        def mk(r=r, names=names):
            cur = extract(masked(r.acc, names))
            q = tail_questions()
            if variant == "V0":
                st = {"press_release": cur}
                if int(r.year) in crit:
                    q["fs_crit"] = fs_crit_q(crit[int(r.year)])
            elif variant == "V1":
                st = {"press_release": cur, "facts": facts(r)}   # 질문 문구는 V0와 동일(데이터만 추가)
            elif variant == "V2":
                st = {"press_release": cur, "previous_quarter_release": extract(_r1().mask(fetch_doc(0, r.prior_acc), names))}
                q.update(DIFF_Q)
            elif variant == "FS":
                pool = allb[allb.x20 < r.e]
                top = pool[pool.gA20 >= 0.15]; bot = pool[pool.gA20 <= -0.15]
                if len(top) < 3 or len(bot) < 3:
                    return None
                rs = np.random.RandomState(int(hashlib.md5(r.acc.encode()).hexdigest()[:8], 16))
                ex = [(x, "followed by strong sustained outperformance") for x in top.iloc[rs.choice(len(top), 3, replace=False)].itertuples()] + \
                     [(x, "followed by clear underperformance") for x in bot.iloc[rs.choice(len(bot), 3, replace=False)].itertuples()]
                rs.shuffle(ex)
                st = {"current_release": extract(masked(r.acc, names), cap=2500, head=1000),
                      "past_examples": [{"release_headline": headline(x.acc, [x.cname, x.ticker], 500), "stock_outcome": lab}
                                        for x, lab in ex]}
                q = {"fs_state": {"type": "noul",
                                  "instructions": ("Is `current_release` more like the `past_examples` that were followed by strong "
                                                   "sustained outperformance than those followed by underperformance?")}}
            return {"state": st, "model": MODEL, "questions": q}
        jobs.append((r.acc, mk))
    return jobs[:limit]


def priors():
    fp = OUT / "priors.csv"
    if fp.exists():
        return pd.read_csv(fp)
    B = base(); B = B[B.v2set]
    rows = []
    for r in B.itertuples():
        cik = int(r.cik)
        fs = []
        for d in (R1 / "submissions_full", R1 / "submissions"):
            f = d / f"{cik}.json"
            if f.exists():
                x = pd.read_json(f, dtype=False)
                if len(x):
                    if "form" in x:
                        x = x[x.form == "8-K"]
                    x = x[x["items"].astype(str).str.contains("2.02", regex=False)]
                    fs.append(x[["accessionNumber", "acceptanceDateTime"]])
        rec = {"acc": r.acc, "prior_acc": None, "prior_gap": None, "prior_ok": False}
        if fs:
            x = pd.concat(fs).drop_duplicates("accessionNumber").reset_index(drop=True)
            at = pd.to_datetime(x.acceptanceDateTime, utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
            gap = (r.date - at.dt.normalize()).dt.days
            ok = (gap >= 50) & (gap <= 130) & (x.accessionNumber != r.acc)
            if ok.any():
                j = gap[ok].sort_values().index[0]
                rec.update(prior_acc=x.accessionNumber[j], prior_gap=int(gap[j]), cik=cik)
        rows.append(rec)
    P = pd.DataFrame(rows)
    todo = P[P.prior_acc.notna()]
    print("prior candidates", len(todo), "of", len(P), flush=True)
    t0 = time.time(); ok = []
    for i, r in enumerate(todo.itertuples()):
        t = fetch_doc(int(r.cik), r.prior_acc)
        ok.append(bool(t and len(t) > 200))
        if i % 200 == 0:
            print(f"  prior {i} {time.time()-t0:.0f}s", flush=True)
    P.loc[todo.index, "prior_ok"] = ok
    P.to_csv(fp, index=False)
    print("prior ok", int(P.prior_ok.sum()), f"{time.time()-t0:.0f}s")
    return P


def jobs_pw(limit=None):
    B = base()
    S = B[B.is_i1 & (B.dvol20 >= 5e6) & B.T1S.notna()]
    jobs = []
    for e, g in S.groupby("e"):
        if len(g) < 2:
            continue
        g = g.sort_values("T1S", ascending=False).head(5)
        for sh in range(min(3, len(g))):
            perm0 = np.random.RandomState(int(e)).permutation(len(g)); perm = np.roll(perm0, sh)  # 회전 → 셔플마다 위치가 바뀜
            labs = "ABCDE"[:len(g)]
            cand = [g.iloc[p] for p in perm]

            def mk(cand=cand, labs=labs):
                st = {f"company_{l}": extract(_r1().mask((R1 / "docs" / f"{c.acc}.txt").read_text(encoding="utf-8"),
                                                         [c.cname, c.ticker]), cap=1800, head=900) for l, c in zip(labs, cand)}
                return {"state": st, "model": MODEL, "questions": {
                    "pick": {"type": "choice",
                             "instructions": ("Each field is one company's quarterly earnings release. Which company's quarter "
                                              "is most likely to lead to the largest sustained stock outperformance over the next month?"),
                             "criteria": {l: f"the company in `company_{l}`" for l in labs}}}}
            jobs.append((f"{int(e)}|{sh}|" + ",".join(c.acc for c in cand), mk))
    return jobs[:limit]


# ------------------------------------------------------------ features
def feats_of(variant):
    rows = []
    for r in jl(CACHE / f"{variant}.jsonl"):
        if "ans" not in r:
            continue
        a = r["ans"]; f = {"acc": r["key"]}
        for k, v in a.items():
            t = v.get("type")
            if t == "noul":
                f[k] = v["noul"]
            elif t == "score":
                p = {int(i): x for i, x in v["probabilities"].items()}
                mu = sum(i * x for i, x in p.items()); f[k] = mu
                f[k + "_sd"] = math.sqrt(max(sum((i - mu) ** 2 * x for i, x in p.items()), 0))
            elif t == "choice":
                for c, x in v["probabilities"].items():
                    f[f"{k}_{c}"] = x
        ps = [f[p] for p in P_Q if p in f]
        if ps:
            f["meanP"] = float(np.mean(ps))
        rows.append(f)
    D = pd.DataFrame(rows).drop_duplicates("acc", keep="last")
    return D.set_index("acc").add_prefix(variant + "_").reset_index()


def cl_ols(y, X, g):
    X = np.column_stack([np.ones(len(y)), X]); beta = np.linalg.lstsq(X, y, rcond=None)[0]; u = y - X @ beta
    Bi = np.linalg.pinv(X.T @ X)
    gg = pd.factorize(pd.Series(g).astype(str))[0]
    sc = np.zeros((gg.max() + 1, X.shape[1])); np.add.at(sc, gg, X * u[:, None])
    G = gg.max() + 1
    V = Bi @ (sc.T @ sc) @ Bi * G / max(G - 1, 1)
    se = np.sqrt(np.maximum(np.diag(V), 1e-18))
    return beta, beta / se


def incr(d, f):
    """d: rows with gA20,T1S,e,year,top10; f: feature col. Returns OLS(gw) & LPM(top10) coef on f controlling T1S."""
    d = d[d[f].notna() & d.T1S.notna()]
    if len(d) < 50 or d[f].std() == 0:
        return None
    X = d[["T1S", f]].astype(float); X = ((X - X.mean()) / X.std()).values
    lo, hi = d.gA20.quantile([.01, .99]); gw = d.gA20.clip(lo, hi).values
    b1, t1_ = cl_ols(gw, X, d.e.values)
    b2, t2_ = cl_ols(d.top10.values.astype(float), X, d.e.values)
    yrs = {}
    for y, g in d.groupby("year"):
        if len(g) >= 30 and g[f].std() > 0:
            Xg = g[["T1S", f]].astype(float); Xg = ((Xg - Xg.mean()) / Xg.std()).values
            yrs[int(y)] = round(float(np.linalg.lstsq(np.column_stack([np.ones(len(g)), Xg]), g.gA20.clip(lo, hi).values, rcond=None)[0][2]) * 100, 2)
    q = d[f].rank(pct=True)
    return {"n": int(len(d)), "ols_b_pct_sd": round(float(b1[2]) * 100, 3), "ols_t": round(float(t1_[2]), 2),
            "t1_t": round(float(t1_[1]), 2), "lpm_b_pp_sd": round(float(b2[2]) * 100, 2), "lpm_t": round(float(t2_[2]), 2),
            "hit_top20": round(float(d.top10[q > 0.8].mean()), 3), "hit_base": round(float(d.top10.mean()), 3),
            "year_b": yrs, "year_pos": int(sum(v > 0 for v in yrs.values())), "n_years": len(yrs)}


def split_i1(d, f):
    d = d[d[f].notna()]
    if len(d) < 50:
        return None
    hi = d[f] >= d[f].median()
    b, t = cl_ols(d.net.values, hi.values.astype(float)[:, None], d.e.values)

    def dist(x):
        x = x.sort_values(ascending=False); tot = x.sum()
        return {"n": int(len(x)), "mean": round(float(x.mean()) * 100, 2), "median": round(float(x.median()) * 100, 2),
                "top10_share": round(float(x.head(max(1, len(x) // 10)).sum() / tot), 2) if tot != 0 else None}
    return {"hi_minus_lo_pp": round(float(b[1]) * 100, 2), "t": round(float(t[1]), 2), "hi": dist(d.net[hi]), "lo": dist(d.net[~hi])}


def verdict(res_oos, res_is, key="ols_t"):
    if not res_oos:
        return "산출 불가"
    t = res_oos[key]; sgn_is = res_is and np.sign(res_is["ols_b_pct_sd"] if key == "ols_t" else res_is["lpm_b_pp_sd"])
    ok = t >= 2.64 and (sgn_is == 1) and res_oos["year_pos"] >= max(4, math.ceil(res_oos["n_years"] * 4 / 6))
    if ok:
        return "확정(바 통과)"
    if t >= 2.0:
        return "후보"
    return "기각"


def build_eval():
    import research_model_T1_earnings_20260927 as t1
    B = base()
    B["net"] = B.gA20 - FEE
    B["top10"] = B.gA20 >= B.groupby("year").gA20.transform(lambda x: x.quantile(0.9))
    B["top10_i1"] = False
    I = B.is_i1
    B.loc[I, "top10_i1"] = B[I].gA20 >= B[I].groupby("year").gA20.transform(lambda x: x.quantile(0.9))
    for v in ("V0", "V1", "V2", "FS"):
        if (CACHE / f"{v}.jsonl").exists():
            B = B.merge(feats_of(v), on="acc", how="left")
    R = {"n": len(B), "cost": {}}
    for f in CACHE.glob("*.jsonl"):
        rr = jl(f); ok = [r for r in rr if "ans" in r]
        R["cost"][f.stem] = {"calls": len(ok), "errors": len(rr) - len(ok),
                             "in_tok": int(sum((r.get("usage") or {}).get("input_tokens", 0) for r in ok)),
                             "usd": round(sum((r.get("usage") or {}).get("input_tokens", 0) for r in ok) * PRICE, 3),
                             "models": sorted({r.get("model") for r in ok if r.get("model")}),
                             "sec_med": float(np.median([r["sec"] for r in ok])) if ok else None}
    # ---------- increment tests
    cand = [c for c in B.columns if re.match(r"^(V0|V1|V2|FS)_", c) and not c.endswith("_sd")]
    INC = {}
    for c in cand:
        row = {}
        for nm, msk in (("all", pd.Series(True, index=B.index)), ("i1", B.is_i1), ("rand", B.is_rand)):
            for per, pm in (("IS", B.year <= 2020), ("OOS", B.year >= 2021)):
                d = B[msk & pm].copy()
                if nm == "i1":
                    d["top10"] = d.top10_i1
                row[f"{nm}_{per}"] = incr(d, c)
        row["i1_split_OOS"] = split_i1(B[B.is_i1 & (B.year >= 2021)], c)
        INC[c] = row
    R["INC"] = INC
    SAME = {}
    if "V2_transformational" in B:
        m2 = B.V2_transformational.notna()
        for c in ["V0_transformational", "V1_transformational", "V2_transformational", "V0_meanP", "V1_meanP", "V2_meanP",
                  "V0_new_driver", "V2_new_driver", "V2_d_new", "V2_d_momentum_stronger", "V2_d_outlook_improved"]:
            if c in B:
                SAME["V2rows|" + c] = {nm: incr(B[m2 & msk & (B.year >= 2021)].assign(
                    top10=lambda x: x.top10_i1 if nm == "i1" else x.top10), c)
                    for nm, msk in (("all", pd.Series(True, index=B.index)), ("i1", B.is_i1))}
    if "FS_fs_state" in B:
        mf = B.FS_fs_state.notna() & B.V0_fs_crit.notna() if "V0_fs_crit" in B else B.FS_fs_state.notna()
        for c in ["V0_fs_crit", "FS_fs_state", "V0_meanP"]:
            if c in B:
                SAME["FSrows|" + c] = {nm: incr(B[mf & msk & (B.year >= 2021)].assign(
                    top10=lambda x: x.top10_i1 if nm == "i1" else x.top10), c)
                    for nm, msk in (("all", pd.Series(True, index=B.index)), ("i1", B.is_i1))}
    R["SAME"] = SAME
    PRIMARY = {"L1 transformational(V0)": "V0_transformational", "L5 meanP(V0)": "V0_meanP", "L5 P1 단독(V0)": "V0_P1",
               "L2 V1 transformational": "V1_transformational", "L2 V2 d_new": "V2_d_new",
               "L2 V2 transformational": "V2_transformational", "L4 fs_state": "FS_fs_state", "L4 fs_crit": "V0_fs_crit",
               "L5 meanP(V1)": "V1_meanP"}
    R["PRIMARY"] = {}
    for lab, c in PRIMARY.items():
        if c not in INC:
            continue
        x = INC[c]
        R["PRIMARY"][lab] = {"col": c, "all_OOS": x["all_OOS"], "i1_OOS": x["i1_OOS"], "all_IS": x["all_IS"],
                             "verdict_all_ols": verdict(x["all_OOS"], x["all_IS"], "ols_t"),
                             "verdict_all_lpm": verdict(x["all_OOS"], x["all_IS"], "lpm_t"),
                             "verdict_i1_ols": verdict(x["i1_OOS"], x["i1_IS"], "ols_t"),
                             "verdict_i1_lpm": verdict(x["i1_OOS"], x["i1_IS"], "lpm_t"),
                             "i1_split": x["i1_split_OOS"]}
    # ---------- LightGBM walk-forward (V1 vector)
    jv = [c for c in B.columns if c.startswith("V1_")]
    if jv:
        M = B[B[jv[0]].notna()].sort_values("e").reset_index(drop=True)
        first_e = M.groupby("year").e.min()
        for tag, fts in (("T1", t1.FEATS_A), ("T1J", t1.FEATS_A + jv)):
            M[tag] = np.nan
            for Y in range(2020, 2027):
                if Y not in first_e:
                    continue
                tr = M[(M.year < Y) & (M.x20 < first_e[Y])]; te = M[M.year == Y]
                M.loc[te.index, tag] = t1.fit_predict("M2_lgbm_reg", tr, te, fts, "gA20")[0]
        sel = {}
        for tag in ("T1", "T1J"):
            q = M.groupby("year")[tag].quantile(0.9)
            M[f"sel_{tag}"] = (M.year >= 2021) & (M[tag] >= M.year.map(lambda y: q.get(y - 1, np.nan)))
            S = M[M[f"sel_{tag}"]]
            sel[tag] = {"n": int(len(S)), "net": round(float(S.net.mean()) * 100, 2),
                        "t_day": round(float(cl_ols(S.net.values, np.zeros((len(S), 0)), S.e.values)[1][0]), 2)}
        W = pd.concat({t_: M[M[f"sel_{t_}"]].groupby("week").net.mean() for t_ in ("T1", "T1J")}, axis=1).dropna()
        dd = W.T1J - W.T1
        from scipy.stats import spearmanr
        o = M[M.T1.notna()]
        R["LGBM"] = {"sel": sel, "weeks": int(len(W)), "diff_pp": round(float(dd.mean()) * 100, 2),
                     "t_nw4": round(t1.nw_t(dd.values, 4), 2),
                     "ic": {t_: round(float(spearmanr(o[t_], o.gA20).correlation), 3) for t_ in ("T1", "T1J")}}
    # ---------- pairwise / tie-breaker
    R["TIE"] = tie(B)
    # ---------- sizing tilt
    R["TILT"] = tilt(B)
    B.to_csv(OUT / "eval_table.csv.gz", index=False, compression="gzip")
    (OUT / "results.json").write_text(json.dumps(R, indent=1, default=str), encoding="utf-8")
    return R


def tie(B):
    import research_model_T1_earnings_20260927 as t1
    rows = jl(CACHE / "PW.jsonl")
    agg = {}
    for r in rows:
        if "ans" not in r:
            continue
        e, sh, accs = r["key"].split("|"); accs = accs.split(",")
        p = r["ans"]["pick"]["probabilities"]
        for l, a in zip("ABCDE", accs):
            agg.setdefault(int(e), {}).setdefault(a, []).append(p.get(l, 0.0))
    Bi = B.drop_duplicates("acc").set_index("acc")
    out = {}
    recs = []
    for e, d in agg.items():
        accs = list(d); sub = Bi.loc[accs]
        pw = {a: float(np.mean(v)) for a, v in d.items()}
        rec = {"e": e, "year": int(sub.year.iloc[0]), "n": len(accs),
               "t1": sub.net[sub.T1S.idxmax()],
               "pw": sub.net[max(pw, key=pw.get)],
               "rand": sub.net.mean(),
               "pw_pos_bias": None}
        for c in ("V0_meanP", "V0_transformational", "V1_meanP", "V1_transformational"):
            if c in sub and sub[c].notna().all():
                rec[c] = sub.net[sub[c].idxmax()]
        rec["oracle"] = sub.net.max()
        recs.append(rec)
    S = pd.DataFrame(recs).sort_values("e")
    if not len(S):
        return None
    for per, pm in (("IS", S.year <= 2020), ("OOS", S.year >= 2021)):
        x = S[pm]; res = {"sessions": int(len(x))}
        for c in ["pw", "rand", "V0_meanP", "V0_transformational", "V1_meanP", "V1_transformational", "oracle"]:
            if c in x and x[c].notna().any():
                dd = (x[c] - x.t1).dropna()
                res[c] = {"diff_pp": round(float(dd.mean()) * 100, 2), "t_nw4": round(t1.nw_t(dd.values, 4), 2),
                          "same_as_t1": round(float((dd == 0).mean()), 2),
                          "year_diff": {int(y): round(float(g.mean()) * 100, 2) for y, g in dd.groupby(x.year[dd.index])}}
        res["t1_net"] = round(float(x.t1.mean()) * 100, 2)
        out[per] = res
    # position bias
    pos = {}
    for r in rows:
        if "ans" in r:
            pos.setdefault(len(r["key"].split("|")[2].split(",")), []).append(r["ans"]["pick"]["choice"])
    out["pos_bias"] = {k: pd.Series(v).value_counts(normalize=True).round(2).to_dict() for k, v in pos.items()}
    S.to_csv(OUT / "tie_sessions.csv", index=False)
    return out


def tilt(B):
    import research_model_T1_earnings_20260927 as t1
    f = "V1_transformational"
    if f not in B:
        return None
    S = B[B.is_i1 & (B.dvol20 >= 5e6) & B[f].notna()].sort_values(["e", "T1S"], ascending=[True, False]).groupby("e").head(1)
    mu, sd = S[S.year <= 2020][f].mean(), S[S.year <= 2020][f].std()
    S = S.assign(w=np.clip(1 + 0.5 * (S[f] - mu) / sd, 0.5, 1.5))
    S["gain"] = (S.w - 1) * S.net
    out = {}
    for per, pm in (("IS", S.year <= 2020), ("OOS", S.year >= 2021)):
        x = S[pm]
        out[per] = {"n": int(len(x)), "eq_net": round(float(x.net.mean()) * 100, 2),
                    "w_net": round(float((x.w * x.net).sum() / x.w.sum()) * 100, 2),
                    "gain_pp": round(float(x.gain.mean()) * 100, 3), "t_nw4": round(t1.nw_t(x.gain.values, 4), 2)}
    return out


# ============================================================ KR diff (supply / rights)
T2O = ROOT / "data" / "analysis" / "model_T2_dart_20260927"


def kr_set():
    E = pd.DataFrame(jl(T2O / "events_scored.jsonl"))
    E = E[E.kind.isin(["supply", "rights"]) & E.has_doc.astype(bool) & E.ex5.notna()].sort_values(["rcept_dt", "rcept_no"])
    g = E.groupby(["stock", "kind"])
    for c in ("rcept_no", "rcept_dt", "sup_log_amt", "ri_log_total"):
        E["prev_" + c] = g[c].shift(1)
    E = E[E.prev_rcept_no.notna()].copy()
    E["amt_ratio"] = np.where(E.kind == "supply", E.sup_log_amt - E.prev_sup_log_amt, E.ri_log_total - E.prev_ri_log_total)
    E["gap_days"] = (pd.to_datetime(E.rcept_dt.astype(str)) - pd.to_datetime(E.prev_rcept_dt.astype(str))).dt.days
    return E.reset_index(drop=True)


def _ratio_words(x):
    if pd.isna(x):
        return "unknown"
    r = math.exp(x)
    return _b(r, [0.33, 0.8, 1.25, 3.0], ["much smaller than the previous one", "smaller than the previous one",
                                          "about the same size as the previous one", "larger than the previous one",
                                          "much larger (3x or more) than the previous one"])


def jobs_kr(limit=None):
    import research_model_T2_dart_20260927 as t2
    E = kr_set(); jobs = []
    for r in E.itertuples():
        def mk(r=r):
            cur = t2.mask_text((t2.DOCS / f"{r.rcept_no}.txt").read_text(encoding="utf-8"), r.name, r.stock)[:1500]
            prv = t2.mask_text((t2.DOCS / f"{r.prev_rcept_no}.txt").read_text(encoding="utf-8"), r.name, r.stock)[:1500]
            ctx = {"disclosure_type": "single-sale or supply contract" if r.kind == "supply" else "rights offering (new share issuance)",
                   "amount_vs_previous": _ratio_words(r.amt_ratio),
                   "days_since_previous": int(r.gap_days) if pd.notna(r.gap_days) else None,
                   "avg_daily_trading_value": _b(r.dv20, [20, 100, 500], ["small (<2bn KRW)", "moderate (2-10bn KRW)",
                                                 "large (10-50bn KRW)", "very large (>50bn KRW)"]) if pd.notna(r.dv20) else "unknown"}
            if r.kind == "supply" and pd.notna(r.sup_ratio):
                ctx["contract_pct_of_annual_revenue"] = _b(r.sup_ratio, [5, 10, 20, 50], ["under 5%", "5-10%", "10-20%", "20-50%", "50% or more"])
            q = {"k_signif": {"type": "score",
                              "instructions": "Compared with `previous_disclosure`, how much more significant is `current_disclosure` for the company's future business?",
                              "criteria": ["Much less significant", "Less significant", "About equally significant",
                                           "More significant", "Much more significant"]},
                 "k_new_cp": {"type": "noul", "instructions": "Is the counterparty or investor in `current_disclosure` different from the one in `previous_disclosure`?"},
                 "k_new_scope": {"type": "noul", "instructions": "Does `current_disclosure` involve a new product, service, region or business area not covered by `previous_disclosure`?"},
                 "k_follow_on": {"type": "noul", "instructions": "Is `current_disclosure` a follow-on, extension or amendment of the same deal as `previous_disclosure`?"}}
            return {"state": {"current_disclosure": cur, "previous_disclosure": prv, "size_context": ctx}, "model": MODEL, "questions": q}
        jobs.append((r.rcept_no, mk))
    return jobs[:limit]


def eval_kr():
    E = kr_set(); F = feats_of("KR").rename(columns={"acc": "rcept_no"})
    E = E.merge(F, on="rcept_no", how="inner")
    E = E[E.elig.astype(bool)].copy()
    E["is_sup"] = (E.kind == "supply").astype(float)
    E["sr"] = np.log1p(E.sup_ratio.fillna(0).clip(0, 1000))
    E["ldv"] = np.log(E.dv20.clip(lower=1))
    ctrl = ["is_sup", "amt_ratio", "sr", "ret0", "ret5", "ldv"]
    med = E.rcept_dt.astype(str).sort_values().iloc[len(E) // 2]
    out = {"n": int(len(E)), "by_kind": E.kind.value_counts().to_dict()}
    for y in ("ex5", "ex1"):
        for f in ["KR_k_signif", "KR_k_new_cp", "KR_k_new_scope", "KR_k_follow_on"]:
            res = {}
            for nm, d in (("all", E), ("h1", E[E.rcept_dt.astype(str) <= med]), ("h2", E[E.rcept_dt.astype(str) > med])):
                d = d[d[ctrl + [f, y]].notna().all(axis=1)]
                X = d[ctrl + [f]].astype(float); X = ((X - X.mean()) / X.std().replace(0, 1)).values
                lo, hi = d[y].quantile([.01, .99])
                b, t = cl_ols(d[y].clip(lo, hi).values, X, d.D.values)
                res[nm] = {"n": int(len(d)), "b_pp_sd": round(float(b[-1]), 3), "t": round(float(t[-1]), 2)}
            ok = res["all"]["t"] >= 2.64 and np.sign(res["h1"]["b_pp_sd"]) == np.sign(res["h2"]["b_pp_sd"]) == 1
            res["verdict"] = "확정(바 통과)" if ok else ("후보" if abs(res["all"]["t"]) >= 2 else "기각")
            out[f"{y}|{f}"] = res
    (OUT / "results_kr.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return out


# ============================================================ H1718: 2017–18 I1 독립 창
def h_table():
    fp = OUT / "h1718.csv"
    if fp.exists():
        return pd.read_csv(fp, parse_dates=["date", "entry_date"])
    T = pd.read_csv(ROOT / "data/analysis/model_T1_impl_20260927/trades_I1.csv.gz", parse_dates=["date", "entry_date"])
    T = T[T.year <= 2018].copy().rename(columns={"M2_lgbm_reg": "T1S"})
    cm = json.loads((R1 / "company_tickers.json").read_text())
    tk = {v["ticker"].upper(): int(v["cik_str"]) for v in cm.values()}
    title = {int(v["cik_str"]): v["title"] for v in cm.values()}
    T["cik"] = T.ticker.str.upper().str.replace(".", "-", regex=False).map(tk)
    rows = []
    for r in T.itertuples():
        rec = {"acc": None, "prior_acc": None, "status": "no_cik"}
        f = R1 / "submissions_full" / f"{int(r.cik)}.json" if pd.notna(r.cik) else None
        if f is not None and f.exists():
            x = pd.read_json(f, dtype=False)
            x = x[(x.form == "8-K") & x["items"].astype(str).str.contains("2.02", regex=False)].reset_index(drop=True) if len(x) else x
            rec["status"] = "no_8k"
            if len(x):
                at = pd.to_datetime(x.acceptanceDateTime, utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
                dd = (at.dt.normalize() - r.date).dt.days
                ok = (dd.abs() <= 3) & (at < r.entry_date + pd.Timedelta(hours=9, minutes=30))
                if ok.any():
                    j = dd[ok].abs().sort_values().index[0]
                    rec.update(acc=x.accessionNumber[j], status="matched")
                    gap = -dd
                    okp = (gap >= 50) & (gap <= 130)
                    if okp.any():
                        rec["prior_acc"] = x.accessionNumber[gap[okp].sort_values().index[0]]
                elif (dd.abs() <= 3).any():
                    rec["status"] = "after_cutoff"
        rows.append(rec)
    T = pd.concat([T.reset_index(drop=True), pd.DataFrame(rows)], axis=1)
    T["cname"] = [title.get(int(c), "") if pd.notna(c) else "" for c in T.cik]
    todo = T[T.acc.notna() & T.prior_acc.notna()]
    print("status", T.status.value_counts().to_dict(), "with prior", len(todo), flush=True)
    t0 = time.time(); ok = []
    for i, r in enumerate(todo.itertuples()):
        a = fetch_doc(int(r.cik), r.acc); b = fetch_doc(int(r.cik), r.prior_acc)
        ok.append(bool(a and len(a) > 200 and b and len(b) > 200))
        if i % 100 == 0:
            print(f"  doc {i} {time.time()-t0:.0f}s", flush=True)
    T["ok"] = False
    T.loc[todo.index, "ok"] = ok
    T.to_csv(fp, index=False)
    print("docs ok", int(T.ok.sum()), f"{time.time()-t0:.0f}s")
    return T


def jobs_h(limit=None):
    T = h_table(); T = T[T.ok.astype(bool)]
    jobs = []
    for r in T.itertuples():
        names = [r.cname, r.ticker]

        def mk(r=r, names=names):
            m = _r1().mask
            q = tail_questions(); q.update(DIFF_Q)
            return {"state": {"press_release": extract(m(fetch_doc(0, r.acc), names)),
                              "previous_quarter_release": extract(m(fetch_doc(0, r.prior_acc), names))},
                    "model": MODEL, "questions": q}
        jobs.append((r.acc, mk))
    return jobs[:limit]


def eval_h():
    T = h_table(); T = T[T.ok.astype(bool)].copy()
    F = feats_of("V2_1718")
    F.columns = [c.replace("V2_1718_", "V2_") for c in F.columns]
    T = T.merge(F, on="acc", how="inner").drop_duplicates("acc")
    T["net"] = T.gA20 - FEE
    T["top10"] = T.gA20 >= T.groupby("year").gA20.transform(lambda x: x.quantile(0.9))
    out = {"n": int(len(T)), "by_year": T.year.value_counts().to_dict()}
    for f in ("V2_d_outlook_improved", "V2_d_momentum_stronger"):
        r = incr(T, f); sp = split_i1(T, f)
        oos_sign = 1
        r["verdict"] = ("후보 유지" if (r["ols_b_pct_sd"] > 0) == (oos_sign > 0) and r["ols_t"] >= 1.65 else "후보 탈락(기각)")
        out[f] = {"incr": r, "split": sp}
    (OUT / "results_h1718.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return out


if __name__ == "__main__":
    st = sys.argv[1]; lim = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if st == "base":
        b = base(); print(len(b), int(b.v2set.sum()), b.T1S.notna().mean())
    elif st == "priors":
        priors()
    elif st in ("V0", "V1", "V2", "FS"):
        run(st, jobs_v(st, lim))
    elif st == "PW":
        run("PW", jobs_pw(lim))
    elif st == "KR":
        run("KR", jobs_kr(lim))
    elif st == "h1718":
        h_table()
    elif st == "H":
        run("V2_1718", jobs_h(lim))
    elif st == "evalh":
        print(json.dumps(eval_h(), indent=1, default=str))
    elif st == "evalkr":
        print(json.dumps(eval_kr(), indent=1, default=str))
    elif st == "eval":
        print(json.dumps(build_eval(), indent=1, default=str)[:20000])
    elif st == "show":
        v = sys.argv[3] if len(sys.argv) > 3 else "V0"
        j = jobs_v(v, lim)
        print(json.dumps(j[0][1](), indent=1)[:6000])
