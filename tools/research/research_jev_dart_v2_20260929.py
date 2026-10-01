#!/usr/bin/env python3
"""Jev 재시험 — 문서 권장 방식(영어·구조화 JSON·원자 질문 + 코드 조합)으로 한국 공시 (2026-09-29).

1차(`jev_dart_20260929.md`)는 한국어 원문 + "주가가 오를까" 한 질문 → 기각. Jev 문서: 주력 언어 영어,
CJK 정확도 낮음, 상태는 이름 붙은 JSON, 넓은 판단은 좁은 질문으로 쪼개고 조합은 코드가 한다.

## 사전 등록(실행 전 고정)
입력 변형 2종(같은 질문):
  EN    : state = 영어 JSON(공시 유형 영문 라벨 + T2가 본문에서 뽑은 수치 + 주가 맥락). 한국어 없음.
  EN_KR : EN + "original_excerpt_korean"(회사명 가린 원문 앞 600자).
원자 질문 7개(영어): materiality(Score 5) · surprise(Score 4) · certainty(Score 4) · financing_negative(Noul) ·
  shareholder_return(Noul) · recurring(Noul) · direction(Choice positive/neutral/negative) + 각 confidence.
조합: T2 walk_forward(월 확장창, LightGBM, 결과 확정 행만 학습) 로 Jev 피처 → ex1 / ex5 예측.
  비교 모델: CTX(유형+주가맥락만) · FIELDS(T2 필드모델 피처) · JEV_EN / JEV_ENKR(CTX + Jev 피처) · 단일 direction.
평가: 2026-07-01 이후 적격(T2와 동일), spreads A(유형기준선 잔차 상위20% − 나머지), 날짜 클러스터 t.
판정: A t ≥ 3 이고 전·후반 양수 → 후보(쉐도우). 모델 수 × 변형 × h 를 감안해 t 3 유지.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
import research_model_T2_dart_20260927 as t2  # noqa: E402

OUT = ROOT / "data" / "analysis" / "jev_dart_v2_20260929"
REPORT = ROOT / "docs" / "reports" / "jev_dart_v2_20260929.md"
URL = "https://api.typesafe.ai/v1/systemone"
KIND_EN = {"supply": "single-sale or supply contract award", "rights": "rights offering / new share issuance",
           "buyback": "treasury share buyback decision", "cancel": "treasury share cancellation",
           "bonus": "bonus share issue (stock dividend)", "split": "stock split or reverse split",
           "major_holder": "change of largest shareholder"}
FIELDS_EN = {"sup_ratio": "contract_amount_pct_of_annual_revenue", "sup_days": "contract_duration_days",
             "sup_related": "counterparty_is_related_party", "sup_region_foreign": "counterparty_is_foreign",
             "sup_cp_pub": "counterparty_is_public_or_government", "ri_dilution": "new_shares_pct_of_outstanding",
             "ri_disc": "issue_price_discount_pct", "ri_fac_sh": "use_of_funds_facilities_share",
             "ri_ops_sh": "use_of_funds_operations_share", "ri_debt_sh": "use_of_funds_debt_repayment_share",
             "ri_acq_sh": "use_of_funds_acquisition_share", "bb_amt_vs_dvol": "buyback_amount_vs_avg_daily_trading_value",
             "bb_days": "buyback_period_days", "bb_trust": "buyback_via_trust_contract", "bb_prior_pct": "treasury_shares_already_held_pct",
             "ca_ratio": "cancelled_shares_pct_of_outstanding", "bo_ratio": "bonus_shares_per_share", "sp_ratio": "split_ratio"}
QUESTIONS = {
    "materiality": {"type": "score", "instructions": "How material is this corporate disclosure to the company's intrinsic business value?",
                    "criteria": ["Immaterial or routine", "Minor", "Moderate", "Significant", "Transformative"]},
    "surprise": {"type": "score", "instructions": "How unexpected is this disclosure for investors, given its type and size?",
                 "criteria": ["Fully routine and expected", "Somewhat expected", "Unexpected", "Highly unexpected"]},
    "certainty": {"type": "score", "instructions": "How certain and binding is the economic effect described?",
                  "criteria": ["Speculative or preliminary", "Conditional", "Largely certain", "Binding and final"]},
    "financing_negative": {"type": "noul", "instructions": "The disclosure involves issuing new shares, convertible bonds, or other financing that dilutes or burdens existing shareholders."},
    "shareholder_return": {"type": "noul", "instructions": "The disclosure returns capital to shareholders (buyback, share cancellation) or signals management confidence in the share price."},
    "recurring": {"type": "noul", "instructions": "The disclosed effect on revenue or earnings is likely recurring rather than one-time."},
    "direction": {"type": "choice", "instructions": "Expected effect on this stock's price relative to similar-sized Korean stocks over the next 1-5 trading days.",
                  "criteria": {"positive": "Outperform similar stocks", "neutral": "No meaningful difference", "negative": "Underperform similar stocks"}},
}


def key() -> str:
    from dotenv import dotenv_values
    return (dotenv_values(ROOT / ".env.live").get("JEV_API_KEY") or "").strip()


def state_of(e: dict, with_kr: bool) -> dict:
    facts = {v: (round(e[k], 4) if isinstance(e.get(k), float) else e.get(k)) for k, v in FIELDS_EN.items()
             if e.get(k) is not None and e.get(k) == e.get(k)}
    s = {"market": "KOSPI/KOSDAQ (Korea)", "disclosure_type": KIND_EN.get(e["kind"], e["kind"]),
         "is_correction_filing": bool(e.get("sub")), "voluntary_disclosure": bool(e.get("autonomous")),
         "extracted_facts": facts,
         "stock_context": {"return_on_disclosure_day_pct": round(e["ret0"], 2) if e.get("ret0") == e.get("ret0") else None,
                           "return_prior_5_days_pct": round(e["ret5"], 2) if e.get("ret5") == e.get("ret5") else None,
                           "avg_daily_trading_value_krw_100m": round(e["dv20"], 1) if e.get("dv20") else None}}
    if with_kr:
        txt = t2.mask_text((t2.DOCS / f"{e['rcept_no']}.txt").read_text(encoding="utf-8"), e["name"], e["stock"])
        s["original_excerpt_korean"] = txt[:600]
    return s


def ask(e: dict, variant: str, k: str) -> dict:
    body = {"state": state_of(e, variant == "EN_KR"), "model": "jev-latest", "questions": QUESTIONS}
    for attempt in range(3):
        t0 = time.time()
        try:
            r = requests.post(URL, json=body, headers={"Authorization": f"Bearer {k}"}, timeout=120)
            if r.status_code == 200:
                a = r.json()["answers"]
                out = {"rcept_no": e["rcept_no"], "variant": variant, "sec": round(time.time() - t0, 3)}
                for q in ("materiality", "surprise", "certainty"):
                    out[q] = a[q]["score"]; out[q + "_c"] = a[q].get("confidence")
                for q in ("financing_negative", "shareholder_return", "recurring"):
                    out[q] = a[q]["noul"]
                p = a["direction"].get("probabilities") or {}
                out["dir"] = p.get("positive", 0) - p.get("negative", 0); out["dir_c"] = a["direction"].get("confidence")
                return out
            err = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001
            err = type(exc).__name__
        time.sleep(2 * (attempt + 1))
    return {"rcept_no": e["rcept_no"], "variant": variant, "error": err}


def collect(variant: str):
    OUT.mkdir(parents=True, exist_ok=True)
    cache = OUT / f"jev_{variant}.jsonl"
    done = {json.loads(l)["rcept_no"] for l in cache.read_text(encoding="utf-8").splitlines()} if cache.exists() else set()
    ev = [e for e in t2.jl(t2.OUT / "events_features.jsonl") if e.get("has_doc") and e["rcept_no"] not in done]
    k = key()
    with cf.ThreadPoolExecutor(max_workers=4) as ex, cache.open("a", encoding="utf-8") as fh:
        for res in ex.map(lambda e: ask(e, variant, k), ev):
            fh.write(json.dumps(res, ensure_ascii=False) + "\n")
    print(f"[Jev v2] {variant} 새 호출 {len(ev)}건")


JEV_COLS = ["materiality", "surprise", "certainty", "financing_negative", "shareholder_return", "recurring", "dir",
            "materiality_c", "surprise_c", "certainty_c", "dir_c"]


def evaluate():
    ev = pd.DataFrame(t2.jl(t2.OUT / "events_features.jsonl"))
    cal = list(t2.load_px(t2.PRICE / "kr_005930.csv")["date"])
    for kd in t2.KINDS:
        ev[f"k_{kd}"] = (ev.kind == kd).astype(float)
    ctx = [f"k_{kd}" for kd in t2.KINDS] + t2.CTX_COLS
    fields = [c for c in FIELDS_EN if c in ev.columns]
    variants = {}
    for v in ("EN", "EN_KR"):
        f = OUT / f"jev_{v}.jsonl"
        if f.exists():
            j = pd.DataFrame(t2.jl(f)).drop_duplicates("rcept_no", keep="last")
            j = j[j.get("error").isna()] if "error" in j else j
            variants[v] = j.set_index("rcept_no")[JEV_COLS].add_prefix(f"{v}_")
    for v, j in variants.items():
        ev = ev.join(j, on="rcept_no")
    specs = {"CTX(유형+주가맥락)": ctx, "FIELDS(T2 필드모델)": ctx + fields}
    for v in variants:
        specs[f"JEV_{v}(맥락+Jev)"] = ctx + [f"{v}_{c}" for c in JEV_COLS]
        specs[f"JEV_{v}+FIELDS"] = ctx + fields + [f"{v}_{c}" for c in JEV_COLS]
    L = ["# Jev 재시험 — 영어·구조화·원자 질문 (2026-09-29)", "",
         "read-only. `tools/research/research_jev_dart_v2_20260929.py`(사전등록 docstring). 평가 = 2026-07 이후 적격, T2 채점 동일.", "",
         "## 1. 조합 모델(월 워크포워드 LightGBM) — A: 예측 상위20% − 나머지(유형 기준선 잔차)", "",
         "| 모델 | h | A (t) | 전반 / 후반 | 상위 20% 초과 평균 (t) |", "|---|---|---:|---|---:|"]
    post_mask = (ev.rcept_dt >= t2.CUTOFF) & ev.elig
    for name, feats in specs.items():
        for h in (1, 5):
            pred, base = t2.walk_forward(ev, h, feats, cal)
            ev[f"base{h}"] = base
            sub = ev[post_mask]
            r = t2.spreads(sub, pred[sub.index], h)
            f = lambda a, t: "-" if a is None else (f"{a:+.2f} ({t:+.2f})" if t is not None else f"{a:+.2f}")  # noqa: E731
            L.append(f"| {name} | {h} | {f(r.get('A'), r.get('A_t'))} | {f(r.get('A_H1'), None)} / {f(r.get('A_H2'), None)} | "
                     f"{f(r.get('top_ex'), r.get('top_ex_t'))} |")
    L += ["", "## 2. 단일 원자 점수 — 사후 창 A(상위20% − 나머지)", "", "| 변형 · 점수 | h1 A (t) | h5 A (t) |", "|---|---:|---:|"]
    for v in variants:
        for c in ("materiality", "surprise", "certainty", "financing_negative", "shareholder_return", "recurring", "dir"):
            cells = []
            for h in (1, 5):
                sub = ev[post_mask]
                r = t2.spreads(sub, sub[f"{v}_{c}"], h)
                cells.append(f"{r['A']:+.2f} ({r['A_t']:+.2f})" if r.get("A_t") is not None else "-")
            L.append(f"| {v} · {c} | {cells[0]} | {cells[1]} |")
    for v in variants:
        j = pd.DataFrame(t2.jl(OUT / f"jev_{v}.jsonl"))
        lat = pd.to_numeric(j.get("sec"), errors="coerce").dropna()
        L.append(f"\n{v}: 호출 {len(j)} · 오류 {int(j.get('error').notna().sum()) if 'error' in j else 0} · 응답 중앙 {lat.median():.2f}s")
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    if "--collect" in sys.argv:
        collect("EN"); collect("EN_KR")
    evaluate()
