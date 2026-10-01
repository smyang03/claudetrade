#!/usr/bin/env python3
"""Jev(TypeSafe AI) vs Claude — DART 공시 원문 판독 비교 (2026-09-29).

운영자: "싸고 빠르게 주식을 판단할 수 있는지 검토". T2(`model_T2_dart_20260927.md`)와 **같은 표본·같은 채점**.
- 표본: 2026-07-01 이후 접수 공시(본문 확보분), 회사명·코드 가림(T2 mask_text), 앞 8,000자.
- Jev 질문(한 호출에 두 개):
  dir  = Choice {POS, NEUTRAL, NEG} — T2 Claude 프롬프트와 같은 판단 기준(다음 거래일 시가 매수, 1~5일, 비슷한 규모 대비)
  up   = Noul  "비슷한 규모 종목 평균보다 나을 것이다" 확률
- 점수: s_dir = p(POS) − p(NEG), s_up = noul. 채점은 T2 `spreads`(유형 기준선 잔차 상위20% − 나머지 A, 방향 B) 그대로.
- 사전등록 판정(T2와 동일 틀): A 또는 B의 t ≥ 3 이면서 전·후반 양수 → 후보 이상. Claude와 같은 표에 나란히.
- 추가(보정 검증): Jev confidence ≥ 0.8 인 POS/NEG만으로 B 재계산. 응답 시간·토큰·비용 실측.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
import research_model_T2_dart_20260927 as t2  # noqa: E402

OUT = ROOT / "data" / "analysis" / "jev_dart_20260929"
CACHE = OUT / "jev_scores.jsonl"
REPORT = ROOT / "docs" / "reports" / "jev_dart_20260929.md"
URL = "https://api.typesafe.ai/v1/systemone"
PRICE_PER_TOKEN = 42 / 1e9     # 사이트 공시 입력 단가

INSTR = ("이 공시가 나온 뒤 다음 거래일 시가에 매수해 1~5거래일 보유할 때, 비슷한 규모 종목들 평균 대비 주가 성과. "
         "공시 유형의 일반론보다 본문의 구체 내용(규모 대비 크기, 조건, 상대방, 자금 용도, 희석, 기간)을 근거로 판단")


def key() -> str:
    from dotenv import dotenv_values
    return (dotenv_values(ROOT / ".env.live").get("JEV_API_KEY") or "").strip()


def ask(e: dict, k: str) -> dict:
    text = t2.mask_text((t2.DOCS / f"{e['rcept_no']}.txt").read_text(encoding="utf-8"), e["name"], e["stock"])[:8000]
    body = {"state": "한국 상장사 DART 공시 원문(회사명은 A사로 가림):\n" + text, "model": "jev-latest",
            "questions": {
                "dir": {"type": "choice", "instructions": INSTR,
                        "criteria": {"POS": "비슷한 규모 종목 평균보다 나을 것", "NEUTRAL": "판단 근거가 약하거나 차이 없음",
                                     "NEG": "비슷한 규모 종목 평균보다 못할 것"}},
                "up": {"type": "noul", "instructions": "이 공시 이후 1~5거래일 동안 이 종목이 비슷한 규모 종목 평균보다 나을 것이다"}}}
    for attempt in range(3):
        t0 = time.time()
        try:
            r = requests.post(URL, json=body, headers={"Authorization": f"Bearer {k}"}, timeout=120)
            if r.status_code == 200:
                j = r.json(); a = j["answers"]
                return {"rcept_no": e["rcept_no"], "choice": a["dir"]["choice"], "conf": a["dir"].get("confidence"),
                        "p": a["dir"].get("probabilities"), "noul": a["up"].get("noul"), "model": j.get("model"),
                        "in_tok": j.get("usage", {}).get("input_tokens"), "sec": round(time.time() - t0, 3)}
            err = f"HTTP {r.status_code} {r.text[:120]}"
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}"
        time.sleep(2 * (attempt + 1))
    return {"rcept_no": e["rcept_no"], "error": err}


def collect():
    OUT.mkdir(parents=True, exist_ok=True)
    done = {json.loads(l)["rcept_no"] for l in CACHE.read_text(encoding="utf-8").splitlines()} if CACHE.exists() else set()
    ev = [e for e in t2.jl(t2.OUT / "events_features.jsonl") if e.get("has_doc") and e["rcept_dt"] >= t2.CUTOFF
          and e["rcept_no"] not in done]
    k = key()
    with cf.ThreadPoolExecutor(max_workers=4) as ex, CACHE.open("a", encoding="utf-8") as fh:
        for res in ex.map(lambda e: ask(e, k), ev):
            fh.write(json.dumps(res, ensure_ascii=False) + "\n")
    print(f"[Jev] 새 호출 {len(ev)}건")


def evaluate():
    ev = pd.DataFrame(t2.jl(t2.OUT / "events_features.jsonl"))
    cal = list(t2.load_px(t2.PRICE / "kr_005930.csv")["date"])
    for h in (1, 5):
        _, base = t2.walk_forward(ev, h, None, cal)
        ev[f"base{h}"] = base
    jv = pd.DataFrame(t2.jl(CACHE)).drop_duplicates("rcept_no", keep="last").set_index("rcept_no")
    cl = pd.DataFrame(t2.jl(t2.OUT / "claude_scores.jsonl")).drop_duplicates("rcept_no", keep="last").set_index("rcept_no")
    ev = ev.set_index("rcept_no")
    post = ev[(ev.rcept_dt >= t2.CUTOFF) & ev.elig & ev.index.isin(jv.index)].copy()
    ok = jv[jv.get("error").isna()] if "error" in jv else jv
    post = post[post.index.isin(ok.index)]
    pj = ok.loc[post.index]
    post["jev_dir"] = pj.choice
    post["jev_conf"] = pj.conf.astype(float)
    post["s_dir"] = pj.p.map(lambda d: (d or {}).get("POS", 0) - (d or {}).get("NEG", 0))
    post["s_up"] = pj.noul.astype(float)
    cc = cl.reindex(post.index)
    post["cl_dir"] = cc.direction
    post["s_cl"] = [(s if d == "POS" else -s if d == "NEG" else 0.0) if isinstance(d, str) else np.nan
                    for d, s in zip(cc.direction, pd.to_numeric(cc.strength, errors="coerce"))]

    L = ["# Jev vs Claude — DART 공시 원문 판독 (2026-09-29)", "",
         "read-only. `tools/research/research_jev_dart_20260929.py`. 표본·채점은 T2와 동일(2026-07 이후, 적격, 규모매칭 초과, 날짜 클러스터).",
         f"평가 {len(post)}건 · 진입일 {post.entry.nunique()}일.", "",
         "| 판단기 | h | A: 상위20% − 나머지 (t) | B: POS − NEG (t) | 전반 / 후반 A | POS·NEG 건수 |", "|---|---|---:|---:|---|---|"]
    rows = [("Jev 방향 p(POS)−p(NEG)", "s_dir", "jev_dir"), ("Jev 예/아니오 확률", "s_up", None),
            ("Claude(opus-5) 방향", "s_cl", "cl_dir")]
    for lab, col, dcol in rows:
        for h in (1, 5):
            pos = (post[dcol] == "POS") if dcol else None
            neg = (post[dcol] == "NEG") if dcol else None
            r = t2.spreads(post, post[col], h, pos, neg)
            f = lambda v, t: "-" if v is None else f"{v:+.2f} ({t:+.2f})" if t is not None else f"{v:+.2f}"  # noqa: E731
            L.append(f"| {lab} | {h} | {f(r.get('A'), r.get('A_t'))} | {f(r.get('B'), r.get('B_t'))} | "
                     f"{f(r.get('A_H1'), None)} / {f(r.get('A_H2'), None)} | {r.get('B_npos', '-')}/{r.get('B_nneg', '-')} |")
    hi = post[post.jev_conf >= 0.8]
    L += ["", "## Jev 확신도 ≥ 0.8 만", ""]
    for h in (1, 5):
        r = t2.spreads(hi, hi.s_dir, h, hi.jev_dir == "POS", hi.jev_dir == "NEG")
        L.append(f"- h{h}: n {r['n']} · B(POS−NEG) {r.get('B')} (t {r.get('B_t')}) · POS/NEG {r.get('B_npos')}/{r.get('B_nneg')}")
    L += ["", "## 판정 분포 · 일치도", "",
          f"- Jev: {post.jev_dir.value_counts().to_dict()} · Claude: {post.cl_dir.value_counts().to_dict()}",
          f"- 방향 일치율(둘 다 판정 있는 건): {(post.jev_dir == post.cl_dir).mean()*100:.0f}%",
          f"- 확신도 분포: 중앙 {post.jev_conf.median():.2f} · ≥0.8 비율 {(post.jev_conf >= 0.8).mean()*100:.0f}%"]
    lat = pd.to_numeric(jv.get("sec"), errors="coerce").dropna()
    tok = pd.to_numeric(jv.get("in_tok"), errors="coerce").dropna()
    L += ["", "## 속도·비용 실측", "",
          f"- 호출 {len(jv)}건(오류 {int(jv.get('error').notna().sum()) if 'error' in jv else 0}) · 응답 중앙 {lat.median():.2f}s · p95 {lat.quantile(.95):.2f}s",
          f"- 입력 토큰 합 {int(tok.sum()):,} → 공시 단가 기준 약 ${tok.sum()*PRICE_PER_TOKEN:.4f} (Claude T2 사후 845건 $9.59)"]
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    if "--collect" in sys.argv:
        collect()
    evaluate()
