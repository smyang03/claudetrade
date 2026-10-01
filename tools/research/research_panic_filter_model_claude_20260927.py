#!/usr/bin/env python3
"""패닉 밤 — 모델/Claude가 '들어갈지 말지'를 고르면 나아지나 (read-only, 2026-09-27).

운영자: "모델을 살려서 모델이 판단하게, 또는 Claude가 판단할 수 있는지 — 예측력 없는 거 알지만 트라이."
유일 생존 축(패닉 다음 밤, 233740)의 신호 249건 중 어느 밤을 받을지를 판단기에게 맡긴다.
판단기가 가치가 있으면 '받은 밤'이 '버린 밤'보다 좋아야 한다.

## 사전 등록(실행 전 고정)
피처(전부 신호일 D 15:20~종가 시점에 알 수 있는 값): 코스닥 당일 종가 등락, 당일 갭, 강신호 여부,
직전 5일 누적(D 포함), 20일 실현변동성, MA20 이격, 직전 미국 세션 SPY 등락(D 이전 날짜의 미국 종가만), 요일.
결과: 원장 net_pct(233740, 종가 매수 → 익일 시가, 비용 반영).

A. ML 필터 — 워크포워드: 연도 Y 예측에 **Y 이전 신호만** 학습(최소 2년), 로지스틱(표준화, C=1 고정, 튜닝 없음).
   라벨 = net > 0. 규칙 = 예측확률 ≥ 학습셋 승률이면 받는다. 대상 연도 2018~2026.
B. Claude 필터 — `claude-opus-5`에 **날짜·연도 없이** 피처 숫자만 주고 TAKE/SKIP + 확신도(0~1).
   학습·예시 없음(제로샷). 시스템 프롬프트에 이 레인의 백필 성과는 알려주지 않는다(누설 방지).
   ⚠️ 한계: 큰 숫자 조합(예: 2020-03)은 Claude가 역사로 알아볼 수 있다 — 완전한 블라인드는 아니다.

판정(둘 다 같은 기준): ① 받은 밤 평균 − 버린 밤 평균 차이의 t(Welch) ≥ 2
② 전반(2018~2021)·후반(2022~2026) 부호 모두 양수 ③ 받은 밤 건당 샤프 > 전체 건당 샤프.
받은 건수가 전체의 20% 미만이면 판정 보류(너무 적게 받는 필터는 수익 자체를 깎는다).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import math
import statistics as st
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "data" / "shadow" / "kr_index_etf_panic.jsonl"
KQ = ROOT / "data" / "analysis" / "kr_index_etf_prices.csv"
SPY = ROOT / "data" / "analysis" / "voltarget_20260924" / "SPY.csv"
CACHE = ROOT / "data" / "analysis" / "panic_filter_20260927_claude.jsonl"
OUT = ROOT / "docs" / "reports" / "panic_filter_model_claude_20260927.md"
FEATS = ["chg", "gap", "strong", "ret5", "rv20", "ma20", "spy_prev", "dow"]


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def build():
    kq = [(r["date"], f(r["kq_close"])) for r in csv.DictReader(KQ.open(encoding="utf-8-sig"))]
    kq = [(d, c) for d, c in kq if c]
    ki = {d: i for i, (d, _) in enumerate(kq)}
    spy = sorted((r[next(iter(r))][:10], f(r["Close"])) for r in csv.DictReader(SPY.open(encoding="utf-8")))
    spy = [(d, c) for d, c in spy if c]
    rows = []
    for line in LEDGER.open(encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("kind") != "trade" or r.get("ticker") != "233740" or r.get("net_pct") is None:
            continue
        d = r["session_date"]
        i = ki.get(d)
        if i is None or i < 25:
            continue
        c = [x for _, x in kq[i - 25: i + 1]]
        rets = [c[k] / c[k - 1] - 1 for k in range(1, len(c))]
        us = [(sd, sc) for sd, sc in spy if sd < d]          # D 이전 날짜의 미국 종가만
        if len(us) < 2:
            continue
        y, m, dd = map(int, d.split("-"))
        rows.append({"date": d, "net": float(r["net_pct"]),
                     "chg": float(r.get("kq_chg_close") or 0), "gap": float(r.get("kq_gap") or 0),
                     "strong": 1.0 if r.get("strength") == "strong" else 0.0,
                     "ret5": (c[-1] / c[-6] - 1) * 100, "rv20": st.pstdev(rets[-20:]) * 100,
                     "ma20": (c[-1] / st.mean(c[-20:]) - 1) * 100,
                     "spy_prev": (us[-1][1] / us[-2][1] - 1) * 100,
                     "dow": float(date(y, m, dd).weekday())})
    return sorted(rows, key=lambda x: x["date"])


def ml_filter(rows):
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    out = {}
    for y in range(2018, 2027):
        tr = [r for r in rows if r["date"] < f"{y}-01-01"]
        te = [r for r in rows if r["date"][:4] == str(y)]
        if len(tr) < 30 or not te:
            continue
        X = [[r[k] for k in FEATS] for r in tr]
        lab = [1 if r["net"] > 0 else 0 for r in tr]
        sc = StandardScaler().fit(X)
        mdl = LogisticRegression(C=1.0, max_iter=1000).fit(sc.transform(X), lab)
        base = sum(lab) / len(lab)
        p = mdl.predict_proba(sc.transform([[r[k] for k in FEATS] for r in te]))[:, 1]
        for r, pp in zip(te, p):
            out[r["date"]] = pp >= base
    return out


SYSTEM = ("You are evaluating a single overnight trade in a Korean KOSDAQ index leveraged ETF. "
          "The rule buys at today's close and sells at tomorrow's open. You will see only numbers known at "
          "today's close. Decide whether taking this overnight trade is worthwhile. "
          "Reply with JSON only: {\"decision\": \"TAKE\" or \"SKIP\", \"confidence\": number 0..1, "
          "\"reason\": short string}.")


def ask_claude(r):
    import anthropic
    client = anthropic.Anthropic()
    msg = (f"KOSDAQ index today: close change {r['chg']:+.2f}%, opening gap {r['gap']:+.2f}%.\n"
           f"KOSDAQ 5-day cumulative return (incl. today): {r['ret5']:+.2f}%.\n"
           f"KOSDAQ 20-day realized daily volatility: {r['rv20']:.2f}%.\n"
           f"KOSDAQ distance from its 20-day moving average: {r['ma20']:+.2f}%.\n"
           f"S&P 500 (SPY) change in the most recent US session before today's Korean close: {r['spy_prev']:+.2f}%.\n"
           f"Weekday index (0=Mon..4=Fri): {int(r['dow'])}.\n"
           f"Signal strength flag: {'strong' if r['strong'] else 'normal'}.")
    try:
        resp = client.messages.create(model="claude-opus-5", max_tokens=4000, system=SYSTEM,
                                      messages=[{"role": "user", "content": msg}])
    except anthropic.RateLimitError:
        return None
    except anthropic.APIStatusError as e:
        return {"error": f"{e.status_code}"}
    if resp.stop_reason == "refusal":
        return {"error": "refusal"}
    txt = "".join(b.text for b in resp.content if b.type == "text").strip()
    s, e = txt.find("{"), txt.rfind("}")
    try:
        j = json.loads(txt[s:e + 1])
        return {"decision": str(j.get("decision", "")).upper(), "confidence": float(j.get("confidence", 0)),
                "reason": str(j.get("reason", ""))[:200]}
    except (ValueError, TypeError):
        return {"error": "parse", "raw": txt[:200]}


def claude_filter(rows):
    done = {}
    if CACHE.exists():
        for line in CACHE.open(encoding="utf-8"):
            j = json.loads(line)
            done[j["date"]] = j
    todo = [r for r in rows if r["date"] not in done and r["date"] >= "2018-01-01"]
    from dotenv import load_dotenv
    load_dotenv(str(ROOT / ".env.live"), override=False)
    with cf.ThreadPoolExecutor(max_workers=6) as ex, CACHE.open("a", encoding="utf-8") as fh:
        for r, res in zip(todo, ex.map(ask_claude, todo)):
            if res is None or "error" in res:
                print("실패", r["date"], res)
                continue
            rec = {"date": r["date"], **res}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            done[r["date"]] = rec
    return {d: (v.get("decision") == "TAKE") for d, v in done.items() if "decision" in v}, done


def judge(rows, take, label):
    rs = [r for r in rows if r["date"] in take]
    tk = [r["net"] for r in rs if take[r["date"]]]
    sk = [r["net"] for r in rs if not take[r["date"]]]
    al = [r["net"] for r in rs]
    if len(tk) < 5 or len(sk) < 5:
        return f"| {label} | {len(al)} | {len(tk)} | 표본 부족 | | | | | |"
    diff = st.mean(tk) - st.mean(sk)
    se = math.sqrt(st.variance(tk) / len(tk) + st.variance(sk) / len(sk))
    t = diff / se if se else 0
    h = []
    for lo, hi in (("2018", "2021"), ("2022", "2026")):
        a = [r["net"] for r in rs if lo <= r["date"][:4] <= hi and take[r["date"]]]
        b = [r["net"] for r in rs if lo <= r["date"][:4] <= hi and not take[r["date"]]]
        h.append(st.mean(a) - st.mean(b) if a and b else float("nan"))
    sh_t, sh_a = st.mean(tk) / st.stdev(tk), st.mean(al) / st.stdev(al)
    ok = t >= 2 and all(x > 0 for x in h) and sh_t > sh_a and len(tk) >= 0.2 * len(al)
    return (f"| {label} | {len(al)} | {len(tk)} ({len(tk)/len(al):.0%}) | {st.mean(tk):+.3f}% | {st.mean(sk):+.3f}% | "
            f"**{diff:+.3f}%p · t {t:+.2f}** | {h[0]:+.3f} / {h[1]:+.3f} | {sh_t:.3f} vs {sh_a:.3f} | "
            f"{'**통과**' if ok else '기각'} |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claude", action="store_true", help="Claude API 호출 포함(유료)")
    args = ap.parse_args()
    rows = build()
    L = ["# 패닉 밤 — 모델·Claude가 고르면 나아지나 (2026-09-27)", "",
         "read-only. `tools/research/research_panic_filter_model_claude_20260927.py`(피처·규칙·판정은 docstring에 실행 전 고정).",
         f"신호 {len(rows)}건(233740, 2016~2026). 평가 구간 2018~2026(ML은 그 이전 연도로만 학습).", "",
         "| 판단기 | 평가 건수 | 받은 밤 | 받은 평균 | 버린 평균 | 차이 · t | 전반 / 후반 차이 | 건당 샤프(받음 vs 전체) | 판정 |",
         "|---|---:|---:|---:|---:|---:|---|---|---|"]
    ml = ml_filter(rows)
    L.append(judge(rows, ml, "A. ML 로지스틱(워크포워드)"))
    strong = {r["date"]: r["strong"] == 1.0 for r in rows if r["date"] >= "2018-01-01"}
    L.append(judge(rows, strong, "참고: 강신호만(기존 사전등록 규칙)"))
    if args.claude:
        cl, raw = claude_filter(rows)
        L.append(judge(rows, cl, "B. Claude 제로샷(블라인드)"))
        conf = {d: (v.get("decision") == "TAKE" and v.get("confidence", 0) >= 0.6) for d, v in raw.items() if "decision" in v}
        L.append(judge(rows, conf, "B'. Claude TAKE & 확신 ≥ 0.6"))
        L += ["", f"Claude 응답 {len(cl)}건 · TAKE 비율 {sum(cl.values())/max(1,len(cl)):.0%}"]
    L += ["", "## 읽는 법", "",
          "- 필터의 값 = 받은 밤이 버린 밤보다 좋은가. 전체보다 적게 받으므로 연 수익은 줄 수 있다 — 샤프로 본다.",
          "- 강신호 행은 이미 사전등록된 규칙이라 비교 기준이다(판정 대상 아님)."]
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
