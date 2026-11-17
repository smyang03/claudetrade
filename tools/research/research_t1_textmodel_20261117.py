"""T1 전용 텍스트 모델 (2026-11-17, 운영자 "진행해" — 분기 슬롯 운영자 예외) — 분석 전용, 라이브 무변경.

질문: 실적 보도자료 텍스트(로컬 모델, API 없음)를 우리 T1 결과로 학습시키면 T1 점수(T1S)에 더해지는
정보가 있는가. 특히 직전 분기 보도자료 대비 '새로 나온 내용'(Lazy Prices·novelty 문헌)이 20세션 드리프트를 가르는가.

=== 사전등록 (실행 전 고정. 결과를 본 뒤 규칙 변경 금지, 이탈은 '이탈'로 기록) ===
[데이터] data/analysis/jev_round2_20260929/base.csv.gz 5,021건(2019~2026, I1 선정 + 무작위 대조, 개장 전 8-K 매칭).
  본문 jev_8k_t1_20260929/docs/{acc}.txt, 직전 분기 jev_round2_20260929/prior_docs/{prior_acc}.txt (priors.csv prior_ok).
  라벨 gA20(open e→open e+20 IWM 초과 gross), 학습은 1/99 winsorize, net = gA20 − 0.44%.
[텍스트 표현]
  A TF-IDF(1–2gram, min_df 5, 최대 5만, 본문 앞 12,000자) → ridge
  B FinBERT(yiyanghkust/finbert-pretrain, 공시·콜 2019년까지 학습) 평균 임베딩 + (현재−직전) 차분 + novelty → ridge
  C bge-small-en-v1.5 같은 구성 → ridge
  청크 = 200단어, 문서당 앞 16청크, max_seq 256. novelty = 현재 청크 중 직전 분기 청크와 최대 코사인 < 0.90 비율,
  평균(1−최대 코사인). 직전 없음 = 차분 0 + 플래그.
[워크포워드] 테스트 연도 Y=2020..2026, 학습 = 연도 < Y 중 x20 < Y 첫 진입 e(퍼지). ridge alpha ∈ {1,10,100,1000}는
  학습 구간 마지막 연도를 내부 검증으로 선택. 텍스트 점수 = 워크포워드 예측(전부 OOS).
[구간] 개발 = 테스트 연도 2020–2023. **봉인 = 2024–2026**(bge 공개 이후, FinBERT 학습 이후 — 사후기억 오염 최소).
[선택] 개발 구간 I1 표본에서 OLS gA20w ~ T1S_z + text_z 의 text 계수 날짜 클러스터 t가 가장 큰 모델 1개(양수일 때만).
[합격(봉인 구간, 선택 모델)]
  (1) I1 OLS text 계수 날짜 클러스터 t ≥ 2.0, 개발과 같은 부호
  (2) 규칙 V: I1 중 text_z 하위 20%(학습 분포 기준 컷) 제외(0 수익) — 쌍대 차이 > 0, 날짜 클러스터 t ≥ 2.0
  (3) (2)의 20세션 블록 부트스트랩(2,000회) 95% 구간 하한 > 0
  셋 다 → '후보'(forward 쉐도우 → 사다리). 하나라도 미달 → 기각. 나머지 모델의 봉인 수치는 보조(판정 아님).
[한계] 생존편향(현 상장 종목), 8-K 매칭분만(외국 6-K 제외), FinBERT/bge 사전학습 자료의 사후 정보 잔존 가능.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
R2 = ROOT / "data" / "analysis" / "jev_round2_20260929"
DOCS = ROOT / "data" / "analysis" / "jev_8k_t1_20260929" / "docs"
PRIOR = R2 / "prior_docs"
OUT = ROOT / "data" / "analysis" / "t1_textmodel_20261117"
COST = 0.0044
MODELS = {"finbert": "yiyanghkust/finbert-pretrain", "bge": "BAAI/bge-small-en-v1.5"}
NCH, CW = 16, 200
DEV, SEAL = (2020, 2023), (2024, 2026)


def load_base():
    b = pd.read_csv(R2 / "base.csv.gz")
    p = pd.read_csv(R2 / "priors.csv")
    p = p[p.prior_ok == True][["acc", "prior_acc"]]  # noqa: E712
    b = b.merge(p, on="acc", how="left")
    b["entry_date"] = pd.to_datetime(b.entry_date).dt.strftime("%Y-%m-%d")
    return b.reset_index(drop=True)


def read(fp: Path) -> str:
    return fp.read_text(encoding="utf-8", errors="ignore") if fp.exists() else ""


def chunks(t: str) -> list[str]:
    w = re.sub(r"\s+", " ", t).split(" ")
    return [" ".join(w[i:i + CW]) for i in range(0, min(len(w), NCH * CW), CW)] or [""]


# ------------------------------------------------------------------ embed (GPU)
def cmd_embed(name: str):
    from sentence_transformers import SentenceTransformer
    import torch
    OUT.mkdir(parents=True, exist_ok=True)
    b = load_base()
    m = SentenceTransformer(MODELS[name], device="cuda" if torch.cuda.is_available() else "cpu")
    m.max_seq_length = 256
    if torch.cuda.is_available():
        m.half()
    accs = list(dict.fromkeys(list(b.acc) + [a for a in b.prior_acc.dropna()]))
    emb = {}
    for i, a in enumerate(accs):
        fp = DOCS / f"{a}.txt"
        if not fp.exists():
            fp = PRIOR / f"{a}.txt"
        ch = chunks(read(fp))
        e = m.encode(ch, batch_size=32, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=False)
        emb[a] = e.astype(np.float16)
        if i % 500 == 0:
            print(f"[{name}] {i}/{len(accs)}", flush=True)
    np.savez_compressed(OUT / f"emb_{name}.npz", **{k.replace("-", "_"): v for k, v in emb.items()})
    print(f"[{name}] saved {len(emb)}")


def doc_feats(name: str, b: pd.DataFrame) -> np.ndarray:
    z = np.load(OUT / f"emb_{name}.npz")
    get = lambda a: z[a.replace("-", "_")].astype(np.float32) if isinstance(a, str) and a.replace("-", "_") in z else None  # noqa: E731
    rows = []
    for a, pa in zip(b.acc, b.prior_acc):
        c = get(a)
        dm = c.mean(0); dm /= (np.linalg.norm(dm) + 1e-9)
        p = get(pa)
        if p is not None and len(p):
            pm = p.mean(0); pm /= (np.linalg.norm(pm) + 1e-9)
            sim = c @ p.T
            mx = sim.max(1)
            nov = [float((mx < 0.90).mean()), float((1 - mx).mean()), 1.0]
            diff = dm - pm
        else:
            nov = [0.0, 0.0, 0.0]; diff = np.zeros_like(dm)
        rows.append(np.concatenate([dm, diff, nov]))
    return np.vstack(rows)


# ------------------------------------------------------------------ stats
def wins(y):
    lo, hi = np.nanpercentile(y, [1, 99]); return np.clip(y, lo, hi)


def cluster_ols(y, X, cl):
    """OLS + 클러스터(날짜) 강건 SE. X에 상수 포함."""
    X = np.asarray(X, float); y = np.asarray(y, float)
    XtXi = np.linalg.pinv(X.T @ X); beta = XtXi @ X.T @ y; u = y - X @ beta
    meat = np.zeros((X.shape[1], X.shape[1]))
    for g in pd.unique(cl):
        idx = cl == g; s = X[idx].T @ u[idx]; meat += np.outer(s, s)
    G = len(pd.unique(cl)); n, k = X.shape
    V = XtXi @ meat @ XtXi * (G / (G - 1)) * ((n - 1) / (n - k))
    return beta, beta / np.sqrt(np.diag(V))


def day_t(x, key):
    s = pd.Series(np.asarray(x, float), index=np.asarray(key)).groupby(level=0).mean().dropna()
    return float(s.mean() / (s.std(ddof=1) / math.sqrt(len(s)))) if len(s) > 2 else float("nan")


def block_boot(diff, e, n=2000, seed=0):
    blk = (np.asarray(e) // 20)
    df = pd.DataFrame({"d": diff, "b": blk})
    g = df.groupby("b").d.agg(["sum", "count"])
    rng = np.random.default_rng(seed); S, C = g["sum"].values, g["count"].values
    ms = []
    for _ in range(n):
        i = rng.integers(0, len(S), len(S)); ms.append(S[i].sum() / C[i].sum())
    return np.percentile(ms, [2.5, 97.5]).tolist()


# ------------------------------------------------------------------ walk-forward text score
def wf_scores(X, b, sparse=False):
    from sklearn.linear_model import Ridge
    y = wins(b.gA20.values)
    out = np.full(len(b), np.nan)
    yrs = b.year.values; e = b.e.values; x20 = b.x20.values
    for Y in range(2020, 2027):
        te = yrs == Y
        if not te.any():
            continue
        cut = e[te].min()
        tr = (yrs < Y) & (x20 < cut)
        # alpha: 학습 마지막 연도를 내부 검증
        ly = yrs[tr].max(); itr = tr & (yrs < ly) & (x20 < e[yrs == ly].min()); iva = tr & (yrs == ly)
        best, ba = -9, 100.0
        for a in (1.0, 10.0, 100.0, 1000.0):
            if itr.sum() < 100:
                break
            mdl = Ridge(alpha=a).fit(X[itr], y[itr]); p = mdl.predict(X[iva])
            ic = pd.Series(p).corr(pd.Series(y[iva]), method="spearman")
            if ic > best:
                best, ba = ic, a
        mdl = Ridge(alpha=ba).fit(X[tr], y[tr])
        out[te] = mdl.predict(X[te])
        # 학습 분포 기준 하위 20% 컷(규칙 V용)
        ptr = mdl.predict(X[tr])
        b.loc[te, "_cut20"] = np.percentile(ptr, 20); b.loc[te, "_mu"] = ptr.mean(); b.loc[te, "_sd"] = ptr.std()
    return out


def evaluate(b, col, period):
    lo, hi = period
    d = b[(b.year >= lo) & (b.year <= hi) & (b.is_i1 == 1) & b[col].notna()].copy()
    tz = (d.T1S - d.T1S.mean()) / d.T1S.std()
    xz = (d[col] - d[col].mean()) / d[col].std()
    X = np.column_stack([np.ones(len(d)), tz, xz])
    beta, t = cluster_ols(wins(d.gA20.values), X, d.entry_date.values)
    net = d.gA20 - COST
    excl = d[col] < d[col + "_cut20"]
    diff = np.where(excl, 0.0, net) - net
    ci = block_boot(diff, d.e.values)
    return {"n": int(len(d)), "beta_text": float(beta[2]), "t_text": float(t[2]), "t_T1S": float(t[1]),
            "excl_n": int(excl.sum()), "excl_mean_net": float(net[excl].mean()) if excl.any() else None,
            "keep_mean_net": float(net[~excl].mean()), "rule_diff": float(diff.mean()),
            "rule_t": day_t(diff, d.entry_date.values), "rule_ci": ci,
            "excl_tail": float((d.gA20[excl] < -0.2).mean()) if excl.any() else None,
            "excl_hr": float((d.gA20[excl] > 0.3).mean()) if excl.any() else None}


def cmd_eval():
    b = load_base()
    res = {}
    # A TF-IDF
    from sklearn.feature_extraction.text import TfidfVectorizer
    txt = [read(DOCS / f"{a}.txt")[:12000] for a in b.acc]
    # 이탈 방지: TF-IDF 어휘는 연도별 학습 구간에서만 적합해야 하나 비용상 전체 어휘 사용 — 어휘(라벨 아님)만 공유
    V = TfidfVectorizer(ngram_range=(1, 2), min_df=5, max_features=50000, sublinear_tf=True).fit(txt)
    XA = V.transform(txt)
    feats = {"tfidf": XA}
    for nm in MODELS:
        if (OUT / f"emb_{nm}.npz").exists():
            feats[nm] = doc_feats(nm, b)
    for nm, X in feats.items():
        col = f"s_{nm}"
        b[col] = wf_scores(X, b)
        b[col + "_cut20"] = b["_cut20"]
        res[nm] = {"dev": evaluate(b, col, DEV), "seal": evaluate(b, col, SEAL)}
    dev_t = {k: v["dev"]["t_text"] for k, v in res.items()}
    pick = max(dev_t, key=dev_t.get)
    verdict = "선택 없음(개발 t 전부 ≤ 0) → 기각"
    if dev_t[pick] > 0:
        s = res[pick]["seal"]
        ok = (s["t_text"] >= 2.0 and np.sign(s["beta_text"]) == np.sign(res[pick]["dev"]["beta_text"])
              and s["rule_diff"] > 0 and s["rule_t"] >= 2.0 and s["rule_ci"][0] > 0)
        verdict = f"선택 {pick} → {'후보' if ok else '기각'}"
    out = {"pick": pick, "dev_t": dev_t, "verdict": verdict, "results": res}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "results.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    b[["ticker", "date", "entry_date", "year", "is_i1", "gA20", "T1S"] + [f"s_{k}" for k in feats]].to_csv(
        OUT / "scores.csv.gz", index=False, compression="gzip")
    for nm, r in res.items():
        for ph in ("dev", "seal"):
            x = r[ph]
            print(f"{nm:8s} {ph:4s} n={x['n']:4d} text t {x['t_text']:+.2f} (T1S t {x['t_T1S']:+.2f}) | "
                  f"하위20% 제외 {x['excl_n']}건 평균 {x['excl_mean_net']:+.4f} vs 유지 {x['keep_mean_net']:+.4f} "
                  f"차이 {x['rule_diff']:+.4f} t {x['rule_t']:+.2f} CI [{x['rule_ci'][0]:+.4f},{x['rule_ci'][1]:+.4f}] "
                  f"제외 꼬리 {x['excl_tail']:.3f} 대박 {x['excl_hr']:.3f}")
    print(verdict)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("embed"); e.add_argument("name", choices=list(MODELS))
    sub.add_parser("eval")
    a = ap.parse_args()
    sys.exit(cmd_embed(a.name) if a.cmd == "embed" else cmd_eval())
