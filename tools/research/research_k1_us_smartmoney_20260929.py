"""K1 US 이식 검정 — 스마트머니 지분 확대(13D/13G/내부자 군집/13F 합의) + 바닥권 → 중기 보유 (2026-09-29).

연구 전용. 라이브 코드·config·.env 미접촉. 산출물은 data/analysis/k1_us_20260929/ 와 보고서뿐.

================================ 사전등록 (결과 계산 전 고정) ================================
데이터
- SEC EDGAR full-index form.gz 2010Q1~2026Q3. 양식: SC 13D, SC 13D/A, SC 13G, SC 13G/A 와
  2024-12 이후 신양식 SCHEDULE 13D, SCHEDULE 13D/A, SCHEDULE 13G, SCHEDULE 13G/A (동일 취급).
- 13D 원본·13D/A(범위 제한)는 제출 원문 앞 96KB를 받아 SEC-HEADER의 SUBJECT COMPANY / FILED BY CIK와
  표지 Row 13(Percent of class) 최대값을 파싱한다. 13G는 헤더 미수집: 같은 accession 행들 중 현재 티커로
  매핑되는 CIK가 정확히 1개일 때 그것을 대상회사로 본다(0개·2개 이상은 제외). 이 휴리스틱의 정확도는
  13D 헤더 정답과 대조해 보고한다.
- CIK→티커: sec.gov/files/company_tickers.json (현재 상장만 → 생존편향. 연도별 매핑률 보고).
  인수합병으로 사라진 13D 대상(행동주의 → 매각 프리미엄)은 하방, 파산 소멸은 상방 편향 → 순부호 미상.
- 가격: T3 yfinance 캐시(auto_adjust=False, adj 칼럼) 재사용 + 부족분 yfinance 동일 형식 수집.
  수정 시가 = open × adj/close.
진입·보유
- 신호일 = EDGAR Date Filed. 진입 = 신호일 다음 거래일 시가(항상 — 16:00~17:30 접수분 포함, 룩어헤드 없음).
- 청산 = 진입일 포함 H번째 거래일 종가. H ∈ {20, 60, 120}. 전체 H 구간 가격이 없는 이벤트는 제외(절단 안 함).
- 필터(신호일 종가 기준): 원가격 ≥ $3, 20일 평균 거래대금 ≥ $1M (보조: ≥ $5M).
- 비용: 왕복 0.44% (KIS 실측).
규칙
- U1: 원본 SC 13D, 같은 대상회사에 직전 365일 원본 13D 없음(첫 신고). 같은 날 여러 건은 1건.
- U2: U1 ∩ 바닥권(신호일 종가의 252일 수정종가 범위 내 위치 ≤ 0.35, 관측 ≥ 200일).
- U3: 13D/A 중 같은 FILED BY가 같은 대상에 낸 직전 신고(원본 또는 /A, 2년 이내) 대비 지분율 +1.0%p 이상.
  수집 범위: 대상·신고인 CIK 쌍이 U1 모집단 원본 13D(2년 이내)와 일치하는 /A만(idx 행 CIK 집합으로 선별).
  [이탈 기록, U3 결과 보기 전] 33,023건 → 대상 CIK가 현재 티커로 매핑되는 것만 15,326건으로 축소
  (SEC 레이트리밋·타 작업 공유 IP. 매핑 안 되는 대상은 가격이 없어 어차피 평가 불가).
- U4: 원본 SC 13G (직전 365일 같은 대상 원본 13G 없음) — 대조군(패시브 5%, 인덱스 펀드 다수).
- U5: 내부자 군집(T3 거래 캐시: 30일 내 서로 다른 매수자 ≥ 3, 3번째 도달 filing일) ∩ 바닥권.
  T3 가격 일치 필터(매수가/원가격 ∈ [0.67,1.5]) 동일 적용. T3 소유자 ID 한계(동일 계열 별도 accession) 승계.
- U6(13F 가능 시): 고정 가치 운용사 목록의 13F-HR 신규 편입(직전 분기 미보유)이 같은 분기에 ≥ 2곳 →
  두 번째 운용사 13F 제출일 다음 시가 진입. CUSIP→티커 매핑률 < 70%면 사유 기록 후 생략.
  운용사(이름으로 고정, CIK는 EDGAR 조회로 확정·기록, 조회 실패는 기록 후 제외 — 대체 추가 없음):
  Berkshire Hathaway, Baupost Group, Greenlight Capital, Southeastern Asset Management,
  Harris Associates, Tweedy Browne, First Pacific Advisors(FPA), Third Avenue Management,
  Pzena Investment Management, Davis Selected Advisers, ValueAct, Fairholme Capital, Appaloosa.
- 모든 규칙: 같은 티커 이벤트는 60거래일 내 첫 건만.
벤치마크
- IWM, SPY 같은 창(다음 시가→H번째 종가, 수정).
- 규모매칭: 신호일 기준 트레이더블(≥$3·≥$1M) 가격 유니버스(T3 캐시 ∪ 본 수집분 ≈ '내부자 매수 이력 종목'
  + 13D/13G 대상 — 중립 유니버스 아님)를 dvol20 10분위로 나눠, 이벤트와 같은 분위의 같은 창 평균 수익.
통계
- 이벤트 net 초과 = ret − 0.44% − bench. 일별 코호트(진입일 평균) 시계열 → 평균, Newey-West t(lag=H 관측),
  독립 날짜 수, 날짜 클러스터 t, 연도별 부호표, 중앙값, 상위 10% 이벤트의 합계 기여 비중.
- 표본 분할: 인샘플 ≤ 2019, OOS ≥ 2020 (진입일 연도).
- 1차 판정 칸: H=60 × 규모매칭 × OOS. 합격(확정) = OOS net > 0, NW t ≥ 2.64, 전체 연도 부호 과반 양수.
  후보 = OOS net > 0 & t ≥ 1.65 & 인샘플도 양수. 그 외 기각. H20/H120, IWM/SPY는 보조.
Jev(선택) — U1 또는 U2의 OOS net(H60 규모매칭)이 양수일 때만, 예산 ≤ US$2: 13D Item 4 원자 질문 분할.
==========================================================================================
실행: python research_k1_us_smartmoney_20260929.py <stage> [...]
  stages: idx | hdr13d | hdr13da | g13 | prices | u5 | u6 | eval | all
"""
from __future__ import annotations

