#!/usr/bin/env python3
"""패닉 밤 필터 — 모델 구조·학습 데이터를 바꾼 변형 6종 (read-only, 2026-09-27).

운영자: "데이터를 바꾼 모델이라든지 아키텍처를 바꿔보는 것도 해보고."

## 사전 등록(한 묶음 6변형, 실행 전 고정)
구조 3 × 데이터 2 = 6. 파라미터는 라이브러리 기본값 고정(튜닝 없음), random_state=0.
- 구조: 로지스틱(C=1) / 그래디언트 부스팅(sklearn 기본) / 랜덤포레스트(n_estimators=300, min_samples_leaf=5).
- 데이터: ① 233740 신호만 ② 233740 + 229200 신호(같은 날 두 ETF — 표본 2배, 결과 스케일 다름 → 라벨은 승/패).
피처·워크포워드(연도 Y는 Y 이전 신호로만 학습, 2018~2026 평가)·받는 규칙(확률 ≥ 학습 승률)은
`research_panic_filter_model_claude_20260927.py`와 동일. 평가는 항상 233740 결과.
판정: 받음−버림 차이 t ≥ **2.64**(6개 다중검정 보정, 양측 5%) · 전·후반 부호 양수 · 받은 밤 샤프 > 전체.
비교 기준선: "5일 누적 하락 큰 순 같은 비율" 단순 규칙 — 모델이 이걸 못 이기면 모델의 값은 0.
"""
from __future__ import annotations

import csv
import json
import math
import statistics as st
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
import research_panic_filter_model_claude_20260927 as base  # noqa: E402

OUT = ROOT / "docs" / "reports" / "panic_filter_variants_20260927.md"


def build_229200():
    """229200 원장 행을 233740 행과 같은 피처로(피처는 지수 기반이라 동일, 결과만 다름)."""
    by_date = {r["date"]: r for r in base.build()}
    out = []
    for line in base.LEDGER.open(encoding="utf-8"):
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("kind") == "trade" and r.get("ticker") == "229200" and r.get("net_pct") is not None \
                and r["session_date"] in by_date:
            out.append({**by_date[r["session_date"]], "net": float(r["net_pct"])})
    return out


def make(kind):
    from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    return {"logit": lambda: LogisticRegression(C=1.0, max_iter=1000),
            "gbm": lambda: GradientBoostingClassifier(random_state=0),
            "rf": lambda: RandomForestClassifier(n_estimators=300, min_samples_leaf=5, random_state=0)}[kind]()


def walk(rows_eval, rows_train, kind):
    from sklearn.preprocessing import StandardScaler
    take = {}
    for y in range(2018, 2027):
        tr = [r for r in rows_train if r["date"] < f"{y}-01-01"]
        te = [r for r in rows_eval if r["date"][:4] == str(y)]
        if len(tr) < 30 or not te:
            continue
        X = [[r[k] for k in base.FEATS] for r in tr]
        lab = [1 if r["net"] > 0 else 0 for r in tr]
        sc = StandardScaler().fit(X)
        mdl = make(kind).fit(sc.transform(X), lab)
        p = mdl.predict_proba(sc.transform([[r[k] for k in base.FEATS] for r in te]))[:, 1]
        thr = sum(lab) / len(lab)
        for r, pp in zip(te, p):
            take[r["date"]] = pp >= thr
    return take


def judge(rows, take, label, tcut=2.64):
    rs = [r for r in rows if r["date"] in take]
    tk = [r["net"] for r in rs if take[r["date"]]]
    sk = [r["net"] for r in rs if not take[r["date"]]]
    al = [r["net"] for r in rs]
    if len(tk) < 5 or len(sk) < 5:
        return f"| {label} | {len(tk)} | 표본 부족 | | | |", None
    d = st.mean(tk) - st.mean(sk)
    t = d / math.sqrt(st.variance(tk) / len(tk) + st.variance(sk) / len(sk))
    h = []
    for lo, hi in (("2018", "2021"), ("2022", "2026")):
        a = [r["net"] for r in rs if lo <= r["date"][:4] <= hi and take[r["date"]]]
        b = [r["net"] for r in rs if lo <= r["date"][:4] <= hi and not take[r["date"]]]
        h.append(st.mean(a) - st.mean(b) if a and b else float("nan"))
    sh = st.mean(tk) / st.stdev(tk)
    ok = t >= tcut and all(x > 0 for x in h) and sh > st.mean(al) / st.stdev(al)
    return (f"| {label} | {len(tk)} ({len(tk)/len(al):.0%}) | {d:+.3f}%p | {t:+.2f} | {h[0]:+.3f} / {h[1]:+.3f} | "
            f"{sh:.3f} | {'**통과**' if ok else '기각'} |"), (len(tk), d)


def main():
    rows = base.build()
    ev = [r for r in rows if r["date"] >= "2018-01-01"]
    both = rows + build_229200()
    L = ["# 패닉 밤 필터 — 모델 구조·데이터 변형 6종 (2026-09-27)", "",
         "read-only. `tools/research/research_panic_filter_variants_20260927.py`(사전등록 docstring).",
         f"평가: 233740 신호 {len(ev)}건(2018~2026, 연 단위 워크포워드). 문턱 t ≥ 2.64(6변형 보정).", "",
         "| 변형 | 받은 밤 | 받음−버림 | t | 전반 / 후반 | 받은 밤 샤프 | 판정 |", "|---|---:|---:|---:|---|---:|---|"]
    allsh = st.mean(r["net"] for r in ev) / st.stdev([r["net"] for r in ev])
    for data_lbl, train in (("233740만", rows), ("233740+229200", both)):
        for kind, klbl in (("logit", "로지스틱"), ("gbm", "부스팅"), ("rf", "랜덤포레스트")):
            line, _ = judge(ev, walk(ev, train, kind), f"{klbl} · {data_lbl}")
            L.append(line)
    # 단순 규칙 기준선: 같은 연도 안에서 5일 누적 하락 큰 순 절반
    simple = {}
    for y in range(2018, 2027):
        yr = sorted([r for r in ev if r["date"][:4] == str(y)], key=lambda r: r["ret5"])
        for i, r in enumerate(yr):
            simple[r["date"]] = i < len(yr) / 2
    line, _ = judge(ev, simple, "기준선: 해마다 5일 누적 하락 큰 절반(모델 아님)")
    L += [line, "", f"전체 건당 샤프 {allsh:.3f}.", "",
          "## 읽는 법", "",
          "- 6개를 한 번에 쳤으므로 하나가 t 2를 넘는 건 우연으로도 흔하다 — 그래서 문턱이 2.64다.",
          "- 기준선(피처 하나로 자른 규칙)을 못 이기면 모델 구조를 바꾼 의미가 없다."]
    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
