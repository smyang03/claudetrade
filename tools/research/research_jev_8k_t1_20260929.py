"""Jev x 8-K 실적 보도자료 → T1 신규 정보 검증 (2026-09-29, 사전등록)

질문: 실적 보도자료(8-K Item 2.02, EX-99.1)에서 Jev가 뽑은 가이던스·매출추세·톤·일회성·부실 신호가
EPS 서프라이즈+가격 피처만 쓰는 T1(M2_lgbm_reg, FEATS_A)에 새 정보를 더하는가.

=== 사전등록 (결과를 보기 전 고정. 이탈은 보고서에 '이탈'로 기록) ===
[표본] T1 build 이벤트(A_open, H20) 중 date ≥ 2019-01-01 이고
  (I1 선정 거래(trades_I1.csv.gz) ∪ 전체 T1 이벤트(2019+) 중 무작위 3,000건(seed 0)).
[8-K 매칭] SEC submissions(recent + 추가 페이지)에서 form=8-K(8-K/A 제외), items에 "2.02",
  |acceptance일 − 이벤트일| ≤ 3일, acceptanceDateTime < 진입 세션(entry_date) 09:30 ET.
  acceptanceDateTime은 UTC('Z')로 해석해 America/New_York으로 변환한다.
  [이탈·결과 보기 전 수정] 초안은 ET로 가정했으나 실측 분포(AAPL 20:30Z 등, 피크 20–21시·11–12시 UTC =
  장후 16–17시·장전 7–8시 ET)가 UTC임을 보여 수정. ET 가정 시 BMO 2,115건이 장 시작 후로 오분류됐다.
  복수면 이벤트일에 가장 가까운 것. 문서 = 인덱스의 EX-99.1(없으면 EX-99*, 그다음 primaryDocument).
  HTML 제거 후 공백 정리, 앞 6,000자.
[Jev] state = {"company_press_release_excerpt": text, "reported_eps_vs_consensus": beat/miss/inline(sgn)},
  질문(영어) guidance choice{raised,maintained,lowered,withdrawn_or_none}, revenue_trend score 4단계,
  tone score 5단계, one_time noul, distress noul. 1이벤트 1호출.
[평가]
  E1 paired: 매칭 이벤트만. 모델 M2_lgbm_reg(T1 동일 하이퍼), 타깃 gA20(open[e]→open[e+20] − IWM).
    연도별 walk-forward(train: year<Y 이고 x20 < Y의 첫 e, test: Y=2020..2026).
    T1-only = FEATS_A, T1+Jev = FEATS_A + Jev 피처(guidance 확률 4, rev score, tone score, one_time, distress,
    confidence 3). 선정: score ≥ 같은 모델의 직전 연도 OOS 점수 90분위 → 선정 연도 2021..2026.
    net = gA20 − 0.0044. 주(entry week)별 평균 net(T1+Jev) − 평균 net(T1-only), 두 모델 모두 선정 있는 주,
    NW lag 4. 통과 = t ≥ 2.5 이고 두 반기(2021–23, 2024–26) 모두 양수 → '후보(shadow 권고)'.
  E2 veto: I1 거래(2019+, 매칭분). veto = guidance argmax ∈ {lowered, withdrawn_or_none} 또는 distress ≥ 0.5.
    거부된 거래의 net 평균(day-cluster t, 음수면 veto가 합계 net 개선), 유지분 vs 전체 평균.
  E3 단변량: guidance argmax별 gA20 평균(day-cluster t), 무작위 표본과 I1 분리.
  라벨 점검: guidance 20건 스니펫 출력 → 수동 대조.
  E1b(추가 사전등록, 채점 전): 저장된 T1 OOS 점수(scores_A_open_H20 M2_lgbm_reg)를 통제변수로 두고
    gA20 ~ T1점수 + Jev 피처(표준화) OLS, Jev 계수별 day-cluster t. 전체 매칭분·무작위 표본만 각각.
    마지막 fold의 Jev 피처 gain 비중도 보고(0이면 E1 null은 무정보가 아니라 미사용).
  강건성: dd ∈ {0,+1}(8-K가 이벤트일 당일/익일)만으로 E2·E3 재계산. 매칭률 AMC/BMO 분리.
  입력 절단 점검: 전문에서 첫 guidance|outlook 위치가 6,000자 뒤인 비율.
  라벨 점검은 guidance 클래스별 5건 층화(20건).
  회사명·티커는 Jev 입력에서 'the Company'로 가린다.
=== 후속 사전등록 (2026-09-29, 1차 결과를 본 뒤·후속 결과를 보기 전 고정) ===
[F1 가이던스 재채점] 가림 버그 수리(정식명·약칭·티커 단어경계 모두 'the Company'). 입력 = 전문 앞 800자(헤드라인)
  + 전문 전체에서 guidance|outlook|reaffirm|reiterat|full-year/fiscal-year 전망 문구 주변 [−300,+700] 창
  (safe harbor/forward-looking 문단 포함 창 제외), 합쳐 4,000자 상한. 질문 1개 choice 6지:
  raised / maintained / lowered / initiated(첫·신규 연도 가이던스) / withdrawn(철회·중단) / not_provided.
  평가: E1b' = gA20(winsorize) ~ T1 OOS 점수 + p_raised, p_lowered, p_initiated, p_withdrawn (+ g_net=p_raised−p_lowered
  단독 모형), day-cluster t, 전체·무작위. E3' = 클래스별 gA20. 부활 기준: 무작위 표본에서 g_net 또는 p_lowered
  |t| ≥ 2.5 이고 전체 표본과 부호 일치. 아니면 가이던스 축 종결.
[F2 8-K 미매칭·발행인 유형] 대상 I1 거래 전체(2017–2026; I1 OOS 점수가 2017부터라 '2014–2018'은 2017–18만 가능).
  분류: matched_8k / no_8k / after_cutoff / no_cik(현 company_tickers에 없음). 발행인 유형(PIT): 이벤트일 이전 400일 안
  10-Q·10-K(/A) 제출 있으면 domestic, 없고 20-F·40-F·6-K 있으면 foreign, 둘 다 없으면 unknown.
  규칙 R = 'foreign 발행인 I1 픽 제외'. 검증 창 = 2017–18(관찰 창 2019+ 밖). net = gA20 − 0.0044.
  통과: 2017–18에서 foreign − 나머지 평균 차 < 0, day+ticker 이중 클러스터 t ≤ −2.0, 2019–26 부호 일치.
  통과 시 라이브 근사(I1 ∩ dvol20 ≥ $5M, 세션당 1건 최고점, 동시 7, 20세션 보유) net/건·t·건수 변화 산출.
[주의] 표본에 I1 선정분이 과대 포함 → E1은 T1 전체 우주가 아닌 편향 표본의 상대 비교다.
"""
from __future__ import annotations

