#!/usr/bin/env python3
"""T2 — DART 공시 원문(본문 필드·Claude 판독)이 공시 '유형'만보다 다음 시가 진입 초과수익을 더 잘 맞히나 (2026-09-27).

연구 전용(read-only). 운영 파일·봇·state 는 건드리지 않는다. 산출물은 data/analysis/model_T2_dart_20260927/ 와
docs/reports/model_T2_dart_20260927.md 뿐. 실행 파이썬: C:/Users/Unknown/research_venv (lightgbm 4.6, anthropic 0.125).

=====================================================================================================
사전등록 (결과 채점 전에 고정 — 2026-09-27 21:5x KST, 결과 파일 생성 전 작성)
=====================================================================================================

## 1. 이벤트 집합
- 원천: data/analysis/dart_events_12m.jsonl + dart_corp_actions_12m.jsonl (2025-09-01~2026-07-31 구간),
  2026-08-01~2026-09-18 은 DART list.json(pblntf_ty I·B, 전 페이지)을 새로 받아 같은 키워드로 분류(8월 겹침으로
  기존 파일 커버리지 대조). 종류 7개: supply(단일판매·공급계약체결) / rights(유상증자결정, 유무상 제외) /
  bonus(무상·유무상증자결정) / major_holder(최대주주 변경 계열) / buyback(자기주식취득결정) / cancel(주식소각결정) /
  split(주식분할결정).
- **1차 표본 = 원공시만**(제목이 '['로 시작하지 않거나 '[첨부추가]'). 정정([기재정정]·[첨부정정] 등)은 제외.
- 같은 (종목, 신호일 D)에 여러 건이면 rcept_no 가 가장 이른 1건만 남기고 건수를 기록.
- 종목코드 없는 공시·가격 파일(data/price/kr/kr_{ticker}.csv) 없는 종목은 탈락(건수 보고).

## 2. 시점·결과
- rcept_dt 는 날짜만 있다 → **당일 공시는 당일 종가 이후에 알았다고 본다.** 신호일 D = rcept_dt 이하 마지막 거래일
  (주말 공시는 직전 금요일이 D, 진입은 다음 거래일). 진입 = D 다음 거래일 시가.
  (장중 공시는 D 종가에 이미 일부 반영 → 초기 반응을 놓치는 보수적 규약.)
- h=1: 진입일 종가, h=5: 진입일 포함 5번째 세션 종가. 종목 봉이 달력과 어긋나면(정지) 해당 h 결측.
- 벤치 = 규모매칭: D 에 적격(종가≥1,000, D 거래대금≥20억, 이력≥25봉, 다음 시가>0)인 전 종목을 dvol20(20봉 평균
  거래대금) 십분위로 나누고, 이벤트 종목과 같은 십분위의 등가중 평균 (D+1 시가→D+h 종가) 수익. 계약 출구 없음.
  이벤트 종목의 십분위 = 그날 적격 유니버스 dvol20 순위상의 위치(적격 미달이면 0 십분위로 클립).
- 결과 ex_h = r_h − bench_h − 0.25%(왕복 비용). 단위 %.
- **1차 모집단 = D 적격(tradable) 이벤트**(벤치와 같은 문턱, 0.25% 비용이 성립하는 곳). 전체(비적격 포함)는 2차.

- [사후 수정, 결과를 본 뒤 추가 — 2026-09-28] 첫 채점에서 h=5 ex 최대 +17,515%(052670), +2,903%(196490) 등
  미수정 분할·병합 가격 오류가 평균을 지배했다. 진입~청산 창(D+1 시가 포함)에 전일 종가 대비 ±30.5%를 넘는
  시가·종가(상하한 밖 = 데이터 오류)가 있으면 그 결과를 결측으로 둔다. 벤치 유니버스에도 같은 규칙. 모델·임계·검정은 불변.

## 3. 원문
- DART document.xml 전량(zip 안 모든 문서) → <style>/<script> 제거 → 태그 제거 → 공백 정규화, 40k자 캐시.
- Claude 입력: 헤더(회사명·대표·주소·전화·홈페이지) 제거, **회사명 마스킹**(corp_name 과 (주)/주식회사/㈜ 변형,
  종목코드 → 'A사'/'XXXXXX'). **날짜는 남긴다**(계약기간·기준일은 실질 정보이고, 1차 평가는 모델 학습 컷오프 이후
  공시라 기억 누수는 날짜가 아니라 사건 자체가 없다). 본문 앞 8,000자.

## 4. 모델 (전부 워크포워드: 월 단위 확장창, 학습 월 끝과 예측 월 사이 5세션 퍼지, 첫 예측월은 학습 3개월 이상 확보 후)
 (0) 기준선: 학습 구간 유형별 평균 ex_h (표본<10 유형은 전체 평균).
 (1) 본문 필드 + 유형 LightGBM 회귀(ex_h 목표). 피처(고정):
     kind(범주), sub(자회사·종속회사 공시), autonomous(자율공시),
     supply: log 계약금액, 매출대비%, log 최근매출, 관계사(1/0/결측), 계약기간(일), 시작까지 일수, 선급금 유/무,
             상대방 해외(영문 포함)·공공(정부/공사/청/시/군/국방/조달) 플래그, 공급지역 해외, 공시유보 여부,
     rights: 방식(주주배정/제3자배정/일반공모), 희석률 = 신주수/증자전 발행주식총수, 운영자금 비중, 채무상환 비중,
             시설자금 비중, 타법인취득 비중, (예정·확정)발행가/D종가−1, 할인·할증율(%),
     buyback: log 취득예정금액, 금액/(dvol20×20), 신탁 여부, 취득기간(일),
     cancel: log 소각예정금액, 소각주식/발행주식총수,
     bonus/split: 1주당 배정주식수 / 분할 비율,
     major_holder: 하위유형(담보제공/양수도/변경/신고서/해제),
     가격 맥락: log dvol20, 십분위, D 당일 수익률, D 이전 5세션 수익률.
     학습 행 = 중복 제거된 전 이벤트(적격·비적격, 적격 여부는 피처), 목표 ex_h 는 학습 구간 1·99% 분위로 클립.
     kind 는 정수 코드(0~6)로 넣는다. 가격 맥락 없는 변형 (1n) 은 진단용(검정 수 밖).
     LightGBM 기본값 고정: n_estimators=200, learning_rate=0.03, num_leaves=15, min_child_samples=30,
     subsample=0.8(bagging_freq=1), colsample_bytree=0.8, reg_lambda=1.0, seed=7. 튜닝 없음.
 (2) Claude 제로샷(claude-opus-5, thinking 기본값, effort=low): 마스킹 본문만 보고
     JSON {direction: POS|NEG|NEUTRAL, strength: 0..1, rationale}. score = +s(POS) / −s(NEG) / 0.
     학습 없음 → 월 구분 없이 채점.
 (3) Claude + 필드 결합 LightGBM: (1) 피처 + claude_score. 학습 행은 Claude 점수가 있는 행만(컷오프 전은
     오염 표본 — 기억한 사건이면 Claude 피처를 과신하게 되어 사후 성능을 오히려 깎는 방향이라 합격을 부풀리지 못한다).
 (3b) 학습 없는 결합(비오염판): 평가 창 안에서 (1)의 예측 순위와 claude_score 순위의 평균.

## 5. 평가 (1차 = rcept_dt ≥ 2026-07-01, 적격 이벤트, 모델 (1)은 12~6월 창도 별도 보고)
- 잔차 u = ex_h − (0)의 예측(학습 구간 유형 평균).
- 스프레드 A = u(상위) − u(나머지). 상위 = 평가 창 안 모델 점수 상위 20%(점수 분포만 사용, 결과 미사용).
  Claude(2)는 score 상위 20%.
- 스프레드 B = ex(상위 20%) − ex(하위 20%). Claude는 POS − NEG(방향 라벨).
- t 는 군집 강건(CR1) — h=1 은 진입일 군집, h=5 는 진입 주(ISO week) 군집(보유 겹침 흡수).
- 반기: 평가 창을 진입일 중앙값으로 둘로 나눠 각 부호.
- 검정 수: 모델 {1, 2, 3, 3b} × h {1, 5} × 스프레드 {A, B} = 16. **합격 = A 의 t ≥ 3 AND 두 반기 A 부호 양(+)
  AND B 부호 양(+).** 이 밑은 '후보'(t≥2 & 반기 유지) 또는 '기각'.
- 기준선 자체의 설명력: (0) 예측 상위 20% vs 나머지의 ex 차이도 보고(참고, 검정 수 밖).
- 검정력: 사후 창 ≈ 50거래일. 최소검출효과(t=3)를 결과표 옆에 적는다.

## 6. 예산
- Claude 호출 ≤ 3,000. 사후 원공시(적격 우선, 여유되면 비적격 포함) 전량 + 컷오프 전 종류층화 표본으로 채움.
  20건 파일럿의 실제 usage 로 비용 추정 후 본 실행.
=====================================================================================================
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "analysis" / "model_T2_dart_20260927"
DOCS = OUT / "docs"
PRICE = ROOT / "data" / "price" / "kr"
CUTOFF = "20260701"
COST = 0.25
KIND_OLD = {"공급계약체결": "supply", "유상증자결정(대조)": "rights", "최대주주변경": "major_holder",
            "자기주식취득결정": "buyback", "무상증자결정": "bonus", "주식소각결정": "cancel", "주식분할결정": "split"}


# ───────────────────────────────── 공통 ─────────────────────────────────
def jl(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def wjl(path, rows):
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")


def classify(nm: str) -> str | None:
    b = re.sub(r"^\[[^\]]*\]", "", nm or "")
    if "공급계약체결" in b and "해지" not in b:
        return "supply"
    if "무상증자결정" in b:          # 유무상 포함
        return "bonus"
    if "유상증자결정" in b:
        return "rights"
    if "최대주주" in b and "변경" in b:
        return "major_holder"
    if "자기주식취득결정" in b:
        return "buyback"
    if "주식소각결정" in b:
        return "cancel"
    if "주식분할결정" in b:
        return "split"
    return None


def is_orig(nm: str) -> bool:
    nm = nm or ""
    return (not nm.startswith("[")) or nm.startswith("[첨부추가]")


# ───────────────────────────────── 1. 이벤트 ─────────────────────────────────
def stage_events():
    rows = []
    for f in ("dart_events_12m.jsonl", "dart_corp_actions_12m.jsonl"):
        for r in jl(ROOT / "data" / "analysis" / f):
            if r["date"] < "20260801":
                rows.append({"rcept_no": r["rcept_no"], "rcept_dt": r["date"], "stock": r["stock"], "name": r["name"],
                             "report": r["report"], "kind": KIND_OLD[r["kind"]], "src": "old12m"})
    lst = sorted(OUT.glob("dart_list_*.jsonl"))
    new = jl(lst[-1])
    seen = set()
    newk = []
    for r in new:
        k = classify(r["report_nm"])
        if not k or not r["stock_code"] or r["rcept_no"] in seen:
            continue
        seen.add(r["rcept_no"])
        newk.append({"rcept_no": r["rcept_no"], "rcept_dt": r["rcept_dt"], "stock": r["stock_code"],
                     "name": r["corp_name"], "report": r["report_nm"], "kind": k, "src": "list_new"})
    # 8월 커버리지 대조
    old_aug = {}
    for f in ("dart_events_12m.jsonl", "dart_corp_actions_12m.jsonl"):
        for r in jl(ROOT / "data" / "analysis" / f):
            if "20260801" <= r["date"] <= "20260831":
                old_aug[r["rcept_no"]] = KIND_OLD[r["kind"]]
    new_aug = {r["rcept_no"]: r["kind"] for r in newk if "20260801" <= r["rcept_dt"] <= "20260831"}
    cov = {"old_aug": len(old_aug), "new_aug": len(new_aug), "both": len(set(old_aug) & set(new_aug)),
           "old_only": len(set(old_aug) - set(new_aug)), "new_only": len(set(new_aug) - set(old_aug)),
           "kind_mismatch": sum(1 for k in set(old_aug) & set(new_aug) if old_aug[k] != new_aug[k]),
           "old_only_by_kind": Counter(old_aug[k] for k in set(old_aug) - set(new_aug)),
           "new_only_by_kind": Counter(new_aug[k] for k in set(new_aug) - set(old_aug))}
    rows += [r for r in newk if r["rcept_dt"] >= "20260801"]
    for r in rows:
        r["orig"] = is_orig(r["report"])
        r["sub"] = int(("자회사" in r["report"]) or ("종속회사" in r["report"]))
        r["autonomous"] = int("자율공시" in r["report"])
    wjl(OUT / "events_all.jsonl", rows)
    origs = [r for r in rows if r["orig"]]
    wjl(OUT / "events_orig.jsonl", origs)
    summ = {"all": len(rows), "orig": len(origs), "coverage_aug": cov,
            "orig_by_kind": Counter(r["kind"] for r in origs),
            "orig_post_by_kind": Counter(r["kind"] for r in origs if r["rcept_dt"] >= CUTOFF)}
    json.dump(summ, open(OUT / "events_summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps(summ, ensure_ascii=False, indent=1, default=str))


# ───────────────────────────────── 2. 결과·벤치 ─────────────────────────────────
def load_px(p: Path) -> pd.DataFrame | None:
    try:
        df = pd.read_csv(p, encoding="utf-8-sig", usecols=["date", "open", "high", "low", "close", "volume"])
    except Exception:
        return None
    df = df.dropna().drop_duplicates("date").sort_values("date").reset_index(drop=True)
    return df


def stage_outcomes():
    cal = list(load_px(PRICE / "kr_005930.csv")["date"])
    cpos = {d: i for i, d in enumerate(cal)}
    uni = []          # 적격 유니버스 행
    tick = {}         # ticker -> 이벤트 조회용 최소 배열
    for p in sorted(PRICE.glob("kr_*.csv")):
        t = p.stem[3:]
        df = load_px(p)
        if df is None or len(df) < 30:
            continue
        d = df["date"].tolist()
        o = df["open"].to_numpy(float); c = df["close"].to_numpy(float); v = df["volume"].to_numpy(float)
        dv = c * v
        dv20 = pd.Series(dv).rolling(20).mean().to_numpy() / 1e8
        n = len(df)
        # 달력 정합: i+k 가 달력상 D+k 인지
        cp = np.array([cpos.get(x, -10 ** 6) for x in d])
        # 가격 오류(미수정 분할·병합) 차단: 상하한 30%를 넘는 종가·시가 변화가 있는 봉은 오류로 본다(사후 수정 — 아래 주석)
        prev_c = np.r_[np.nan, c[:-1]]
        bad = (np.abs(c / prev_c - 1) > 0.305) | (np.abs(o / prev_c - 1) > 0.305)
        badcum = np.cumsum(np.nan_to_num(bad.astype(float)))
        r = {}
        for h in (1, 5):
            rr = np.full(n, np.nan)
            ok = np.zeros(n, bool)
            if n > h:
                ok[: n - h] = (cp[h:] - cp[: n - h] == h) & (cp[1:n - h + 1] - cp[: n - h] == 1)
            idx = np.where(ok)[0]
            rr[idx] = (c[idx + h] / o[idx + 1] - 1) * 100
            clean = badcum[np.minimum(idx + h, n - 1)] - badcum[idx] == 0      # D+1..D+h 구간에 오류봉 없음
            rr[idx[~clean]] = np.nan
            r[h] = rr
        ret0 = np.full(n, np.nan); ret0[1:] = (c[1:] / c[:-1] - 1) * 100
        ret5 = np.full(n, np.nan); ret5[5:] = (c[5:] / c[:-5] - 1) * 100
        nxt_o = np.full(n, np.nan); nxt_o[:-1] = o[1:]
        elig = (np.arange(n) >= 25) & (c >= 1000) & (dv / 1e8 >= 20) & (nxt_o > 0)
        tick[t] = {"d": d, "dv20": dv20, "r1": r[1], "r5": r[5], "c": c, "ret0": ret0, "ret5": ret5, "elig": elig}
        for i in np.where(elig)[0]:
            uni.append((d[i], dv20[i], r[1][i], r[5][i]))
    U = pd.DataFrame(uni, columns=["date", "dv20", "r1", "r5"])
    del uni
    U = U.dropna(subset=["dv20"])
    U["n"] = U.groupby("date")["dv20"].transform("size")
    U["rk"] = U.groupby("date")["dv20"].rank(method="first") - 1
    U["dec"] = np.minimum(9, (U["rk"] * 10 // U["n"]).astype(int))
    bench = U.groupby(["date", "dec"])[["r1", "r5"]].mean()
    bk = {k: (a, b) for k, a, b in zip(bench.index, bench["r1"], bench["r5"])}
    sorted_dv = {dt: np.sort(g.to_numpy()) for dt, g in U.groupby("date")["dv20"]}
    uni_n = U.groupby("date").size().to_dict()
    del U

    ev = jl(OUT / "events_orig.jsonl")
    drop = Counter()
    out = []
    for e in ev:
        T = tick.get(e["stock"])
        if T is None:
            drop["no_price_file"] += 1
            continue
        rd = datetime.strptime(e["rcept_dt"], "%Y%m%d").strftime("%Y-%m-%d")
        # 신호일 D = rcept_dt 이하 마지막 달력 거래일, 종목도 그날 봉이 있어야 함
        j = np.searchsorted(np.array(cal), rd, side="right") - 1
        if j < 0:
            drop["before_cal"] += 1
            continue
        D = cal[j]
        try:
            i = T["d"].index(D)
        except ValueError:
            drop["no_bar_on_D"] += 1
            continue
        sd = sorted_dv.get(D)
        dv20 = T["dv20"][i]
        if sd is None or not np.isfinite(dv20):
            drop["no_universe_or_dv20"] += 1
            continue
        dec = int(min(9, np.searchsorted(sd, dv20, side="left") * 10 // len(sd)))
        row = {**e, "D": D, "entry": cal[j + 1] if j + 1 < len(cal) else None, "dv20": float(dv20), "dec": dec,
               "elig": bool(T["elig"][i]), "close_D": float(T["c"][i]), "ret0": float(T["ret0"][i]),
               "ret5": float(T["ret5"][i]), "uni_n": uni_n.get(D)}
        for h in (1, 5):
            rr = T[f"r{h}"][i]
            b = bk.get((D, dec), (np.nan, np.nan))[0 if h == 1 else 1]
            row[f"r{h}"] = float(rr) if np.isfinite(rr) else None
            row[f"b{h}"] = float(b) if np.isfinite(b) else None
            row[f"ex{h}"] = float(rr - b - COST) if (np.isfinite(rr) and np.isfinite(b)) else None
        out.append(row)
    # 같은 (종목, D) 중 가장 이른 rcept_no
    out.sort(key=lambda r: r["rcept_no"])
    g = defaultdict(list)
    for r in out:
        g[(r["stock"], r["D"])].append(r)
    ded = []
    for k, v in g.items():
        v[0]["n_same"] = len(v)
        v[0]["kinds_same"] = sorted({x["kind"] for x in v})
        ded.append(v[0])
    ded.sort(key=lambda r: (r["D"], r["rcept_no"]))
    wjl(OUT / "events_outcomes.jsonl", ded)
    summ = {"orig_in": len(ev), "with_outcome_rows": len(out), "dedup": len(ded), "drops": drop,
            "elig": sum(r["elig"] for r in ded), "post_elig": sum(r["elig"] and r["rcept_dt"] >= CUTOFF for r in ded),
            "post_all": sum(r["rcept_dt"] >= CUTOFF for r in ded),
            "ex1_nonnull": sum(r["ex1"] is not None for r in ded), "ex5_nonnull": sum(r["ex5"] is not None for r in ded),
            "last_cal": cal[-1]}
    json.dump(summ, open(OUT / "outcomes_summary.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
    print(json.dumps(summ, ensure_ascii=False, indent=1, default=str))


# ───────────────────────────────── 3. 본문 필드 ─────────────────────────────────
sys.path.insert(0, str(ROOT))
from runtime.kr_event_lane import parse_supply_contract  # noqa: E402  (순수 함수 import, 부작용 없음)

_N = r"(-|[\d,]+(?:\.\d+)?)"


def num_after(text, label, span=60):
    m = re.search(label + r"[^\d\-]{0,%d}?" % span + _N, text)
    if not m:
        return None
    g = m.group(1)
    if g == "-":
        return 0.0
    try:
        return float(g.replace(",", ""))
    except ValueError:
        return None


def ymd_after(text, label):
    m = re.search(label + r"\s*(\d{4})\s*[-년.]\s*(\d{1,2})\s*[-월.]\s*(\d{1,2})", text)
    if not m:
        return None
    try:
        return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def lg(x):
    return math.log10(x) if x and x > 0 else None


def parse_fields(e, text):
    k = e["kind"]
    f = {}
    D = datetime.strptime(e["D"], "%Y-%m-%d")
    if k == "supply":
        s = parse_supply_contract(text)
        f["sup_log_amt"] = lg(s.get("amount_krw"))
        f["sup_ratio"] = s.get("ratio_pct")
        f["sup_log_sales"] = lg(s.get("recent_sales_krw"))
        rp = s.get("related_party")
        f["sup_related"] = None if rp is None else float(rp)
        if s.get("period"):
            try:
                a = datetime.strptime(s["period"][0], "%Y-%m-%d"); b = datetime.strptime(s["period"][1], "%Y-%m-%d")
                f["sup_days"] = (b - a).days
                f["sup_start_in"] = (a - D).days
            except ValueError:
                pass
        ap = s.get("advance_payment")
        f["sup_adv"] = None if ap is None else float(ap == "유")
        cp = s.get("counterparty") or ""
        f["sup_cp_en"] = float(bool(re.search(r"[A-Za-z]{3,}", cp))) if cp else None
        f["sup_cp_pub"] = float(bool(re.search(r"정부|공사|청$|청\b|시청|군청|국방|조달|공단|지방자치|특별시|광역시|도청", cp))) if cp else None
        m = re.search(r"판매\S*\s*공급지역\s*(\S+)", text)
        if m:
            f["sup_region_foreign"] = float(not re.match(r"(대한민국|국내|한국|-)", m.group(1)))
        m = re.search(r"유보사유\s*(\S+)", text)
        f["sup_masked"] = float(bool(m) and m.group(1) not in ("-", "해당없음", "해당사항없음"))
    elif k == "rights":
        new = num_after(text, r"신주의\s*종류와\s*수\s*보통주식\s*\(주\)", 10)
        pre = num_after(text, r"증자전\s*발행주식총수\s*(?:\(주\))?\s*보통주식\s*\(주\)", 10)
        f["ri_dilution"] = (new / pre) if (new and pre) else None
        funds = {n: num_after(text, lab, 15) for n, lab in [("fac", r"시설자금\s*\(원\)"), ("ops", r"운영자금\s*\(원\)"),
                                                          ("debt", r"채무상환자금\s*\(원\)"), ("acq", r"타법인\s*증권\s*취득자금\s*\(원\)")]}
        tot = sum(v for v in funds.values() if v) + (num_after(text, r"영업양수자금\s*\(원\)", 15) or 0) + (num_after(text, r"기타자금\s*\(원\)", 15) or 0)
        for n, v in funds.items():
            f[f"ri_{n}_sh"] = (v / tot) if (tot and v is not None) else None
        f["ri_log_total"] = lg(tot)
        m = re.search(r"증자방식\s*(\S+)", text)
        meth = m.group(1) if m else ""
        f["ri_method"] = (0.0 if "주주배정" in meth else 1.0 if "제3자" in meth else 2.0 if "공모" in meth else None)
        px = num_after(text, r"(?:확정발행가|예정발행가)\s*보통주식\s*\(원\)", 5)
        if not px:
            px = num_after(text, r"예정발행가\s*보통주식\s*\(원\)", 5)
        if not px:
            px = num_after(text, r"신주\s*발행가액\s*보통주식\s*\(원\)", 5)
        m = re.search(r"할인율\s*또는\s*할증율\s*\(%\)\s*(-?[\d.]+)", text)
        f["ri_disc"] = float(m.group(1)) if m else None
        f["ri_px_vs_close"] = (px / e["close_D"] - 1) if (px and e.get("close_D")) else None
    elif k == "buyback":
        amt = num_after(text, r"취득예정금액\s*\(원\)\s*보통주식", 5)
        f["bb_log_amt"] = lg(amt)
        f["bb_amt_vs_dvol"] = (amt / (e["dv20"] * 1e8 * 20)) if (amt and e.get("dv20")) else None
        a = ymd_after(text, r"취득예상기간\s*시작일"); b = ymd_after(text, r"종료일")
        f["bb_days"] = (b - a).days if (a and b and b > a) else None
        m = re.search(r"취득방법\s*(.{0,30})", text)
        f["bb_trust"] = float("신탁" in (m.group(1) if m else ""))
        f["bb_prior_pct"] = num_after(text, r"배당가능이익\s*범위\s*내\s*취득\s*\(주\)\s*보통주식\s*[\d,\-]+\s*비율\s*\(%\)", 3)
    elif k == "cancel":
        sh = num_after(text, r"소각할\s*주식의\s*종류와\s*수\s*보통주식\s*\(주\)", 5)
        tot = num_after(text, r"발행주식\s*총수\s*보통주식\s*\(주\)", 5)
        f["ca_ratio"] = (sh / tot) if (sh and tot) else None
        f["ca_log_amt"] = lg(num_after(text, r"소각예정금액\s*\(원\)", 5))
        f["ca_amt_vs_dvol"] = None
        amt = num_after(text, r"소각예정금액\s*\(원\)", 5)
        if amt and e.get("dv20"):
            f["ca_amt_vs_dvol"] = amt / (e["dv20"] * 1e8 * 20)
    elif k == "bonus":
        f["bo_ratio"] = num_after(text, r"1주당\s*신주\s*배정\s*주식\s*수\s*(?:\(주\))?\s*보통주식\s*(?:\(주\))?", 5)
        f["bo_with_rights"] = float("유무상" in e["report"])
    elif k == "split":
        a = num_after(text, r"분할\s*전", 20); b = num_after(text, r"분할\s*후", 20)
        f["sp_ratio"] = (a / b) if (a and b) else None
    elif k == "major_holder":
        r = e["report"]
        f["mh_sub"] = (0.0 if "담보" in r and "해제" not in r else 1.0 if "양수도" in r and "해제" not in r
                       else 2.0 if "해제" in r or "취소" in r else 3.0 if "신고서" in r else 4.0)
    return f


KINDS = ["supply", "rights", "buyback", "cancel", "bonus", "split", "major_holder"]
FIELD_COLS = ["sup_log_amt", "sup_ratio", "sup_log_sales", "sup_related", "sup_days", "sup_start_in", "sup_adv",
              "sup_cp_en", "sup_cp_pub", "sup_region_foreign", "sup_masked",
              "ri_dilution", "ri_fac_sh", "ri_ops_sh", "ri_debt_sh", "ri_acq_sh", "ri_log_total", "ri_method", "ri_px_vs_close", "ri_disc",
              "bb_log_amt", "bb_amt_vs_dvol", "bb_days", "bb_trust", "bb_prior_pct",
              "ca_ratio", "ca_log_amt", "ca_amt_vs_dvol", "bo_ratio", "bo_with_rights", "sp_ratio", "mh_sub"]
CTX_COLS = ["log_dv20", "dec", "ret0", "ret5", "elig_i", "sub", "autonomous"]


def stage_fields():
    ev = jl(OUT / "events_outcomes.jsonl")
    got = Counter(); tot = Counter()
    for e in ev:
        p = DOCS / f"{e['rcept_no']}.txt"
        tot[e["kind"]] += 1
        e["has_doc"] = p.exists()
        f = {}
        if e["has_doc"]:
            got[e["kind"]] += 1
            f = parse_fields(e, p.read_text(encoding="utf-8"))
        e.update({c: f.get(c) for c in FIELD_COLS})
        e["log_dv20"] = lg(e["dv20"] * 1e8)
        e["elig_i"] = float(e["elig"])
    wjl(OUT / "events_features.jsonl", ev)
    df = pd.DataFrame(ev)
    cov = {k: {"n": tot[k], "doc": got[k]} for k in KINDS}
    for k, cols in [("supply", [c for c in FIELD_COLS if c.startswith("sup_")]), ("rights", [c for c in FIELD_COLS if c.startswith("ri_")]),
                    ("buyback", [c for c in FIELD_COLS if c.startswith("bb_")]), ("cancel", [c for c in FIELD_COLS if c.startswith("ca_")]),
                    ("bonus", ["bo_ratio"]), ("split", ["sp_ratio"]), ("major_holder", ["mh_sub"])]:
        sub = df[(df.kind == k) & df.has_doc]
        cov[k]["fill"] = {c: round(float(sub[c].notna().mean()), 3) if len(sub) else None for c in cols}
    json.dump(cov, open(OUT / "fields_coverage.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(cov, ensure_ascii=False, indent=1))


# ───────────────────────────────── 4. Claude ─────────────────────────────────
MODEL = "claude-opus-5"
PROMPT = (
    "아래는 한국 상장사 1곳의 DART 공시 원문(앞부분)이다. 회사명은 'A사'로 가렸다.\n"
    "이 공시가 나온 뒤 **다음 거래일 시가에 매수해 1~5거래일 보유**할 때, 비슷한 규모 종목들 평균보다 "
    "주가가 나을지(POS) 못할지(NEG) 판단하라. 공시 유형의 일반론보다 본문의 구체 내용(규모 대비 크기, 조건, 상대방, "
    "자금 용도, 희석, 기간 등)을 근거로 삼아라. 판단 근거가 약하면 NEUTRAL.\n"
    "JSON 한 줄만 출력: {\"direction\": \"POS\"|\"NEG\"|\"NEUTRAL\", \"strength\": 0~1 숫자, \"rationale\": \"한 문장\"}\n\n"
    "공시 원문:\n"
)


def mask_text(text, name, stock):
    t = text
    t = re.sub(r"회\s*사\s*명\s*:.*?작\s*성\s*책\s*임\s*자\s*:.*?\(\s*전\s*화\s*\)\s*[\d\-\s)]{6,}", " [헤더 생략] ", t, count=1, flags=re.S)
    t = re.sub(r"https?://\S+|www\.\S+", " ", t)
    t = re.sub(r"\b0\d{1,2}-\d{3,4}-\d{4}\b", " ", t)
    base = re.sub(r"\(주\)|㈜|주식회사|\s", "", name or "")
    for n in sorted({name or "", base, base.replace("(주)", "")}, key=len, reverse=True):
        if len(n) >= 2:
            t = t.replace(n, "A사")
    if stock:
        t = t.replace(stock, "XXXXXX")
    return re.sub(r"\s+", " ", t).strip()


def claude_targets(cap=3000, seed=7):
    ev = [e for e in jl(OUT / "events_features.jsonl") if e.get("has_doc")]
    post = [e for e in ev if e["rcept_dt"] >= CUTOFF]
    pre = [e for e in ev if e["rcept_dt"] < CUTOFF]
    rng = np.random.default_rng(seed)
    k = max(0, cap - len(post))
    pre_s = []
    if k:   # 종류 비례 층화
        frac = min(1.0, k / max(1, len(pre)))
        for kd in KINDS:
            grp = [e for e in pre if e["kind"] == kd]
            m = int(round(len(grp) * frac))
            pre_s += [grp[i] for i in sorted(rng.choice(len(grp), size=min(m, len(grp)), replace=False))] if m else []
    return post + pre_s


def _client():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env.live")
    import anthropic
    return anthropic.Anthropic(max_retries=4, timeout=120.0)


def call_one(client, e):
    text = mask_text((DOCS / f"{e['rcept_no']}.txt").read_text(encoding="utf-8"), e["name"], e["stock"])[:8000]
    t0 = time.time()
    resp = client.messages.create(model=MODEL, max_tokens=2000, output_config={"effort": "low"},
                                  messages=[{"role": "user", "content": PROMPT + text}])
    raw = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", raw, flags=re.S)
    d = {}
    try:
        d = json.loads(m.group(0)) if m else {}
    except Exception:
        d = {}
    u = resp.usage
    return {"rcept_no": e["rcept_no"], "direction": d.get("direction"), "strength": d.get("strength"),
            "rationale": d.get("rationale"), "stop": resp.stop_reason, "in_tok": u.input_tokens, "out_tok": u.output_tokens,
            "sec": round(time.time() - t0, 1), "raw": raw[:400] if not d else None}


def stage_claude(n_limit=None):
    path = OUT / "claude_scores.jsonl"
    done = {r["rcept_no"] for r in jl(path)} if path.exists() else set()
    tg = [e for e in claude_targets() if e["rcept_no"] not in done]
    if n_limit:
        tg = tg[: int(n_limit)]
    print("targets todo", len(tg), "done", len(done), flush=True)
    client = _client()
    n = 0
    with ThreadPoolExecutor(max_workers=6) as ex, open(path, "a", encoding="utf-8") as fh:
        futs = {ex.submit(call_one, client, e): e for e in tg}
        for fu in as_completed(futs):
            try:
                r = fu.result()
            except Exception as exc:
                print("err", futs[fu]["rcept_no"], type(exc).__name__, str(exc)[:150], flush=True)
                continue
            fh.write(json.dumps(r, ensure_ascii=False) + "\n"); fh.flush()
            n += 1
            if n % 100 == 0:
                print("done", n, flush=True)
    rows = jl(path)
    it = sum(r["in_tok"] for r in rows); ot = sum(r["out_tok"] for r in rows)
    print(f"calls {len(rows)} in {it} out {ot} cost ${it * 5e-6 + ot * 25e-6:.2f} "
          f"per-call ${(it * 5e-6 + ot * 25e-6) / max(1, len(rows)):.4f}", Counter(r['direction'] for r in rows))


# ───────────────────────────────── 5. 모델·평가 ─────────────────────────────────
def cr_t(y, g, x=None):
    """군집 강건(CR1) OLS. x=None 이면 평균, 아니면 [1, x] 의 x 계수. (coef, t, n, G)."""
    y = np.asarray(y, float); g = np.asarray(g)
    X = np.ones((len(y), 1)) if x is None else np.column_stack([np.ones(len(y)), np.asarray(x, float)])
    k = X.shape[1]
    if len(y) < k + 2:
        return None, None, len(y), 0
    XtX_inv = np.linalg.pinv(X.T @ X)
    b = XtX_inv @ X.T @ y
    e = y - X @ b
    meat = np.zeros((k, k))
    G = 0
    for gg in pd.unique(g):
        idx = g == gg
        s = X[idx].T @ e[idx]
        meat += np.outer(s, s); G += 1
    if G < 3:
        return float(b[-1]), None, len(y), G
    V = XtX_inv @ meat @ XtX_inv * (G / (G - 1)) * ((len(y) - 1) / (len(y) - k))
    se = math.sqrt(V[-1, -1]) if V[-1, -1] > 0 else float("nan")
    return float(b[-1]), float(b[-1] / se) if se and se == se else None, len(y), G


def month_of(d):
    return d[:7]


def lgbm_params():
    return dict(n_estimators=200, learning_rate=0.03, num_leaves=15, min_child_samples=30, subsample=0.8,
                subsample_freq=1, colsample_bytree=0.8, reg_lambda=1.0, random_state=7, verbose=-1, n_jobs=2)


def walk_forward(df, h, feats, cal, min_train_months=3):
    """월 단위 확장창. 학습 = 예측월 첫 거래일보다 (h+1)세션 이전에 결과가 끝나는 행. 반환: pred(모델), base(유형평균)."""
    import lightgbm as lgb
    cpos = {d: i for i, d in enumerate(cal)}
    y = f"ex{h}"
    months = sorted(df["D"].map(month_of).unique())
    pred = pd.Series(np.nan, index=df.index); base = pd.Series(np.nan, index=df.index)
    for mi, m in enumerate(months):
        if mi < min_train_months:
            continue
        test = df[df["D"].map(month_of) == m]
        first = cpos[min(test["D"])]
        tr = df[(df["D"].map(cpos) + h + 1 <= first - 1) & df[y].notna()]   # 5세션 퍼지 포함(h+1+… ≥ 경계)
        tr = tr[tr["D"].map(cpos) <= first - 5 - h]
        if len(tr) < 100:
            continue
        # (0) 유형 평균
        km = tr.groupby("kind")[y].agg(["mean", "size"])
        gm = tr[y].mean()
        base.loc[test.index] = test["kind"].map(lambda k: km.loc[k, "mean"] if (k in km.index and km.loc[k, "size"] >= 10) else gm)
        if feats is None:
            continue
        lo, hi = tr[y].quantile(0.01), tr[y].quantile(0.99)
        Xtr = tr[feats].astype(float); Xte = test[feats].astype(float)
        mdl = lgb.LGBMRegressor(**lgbm_params())
        mdl.fit(Xtr, tr[y].clip(lo, hi))
        pred.loc[test.index] = mdl.predict(Xte)
    return pred, base


def spreads(ev, score, h, pos=None, neg=None):
    """ev: 평가 DataFrame(ex{h}, base{h}, D, entry). score: Series. 반환 dict."""
    y = f"ex{h}"
    d = ev[ev[y].notna() & score.notna() & ev[f"base{h}"].notna()].copy()
    d["s"] = score[d.index]
    d["u"] = d[y] - d[f"base{h}"]
    grp = d["entry"] if h == 1 else pd.to_datetime(d["entry"]).dt.strftime("%G-%V")
    q80 = d["s"].quantile(0.8); q20 = d["s"].quantile(0.2)
    top = (d["s"] >= q80).astype(float)
    res = {"n": len(d), "days": int(d["entry"].nunique())}
    res["A"], res["A_t"], _, res["G"] = cr_t(d["u"], grp, top)
    res["top_n"] = int(top.sum())
    res["top_ex"], res["top_ex_t"], _, _ = cr_t(d.loc[top == 1, y], grp[top == 1])
    if pos is not None:
        mask = pos[d.index] | neg[d.index]
        dd = d[mask]; gg = grp[mask]
        res["B"], res["B_t"], _, _ = cr_t(dd[y], gg, pos[dd.index].astype(float))
        res["B_npos"], res["B_nneg"] = int(pos[d.index].sum()), int(neg[d.index].sum())
    else:
        mask = (d["s"] >= q80) | (d["s"] <= q20)
        dd = d[mask]; gg = grp[mask]
        res["B"], res["B_t"], _, _ = cr_t(dd[y], gg, (dd["s"] >= q80).astype(float))
    med = sorted(d["entry"].unique())[len(d["entry"].unique()) // 2] if len(d) else None
    for half, sel in (("H1", d["entry"] < med), ("H2", d["entry"] >= med)):
        a, t, n, _ = cr_t(d.loc[sel, "u"], grp[sel], top[sel]) if sel.sum() > 5 else (None, None, int(sel.sum()), 0)
        res[f"A_{half}"] = a; res[f"A_{half}_t"] = t
    return res


def stage_models():
    ev = pd.DataFrame(jl(OUT / "events_features.jsonl"))
    cal = list(load_px(PRICE / "kr_005930.csv")["date"])
    for k in KINDS:
        ev[f"k_{k}"] = (ev["kind"] == k).astype(float)
    ev["kind_c"] = ev["kind"].map({k: i for i, k in enumerate(KINDS)}).astype(float)
    cs = {r["rcept_no"]: r for r in jl(OUT / "claude_scores.jsonl")} if (OUT / "claude_scores.jsonl").exists() else {}

    def csc(rn):
        r = cs.get(rn)
        if not r or r.get("direction") not in ("POS", "NEG", "NEUTRAL"):
            return np.nan
        try:
            s = float(r.get("strength") or 0)
        except (TypeError, ValueError):
            s = 0.0
        return s if r["direction"] == "POS" else -s if r["direction"] == "NEG" else 0.0
    ev["claude"] = ev["rcept_no"].map(csc)
    ev["claude_dir"] = ev["rcept_no"].map(lambda rn: (cs.get(rn) or {}).get("direction"))
    F1 = ["kind_c"] + FIELD_COLS + CTX_COLS
    F1_noctx = ["kind_c"] + FIELD_COLS + ["sub", "autonomous"]
    out = {"n_events": len(ev), "claude_scored": int(ev["claude"].notna().sum())}
    results = []
    for h in (1, 5):
        p1, b = walk_forward(ev, h, F1, cal)
        p1n, _ = walk_forward(ev, h, F1_noctx, cal)
        ev[f"base{h}"] = b; ev[f"p1_{h}"] = p1; ev[f"p1n_{h}"] = p1n
        evc = ev[ev["claude"].notna()]
        p3, _ = walk_forward(evc, h, F1 + ["claude"], cal)
        ev[f"p3_{h}"] = p3.reindex(ev.index)
    ev.to_json(OUT / "events_scored.jsonl", orient="records", lines=True, force_ascii=False)
    windows = {"post": ev["rcept_dt"] >= CUTOFF, "pre_wf": (ev["rcept_dt"] < CUTOFF)}
    pops = {"elig": ev["elig"], "all": pd.Series(True, index=ev.index)}
    for wname, wsel in windows.items():
        for pname, psel in pops.items():
            for h in (1, 5):
                sub = ev[wsel & psel & ev[f"base{h}"].notna()].copy()
                if len(sub) < 30:
                    continue
                # 3b: 평가 창 안 순위 평균
                both = sub[f"p1_{h}"].notna() & sub["claude"].notna()
                s3b = pd.Series(np.nan, index=sub.index)
                s3b[both] = (sub.loc[both, f"p1_{h}"].rank(pct=True) + sub.loc[both, "claude"].rank(pct=True)) / 2
                pos = sub["claude_dir"] == "POS"; neg = sub["claude_dir"] == "NEG"
                cand = {"0_base": (sub[f"base{h}"], None, None), "1_fields": (sub[f"p1_{h}"], None, None),
                        "1n_fields_noctx": (sub[f"p1n_{h}"], None, None),
                        "2_claude": (sub["claude"], pos, neg), "3_lgbm+claude": (sub[f"p3_{h}"], None, None),
                        "3b_rankavg": (s3b, None, None)}
                for mname, (sc, ps, ng) in cand.items():
                    if sc.notna().sum() < 30:
                        continue
                    r = spreads(sub, sc, h, ps, ng)
                    r.update({"window": wname, "pop": pname, "h": h, "model": mname})
                    results.append(r)
                # 유형별 평균(참고)
    pd.DataFrame(results).to_csv(OUT / "results.csv", index=False)
    json.dump(out, open(OUT / "models_summary.json", "w"), indent=1)
    pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
    R = pd.DataFrame(results)
    cols = ["window", "pop", "h", "model", "n", "days", "G", "top_n", "A", "A_t", "A_H1", "A_H2", "B", "B_t", "top_ex", "top_ex_t"]
    print(R[[c for c in cols if c in R.columns]].round(3).to_string())


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    cmd = sys.argv[1]
    if cmd == "events":
        stage_events()
    elif cmd == "outcomes":
        stage_outcomes()
    elif cmd == "fields":
        stage_fields()
    elif cmd == "claude":
        stage_claude(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "models":
        stage_models()