import gzip
import io
import json
import re
import sys
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "analysis" / "k1_us_20260929"
IDX = OUT / "idx"
HDR = OUT / "hdr"
PX = OUT / "prices"
T3 = ROOT / "data" / "analysis" / "model_T3_form4_20260927"
UA = "claudetrade research smyang03@gmail.com"  # 저장소 기존 SEC 접근 정책용 UA 재사용(T3)
FORMS = ["SCHEDULE 13D/A", "SCHEDULE 13G/A", "SCHEDULE 13D", "SCHEDULE 13G",
         "SC 13D/A", "SC 13G/A", "SC 13D", "SC 13G"]
FNORM = {"SCHEDULE 13D/A": "DA", "SC 13D/A": "DA", "SCHEDULE 13D": "D", "SC 13D": "D",
         "SCHEDULE 13G/A": "GA", "SC 13G/A": "GA", "SCHEDULE 13G": "G", "SC 13G": "G"}
COST = 0.0044
HS = (20, 60, 120)
IS_END = 2019
for d in (OUT, IDX, HDR, PX):
    d.mkdir(parents=True, exist_ok=True)


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    with open(OUT / "run.log", "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + s + "\n")


# ------------------------------------------------------------------ SEC fetch (rate-limited)
_lock = threading.Lock()
_last = [0.0]
RATE = float(__import__("os").environ.get("K1_RATE", "5"))


def _throttle():
    with _lock:
        now = time.time()
        wait = _last[0] + 1.0 / RATE - now
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()


def sec_get(url, max_bytes=None, tries=10):
    for k in range(tries):
        _throttle()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read(max_bytes) if max_bytes else r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (500, 502, 503, 504):
                time.sleep(5 * (k + 1))
                continue
            if e.code in (403, 429):
                log(f"SEC {e.code} — backoff {min(600, 120 * (k + 1))}s")
                with _lock:
                    time.sleep(min(600, 120 * (k + 1)))
                continue
            time.sleep(3)
        except Exception:  # noqa
            time.sleep(3 * (k + 1))
    return None


# ------------------------------------------------------------------ stage idx
LINE_RE = re.compile(r"^\s*(.*?)\s+(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(edgar/\S+)\s*$")


def quarters():
    for y in range(2010, 2027):
        for q in range(1, 5):
            if (y, q) > (2026, 3):
                return
            yield y, q


def stage_idx():
    rows = []
    for y, q in quarters():
        fp = IDX / f"{y}Q{q}.gz"
        if not fp.exists():
            b = sec_get(f"https://www.sec.gov/Archives/edgar/full-index/{y}/QTR{q}/form.gz")
            if not b:
                log("idx fail", y, q)
                continue
            fp.write_bytes(b)
        txt = gzip.decompress(fp.read_bytes()).decode("latin-1")
        n = 0
        for line in txt.splitlines():
            for f in FORMS:
                if line.startswith(f + " "):
                    m = LINE_RE.match(line[len(f):])
                    if m:
                        name, cik, dt, fn = m.groups()
                        acc = fn.rsplit("/", 1)[-1].replace(".txt", "")
                        rows.append((FNORM[f], f, name.strip(), int(cik), dt, fn, acc))
                        n += 1
                    break
        log(f"idx {y}Q{q} rows={n}")
    df = pd.DataFrame(rows, columns=["form", "form_raw", "name", "cik", "date", "file", "acc"])
    df.to_csv(OUT / "idx_13dg.csv.gz", index=False, compression="gzip")
    g = df.drop_duplicates("acc").assign(year=lambda d: d.date.str[:4]).groupby(["year", "form"]).size().unstack()
    log("accessions by year/form\n" + g.to_string())


def load_idx():
    return pd.read_csv(OUT / "idx_13dg.csv.gz")


# ------------------------------------------------------------------ tickers
def ticker_map():
    fp = OUT / "company_tickers.json"
    if not fp.exists():
        fp.write_bytes(sec_get("https://www.sec.gov/files/company_tickers.json"))
    j = json.loads(fp.read_text())
    m = {}
    for v in j.values():  # 첫 항목 = 주 종목
        m.setdefault(int(v["cik_str"]), v["ticker"].upper().replace(".", "-"))
    return m


# ------------------------------------------------------------------ header / cover parse
TAG_RE = re.compile(r"<[^>]+>")
PCT_RE = re.compile(r"PERCENT\s+OF\s+CLASS\s+REPRESENTED\s+BY\s+AMOUNT\s+IN\s+ROW", re.I)
NUM_RE = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")
XPCT_RE = re.compile(r"<(?:\w+:)?percentOfClass>\s*([\d.]+)\s*<", re.I)


def parse_doc(raw: bytes):
    t = raw.decode("latin-1", "ignore")
    out = {}
    hdr = t.split("</SEC-HEADER>")[0] if "</SEC-HEADER>" in t else t[:8000]
    m = re.search(r"ACCEPTANCE-DATETIME>\s*(\d{14})", hdr)
    out["accept"] = m.group(1) if m else None
    for key, lab in (("subj", "SUBJECT COMPANY:"), ("filer", "FILED BY:")):
        i = hdr.find(lab)
        if i >= 0:
            m = re.search(r"CENTRAL INDEX KEY:\s*(\d+)", hdr[i:i + 1500])
            out[key] = int(m.group(1)) if m else None
        else:
            out[key] = None
    pcts = [float(x) for x in XPCT_RE.findall(t)]
    if not pcts:
        body = TAG_RE.sub(" ", t).replace("&nbsp;", " ").replace("&#160;", " ")
        body = re.sub(r"\s+", " ", body)
        for mm in PCT_RE.finditer(body):
            w = body[mm.end(): mm.end() + 400]
            n = NUM_RE.search(w)
            if n:
                v = float(n.group(1))
                if v <= 100:
                    pcts.append(v)
    out["pct"] = max(pcts) if pcts else None
    return out


def fetch_hdrs(sub: pd.DataFrame, tag: str):
    accs = sub.drop_duplicates("acc")[["acc", "file"]].values.tolist()
    def _need(a):
        fp = HDR / f"{a}.json"
        return (not fp.exists()) or ("err" in fp.read_text())
    todo = [(a, f) for a, f in accs if _need(a)]
    log(f"{tag}: {len(accs)} accessions, todo {len(todo)}")
    done = [0]

    def one(af):
        a, f = af
        raw = sec_get("https://www.sec.gov/Archives/" + f, max_bytes=96 * 1024)
        rec = parse_doc(raw) if raw else {"err": "fetch"}
        (HDR / f"{a}.json").write_text(json.dumps(rec))
        done[0] += 1
        if done[0] % 1000 == 0:
            log(f"{tag}: {done[0]}/{len(todo)}")

    with ThreadPoolExecutor(int(__import__("os").environ.get("K1_THREADS", "6"))) as ex:
        list(ex.map(one, todo))
    log(f"{tag}: done")


def hdr_table(accs):
    rows = []
    for a in accs:
        fp = HDR / f"{a}.json"
        if fp.exists():
            r = json.loads(fp.read_text())
            r["acc"] = a
            rows.append(r)
    return pd.DataFrame(rows)


def stage_hdr13d(years=None):
    df = load_idx()
    d = df[df.form == "D"]
    if years:
        d = d[d.date.str[:4].isin(years)]
    fetch_hdrs(d, "13D")


def u1_pairs():
    """원본 13D (subject, filer, date) from headers."""
    df = load_idx()
    d = df[df.form == "D"].drop_duplicates("acc")
    h = hdr_table(d.acc)
    h = h.merge(d[["acc", "date"]], on="acc")
    return h.dropna(subset=["subj", "filer"])


def stage_hdr13da():
    df = load_idx()
    pairs = u1_pairs()
    pairs["date"] = pd.to_datetime(pairs.date)
    da = df[df.form == "DA"].copy()
    da["date"] = pd.to_datetime(da.date)
    cs = da.groupby("acc").agg(ciks=("cik", lambda s: frozenset(s)), date=("date", "first"),
                               file=("file", "first")).reset_index()
    pk = {}
    for s, f, dt in pairs[["subj", "filer", "date"]].itertuples(index=False):
        pk.setdefault((int(s), int(f)), []).append(dt)
    keep = []
    tm = ticker_map()  # 수집 전 범위 축소: 대상 CIK가 현재 티커로 매핑되는 쌍만(가격 없는 대상은 어차피 평가 불가)
    for a, ciks, dt, f in cs.itertuples(index=False):
        ok = False
        for s in ciks:
            for fl in ciks:
                if s != fl and s in tm and (s, fl) in pk and any(0 <= (dt - d0).days <= 730 for d0 in pk[(s, fl)]):
                    ok = True
                    break
            if ok:
                break
        if ok:
            keep.append(a)
    log(f"13D/A scoped: {len(keep)} of {len(cs)}")
    fetch_hdrs(da[da.acc.isin(keep)], "13DA")


# ------------------------------------------------------------------ events
def ev_13d(tmap):
    p = u1_pairs()
    p["date"] = pd.to_datetime(p.date)
    p = p.sort_values("date")
    last = {}
    ev = []
    seen = {}  # filer -> 이전 날짜에 신고한 대상 집합 (PIT)
    for dt, g in p.groupby("date", sort=True):
        for r in g.itertuples(index=False):
            s = int(r.subj)
            prev = last.get(s)
            last[s] = r.date
            if prev is not None and (r.date - prev).days <= 365:
                continue
            ev.append((s, r.date, r.pct, r.acc, int(r.filer), len(seen.get(int(r.filer), ()))))
        for r in g.itertuples(index=False):
            seen.setdefault(int(r.filer), set()).add(int(r.subj))
    e = pd.DataFrame(ev, columns=["cik", "date", "pct", "acc", "filer", "filer_prior"]).drop_duplicates(["cik", "date"])
    e["ticker"] = e.cik.map(tmap)
    return e


def ev_13da(tmap):
    df = load_idx()
    d = df[df.form.isin(["D", "DA"])].drop_duplicates("acc")
    h = hdr_table(d.acc).merge(d[["acc", "date", "form"]], on="acc")
    h = h.dropna(subset=["subj", "filer"])
    h["date"] = pd.to_datetime(h.date)
    h = h.sort_values(["subj", "filer", "date"])
    ev = []
    stats = {"amend": 0, "amend_pct": 0, "prev_pct": 0}
    for (s, f), g in h.groupby(["subj", "filer"]):
        prev_pct, prev_dt = None, None
        for r in g.itertuples(index=False):
            if r.form == "DA":
                stats["amend"] += 1
                if r.pct == r.pct and r.pct is not None:
                    stats["amend_pct"] += 1
                    if prev_pct is not None and (r.date - prev_dt).days <= 730:
                        stats["prev_pct"] += 1
                        if r.pct - prev_pct >= 1.0:
                            ev.append((int(s), r.date, r.pct, r.pct - prev_pct, r.acc))
            if r.pct == r.pct and r.pct is not None:
                prev_pct, prev_dt = r.pct, r.date
    e = pd.DataFrame(ev, columns=["cik", "date", "pct", "dpct", "acc"]).drop_duplicates(["cik", "date"])
    e["ticker"] = e.cik.map(tmap)
    return e, stats


def g13_subjects(tmap, form="G"):
    df = load_idx()
    g = df[df.form == form]
    rows = []
    for a, grp in g.groupby("acc"):
        ciks = set(grp.cik)
        mapped = [c for c in ciks if c in tmap]
        if len(mapped) == 1:
            rows.append((mapped[0], grp.date.iloc[0], a))
    return pd.DataFrame(rows, columns=["cik", "date", "acc"])


def heuristic_check(tmap):
    """13G 휴리스틱을 13D 헤더 정답과 대조."""
    df = load_idx()
    d = df[df.form == "D"]
    h = hdr_table(d.acc.unique()).dropna(subset=["subj"])
    truth = dict(zip(h.acc, h.subj.astype(int)))
    ok = tot = amb = 0
    for a, grp in d[d.acc.isin(truth)].groupby("acc"):
        mapped = [c for c in set(grp.cik) if c in tmap]
        if len(mapped) == 1:
            tot += 1
            ok += int(mapped[0] == truth[a])
        else:
            amb += 1
    return {"single_mapped": tot, "agree": ok, "agree_pct": round(100 * ok / max(tot, 1), 2), "dropped": amb}


def ev_13g(tmap):
    g = g13_subjects(tmap, "G")
    g["date"] = pd.to_datetime(g.date)
    g = g.sort_values("date")
    last, ev = {}, []
    for r in g.itertuples(index=False):
        prev = last.get(r.cik)
        last[r.cik] = r.date
        if prev is not None and (r.date - prev).days <= 365:
            continue
        ev.append((r.cik, r.date, r.acc))
    e = pd.DataFrame(ev, columns=["cik", "date", "acc"]).drop_duplicates(["cik", "date"])
    e["ticker"] = e.cik.map(tmap)
    return e


def ev_u5():
    fr = [pd.read_csv(f, dtype={"ticker": str}, parse_dates=["fdate"]) for f in sorted((T3 / "trans").glob("*.csv.gz"))]
    a = pd.concat(fr, ignore_index=True).drop_duplicates("ACCESSION_NUMBER")
    a = a[a.fdate >= "2010-01-01"].sort_values(["ticker", "fdate"])
    a["px"] = a.value / a.bought
    ev = []
    for t, g in a.groupby("ticker"):
        fd = g.fdate.values
        ow = g.owner.values
        last_sig = None
        for i in range(len(g)):
            F = fd[i]
            m = (fd >= F - np.timedelta64(29, "D")) & (fd <= F)
            if len(set(ow[m])) >= 3:
                if last_sig is None or (F - last_sig) / np.timedelta64(1, "D") > 90:
                    vw = g.value.values[m].sum() / max(g.bought.values[m].sum(), 1e-9)
                    ev.append((t, pd.Timestamp(F), vw))
                last_sig = F
    return pd.DataFrame(ev, columns=["ticker", "date", "vwap"]).drop_duplicates(["ticker", "date"])


# ------------------------------------------------------------------ U6 13F
MANAGERS = {"Appaloosa": [1006438, 1656456], "Baupost": [1061768], "Berkshire": [1067983],
            "Davis": [1036325], "Fairholme": [1056831], "FPA": [1377581], "Greenlight": [1079114],
            "Harris": [813917], "Pzena": [1027796], "Southeastern": [807985], "ThirdAvenue": [1099281],
            "Tweedy": [732905], "ValueAct": [1418814, 1395267]}
F13 = OUT / "f13"


def stage_u6():
    import os
    global RATE
    RATE = float(os.environ.get("K1_RATE", "2"))
    F13.mkdir(exist_ok=True)
    rows = []
    for f in sorted(IDX.glob("*.gz")):
        for line in gzip.decompress(f.read_bytes()).decode("latin-1").splitlines():
            if line.startswith("13F-HR "):
                m = LINE_RE.match(line[7:])
                if m and int(m.group(2)) in {c for v in MANAGERS.values() for c in v}:
                    rows.append((int(m.group(2)), m.group(3), m.group(4)))
    fl = pd.DataFrame(rows, columns=["cik", "date", "file"]).drop_duplicates("file")
    fl = fl[fl.date >= "2013-07-01"]
    inv = {c: k for k, v in MANAGERS.items() for c in v}
    fl["mgr"] = fl.cik.map(inv)
    log(f"U6: 13F-HR filings {len(fl)}")
    hold = []
    for r in fl.itertuples(index=False):
        acc = r.file.rsplit("/", 1)[-1].replace(".txt", "")
        cp = F13 / f"{acc}.csv"
        if not cp.exists():
            base = f"https://www.sec.gov/Archives/edgar/data/{r.cik}/{acc.replace('-', '')}/"
            j = sec_get(base + "index.json")
            xmlname = None
            if j:
                items = json.loads(j)["directory"]["item"]
                xs = [i["name"] for i in items if i["name"].lower().endswith(".xml") and i["name"].lower() != "primary_doc.xml"]
                xmlname = xs[0] if xs else None
            recs = []
            if xmlname:
                x = sec_get(base + xmlname)
                if x:
                    t = x.decode("utf-8", "ignore")
                    for blk in re.findall(r"<(?:\w+:)?infoTable>(.*?)</(?:\w+:)?infoTable>", t, re.S):
                        cu = re.search(r"<(?:\w+:)?cusip>\s*(\w+)", blk)
                        nm = re.search(r"<(?:\w+:)?nameOfIssuer>\s*([^<]*)", blk)
                        pc = re.search(r"<(?:\w+:)?putCall>\s*(\w+)", blk)
                        if cu and not pc:
                            recs.append((cu.group(1).upper(), (nm.group(1) if nm else "").strip()))
            if not recs:
                log(f"U6: no holdings parsed {acc} (index={bool(j)}, xml={xmlname})")
                continue
            pd.DataFrame(recs, columns=["cusip", "name"]).drop_duplicates("cusip").to_csv(cp, index=False)
        h = pd.read_csv(cp, dtype=str)
        h["mgr"], h["date"], h["acc"] = r.mgr, r.date, acc
        hold.append(h)
    H = pd.concat(hold, ignore_index=True)
    H["date"] = pd.to_datetime(H.date)
    # 운용사별 제출 순서 → 직전 제출 대비 신규 CUSIP. 분기 = 제출일 직전 분기말.
    H["q"] = (H.date - pd.offsets.QuarterEnd(1)).dt.to_period("Q")
    new = []
    for m, g in H.groupby("mgr"):
        filings = g.drop_duplicates("acc").sort_values("date")
        # 같은 분기 복수 제출(계열 CIK 2개)은 합친다
        prev = None
        for q, gq in g.groupby("q", sort=True):
            cur = set(gq.cusip)
            if prev is not None and cur:
                for c in cur - prev:
                    new.append((m, q, c, gq.date.max(), gq[gq.cusip == c].name.iloc[0]))
            if cur:
                prev = cur
    N = pd.DataFrame(new, columns=["mgr", "q", "cusip", "date", "name"])
    cons = []
    for (q, c), g in N.groupby(["q", "cusip"]):
        if g.mgr.nunique() >= 2:
            d2 = g.sort_values("date").drop_duplicates("mgr").date.iloc[1]
            cons.append((str(q), c, d2, g.name.iloc[0], g.mgr.nunique()))
    C = pd.DataFrame(cons, columns=["q", "cusip", "date", "name", "n_mgr"])
    log(f"U6: new positions {len(N)}, consensus events {len(C)}, cusips {C.cusip.nunique()}")
    # OpenFIGI CUSIP→ticker (무키 25req/min, 10건/req)
    mp = OUT / "figi_map.json"
    fmap = json.loads(mp.read_text()) if mp.exists() else {}
    todo = [c for c in C.cusip.unique() if c not in fmap]
    for i in range(0, len(todo), 10):
        b = todo[i:i + 10]
        body = json.dumps([{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in b]).encode()
        for k in range(5):
            try:
                req = urllib.request.Request("https://api.openfigi.com/v3/mapping", data=body,
                                             headers={"Content-Type": "application/json"})
                res = json.loads(urllib.request.urlopen(req, timeout=30).read())
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    time.sleep(30); continue
                res = None; break
            except Exception:  # noqa
                time.sleep(5); res = None
        if res is None:
            continue
        for c, r in zip(b, res):
            d = (r.get("data") or [{}])[0]
            fmap[c] = {"ticker": d.get("ticker"), "name": d.get("name"), "type": d.get("securityType2")}
        mp.write_text(json.dumps(fmap))
        time.sleep(2.6)
    C["ticker"] = C.cusip.map(lambda c: (fmap.get(c) or {}).get("ticker"))
    C["sectype"] = C.cusip.map(lambda c: (fmap.get(c) or {}).get("type"))
    C["ticker"] = C.ticker.str.upper().str.replace("/", "-", regex=False).str.replace(".", "-", regex=False)
    cov = C.ticker.notna().mean()
    log(f"U6: FIGI mapping coverage {cov:.1%}")
    C.to_csv(OUT / "u6_consensus_all.csv", index=False)
    if cov >= 0.70:
        C[C.ticker.notna() & (C.sectype.fillna("Common Stock") == "Common Stock")].to_csv(OUT / "u6_events.csv", index=False)
    else:
        log("U6: mapping < 70% — skipped per pre-registration")


# ------------------------------------------------------------------ prices
def t3_tickers():
    import glob
    fp = OUT / "t3_ticker_list.json"
    if fp.exists():
        return set(json.loads(fp.read_text()))
    s = set()
    for f in sorted(glob.glob(str(T3 / "prices" / "*.csv.gz"))):
        s |= set(pd.read_csv(f, usecols=["ticker"]).ticker.astype(str))
    fp.write_text(json.dumps(sorted(s)))
    return s


def stage_prices(extra):
    import yfinance as yf
    have = t3_tickers()
    fails = set(json.loads((T3 / "download_fails.json").read_text()))
    ownf = OUT / "download_fails.json"
    if ownf.exists():
        fails |= set(json.loads(ownf.read_text()))
    got = set()
    for f in PX.glob("*.csv.gz"):
        got |= set(pd.read_csv(f, usecols=["ticker"]).ticker.astype(str))
    need = sorted(t for t in set(extra) if isinstance(t, str) and t not in have and t not in fails and t not in got)
    log(f"prices: need {len(need)} new tickers")
    myfails = set(json.loads(ownf.read_text())) if ownf.exists() else set()
    for bi in range(0, len(need), 100):
        b = need[bi:bi + 100]
        fp = PX / f"batch_{int(time.time())}_{bi:05d}.csv.gz"
        df = None
        for attempt in range(6):
            try:
                df = yf.download(b, start="2009-01-01", end="2026-09-28", auto_adjust=False, actions=True,
                                 group_by="ticker", threads=2, progress=False)
            except Exception as e:  # noqa
                log("retry", e); time.sleep(60); continue
            errs = " ".join(str(v) for v in yf.shared._ERRORS.values())
            if "Rate" in errs or "Too Many" in errs:
                log("rate limited, sleep 600"); df = None; time.sleep(600); continue
            break
        if df is None:
            continue
        rows = []
        for t in b:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
            except KeyError:
                myfails.add(t); continue
            sub = sub.dropna(subset=["Close"])
            if len(sub) == 0:
                myfails.add(t); continue
            rows.append(pd.DataFrame({"date": sub.index.strftime("%Y-%m-%d"), "ticker": t,
                                      "open": sub["Open"].astype(float), "close": sub["Close"].astype(float),
                                      "adj": sub["Adj Close"].astype(float), "volume": sub["Volume"].astype(float)}))
        if rows:
            pd.concat(rows).to_csv(fp, index=False, compression="gzip")
        ownf.write_text(json.dumps(sorted(myfails)))
        log(f"prices batch {bi // 100 + 1}/{(len(need) + 99) // 100} ok={len(rows)}")
        time.sleep(8)


def build_matrices():
    fp = OUT / "px_mats.npz"
    if fp.exists():
        z = np.load(fp, allow_pickle=True)
        return {k: z[k] for k in z.files}
    files = sorted((T3 / "prices").glob("*.csv.gz")) + sorted(PX.glob("*.csv.gz"))
    parts = []
    for f in files:
        d = pd.read_csv(f, usecols=["date", "ticker", "open", "close", "adj", "volume"])
        d = d[d.date >= "2009-01-01"]
        parts.append(d)
    d = pd.concat(parts, ignore_index=True).drop_duplicates(["ticker", "date"], keep="last")
    d = d[(d.close > 0) & (d.adj > 0) & (d.open > 0)]
    cal = np.array(sorted(d[d.ticker == "IWM"].date.unique()))
    tick = np.array(sorted(d.ticker.astype(str).unique()))
    di = pd.Index(cal).get_indexer(d.date)
    ti = pd.Index(tick).get_indexer(d.ticker.astype(str))
    ok = di >= 0
    di, ti, d = di[ok], ti[ok], d[ok]
    shp = (len(cal), len(tick))
    mats = {}
    f_ = d.adj.values / d.close.values
    for k, v in (("aopen", d.open.values * f_), ("aclose", d.adj.values), ("close", d.close.values),
                 ("dv", d.close.values * d.volume.values)):
        m = np.full(shp, np.nan, dtype=np.float32)
        m[di, ti] = v
        mats[k] = m
    dvs = pd.DataFrame(mats["dv"]).rolling(20, min_periods=15).mean().values.astype(np.float32)
    mats["dv20"] = dvs
    del mats["dv"]
    ac = pd.DataFrame(mats["aclose"])
    hi = ac.rolling(252, min_periods=200).max().values
    lo = ac.rolling(252, min_periods=200).min().values
    with np.errstate(invalid="ignore", divide="ignore"):
        mats["rpos"] = ((mats["aclose"] - lo) / (hi - lo)).astype(np.float32)
    mats["cal"] = cal
    mats["tick"] = tick
    np.savez(fp, **mats)
    return mats


def fwd_ret(M, H):
    ao, ac = M["aopen"], M["aclose"]
    T = ao.shape[0]
    R = np.full(ao.shape, np.nan, dtype=np.float32)
    # 진입 t0의 시가 → t0+H-1 종가
    R[: T - H + 1] = ac[H - 1:] / ao[: T - H + 1] - 1
    return R


def size_bench(M, R, mindv=1e6):
    """R[t0] 기준, 신호일 s=t0-1의 dv20 10분위별 평균."""
    T, N = R.shape
    B = np.full((T, 10), np.nan, dtype=np.float32)
    BR = np.full((T, 10, 3), np.nan, dtype=np.float32)  # 진단: 규모 × 252일 위치(≤.35/.35~.7/>.7)
    edges = np.full((T, 9), np.nan, dtype=np.float32)
    for t0 in range(1, T):
        s = t0 - 1
        dv = M["dv20"][s]
        ok = (M["close"][s] >= 3) & (dv >= mindv) & np.isfinite(dv)
        if ok.sum() < 100:
            continue
        e = np.nanpercentile(dv[ok], np.arange(10, 100, 10))
        edges[s] = e
        r = R[t0]
        dec = np.searchsorted(e, dv[ok])
        rr = r[ok]
        fin = np.isfinite(rr)
        for k in range(10):
            mk = fin & (dec == k)
            if mk.sum() >= 5:
                B[t0, k] = rr[mk].mean()
        rp = M["rpos"][s][ok]
        rb = np.where(rp <= 0.35, 0, np.where(rp <= 0.7, 1, 2))
        fr = fin & np.isfinite(rp)
        for k in range(10):
            for b in range(3):
                mk = fr & (dec == k) & (rb == b)
                if mk.sum() >= 5:
                    BR[t0, k, b] = rr[mk].mean()
    return B, edges, BR


# ------------------------------------------------------------------ eval
def attach(ev, M, Rs, Bs, mindv=1e6):
    cal = pd.to_datetime(M["cal"])
    tix = {t: i for i, t in enumerate(M["tick"])}
    iwm, spy = tix["IWM"], tix.get("SPY")
    out = []
    drop = {"no_ticker": 0, "no_price": 0, "filter": 0}
    for r in ev.itertuples(index=False):
        t = r.ticker
        if not isinstance(t, str):
            drop["no_ticker"] += 1; continue
        j = tix.get(t)
        if j is None:
            drop["no_price"] += 1; continue
        s = cal.searchsorted(pd.Timestamp(r.date), side="right") - 1  # 신호일(또는 직전 거래일)
        t0 = s + 1
        if s < 0 or t0 >= len(cal):
            continue
        c, dv = M["close"][s, j], M["dv20"][s, j]
        if not (np.isfinite(c) and np.isfinite(dv)):
            drop["no_price"] += 1; continue
        if c < 3 or dv < mindv:
            drop["filter"] += 1; continue
        rec = {"ticker": t, "date": r.date, "t0": cal[t0], "rpos": float(M["rpos"][s, j]), "dv20": float(dv),
               "close": float(c)}
        for extra in ("pct", "dpct", "vwap", "acc", "filer", "filer_prior"):
            if hasattr(r, extra):
                rec[extra] = getattr(r, extra)
        for H in HS:
            R, (B, edges, BR) = Rs[H], Bs[H]
            ret = R[t0, j]
            rec[f"ret{H}"] = float(ret) if np.isfinite(ret) else np.nan
            rec[f"iwm{H}"] = float(R[t0, iwm])
            rec[f"spy{H}"] = float(R[t0, spy]) if spy is not None else np.nan
            e = edges[s]
            if np.isfinite(e).all():
                k = int(np.searchsorted(e, dv))
                rec[f"sz{H}"] = float(B[t0, k])
                rp = rec["rpos"]
                rec[f"szr{H}"] = float(BR[t0, k, 0 if rp <= 0.35 else (1 if rp <= 0.7 else 2)]) if np.isfinite(rp) else np.nan
            else:
                rec[f"sz{H}"] = np.nan
                rec[f"szr{H}"] = np.nan
        out.append(rec)
    return pd.DataFrame(out), drop


def dedupe60(e, M):
    cal = pd.to_datetime(M["cal"])
    e = e.sort_values(["ticker", "t0"]).copy()
    pos = cal.get_indexer(e.t0)
    e["pos"] = pos
    keep = []
    last = {}
    for i, (t, p) in enumerate(zip(e.ticker, e.pos)):
        lp = last.get(t)
        if lp is None or p - lp >= 60:
            keep.append(i)
            last[t] = p
    return e.iloc[keep].drop(columns="pos")


def nw_t(x, lags):
    x = np.asarray(x, float)
    n = len(x)
    if n < 5:
        return np.nan
    u = x - x.mean()
    L = min(lags, n - 1)
    v = u @ u / n
    for l in range(1, L + 1):
        v += 2 * (1 - l / (L + 1)) * (u[l:] @ u[:-l]) / n
    return x.mean() / np.sqrt(v / n) if v > 0 else np.nan


def stats(e, H, bench):
    col = f"x_{bench}{H}"
    d = e.dropna(subset=[f"ret{H}", f"{bench}{H}"]).copy()
    if len(d) == 0:
        return {"n": 0}
    d[col] = d[f"ret{H}"] - COST - d[f"{bench}{H}"]
    coh = d.groupby("t0")[col].mean().sort_index()
    yrs = coh.groupby(coh.index.year).mean()
    ins, oos = coh[coh.index.year <= IS_END], coh[coh.index.year > IS_END]
    xs = np.sort(d[col].values)[::-1]
    k = max(1, int(round(len(xs) * 0.1)))
    tot = xs.sum()

    def cl_t(c):
        return c.mean() / (c.std(ddof=1) / np.sqrt(len(c))) if len(c) > 2 else np.nan

    return {"n": int(len(d)), "dates": int(len(coh)), "mean_evt": float(d[col].mean()),
            "ret_raw": float(d.groupby("t0")[f"ret{H}"].mean().mean()),
            "bench_raw": float(d.groupby("t0")[f"{bench}{H}"].mean().mean()),
            "median_evt": float(d[col].median()), "mean_coh": float(coh.mean()),
            "nw_t": float(nw_t(coh.values, H)), "cl_t": float(cl_t(coh)),
            "top10_share": float(xs[:k].sum() / tot) if tot > 0 else None,
            "ins_mean": float(ins.mean()) if len(ins) else None, "ins_t": float(nw_t(ins.values, H)) if len(ins) else None,
            "ins_n": int(len(ins)),
            "oos_mean": float(oos.mean()) if len(oos) else None, "oos_t": float(nw_t(oos.values, H)) if len(oos) else None,
            "oos_n": int(len(oos)),
            "years_pos": int((yrs > 0).sum()), "years": int(len(yrs)),
            "by_year": {int(k): round(float(v) * 100, 2) for k, v in yrs.items()},
            "win": float((d[col] > 0).mean())}


def verdict(s):
    if not s or s.get("n", 0) == 0 or s.get("oos_mean") is None:
        return "기각(표본 없음)"
    om, ot = s["oos_mean"], s["oos_t"] or 0
    if om > 0 and ot >= 2.64 and s["years_pos"] * 2 > s["years"]:
        return "확정"
    if om > 0 and ot >= 1.65 and (s["ins_mean"] or 0) > 0:
        return "후보"
    return "기각"


def stage_eval(only_years=None):
    tmap = ticker_map()
    M = build_matrices()
    Rs = {H: fwd_ret(M, H) for H in HS}
    bfp = OUT / "size_bench.npz"
    if bfp.exists():
        z = np.load(bfp)
        Bs = {H: (z[f"B{H}"], z[f"E{H}"], z[f"R{H}"]) for H in HS}
        Bs5 = {H: (z[f"B5_{H}"], z[f"E5_{H}"], z[f"R5_{H}"]) for H in HS}
    else:
        Bs, Bs5, sv = {}, {}, {}
        for H in HS:
            Bs[H] = size_bench(M, Rs[H], 1e6)
            Bs5[H] = size_bench(M, Rs[H], 5e6)
            sv[f"B{H}"], sv[f"E{H}"], sv[f"R{H}"] = Bs[H]
            sv[f"B5_{H}"], sv[f"E5_{H}"], sv[f"R5_{H}"] = Bs5[H]
        np.savez(bfp, **sv)
    log("matrices ready", M["aopen"].shape)

    evs, meta = {}, {}
    e1 = ev_13d(tmap)
    evs["U1"] = e1
    evs["U4"] = ev_13g(tmap)
    try:
        e3, st3 = ev_13da(tmap)
        evs["U3"] = e3
        meta["U3_parse"] = st3
    except Exception as ex:  # noqa
        meta["U3_err"] = repr(ex)
    e5 = ev_u5()
    evs["U5"] = e5
    u6p = OUT / "u6_events.csv"
    if u6p.exists():
        evs["U6"] = pd.read_csv(u6p, parse_dates=["date"])
    meta["heuristic_13g"] = heuristic_check(tmap)

    # 생존편향: 연도별 13D 대상 매핑률
    cov = e1.assign(year=e1.date.dt.year, mapped=e1.ticker.notna(),
                    priced=e1.ticker.isin(set(M["tick"])))
    meta["u1_coverage"] = cov.groupby("year").agg(n=("cik", "size"), mapped=("mapped", "mean"),
                                                  priced=("priced", "mean")).round(3).reset_index().to_dict("records")

    results = {}
    tables = {}
    for name, ev in evs.items():
        if len(ev) == 0:
            meta[f"{name}_empty"] = True
            continue
        ev = ev.assign(date=pd.to_datetime(ev.date))
        ev = ev[(ev.date >= "2012-01-01")]
        if only_years:
            ev = ev[ev.date.dt.year.isin(only_years)]
        for dvmin, Bx, tag in ((1e6, Bs, ""), (5e6, Bs5, "_5M")):
            a, drop = attach(ev, M, Rs, Bx, dvmin)
            if len(a) == 0:
                continue
            if name == "U5":
                # T3 가격 일치 필터
                a = a[(a.vwap / a.close).between(0.67, 1.5)]
            a = dedupe60(a, M)
            sets = {name: a}
            if name == "U1":
                sets["U2"] = a[a.rpos <= 0.35]
                sets["U1_top"] = a[a.rpos > 0.35]
                sets["U1_repeat"] = a[a.filer_prior >= 4]   # 진단: 신고인이 이전에 다른 대상 ≥4곳에 13D (PIT)
                sets["U1_oneoff"] = a[a.filer_prior == 0]
            if name == "U5":
                sets["U5_all"] = a
                sets["U5"] = a[a.rpos <= 0.35]
            if name == "U4":
                sets["U4_bottom"] = a[a.rpos <= 0.35]
            for sn, sa in sets.items():
                key = sn + tag
                tables[key] = sa
                results[key] = {"drop": drop}
                for H in HS:
                    for b in ("sz", "iwm", "spy", "szr"):
                        results[key][f"H{H}_{b}"] = stats(sa, H, b)
                results[key]["verdict"] = (verdict(results[key]["H60_sz"]) if sn in ("U1", "U2", "U3", "U4", "U5", "U6")
                                           else "진단(비사전등록): " + verdict(results[key]["H60_sz"]).replace("확정", "후보(비사전등록)"))
    for k, v in tables.items():
        v.to_csv(OUT / f"events_{k}.csv.gz", index=False, compression="gzip")
    (OUT / "results.json").write_text(json.dumps({"meta": meta, "results": results}, ensure_ascii=False,
                                                 indent=1, default=str))
    for k, r in results.items():
        s = r["H60_sz"]
        if s.get("n"):
            log(f"{k:12s} n={s['n']:5d} d={s['dates']:5d} coh={s['mean_coh']*100:+.2f}% t={s['nw_t']:.2f} "
                f"oos={100*(s['oos_mean'] or 0):+.2f}%(t {s['oos_t'] or 0:.2f}) med={s['median_evt']*100:+.2f}% "
                f"yrs {s['years_pos']}/{s['years']} iwm={r['H60_iwm']['mean_coh']*100:+.2f}% -> {r['verdict']}")


if __name__ == "__main__":
    st = sys.argv[1] if len(sys.argv) > 1 else "all"
    if st == "idx":
        stage_idx()
    elif st == "hdr13d":
        stage_hdr13d(sys.argv[2].split(",") if len(sys.argv) > 2 else None)
    elif st == "hdr13da":
        stage_hdr13da()
    elif st == "u6":
        stage_u6()
    elif st == "prices":
        tm = ticker_map()
        need = set(ev_13d(tm).ticker.dropna()) | set(ev_13g(tm).ticker.dropna())
        u6p = OUT / "u6_events.csv"
        if u6p.exists():
            need |= set(pd.read_csv(u6p).ticker.dropna())
        stage_prices(need)
    elif st == "eval":
        stage_eval([int(y) for y in sys.argv[2].split(",")] if len(sys.argv) > 2 else None)
