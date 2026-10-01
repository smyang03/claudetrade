"""Jev x 비실적 8-K 이벤트 → 사후 드리프트 · T1 회피/청산 검증 (2026-09-29, 사전등록)

질문: 실적(2.02) 밖의 8-K 이벤트(문헌상 사후 드리프트가 보고된 항목)에서
  (E0) 항목 유형만으로 드리프트가 있는가, (E1) Jev가 본문에서 뽑은 원자 신호가 항목 유형을 넘어 드리프트를 가르는가,
  그리고 그것이 T1(US 실적 서프라이즈, 시가 진입 20세션 보유, 손절 없음)에 (a) 회피 (b) 보유 중 청산 (c) 독립 전략으로 쓰이는가.

=== 사전등록 (수익률 계산 전 고정. 이벤트 건수만 본 상태. 이탈은 보고서에 '이탈'로 기록) ===
[우주] T1 가격 우주(model_T1_earnings_20260927/prices, 현재 상장 1,592종 + IWM/SPY, yfinance 수정주가).
  CIK = SEC company_tickers(현재). → **생존편향**: 이벤트 뒤 상폐된 회사가 빠진다. 부정 항목(3.01·4.02·2.06·going-concern)의
  드리프트는 **상향 편향(덜 나쁘게 보임)** → 부정 항목의 E0/회피 결과는 상한, '롱' 판정은 추가 의심.
[이벤트] SEC submissions(2015-06 이후) form == '8-K'(8-K/A 제외). items를 쉼표로 쪼개 정확 일치.
  대상 항목 1.01, 1.02, 2.05, 2.06, 3.01, 4.01, 4.02, 5.02, 8.01. 2.02가 함께 있는 공시는 제외(실적 보도, 이미 검증).
  주 항목(한 공시 1개): 우선순위 4.02 > 3.01 > 4.01 > 2.06 > 2.05 > 1.02 > 5.02 > 1.01 > 8.01. 동시 항목 수는 보고.
  acceptanceDateTime(UTC) → America/New_York. 진입 세션 e: 접수일이 거래일이고 09:30 전이면 그 세션 시가,
  아니면 접수일 다음 거래일 시가(장중 접수는 다음날 시가 — 보수적). 같은 티커·같은 e 복수 공시는 우선순위 최고 1건.
  앵커 a = e−1. 필터(E0 기본): close[a] ≥ $3, dvol20(a) ≥ $1M, open[e] > 0. 진입 2015-09-01 ~.
  **실적 창 제외(E0 기본)**: 같은 티커 실적 진입 세션(T1 규칙: AMC→다음 세션, BMO→당일, DMH 포함 날짜 기준)과
  |e − e_earn| ≤ 3세션이면 E0 본표에서 제외(PEAD 재측정 방지). 포함 버전은 강건성 행.
[수익률] H ∈ {5, 20, 60}: r = open[e+H]/open[e] − 1. 벤치 ① 규모매칭: 세션 a의 우주(close≥3, dvol20≥$1M) dvol20 10분위,
  같은 분위 동일가중 open[e]→open[e+H]. ② IWM open→open. 초과 = r − 벤치, net = 초과 − 0.44%.
  주 판정 = 규모매칭 net, H20(T1 보유와 동일). IWM은 보조 열.
  정보 도착 진단: pre = open[e] / close[직전 세션(접수 직전 마지막 종가)] − 1 − IWM 같은 구간, 항목별 평균.
  (5.02 등은 보도자료가 8-K보다 먼저 나가 갭이 이미 흡수했을 수 있음 → '아직 가격에 안 들어간' 전제 점검용)
[추론] 항목별 평균 net. t = 클러스터 t(H5: 진입 주, H20: 진입 월, H60: 진입 분기 — 보유 겹침 흡수).
  IS = 진입 2015-09 ~ 2020-12, OOS = 2021-01 ~. OOS 연도별 부호.
  사전 부호 기대(문헌): 5.02(특히 급작스러운 CEO/CFO 이탈) −, 4.02 −, 4.01 −(불일치·사임 시), 3.01 −, 2.06 −,
  2.05 혼재, 1.01·1.02·8.01 사전 기대 없음(잡동사니, 희석으로 ≈0 예상 — 0이어도 문헌 기각 아님).
  E0 등급: 확정 = |t| ≥ 2.5 AND IS·OOS 부호 같음 AND OOS 연도 ≥ 4/6 같은 부호. 후보 = |t| ≥ 2 AND IS·OOS 부호 같음. 그 외 기각.
[Jev 표본] (문서 = primaryDocument 직접 1요청. HTML 제거 → 주 항목 절 'Item X.XX'부터 다음 'Item d.dd'/SIGNATURE 전까지,
  못 찾으면 첫 'Item' 이후, 4,000자 상한. 회사명·티커 가림 jv.mask)
  ① 사용 사례 집합 전부: T1 I1 거래(trades_I1, 4,580)·라이브 복제(trades_a, 563)의 진입 전 30일 안 접수 대상 8-K,
     보유 중(진입 후 ~ 청산 세션 전) 접수 대상 8-K. (실적 창 제외 적용 안 함, 2.02 동시 공시만 제외)
  ② 희귀 항목 전부: 1.02, 2.05, 2.06, 3.01, 4.01, 4.02 (E0 기본 필터 통과분).
  ③ 5.02 / 1.01 / 8.01: E0 기본 필터 통과분에서 항목별 무작위 상한 5,000 / 3,000 / 3,000 (seed 0).
  총 문서 상한 20,000(넘으면 ③을 비례 축소, 축소 사실 보고). Jev 예산 ≤ US$3.
[Jev 질문 (영어, 1이벤트 1호출)]
  impact: score 5단계 very negative..very positive — 주주에게의 영향.
  exec_departure noul: CEO/CFO/President 이탈·해임.  sudden noul: 즉시 효력/돌발, 사전 발표된 승계 계획·정식 후임 없음.
  for_cause noul: 해고 사유·비위·내부/정부 조사·소송 관련.  auditor_issue noul: 감사인과 불일치·reportable event·
  중대한 내부통제 취약점·감사인 사임/재선임 거부.  distress noul: going-concern·디폴트·약정 위반·상장폐지 위험·파산.
  dilution noul: 신주·워런트·전환증권 발행.  material noul: 회사 규모 대비 큰 사건.
  **severe(사전 정의)** = impact ≤ 1.0 OR (exec_departure ≥ .5 AND sudden ≥ .5) OR for_cause ≥ .5 OR auditor_issue ≥ .5
  OR distress ≥ .5.
[E1] E0 기본 필터 & Jev 채점분. 1차: net20(규모매칭) ~ 주 항목 더미 + severe, 진입 월 클러스터 t.
  보조: + impact(표준화) 연속, 항목 내 impact 하위 1/3 − 상위 1/3(1/3 경계는 항목별 전체 분포 — 수익률 미사용).
  보조2: 연도 walk-forward LightGBM(M2 동일 기본값, 타깃 net20 winsorize 1/99), 피처 = 항목 더미+ret20+vol20+ldvol20
  vs 같은 + Jev 8피처. OOS(2021+) 예측 상위 1/5 − 하위 1/5 차이(월 클러스터 t), 두 모델 비교.
  E1 통과 = severe 계수 |t| ≥ 2.5 AND IS·OOS 부호 같음 → 후보(확정은 forward 후).
[사용 사례]
  (a) 회피: T1 픽(I1 · trades_a 각각)마다 진입 시가 전 30일 안 접수(2.02 동시 제외) 대상 8-K 중 severe 1건 이상 = flag.
      net = gA20 − 0.44%(T1 파일 값). flag − 비flag 평균 차, 날짜+티커 이중 클러스터 t.
      통과 = 차 < 0, t ≤ −2.5, IS(≤2020)·OOS 부호 같음, flag n ≥ 30. n < 30이면 '산출 불가(표본 부족)'.
      보조: Jev 없이 '대상 8-K 아무거나'·'희귀 부정 항목(2.06/3.01/4.01/4.02)' flag.
  (b) 청산: 보유 중 접수된 severe 8-K. 그 8-K의 진입 세션 e8(e < e8 < e+20)부터 예정 청산까지
      잔여 = open[e+20]/open[e8] − 1 − IWM 같은 구간. 대조 = 보유 중 대상 8-K가 없는 같은 집합 픽들의 같은 오프셋 k = e8 − e
      잔여 평균. 차 = 잔여 − 대조(k), 이중 클러스터 t. 통과 = 차 < 0, t ≤ −2.5, OOS 부호 같음, n ≥ 30.
      보조: Jev 없이 '보유 중 대상 8-K 아무거나'.
  (c) 독립: E0/E1을 dvol20 ≥ $5M으로 다시 계산(롱은 양의 드리프트, 부정 드리프트는 회피 신호로만 — 공매도 불가).
[한계(사전 인지)] 생존편향(위), 현재 CIK 매핑(티커 변경·합병 누락), 수정주가, 장중 접수 이벤트는 다음날 시가(늦은 진입),
  Jev 라벨 정확도는 20건 층화 점검으로 보고.
[이탈 기록 (실행 중)]
  1) items/item이 CSV에서 float로 읽혀 희귀·상한 표본이 비는 버그 → dtype=str로 수리 후 표본 재추출(수익률 보기 전).
  2) SEC 429(공유 IP로 추정, 03:30·03:42) → 요청 간격 0.34초·2스레드·429 시 120초 대기로 감속, 순서 usecase→rare→capped.
     결과적으로 표본 17,380건 전부 수집(축소 없음).
  3) E0를 본 뒤 gross(비용 전) 초과·t 열을 추가. net<0 '확정'은 롱 전략에서 비용 0.44%만으로도 생기므로,
     회피 신호 판정은 gross 부호·t로 읽는다(사전 등급 열은 그대로 보고).
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import math
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import warnings
warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))
import research_jev_8k_t1_20260929 as jv  # noqa: E402  (sec_get, strip, mask, jkey, ols_cl, cluster_t 재사용)

OUT = ROOT / "data" / "analysis" / "jev_8k_events_20260929"
SUBF = OUT / "submissions_full"
DOCS = OUT / "docs"
FEE = 0.0044
PRIO = ["4.02", "3.01", "4.01", "2.06", "2.05", "1.02", "5.02", "1.01", "8.01"]
HS = (5, 20, 60)
_PX = {}


def filings_full(cik):
    for fp in (SUBF / f"{cik}.json",):  # 기존 캐시엔 primaryDocument 없음 → 재수집
        if fp.exists():
            return pd.read_json(fp, dtype=False)
    r = jv.sec_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    if r is None:
        return pd.DataFrame()
    j = r.json(); fr = [pd.DataFrame(j["filings"]["recent"])]
    for f in j["filings"].get("files", []):
        if f.get("filingTo", "9999") >= "2015-06-01":
            rr = jv.sec_get("https://data.sec.gov/submissions/" + f["name"])
            if rr is not None:
                fr.append(pd.DataFrame(rr.json()))
    df = pd.concat(fr, ignore_index=True)
    df = df[(df.filingDate >= "2015-06-01") & df.form.isin(
        ["8-K", "10-Q", "10-K", "10-Q/A", "10-K/A", "20-F", "40-F", "6-K", "20-F/A", "40-F/A", "10-KT"])]
    keep = [c for c in ["form", "accessionNumber", "filingDate", "acceptanceDateTime", "items", "primaryDocument"] if c in df]
    df = df[keep].reset_index(drop=True)
    SUBF.mkdir(parents=True, exist_ok=True)
    df.to_json(SUBF / f"{cik}.json", orient="records")
    return df


def prices():
    if not _PX:
        import research_model_T1_earnings_20260927 as t1
        cal, ti, W = t1.load_prices()
        _PX.update(cal=cal, ti=ti, W=W)
    return _PX["cal"], _PX["ti"], _PX["W"]


def events_raw():
    fp = OUT / "events_raw.csv.gz"
    if fp.exists():
        return pd.read_csv(fp, dtype={"items": str})
    OUT.mkdir(parents=True, exist_ok=True)
    cal, ti, W = prices()
    cm = jv.cik_map()
    tk = [t for t in ti if t not in ("IWM", "SPY")]
    ck = {t: cm.get(t.upper().replace(".", "-")) for t in tk}
    ciks = sorted({c for c in ck.values() if c})
    t0 = time.time()
    with cf.ThreadPoolExecutor(4) as ex:
        F = dict(zip(ciks, ex.map(filings_full, ciks)))
    print(f"submissions {len(ciks)} CIK {time.time()-t0:.0f}s", flush=True)
    rows = []
    for t, c in ck.items():
        if not c or not len(F[c]):
            continue
        f = F[c]
        f = f[f.form == "8-K"]
        for r in f.itertuples():
            rows.append({"ticker": t, "cik": c, "acc": r.accessionNumber, "fdate": r.filingDate,
                         "accept": r.acceptanceDateTime, "items": str(r.items),
                         "prim": getattr(r, "primaryDocument", None)})
    ev = pd.DataFrame(rows)
    ev.to_csv(fp, index=False, compression="gzip")
    return ev


# ------------------------------------------------------------ matrices
def mats():
    """dvol20, 규모 10분위 벤치(e, H), 우주 적격 마스크."""
    fp = OUT / "bench.npz"
    cal, ti, W = prices()
    O, C, V = W["open"], W["close"], W["volume"]
    if fp.exists():
        z = np.load(fp)
        return z["dv"], z["dec"], {H: z[f"b{H}"] for H in HS}
    dvx = pd.DataFrame((C * V).T).rolling(20, min_periods=15).mean().values.T.astype(np.float32)  # a 포함 20세션
    elig = (C >= 3) & (dvx >= 1e6)
    dec = np.full(C.shape, -1, np.int8)
    for a in range(C.shape[1]):
        m = elig[:, a]
        if m.sum() < 50:
            continue
        x = dvx[m, a]
        rk = x.argsort().argsort() / (len(x) - 1)
        dec[np.where(m)[0], a] = np.minimum((rk * 10).astype(int), 9)
    T = C.shape[1]; B = {}
    for H in HS:
        b = np.full((10, T), np.nan, np.float32)
        for e in range(1, T - H):
            dd = dec[:, e - 1]
            r = O[:, e + H] / O[:, e] - 1
            ok = (dd >= 0) & np.isfinite(r)
            if ok.sum() < 50:
                continue
            s = np.bincount(dd[ok], weights=r[ok], minlength=10); n = np.bincount(dd[ok], minlength=10)
            b[:, e] = s / np.maximum(n, 1)
        B[H] = b
    np.savez_compressed(fp, dv=dvx, dec=dec, **{f"b{H}": B[H] for H in HS})
    return dvx, dec, B


def earn_sessions(cal):
    d = pd.read_json(ROOT / "data" / "analysis" / "us_earnings_dates.jsonl", lines=True)
    d = d[d.date >= "2015-01-01"].drop_duplicates(["ticker", "date"])
    dv = pd.to_datetime(d.date).values.astype("datetime64[ns]")
    bmo = (d.hour == "BMO").values
    e = np.where(bmo, np.searchsorted(cal.values, dv, side="left"), np.searchsorted(cal.values, dv, side="right"))
    out = {}
    for t, x in zip(d.ticker.values, e):
        out.setdefault(t, []).append(int(x))
    return {t: np.array(sorted(v)) for t, v in out.items()}


def split_items(s):
    return [x.strip() for x in str(s).split(",") if x.strip()]


def build():
    """이벤트 테이블(모든 대상 8-K, 2.02 동시 제외) + 진입 세션·수익률·벤치·플래그."""
    fp = OUT / "events.csv.gz"
    if fp.exists():
        return pd.read_csv(fp, parse_dates=["accept_et", "entry_date"], dtype={"item": str, "items": str})
    cal, ti, W = prices(); O, C = W["open"], W["close"]; iw = ti["IWM"]
    dvx, dec, B = mats()
    ev = events_raw()
    it = ev["items"].map(split_items)
    ev = ev[~it.map(lambda x: "2.02" in x)].copy(); it = it[ev.index]
    ev["item"] = it.map(lambda x: next((p for p in PRIO if p in x), None))
    ev["n_target"] = it.map(lambda x: sum(p in x for p in PRIO))
    ev = ev[ev.item.notna()].copy()
    at = pd.to_datetime(ev.accept, utc=True).dt.tz_convert("America/New_York").dt.tz_localize(None)
    ev["accept_et"] = at
    day = at.dt.normalize().values.astype("datetime64[ns]")
    pos = np.searchsorted(cal.values, day, side="left")
    is_td = (pos < len(cal)) & (cal.values[np.minimum(pos, len(cal) - 1)] == day)
    pre = (at.dt.hour * 60 + at.dt.minute).values < 9 * 60 + 30
    ev["e"] = np.where(is_td & pre, pos, np.searchsorted(cal.values, day, side="right"))
    # 접수 직전 마지막 종가 세션: 거래일 16:00 이후 접수면 그날, 아니면 전날
    post = (at.dt.hour * 60 + at.dt.minute).values >= 16 * 60
    ev["pc"] = np.where(is_td & post, pos, np.searchsorted(cal.values, day, side="left") - 1)
    ev = ev[(ev.e >= 61) & (ev.e < len(cal))].copy()
    ev["prio"] = ev.item.map({p: i for i, p in enumerate(PRIO)})
    ev = ev.sort_values(["ticker", "e", "prio"]).drop_duplicates(["ticker", "e"]).reset_index(drop=True)
    r = ev.ticker.map(ti).values; e = ev.e.values; a = e - 1; T = len(cal)
    ev["entry_date"] = cal[e]
    ev["year"] = cal[e].year
    ev["close_a"] = C[r, a]; ev["dvol20"] = dvx[r, a]; ev["open_e"] = O[r, e]
    ev["dec"] = dec[r, a]
    ev["ret20"] = C[r, a] / C[r, a - 20] - 1
    lr = np.diff(np.log(C[r[:, None], a[:, None] + np.arange(-20, 1)[None, :]]), axis=1)
    ev["vol20"] = np.nanstd(lr, axis=1); ev["ldvol20"] = np.log(np.maximum(ev.dvol20.fillna(1), 1))
    pc = ev.pc.values
    ev["pre"] = (O[r, e] / C[r, pc] - 1) - (O[iw, e] / C[iw, pc] - 1)
    for H in HS:
        x = e + H; ok = x < T; xs = np.minimum(x, T - 1)
        rr = O[r, xs] / O[r, e] - 1; rr[~ok] = np.nan
        bi = O[iw, xs] / O[iw, e] - 1
        bs = np.where(ev.dec.values >= 0, B[H][np.maximum(ev.dec.values, 0), e], np.nan)
        ev[f"r{H}"] = rr; ev[f"xs{H}"] = rr - bs; ev[f"xi{H}"] = rr - bi
    es = earn_sessions(cal)
    ev["earn_win"] = [bool(len(es.get(t, [])) and np.abs(es[t] - x).min() <= 3) for t, x in zip(ev.ticker, ev.e)]
    ev["base"] = (ev.close_a >= 3) & (ev.dvol20 >= 1e6) & (ev.open_e > 0) & (ev.entry_date >= "2015-09-01")
    ev.to_csv(fp, index=False, compression="gzip")
    return ev


# ------------------------------------------------------------ use-case sets & Jev sample
def t1_sets():
    I1 = pd.read_csv(ROOT / "data/analysis/model_T1_impl_20260927/trades_I1.csv.gz", parse_dates=["date", "entry_date"])
    A = pd.read_csv(ROOT / "data/analysis/T1_live_capacity_20260929/trades_a.csv.gz", parse_dates=["date", "entry_date"])
    return {"I1": I1, "A": A}


def usecase_links(ev):
    """T1 픽 × 대상 8-K 연결: pre30(진입 시가 전 30일 안 접수), hold(e < e8 < e+20)."""
    rows = []
    g = {t: x for t, x in ev.groupby("ticker")}
    for nm, P in t1_sets().items():
        for p in P.itertuples():
            x = g.get(p.ticker)
            if x is None:
                continue
            cut = p.entry_date + pd.Timedelta(hours=9, minutes=30)
            m1 = (x.accept_et < cut) & (x.accept_et >= cut - pd.Timedelta(days=30))
            m2 = (x.e > p.e) & (x.e < p.e + 20)
            for kind, m in (("pre30", m1), ("hold", m2)):
                for q in x[m].itertuples():
                    rows.append({"set": nm, "ticker": p.ticker, "pdate": p.date, "pe": p.e, "kind": kind,
                                 "acc": q.acc, "e8": q.e, "item": q.item})
    return pd.DataFrame(rows)


def jev_sample():
    fp = OUT / "jev_sample.csv"
    if fp.exists():
        return pd.read_csv(fp, dtype={"item": str})
    ev = build(); L = usecase_links(ev); L.to_csv(OUT / "links.csv.gz", index=False, compression="gzip")
    b = ev[ev.base]
    s1 = set(L.acc)
    s2 = set(b[b.item.isin(["1.02", "2.05", "2.06", "3.01", "4.01", "4.02"])].acc)
    caps = {"5.02": 5000, "1.01": 3000, "8.01": 3000}
    left = 20000 - len(s1 | s2)
    tot = sum(caps.values())
    scale = min(1.0, max(left, 0) / tot)
    s3 = set()
    for it_, c in caps.items():
        pool = b[(b.item == it_) & ~b.acc.isin(s1 | s2)]
        s3 |= set(pool.sample(min(len(pool), int(c * scale)), random_state=0).acc)
    S = ev[ev.acc.isin(s1 | s2 | s3)][["ticker", "cik", "acc", "prim", "item"]].copy()
    S["src"] = np.where(S.acc.isin(s1), "usecase", np.where(S.acc.isin(s2), "rare", "capped"))
    S.to_csv(fp, index=False)
    print("jev sample", len(S), S.src.value_counts().to_dict(), "scale", round(scale, 3),
          S.item.value_counts().to_dict())
    return S


# ------------------------------------------------------------ docs
import threading
_lk = threading.Lock(); _lt = [0.0]; _blk = [0.0]
GAP = 0.34  # 이탈: 공유 IP 429 차단(03:30·03:42) 뒤 초당 ~3건으로 감속


def sec_slow(url):
    for att in range(6):
        with _lk:
            w = max(GAP - (time.time() - _lt[0]), _blk[0] - time.time())
            if w > 0:
                time.sleep(w)
            _lt[0] = time.time()
        try:
            r = requests.get(url, headers=jv.UA, timeout=30)
            if r.status_code == 200:
                return r
            if r.status_code == 404:
                return None
            if r.status_code in (429, 403):
                with _lk:
                    _blk[0] = time.time() + 120
                print("429 backoff", flush=True)
                continue
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3 + 3 * att)
    return None


def fetch(row):
    fp = DOCS / f"{row.acc}.txt"
    if fp.exists():
        return "cache"
    if not isinstance(row.prim, str) or not row.prim:
        return "noprim"
    nd = row.acc.replace("-", "")
    r = sec_slow(f"https://www.sec.gov/Archives/edgar/data/{int(row.cik)}/{nd}/{row.prim}")
    if r is None:
        return "nodoc"
    t = r.text
    if row.prim.lower().endswith((".htm", ".html")) or "<html" in t[:2000].lower():
        t = jv.strip(t)
    else:
        t = re.sub(r"\s+", " ", t)
    fp.write_text(t, encoding="utf-8")
    return "ok"


def collect_docs():
    DOCS.mkdir(parents=True, exist_ok=True)
    S = jev_sample(); t0 = time.time()
    # 이탈: 속도 제한으로 순서 = usecase → rare → capped(무작위 순서, 시간 내 도달분만)
    S = S.assign(o=S.src.map({"usecase": 0, "rare": 1, "capped": 2}), r=np.random.default_rng(1).random(len(S)))
    S = S.sort_values(["o", "r"])
    S = S[[not (DOCS / f"{a}.txt").exists() for a in S.acc]].drop_duplicates("acc")
    print("todo", len(S), S.src.value_counts().to_dict(), flush=True)
    with cf.ThreadPoolExecutor(2) as ex:
        res = list(ex.map(fetch, S.itertuples()))
    print(pd.Series(res).value_counts().to_dict(), f"{time.time()-t0:.0f}s")


def item_text(t, item):
    m = re.search(r"(?i)item\s*" + re.escape(item) + r"(?!\d)", t)
    if m:
        rest = t[m.start():]
        n = re.search(r"(?i)(?:\bitem\s*\d\.\d\d(?!\d)|\bSIGNATURES?\b)", rest[20:])
        sec = rest[: 20 + n.start()] if n else rest
        if len(sec) < 300:  # 목차형 짧은 절이면 이어지는 본문 더 포함
            sec = rest
    else:
        m2 = re.search(r"(?i)\bitem\s*\d\.\d\d", t)
        sec = t[m2.start():] if m2 else t
    return sec[:4000]


QS = {
    "impact": {"type": "score", "instructions": "Likely impact of the disclosed event on the company's existing shareholders.",
               "criteria": ["very negative", "negative", "neutral", "positive", "very positive"]},
    "exec_departure": {"type": "noul", "instructions": "The CEO, CFO, or President is leaving, resigning, retiring, or being removed."},
    "sudden": {"type": "noul", "instructions": ("The change is effective immediately or abrupt, without a previously announced "
                                                "transition plan or a named permanent successor.")},
    "for_cause": {"type": "noul", "instructions": ("The event involves termination for cause, misconduct, an internal or "
                                                   "government investigation, or litigation.")},
    "auditor_issue": {"type": "noul", "instructions": ("The filing reports disagreements with the auditor, reportable events, a material "
                                                       "weakness in internal control, a restatement, or the auditor resigned or declined to stand for re-election.")},
    "distress": {"type": "noul", "instructions": ("The filing mentions going-concern doubt, a debt default, covenant breach, "
                                                  "delisting risk or bankruptcy.")},
    "dilution": {"type": "noul", "instructions": "The filing announces issuance of shares, warrants, or convertible securities that dilute existing shareholders."},
    "material": {"type": "noul", "instructions": ("The disclosed event appears large relative to the company's size (e.g., major "
                                                  "acquisition, sale of the company, large contract, large charge or write-down).")},
}


def ask(row, k, names):
    t = (DOCS / f"{row['acc']}.txt").read_text(encoding="utf-8")
    txt = jv.mask(item_text(t, row["item"]), names)
    body = {"state": {"sec_form_8k_item": row["item"], "filing_excerpt": txt}, "model": "jev-latest", "questions": QS}
    err = ""
    for att in range(3):
        t0 = time.time()
        try:
            r = requests.post(jv.JURL, json=body, headers={"Authorization": f"Bearer {k}"}, timeout=120)
            if r.status_code == 200:
                j = r.json()
                return {"acc": row["acc"], "ans": j["answers"], "usage": j.get("usage"), "model": j.get("model"),
                        "sec": round(time.time() - t0, 3), "nchar": len(txt)}
            err = f"HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001
            err = type(exc).__name__
        time.sleep(2 * (att + 1))
    return {"acc": row["acc"], "error": err}


def collect_jev(limit=None):
    S = jev_sample()
    S = S[[(DOCS / f"{a}.txt").exists() for a in S.acc]]
    cm = json.loads((jv.OUT / "company_tickers.json").read_text())
    title = {int(v["cik_str"]): v["title"] for v in cm.values()}
    cache = OUT / "jev.jsonl"
    done = {r["acc"] for r in jv.jl(cache) if "ans" in r}
    todo = [r for r in S.to_dict("records") if r["acc"] not in done][:limit]
    k = jv.jkey(); t0 = time.time()
    with cf.ThreadPoolExecutor(4) as ex, cache.open("a", encoding="utf-8") as fh:
        for res in ex.map(lambda r: ask(r, k, [title.get(int(r["cik"]), ""), r["ticker"]]), todo):
            fh.write(json.dumps(res) + "\n"); fh.flush()
    print(f"Jev {len(todo)} calls {time.time()-t0:.0f}s")


def jev_feats():
    R = [r for r in jv.jl(OUT / "jev.jsonl") if "ans" in r]
    rows = []
    for r in R:
        a = r["ans"]
        rows.append({"acc": r["acc"], "impact": a["impact"].get("score"),
                     **{q: a[q].get("noul") for q in QS if q != "impact"},
                     "sec": r["sec"], "in_tok": (r.get("usage") or {}).get("input_tokens"),
                     "out_tok": (r.get("usage") or {}).get("output_tokens")})
    J = pd.DataFrame(rows).drop_duplicates("acc", keep="last")
    J["severe"] = ((J.impact <= 1.0) | ((J.exec_departure >= .5) & (J.sudden >= .5)) | (J.for_cause >= .5)
                   | (J.auditor_issue >= .5) | (J.distress >= .5))
    return J


JF = ["impact", "exec_departure", "sudden", "for_cause", "auditor_issue", "distress", "dilution", "material"]


# ------------------------------------------------------------ evaluation
def grp_t(v, g):
    v = np.asarray(v, float); ok = np.isfinite(v)
    return jv.cluster_t(v[ok], np.asarray(g)[ok]) if ok.sum() > 5 else float("nan")


def clus(df, H):
    d = pd.to_datetime(df.entry_date)
    return {5: d.dt.to_period("W").astype(str), 20: d.dt.to_period("M").astype(str), 60: d.dt.to_period("Q").astype(str)}[H].values


def stat(x, H, col="xs"):
    v = (x[f"{col}{H}"] - FEE).values
    ok = np.isfinite(v)
    x = x[ok]; v = v[ok]
    if len(v) < 10:
        return {"n": int(len(v))}
    isx = x.year <= 2020
    yr = pd.Series(v).groupby(x.year.values).mean()
    oy = yr[yr.index >= 2021]
    g = v + FEE; gi = x[f"xi{H}"].values
    return {"n": int(len(v)), "net": round(float(v.mean()), 4), "t": round(grp_t(v, clus(x, H)), 2),
            "gross": round(float(g.mean()), 4), "gross_t": round(grp_t(g, clus(x, H)), 2),
            "gross_is": round(float(g[isx.values].mean()), 4) if isx.any() else None,
            "gross_oos": round(float(g[~isx.values].mean()), 4) if (~isx).any() else None,
            "gross_oos_t": round(grp_t(g[~isx.values], clus(x[~isx], H)), 2) if (~isx).sum() > 10 else None,
            "gross_oos_years_neg": f"{int((pd.Series(g).groupby(x.year.values).mean().loc[lambda z: z.index >= 2021] < 0).sum())}/{len(oy)}",
            "is_n": int(isx.sum()), "is": round(float(v[isx.values].mean()), 4) if isx.any() else None,
            "is_t": round(grp_t(v[isx.values], clus(x[isx], H)), 2) if isx.sum() > 10 else None,
            "oos_n": int((~isx).sum()), "oos": round(float(v[~isx.values].mean()), 4) if (~isx).any() else None,
            "oos_t": round(grp_t(v[~isx.values], clus(x[~isx], H)), 2) if (~isx).sum() > 10 else None,
            "oos_years_pos": f"{int((oy > 0).sum())}/{len(oy)}",
            "years": {int(k): round(float(val), 4) for k, val in yr.items()}}


def grade(s):
    if "t" not in s or s.get("is") is None or s.get("oos") is None or not np.isfinite(s["t"]):
        return "산출 불가"
    same = np.sign(s["is"]) == np.sign(s["oos"])
    pos, n = map(int, s["oos_years_pos"].split("/"))
    yrs_ok = (pos >= 4) if s["net"] > 0 else ((n - pos) >= 4)
    if abs(s["t"]) >= 2.5 and same and yrs_ok:
        return "확정"
    if abs(s["t"]) >= 2 and same:
        return "후보"
    return "기각"


def e0(ev, R, tag, mask):
    out = {}
    for it_ in PRIO + ["ALL"]:
        x = ev[mask & ((ev.item == it_) if it_ != "ALL" else True)]
        row = {"n_all": int(len(x)), "pre_gap": round(float(x.pre.mean()), 4) if len(x) else None,
               "pre_t": round(grp_t(x.pre.values, x.entry_date.dt.to_period("W").astype(str).values), 2) if len(x) > 10 else None,
               "multi_item_share": round(float((x.n_target > 1).mean()), 3) if len(x) else None}
        for H in HS:
            s = stat(x, H); s["grade"] = grade(s); row[f"H{H}"] = s
            row[f"H{H}_iwm"] = {k: v for k, v in stat(x, H, "xi").items() if k in ("n", "net", "t", "is", "oos")}
        out[it_] = row
    R[tag] = out


def ols_fe(y, X, cl):
    b, t = jv.ols_cl(y, X, [cl])
    return b, t


def e1(d, R, tag):
    d = d[np.isfinite(d.xs20)].copy()
    d["net20"] = d.xs20 - FEE
    d["yw"] = d.net20.clip(*d.net20.quantile([.01, .99]))
    items = [i for i in PRIO if (d.item == i).sum() >= 30]
    dm = pd.get_dummies(d.item)[items[1:]].astype(float).values if len(items) > 1 else np.zeros((len(d), 0))
    cl = pd.to_datetime(d.entry_date).dt.to_period("M").astype(str).values
    out = {"n": int(len(d)), "severe_share": round(float(d.severe.mean()), 3)}
    for nm, m in (("all", np.ones(len(d), bool)), ("IS", (d.year <= 2020).values), ("OOS", (d.year >= 2021).values)):
        X = np.column_stack([dm[m], d.severe.values[m].astype(float)])
        b, t = ols_fe(d.yw.values[m], X, cl[m])
        imp = ((d.impact - d.impact.mean()) / d.impact.std()).values[m]
        b2, t2 = ols_fe(d.yw.values[m], np.column_stack([dm[m], imp]), cl[m])
        out[nm] = {"n": int(m.sum()), "severe_b": round(float(b[-1]), 4), "severe_t": round(float(t[-1]), 2),
                   "impact_b_per_sd": round(float(b2[-1]), 4), "impact_t": round(float(t2[-1]), 2)}
    out["pass"] = bool(abs(out["all"]["severe_t"]) >= 2.5 and np.sign(out["IS"]["severe_b"]) == np.sign(out["OOS"]["severe_b"]))
    # by item: severe vs not, impact tercile
    by = {}
    for it_ in items:
        x = d[d.item == it_]
        q = x.impact.quantile([1 / 3, 2 / 3]).values
        lo, hi = x[x.impact <= q[0]], x[x.impact >= q[1]]
        sv, ns = x[x.severe], x[~x.severe]
        by[it_] = {"n": int(len(x)), "n_severe": int(len(sv)),
                   "severe_net": round(float(sv.net20.mean()), 4) if len(sv) else None,
                   "severe_t": round(grp_t(sv.net20.values, clus(sv, 20)), 2) if len(sv) > 10 else None,
                   "nonsev_net": round(float(ns.net20.mean()), 4) if len(ns) else None,
                   "sev_is": round(float(sv[sv.year <= 2020].net20.mean()), 4) if (sv.year <= 2020).any() else None,
                   "sev_oos": round(float(sv[sv.year >= 2021].net20.mean()), 4) if (sv.year >= 2021).any() else None,
                   "imp_lo_net": round(float(lo.net20.mean()), 4), "imp_hi_net": round(float(hi.net20.mean()), 4),
                   "lo_minus_hi": round(float(lo.net20.mean() - hi.net20.mean()), 4)}
        # special subsets
        if it_ == "5.02":
            s = x[(x.exec_departure >= .5) & (x.sudden >= .5)]
            by[it_]["sudden_exec"] = {"n": int(len(s)), "net": round(float(s.net20.mean()), 4) if len(s) else None,
                                      "t": round(grp_t(s.net20.values, clus(s, 20)), 2) if len(s) > 10 else None,
                                      "is": round(float(s[s.year <= 2020].net20.mean()), 4) if (s.year <= 2020).any() else None,
                                      "oos": round(float(s[s.year >= 2021].net20.mean()), 4) if (s.year >= 2021).any() else None}
    out["by_item"] = by
    # walk-forward LightGBM (보조2)
    import lightgbm as lgb
    base = ["ret20", "vol20", "ldvol20"]
    dmf = pd.get_dummies(d.item, prefix="it").astype(float)
    Z = pd.concat([d.reset_index(drop=True), dmf.reset_index(drop=True)], axis=1)
    F0 = base + list(dmf.columns); F1 = F0 + JF
    for tag2, F in (("ctx", F0), ("ctx_jev", F1)):
        Z[tag2] = np.nan
        for Y in range(2019, 2027):
            tr = Z[(Z.year < Y) & (Z.e + 20 < Z[Z.year == Y].e.min() if (Z.year == Y).any() else False)]
            te = Z[Z.year == Y]
            if len(te) == 0 or len(tr) < 300:
                continue
            m = lgb.LGBMRegressor(n_jobs=4, random_state=0, verbose=-1)
            m.fit(tr[F].values.astype(np.float32), tr.yw.values)
            Z.loc[te.index, tag2] = m.predict(te[F].values.astype(np.float32))
    wf = {}
    for tag2 in ("ctx", "ctx_jev"):
        o = Z[Z[tag2].notna() & (Z.year >= 2021)].copy()
        o["q"] = o.groupby("year")[tag2].transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False))
        top, bot = o[o.q == 4], o[o.q == 0]
        wf[tag2] = {"n": int(len(o)), "top": round(float(top.net20.mean()), 4), "bot": round(float(bot.net20.mean()), 4),
                    "top_t": round(grp_t(top.net20.values, clus(top, 20)), 2),
                    "bot_t": round(grp_t(bot.net20.values, clus(bot, 20)), 2)}
    out["wf"] = wf
    R[tag] = out


def uc(ev, J, R):
    L = pd.read_csv(OUT / "links.csv.gz", parse_dates=["pdate"], dtype={"item": str})
    L = L.merge(J[["acc", "severe", "impact"]], on="acc", how="left")
    cal, ti, W = prices(); O = W["open"]; iw = ti["IWM"]; T = O.shape[1]
    rare_neg = {"2.06", "3.01", "4.01", "4.02"}
    out = {}
    for nm, P in t1_sets().items():
        P = P.copy(); P["net"] = P.gA20 - FEE
        Lp = L[(L.set == nm) & (L.kind == "pre30")]
        key = lambda df: set(zip(df.ticker, df.pdate))  # noqa: E731
        flags = {"severe": key(Lp[Lp.severe == True]), "any": key(Lp),  # noqa: E712
                 "rare_neg": key(Lp[Lp.item.isin(rare_neg)]), "unscored": key(Lp[Lp.severe.isna()])}
        A = {}
        for fn, ks in flags.items():
            f = np.array([(t, d) in ks for t, d in zip(P.ticker, P.date)])
            if fn == "unscored":
                A[fn] = int(f.sum()); continue
            row = {"n_flag": int(f.sum()), "n": int(len(P))}
            if f.sum() >= 3:
                b, t = jv.ols_cl(P.net.values, f.astype(float)[:, None], [P.e.values, P.ticker.values])
                isx = P.year.values <= 2020
                row.update(flag_net=round(float(P.net[f].mean()), 4), rest_net=round(float(P.net[~f].mean()), 4),
                           diff=round(float(b[1]), 4), t=round(float(t[1]), 2),
                           is_diff=round(float(P.net[f & isx].mean() - P.net[~f & isx].mean()), 4) if (f & isx).any() else None,
                           oos_diff=round(float(P.net[f & ~isx].mean() - P.net[~f & ~isx].mean()), 4) if (f & ~isx).any() else None,
                           n_flag_oos=int((f & ~isx).sum()),
                           years={int(y): round(float(P.net[f & (P.year.values == y)].mean() - P.net[~f & (P.year.values == y)].mean()), 4)
                                  for y in sorted(P.year.unique()) if (f & (P.year.values == y)).any()})
                row["pass"] = bool(row["n_flag"] >= 30 and row["diff"] < 0 and row["t"] <= -2.5 and row["is_diff"] is not None
                                   and row["oos_diff"] is not None and row["is_diff"] < 0 and row["oos_diff"] < 0)
            A[fn] = row
        out[f"a_{nm}"] = A
        # (b) hold
        Lh = L[(L.set == nm) & (L.kind == "hold")]
        held = key(Lh)
        ctrl = P[[(t, d) not in held for t, d in zip(P.ticker, P.date)]]
        ri = ctrl.ticker.map(ti).values; ce = ctrl.e.values.astype(int)
        cm = {}
        for k in range(1, 20):
            ok = ce + 20 < T
            v = O[ri[ok], ce[ok] + 20] / O[ri[ok], ce[ok] + k] - 1 - (O[iw, ce[ok] + 20] / O[iw, ce[ok] + k] - 1)
            cm[k] = float(np.nanmean(v))
        Bh = {}
        Lh = Lh.merge(P[["ticker", "date", "e", "year"]].rename(columns={"date": "pdate", "e": "pe2"}), on=["ticker", "pdate"])
        Lh = Lh.sort_values("e8").drop_duplicates(["ticker", "pdate", "severe"])
        for fn, m in (("severe", Lh.severe == True), ("any", np.ones(len(Lh), bool))):  # noqa: E712
            x = Lh[m].sort_values("e8").drop_duplicates(["ticker", "pdate"]).copy()
            x = x[x.pe + 20 < T]
            if len(x) < 3:
                Bh[fn] = {"n": int(len(x))}; continue
            r = x.ticker.map(ti).values; e8 = x.e8.values.astype(int); ex = x.pe.values.astype(int) + 20
            x["rem"] = O[r, ex] / O[r, e8] - 1 - (O[iw, ex] / O[iw, e8] - 1)
            x["ctrl"] = [cm[int(k)] for k in (e8 - x.pe.values)]
            x["d"] = x.rem - x.ctrl
            b, t = jv.ols_cl(x.d.values, np.zeros((len(x), 0)), [x.e8.values, x.ticker.values])
            isx = x.year.values <= 2020
            Bh[fn] = {"n": int(len(x)), "rem": round(float(x.rem.mean()), 4), "ctrl": round(float(x.ctrl.mean()), 4),
                      "diff": round(float(b[0]), 4), "t": round(float(t[0]), 2),
                      "is_diff": round(float(x.d[isx].mean()), 4) if isx.any() else None,
                      "oos_diff": round(float(x.d[~isx].mean()), 4) if (~isx).any() else None, "n_oos": int((~isx).sum()),
                      "items": x.item.value_counts().to_dict()}
            Bh[fn]["pass"] = bool(Bh[fn]["n"] >= 30 and Bh[fn]["diff"] < 0 and Bh[fn]["t"] <= -2.5 and
                                  (Bh[fn]["oos_diff"] or 0) < 0)
        out[f"b_{nm}"] = Bh
    R["usecase"] = out


def evaluate():
    ev = build(); J = jev_feats()
    ev["accept_et"] = pd.to_datetime(ev.accept_et); ev["entry_date"] = pd.to_datetime(ev.entry_date)
    R = {"n_events_target": int(len(ev)), "n_base": int(ev.base.sum()), "n_base_noearn": int((ev.base & ~ev.earn_win).sum()),
         "items_base_noearn": ev[ev.base & ~ev.earn_win].item.value_counts().to_dict(),
         "n_jev": int(len(J)), "jev_tok_in": float(J.in_tok.sum()), "jev_tok_out": float(J.out_tok.sum()),
         "jev_lat_mean": float(J.sec.mean()), "severe_share": float(J.severe.mean())}
    e0(ev, R, "E0", ev.base & ~ev.earn_win)
    e0(ev, R, "E0_with_earn", ev.base)
    e0(ev, R, "E0c_5M", ev.base & ~ev.earn_win & (ev.dvol20 >= 5e6))
    d = ev[ev.base & ~ev.earn_win].merge(J, on="acc")
    e1(d, R, "E1")
    e1(d[d.dvol20 >= 5e6], R, "E1c_5M")
    # severe standalone drift (c)
    sv = d[(d.severe) & (d.dvol20 >= 5e6)]
    R["C_severe_5M"] = {f"H{H}": stat(sv, H) for H in HS}
    for H in HS:
        R["C_severe_5M"][f"H{H}"]["grade"] = grade(R["C_severe_5M"][f"H{H}"])
    uc(ev, J, R)
    (OUT / "results.json").write_text(json.dumps(R, indent=1, default=str))
    print(json.dumps(R, indent=1, default=str)[:20000])


def spot(n=3):
    J = jev_feats(); ev = build()[["acc", "item"]]
    J = J.merge(ev, on="acc")
    J["lab"] = np.where(J.severe, "severe", "not")
    s = J.groupby(["item", "lab"], group_keys=False).apply(lambda g: g.sample(min(1, len(g)), random_state=3))
    s = s.sample(min(20, len(s)), random_state=3)
    for r in s.itertuples():
        t = item_text((DOCS / f"{r.acc}.txt").read_text(encoding="utf-8"), r.item)
        print(f"### {r.acc} item={r.item} severe={r.severe} impact={r.impact:.2f} exec={r.exec_departure:.2f} "
              f"sudden={r.sudden:.2f} cause={r.for_cause:.2f} aud={r.auditor_issue:.2f} dist={r.distress:.2f}\n{t[:900]}\n")


if __name__ == "__main__":
    st = sys.argv[1]
    if st == "events":
        ev = events_raw()
        print(len(ev), ev.ticker.nunique())
        it = ev["items"].map(split_items)
        for i in PRIO + ["2.02"]:
            m = it.map(lambda x: i in x)
            print(i, int(m.sum()), "w/o 2.02:", int((m & ~it.map(lambda x: "2.02" in x)).sum()))
    elif st == "build":
        ev = build(); print(len(ev), ev.base.sum(), (ev.base & ~ev.earn_win).sum())
        print(ev[ev.base & ~ev.earn_win].item.value_counts())
    elif st == "sample":
        jev_sample()
    elif st == "docs":
        collect_docs()
    elif st == "jev":
        collect_jev(int(sys.argv[2]) if len(sys.argv) > 2 else None)
    elif st == "eval":
        evaluate()
    elif st == "spot":
        spot()