import concurrent.futures as cf
import html
import json
import math
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
OUT = ROOT / "data" / "analysis" / "jev_8k_t1_20260929"
SUB = OUT / "submissions"; DOCS = OUT / "docs"; IDX = OUT / "index"
UA = {"User-Agent": "claudetrade research smyang03@gmail.com", "Accept-Encoding": "gzip, deflate"}
JURL = "https://api.typesafe.ai/v1/systemone"
FEE = 0.0044
_lock = threading.Lock(); _last = [0.0]


def sec_get(url):
    for att in range(4):
        with _lock:
            w = 0.13 - (time.time() - _last[0])
            if w > 0:
                time.sleep(w)
            _last[0] = time.time()
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2 + 3 * att)
    return None


def jl(p):
    return [json.loads(l) for l in Path(p).read_text(encoding="utf-8").splitlines() if l.strip()] if Path(p).exists() else []


# ------------------------------------------------------------ sample
def sample():
    fp = OUT / "sample.csv.gz"
    if fp.exists():
        return pd.read_csv(fp, parse_dates=["date", "entry_date", "week"])
    import research_model_T1_earnings_20260927 as t1
    ev, _ = t1.load_events(); cal, ti, W = t1.load_prices(); d, _ = t1.build(ev, cal, ti, W)
    d = d[d.gA20.notna() & (d.date >= "2019-01-01")].copy()
    tr = pd.read_csv(ROOT / "data/analysis/model_T1_impl_20260927/trades_I1.csv.gz", parse_dates=["date"])
    key = set(zip(tr.ticker, tr.date))
    d["is_i1"] = [(a, b) in key for a, b in zip(d.ticker, d.date)]
    rnd = d.sample(3000, random_state=0).index
    d["is_rand"] = d.index.isin(rnd)
    s = d[d.is_i1 | d.is_rand].copy()
    cols = ["ticker", "date", "hour", "entry_date", "e", "x20", "week", "year", "gA20", "dvol20", "close_a",
            "is_i1", "is_rand"] + t1.FEATS_A
    s = s[cols].reset_index(drop=True)
    OUT.mkdir(parents=True, exist_ok=True)
    s.to_csv(fp, index=False, compression="gzip")
    print("sample", len(s), "i1", int(s.is_i1.sum()), "rand", int(s.is_rand.sum()), "i1 in trades", len(tr[tr.date >= "2019-01-01"]))
    return s


# ------------------------------------------------------------ SEC
def cik_map():
    fp = OUT / "company_tickers.json"
    if not fp.exists():
        fp.write_bytes(sec_get("https://www.sec.gov/files/company_tickers.json").content)
    j = json.loads(fp.read_text())
    return {v["ticker"].upper(): int(v["cik_str"]) for v in j.values()}


def filings(cik):
    fp = SUB / f"{cik}.json"
    if fp.exists():
        return pd.read_json(fp)
    r = sec_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    if r is None:
        return pd.DataFrame()
    j = r.json(); fr = [pd.DataFrame(j["filings"]["recent"])]
    for f in j["filings"].get("files", []):
        if f.get("filingTo", "9999") >= "2018-12-01":
            rr = sec_get("https://data.sec.gov/submissions/" + f["name"])
            if rr is not None:
                fr.append(pd.DataFrame(rr.json()))
    df = pd.concat(fr, ignore_index=True)
    df = df[(df.form == "8-K") & df["items"].astype(str).str.contains("2.02", regex=False)
            & (df.filingDate >= "2018-12-01")]
    df = df[["accessionNumber", "filingDate", "acceptanceDateTime", "items", "primaryDocument"]]
    df.to_json(fp, orient="records")
    return df.reset_index(drop=True)


def strip(h):
    h = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    return re.sub(r"\s+", " ", html.unescape(h)).strip()


def fetch_doc(cik, acc, prim):
    fp = DOCS / f"{acc}.txt"
    if fp.exists():
        return fp.read_text(encoding="utf-8"), "cache"
    nd = acc.replace("-", "")
    base = f"https://www.sec.gov/Archives/edgar/data/{cik}/{nd}/"
    r = sec_get(base + f"{acc}-index.htm")
    name, src = None, "primary"
    if r is not None:
        rows = re.findall(r'(?is)<tr[^>]*>(.*?)</tr>', r.text)
        cand = []
        for row in rows:
            m = re.search(r'href="([^"]+)"', row)
            tds = [strip(x) for x in re.findall(r'(?is)<td[^>]*>(.*?)</td>', row)]
            if m and len(tds) >= 4:
                cand.append((tds[3].upper(), m.group(1).split("/")[-1]))
        for want in ("EX-99.1", "EX-99"):
            hit = [n for t, n in cand if t.startswith(want) and not n.lower().endswith((".pdf", ".jpg", ".gif"))]
            if hit:
                name, src = hit[0], want; break
    name = name or prim
    d = sec_get(base + name)
    if d is None:
        return None, "nodoc"
    txt = strip(d.text)  # 전체 캐시, Jev 입력 시 앞 6,000자
    fp.write_text(txt, encoding="utf-8")
    return txt, src


