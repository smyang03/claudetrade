"""US 기업 이벤트 느린 반응(사후 드리프트) 탐색 — E1~E5 (2026-09-30, 사전등록, 연구 전용)

질문: 공시/발표가 공개된 뒤 '다음 시가'에 사도(왕복 0.50%) 며칠~몇 주 초과수익이 이어지는 US 이벤트가 있는가.

=== 사전등록 (수익률 계산 전 고정. 이탈은 보고서에 '이탈'로 기록) ===
[가격] data/analysis/k1_us_20260929/px_mats.npz (yfinance, 2009-01 ~ 2026-09, 7,651 티커 — 대부분 현재 상장 티커).
  수정 시가 aopen = open × adj/close. 수익률 = 보유 H 세션 open→open: aopen[e+H]/aopen[e] − 1 (H ∈ 5/20/60, E3은 20/60/120).
  청산 시점 가격 결측(상폐 등)이면 e..e+H 구간 마지막 유효 수정종가로 청산(드롭하지 않음).
[비용] 왕복 0.50% (KIS 실측). net = r − 0.005 − 벤치.
[벤치] ① 규모매칭(주): 앵커 a = e−1의 우주(close ≥ $3, dv20 ≥ $1M) dv20 10분위, 같은 분위 동일가중 같은 창.
  ② IWM 같은 창(보조). 이벤트 필터: close[a] ≥ $3, dv20[a] ≥ $1M. 유동성 부분집합 dv20 ≥ $5M.
[추론] 진입일 코호트 평균 시계열 → Newey-West t (lag = H). IS = 진입 ≤ 2019, OOS = 진입 ≥ 2020. 연도별 부호.
  중앙값(이벤트), 상위 10% 이벤트의 합 비중(top-decile share), 유동성 부분집합, T1 I1 픽과의 겹침
  (같은 티커 I1 진입이 이벤트 진입 [−5, +20] 세션 안).
  통과선(확정): OOS net 초과(규모매칭, 주 보유기간) > 0 AND OOS NW t ≥ 2.64 AND 연도 부호 과반 양수 AND 유동성 부분집합 같은 부호.
  후보: OOS > 0 AND OOS t ≥ 1.65 AND IS > 0. 그 외 기각. 회피(avoid) 신호는 부호를 뒤집어 같은 기준(음수가 '통과').
  주 보유기간: E1 = 발표→효력일 종가 창(추가)과 효력일+1 시가 → H20(제외 반등), E2/E4/E5 = H20, E3 = H60.
[생존편향] 가격 우주는 현재 티커 중심 → 이벤트 뒤 상폐·피인수된 회사는 빠진다. 연도별 '이벤트 중 가격 매핑 비율'을 보고한다.
  CIK → 티커는 SEC company_tickers.json(현재). 과거 연도일수록 매핑률이 낮다 = 편향이 크다.
[진입 시각] 8-K(FTS)는 file_date만 있다 → 주 규칙: file_date 다음 거래 세션 시가(항상 공개 이후 — 보수적, 장전 공시는 하루 늦음).
  강건성: jev 캐시(submissions_full, 2015-06+, T1 우주)에 acceptanceDateTime이 있으면 UTC→ET, 거래일 09:30 전 접수 = 당일 시가.
  같은 티커 60세션 안 반복 이벤트는 첫 건만.
[E1 지수 편입/편출] 출처 = Wikipedia wikitext(무료): 'Historical components of the S&P 500'(id=changes),
  'List of S&P 400 companies'·'List of S&P 600 companies'의 변경 표. 발표일 = 각 행 ref의 S&P 문서 URL
  (indexnews/announcements/YYYYMMDD) → press.spglobal.com/YYYY-MM-DD → ref |date= 순. 발표일 없는 행은 발표 창 분석 제외.
  S&P 발표는 통상 장 마감 후 → 진입 = 발표일 다음 세션 시가.
  (a) 편입: 진입 → 효력일(표의 Effective Date) 전 마지막 세션 종가(= 효력일 직전 거래일 종가, 지수 편입 체결 시점), 그리고 고정 H.
  (b) 편출 반등: 효력일 세션 시가 진입 → H (편출 사유가 시총 변화/타 지수 이동인 행만 — 피인수·파산 편출은 매핑 불가·거래 불가).
  (c) 편입 사후(효력일 시가 → H) — 반전 여부(회피 정보).
  표의 효력일은 일부가 '편출 종목의 마지막 거래일'로 적혀 있다(위키 주석) → ±1일 강건성.
[E2 자사주 매입 승인] EDGAR FTS, form 8-K, 월 단위 창 2010-01 ~ 2026-09, 질의(AND 의미 검증: '"repurchase program"' 934 →
  '+authorized' 605, 2015Q1): Q1 '"repurchase program" authorized', Q2 '"repurchase program" approved',
  Q3 '"repurchase plan" authorized', Q4 '"buyback program" authorized'. adsh 중복 제거.
  주 표본: items에 2.02(실적) 없는 공시. 2.02 동반은 강건성 행.
  본문(EX-99 우선, 없으면 첫 hit 문서)에서 '(repurchase|buyback|buy back)' 과 '(authoriz|approv)' 가 한 문장에 있는 첫 문장 →
  $금액(million/billion) 최대값, 또는 'N shares', 또는 'N% of ... outstanding'. 본문에 해당 문장이 없으면 표본 제외(진행형 언급).
  규모 = 금액 / (close[a] × 발행주식수[XBRL frames dei:EntityCommonStockSharesOutstanding, 이벤트 분기 이전 최신]).
  하위표본: 규모 ≥ 5%, ≥ 10%, 소형(dv20 하위 5분위), '2년 내 첫 승인'.
[E3 스핀오프] EDGAR full-index(k1 캐시) form 10-12B(/A 포함) 첫 접수 CIK = SpinCo. 가격 매핑 = 현재 티커만(피인수 SpinCo 누락 명시).
  첫 정규 거래일 = 10-12B 첫 접수 이후 매트릭스 첫 유효 시가(이 날이 2009-02 이후이고, 접수 뒤 400세션 안). 진입 = 둘째 세션 시가.
  보유 20/60/120. 모회사 레그는 모회사 식별 불가(10-12B에 모회사 CIK 없음)로 생략.
[E4 배당 개시/재개] FTS 8-K 질의: '"first quarterly dividend"', '"initiation of a quarterly"', '"initiate a quarterly"',
  '"initiated a quarterly"', '"reinstatement of" dividend', '"reinstate" "quarterly dividend"', '"resumption of" dividend',
  '"inaugural" "quarterly dividend"', '"first-ever" dividend'. 확인 = 앵커 a 이전 252세션 동안 adj/close 비율 변화(|Δ|>2e-4)
  없음(= 배당 없음; 200세션 이상 가격 필요). 확인 통과만 주 표본. 2.02 동반 제외(강건성 행 포함).
[E5 회피] (i) 증자: full-index 424B4/424B5 → 접수일 다음 세션 시가, 60세션 dedupe. (ii) 액면 병합: FTS '"reverse stock split"' 8-K.
  단독 이벤트 수익률(음수 = 회피 근거) + T1 I1 픽 오버레이: I1 진입 전 30일 안 이벤트 있는 픽 vs 없는 픽 gA20 net 차.

=== 사전등록 v2 (2026-09-30, 1차 결과 뒤·신규 계산 전 동결) ===
[동결 규칙 — 변경 금지]
  C-E2: 8-K(FTS 동일 질의 4종, 2.02 없음) 승인 문장 규모 ≥ 10% of mktcap, 진입 = file_date 다음 세션 시가,
        H60 open→open, 60세션 dedupe, 필터 close[a] ≥ $3·dv20[a] ≥ $1M, 비용 0.50%, 규모매칭 벤치.
  C-E3: 10-12B(/A) 첫 접수 CIK = SpinCo, 첫 정규 거래일 f(10-12B 첫 접수 이후 첫 유효 시가, 접수 후 400세션 안, 2009-02 이후),
        진입 = 세션 f+16 시가(= 기존 코드 e+15), H120 open→open, 필터·분위 = 진입 전일 dv20/close, 비용 0.50%, 규모매칭 벤치.
[생존 기준 — 두 후보 공통] 확장 표본 전체에서 net 초과 평균 > 0 AND 진입 분기 클러스터 t ≥ 2.0. (보고: OOS ≥2020 분기 클러스터 t, 연도 부호.)
[E3 확장] 현재 티커로 매핑 안 되는 10-12B CIK: SEC submissions JSON(tickers, formerNames, name) + FTS display_names(접수 당시 표기
  '(TICKER)')로 과거 티커 복원 → 가격: yfinance → stooq(무료) → 그 외 무료 소스 순서. 개인·신탁·비상장 접수(개인명, 'TRUST', 'LLC'
  등 가격 없는 것)는 '복원 불가'로 분류해 분모에 남기고 연도별 커버리지(복원·가격 확보 / 전체)를 보고. 복원 가격은 수정주가 여부·
  출처를 기록하고, 벤치 분위는 기존 매트릭스 우주 edges로 배정(복원 종목은 우주 평균 계산에 넣지 않음). 상폐로 청산 가격 결측이면
  마지막 유효 종가(기존 규칙). 판정 = 기존 + 복원 합친 표본.
[E2 규모 검증] 규모 ≥ 5% 후보에서 층화 200건(연도 IS/OOS × 규모 5~10%/10~20%/20%+ × 금액유형 usd/shares/pct, seed 0) — Claude Code가
  문서를 직접 읽어 정답(이번 승인 여부, 승인 금액/주식수) 기록 → regex 정밀도·규모 오차 측정 → 추출기 수정(수정은 검증 표본 밖
  규칙 일반화만, 표본 개별 예외 하드코딩 금지) → 전체 재추출·재측정. 2.02 동반 문서 수집 완료 후 강건성 행 갱신.
[생존 시 산출] 섀도 원장 설계서만(신호원·시각·KIS LOO 가능성). 구현 안 함.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/analysis/us_events_20260930"
K1 = ROOT / "data/analysis/k1_us_20260929"
JEV = ROOT / "data/analysis/jev_8k_events_20260929"
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "fts").mkdir(exist_ok=True)
(OUT / "docs").mkdir(exist_ok=True)
UA = "claudetrade research smyang03@gmail.com"  # 저장소 기존 SEC UA(k1/T3)
COST = 0.005
IS_END = 2019
RATE = float(os.environ.get("EV_RATE", "3.5"))


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    with open(OUT / "run.log", "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + s + "\n")


_lock = threading.Lock()
_last = [0.0]


def _throttle():
    with _lock:
        now = time.time()
        w = _last[0] + 1.0 / RATE - now
        if w > 0:
            time.sleep(w)
        _last[0] = time.time()


def sec_get(url, tries=10):
    for k in range(tries):
        _throttle()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (403, 429):
                log(f"SEC {e.code} backoff {min(600, 60 * (k + 1))}s {url[:80]}")
                with _lock:
                    time.sleep(min(600, 60 * (k + 1)))
                continue
            time.sleep(5 * (k + 1))
        except Exception:  # noqa
            time.sleep(3 * (k + 1))
    return None


# ------------------------------------------------------------------ FTS
E2_Q = ['"repurchase program" authorized', '"repurchase program" approved',
        '"repurchase plan" authorized', '"buyback program" authorized']
E4_Q = ['"first quarterly dividend"', '"initiation of a quarterly"', '"initiate a quarterly"',
        '"initiated a quarterly"', '"reinstatement of" dividend', '"reinstate" "quarterly dividend"',
        '"resumption of" dividend', '"inaugural" "quarterly dividend"', '"first-ever" dividend']
E5_Q = ['"reverse stock split"']


def months(y0=2010, y1=2026, m1=9):
    for y in range(y0, y1 + 1):
        for m in range(1, 13):
            if (y, m) > (y1, m1):
                return
            s = f"{y}-{m:02d}-01"
            e = (pd.Timestamp(s) + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")
            yield s, e


def fts_window(q, s, e):
    rows, frm = [], 0
    while True:
        u = ("https://efts.sec.gov/LATEST/search-index?q=" + urllib.parse.quote(q)
             + f"&forms=8-K&dateRange=custom&startdt={s}&enddt={e}&from={frm}")
        b = sec_get(u)
        if b is None:
            return None
        j = json.loads(b)
        hs = j["hits"]["hits"]
        tot = j["hits"]["total"]["value"]
        for h in hs:
            src = h["_source"]
            rows.append({"id": h["_id"], "adsh": src.get("adsh"), "cik": int(src["ciks"][0]) if src.get("ciks") else None,
                         "name": (src.get("display_names") or [""])[0], "file_date": src.get("file_date"),
                         "items": ",".join(src.get("items") or []), "file_type": src.get("file_type"),
                         "form": src.get("form")})
        frm += len(hs)
        if not hs or frm >= tot or frm >= 9900:
            if tot >= 10000:
                log("FTS cap hit", q, s)
            return rows


def stage_fts(name, qs):
    fp = OUT / "fts" / f"{name}.jsonl"
    done = set()
    if fp.exists():
        for line in open(fp, encoding="utf-8"):
            r = json.loads(line)
            if r.get("_done"):
                done.add((r["q"], r["s"]))
    import concurrent.futures as cf
    todo = [(q, s, e) for q in qs for s, e in months() if (q, s) not in done]
    wl = threading.Lock()

    def one(t):
        q, s, e = t
        return t, fts_window(q, s, e)

    with open(fp, "a", encoding="utf-8") as f, cf.ThreadPoolExecutor(4) as ex:
        for i, ((q, s, e), rows) in enumerate(ex.map(one, todo)):
            if rows is None:
                log("FTS fail", q, s)
                continue
            with wl:
                for r in rows:
                    r["q"] = q
                    f.write(json.dumps(r) + "\n")
                f.write(json.dumps({"_done": 1, "q": q, "s": s, "n": len(rows)}) + "\n")
                f.flush()
            if i % 100 == 0:
                log(f"FTS {name} {i}/{len(todo)}")
    log(f"FTS {name} done")


def load_fts(name):
    rows = [json.loads(l) for l in open(OUT / "fts" / f"{name}.jsonl", encoding="utf-8")]
    d = pd.DataFrame([r for r in rows if not r.get("_done")])
    return d


# ------------------------------------------------------------------ prices / bench
_M = {}


def M():
    if not _M:
        z = np.load(K1 / "px_mats.npz", allow_pickle=True)
        for k in ("aopen", "aclose", "close", "dv20"):
            _M[k] = z[k]
        _M["cal"] = pd.to_datetime(z["cal"])
        _M["tick"] = z["tick"]
        _M["tix"] = {t: i for i, t in enumerate(z["tick"])}
        fp = OUT / "edges.npz"
        if fp.exists():
            _M["edges"] = np.load(fp)["e"]
        else:
            T = len(_M["cal"])
            E = np.full((T, 9), np.nan, np.float32)
            for s in range(T):
                dv = _M["dv20"][s]
                ok = (_M["close"][s] >= 3) & (dv >= 1e6) & np.isfinite(dv)
                if ok.sum() >= 100:
                    E[s] = np.nanpercentile(dv[ok], np.arange(10, 100, 10))
            np.savez(fp, e=E)
            _M["edges"] = E
    return _M


def ticker_map():
    j = json.loads((K1 / "company_tickers.json").read_text())
    m = {}
    for v in j.values():
        m.setdefault(int(v["cik_str"]), v["ticker"].upper().replace(".", "-"))
    return m


def exit_px(j, t0, t1, kind):
    """kind 'open': aopen[t1]; 'close': aclose[t1]. 결측이면 t0..t1 마지막 유효 수정종가."""
    m = M()
    v = m["aopen"][t1, j] if kind == "open" else m["aclose"][t1, j]
    if np.isfinite(v):
        return float(v)
    c = m["aclose"][t0:t1 + 1, j]
    f = np.where(np.isfinite(c))[0]
    return float(c[f[-1]]) if len(f) else np.nan


_BC = {}


def bench(t0, t1, kind, k):
    """분위 k(0..9) 동일가중, a=t0-1 우주, open[t0] → (open|close)[t1]. 결측 청산 = 드롭(벤치는 우주 평균)."""
    key = (t0, t1, kind)
    if key not in _BC:
        m = M()
        a = t0 - 1
        dv = m["dv20"][a]
        ok = (m["close"][a] >= 3) & (dv >= 1e6) & np.isfinite(dv)
        e = m["edges"][a]
        x1 = m["aopen"][t1] if kind == "open" else m["aclose"][t1]
        r = x1 / m["aopen"][t0] - 1
        dec = np.searchsorted(e, np.where(np.isfinite(dv), dv, 0))
        out = np.full(10, np.nan)
        fin = ok & np.isfinite(r)
        for q in range(10):
            mk = fin & (dec == q)
            if mk.sum() >= 5:
                out[q] = r[mk].mean()
        iw = m["tix"]["IWM"]
        _BC[key] = (out, float(x1[iw] / m["aopen"][t0, iw] - 1))
    b, iwm = _BC[key]
    return float(b[k]), iwm


def entry_index(dates, pre_open=None):
    """file_date → 다음 세션 인덱스. pre_open True면 당일(거래일일 때) 세션."""
    cal = M()["cal"]
    d = pd.to_datetime(pd.Series(dates)).values
    e = cal.searchsorted(d, side="right")
    if pre_open is not None:
        same = cal.searchsorted(d, side="left")
        istd = (same < len(cal)) & (cal.values[np.minimum(same, len(cal) - 1)] == d)
        e = np.where(np.asarray(pre_open, bool) & istd, same, e)
    return e


def attach(ev, holds, kind_exit="open", extra_windows=None):
    """ev: ticker, e(진입 세션 인덱스). holds → ret/sz/iwm 열. extra_windows: {name: 열(청산 인덱스), kind}."""
    m = M()
    T = len(m["cal"])
    out = []
    for r in ev.itertuples(index=False):
        j = m["tix"].get(r.ticker)
        e = int(r.e)
        if j is None or e < 25 or e >= T:
            continue
        a = e - 1
        c, dv, o = m["close"][a, j], m["dv20"][a, j], m["aopen"][e, j]
        if not (np.isfinite(c) and np.isfinite(dv) and np.isfinite(o)) or c < 3 or dv < 1e6:
            continue
        E = m["edges"][a]
        if not np.isfinite(E).all():
            continue
        k = int(np.searchsorted(E, dv))
        rec = r._asdict()
        rec.update(t0=m["cal"][e], dv20=float(dv), dec=k)
        wins = [(f"{H}", e + H, kind_exit) for H in holds]
        if extra_windows:
            for nm, (col, kd) in extra_windows.items():
                wins.append((nm, int(getattr(r, col)), kd))
        for nm, t1, kd in wins:
            if t1 >= T or t1 < e:
                rec[f"ret{nm}"] = rec[f"sz{nm}"] = rec[f"iwm{nm}"] = np.nan
                continue
            x = exit_px(j, e, t1, kd)
            rec[f"ret{nm}"] = x / float(o) - 1
            rec[f"sz{nm}"], rec[f"iwm{nm}"] = bench(e, t1, kd, k)
        out.append(rec)
    return pd.DataFrame(out)


def dedupe(e, gap=60):
    e = e.sort_values(["ticker", "e"])
    keep, last = [], {}
    for i, (t, p) in enumerate(zip(e.ticker, e.e)):
        lp = last.get(t)
        if lp is None or p - lp >= gap:
            keep.append(i)
            last[t] = p
    return e.iloc[keep]


# ------------------------------------------------------------------ stats
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
    return float(x.mean() / np.sqrt(v / n)) if v > 0 else np.nan


_I1 = {}


def i1_overlap(d):
    if not _I1:
        t = pd.read_csv(ROOT / "data/analysis/model_T1_impl_20260927/trades_I1.csv.gz", parse_dates=["entry_date"])
        cal = M()["cal"]
        t["ei"] = cal.searchsorted(t.entry_date.values)
        _I1["g"] = t.groupby("ticker").ei.apply(lambda s: np.sort(s.values)).to_dict()
        _I1["t"] = t
    cal = M()["cal"]
    ei = cal.searchsorted(pd.to_datetime(d.t0).values)
    hit = []
    for tk, x in zip(d.ticker, ei):
        arr = _I1["g"].get(tk)
        hit.append(arr is not None and bool(((arr >= x - 5) & (arr <= x + 20)).any()))
    return int(np.sum(hit))


def stats(d, nm, lag, bench_col="sz", sign=1):
    col = f"ret{nm}"
    d = d.dropna(subset=[col, f"{bench_col}{nm}"]).copy()
    if len(d) < 5:
        return {"n": int(len(d))}
    d["x"] = d[col] - COST - d[f"{bench_col}{nm}"]
    coh = d.groupby("t0").x.mean().sort_index()
    yrs = coh.groupby(coh.index.year).mean()
    ins, oos = coh[coh.index.year <= IS_END], coh[coh.index.year > IS_END]
    xs = np.sort(d.x.values)[::-1] if sign > 0 else np.sort(d.x.values)
    k = max(1, int(round(len(xs) * 0.1)))
    tot = xs.sum()
    liq = d[d.dv20 >= 5e6]
    lc = liq.groupby("t0").x.mean()
    return {"n": int(len(d)), "dates": int(len(coh)), "mean": float(coh.mean()), "evt_mean": float(d.x.mean()),
            "median": float(d.x.median()), "t": nw_t(coh.values, lag),
            "raw": float(d.groupby("t0")[col].mean().mean()),
            "top10_share": float(xs[:k].sum() / tot) if tot * sign > 0 else None,
            "is_mean": float(ins.mean()) if len(ins) else None, "is_t": nw_t(ins.values, lag) if len(ins) >= 5 else None,
            "is_dates": int(len(ins)),
            "oos_mean": float(oos.mean()) if len(oos) else None, "oos_t": nw_t(oos.values, lag) if len(oos) >= 5 else None,
            "oos_dates": int(len(oos)),
            "years_pos": int((yrs > 0).sum()), "years": int(len(yrs)),
            "by_year": {int(a): round(float(b) * 100, 2) for a, b in yrs.items()},
            "liq_n": int(len(liq)), "liq_mean": float(lc.mean()) if len(lc) else None,
            "liq_t": nw_t(lc.sort_index().values, lag) if len(lc) >= 5 else None,
            "i1_overlap": i1_overlap(d), "win": float((d.x > 0).mean())}


def verdict(s, sign=1):
    if not s or s.get("n", 0) < 5 or s.get("oos_mean") is None or s.get("oos_t") is None:
        return "판정불가(표본)"
    om, ot = sign * s["oos_mean"], sign * (s["oos_t"] or 0)
    ypos = s["years_pos"] if sign > 0 else s["years"] - s["years_pos"]
    liq = s.get("liq_mean")
    if om > 0 and ot >= 2.64 and ypos * 2 > s["years"] and liq is not None and sign * liq > 0:
        return "확정"
    if om > 0 and ot >= 1.65 and sign * (s["is_mean"] or 0) > 0:
        return "후보"
    return "기각"


RES = {}


def report(tag, d, nm, lag, sign=1, primary=False):
    for b in ("sz", "iwm"):
        s = stats(d, nm, lag, b, sign)
        s["verdict"] = verdict(s, sign)
        s["primary"] = primary and b == "sz"
        RES[f"{tag}|{nm}|{b}"] = s
        if b == "sz":
            f = lambda v: "nan" if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:+.4f}"  # noqa
            log(f"{tag:34s} H{nm:>5s} n={s.get('n')} d={s.get('dates')} mean={f(s.get('mean'))} t={f(s.get('t'))} "
                f"IS={f(s.get('is_mean'))} OOS={f(s.get('oos_mean'))} oosT={f(s.get('oos_t'))} "
                f"med={f(s.get('median'))} yrs={s.get('years_pos')}/{s.get('years')} liq={f(s.get('liq_mean'))} "
                f"I1={s.get('i1_overlap')} -> {s['verdict']}")


def map_share(ev, tag, datecol="file_date"):
    tix = M()["tix"]
    y = pd.to_datetime(ev[datecol]).dt.year
    ok = ev.ticker.isin(tix) if "ticker" in ev else pd.Series(False, index=ev.index)
    g = pd.DataFrame({"y": y, "ok": ok}).groupby("y").ok.agg(["size", "mean"])
    RES[f"{tag}|mapshare"] = {int(k): [int(v["size"]), round(float(v["mean"]), 3)] for k, v in g.iterrows()}


# ------------------------------------------------------------------ E1
MON = {m: i for i, m in enumerate(["january", "february", "march", "april", "may", "june", "july", "august",
                                   "september", "october", "november", "december"], 1)}


def pdate(s):
    try:
        return pd.Timestamp(s.strip())
    except Exception:  # noqa
        return None


def ann_date(ref):
    m = re.search(r"indexnews/announcements/(20\d{6})", ref)
    if m:
        return pd.Timestamp(m.group(1)), "sp_url"
    m = re.search(r"press\.spglobal\.com/(20\d\d-\d\d-\d\d)", ref)
    if m:
        return pd.Timestamp(m.group(1)), "sp_press"
    m = re.search(r"\|\s*date\s*=\s*([^|}]+)", ref)
    if m:
        d = pdate(m.group(1))
        if d is not None:
            return d, "ref_date"
    return None, None


def clean_cell(c):
    c = re.sub(r"<ref[^>]*/>", "", c)
    c = re.sub(r"<ref.*?</ref>", "", c, flags=re.S)
    c = re.sub(r"\{\{[^|}]*\|([^}|]*)[^}]*\}\}", r"\1", c)
    c = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", c)
    return c.strip()


def parse_changes(txt, index):
    i = txt.find('id="changes"')
    body = txt[i:]
    body = body[:body.find("\n|}")]
    rows = body.split("\n|-")
    named = {}
    for m in re.finditer(r'<ref name="?([^">/]+)"?\s*>(.*?)</ref>', txt, flags=re.S):
        named[m.group(1).strip()] = m.group(2)
    out = []
    for r in rows[1:]:
        raw = r.strip()
        if not raw or raw.startswith("!"):
            continue
        cells = re.split(r"\n\|\||\n\||\|\|", "\n" + raw)
        cells = [c for c in cells if c.strip() != ""]
        if len(cells) < 5:
            continue
        eff = pdate(clean_cell(cells[0]))
        if eff is None:
            continue
        # 참조 수집
        refs = "".join(re.findall(r"<ref[^>]*>.*?</ref>", raw, flags=re.S))
        for nm in re.findall(r'<ref name="?([^">/]+)"?\s*/>', raw):
            refs += named.get(nm.strip(), "")
        ad, src = ann_date(refs)
        cc = [clean_cell(c) for c in cells]
        add_t, rem_t = cc[1], cc[3] if len(cc) > 3 else ""
        reason = cc[5] if len(cc) > 5 else ""
        out.append({"index": index, "eff": eff, "add": add_t, "rem": rem_t, "reason": reason, "ann": ad, "ann_src": src})
    return pd.DataFrame(out)


def stage_e1():
    pages = {"500": "Historical_components_of_the_S%26P_500", "400": "List_of_S%26P_400_companies",
             "600": "List_of_S%26P_600_companies"}
    frames = []
    for k, p in pages.items():
        fp = OUT / f"wiki_{k}.txt"
        if not fp.exists():
            req = urllib.request.Request(f"https://en.wikipedia.org/w/index.php?title={p}&action=raw",
                                         headers={"User-Agent": UA})
            fp.write_bytes(urllib.request.urlopen(req, timeout=60).read())
        frames.append(parse_changes(fp.read_text(encoding="utf-8"), k))
    ch = pd.concat(frames, ignore_index=True)
    ch.to_csv(OUT / "e1_changes.csv", index=False)
    return ch


def tk(s):
    s = (s or "").strip().upper().replace(".", "-")
    s = re.sub(r"[^A-Z\-]", "", s)
    return s or None


def run_e1():
    ch = stage_e1()
    m = M()
    cal = m["cal"]
    ch["eff_y"] = ch.eff.dt.year
    cov = ch.assign(has=ch.ann.notna()).groupby(["index", "eff_y"]).has.agg(["size", "sum"])
    RES["E1|coverage"] = {f"{a}-{b}": [int(v["size"]), int(v["sum"])] for (a, b), v in cov.iterrows()}
    lag = (ch.eff - ch.ann).dt.days
    RES["E1|ann_to_eff_days"] = lag.describe().round(1).to_dict()
    ch = ch[(ch.eff >= "2009-03-01")]
    # (a) 편입: 발표 다음 세션 시가 → 효력일 직전 세션 종가
    A = ch[ch["add"].map(tk).notna()].copy()
    A["ticker"] = A["add"].map(tk)
    A["file_date"] = A.eff
    map_share(A, "E1_add", "eff")
    ea = A[A.ann.notna() & (A.ann < A.eff)].copy()
    ea["e"] = entry_index(ea.ann)
    ea["x1"] = cal.searchsorted(ea.eff.values, side="left") - 1  # 효력일 직전 세션
    ea["x1b"] = ea.x1 + 1  # 강건성: 효력일 세션 종가
    ea = ea[(ea.x1 >= ea.e) & ((ea.eff - ea.ann).dt.days <= 45)]  # 이탈(품질 가드): 발표-효력 1~45일만
    for ix in ("500", "400", "600", "all"):
        s = ea if ix == "all" else ea[ea["index"] == ix]
        d = attach(s[["ticker", "e", "x1", "x1b", "index"]], (5, 20, 60), extra_windows={"win": ("x1", "close"),
                                                                                           "winb": ("x1b", "close")})
        if len(d):
            report(f"E1a_add_ann_{ix}", d, "win", 5, primary=True)
            report(f"E1a_add_ann_{ix}", d, "winb", 5)
            report(f"E1a_add_ann_{ix}", d, "20", 20)
    # (c) 편입 사후: 효력일 세션 시가 → H
    pa = A.copy()
    pa["e"] = cal.searchsorted(pa.eff.values, side="left")
    for ix in ("500", "400", "600", "all"):
        s = pa if ix == "all" else pa[pa["index"] == ix]
        d = attach(s[["ticker", "e", "index"]], (5, 20, 60))
        if len(d):
            for H in (5, 20, 60):
                report(f"E1c_add_post_{ix}", d, str(H), H, primary=(H == 20))
    # (b) 편출 반등: 사유 시총/지수 이동만
    R = ch[ch["rem"].map(tk).notna()].copy()
    R["ticker"] = R["rem"].map(tk)
    map_share(R, "E1_del", "eff")
    good = R.reason.str.contains(r"market cap|capitaliz|moved|move to|moving|S&P (?:MidCap|SmallCap)|smallcap|midcap|"
                                 r"no longer representative|representative|index maintenance|quarterly rebalanc|"
                                 r"rebalanc|size", case=False, regex=True)
    acq = R.reason.str.contains(r"acqui|merg|bankrupt|spin|take private|taken private|delist|purchase", case=False)
    RES["E1|del_reason_counts"] = {"kept": int((good & ~acq).sum()), "all": int(len(R))}
    R = R[good & ~acq]
    R["e"] = cal.searchsorted(R.eff.values, side="left")
    Rb = R.copy()
    Rb["e"] = Rb.e + 1
    for ix in ("500", "400", "600", "all"):
        s = R if ix == "all" else R[R["index"] == ix]
        d = attach(s[["ticker", "e", "index"]], (5, 20, 60))
        if len(d):
            for H in (5, 20, 60):
                report(f"E1b_del_rebound_{ix}", d, str(H), H, primary=(H == 20))
    d = attach(Rb[["ticker", "e", "index"]], (20,))
    if len(d):
        report("E1b_del_rebound_all_eff+1", d, "20", 20)
    # 편출 발표 → 효력(회피/하락 확인)
    rr = R[R.ann.notna() & (R.ann < R.eff)].copy()
    rr["e"] = entry_index(rr.ann)
    rr["x1"] = cal.searchsorted(rr.eff.values, side="left") - 1
    rr = rr[(rr.x1 >= rr.e) & ((rr.eff - rr.ann).dt.days <= 45)]
    d = attach(rr[["ticker", "e", "x1", "index"]], (20,), extra_windows={"win": ("x1", "close")})
    if len(d):
        report("E1d_del_ann_window_all", d, "win", 5, sign=-1)


# ------------------------------------------------------------------ acceptance times (jev cache)
def accept_map():
    fp = OUT / "accept_map.csv.gz"
    if fp.exists():
        return pd.read_csv(fp)
    rows = []
    for f in (JEV / "submissions_full").glob("*.json"):
        try:
            d = pd.read_json(f, dtype=False)
        except Exception:  # noqa
            continue
        if "acceptanceDateTime" not in d:
            continue
        d = d[d.form == "8-K"]
        rows.append(d[["accessionNumber", "acceptanceDateTime"]])
    a = pd.concat(rows).rename(columns={"accessionNumber": "adsh", "acceptanceDateTime": "acc"})
    a.to_csv(fp, index=False, compression="gzip")
    return a


def pre_open_flag(ev):
    a = accept_map().drop_duplicates("adsh").set_index("adsh").acc
    t = pd.to_datetime(ev.adsh.map(a), utc=True, errors="coerce").dt.tz_convert("America/New_York")
    po = (t.dt.hour * 60 + t.dt.minute) < 9 * 60 + 30
    same = t.dt.strftime("%Y-%m-%d") == ev.file_date
    return (po & same).fillna(False).values, t.notna().values


# ------------------------------------------------------------------ E2 docs
def doc_url(cik, id_):
    adsh, fn = id_.split(":", 1)
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{adsh.replace('-', '')}/{fn}"


TAG = re.compile(r"<[^>]+>")
SENT = re.compile(r"(?<=[.;])\s+(?=[A-Z])")
AMT = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(million|billion|mm|bn)?", re.I)
SHR = re.compile(r"(?<![$\d,.])(?<!\$ )(\d[\d,]*(?:\.\d+)?)\s*(million)?\s+(?:of\s+(?:its|the company's|our)\s+)?"
                 r"(?:outstanding\s+)?(?:shares|common shares|of\s[^$]{0,80}?common (?:stock|shares))", re.I)
PCT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s+of\s+(?:the\s+|its\s+|our\s+|the company's\s+)?"
                 r"(?:issued and\s+)?outstanding", re.I)
PROG = re.compile(r"(?:share|stock) (?:re)?purchase|repurchase (?:program|plan|authori[sz]ation)|buy-?\s?back|"
                  r"repurchase (?:of )?up to|authori[sz]ed the repurchase|repurchase of (?:its|the company's|our) "
                  r"(?:common|outstanding|shares)", re.I)


def html_text(b):
    import html as _h
    t = b.decode("latin-1", "ignore")
    t = re.sub(r"(?is)<(script|style).*?</>", " ", t)
    t = TAG.sub(" ", t)
    t = _h.unescape(t).replace(" ", " ")
    return re.sub(r"\s+", " ", t)


def snippet(text):
    out = [s[:1500] for s in SENT.split(text) if re.search(r"repurchase|buyback|buy back|buy-back", s, re.I)]
    return " || ".join(out)[:8000]


def num(s):
    return float(s.replace(",", ""))


def parse_buyback(snip, fyear=None):
    """스니펫(재매입 언급 문장들)에서 '이번' 승인 문장 첫 건 → 규모. 과거 연도 언급 문장·발표 동사 없는 문장 제외."""
    for s in (snip or "").split(" || "):
        if not (PROG.search(s) and re.search(r"authori[sz]|approv", s, re.I)):
            continue
        if not re.search(r"announc|today|has (?:authori[sz]ed|approved)|board of directors (?:has )?(?:authori[sz]|approv)|"
                         r"board\b[^|]{0,150}?(?:authori[sz]|approv)", s, re.I):
            continue
        if fyear and any(int(y) < fyear for y in re.findall(r"\b(20[01]\d|202\d)\b", s)):
            continue
        if re.search(r"remain(?:s|ed|ing)? (?:available|under)|previously (?:authori[sz]ed|announced)", s, re.I) and                 not re.search(r"\bnew\b|additional|increase", s, re.I):
            continue
        if re.search(r"indenture|convertible notes|warrant", s, re.I) and not re.search(r"repurchase program", s, re.I):
            continue
        usd = []
        for m in AMT.finditer(s):
            v = num(m.group(1))
            u = (m.group(2) or "").lower()
            v *= 1e9 if u in ("billion", "bn") else (1e6 if u in ("million", "mm") else 1)
            if v >= 1e5:
                usd.append(v)
        sh = []
        for m in SHR.finditer(s):
            v = num(m.group(1)) * (1e6 if m.group(2) else 1)
            if v >= 1e4:
                sh.append(v)
        pc = [num(m.group(1)) for m in PCT.finditer(s) if 0 < num(m.group(1)) <= 50]
        return {"sent": s[:400], "usd": max(usd) if usd else None, "shares": max(sh) if sh else None,
                "pct": max(pc) if pc else None, "new_kw": bool(re.search(r"\bnew\b|additional|increase", s, re.I))}
    return None


def stage_e2_docs():
    ev = e2_events(raw=True)
    fp = OUT / "e2_docs.jsonl"
    done = set()
    if fp.exists():
        done = {json.loads(l)["adsh"] for l in open(fp, encoding="utf-8")}
    todo = ev[~ev.adsh.isin(done)].copy()
    # 주 표본(2.02 없음) 먼저, 2.02 동반(강건성)은 무작위 순서로 뒤에
    todo["h"] = todo["items"].fillna("").str.split(",").map(lambda x: "2.02" in x)
    todo = pd.concat([todo[~todo.h], todo[todo.h].sample(frac=1, random_state=0)])
    log(f"E2 docs todo {len(todo)} (done {len(done)})")
    import concurrent.futures as cf

    def one(r):
        b = sec_get(doc_url(r.cik, r.id))
        return {"adsh": r.adsh, "ok": b is not None, "snip": snippet(html_text(b)) if b else None}

    with open(fp, "a", encoding="utf-8") as f, cf.ThreadPoolExecutor(3) as ex:
        for i, res in enumerate(ex.map(one, todo.itertuples(index=False))):
            f.write(json.dumps(res) + "\n")
            if i % 200 == 0:
                f.flush()
                log(f"E2 docs {i}/{len(todo)}")


def e2_events(raw=False):
    d = load_fts("e2")
    d = d[d.form == "8-K"]
    tm = ticker_map()
    # 문서 선택: adsh별 EX-99 우선
    ft = d.file_type.fillna("")
    d["pri"] = np.where(ft.str.startswith("EX-99"), 0, np.where(ft.str.startswith("8-K"), 1, 2))
    d = d.sort_values(["adsh", "pri"]).drop_duplicates("adsh")
    d["ticker"] = d.cik.map(tm)
    if raw:
        tix = M()["tix"]
        return d[d.ticker.isin(tix)]
    return d


_SH = {}


def shares_out():
    if _SH:
        return _SH["d"]
    fp = OUT / "shares_frames.csv.gz"
    if not fp.exists():
        rows = []
        for y in range(2009, 2027):
            for q in range(1, 5):
                if (y, q) > (2026, 3):
                    break
                b = sec_get(f"https://data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/CY{y}Q{q}I.json")
                if not b:
                    log("frames miss", y, q)
                    continue
                for r in json.loads(b)["data"]:
                    rows.append({"cik": r["cik"], "end": r["end"], "val": r["val"]})
        pd.DataFrame(rows).to_csv(fp, index=False, compression="gzip")
    d = pd.read_csv(fp, parse_dates=["end"]).sort_values("end")
    _SH["d"] = d
    return d


def shares_asof(cik, date):
    d = shares_out()
    if "g" not in _SH:
        _SH["g"] = {k: (v.end.values, v.val.values) for k, v in d.groupby("cik")}
    g = _SH["g"].get(int(cik))
    if g is None:
        return np.nan
    i = np.searchsorted(g[0], np.datetime64(pd.Timestamp(date)), side="right") - 1
    if i < 0:
        return np.nan
    if (pd.Timestamp(date) - pd.Timestamp(g[0][i])).days > 550:
        return np.nan
    return float(g[1][i])


def run_e2():
    ev = e2_events()
    map_share(ev, "E2")
    L = []
    for l in open(OUT / "e2_docs.jsonl", encoding="utf-8"):
        try:
            L.append(json.loads(l))
        except Exception:  # noqa  (수집 중 마지막 줄)
            pass
    docs = pd.DataFrame(L).drop_duplicates("adsh")
    fy = docs.adsh.map(ev.drop_duplicates("adsh").set_index("adsh").file_date.str[:4].astype(int))
    P = [parse_buyback(x, int(y) if y == y else None) or {"sent": None} for x, y in zip(docs.snip, fy)]
    docs = pd.concat([docs[["adsh", "ok"]].reset_index(drop=True), pd.DataFrame(P)], axis=1)
    RES["E2|docs"] = {"fetched": int(len(docs)), "ok": int(docs.ok.sum()), "auth_sentence": int(docs.sent.notna().sum())}
    ev = ev.merge(docs, on="adsh", how="inner")
    ev = ev[ev.sent.notna()].copy()
    ev["has202"] = ev["items"].fillna("").str.split(",").map(lambda x: "2.02" in x)
    po, has_t = pre_open_flag(ev)
    ev["e"] = entry_index(ev.file_date)
    ev["e_acc"] = entry_index(ev.file_date, po)
    m = M()
    ev = ev[ev.e < len(m["cal"])]
    a = ev.e.values - 1
    jj = ev.ticker.map(m["tix"]).values.astype(int)
    px = m["close"][a, jj]
    ev["so"] = [shares_asof(c, d) for c, d in zip(ev.cik, ev.file_date)]
    ev["mcap"] = px * ev.so
    usd_sz = ev.usd / ev.mcap
    sh_sz = ev.shares / ev.so
    ev["size"] = np.where(ev.pct.notna(), ev.pct / 100, np.where(ev.usd.notna(), usd_sz, sh_sz))
    ev.loc[(ev["size"] > 1) | (ev["size"] <= 0), "size"] = np.nan
    RES["E2|size_cov"] = {"n": int(len(ev)), "size_ok": int(ev["size"].notna().sum()),
                          "ge5": int((ev["size"] >= .05).sum()), "ge10": int((ev["size"] >= .10).sum())}
    ev.to_csv(OUT / "e2_events.csv.gz", index=False, compression="gzip")
    main = dedupe(ev[~ev.has202])
    # 2년 내 첫 승인
    evs = ev.sort_values(["ticker", "e"])
    prev = evs.groupby("ticker").e.shift()
    evs["first2y"] = prev.isna() | (evs.e - prev >= 504)
    first = set(evs[evs.first2y].adsh)
    D = attach(main[["ticker", "e", "size", "adsh"]], (5, 20, 60))
    for H in (5, 20, 60):
        report("E2_all_no202", D, str(H), H, primary=(H == 20))
    for thr in (0.05, 0.10):
        s = D[D["size"] >= thr]
        for H in (5, 20, 60):
            report(f"E2_size>={int(thr * 100)}%", s, str(H), H, primary=(H == 20))
    report("E2_small_dv(dec<=4)", D[D.dec <= 4], "20", 20)
    report("E2_small_dv&size>=5%", D[(D.dec <= 4) & (D["size"] >= .05)], "20", 20)
    report("E2_first_in_2y", D[D.adsh.isin(first)], "20", 20)
    report("E2_size_missing", D[D["size"].isna()], "20", 20)
    D2 = attach(dedupe(ev[ev.has202])[["ticker", "e", "size", "adsh"]], (20,))
    report("E2_with202(robust)", D2, "20", 20)
    RES["E2|accept_time_cov"] = float(has_t.mean())
    acc = dedupe(ev[~ev.has202].assign(e=ev.e_acc[~ev.has202]))
    Da = attach(acc[["ticker", "e", "size", "adsh"]], (20,))
    report("E2_no202_accept_time(robust)", Da, "20", 20)
    report("E2_no202_accept_time_size>=5%", Da[Da["size"] >= .05], "20", 20)


# ------------------------------------------------------------------ E4
# ================================================================== v2: E2 추출기 수정(검증 200건에서 드러난 오류 유형 일반화)
RETRO = re.compile(r"during the (?:quarter|year|period)|completion of|completed|has completed|previously|existing|"
                   r"under (?:the|its|our) (?:current|existing)? ?(?:share |stock )?(?:re)?purchase (?:program|plan)|"
                   r"since (?:its )?inception|as amended|cumulative|remain(?:s|ed|ing)?\b|10b5-1|recent(?:ly)? announce|"
                   r"announced (?:last|on|in) |extended|extension|notes to|condensed consolidated", re.I)
AGM = re.compile(r"shareholder approval|stockholder approval|general meeting|annual meeting|management board|"
                 r"supervisory board|our shareholders|shareholders (?:authori[sz]ed|approved|adopted)|normal course issuer", re.I)
TOTALW = re.compile(r"\btotal\b|aggregate|bringing|raising|increas\w* (?:the )?(?:\w+ )?(?:authori[sz]ation|amount|program)?\s*"
                    r"(?:\w+ )?to \$|for a total|up to an aggregate", re.I)
BADAMT = re.compile(r"^\s*(?:million|billion)?\s*(?:bank|financial|savings)?\s*holding company|^\s*(?:million|billion)?\s*"
                    r"(?:in|of) (?:annual |net )?(?:revenue|sales|assets|net income|cash|debt|notes)", re.I)
MON = "January|February|March|April|May|June|July|August|September|October|November|December"
DATE_RE = re.compile(rf"\b({MON})\s+(\d{{1,2}})?,?\s*(20\d\d)")
OUTSH = re.compile(r"^\s*(?:million\s+)?(?:shares\s+)?(?:of\s+(?:its|the company's|our)\s+)?(?:common\s+)?(?:stock\s+)?"
                   r"(?:shares\s+)?outstanding|^\s*(?:million\s+)?shares\s+(?:of\s+common\s+stock\s+)?outstanding", re.I)


def parse_buyback_v2(snip, fdate):
    fd = pd.Timestamp(fdate)
    for s in (snip or "").split(" || "):
        if len(s) > 900 or not (PROG.search(s) and re.search(r"authori[sz]|approv", s, re.I)):
            continue
        if not re.search(r"announc|today|has (?:authori[sz]ed|approved)|board\b[^|]{0,150}?(?:authori[sz]|approv)", s, re.I):
            continue
        if any(int(y) < fd.year for y in re.findall(r"\b(20[01]\d|202\d)\b", s)):
            continue
        old = False
        for m in DATE_RE.finditer(s):
            try:
                d = pd.Timestamp(f"{m.group(1)} {m.group(2) or 1} {m.group(3)}")
            except Exception:  # noqa
                continue
            if (fd - d).days > 45:
                old = True
        if old or AGM.search(s):
            continue
        new_kw = bool(re.search(r"\bnew\b|additional|increase", s, re.I))
        if RETRO.search(s) and not new_kw:
            continue
        usd = []
        for m in AMT.finditer(s):
            if BADAMT.search(s[m.end():m.end() + 60]) or re.search(r"per share|a share", s[m.end():m.end() + 25], re.I):
                continue
            v = num(m.group(1))
            u = (m.group(2) or "").lower()
            v *= 1e9 if u in ("billion", "bn") else (1e6 if u in ("million", "mm") else 1)
            if v >= 1e5:
                usd.append(v)
        sh = []
        for m in SHR.finditer(s):
            tail = s[m.start() + len(m.group(1)):m.start() + len(m.group(1)) + 70]
            head = s[max(0, m.start() - 40):m.start()]
            if OUTSH.search(tail) or re.search(r"(?:down )?to (?:approximately )?$|of the (?:approximately )?$|of its $", head, re.I):
                continue
            v = num(m.group(1)) * (1e6 if m.group(2) else 1)
            if v >= 1e4:
                sh.append(v)
        pc = [num(m.group(1)) for m in PCT.finditer(s) if 0 < num(m.group(1)) <= 50]
        tot = bool(TOTALW.search(s)) and (len(usd) >= 2 or len(sh) >= 2)
        pick = min if tot else max
        return {"sent": s[:400], "usd": pick(usd) if usd else None, "shares": pick(sh) if sh else None,
                "pct": pc[0] if pc else None, "new_kw": new_kw}
    return None


_SPL = {}


def split_after(tkr, date):
    """date 이후 분할 누적배수(yfinance Close는 분할 소급조정 → 당시 원주가 = 조정종가 × 이 값). T3 캐시 밖 티커는 NaN."""
    if not _SPL:
        s = pd.read_csv(OUT / "splits_t3.csv.gz", parse_dates=["date"])
        _SPL["g"] = {k: v for k, v in s.groupby("ticker")}
        _SPL["cov"] = set(json.loads((OUT / "t3_tickers.json").read_text()))
    if tkr not in _SPL["cov"]:
        return np.nan
    g = _SPL["g"].get(tkr)
    if g is None:
        return 1.0
    return float(np.prod(g[g.date > pd.Timestamp(date)].split.values)) if len(g) else 1.0


def e2v2_build():
    ev = e2_events()
    L = []
    for l in open(OUT / "e2_docs.jsonl", encoding="utf-8"):
        try:
            L.append(json.loads(l))
        except Exception:  # noqa
            pass
    docs = pd.DataFrame(L).drop_duplicates("adsh")
    fdm = ev.drop_duplicates("adsh").set_index("adsh").file_date
    docs["fd"] = docs.adsh.map(fdm)
    docs = docs[docs.fd.notna()]
    P = [parse_buyback_v2(x, f) or {"sent": None} for x, f in zip(docs.snip, docs.fd)]
    docs = pd.concat([docs[["adsh", "ok"]].reset_index(drop=True), pd.DataFrame(P)], axis=1)
    ev = ev.merge(docs, on="adsh", how="inner")
    ev = ev[ev.sent.notna()].copy()
    ev["has202"] = ev["items"].fillna("").str.split(",").map(lambda x: "2.02" in x)
    ev["e"] = entry_index(ev.file_date)
    m = M()
    ev = ev[ev.e < len(m["cal"])].copy()
    a = ev.e.values - 1
    jj = ev.ticker.map(m["tix"]).values.astype(int)
    ev["adjclose"] = m["close"][a, jj]
    ev["splitf"] = [split_after(t, m["cal"][x]) for t, x in zip(ev.ticker, a)]
    ev["so"] = [shares_asof(c, d) for c, d in zip(ev.cik, ev.file_date)]
    ev["mcap_raw"] = ev.adjclose * ev.splitf * ev.so
    usd_sz = ev.usd / ev.mcap_raw
    sh_sz = ev.shares / ev.so
    ev["size"] = np.where(ev.pct.notna(), ev.pct / 100, np.where(ev.usd.notna(), usd_sz, sh_sz))
    ev.loc[(ev["size"] > 0.5) | (ev["size"] <= 0), "size"] = np.nan
    ev.to_csv(OUT / "e2v2_events.csv.gz", index=False, compression="gzip")
    return ev


def e2v2_validate():
    """검증 표본에서 v1 vs v2: 유효성 판정 일치, 금액 정확, ≥10% 버킷 재분류."""
    V = pd.read_csv(OUT / "e2_val_labels.csv")
    sn = {}
    for l in open(OUT / "e2_docs.jsonl", encoding="utf-8"):
        try:
            j = json.loads(l)
            sn[j["adsh"]] = j.get("snip")
        except Exception:  # noqa
            pass
    r2 = [parse_buyback_v2(sn.get(a), f) for a, f in zip(V.adsh, V.file_date)]
    V["v2_keep"] = [x is not None for x in r2]
    V["v2_usd"] = [x and x["usd"] for x in r2]
    V["v2_sh"] = [x and x["shares"] for x in r2]
    out = {"n": int(len(V)), "labels_valid": int(V.valid_new.sum()),
           "v1_precision": float(V.valid_new.mean()),
           "v2_kept": int(V.v2_keep.sum()),
           "v2_precision": float(V[V.v2_keep].valid_new.mean()),
           "v2_recall": float(V[V.valid_new == 1].v2_keep.mean()),
           "v2_rejected_invalid": int(((~V.v2_keep) & (V.valid_new == 0)).sum()),
           "v2_rejected_valid": int(((~V.v2_keep) & (V.valid_new == 1)).sum()),
           "by_class_kept": V.groupby("cls").v2_keep.agg(["size", "sum"]).to_dict("index")}
    # 금액 수정: T(총액) 오류가 v2에서 바뀌었나
    T = V[V.cls.isin(["T", "C", "E"])]
    out["amount_err_changed"] = int(((T.v2_usd.fillna(-1) != T.usd.fillna(-1)) | (T.v2_sh.fillna(-1) != T.shares.fillna(-1))).sum())
    out["amount_err_n"] = int(len(T))
    # 분모(분할) 영향: 표본 usd형
    m = M()
    U = V[V.typ == "usd"].copy()
    ei = entry_index(U.file_date) - 1
    U["splitf"] = [split_after(t, m["cal"][x]) for t, x in zip(U.ticker, ei)]
    out["usd_split_factor_ne1"] = int((U.splitf.fillna(1) != 1).sum())
    out["usd_n"] = int(len(U))
    out["usd_ge10_before"] = int((U["size"] >= .1).sum())
    out["usd_ge10_after"] = int(((U["size"] / U.splitf) >= .1).sum())
    V.to_csv(OUT / "e2_val_labels_v2.csv", index=False)
    RES["v2|E2_validation"] = out
    log("v2 E2 validation", out)
    return out


def run_e2v2():
    e2v2_validate()
    ev = e2v2_build()
    RES["v2|E2_cov"] = {"n": int(len(ev)), "size_ok": int(ev["size"].notna().sum()),
                        "split_cov": float(ev.splitf.notna().mean()),
                        "ge10": int((ev["size"] >= .1).sum()), "ge10_no202": int(((ev["size"] >= .1) & ~ev.has202).sum())}
    main = dedupe(ev[~ev.has202])
    D = attach(main[["ticker", "e", "size", "adsh", "splitf"]], (20, 60))
    qstat(D[D["size"] >= .10], 60, "E2v2_C-E2_size>=10%")
    qstat(D[(D["size"] >= .10) & D.splitf.notna()], 60, "E2v2_size>=10%_splitcovered")
    qstat(D[(D["size"] >= .05) & (D["size"] < .10)], 60, "E2v2_size5-10%")
    qstat(D, 60, "E2v2_all_no202")
    qstat(D[D["size"] >= .10], 20, "E2v2_C-E2_size>=10%")
    D2 = attach(dedupe(ev[ev.has202])[["ticker", "e", "size", "adsh"]], (60,))
    qstat(D2[D2["size"] >= .10], 60, "E2v2_with202_size>=10%")
    RES["v2|E2_with202_docs_done"] = int(len(ev[ev.has202]))


def run_e4():
    d = load_fts("e4")
    d = d[d.form == "8-K"].drop_duplicates("adsh")
    tm = ticker_map()
    d["ticker"] = d.cik.map(tm)
    map_share(d, "E4")
    m = M()
    d = d[d.ticker.isin(m["tix"])].copy()
    d["e"] = entry_index(d.file_date)
    d = d[d.e < len(m["cal"])]
    d["has202"] = d["items"].fillna("").str.split(",").map(lambda x: "2.02" in x)
    ratio_ok = []
    for tkr, e in zip(d.ticker, d.e):
        j = m["tix"][tkr]
        a = e - 1
        lo = max(0, a - 252)
        c, ac = m["close"][lo:a + 1, j], m["aclose"][lo:a + 1, j]
        f = np.isfinite(c) & np.isfinite(ac) & (c > 0)
        if f.sum() < 200:
            ratio_ok.append(None)
            continue
        r = (ac[f] / c[f]).astype(float)
        ratio_ok.append(bool(np.nanmax(np.abs(r[1:] / r[:-1] - 1)) < 2e-4))
    d["no_prior_div"] = ratio_ok
    RES["E4|validate"] = {"mapped": int(len(d)), "no_prior_div": int((d.no_prior_div == True).sum()),  # noqa
                          "had_div": int((d.no_prior_div == False).sum()), "short_hist": int(d.no_prior_div.isna().sum())}  # noqa
    d.to_csv(OUT / "e4_events.csv.gz", index=False, compression="gzip")
    main = dedupe(d[(d.no_prior_div == True) & ~d.has202])  # noqa
    D = attach(main[["ticker", "e"]], (5, 20, 60))
    for H in (5, 20, 60):
        report("E4_init_confirmed_no202", D, str(H), H, primary=(H == 20))
    D2 = attach(dedupe(d[(d.no_prior_div == True)])[["ticker", "e"]], (20,))  # noqa
    report("E4_init_confirmed_incl202", D2, "20", 20)
    D3 = attach(dedupe(d[~d.has202])[["ticker", "e"]], (20,))
    report("E4_all_hits_no202(unvalidated)", D3, "20", 20)
    po, _ = pre_open_flag(main)
    Da = attach(main.assign(e=entry_index(main.file_date, po))[["ticker", "e"]], (20,))
    report("E4_accept_time(robust)", Da, "20", 20)


# ------------------------------------------------------------------ E3 / E5 (full-index)
LINE_RE = re.compile(r"^\s*(.*?)\s+(\d+)\s+(\d{4}-\d{2}-\d{2})\s+(edgar/\S+)\s*$")


def idx_forms(forms, tag):
    fp = OUT / f"idx_{tag}.csv.gz"
    if fp.exists():
        return pd.read_csv(fp)
    rows = []
    for f in sorted((K1 / "idx").glob("*.gz")):
        txt = gzip.decompress(f.read_bytes()).decode("latin-1")
        for line in txt.splitlines():
            for fm in forms:
                if line.startswith(fm + " "):
                    mm = LINE_RE.match(line[len(fm):])
                    if mm:
                        name, cik, dt, fn = mm.groups()
                        rows.append((fm, name.strip(), int(cik), dt, fn))
                    break
    d = pd.DataFrame(rows, columns=["form", "name", "cik", "file_date", "file"])
    d.to_csv(fp, index=False, compression="gzip")
    return d


def run_e3():
    d = idx_forms(["10-12B/A", "10-12B"], "e3")
    g = d.groupby("cik").agg(first=("file_date", "min"), name=("name", "first")).reset_index()
    tm = ticker_map()
    g["ticker"] = g.cik.map(tm)
    g["file_date"] = g["first"]
    map_share(g, "E3")
    m = M()
    cal = m["cal"]
    rows = []
    for r in g.itertuples():
        j = m["tix"].get(r.ticker)
        if j is None:
            continue
        f0 = cal.searchsorted(pd.Timestamp(r.first))
        fin = np.where(np.isfinite(m["aopen"][:, j]))[0]
        if not len(fin):
            continue
        first = fin[0]
        if first <= 20 or first < f0 or first - f0 > 400:
            continue
        rows.append({"ticker": r.ticker, "e": first + 1, "first_trade": cal[first], "name": r.name})
    ev = pd.DataFrame(rows)
    RES["E3|n_spincos_with_px"] = int(len(ev))
    ev.to_csv(OUT / "e3_events.csv.gz", index=False, compression="gzip")
    # dv20 필요 → 첫 거래 후 20세션 전엔 dv20 NaN. 사전등록 이탈 방지: dv20 결측이면 첫 20세션 평균 불가 → 진입 e를 dv20 가능 시점까지 미룸?
    # 사전등록 규칙은 '둘째 세션 시가'. dv20(min_periods=15) 결측 시 attach 필터 탈락 → 대체: 필터·분위를 진입 20세션 뒤 값으로 대신하지 않고
    # 벤치 분위만 '첫 15세션 뒤 dv20'으로 산정하는 별도 경로를 쓴다(아래).
    D = attach_spin(ev, (20, 60, 120))
    for H in (20, 60, 120):
        report("E3_spinco_day2", D, str(H), H, primary=(H == 60))
    ev2 = ev.copy()
    ev2["e"] = ev2.e + 15  # 강건성: dv20 가용 시점(첫 거래 +16세션) 진입
    D2 = attach(ev2[["ticker", "e"]], (20, 60, 120))
    for H in (60, 120):
        report("E3_spinco_day16(robust)", D2, str(H), H)


def attach_spin(ev, holds):
    """SpinCo: dv20 결측(상장 직후) → 분위는 첫 15세션 평균 거래대금으로, 필터는 첫날 종가 ≥ $3."""
    m = M()
    T = len(m["cal"])
    out = []
    for r in ev.itertuples(index=False):
        j = m["tix"][r.ticker]
        e = int(r.e)
        f = e - 1
        if e + 16 >= T:
            continue
        o = m["aopen"][e, j]
        if not np.isfinite(o) or m["close"][f, j] < 3:
            continue
        # 분위: 첫날 종가×(첫 15세션 dv20 근사가 없음) → dv20[e+15] 사용 — 룩어헤드이나 벤치 분위 배정에만 쓰고 수익률엔 안 씀
        dv = m["dv20"][min(e + 15, T - 1), j]
        E = m["edges"][e - 1]
        if not (np.isfinite(dv) and np.isfinite(E).all()):
            continue
        k = int(np.searchsorted(E, dv))
        rec = r._asdict()
        rec.update(t0=m["cal"][e], dv20=float(dv), dec=k)
        for H in holds:
            t1 = e + H
            if t1 >= T:
                rec[f"ret{H}"] = rec[f"sz{H}"] = rec[f"iwm{H}"] = np.nan
                continue
            rec[f"ret{H}"] = exit_px(j, e, t1, "open") / float(o) - 1
            rec[f"sz{H}"], rec[f"iwm{H}"] = bench(e, t1, "open", k)
        out.append(rec)
    return pd.DataFrame(out)


def run_e5():
    tm = ticker_map()
    m = M()
    d = idx_forms(["424B4", "424B5"], "e5")
    d["ticker"] = d.cik.map(tm)
    map_share(d, "E5_424B")
    d = d[d.ticker.isin(m["tix"])].copy()
    d["e"] = entry_index(d.file_date)
    d = d[d.e < len(m["cal"])]
    D = attach(dedupe(d)[["ticker", "e", "form"]], (5, 20, 60))
    for H in (5, 20, 60):
        report("E5_offering_424B4/5", D, str(H), H, sign=-1, primary=(H == 20))
    report("E5_offering_424B4_only", D[D.form == "424B4"], "20", 20, sign=-1)
    r = load_fts("e5")
    r = r[r.form == "8-K"].drop_duplicates("adsh")
    r["ticker"] = r.cik.map(tm)
    map_share(r, "E5_rsplit")
    r = r[r.ticker.isin(m["tix"])].copy()
    r["e"] = entry_index(r.file_date)
    r = r[r.e < len(m["cal"])]
    DR = attach(dedupe(r)[["ticker", "e"]], (5, 20, 60))
    for H in (5, 20, 60):
        report("E5_reverse_split_8K", DR, str(H), H, sign=-1, primary=(H == 20))
    # T1 I1 오버레이
    t = pd.read_csv(ROOT / "data/analysis/model_T1_impl_20260927/trades_I1.csv.gz", parse_dates=["entry_date"])
    t["net"] = t.gA20 - COST
    cal = m["cal"]
    t["ei"] = cal.searchsorted(t.entry_date.values)
    out = {}
    for nm, src in (("424B4/5", d), ("rsplit", r)):
        g = src.groupby("ticker").e.apply(lambda s: np.sort(s.values)).to_dict()
        fl = []
        for tkr, ei in zip(t.ticker, t.ei):
            arr = g.get(tkr)
            fl.append(arr is not None and bool(((arr >= ei - 21) & (arr <= ei)).any()))
        t["flag"] = fl
        a = t[t.flag].groupby("entry_date").net.mean()
        b = t[~t.flag].groupby("entry_date").net.mean()
        j = pd.concat([a, b], axis=1, keys=["f", "n"]).dropna()
        diff = j.f - j.n
        out[nm] = {"flag_n": int(t.flag.sum()), "flag_mean": float(t[t.flag].net.mean()) if t.flag.any() else None,
                   "unflag_mean": float(t[~t.flag].net.mean()), "paired_dates": int(len(diff)),
                   "diff_mean": float(diff.mean()) if len(diff) else None, "diff_t": nw_t(diff.values, 1) if len(diff) >= 5 else None,
                   "flag_oos_mean": float(t[t.flag & (t.entry_date.dt.year > IS_END)].net.mean()) if t.flag.any() else None}
        log("E5 overlay", nm, out[nm])
    RES["E5|T1_overlay"] = out


def evaluate(which):
    fp = OUT / "results.json"
    if fp.exists():
        RES.update(json.loads(fp.read_text(encoding="utf-8")))
    for w in which:
        {"e1": run_e1, "e2": run_e2, "e3": run_e3, "e4": run_e4, "e5": run_e5, "e3x": run_e3x, "e2v2": run_e2v2, "e2val": e2v2_validate}[w]()
        fp.write_text(json.dumps(RES, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


# ================================================================== v2: E3 생존편향 확장
XT = re.compile(r"\((?:NYSE|NASDAQ|Nasdaq|NasdaqGS|NasdaqGM|NasdaqCM|NYSE American|NYSE MKT|NYSE Amex|AMEX|NYSE Arca)"
                r"(?:\s*Global Select Market|\s*Global Market|\s*Capital Market)?\s*:\s*\"?([A-Z]{1,5}(?:[.\-][A-Z])?)\"?\s*\)")
TS = re.compile(r"Trading\s+Symbol\(?s?\)?\s+Name of each exchange[^A-Za-z]*(?:[^|]{0,120}?)\b([A-Z]{1,5})\s+(?:The\s+)?"
                r"(?:New York Stock Exchange|NYSE|Nasdaq|NASDAQ)", re.S)


GENERIC = {"the", "new", "inc", "inc.", "holdings", "holding", "corp", "corp.", "corporation", "group", "american", "first",
           "national", "international", "global", "company", "co", "co.", "ltd", "ltd.", "llc", "plc", "n.v.", "spinco",
           "united", "general", "north", "us", "u.s.", "trust", "capital", "energy", "financial", "services", "&", "and"}


def name_keys(names):
    ks = set()
    for n in names:
        if not isinstance(n, str):
            continue
        w = [x for x in re.split(r"[\s,]+", n.lower()) if x and x not in GENERIC and len(x) >= 3]
        if w:
            ks.add(w[0])
    return ks


def e3x_meta():
    """미매핑 10-12B CIK → submissions(name, formerNames, 25/15 날짜, 첫 8-K/10-Q/10-K 목록) + 문서에서 티커 복원."""
    fp = OUT / "e3x_meta.jsonl"
    done = set()
    if fp.exists():
        done = {json.loads(l)["cik"] for l in open(fp, encoding="utf-8")}
    u = pd.read_csv(OUT / "e3_unmapped.csv")
    import collections
    with open(fp, "a", encoding="utf-8") as f:
        for r in u.itertuples():
            if r.cik in done:
                continue
            b = sec_get(f"https://data.sec.gov/submissions/CIK{int(r.cik):010d}.json")
            rec = {"cik": int(r.cik), "first10": r.first, "name10": r.name}
            if not b:
                rec["err"] = "no_submissions"
                f.write(json.dumps(rec) + "\n"); continue
            j = json.loads(b)
            rr = pd.DataFrame(j["filings"]["recent"])
            rec.update(name=j.get("name"), tickers=j.get("tickers"), former=[x["name"] for x in j.get("formerNames", [])],
                       sic=j.get("sic"), forms=sorted(set(rr.form)) if len(rr) else [])
            d25 = rr[rr.form.isin(["25-NSE", "25", "15-12B", "15-12G", "15-15D"])].filingDate
            rec["delist"] = d25.min() if len(d25) else None
            keys = name_keys([r.name, j.get("name")] + rec["former"])
            rec["keys"] = sorted(keys)
            cand = rr[(rr.form.isin(["8-K", "10-Q", "10-K"])) & (rr.filingDate >= r.first)].sort_values("filingDate")
            cnt = collections.Counter()
            for c in cand.head(4).itertuples():
                acc = c.accessionNumber.replace("-", "")
                ib = sec_get(f"https://www.sec.gov/Archives/edgar/data/{int(r.cik)}/{acc}/index.json")
                if not ib:
                    continue
                items = [x["name"] for x in json.loads(ib)["directory"]["item"]]
                docs = [c.primaryDocument] + [x for x in items if re.search(r"ex-?99|ex99|pr\d|press", x, re.I)
                                              and x.lower().endswith((".htm", ".html", ".txt"))][:2]
                for dn in docs:
                    if not dn:
                        continue
                    db = sec_get(f"https://www.sec.gov/Archives/edgar/data/{int(r.cik)}/{acc}/{dn}")
                    if not db:
                        continue
                    t = html_text(db)[:200000]
                    for m in XT.finditer(t):
                        ctx = t[max(0, m.start() - 160):m.start()].lower()
                        if any(k in ctx for k in keys):
                            cnt[m.group(1).replace(".", "-")] += 2
                    for m in TS.finditer(t[:20000]):
                        cnt[m.group(1)] += 1
                if sum(cnt.values()) >= 6:
                    break
            rec["tk_votes"] = dict(cnt.most_common(5))
            rec["ticker"] = cnt.most_common(1)[0][0] if cnt else None
            f.write(json.dumps(rec) + "\n")
            f.flush()
    log("e3x meta done")


def _alp_headers():
    env = {}
    for line in (ROOT / ".env.alpaca").read_text(encoding="utf-8", errors="replace").splitlines():  # 읽기 전용(기존 도구와 동일)
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return {"APCA-API-KEY-ID": env["ALPACA_API_KEY_ID"], "APCA-API-SECRET-KEY": env["ALPACA_API_SECRET_KEY"]}


def alpaca_bars(sym, start, end):
    import requests
    h = _alp_headers()
    out, tok = [], None
    while True:
        p = {"symbols": sym, "timeframe": "1Day", "start": start, "end": end, "adjustment": "all", "feed": "sip",
             "limit": 10000}
        if tok:
            p["page_token"] = tok
        for k in range(5):
            rs = requests.get("https://data.alpaca.markets/v2/stocks/bars", headers=h, params=p, timeout=60)
            if rs.status_code == 429:
                time.sleep(30); continue
            break
        if rs.status_code != 200:
            return None, f"http{rs.status_code}"
        j = rs.json()
        out += j.get("bars", {}).get(sym, []) or []
        tok = j.get("next_page_token")
        if not tok:
            break
    if not out:
        return None, "empty"
    d = pd.DataFrame(out)
    d["date"] = pd.to_datetime(d.t).dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
    return d.rename(columns={"o": "open", "c": "close", "v": "volume"})[["date", "open", "close", "volume"]], "ok"


def yf_bars(sym, start, end):
    try:
        import yfinance as yf
        d = yf.download(sym, start=start, end=end, auto_adjust=False, progress=False)
    except Exception as e:  # noqa
        return None, "yf_err"
    if d is None or len(d) == 0:
        return None, "empty"
    if isinstance(d.columns, pd.MultiIndex):
        d.columns = d.columns.get_level_values(0)
    f = d["Adj Close"] / d["Close"]
    return pd.DataFrame({"date": d.index, "open": (d.Open * f).values, "close": d["Adj Close"].values,
                         "volume": d.Volume.values, "rawclose": d.Close.values}), "ok"


def e3x_prices():
    meta = pd.DataFrame([json.loads(l) for l in open(OUT / "e3x_meta.jsonl", encoding="utf-8")])
    (OUT / "e3x_px").mkdir(exist_ok=True)
    rows = []
    for r in meta.itertuples():
        tkr = getattr(r, "ticker", None)
        if not isinstance(tkr, str):
            rows.append({"cik": r.cik, "status": "no_ticker"}); continue
        start = (pd.Timestamp(r.first10) - pd.Timedelta(days=60)).strftime("%Y-%m-%d")
        end = (pd.Timestamp(r.first10) + pd.Timedelta(days=900)).strftime("%Y-%m-%d")
        fp = OUT / "e3x_px" / f"{r.cik}.csv"
        src = None
        if fp.exists():
            d = pd.read_csv(fp, parse_dates=["date"]); src = "cache"
        else:
            d, st = (None, "pre2016") if pd.Timestamp(end) < pd.Timestamp("2016-01-04") else alpaca_bars(tkr, start, end)
            src = "alpaca" if d is not None else None
            if d is None:
                d, st2 = yf_bars(tkr, start, end)
                src = "yf" if d is not None else None
                st = f"{st}/{st2}"
            if d is not None:
                d.to_csv(fp, index=False)
        rows.append({"cik": r.cik, "ticker": tkr, "status": "ok" if d is not None else st, "src": src,
                     "n": 0 if d is None else len(d), "first_bar": None if d is None else str(d.date.min().date())})
    P = pd.DataFrame(rows)
    P.to_csv(OUT / "e3x_px_status.csv", index=False)
    log("e3x prices", P.status.value_counts().to_dict())
    return P


REJ = []


def e3x_events():
    """확장 SpinCo 이벤트: 복원 가격으로 C-E3 규칙(첫 거래 f, 진입 f+16 시가, H120 open→open)."""
    m = M()
    cal = m["cal"]
    meta = pd.DataFrame([json.loads(l) for l in open(OUT / "e3x_meta.jsonl", encoding="utf-8")]).set_index("cik")
    st = pd.read_csv(OUT / "e3x_px_status.csv")
    out = []
    REJ.clear()
    for r in st[st.status == "ok"].itertuples():
        d = pd.read_csv(OUT / "e3x_px" / f"{r.cik}.csv", parse_dates=["date"])
        d = d[d.date.isin(cal)].drop_duplicates("date").sort_values("date")
        f10 = pd.Timestamp(meta.loc[r.cik, "first10"])
        if d.date.min() < f10:  # 10-12B 이전부터 거래 = 신규 상장 아님(모회사/재사용 티커) → 제외
            REJ.append((r.cik, "pre_existing")); continue
        d = d[d.date >= f10]
        if len(d) < 40:
            continue
        pos = cal.get_indexer(d.date)
        # 연속성: 첫 봉 이전 20세션에 다른 봉 없음(재사용 티커 방지) — 창 시작이 f10이라 자동 충족, 대신 f10 이후 400세션 안
        f = pos[0]
        if f - cal.searchsorted(f10) > 400:
            continue
        s = pd.Series(d.open.values, index=pos)
        c = pd.Series(d.close.values, index=pos)
        dv = pd.Series((d.close * d.volume).values, index=pos)  # adjustment=all → 근사
        e = f + 16
        if e + 1 >= len(cal) or e not in s.index:
            continue
        a = e - 1
        rawc = c.get(a, np.nan)
        dv20 = dv.loc[(dv.index >= a - 19) & (dv.index <= a)].mean()
        rec = {"ticker": r.ticker, "cik": r.cik, "src": r.src, "first_trade": cal[f], "t0": cal[e], "e": e,
               "close_a": rawc, "dv20": dv20}
        E = m["edges"][a]
        k = int(np.searchsorted(E, dv20)) if np.isfinite(E).all() and np.isfinite(dv20) else None
        for H in (60, 120):
            t1 = e + H
            if t1 >= len(cal) or k is None:
                rec[f"ret{H}"] = rec[f"sz{H}"] = rec[f"iwm{H}"] = np.nan; continue
            x1 = s.get(t1, np.nan)
            if not np.isfinite(x1):
                cc = c.loc[(c.index >= e) & (c.index <= t1)]
                x1 = cc.iloc[-1] if len(cc) else np.nan
            rec[f"ret{H}"] = x1 / s[e] - 1
            rec[f"sz{H}"], rec[f"iwm{H}"] = bench(e, t1, "open", k)
        out.append(rec)
    X = pd.DataFrame(out)
    X.to_csv(OUT / "e3x_events.csv.gz", index=False, compression="gzip")
    return X


def qstat(d, H, lab):
    d = d.dropna(subset=[f"ret{H}", f"sz{H}"]).copy()
    d["x"] = d[f"ret{H}"] - COST - d[f"sz{H}"]
    d["t0"] = pd.to_datetime(d.t0)
    def qt(s):
        q = s.groupby(s.t0.dt.to_period("Q")).x.mean()
        return (float(q.mean()), float(q.mean() / q.std() * np.sqrt(len(q))) if len(q) > 2 else np.nan, int(len(q)))
    o = d[d.t0.dt.year > IS_END]
    yrs = d.groupby(d.t0.dt.year).x.mean()
    res = {"n": int(len(d)), "evt_mean": float(d.x.mean()), "median": float(d.x.median()),
           "q_all": qt(d), "q_oos": qt(o) if len(o) > 2 else None, "q_is": qt(d[d.t0.dt.year <= IS_END]),
           "years_pos": f"{int((yrs > 0).sum())}/{len(yrs)}",
           "liq_evt_mean": float(d[d.dv20 >= 5e6].x.mean()) if (d.dv20 >= 5e6).any() else None,
           "by_year": {int(a): round(float(b) * 100, 2) for a, b in yrs.items()}}
    res["survive"] = bool(res["q_all"][0] > 0 and res["q_all"][1] >= 2.0)
    RES[f"v2|{lab}|H{H}"] = res
    log(f"v2 {lab} H{H}", {k: v for k, v in res.items() if k != "by_year"})
    return res


def run_e3x():
    ev = pd.read_csv(OUT / "e3_events.csv.gz")
    ev["e"] = ev.e + 15
    base = attach(ev[["ticker", "e"]], (60, 120))
    base["src"] = "matrix"
    X = e3x_events()
    RES["v2|E3x_counts"] = {"base": int(len(base)), "recovered": int(len(X)), "rejected": pd.Series([x[1] for x in REJ]).value_counts().to_dict()}
    cols = ["ticker", "t0", "dv20", "ret120", "sz120", "iwm120", "ret60", "sz60", "iwm60", "src"]
    A = pd.concat([base[cols], X[cols]], ignore_index=True)
    A.to_csv(OUT / "e3x_all.csv.gz", index=False, compression="gzip")
    qstat(base, 120, "E3_base")
    qstat(X, 120, "E3_recovered_only")
    qstat(A, 120, "E3_expanded")
    qstat(A, 60, "E3_expanded")
    # 커버리지
    u = pd.read_csv(OUT / "e3_unmapped.csv")
    st = pd.read_csv(OUT / "e3x_px_status.csv")
    u = u.merge(st, on="cik", how="left")
    u["y"] = u["first"].str[:4]
    u["rec"] = u.cik.isin(X.cik)
    RES["v2|E3x_coverage"] = {y: [int(len(g)), int((g.status == "ok").sum()), int(g.rec.sum())] for y, g in u.groupby("y")}
    RES["v2|E3x_status"] = u.status.fillna("none").value_counts().to_dict()


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "e3x_meta":
        e3x_meta()
    elif cmd == "e3x_px":
        e3x_prices()
    if cmd == "fts":
        name = sys.argv[2]
        stage_fts(name, {"e2": E2_Q, "e4": E4_Q, "e5": E5_Q}[name])
    elif cmd == "docs":
        stage_e2_docs()
    elif cmd == "frames":
        shares_out()
    elif cmd == "eval":
        evaluate(sys.argv[2].split(","))
