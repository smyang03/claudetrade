"""T3 — US 내부자 공개시장 매수(SEC Form 4) 모델 연구 (2026-09-27, 사전등록)

질문: Form 4 공개시장 매수(가격 밖의 새 정보: 직책·금액·군집·보유 증가율)가 filing 이후 IWM 대비
초과수익(비용 0.5% 왕복 후)을 예측하는가. 고전 규칙(7일 내 서로 다른 매수자 ≥2)은 2012~2026에 살아 있는가,
어떤 아키텍처가 그 규칙보다 나은가.

=== 사전등록 (실행 전 고정, 결과를 본 뒤 규칙 변경 금지. 이탈은 보고서에 '이탈'로 기록) ===

[원천]
- SEC Insider Transactions Data Sets 분기 zip. URL은 공식 인덱스 페이지
  (https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets)의 href를 그대로 사용
  (2026q2부터 경로가 /files/datastandardsinnovation/... 로 바뀜). User-Agent는 SEC 규정용 연락처,
  요청 간 0.5초 이상 대기. 2007q1~최신 분기 수집(2007은 이력 피처 워밍업 전용, 이벤트는 2008~).
- 읽는 파일: SUBMISSION(FILING_DATE, DOCUMENT_TYPE, ISSUERCIK, ISSUERTRADINGSYMBOL),
  NONDERIV_TRANS(TRANS_CODE, TRANS_ACQUIRED_DISP_CD, TRANS_DATE, TRANS_SHARES, TRANS_PRICEPERSHARE,
  SHRS_OWND_FOLWNG_TRANS), REPORTINGOWNER(RPTOWNERCIK, RPTOWNER_RELATIONSHIP, RPTOWNER_TITLE).

[거래 필터]
- DOCUMENT_TYPE == '4' 만(4/A 정정·Form 5 지연보고 제외). TRANS_CODE == 'P', TRANS_ACQUIRED_DISP_CD == 'A',
  TRANS_SHARES > 0, TRANS_PRICEPERSHARE > 0.
- 심볼 정리: 대문자·공백 제거, 앞의 거래소 접두어(NYSE/NASDAQ/AMEX/OTC..+':') 제거, 빈 값/NONE/N/A/NA 및
  ',' ';' 공백이 남은 다중 심볼은 제외(건수 보고). '.' '/' → '-'. CIK→현재 티커 대체 매핑은 쓰지 않는다
  (현재 시점 스냅샷이라 생존편향·재사용 위험).

[매수자 식별·직책]
- accession 1건 = 매수자 1명. 매수자 ID = 그 accession REPORTINGOWNER 중 최소 RPTOWNERCIK
  (공동 신고 펀드+GP+개인이 '군집 3명'으로 부풀지 않게).
- 직책은 accession 내 소유자 전부의 합집합: is_dir('Director'), is_off('Officer'), is_10pct('TenPercentOwner'),
  is_ceo = TITLE 정규식 r'\\bCEO\\b|CHIEF EXECUTIVE', is_cfo = r'\\bCFO\\b|CHIEF FINANCIAL' (대문자 변환 후).
- accession 금액 = Σ shares×price, 매수 주식 = Σ shares, 사후 보유 = max SHRS_OWND_FOLWNG_TRANS,
  보유 증가율 pct = bought / (post − bought); post−bought ≤ 0 또는 결측이면 10(신규 포지션), [0,10] 클립.

[이벤트]
- 이벤트 단위 = 종목(심볼) × FILING_DATE(F). 같은 날 여러 filing은 합산.
- 이벤트 시점 F는 '그날 장 마감 후 알려짐'으로 간주 → 진입 세션 e = F보다 엄격히 늦은 첫 거래일, open[e] 진입.
  앵커 a = e−1. 모든 가격 피처는 close[a]까지.
- 군집: 같은 종목에서 FILING_DATE ∈ [F−6, F] (7일), [F−29, F] (30일)인 매수자 ID 고유 수 n7, n30 (F 당일 포함).
- days_since = 같은 종목 직전 매수 filing(F 이전)과의 달력일수(없으면 결측).
- 이벤트 피처: n_day(당일 매수자 수), n7, n30, is_ceo/is_cfo/is_dir/is_off/is_10pct(당일 filing 합집합),
  lval = log1p(당일 총 $), pct_hold = 당일 accession pct 최대값, lval_dvol = log1p(총$ / dvol20 × 100),
  hi52 = close[a]/max(close[a−251..a]) − 1, lo52 = close[a]/min(...) − 1, ret20, ret60, vol20(일간 로그수익 std 20),
  lsize = log(raw close[a] × 평균 volume a−59..a) (시가총액 대신 거래대금 규모 대리값 — 발행주식 수 없음),
  days_since. month 계열은 넣지 않음.

[가격]
- yfinance 일봉 auto_adjust=False, actions=True, 2007-01-01~2026-09-27, 배치 100, threads=2, 캐시.
  Close = 분할 조정, Adj Close = 분할+배당 조정. raw close = Close × (이후 분할 비율 누적곱).
  수익률은 배당 포함 조정 시가 O_adj = Open × AdjClose/Close. 벤치 IWM 같은 방식. 거래일 달력 = IWM.
- 티커 재사용/오매핑 방지(가격 일치 필터): 이벤트 가중평균 매수가 / 마지막 TRANS_DATE 이하 최근 세션의
  raw close ∈ [0.67, 1.5]가 아니면 제외(제외 건수 보고 = 오매핑·데이터 오류·상폐 대리지표).
- 트레이더빌리티(판정용 모집단): raw close[a] ≥ $3, dvol20(= 평균 Close×Volume, a−19..a) ≥ $1M, open[e]>0,
  청산 가격 존재, 앵커 이전 252세션 가격 존재 불요(결측 허용), 단 ret60 계산 가능(a ≥ 60).

[라벨·보유]
- H ∈ {20, 60} 세션. gross 초과 = O_adj[e+H]/O_adj[e] − IWM 동일 구간. net = gross − 0.005.

[아키텍처 5종 — 기본 파라미터, 튜닝 없음, 시드 0, n_jobs ≤ 2]
- M0 기준선 규칙: n7 ≥ 2 인 이벤트 전부 동일가중(순위 모델이 아님).
- M1 logistic: LogisticRegression(max_iter=2000), 라벨 net>0, 표준화 + 학습 중앙값 대치.
- M2 LightGBM 회귀: LGBMRegressor 기본값, 타깃 = gross 초과를 학습 1/99 분위 winsorize.
- M3 LightGBM LambdaRank: query = 진입 주(W-FRI), relevance = 주 내부 gross 초과 5분위(0~4).
- M4 MLP: MLPRegressor((32,16), early_stopping=True, random_state 0), 표준화 피처, 타깃 = winsorize 후 표준화.

[워크포워드]
- 테스트 연도 Y = 2012..2026, 학습 = 진입연도 2008..Y−1 중 청산 세션 인덱스 < Y 첫 세션 인덱스(퍼지 H). 연 1회 재학습.

[평가]
- 주 = 진입 세션의 W-FRI 주. 트레이더블 이벤트 N < 10인 주는 전 전략 공통 스킵(스킵 주 수 보고).
  같은 주에 같은 종목 이벤트가 여럿이어도 중복 제거하지 않는다(보고서에 명시).
- 모델: 매주 점수 상위 n = max(3, floor(0.1·N)) 동일가중 H 보유. 비교 (a) 그 주 전체 이벤트 동일가중(all),
  (b) M0 규칙(그 주 n7≥2 이벤트 동일가중).
- 차이(모델 − M0)는 M0 이벤트가 1건 이상인 주에서만 계산(주 수 보고).
- 지표: 주간 net 평균, Newey-West t(lag = ceil(H/5): H20→4, H60→12), 차이 NW t, 반기(2012–2018 / 2019–2026),
  주간 Spearman IC(점수 vs gross, 그 주 전체) 평균·NW t, 손익분기 비용 = 선택 이벤트 gross 초과 평균,
  이벤트 중앙값 net.
- 합격(M1~M4): 차이 NW t ≥ 3.0 AND 두 반기 모두 [모델 net > 0 AND 차이 > 0] AND 손익분기 ≥ 1.0%.
- 합격(M0): M0 주간 net NW t ≥ 3.0 AND 두 반기 net > 0 AND 손익분기 ≥ 1.0%.
- 검정 수 = 2 H × 5 = 10. t ≥ 3이 보정 문턱.
- 등급: 합격 셀 있음 = 확정 후보(확정은 쉐도우 forward 후) / t ≥ 2 & 반기 부호 유지 & 손익분기 ≥ 1% = 후보 / 그 외 기각.
- 비계수 진단(판정에 넣지 않음): M0 규칙을 트레이더빌리티 필터 없이(가격 일치·청산 가격만) IWM 대비로 보고.

[한계(사전 인지)]
- yfinance는 상폐 종목이 대부분 없다 → 생존편향. 가격 없는 이벤트 수 보고, 판정은 같은 모집단 내부 비교 위주.
- 규모 대리값은 거래대금(발행주식 수 없음). 벤치는 IWM 하나(규모매칭 아님).
- 분기 zip은 분기 종료 후 공개 → 백필 전용. 실시간 적용 시에는 EDGAR 일별 수집이 필요.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import re
import sys
import time
import urllib.request
import warnings
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", message="X does not have valid feature names")

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "analysis" / "model_T3_form4_20260927"
ZDIR = OUT / "zips"
TDIR = OUT / "trans"
PX = OUT / "prices"
OLD_CACHE = ROOT / "data" / "analysis" / "edgar_form345"
UA = "claudetrade research smyang03@gmail.com"  # SEC 접근 정책용 User-Agent
INDEX = "https://www.sec.gov/data-research/sec-markets-data/insider-transactions-data-sets"
START, END = "2007-01-01", "2026-09-27"
COST = 0.005
HS = (20, 60)
MODELS = ("M0_rule", "M1_logit", "M2_lgbm_reg", "M3_lgbm_rank", "M4_mlp")
FEATS = ["n_day", "n7", "n30", "is_ceo", "is_cfo", "is_dir", "is_off", "is_10pct", "lval", "pct_hold",
         "lval_dvol", "hi52", "lo52", "ret20", "ret60", "vol20", "lsize", "days_since"]
RE_CEO = re.compile(r"\bCEO\b|CHIEF EXECUTIVE")
RE_CFO = re.compile(r"\bCFO\b|CHIEF FINANCIAL")
RE_PREFIX = re.compile(r"^(NYSE\s*AMERICAN|NYSE|NASDAQ|AMEX|OTCBB|OTCQX|OTCQB|OTC|PINK)\s*[:\-]\s*")


# ---------------------------------------------------------------- SEC download + parse
def quarter_hrefs():
    req = urllib.request.Request(INDEX, headers={"User-Agent": UA})
    html = urllib.request.urlopen(req, timeout=60).read().decode("utf-8", "replace")
    hrefs = sorted(set(re.findall(r'href="([^"]*?(\d{4}q\d)_form345\.zip)"', html)), key=lambda x: x[1])
    return {q: ("https://www.sec.gov" + h if h.startswith("/") else h) for h, q in hrefs}


def get_zip(q, url):
    old = OLD_CACHE / f"{q}_form345.zip"
    if old.exists() and old.stat().st_size > 1000:
        return old  # 기존 캐시는 읽기만
    ZDIR.mkdir(parents=True, exist_ok=True)
    p = ZDIR / f"{q}_form345.zip"
    if p.exists() and p.stat().st_size > 1000:
        return p
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            data = urllib.request.urlopen(req, timeout=300).read()
            p.write_bytes(data)
            time.sleep(0.6)
            return p
        except Exception as e:  # noqa
            print("dl fail", q, e, flush=True); time.sleep(5)
    return None


def _tsv(z, name, cols):
    nm = next(n for n in z.namelist() if n.upper().endswith(name))
    with z.open(nm) as fh:
        return pd.read_csv(fh, sep="\t", dtype=str, usecols=cols, quoting=3, on_bad_lines="skip",
                           encoding_errors="replace", keep_default_na=False)


def clean_sym(s):
    s = (s or "").strip().upper()
    s = RE_PREFIX.sub("", s).strip()
    if s in ("", "NONE", "N/A", "NA", "-") or re.search(r"[,;\s]", s):
        return ""
    return s.replace(".", "-").replace("/", "-")


def parse_quarter(q, zp):
    z = zipfile.ZipFile(zp)
    sub = _tsv(z, "SUBMISSION.TSV", ["ACCESSION_NUMBER", "FILING_DATE", "DOCUMENT_TYPE", "ISSUERCIK",
                                      "ISSUERTRADINGSYMBOL"])
    sub = sub[sub.DOCUMENT_TYPE.str.strip() == "4"]
    tr = _tsv(z, "NONDERIV_TRANS.TSV", ["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
                                        "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD", "SHRS_OWND_FOLWNG_TRANS"])
    stats = {"q": q, "form4_filings": len(sub), "nonderiv_rows": len(tr)}
    tr = tr[(tr.TRANS_CODE.str.strip() == "P") & (tr.TRANS_ACQUIRED_DISP_CD.str.strip() == "A")]
    tr = tr[tr.ACCESSION_NUMBER.isin(set(sub.ACCESSION_NUMBER))].copy()
    tr["shares"] = pd.to_numeric(tr.TRANS_SHARES, errors="coerce")
    tr["price"] = pd.to_numeric(tr.TRANS_PRICEPERSHARE, errors="coerce")
    tr["post"] = pd.to_numeric(tr.SHRS_OWND_FOLWNG_TRANS, errors="coerce")
    tr = tr[(tr.shares > 0) & (tr.price > 0)]
    stats["p_rows"] = len(tr)
    ow = _tsv(z, "REPORTINGOWNER.TSV", ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNER_RELATIONSHIP",
                                        "RPTOWNER_TITLE"])
    ow = ow[ow.ACCESSION_NUMBER.isin(set(tr.ACCESSION_NUMBER))]
    rel = ow.RPTOWNER_RELATIONSHIP.fillna("")
    tit = ow.RPTOWNER_TITLE.fillna("").str.upper()
    ow = ow.assign(is_dir=rel.str.contains("Director").astype(int), is_off=rel.str.contains("Officer").astype(int),
                   is_10pct=rel.str.contains("TenPercentOwner").astype(int),
                   is_ceo=tit.str.contains(RE_CEO).astype(int), is_cfo=tit.str.contains(RE_CFO).astype(int),
                   cik=pd.to_numeric(ow.RPTOWNERCIK, errors="coerce"))
    og = ow.groupby("ACCESSION_NUMBER").agg(owner=("cik", "min"), is_dir=("is_dir", "max"), is_off=("is_off", "max"),
                                            is_10pct=("is_10pct", "max"), is_ceo=("is_ceo", "max"),
                                            is_cfo=("is_cfo", "max"), n_owner_rows=("cik", "size"))
    tr["val"] = tr.shares * tr.price
    tr["tdate"] = pd.to_datetime(tr.TRANS_DATE.str.strip(), format="%d-%b-%Y", errors="coerce")
    ag = tr.groupby("ACCESSION_NUMBER").agg(bought=("shares", "sum"), value=("val", "sum"), post=("post", "max"),
                                            tdate_max=("tdate", "max"), tdate_min=("tdate", "min"))
    ag = ag.join(og, how="left").join(sub.set_index("ACCESSION_NUMBER")[["FILING_DATE", "ISSUERCIK",
                                                                           "ISSUERTRADINGSYMBOL"]])
    ag["fdate"] = pd.to_datetime(ag.FILING_DATE.str.strip(), format="%d-%b-%Y", errors="coerce")
    ag["raw_sym"] = ag.ISSUERTRADINGSYMBOL.fillna("")
    ag["ticker"] = ag.raw_sym.map(clean_sym)
    stats["accessions"] = len(ag)
    stats["blank_or_multi_symbol"] = int((ag.ticker == "").sum())
    stats["joint_filings"] = int((ag.n_owner_rows > 1).sum())
    base = ag.post - ag.bought
    ag["pct"] = np.where(ag.post.notna() & (base > 0), ag.bought / base.where(base > 0, 1), 10.0)
    ag["pct"] = ag.pct.clip(0, 10)
    ag = ag[(ag.ticker != "") & ag.fdate.notna()].reset_index()
    cols = ["ACCESSION_NUMBER", "ticker", "ISSUERCIK", "fdate", "tdate_min", "tdate_max", "owner", "is_dir",
            "is_off", "is_10pct", "is_ceo", "is_cfo", "bought", "value", "post", "pct"]
    TDIR.mkdir(parents=True, exist_ok=True)
    ag[cols].to_csv(TDIR / f"{q}.csv.gz", index=False, compression="gzip")
    return stats


def stage_sec():
    hrefs = quarter_hrefs()
    qs = [q for q in hrefs if q >= "2007q1"]
    allstats = []
    for q in qs:
        if (TDIR / f"{q}.csv.gz").exists():
            continue
        zp = get_zip(q, hrefs[q])
        if zp is None:
            allstats.append({"q": q, "error": "download"}); continue
        st = parse_quarter(q, zp)
        allstats.append(st)
        print(st, flush=True)
    sp = OUT / "sec_parse_stats.json"
    prev = json.loads(sp.read_text()) if sp.exists() else []
    sp.write_text(json.dumps(prev + allstats, indent=1, default=str))


def load_acc():
    fr = [pd.read_csv(f, dtype={"ticker": str, "ISSUERCIK": str}, parse_dates=["fdate", "tdate_min", "tdate_max"])
          for f in sorted(TDIR.glob("*.csv.gz"))]
    a = pd.concat(fr, ignore_index=True).drop_duplicates("ACCESSION_NUMBER")
    return a


# ---------------------------------------------------------------- prices
def stage_prices():
    import yfinance as yf
    acc = load_acc()
    acc = acc[acc.fdate >= "2008-01-01"]
    tickers = sorted(set(acc.ticker))
    PX.mkdir(parents=True, exist_ok=True)
    batches = [["IWM", "SPY"]] + [tickers[i:i + 100] for i in range(0, len(tickers), 100)]
    (OUT / "price_tickers.json").write_text(json.dumps({"n": len(tickers)}))
    fails = []
    fp_f = OUT / "download_fails.json"
    if fp_f.exists():
        fails = json.loads(fp_f.read_text())
    empty_streak = 0
    for bi, b in enumerate(batches):
        fp = PX / f"batch_{bi:03d}.csv.gz"
        if fp.exists():
            continue
        df = None
        for attempt in range(8):
            try:
                df = yf.download(b, start=START, end=END, auto_adjust=False, actions=True, group_by="ticker",
                                 threads=2, progress=False)
            except Exception as e:  # noqa
                print("retry", bi, e, flush=True); time.sleep(60); df = None; continue
            errs = " ".join(str(v) for v in yf.shared._ERRORS.values())
            if "Rate" in errs or "Too Many" in errs:
                print(f"rate limited at batch {bi} — sleep 900s", flush=True)
                df = None; time.sleep(900); continue
            break
        if df is None:
            print("giving up batch", bi, flush=True); continue
        rows = []
        for t in b:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
            except KeyError:
                fails.append(t); continue
            sub = sub.dropna(subset=["Close"])
            if len(sub) == 0:
                fails.append(t); continue
            rows.append(pd.DataFrame({
                "date": sub.index.strftime("%Y-%m-%d"), "ticker": t,
                "open": sub["Open"].astype("float64"), "close": sub["Close"].astype("float64"),
                "adj": sub["Adj Close"].astype("float64"), "volume": sub["Volume"].astype("float64"),
                "split": sub["Stock Splits"].astype("float64") if "Stock Splits" in sub else 0.0}))
        del df
        if rows:
            pd.concat(rows).to_csv(fp, index=False, compression="gzip")
            empty_streak = 0
        else:
            pd.DataFrame(columns=["date", "ticker", "open", "close", "adj", "volume", "split"]).to_csv(
                fp, index=False, compression="gzip")
            empty_streak += 1
        fp_f.write_text(json.dumps(sorted(set(fails))))
        print(f"batch {bi}/{len(batches)} ok={len(rows)} fails_total={len(set(fails))}", flush=True)
        if empty_streak >= 3:
            print("3 empty batches in a row — backing off 300s", flush=True); time.sleep(300)
        time.sleep(15)


# ---------------------------------------------------------------- events
def make_events(acc):
    """issuer × filing date events with cluster/history features (PIT on filing date)."""
    acc = acc.sort_values(["ticker", "fdate"]).reset_index(drop=True)
    acc["wv"] = acc.value  # for vwap
    g = acc.groupby(["ticker", "fdate"])
    ev = g.agg(n_day=("owner", "nunique"), value=("value", "sum"), bought=("bought", "sum"),
               pct_hold=("pct", "max"), is_ceo=("is_ceo", "max"), is_cfo=("is_cfo", "max"),
               is_dir=("is_dir", "max"), is_off=("is_off", "max"), is_10pct=("is_10pct", "max"),
               tdate_max=("tdate_max", "max"), tdate_min=("tdate_min", "min"), n_acc=("owner", "size")).reset_index()
    ev["vwap"] = ev.value / ev.bought
    # cluster counts: distinct owners with fdate in [F-k+1, F]
    n7 = np.zeros(len(ev)); n30 = np.zeros(len(ev)); dsl = np.full(len(ev), np.nan)
    acc_by = {t: d for t, d in acc.groupby("ticker")}
    pos = 0
    for t, e in ev.groupby("ticker", sort=False):
        a = acc_by[t]
        fd = a.fdate.values; ow = a.owner.values
        for i, F in zip(e.index, e.fdate.values):
            lo7 = F - np.timedelta64(6, "D"); lo30 = F - np.timedelta64(29, "D")
            m30 = (fd >= lo30) & (fd <= F)
            n30[i] = len(set(ow[m30]))
            n7[i] = len(set(ow[m30 & (fd >= lo7)]))
            prev = fd[fd < F]
            if len(prev):
                dsl[i] = (F - prev.max()) / np.timedelta64(1, "D")
    ev["n7"] = n7; ev["n30"] = n30; ev["days_since"] = dsl
    return ev


def build():
    acc = load_acc()
    ev = make_events(acc)
    ev = ev[(ev.fdate >= "2008-01-01")].reset_index(drop=True)
    info = {"accessions_total": int(len(acc)), "events_2008plus": int(len(ev)),
            "tickers_2008plus": int(ev.ticker.nunique())}
    # IWM calendar
    frames = {}
    iw = pd.read_csv(PX / "batch_000.csv.gz")
    iw = iw[iw.ticker == "IWM"].sort_values("date")
    cal = pd.DatetimeIndex(pd.to_datetime(iw.date.values))
    T = len(cal)
    iw_oadj = (iw.open * iw.adj / iw.close).values
    calv = cal.values
    out = []
    stat = {"no_price_ticker": 0, "no_price_events": 0, "pricematch_fail": 0, "no_anchor": 0}
    have = set()
    ev_by = {t: d for t, d in ev.groupby("ticker")}
    for f in sorted(PX.glob("batch_*.csv.gz")):
        p = pd.read_csv(f, dtype={"ticker": str})
        if len(p) == 0:
            continue
        for t, s in p.groupby("ticker"):
            if t in ("IWM", "SPY"):
                continue
            have.add(t)
            e_t = ev_by.get(t)
            if e_t is None or len(e_t) == 0:
                continue
            s = s.drop_duplicates("date").set_index(pd.to_datetime(s.drop_duplicates("date").date)).reindex(cal)
            C = s.close.values; A = s.adj.values; O = s.open.values; V = s.volume.values
            sp = np.nan_to_num(s.split.values, nan=0.0); sp[sp <= 0] = 1.0
            fac_after = np.concatenate([np.cumprod(sp[::-1])[::-1][1:], [1.0]])  # prod of splits after i
            raw = C * fac_after
            oadj = O * A / C
            lr = np.diff(np.log(A), prepend=np.nan)
            dvol = C * V
            e_idx = np.searchsorted(calv, e_t.fdate.values.astype("datetime64[ns]"), side="right")
            t_idx = np.searchsorted(calv, e_t.tdate_max.values.astype("datetime64[ns]"), side="right") - 1
            for k, (ri, row) in enumerate(e_t.iterrows()):
                e = e_idx[k]; a = e - 1; ti_ = t_idx[k]
                rec = row.to_dict()
                if e >= T or a < 60 or np.isnan(C[a]):
                    stat["no_anchor"] += 1; continue
                rc = raw[ti_] if 0 <= ti_ < T else np.nan
                if np.isnan(rc) and 0 <= ti_ < T:  # nearest earlier valid
                    vv = np.where(~np.isnan(raw[max(0, ti_ - 5):ti_ + 1]))[0]
                    rc = raw[max(0, ti_ - 5) + vv[-1]] if len(vv) else np.nan
                ratio = row.vwap / rc if rc and not np.isnan(rc) else np.nan
                rec["pm_ratio"] = ratio
                if not (0.67 <= ratio <= 1.5):
                    stat["pricematch_fail"] += 1; continue
                w20 = slice(a - 19, a + 1); w60 = slice(a - 59, a + 1); w252 = slice(max(0, a - 251), a + 1)
                rec.update({
                    "e": e, "entry_date": cal[e], "raw_close_a": raw[a], "open_e": O[e],
                    "dvol20": np.nanmean(dvol[w20]),
                    "ret20": A[a] / A[a - 20] - 1, "ret60": A[a] / A[a - 60] - 1,
                    "vol20": np.nanstd(lr[w20]),
                    "lsize": np.log(max(raw[a] * np.nanmean(V[w60] / fac_after[w60]), 1.0)),
                    "hi52": C[a] / np.nanmax(C[w252]) - 1, "lo52": C[a] / np.nanmin(C[w252]) - 1,
                })
                for H in HS:
                    x = e + H
                    if x < T and oadj[e] > 0 and not np.isnan(oadj[x]):
                        rec[f"g{H}"] = oadj[x] / oadj[e] - 1 - (iw_oadj[x] / iw_oadj[e] - 1)
                    else:
                        rec[f"g{H}"] = np.nan
                    rec[f"x{H}"] = x
                out.append(rec)
        del p
        print("built", f.name, len(out), flush=True)
    tab = pd.DataFrame(out)
    miss = ev[~ev.ticker.isin(have)]
    stat["no_price_ticker"] = int(miss.ticker.nunique()); stat["no_price_events"] = int(len(miss))
    tab["lval"] = np.log1p(tab.value)
    tab["lval_dvol"] = np.log1p(tab.value / tab.dvol20.clip(lower=1) * 100)
    tab["tradable"] = ((tab.raw_close_a >= 3) & (tab.dvol20 >= 1e6) & (tab.open_e > 0)).astype(int)
    tab["year"] = pd.to_datetime(tab.entry_date).dt.year
    tab["week"] = pd.to_datetime(tab.entry_date).dt.to_period("W-FRI").dt.start_time
    info.update(stat)
    # coverage by year (all 2008+ events vs priced vs tradable)
    evy = ev.fdate.dt.year.value_counts().sort_index()
    cov = pd.DataFrame({"events": evy,
                        "tickers": ev.groupby(ev.fdate.dt.year).ticker.nunique(),
                        "priced_pm_ok": tab.groupby(pd.to_datetime(tab.fdate).dt.year).size(),
                        "tradable": tab[tab.tradable == 1].groupby(pd.to_datetime(tab.fdate).dt.year).size()})
    cov["pct_priced"] = cov.priced_pm_ok / cov.events
    cov.to_csv(OUT / "coverage_by_year.csv")
    tab.to_csv(OUT / "events_table.csv.gz", index=False, compression="gzip")
    (OUT / "build_info.json").write_text(json.dumps(info, indent=1, default=str))
    print(info); print(cov.to_string())


# ---------------------------------------------------------------- models
def fit_predict(model, tr, te, y):
    from sklearn.preprocessing import StandardScaler
    Xtr = tr[FEATS].values.astype(np.float32); Xte = te[FEATS].values.astype(np.float32)
    ytr = tr[y].values
    lo, hi = np.nanpercentile(ytr, [1, 99]); yw = np.clip(ytr, lo, hi)
    imp = {}
    if model in ("M1_logit", "M4_mlp"):
        med = np.nanmedian(Xtr, axis=0); med = np.where(np.isnan(med), 0, med)
        Xtr = np.where(np.isnan(Xtr), med, Xtr); Xte = np.where(np.isnan(Xte), med, Xte)
        sc = StandardScaler().fit(Xtr); Xtr = sc.transform(Xtr); Xte = sc.transform(Xte)
    if model == "M1_logit":
        from sklearn.linear_model import LogisticRegression
        m = LogisticRegression(max_iter=2000).fit(Xtr, (ytr - COST > 0).astype(int))
        imp = dict(zip(FEATS, m.coef_[0].tolist()))
        return m.predict_proba(Xte)[:, 1], imp
    if model == "M4_mlp":
        from sklearn.neural_network import MLPRegressor
        mu, sd = yw.mean(), yw.std()
        m = MLPRegressor(hidden_layer_sizes=(32, 16), early_stopping=True, random_state=0, max_iter=200)
        m.fit(Xtr, (yw - mu) / sd)
        return m.predict(Xte), imp
    import lightgbm as lgb
    if model == "M2_lgbm_reg":
        m = lgb.LGBMRegressor(n_jobs=2, random_state=0, verbose=-1).fit(Xtr, yw)
    else:
        trs = tr.assign(_y=ytr).sort_values("week", kind="stable")
        pct = trs.groupby("week")._y.rank(pct=True, method="first").values
        rel = np.clip(np.floor(pct * 5 - 1e-9), 0, 4).astype(int)
        grp = trs.groupby("week", sort=False).size().values
        m = lgb.LGBMRanker(objective="lambdarank", n_jobs=2, random_state=0, verbose=-1)
        m.fit(trs[FEATS].values.astype(np.float32), rel, group=grp)
    imp = dict(zip(FEATS, m.booster_.feature_importance("gain").tolist()))
    return m.predict(Xte), imp


def nw_t(x, lags):
    x = np.asarray(x, float); x = x[~np.isnan(x)]
    n = len(x)
    if n < 5:
        return float("nan")
    u = x - x.mean()
    s = (u @ u) / n
    for L in range(1, lags + 1):
        w = 1 - L / (lags + 1)
        s += 2 * w * (u[L:] @ u[:-L]) / n
    return float(x.mean() / math.sqrt(s / n)) if s > 0 else float("nan")


def half(wk):
    return np.where(pd.to_datetime(wk).dt.year <= 2018, "h1", "h2")


def evaluate(P, H, gcol, lags):
    from scipy.stats import spearmanr
    rows = []; skipped = 0
    for wk, g in P.groupby("week"):
        N = len(g)
        if N < 10:
            skipped += 1; continue
        n = max(3, int(math.floor(0.1 * N)))
        gv = g[gcol].values
        base = g.n7.values >= 2
        rec = {"week": wk, "N": N, "all": gv.mean() - COST,
               "M0_rule": gv[base].mean() - COST if base.any() else np.nan, "M0_rule_n": int(base.sum())}
        rec["M0_rule_top"] = gv[base].tolist()
        for m in MODELS[1:]:
            sc = g[m].values
            order = np.argsort(-sc, kind="stable")
            top = gv[order[:n]]
            rec[m] = top.mean() - COST
            rec[m + "_top"] = top.tolist()
            rec[m + "_ic"] = spearmanr(sc, gv).correlation if np.std(sc) > 0 else np.nan
        rec["M0_rule_ic"] = spearmanr(g.n7.values, gv).correlation if np.std(g.n7.values) > 0 else np.nan
        rows.append(rec)
    Wk = pd.DataFrame(rows)
    Wk["half"] = half(Wk.week)
    out = {"_weeks_eval": len(Wk), "_weeks_skipped_N_lt_10": skipped,
           "_weeks_with_rule_events": int(Wk.M0_rule.notna().sum())}
    for m in MODELS:
        tops = np.concatenate([np.array(t, float) for t in Wk[m + "_top"]])
        res = {"events_selected": int(len(tops)),
               "mean_net_event": float(tops.mean() - COST), "median_net_event": float(np.median(tops) - COST),
               "weekly_mean_net": float(Wk[m].mean()), "t_net": nw_t(Wk[m], lags),
               "h1_net": float(Wk.loc[Wk.half == "h1", m].mean()), "h2_net": float(Wk.loc[Wk.half == "h2", m].mean()),
               "diff_vs_all": float((Wk[m] - Wk["all"]).mean()), "t_diff_all": nw_t(Wk[m] - Wk["all"], lags),
               "ic_mean": float(Wk[m + "_ic"].mean()), "ic_t": nw_t(Wk[m + "_ic"], lags),
               "breakeven_cost": float(tops.mean())}
        if m == "M0_rule":
            res["pass"] = bool(res["t_net"] >= 3 and res["h1_net"] > 0 and res["h2_net"] > 0
                               and res["breakeven_cost"] >= 2 * COST)
            res["grade_candidate"] = bool(res["t_net"] >= 2 and res["h1_net"] > 0 and res["h2_net"] > 0
                                          and res["breakeven_cost"] >= 2 * COST)
        else:
            d = (Wk[m] - Wk["M0_rule"])  # NaN on weeks without rule events
            res.update({"diff_vs_base": float(d.mean()), "t_diff_base": nw_t(d.dropna(), lags),
                        "h1_diff": float(d[Wk.half == "h1"].mean()), "h2_diff": float(d[Wk.half == "h2"].mean())})
            cond = (res["h1_net"] > 0 and res["h2_net"] > 0 and res["h1_diff"] > 0 and res["h2_diff"] > 0
                    and res["breakeven_cost"] >= 2 * COST)
            res["pass"] = bool(res["t_diff_base"] >= 3 and cond)
            res["grade_candidate"] = bool(res["t_diff_base"] >= 2 and cond)
        out[m] = res
    out["_all"] = {"weekly_mean_net": float(Wk["all"].mean()), "t_net": nw_t(Wk["all"], lags),
                   "h1": float(Wk.loc[Wk.half == "h1", "all"].mean()),
                   "h2": float(Wk.loc[Wk.half == "h2", "all"].mean()), "events": int(Wk.N.sum())}
    return out, Wk


def diag_untradable(tab, H, lags):
    """비계수 진단: 트레이더빌리티 필터 없이 M0 규칙 vs IWM (주간, N 스킵 없음)."""
    g = f"g{H}"
    d = tab[(tab.year >= 2012) & tab[g].notna() & (tab.n7 >= 2)]
    wk = d.groupby("week")[g].mean() - COST
    h = half(pd.Series(wk.index))
    allw = tab[(tab.year >= 2012) & tab[g].notna()].groupby("week")[g].mean() - COST
    return {"events": int(len(d)), "weeks": int(len(wk)), "weekly_mean_net": float(wk.mean()),
            "t_net": nw_t(wk.values, lags), "h1": float(wk.values[h == "h1"].mean()),
            "h2": float(wk.values[h == "h2"].mean()), "mean_net_event": float(d[g].mean() - COST),
            "median_net_event": float(d[g].median() - COST),
            "all_events_weekly_mean_net": float(allw.mean()), "all_t": nw_t(allw.values, lags)}


def run(years=None):
    tab = pd.read_csv(OUT / "events_table.csv.gz", parse_dates=["entry_date", "week", "fdate"])
    import pandas.api.types as pt  # noqa
    cal0 = pd.read_csv(PX / "batch_000.csv.gz")
    cal = pd.DatetimeIndex(pd.to_datetime(sorted(cal0[cal0.ticker == "IWM"].date.unique())))
    first_idx = {y: int(np.searchsorted(cal.values, np.datetime64(f"{y}-01-01"))) for y in range(2012, 2027)}
    results = {}; imps = {}; diags = {}
    trd = tab[tab.tradable == 1].reset_index(drop=True)
    test_years = years or list(range(2012, 2027))
    for H in HS:
        gcol, xcol = f"g{H}", f"x{H}"
        lags = int(math.ceil(H / 5))
        diags[f"H{H}"] = diag_untradable(tab, H, lags)
        d = trd[trd[gcol].notna()]
        preds = []
        for Y in test_years:
            tr = d[(d.year < Y) & (d[xcol] < first_idx[Y])]
            te = d[d.year == Y]
            if len(te) == 0:
                continue
            p = te[["week", "year", "n7", gcol]].copy()
            for m in MODELS[1:]:
                s, imp = fit_predict(m, tr, te, gcol)
                p[m] = s
                if imp:
                    imps.setdefault(f"H{H}_{m}", []).append(imp)
            preds.append(p)
            print(H, Y, len(tr), len(te), flush=True)
        P = pd.concat(preds)
        P.to_csv(OUT / f"preds_H{H}.csv.gz", index=False, compression="gzip")
        res, Wk = evaluate(P, H, gcol, lags)
        results[f"H{H}"] = res
        Wk.drop(columns=[c for c in Wk.columns if c.endswith("_top")]).to_csv(OUT / f"weekly_H{H}.csv", index=False)
    imp_avg = {k: pd.DataFrame(v).mean().sort_values(ascending=False).to_dict() for k, v in imps.items()}
    (OUT / "results.json").write_text(json.dumps({"results": results, "diag_untradable": diags,
                                                  "importance": imp_avg}, default=str, indent=1))
    for k, v in results.items():
        print(k, {kk: vv for kk, vv in v.items() if kk.startswith("_")})
        print(pd.DataFrame({m: v[m] for m in MODELS}).T.round(4).to_string())
    print(json.dumps(diags, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sec", action="store_true")
    ap.add_argument("--prices", action="store_true")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--years", type=int, nargs="*")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.sec:
        stage_sec()
    if a.prices:
        stage_prices()
    if a.build:
        build()
    if a.run:
        run(a.years)