def collect_sec():
    s = sample(); cm = cik_map(); t0 = time.time()
    SUB.mkdir(parents=True, exist_ok=True); DOCS.mkdir(parents=True, exist_ok=True)
    s["cik"] = s.ticker.str.upper().str.replace(".", "-", regex=False).map(cm)
    ciks = sorted(s.cik.dropna().astype(int).unique())
    with cf.ThreadPoolExecutor(4) as ex:
        F = dict(zip(ciks, ex.map(filings, ciks)))
    print(f"submissions {len(ciks)} CIK {time.time()-t0:.0f}s", flush=True)
    rows = []
    for i, e in s.iterrows():
        rec = {"ticker": e.ticker, "date": str(e.date.date()), "hour": e.hour, "status": "no_cik"}
        if pd.notna(e.cik):
            f = F[int(e.cik)]; rec["status"] = "no_8k"
            if len(f):
                acc_t = pd.to_datetime(f.acceptanceDateTime, utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
                cutoff = e.entry_date + pd.Timedelta(hours=9, minutes=30)
                dd = (acc_t.dt.normalize() - e.date).dt.days
                ok = (dd.abs() <= 3) & (acc_t < cutoff)
                if ok.any():
                    j = dd[ok].abs().idxmin()
                    rec.update(status="matched", cik=int(e.cik), acc=f.accessionNumber[j],
                               accept=str(acc_t[j]), prim=f.primaryDocument[j], dd=int(dd[j]))
                elif ((dd.abs() <= 3)).any():
                    rec["status"] = "after_cutoff"
        rows.append(rec)
    m = pd.DataFrame(rows)
    mm = m[m.status == "matched"]
    with cf.ThreadPoolExecutor(4) as ex:
        res = list(ex.map(lambda r: fetch_doc(int(r.cik), r.acc, r.prim), mm.itertuples()))
    m.loc[mm.index, "doc_src"] = [x[1] for x in res]
    m.loc[mm.index, "nchar"] = [len(x[0]) if x[0] else 0 for x in res]
    m.to_csv(OUT / "match.csv", index=False)
    print(m.status.value_counts().to_dict(), m.doc_src.value_counts().to_dict(), f"total {time.time()-t0:.0f}s")
    (OUT / "sec_time.json").write_text(json.dumps({"sec_seconds": time.time() - t0}))


# ------------------------------------------------------------ Jev
QUESTIONS = {
    "guidance": {"type": "choice",
                 "instructions": "Compare the company's forward outlook/guidance in this press release with its prior guidance.",
                 "criteria": {"raised": "guidance for revenue, EPS or other key metrics is raised versus prior guidance",
                              "maintained": "guidance is reaffirmed or essentially unchanged",
                              "lowered": "guidance is lowered or cut versus prior guidance",
                              "withdrawn_or_none": "guidance is withdrawn, suspended, or no forward guidance is given"}},
    "revenue_trend": {"type": "score", "instructions": "Year-over-year revenue trend reported in this release.",
                      "criteria": ["declining", "flat", "growing", "strongly growing"]},
    "tone": {"type": "score",
             "instructions": "Tone of management commentary about demand and business outlook.",
             "criteria": ["very negative", "negative", "neutral", "positive", "very positive"]},
    "one_time": {"type": "noul",
                 "instructions": "The reported results are materially affected by one-time or non-recurring items."},
    "distress": {"type": "noul",
                 "instructions": "The release mentions going-concern doubt, restructuring, covenant issues, or a planned equity offering."},
}


COMMON = set("""first general american united national international global great southern northern western eastern
public service services energy advanced applied digital capital pacific atlantic central royal standard universal federal
world alpha summit select premier allied community security western financial health healthcare medical power
southwest northwest texas florida california carolina old north south east west trust image intelligent bright
simply super vital green black white golden silver star smart sound total mid-america heritage liberty patriot
enterprise industrial commercial integrated innovative advance quality superior interactive""".split())


def mask(t, names):
    """회사명·약칭·티커를 'the Company'로 가린다."""
    full, tk = names
    full = (full or "").strip()
    core = re.sub(r"(?i)[,.]?\s+(inc|corp|corporation|co|ltd|plc|holdings?|group|company|n\.v|s\.a|l\.p|limited)\.?$", "", full).strip()
    core2 = re.sub(r"(?i)[,.]?\s+(inc|corp|corporation|co|ltd|plc|holdings?|group|company|limited)\.?$", "", core).strip()
    pats = sorted({x for x in (full, core, core2) if len(x) >= 4}, key=len, reverse=True)
    for x in pats:
        t = re.sub(r"\b" + re.escape(x) + r"(?![A-Za-z])", "the Company", t, flags=re.I)
    w0 = core2.split()[0] if core2 else ""
    if len(core2.split()) > 1 and len(w0) >= 5 and w0.lower() not in COMMON:   # 약칭 첫 단어(예: Acadia)
        t = re.sub(r"\b" + re.escape(w0.title()) + r"\b|\b" + re.escape(w0.upper()) + r"\b", "the Company", t)
    if len(tk) >= 2:
        t = re.sub(r"\b" + re.escape(tk) + r"\b", "the Company", t)
    return t


def jkey():
    from dotenv import dotenv_values
    return (dotenv_values(ROOT / ".env.live").get("JEV_API_KEY") or "").strip()


def ask(row, k):
    txt = mask((DOCS / f"{row['acc']}.txt").read_text(encoding="utf-8"), row["names"])[:6000]
    body = {"state": {"company_press_release_excerpt": txt, "reported_eps_vs_consensus": row["eps"]},
            "model": "jev-latest", "questions": QUESTIONS}
    err = ""
    for att in range(3):
        t0 = time.time()
        try:
            r = requests.post(JURL, json=body, headers={"Authorization": f"Bearer {k}"}, timeout=120)
            if r.status_code == 200:
                j = r.json(); a = j["answers"]
                return {"acc": row["acc"], "ans": a, "usage": j.get("usage"), "model": j.get("model"),
                        "sec": round(time.time() - t0, 3)}
            err = f"HTTP {r.status_code} {r.text[:200]}"
        except Exception as exc:  # noqa: BLE001
            err = type(exc).__name__
        time.sleep(2 * (att + 1))
    return {"acc": row["acc"], "error": err}


def collect_jev(limit=None):
    s = sample(); m = pd.read_csv(OUT / "match.csv", parse_dates=["date"])
    s = s.merge(m, on=["ticker", "date"], how="left")
    s = s[(s.status == "matched") & (s.nchar > 200)].drop_duplicates("acc")
    cm = json.loads((OUT / "company_tickers.json").read_text())
    title = {int(v["cik_str"]): v["title"] for v in cm.values()}
    s["names"] = [[title.get(int(c), ""), t] for c, t in zip(s.cik, s.ticker)]
    s["eps"] = np.where(s.sgn > 0, "beat", np.where(s.sgn < 0, "miss", "inline"))
    cache = OUT / "jev.jsonl"
    done = {r["acc"] for r in jl(cache) if "ans" in r}
    todo = [r for r in s[["acc", "eps", "names"]].to_dict("records") if r["acc"] not in done][:limit]
    k = jkey(); t0 = time.time()
    with cf.ThreadPoolExecutor(4) as ex, cache.open("a", encoding="utf-8") as fh:
        for res in ex.map(lambda r: ask(r, k), todo):
            fh.write(json.dumps(res) + "\n"); fh.flush()
    print(f"Jev {len(todo)} calls {time.time()-t0:.0f}s")
    tf = OUT / "jev_time.json"; old = json.loads(tf.read_text()) if tf.exists() else []
    tf.write_text(json.dumps(old + [{"calls": len(todo), "seconds": time.time() - t0}]))


def jev_feats():
    R = [r for r in jl(OUT / "jev.jsonl") if "ans" in r]
    rows = []
    for r in R:
        a = r["ans"]; g = a["guidance"]; p = g.get("probabilities") or {}
        rows.append({"acc": r["acc"], "g_choice": g.get("choice"), "g_conf": g.get("confidence"),
                     **{f"g_{c}": p.get(c, np.nan) for c in QUESTIONS["guidance"]["criteria"]},
                     "rev": a["revenue_trend"].get("score"), "rev_conf": a["revenue_trend"].get("confidence"),
                     "tone": a["tone"].get("score"), "tone_conf": a["tone"].get("confidence"),
                     "one_time": a["one_time"].get("noul"), "distress": a["distress"].get("noul"),
                     "sec": r["sec"], "in_tok": (r.get("usage") or {}).get("input_tokens"),
                     "out_tok": (r.get("usage") or {}).get("output_tokens")})
    return pd.DataFrame(rows).drop_duplicates("acc", keep="last")


JEVF = ["g_raised", "g_maintained", "g_lowered", "g_withdrawn_or_none", "rev", "tone", "one_time", "distress",
        "g_conf", "rev_conf", "tone_conf"]


# ------------------------------------------------------------ evaluation
def cluster_t(v, g):
    v = np.asarray(v, float); df = pd.DataFrame({"v": v - v.mean(), "g": g})
    sums = df.groupby("g").v.sum().values; G = len(sums); n = len(v)
    if G < 3:
        return float("nan")
    var = (sums @ sums) / n ** 2 * G / (G - 1)
    return float(v.mean() / math.sqrt(var)) if var > 0 else float("nan")


def evaluate():
    import research_model_T1_earnings_20260927 as t1
    s = sample(); m = pd.read_csv(OUT / "match.csv", parse_dates=["date"]); J = jev_feats()
    d = s.merge(m[["ticker", "date", "status", "acc", "dd"]], on=["ticker", "date"], how="left").merge(J, on="acc", how="left")
    R = {"n_sample": len(s), "n_i1": int(s.is_i1.sum()), "n_rand": int(s.is_rand.sum()),
         "match_status": m.status.value_counts().to_dict(), "n_jev": int(d.tone.notna().sum()),
         "n_jev_i1": int((d.tone.notna() & d.is_i1).sum()), "n_jev_rand": int((d.tone.notna() & d.is_rand).sum())}
    R["jev_latency"] = {"mean_s": float(J.sec.mean()), "p95_s": float(J.sec.quantile(.95)),
                        "in_tok_sum": float(J.in_tok.sum()), "out_tok_sum": float(J.out_tok.sum()),
                        "in_tok_mean": float(J.in_tok.mean())}
    R["guidance_dist"] = J.g_choice.value_counts().to_dict()
    d["net"] = d.gA20 - FEE
    d["veto"] = d.g_choice.isin(["lowered", "withdrawn_or_none"]) | (d.distress >= 0.5)
    for c in ["rev", "tone", "one_time", "distress"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    # E1 paired walk-forward on matched events
    M = d[d.tone.notna()].copy().sort_values("e").reset_index(drop=True)
    first_e = M.groupby("year").e.min()
    for tag, feats in (("T1", t1.FEATS_A), ("T1J", t1.FEATS_A + JEVF)):
        M[tag] = np.nan
        for Y in range(2020, 2027):
            if Y not in first_e:
                continue
            tr = M[(M.year < Y) & (M.x20 < first_e[Y])]; te = M[M.year == Y]
            M.loc[te.index, tag] = t1.fit_predict("M2_lgbm_reg", tr, te, feats, "gA20")[0]
    imp = t1.fit_predict("M2_lgbm_reg", M[M.year < 2026], M[M.year == 2026], t1.FEATS_A + JEVF, "gA20")[1]
    tot = sum(imp.values()) or 1
    R["jev_gain_share_last_fold"] = float(sum(imp[f] for f in JEVF) / tot)
    R["gain_top"] = dict(sorted(((k_, round(v / tot, 3)) for k_, v in imp.items()), key=lambda x: -x[1])[:8])
    sel = {}
    for tag in ("T1", "T1J"):
        q = M.groupby("year")[tag].quantile(0.9)
        M[f"thr_{tag}"] = M.year.map(lambda y: q.get(y - 1, np.nan))
        M[f"sel_{tag}"] = (M.year >= 2021) & (M[tag] >= M[f"thr_{tag}"])
        S = M[M[f"sel_{tag}"]]
        sel[tag] = {"n": int(len(S)), "mean_net": float(S.net.mean()), "t_day": cluster_t(S.net, S.e),
                    "by_year": S.groupby("year").net.mean().round(4).to_dict()}
    wk = {tag: M[M[f"sel_{tag}"]].groupby("week").net.mean() for tag in ("T1", "T1J")}
    W = pd.concat(wk, axis=1).dropna(); W["diff"] = W.T1J - W.T1
    yrs = pd.to_datetime(W.index).year
    E1 = {"sel": sel, "weeks_both": int(len(W)), "diff_mean": float(W["diff"].mean()),
          "diff_t_nw4": t1.nw_t(W["diff"].values, 4),
          "h1_2021_23": float(W["diff"][yrs <= 2023].mean()), "h2_2024_26": float(W["diff"][yrs >= 2024].mean()),
          "overlap_trades": int((M.sel_T1 & M.sel_T1J).sum())}
    from scipy.stats import spearmanr
    oos = M[M.T1.notna()]
    E1["rank_ic"] = {tag: float(spearmanr(oos[tag], oos.gA20).correlation) for tag in ("T1", "T1J")}
    E1["rank_ic_rand_only"] = {tag: float(spearmanr(oos[oos.is_rand][tag], oos[oos.is_rand].gA20).correlation)
                               for tag in ("T1", "T1J")}
    E1["pass"] = bool(E1["diff_t_nw4"] >= 2.5 and E1["h1_2021_23"] > 0 and E1["h2_2024_26"] > 0)
    R["E1"] = E1
    # E2 veto on I1
    I = d[d.is_i1 & d.tone.notna()]
    V = I[I.veto]; K = I[~I.veto]
    R["E2"] = {"n_i1_matched": int(len(I)), "n_veto": int(len(V)), "all_mean": float(I.net.mean()),
               "veto_mean": float(V.net.mean()), "veto_t_day": cluster_t(V.net, V.e),
               "kept_mean": float(K.net.mean()),
               "veto_by_half": {h: float(g.net.mean()) for h, g in V.groupby(np.where(V.year <= 2022, "2019-22", "2023-26"))},
               "veto_n_by_reason": {"guidance": int(I.g_choice.isin(["lowered", "withdrawn_or_none"]).sum()),
                                    "distress": int((I.distress >= 0.5).sum())},
               "i1_unmatched_mean": float(d[d.is_i1 & d.tone.isna()].net.mean()),
               "n_i1_unmatched": int((d.is_i1 & d.tone.isna()).sum())}
    # 보조(사후, 판정 아님): withdrawn_or_none이 '가이던스 미제시'를 대량 흡수(파일럿 74%)해 veto 정의가 과대.
    for nm, msk in (("lowered_only", I.g_choice == "lowered"), ("distress_only", I.distress >= 0.5),
                    ("lowered_or_distress", (I.g_choice == "lowered") | (I.distress >= 0.5))):
        V2 = I[msk]
        R["E2"]["aux_" + nm] = {"n": int(len(V2)), "mean": float(V2.net.mean()), "t_day": cluster_t(V2.net, V2.e),
                                "by_half": {h: float(g.net.mean()) for h, g in V2.groupby(np.where(V2.year <= 2022, "2019-22", "2023-26"))}}
    # E3 univariate
    E3 = {}
    for nm, sub in (("rand", d[d.is_rand & d.tone.notna()]), ("i1", I)):
        E3[nm] = {c: {"n": int(len(g)), "gA20": float(g.gA20.mean()), "t_day": cluster_t(g.gA20, g.e)}
                  for c, g in sub.groupby("g_choice")}
        E3[nm + "_tone_ic"] = float(spearmanr(sub.tone, sub.gA20).correlation)
        E3[nm + "_rev_ic"] = float(spearmanr(sub.rev, sub.gA20).correlation)
    R["E3"] = E3
    # robustness dd in {0,1}
    d2 = d[d.dd.isin([0, 1])]
    I2 = d2[d2.is_i1 & d2.tone.notna()]
    R["E2_dd01"] = {"n": int(len(I2)), "n_veto": int(I2.veto.sum()), "veto_mean": float(I2[I2.veto].net.mean()),
                    "veto_t_day": cluster_t(I2[I2.veto].net, I2[I2.veto].e), "all_mean": float(I2.net.mean())}
    R["E3_rand_dd01"] = {c: {"n": int(len(g)), "gA20": float(g.gA20.mean()), "t_day": cluster_t(g.gA20, g.e)}
                         for c, g in d2[d2.is_rand & d2.tone.notna()].groupby("g_choice")}
    # E1b incremental regression with saved T1 OOS score
    sc = pd.read_csv(ROOT / "data/analysis/model_T1_robust_20260927/scores_A_open_H20.csv.gz",
                     usecols=["ticker", "date", "M2_lgbm_reg"], parse_dates=["date"])
    B = d[d.tone.notna()].merge(sc, on=["ticker", "date"], how="inner")
    B = B.assign(gw=B.gA20.clip(*B.gA20.quantile([.01, .99])))
    xs = ["M2_lgbm_reg", "g_raised", "g_lowered", "g_withdrawn_or_none", "rev", "tone", "one_time", "distress"]
    E1b = {}
    for nm, sub in (("all", B), ("rand", B[B.is_rand])):
        X = sub[xs].astype(float); X = (X - X.mean()) / X.std()
        X = np.column_stack([np.ones(len(X)), X.fillna(0).values]); y = sub.gw.values
        beta, *_ = np.linalg.lstsq(X, y, rcond=None); u = y - X @ beta
        XtXi = np.linalg.pinv(X.T @ X); g_ = sub.e.values
        meat = sum(np.outer(X[g_ == k_].T @ u[g_ == k_], X[g_ == k_].T @ u[g_ == k_]) for k_ in np.unique(g_))
        se = np.sqrt(np.diag(XtXi @ meat @ XtXi))
        E1b[nm] = {"n": int(len(sub)), **{c: {"b_pct_per_sd": round(float(beta[i + 1]) * 100, 3),
                                             "t_day": round(float(beta[i + 1] / se[i + 1]), 2)} for i, c in enumerate(xs)}}
    R["E1b"] = E1b
    # coverage details
    mt = pd.read_csv(OUT / "match.csv")
    R["match_by_hour"] = mt.groupby("hour").status.value_counts().unstack().fillna(0).astype(int).to_dict("index")
    R["dd_hist"] = mt.dd.value_counts().sort_index().to_dict()
    pos = []
    for acc in J.acc:
        t = (DOCS / f"{acc}.txt").read_text(encoding="utf-8"); mm = re.search(r"(?i)guidance|outlook", t)
        pos.append(mm.start() if mm else -1)
    pos = np.array(pos)
    R["outlook_pos"] = {"none": float((pos < 0).mean()), "within6000": float(((pos >= 0) & (pos < 6000)).mean()),
                        "beyond6000": float((pos >= 6000).mean())}
    R["outlook_pos_by_class"] = {c: float((pos[J.g_choice.values == c] >= 6000).mean()) for c in J.g_choice.unique()}
    M.to_csv(OUT / "oos_scores.csv.gz", index=False, compression="gzip")
    (OUT / "results.json").write_text(json.dumps(R, indent=1, default=str))
    print(json.dumps(R, indent=1, default=str))


def spot(n=20):
    J = jev_feats().groupby("g_choice", group_keys=False).apply(lambda g: g.sample(min(5, len(g)), random_state=1))
    for r in J.itertuples():
        t = (DOCS / f"{r.acc}.txt").read_text(encoding="utf-8")
        hits = [mm.start() for mm in re.finditer(r"(?i)guidance|outlook|expects?|forecast|reaffirm|rais|lower", t)]
        t = t[:6000]
        hits = [mm.start() for mm in re.finditer(r"(?i)guidance|outlook|reaffirm|full[- ]year", t)]
        snip = " | ".join(t[max(0, h - 150):h + 250] for h in hits[:3]) if hits else "(no outlook words in 6000) " + t[:200]
        print(f"### {r.acc} JEV={r.g_choice} conf={r.g_conf:.2f}\n{snip}\n")


# ============================================================ F1: guidance re-score
KW = re.compile(r"(?i)guidance|outlook|reaffirm|reiterat|full[- ]year\s+(?:20\d\d|fiscal)|fiscal\s+(?:year\s+)?20\d\d\s+(?:expect|estimat|target)")
BOILER = re.compile(r"(?i)forward[- ]looking statement|safe harbor|private securities litigation")
GQ = {"guidance": {"type": "choice",
                   "instructions": ("Based on the headline and outlook passages of this earnings press release, how did the "
                                    "company change its forward financial guidance (revenue, EPS, EBITDA or similar) versus its prior guidance?"),
                   "criteria": {"raised": "guidance raised versus prior guidance",
                                "maintained": "guidance reaffirmed, reiterated or essentially unchanged",
                                "lowered": "guidance lowered or cut versus prior guidance",
                                "initiated": "first-time guidance or initial guidance for a new fiscal year, with no comparison to a prior range",
                                "withdrawn": "guidance withdrawn or suspended",
                                "not_provided": "no forward financial guidance is given in the text"}}}


def outlook_text(t):
    head = t[:800]
    wins = []
    for mm in KW.finditer(t):
        a, b = max(0, mm.start() - 300), min(len(t), mm.start() + 700)
        if BOILER.search(t[a:b]) or a < 800 and b <= 800:
            continue
        if wins and a <= wins[-1][1]:
            wins[-1][1] = b
        else:
            wins.append([max(a, 800), b])
    body = " ... ".join(t[a:b] for a, b in wins if b > a)
    if not body:
        body = "(No outlook or guidance passage found in the release.)"
    return ("HEADLINE: " + head + "\nOUTLOOK PASSAGES: " + body)[:4800]


def ask_g(row, k):
    txt = outlook_text(mask((DOCS / f"{row['acc']}.txt").read_text(encoding="utf-8"), row["names"]))
    body = {"state": {"press_release_headline_and_outlook": txt}, "model": "jev-latest", "questions": GQ}
    err = ""
    for att in range(3):
        t0 = time.time()
        try:
            r = requests.post(JURL, json=body, headers={"Authorization": f"Bearer {k}"}, timeout=120)
            if r.status_code == 200:
                j = r.json()
                return {"acc": row["acc"], "ans": j["answers"]["guidance"], "usage": j.get("usage"),
                        "sec": round(time.time() - t0, 3), "nchar": len(txt)}
            err = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001
            err = type(exc).__name__
        time.sleep(2 * (att + 1))
    return {"acc": row["acc"], "error": err}


def collect_g(limit=None):
    J = jev_feats(); m = pd.read_csv(OUT / "match.csv")
    m = m[m.acc.isin(J.acc)].drop_duplicates("acc")
    cm = json.loads((OUT / "company_tickers.json").read_text())
    title = {int(v["cik_str"]): v["title"] for v in cm.values()}
    rows = [{"acc": r.acc, "names": [title.get(int(r.cik), ""), r.ticker]} for r in m.itertuples()]
    cache = OUT / "jev_guid2.jsonl"
    done = {r["acc"] for r in jl(cache) if "ans" in r}
    todo = [r for r in rows if r["acc"] not in done][:limit]
    k = jkey(); t0 = time.time()
    with cf.ThreadPoolExecutor(4) as ex, cache.open("a", encoding="utf-8") as fh:
        for res in ex.map(lambda r: ask_g(r, k), todo):
            fh.write(json.dumps(res) + "\n"); fh.flush()
    print(f"Jev guid2 {len(todo)} calls {time.time()-t0:.0f}s")


def ols_cl(y, X, groups_list):
    """OLS with (multi-way, CGM) cluster SE. groups_list: list of arrays; 2 entries → day+ticker−(day,ticker)."""
    X = np.column_stack([np.ones(len(y)), X]); beta = np.linalg.lstsq(X, y, rcond=None)[0]; u = y - X @ beta
    B = np.linalg.pinv(X.T @ X)

    def V(g):
        g = pd.factorize(pd.Series(g).astype(str))[0]
        S = np.zeros((X.shape[1], X.shape[1]))
        sc = np.zeros((g.max() + 1, X.shape[1])); np.add.at(sc, g, X * u[:, None])
        S = sc.T @ sc; G = g.max() + 1
        return B @ S @ B * G / max(G - 1, 1)
    if len(groups_list) == 1:
        Vt = V(groups_list[0])
    else:
        a, b = groups_list
        Vt = V(a) + V(b) - V(np.char.add(np.asarray(a, str), np.asarray(b, str)))
    se = np.sqrt(np.maximum(np.diag(Vt), 1e-18))
    return beta, beta / se


def eval_g():
    G = [r for r in jl(OUT / "jev_guid2.jsonl") if "ans" in r]
    g = pd.DataFrame([{"acc": r["acc"], "g2": r["ans"]["choice"], "g2_conf": r["ans"].get("confidence"),
                       **{"p_" + c: (r["ans"].get("probabilities") or {}).get(c, np.nan) for c in GQ["guidance"]["criteria"]},
                       "sec": r["sec"], "in_tok": (r.get("usage") or {}).get("input_tokens")} for r in G]).drop_duplicates("acc", keep="last")
    s = sample(); m = pd.read_csv(OUT / "match.csv", parse_dates=["date"]); J = jev_feats()[["acc", "g_choice"]]
    d = s.merge(m[["ticker", "date", "acc"]], on=["ticker", "date"]).merge(g, on="acc").merge(J, on="acc", how="left")
    sc = pd.read_csv(ROOT / "data/analysis/model_T1_robust_20260927/scores_A_open_H20.csv.gz",
                     usecols=["ticker", "date", "M2_lgbm_reg"], parse_dates=["date"])
    d = d.merge(sc, on=["ticker", "date"], how="left")
    d["gw"] = d.gA20.clip(*d.gA20.quantile([.01, .99])); d["net"] = d.gA20 - FEE
    d["g_net"] = d.p_raised - d.p_lowered
    R = {"n": len(d), "dist": d.g2.value_counts().to_dict(), "lat_mean": float(g.sec.mean()),
         "in_tok_sum": float(g.in_tok.sum()), "crosstab_old_new": pd.crosstab(d.g_choice, d.g2).to_dict()}
    E = {}
    for nm, sub in (("all", d), ("rand", d[d.is_rand]), ("i1", d[d.is_i1])):
        sub = sub[sub.M2_lgbm_reg.notna()]
        out = {"n": int(len(sub))}
        for tag, xs in (("full", ["M2_lgbm_reg", "p_raised", "p_lowered", "p_initiated", "p_withdrawn"]),
                        ("gnet", ["M2_lgbm_reg", "g_net"])):
            X = sub[xs].astype(float); X = ((X - X.mean()) / X.std()).fillna(0).values
            b, t = ols_cl(sub.gw.values, X, [sub.e.values])
            out[tag] = {c: {"b_pct_per_sd": round(float(b[i + 1]) * 100, 3), "t_day": round(float(t[i + 1]), 2)}
                        for i, c in enumerate(xs)}
        E[nm] = out
    R["E1b2"] = E
    R["E3b"] = {nm: {c: {"n": int(len(x)), "gA20": round(float(x.gA20.mean()), 4), "t_day": round(cluster_t(x.gA20, x.e), 2)}
                     for c, x in sub.groupby("g2")} for nm, sub in (("rand", d[d.is_rand]), ("i1", d[d.is_i1]))}
    rr = E["rand"]["gnet"]["g_net"]["t_day"]; rl = E["rand"]["full"]["p_lowered"]["t_day"]
    ok = lambda nm_, key, t_: abs(t_) >= 2.5 and np.sign(t_) == np.sign(E["all"][key[0]][key[1]]["t_day"])  # noqa: E731
    R["revive"] = bool(ok("rand", ("gnet", "g_net"), rr) or ok("rand", ("full", "p_lowered"), rl))
    (OUT / "results_guid2.json").write_text(json.dumps(R, indent=1, default=str))
    print(json.dumps(R, indent=1, default=str))
    return d


def spot_g(n=3):
    d = pd.DataFrame([{"acc": r["acc"], "g2": r["ans"]["choice"], "c": r["ans"].get("confidence")}
                      for r in jl(OUT / "jev_guid2.jsonl") if "ans" in r])
    d = d.groupby("g2", group_keys=False).apply(lambda x: x.sample(min(n, len(x)), random_state=2))
    for r in d.itertuples():
        t = outlook_text((DOCS / f"{r.acc}.txt").read_text(encoding="utf-8"))
        hs = [mm.start() for mm in re.finditer(r"(?i)rais|lower|reaffirm|reiterat|initial|withdr|suspend|guidance|outlook", t[800:])]
        snip = t[:200] + " || " + (" | ".join(t[800 + max(0, h - 100):800 + h + 220] for h in hs[:2]) if hs else t[800:1000])
        print(f"### {r.acc} NEW={r.g2} conf={r.c:.2f}\n{snip}\n")


# ============================================================ F2: 8-K status × filer type on I1
SUBF = OUT / "submissions_full"


def filings_full(cik):
    fp = SUBF / f"{cik}.json"
    if fp.exists():
        return pd.read_json(fp, dtype=False)
    r = sec_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    if r is None:
        return pd.DataFrame()
    j = r.json(); fr = [pd.DataFrame(j["filings"]["recent"])]
    for f in j["filings"].get("files", []):
        if f.get("filingTo", "9999") >= "2015-06-01":
            rr = sec_get("https://data.sec.gov/submissions/" + f["name"])
            if rr is not None:
                fr.append(pd.DataFrame(rr.json()))
    df = pd.concat(fr, ignore_index=True)
    df = df[(df.filingDate >= "2015-06-01") & df.form.isin(
        ["8-K", "10-Q", "10-K", "10-Q/A", "10-K/A", "20-F", "40-F", "6-K", "20-F/A", "40-F/A", "10-KT"])]
    df = df[["form", "accessionNumber", "filingDate", "acceptanceDateTime", "items"]].reset_index(drop=True)
    df.to_json(fp, orient="records")
    return df


def filer_stage():
    SUBF.mkdir(parents=True, exist_ok=True)
    T = pd.read_csv(ROOT / "data/analysis/model_T1_impl_20260927/trades_I1.csv.gz", parse_dates=["date", "entry_date"])
    cm = cik_map()
    T["cik"] = T.ticker.str.upper().str.replace(".", "-", regex=False).map(cm)
    ciks = sorted(T.cik.dropna().astype(int).unique()); t0 = time.time()
    with cf.ThreadPoolExecutor(4) as ex:
        F = dict(zip(ciks, ex.map(filings_full, ciks)))
    print(f"full submissions {len(ciks)} CIK {time.time()-t0:.0f}s", flush=True)
    st, ft = [], []
    for e in T.itertuples():
        if pd.isna(e.cik):
            st.append("no_cik"); ft.append("no_cik"); continue
        f = F[int(e.cik)]
        if not len(f):
            st.append("no_8k"); ft.append("unknown"); continue
        fd = pd.to_datetime(f.filingDate)
        prior = f[(fd < e.date) & (fd >= e.date - pd.Timedelta(days=400))].form
        dom = prior.isin(["10-Q", "10-K", "10-Q/A", "10-K/A", "10-KT"]).any()
        frn = prior.isin(["20-F", "40-F", "6-K", "20-F/A", "40-F/A"]).any()
        ft.append("domestic" if dom else ("foreign" if frn else "unknown"))
        k8 = f[(f.form == "8-K") & f["items"].astype(str).str.contains("2.02", regex=False)]
        if not len(k8):
            st.append("no_8k"); continue
        acc_t = pd.to_datetime(k8.acceptanceDateTime, utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
        dd = (acc_t.dt.normalize() - e.date).dt.days
        near = dd.abs() <= 3
        if (near & (acc_t < e.entry_date + pd.Timedelta(hours=9, minutes=30))).any():
            st.append("matched_8k")
        elif near.any():
            st.append("after_cutoff")
        else:
            st.append("no_8k")
    T["k8"] = st; T["filer"] = ft; T["net"] = T.gA20 - FEE
    T.to_csv(OUT / "i1_filer.csv.gz", index=False, compression="gzip")
    return T


def live_sim(T):
    """라이브 근사: dvol20 ≥ $5M, 세션당 최고점 1건, 동시 7, 20세션 보유."""
    c = T[T.dvol20 >= 5e6].sort_values(["e", "M2_lgbm_reg"], ascending=[True, False])
    keep, exits = [], []
    for sess, g in c.groupby("e", sort=True):
        exits = [x for x in exits if x > sess]
        if len(exits) < 7:
            keep.append(g.index[0]); exits.append(sess + 20)
    return T.loc[keep]


def eval_filer():
    fp = OUT / "i1_filer.csv.gz"
    T = pd.read_csv(fp, parse_dates=["date", "entry_date"]) if fp.exists() else filer_stage()
    T["per"] = np.where(T.year <= 2018, "2017-18", "2019-26")
    R = {"counts": {p: pd.crosstab(x.k8, x.filer).to_dict() for p, x in T.groupby("per")}}
    grp = {}
    for p, x in list(T.groupby("per")) + [("all", T)]:
        for col in ("k8", "filer"):
            for c, y in x.groupby(col):
                b, t = ols_cl(y.net.values, np.zeros((len(y), 0)), [y.e.values, y.ticker.values])
                grp[f"{p}|{col}={c}"] = {"n": int(len(y)), "net": round(float(y.net.mean()), 4), "t_2way": round(float(t[0]), 2),
                                        "n_tickers": int(y.ticker.nunique())}
    R["groups"] = grp
    diff = {}
    for p, x in list(T.groupby("per")) + [("all", T)]:
        for nm, flag in (("foreign_vs_rest", x.filer == "foreign"), ("no8k_vs_rest", x.k8 == "no_8k"),
                         ("no8k_domestic_vs_matched", None)):
            if nm == "no8k_domestic_vs_matched":
                y = x[x.k8.isin(["no_8k", "matched_8k"]) & (x.filer == "domestic")]; fl = (y.k8 == "no_8k")
            else:
                y = x; fl = flag
            if fl.sum() < 3:
                continue
            b, t = ols_cl(y.net.values, fl.values.astype(float)[:, None], [y.e.values, y.ticker.values])
            diff[f"{p}|{nm}"] = {"diff": round(float(b[1]), 4), "t_2way": round(float(t[1]), 2), "n_flag": int(fl.sum())}
    R["diffs"] = diff
    o = diff.get("2017-18|foreign_vs_rest", {}); i = diff.get("2019-26|foreign_vs_rest", {})
    R["pass_R"] = bool(o and o["diff"] < 0 and o["t_2way"] <= -2.0 and i.get("diff", 1) < 0)
    # impact of rule R on I1 and live approx (reported regardless; adoption only if pass_R)
    imp = {}
    for nm, X in (("I1_all", T), ("I1_skip_foreign", T[T.filer != "foreign"]),
                  ("LIVE_all", live_sim(T)), ("LIVE_skip_foreign", live_sim(T[T.filer != "foreign"].copy()))):
        for p, y in list(X.groupby("per")) + [("all", X)]:
            b, t = ols_cl(y.net.values, np.zeros((len(y), 0)), [y.e.values, y.ticker.values])
            sess = y.groupby("e").net.mean()
            import research_model_T1_earnings_20260927 as t1
            imp[f"{nm}|{p}"] = {"n": int(len(y)), "net": round(float(y.net.mean()), 4), "t_2way": round(float(t[0]), 2),
                                "t_sess_nw20": round(t1.nw_t(sess.values, 20), 2), "sum_net": round(float(y.net.sum()), 2)}
    R["impact"] = imp
    (OUT / "results_filer.json").write_text(json.dumps(R, indent=1, default=str))
    print(json.dumps(R, indent=1, default=str))


if __name__ == "__main__":
    st = sys.argv[1]
    if st == "guid2":
        collect_g(int(sys.argv[2]) if len(sys.argv) > 2 else None)
    elif st == "evalg":
        eval_g()
    elif st == "spotg":
        spot_g()
    elif st == "filer":
        filer_stage()
    elif st == "evalfiler":
        eval_filer()
    if st == "sec":
        collect_sec()
    elif st == "jev":
        collect_jev(int(sys.argv[2]) if len(sys.argv) > 2 else None)
    elif st == "eval":
        evaluate()
    elif st == "spot":
        spot()
