#!/usr/bin/env python3
"""T1-KR — 한국 잠정실적/손익구조변경 공시 서프라이즈 모델 (read-only 연구, 2026-09-28).

운영 파일은 건드리지 않는다. 산출물은 data/analysis/model_T1kr_earnings_20260928/ 에만 쓴다.
DART 키는 .env.live 의 DART_API_KEY 를 dotenv 로 읽고 출력하지 않는다.

서브커맨드
  list    : DART list.json. pblntf_detail_ty=I002(공정공시) 전 기간 + I001(수시공시) 1~6월 창(분기창 1·2)만
            (손익구조 30%(15%) 변경은 연간 결산 공시라 1~4월에 몰린다). report_nm 필터 후 events_raw.jsonl.
            stock_code 공란(상폐·비상장) 건수와 접두 종류는 list_counts.json 에 센다.
  fin     : fnlttMultiAcnt(100개 corp 묶음)로 사업연도 2016~2026 × 보고서 4종의 매출액·영업이익·자본총계 → fin.jsonl.
  prices  : 이벤트 종목 중 캐시(price_backfill_yf, 2018 이전 시작)에 없는 종목을 yfinance(.KS/.KQ, auto_adjust)로 받아 prices/ 캐시.
  check   : 재무 API 대체의 검증 — 잠정실적 표본 30건의 공시 원문(document.xml)에서 영업이익 당해실적·전년동기실적을 읽어
            재무 API 로 재구성한 값과 비율(당해/전년동기)을 비교 → check.json (판정과 무관한 품질 측정).
  run     : 피처·결과·워크포워드·판정 → results.json, weekly_*.csv.

==================== 사전등록 (결과 보기 전 고정, 2026-09-28) ====================
표본
- 이벤트: report_nm 에 '잠정' 과 '실적' 이 함께 있거나('영업(잠정)실적(공정공시)', '연결재무제표기준영업(잠정)실적(공정공시)'),
  '손익구조' 가 있는 것('매출액또는손익구조30%(대규모법인은15%)이상변경'). 정정 접두('[기재정정]'·'[첨부정정]'·'[정정]' 등
  '정정' 포함 접두)는 제외(최초 공시만 그 날 알려진 정보다). '[첨부추가]'·'[발행조건확정]' 등 정정 아닌 접두는 원공시로 유지. stock_code 없는 비상장 제외.
- 기간: rcept_dt 2018-01-01 ~ 2026-08-31 (가격 캐시가 2018-01 시작이라 2016~2017 은 가격 부재로 제외; H20 결과 확보 위해 8월 말까지).
- 월별 실적 공시자(같은 corp 가 한 해 잠정실적을 서로 다른 7개 달 이상에 냄) 제외 — 분기 매핑이 틀어진다.
  (정정 기록: 최초 코드는 '건수 7건 이상'이라 별도+연결 동시 공시 회사(분기당 2건)를 잘못 제외했다. 결과 확인 전에
  로그의 제외 473 corp-year 를 보고 '서로 다른 달 수'로 고쳤다.)
- 같은 (종목, 진입일) 중복은 하나로 합친다(잠정실적 우선, 그다음 이른 rcept_no).
- 결과는 합친 풀 기준으로 판정하고, kind(prelim/pl30)별은 진단으로 보고. pl30 중 직전 60일 내 같은 종목 잠정실적이
  있던 건은 pl30_pre 플래그(피처 아님, 진단용).
시점
- rcept_dt 는 날짜만 있다 → 장 마감 후 알려진 것으로 취급. 진입 = rcept_dt 다음 KR 세션 시가. 피처 가격은 rcept_dt 종가까지.
서프라이즈 값(컨센서스 없음) — 공시 본문 대신 DART 재무 API(정기보고서 값)로 재구성한다
- 기간 매핑: 잠정실적은 공시 월 1~3 → 전년 4분기(연간−3분기누적), 4~6 → 1분기, 7~9 → 반기(2분기), 10~12 → 3분기.
  손익구조 변경은 공시 월 1~6 → 전년 연간.
- 연결 공시('연결' 포함)면 CFS 우선, 아니면 OFS 우선, 없으면 다른 쪽.
- lookahead 위험(한계로 명시): 값은 이후 제출된 정기보고서 수치다. 잠정치와 확정치 차이·재작성은 반영 못 한다.
피처 (결정 시점 이전 정보만)
- 이벤트(9): op_yoy(=(OP−OP_ly)/|OP_ly|, ±10 클립), rev_yoy(같은 방식), op_qoq, rev_qoq(분기 이벤트만, 연간은 NaN),
  turn_up(OP_ly≤0<OP), turn_down(OP_ly>0≥OP), op_margin(OP/매출, ±2 클립), dop_eq(=(OP−OP_ly)/자본총계, ±2 클립; 시가총액
  대체물 — 주식수 미수집), is_annual(손익구조 공시 또는 4분기).
- 가격(4): ret20, ret60, vol20(일간 수익 표준편차), ldvol20(log 20일 평균 거래대금).
- 기타: is_kosdaq, month.
결과
- 수익 = 진입 시가 → h번째 세션 종가 (h=5, 20). 비용 0.25% 왕복 차감.
- 주 벤치(판정): 규모매칭 — 같은 진입일, 같은 dvol20 십분위(적격 유니버스 = 가격 캐시 전체 + 이번 수집 종목, 종가≥1,000원,
  20일 평균 거래대금 ≥ 1억) 동일 진입·보유 평균. 보조 벤치: KOSPI200(069500), KOSDAQ150(229200) 둘 다 보고.
규칙·모델 (3 × 2보유 = 6검정)
- M0 기준선: 주별 코호트에서 op_yoy 상위 10% (±10 클립 동점은 dop_eq 로 2차 정렬).
- M1 로지스틱(표준화, C=1.0, 목표=규모매칭 초과>0).
- M2 LightGBM 회귀(목표=규모매칭 초과, n_estimators 300, lr 0.03, num_leaves 15, min_child_samples 50,
  subsample 0.8, colsample 0.8, seed 7).
- 워크포워드: 연 1회 확장창, 첫 테스트 2019. 학습은 청산일이 테스트 연도 시작 전인 이벤트만(퍼지).
- 선택: 진입 주(ISO) 코호트 N≥10 인 주에서 점수 상위 10%(ceil) 동일가중.
판정
- 통계: 주간 net 초과(규모매칭) 평균, Newey-West t(lag H5=1, H20=4). 반기 = 테스트 주 중앙값으로 둘로 나눔.
- 합격: t ≥ 3 + 두 반기 모두 양수. 후보: t ≥ 2 + 반기 양수. 모델은 기준선 대비 차이 t 도 보고(판정 보조).
- 진단(판정 밖): 코호트 전체(무차별 매수) net, 이벤트 전용 피처 모델, 지수 벤치 net.
생존편향: 현재 상장 종목만 가격이 있다(yfinance). 상폐 종목의 공시는 빠진다 → 한계로 명시.
====================================================================================
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "analysis" / "model_T1kr_earnings_20260928"
PX = OUT / "prices"
CACHE = ROOT / "data" / "price_backfill_yf" / "kr_fallen_price_cache_2018_2026.json"
BENCH_DIR = ROOT / "data" / "price_backfill_yf" / "kr"
SLEEP = 0.25
COST = 0.25
HS = (5, 20)
BGN, END = "20180101", "20260831"

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env.live")
except Exception:
    pass
KEY = (os.getenv("DART_API_KEY") or "").strip()


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    with (OUT / "run.log").open("a", encoding="utf-8") as fh:
        fh.write(s + "\n")


def api(name: str, **params) -> dict:
    q = urllib.parse.urlencode({"crtfc_key": KEY, **params})
    for k in range(4):
        try:
            req = urllib.request.Request(f"https://opendart.fss.or.kr/api/{name}.json?{q}", headers={"User-Agent": "Mozilla/5.0"})
            d = json.loads(urllib.request.urlopen(req, timeout=30).read())
            time.sleep(SLEEP)
            if d.get("status") == "020":
                raise SystemExit("DART 한도 초과(020) — 중단")
            return d
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001
            time.sleep(2 + 3 * k)
            last = e
    raise RuntimeError(f"api fail {name}: {last}")


def keep_name(nm: str) -> str | None:
    n = nm.strip()
    if n.startswith("[") and "정정" in n.split("]")[0]:
        return None
    if n.startswith("["):
        n = n.split("]", 1)[1]
    if "잠정" in n and "실적" in n:
        return "prelim"
    if "손익구조" in n:
        return "pl30"
    return None


def windows():
    for y in range(2018, 2027):
        for a, b in (("0101", "0331"), ("0401", "0630"), ("0701", "0930"), ("1001", "1231")):
            s, e = f"{y}{a}", f"{y}{b}"
            if s > END:
                return
            yield s, min(e, END)


def cmd_list():
    path = OUT / "events_raw.jsonl"
    done = set()
    if path.exists():
        for l in path.open(encoding="utf-8"):
            pass
    prog = OUT / "list_progress.json"
    pg = json.loads(prog.read_text()) if prog.exists() else {}
    fh = path.open("a", encoding="utf-8")
    ncall = 0
    cp = OUT / "list_counts.json"
    cnts = Counter(json.loads(cp.read_text(encoding="utf-8"))) if cp.exists() else Counter()
    for s, e in windows():
        tys = ["I002"]
        if s[4:6] in ("01", "04"):
            tys.append("I001")
        for ty in tys:
            key = f"{s}_{ty}"
            if pg.get(key) == "done":
                continue
            page, tot = 1, 1
            nk = 0
            while page <= tot:
                d = api("list", bgn_de=s, end_de=e, pblntf_detail_ty=ty, page_no=page, page_count=100)
                ncall += 1
                if d.get("status") not in ("000", "013"):
                    log("list err", key, page, d.get("status"), d.get("message"))
                    break
                tot = int(d.get("total_page") or 0)
                for x in d.get("list", []):
                    nm0 = x.get("report_nm", "").strip()
                    k0 = keep_name(nm0.split("]", 1)[1] if nm0.startswith("[") else nm0)
                    if k0:
                        cnts[f"{k0}|{'code' if (x.get('stock_code') or '').strip() else 'nocode'}|"
                             f"{nm0.split(']')[0] + ']' if nm0.startswith('[') else 'orig'}"] += 1
                    k = keep_name(nm0)
                    if k and (x.get("stock_code") or "").strip():
                        x["kind"] = k
                        fh.write(json.dumps(x, ensure_ascii=False) + "\n")
                        nk += 1
                page += 1
            fh.flush()
            pg[key] = "done"
            prog.write_text(json.dumps(pg))
            (OUT / "list_counts.json").write_text(json.dumps(cnts, ensure_ascii=False, indent=1), encoding="utf-8")
            log("list", key, "pages", tot, "kept", nk, "calls", ncall)
    fh.close()


def load_events():
    seen = {}
    for l in (OUT / "events_raw.jsonl").open(encoding="utf-8"):
        x = json.loads(l)
        seen[x["rcept_no"]] = x
    return list(seen.values())


def cmd_fin():
    ev = load_events()
    corps = sorted({x["corp_code"] for x in ev})
    log("fin corps", len(corps))
    path = OUT / "fin.jsonl"
    prog = OUT / "fin_progress.json"
    pg = json.loads(prog.read_text()) if prog.exists() else {}
    fh = path.open("a", encoding="utf-8")
    want = {"매출액", "수익(매출액)", "영업수익", "영업이익", "영업이익(손실)", "자본총계"}
    for y in range(2016, 2027):
        for rc in ("11013", "11012", "11014", "11011"):
            for i in range(0, len(corps), 100):
                key = f"{y}_{rc}_{i}"
                if pg.get(key):
                    continue
                d = api("fnlttMultiAcnt", corp_code=",".join(corps[i:i + 100]), bsns_year=str(y), reprt_code=rc)
                n = 0
                for x in d.get("list", []) or []:
                    if x.get("account_nm") in want:
                        fh.write(json.dumps({k: x.get(k) for k in ("corp_code", "bsns_year", "reprt_code", "fs_div", "sj_div",
                                                                   "account_nm", "thstrm_amount", "thstrm_add_amount",
                                                                   "frmtrm_amount", "frmtrm_add_amount")}, ensure_ascii=False) + "\n")
                        n += 1
                fh.flush()
                if d.get("status") in ("000", "013"):
                    pg[key] = d.get("status")
                    prog.write_text(json.dumps(pg))
                else:
                    log("fin err", key, d.get("status"), d.get("message"))
            log("fin", y, rc, "done", Counter(v for k, v in pg.items() if k.startswith(f"{y}_{rc}_")))
    fh.close()


# ---------------------------------------------------------------- prices
def load_cache():
    raw = json.loads(CACHE.read_text(encoding="utf-8"))
    out = {}
    for t, rows in raw.items():
        if rows and rows[0]["d"] < "2018-02-01":
            out[t] = [(r["d"], r["o"], r["h"], r["l"], r["c"], r["v"]) for r in rows]
    return out


def cmd_prices(retry: bool = False):
    """retry=True: 빈 파일(1차 실패)만 반대 접미사(.KS↔.KQ)로 재시도."""
    import yfinance as yf
    PX.mkdir(parents=True, exist_ok=True)
    cache = load_cache()
    ev = load_events()
    cls = {}
    for x in ev:
        cls[x["stock_code"].strip()] = x.get("corp_cls")
    if retry:
        need = [t for t in sorted(cls) if (PX / f"{t}.csv").exists() and (PX / f"{t}.csv").stat().st_size < 60]
        cls = {t: ("K" if cls[t] == "Y" else "Y") for t in cls}
    else:
        need = [t for t in sorted(cls) if t not in cache and not (PX / f"{t}.csv").exists()]
    log("prices need", len(need), "cached-long", len(cache))
    for i in range(0, len(need), 50):
        chunk = need[i:i + 50]
        syms = {t: f"{t}.{'KS' if cls[t] == 'Y' else 'KQ'}" for t in chunk}
        try:
            df = yf.download(list(syms.values()), start="2017-10-01", end="2026-09-26", auto_adjust=True,
                             progress=False, group_by="ticker", threads=True)
        except Exception as e:  # noqa: BLE001
            log("yf err", e)
            continue
        for t, s in syms.items():
            try:
                sub = df[s].dropna(subset=["Open", "Close"])
            except Exception:
                sub = None
            if sub is None or len(sub) < 30:
                (PX / f"{t}.csv").write_text("date,open,high,low,close,volume\n")
                continue
            with (PX / f"{t}.csv").open("w", encoding="utf-8") as fh:
                fh.write("date,open,high,low,close,volume\n")
                for d_, r in sub.iterrows():
                    fh.write(f"{d_.date()},{r['Open']},{r['High']},{r['Low']},{r['Close']},{r['Volume']}\n")
        log("prices", i + len(chunk), "/", len(need))


def read_csv(p: Path):
    rows = []
    for l in p.read_text(encoding="utf-8-sig").splitlines()[1:]:
        a = l.split(",")
        if len(a) >= 6:
            rows.append((a[0], float(a[1]), float(a[2]), float(a[3]), float(a[4]), float(a[5])))
    return rows


# ---------------------------------------------------------------- run
def num(s):
    if s in (None, "", "-"):
        return None
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def pct(a, b):
    if a is None or b is None or b == 0:
        return float("nan")
    return max(-10.0, min(10.0, (a - b) / abs(b)))


def nw_t(xs, lag):
    import numpy as np
    x = np.asarray(xs, float)
    n = len(x)
    if n < 5:
        return float("nan")
    m = x.mean()
    u = x - m
    v = (u @ u) / n
    for L in range(1, lag + 1):
        w = 1 - L / (lag + 1)
        v += 2 * w * (u[L:] @ u[:-L]) / n
    return m / math.sqrt(v / n) if v > 0 else float("nan")


def cmd_run():
    import numpy as np
    import pandas as pd

    # ---- 재무 테이블: (corp, year, rc, fs) -> {acct: (thstrm, thstrm_add, frmtrm, frmtrm_add)}
    fin = defaultdict(dict)
    alias = {"매출액": "rev", "수익(매출액)": "rev", "영업수익": "rev", "영업이익": "op", "영업이익(손실)": "op", "자본총계": "eq"}
    for l in (OUT / "fin.jsonl").open(encoding="utf-8"):
        x = json.loads(l)
        a = alias[x["account_nm"]]
        k = (x["corp_code"], int(x["bsns_year"]), x["reprt_code"], x["fs_div"])
        if a in fin[k] and a == "rev" and x["account_nm"] != "매출액":
            continue
        fin[k][a] = (num(x["thstrm_amount"]), num(x["thstrm_add_amount"]), num(x["frmtrm_amount"]), num(x["frmtrm_add_amount"]))

    def get(corp, y, rc, pref):
        for fs in (pref, "OFS" if pref == "CFS" else "CFS"):
            if (corp, y, rc, fs) in fin and "op" in fin[(corp, y, rc, fs)]:
                return fin[(corp, y, rc, fs)], fs
        return None, None

    def period_vals(corp, kind, y, q, pref):
        """(rev, op, rev_ly, op_ly, rev_pq, op_pq, eq, is_annual)"""
        if kind == "pl30" or q == 4:
            a, fs = get(corp, y, "11011", pref)
            if a is None:
                return None
            if kind == "pl30":
                return (a.get("rev", (None,) * 4)[0], a["op"][0], a.get("rev", (None,) * 4)[2], a["op"][2],
                        None, None, a.get("eq", (None,) * 4)[0], 1)
            b = fin.get((corp, y, "11014", fs))
            if not b or "op" not in b:
                return None
            def q4(acc):
                A, B = a.get(acc), b.get(acc)
                if not A or not B or None in (A[0], A[2], B[1], B[3]):
                    return None, None, None
                return A[0] - B[1], A[2] - B[3], B[0]
            r, rly, rpq = q4("rev")
            o, oly, opq = q4("op")
            return (r, o, rly, oly, rpq, opq, a.get("eq", (None,) * 4)[0], 1)
        rc = {1: "11013", 2: "11012", 3: "11014"}[q]
        a, fs = get(corp, y, rc, pref)
        if a is None:
            return None
        prev = {1: (y - 1, "11011"), 2: (y, "11013"), 3: (y, "11012")}[q]
        pa = fin.get((corp, prev[0], prev[1], fs)) or {}
        if q == 1:
            # 직전 분기 = 전년 4분기 = 전년 연간 − 전년 3분기 누적
            p3 = fin.get((corp, y - 1, "11014", fs)) or {}
            def pq(acc):
                A, B = pa.get(acc), p3.get(acc)
                return A[0] - B[1] if A and B and A[0] is not None and B[1] is not None else None
            rpq, opq = pq("rev"), pq("op")
        else:
            rpq = (pa.get("rev") or (None,))[0]
            opq = (pa.get("op") or (None,))[0]
        R = a.get("rev", (None,) * 4)
        return (R[0], a["op"][0], R[2], a["op"][2], rpq, opq, a.get("eq", (None,) * 4)[0], 0)

    # ---- 이벤트
    ev = load_events()
    ev = [x for x in ev if BGN <= x["rcept_dt"] <= END]
    log("events raw(기간 내, 정정 제외)", len(ev), Counter(x["kind"] for x in ev))
    # 별도·연결 잠정실적을 함께 내는 회사는 분기당 2건이라, 건수가 아니라 '공시가 있었던 서로 다른 달' 수로 센다.
    cnt = Counter(k[:2] for k in {(x["corp_code"], x["rcept_dt"][:4], x["rcept_dt"][4:6]) for x in ev if x["kind"] == "prelim"})
    monthly = {k for k, v in cnt.items() if v >= 7}
    ev = [x for x in ev if not (x["kind"] == "prelim" and (x["corp_code"], x["rcept_dt"][:4]) in monthly)]
    log("월별 공시자 제외 후", len(ev), "제외 corp-year", len(monthly))

    # ---- 가격
    cache = load_cache()
    px = dict(cache)
    for p in PX.glob("*.csv"):
        r = read_csv(p)
        if r:
            px[p.stem] = r
    log("price tickers", len(px))
    # 유니버스 패널
    frames = {}
    for t, rows in px.items():
        a = np.array([r[1:] for r in rows], float)
        frames[t] = pd.DataFrame(a[:, [0, 3, 4]], index=pd.to_datetime([r[0] for r in rows]), columns=["o", "c", "v"])
    O = pd.DataFrame({t: f["o"] for t, f in frames.items()}).sort_index()
    C = pd.DataFrame({t: f["c"] for t, f in frames.items()}).reindex(O.index)
    V = pd.DataFrame({t: f["v"] for t, f in frames.items()}).reindex(O.index)
    bk = {}
    for b in ("069500", "229200"):
        r = read_csv(BENCH_DIR / f"kr_{b}.csv")
        bk[b] = pd.DataFrame([x[1:5] for x in r], index=pd.to_datetime([x[0] for x in r]), columns=["o", "h", "l", "c"])
    # 세션 달력 = 069500 거래일
    cal = bk["069500"].dropna().index
    bk = {b: v.reindex(cal) for b, v in bk.items()}
    O, C, V = O.reindex(cal), C.reindex(cal), V.reindex(cal)
    O = O.where(O > 0)
    C = C.where(C > 0)
    dv = (C * V)
    dv20 = dv.rolling(20, min_periods=15).mean().shift(1)          # 진입일 기준 전일까지
    elig = (C.shift(1) >= 1000) & (dv20 >= 1e8)
    dec = dv20.where(elig).rank(axis=1, pct=True)
    dec = np.floor(dec * 10).clip(upper=9)
    fwd = {}
    for h in HS:
        fwd[h] = (C.shift(-(h - 1)) / O - 1) * 100                     # 진입일 시가 → h번째 세션 종가
    szb = {}
    for h in HS:
        R = fwd[h].where(elig)
        m = {}
        for dcl in range(10):
            m[dcl] = R.where(dec == dcl).mean(axis=1)
        szb[h] = pd.DataFrame(m)
    idxret = {b: {h: (bk[b]["c"].shift(-(h - 1)) / bk[b]["o"] - 1) * 100 for h in HS} for b in bk}
    logret = np.log(C / C.shift(1))
    ret20 = (C / C.shift(20) - 1)
    ret60 = (C / C.shift(60) - 1)
    vol20 = logret.rolling(20, min_periods=15).std()
    dvf = dv.rolling(20, min_periods=15).mean()
    pos = {d: i for i, d in enumerate(cal)}

    rows = []
    miss = Counter()
    for x in sorted(ev, key=lambda z: z["rcept_no"]):
        t = x["stock_code"].strip()
        if t not in O.columns:
            miss["no_price"] += 1
            continue
        rd = pd.Timestamp(datetime.strptime(x["rcept_dt"], "%Y%m%d"))
        j = cal.searchsorted(rd, side="right")                       # rcept_dt 다음 세션
        if j >= len(cal):
            miss["no_session"] += 1
            continue
        ed = cal[j]
        fd = cal[j - 1]                                              # 피처 기준일(공시일 또는 직전 세션)
        m = int(x["rcept_dt"][4:6])
        y = int(x["rcept_dt"][:4])
        if x["kind"] == "pl30":
            if m > 6:
                miss["pl30_off"] += 1
                continue
            fy, q = y - 1, 4
        else:
            fy, q = (y - 1, 4) if m <= 3 else (y, (m - 1) // 3)
        pref = "CFS" if "연결" in x["report_nm"] else "OFS"
        pv = period_vals(x["corp_code"], x["kind"], fy, q, pref)
        if pv is None or pv[1] is None or pv[3] is None:
            miss["no_fin"] += 1
            continue
        rev, op, rly, oly, rpq, opq, eq, ann = pv
        if pd.isna(O.at[ed, t]):
            miss["no_open"] += 1
            continue
        r = {"rcept_no": x["rcept_no"], "t": t, "kind": x["kind"], "ed": ed, "kosdaq": int(x.get("corp_cls") == "K"),
             "op_yoy": pct(op, oly), "rev_yoy": pct(rev, rly),
             "op_qoq": pct(op, opq) if not ann else float("nan"), "rev_qoq": pct(rev, rpq) if not ann else float("nan"),
             "turn_up": int(oly <= 0 < op), "turn_down": int(oly > 0 >= op),
             "op_margin": max(-2, min(2, op / rev)) if rev else float("nan"),
             "dop_eq": max(-2, min(2, (op - oly) / eq)) if eq and eq > 0 else float("nan"),
             "is_annual": ann, "month": m,
             "ret20": ret20.at[fd, t], "ret60": ret60.at[fd, t], "vol20": vol20.at[fd, t],
             "ldvol20": math.log(dvf.at[fd, t]) if dvf.at[fd, t] and dvf.at[fd, t] > 0 else float("nan")}
        dc = dec.at[ed, t]
        for h in HS:
            if j + h - 1 >= len(cal) or pd.isna(fwd[h].at[ed, t]):
                r[f"y{h}"] = float("nan")
                continue
            rr = fwd[h].at[ed, t]
            r[f"raw{h}"] = rr
            r[f"y{h}"] = rr - szb[h].at[ed, int(dc)] if not pd.isna(dc) and not pd.isna(szb[h].at[ed, int(dc)]) else float("nan")
            r[f"k2{h}"] = rr - idxret["069500"][h].at[ed]
            r[f"kq{h}"] = rr - idxret["229200"][h].at[ed]
            r[f"exit{h}"] = cal[j + h - 1]
        rows.append(r)
    df = pd.DataFrame(rows)
    log("miss", dict(miss), "rows", len(df))
    df = df.sort_values(["ed", "kind", "rcept_no"], ascending=[True, False, True]).drop_duplicates(["t", "ed"], keep="first")
    pre = df[df.kind == "prelim"].groupby("t")["ed"].apply(list).to_dict()
    df["pl30_pre"] = [int(k == "pl30" and any(0 < (e - p).days <= 60 for p in pre.get(t, [])))
                      for k, t, e in zip(df.kind, df.t, df.ed)]
    log("dedupe (t,ed)", len(df), Counter(df["kind"]))
    df.to_csv(OUT / "events_features.csv.gz", index=False)

    EVF = ["op_yoy", "rev_yoy", "op_qoq", "rev_qoq", "turn_up", "turn_down", "op_margin", "dop_eq", "is_annual"]
    PRF = ["ret20", "ret60", "vol20", "ldvol20"]
    OTH = ["kosdaq", "month"]
    FEATS = EVF + PRF + OTH
    import lightgbm as lgb
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    df["year"] = df["ed"].dt.year
    df["wk"] = df["ed"].dt.to_period("W-SUN").dt.start_time
    results = {"coverage": {"n": int(len(df)), "by_year": {int(k): int(v) for k, v in df["year"].value_counts().sort_index().items()},
                            "by_kind": dict(Counter(df["kind"])), "miss": dict(miss), "tickers": int(df["t"].nunique())}}
    for h in HS:
        d = df.dropna(subset=[f"y{h}"]).copy()
        for name in ("M0", "M1", "M2", "M2ev"):
            d[name] = np.nan
        for ty in range(2019, 2027):
            tr = d[(d[f"exit{h}"] < pd.Timestamp(f"{ty}-01-01"))]
            te = d["year"] == ty
            if te.sum() == 0 or len(tr) < 200:
                continue
            d.loc[te, "M0"] = d.loc[te, "op_yoy"].fillna(-99) + 1e-3 * d.loc[te, "dop_eq"].fillna(0).clip(-2, 2)
            Xtr = tr[FEATS].astype(float)
            med = Xtr.median()
            sc = StandardScaler().fit(Xtr.fillna(med))
            lr = LogisticRegression(C=1.0, max_iter=2000).fit(sc.transform(Xtr.fillna(med)), (tr[f"y{h}"] > 0).astype(int))
            d.loc[te, "M1"] = lr.predict_proba(sc.transform(d.loc[te, FEATS].astype(float).fillna(med)))[:, 1]
            prm = dict(n_estimators=300, learning_rate=0.03, num_leaves=15, min_child_samples=50, subsample=0.8,
                       subsample_freq=1, colsample_bytree=0.8, random_state=7, verbose=-1)
            g = lgb.LGBMRegressor(**prm).fit(Xtr, tr[f"y{h}"])
            d.loc[te, "M2"] = g.predict(d.loc[te, FEATS].astype(float))
            g2 = lgb.LGBMRegressor(**prm).fit(tr[EVF].astype(float), tr[f"y{h}"])
            d.loc[te, "M2ev"] = g2.predict(d.loc[te, EVF].astype(float))
            log(f"H{h} fold {ty} train {len(tr)} test {int(te.sum())}")
        d = d.dropna(subset=["M0"])
        lag = 1 if h == 5 else 4
        weeks = []
        for wk, gdf in d.groupby("wk"):
            if len(gdf) < 10:
                continue
            k = math.ceil(len(gdf) * 0.1)
            w = {"wk": wk, "n": len(gdf)}
            for col in ("y", "k2", "kq"):
                w[f"all_{col}"] = gdf[f"{col}{h}"].mean() - COST
            for mname in ("M0", "M1", "M2", "M2ev"):
                top = gdf.nlargest(k, mname)
                for col in ("y", "k2", "kq"):
                    w[f"{mname}_{col}"] = top[f"{col}{h}"].mean() - COST
            weeks.append(w)
        W = pd.DataFrame(weeks).sort_values("wk")
        W.to_csv(OUT / f"weekly_H{h}.csv", index=False)
        half = W["wk"] <= W["wk"].median()
        res = {"weeks": int(len(W)), "events_test": int(len(d)), "first_wk": str(W["wk"].min().date()), "last_wk": str(W["wk"].max().date())}
        for mname in ("all", "M0", "M1", "M2", "M2ev"):
            rr = {}
            for col in ("y", "k2", "kq"):
                s = W[f"{mname}_{col}"]
                rr[col] = {"mean": round(s.mean(), 3), "t": round(nw_t(s, lag), 2),
                           "h1": round(s[half].mean(), 3), "h2": round(s[~half].mean(), 3)}
            if mname in ("M1", "M2", "M2ev"):
                df_ = W[f"{mname}_y"] - W["M0_y"]
                rr["diff_vs_M0"] = {"mean": round(df_.mean(), 3), "t": round(nw_t(df_, lag), 2),
                                    "h1": round(df_[half].mean(), 3), "h2": round(df_[~half].mean(), 3)}
                dfa = W[f"{mname}_y"] - W["all_y"]
                rr["diff_vs_all"] = {"mean": round(dfa.mean(), 3), "t": round(nw_t(dfa, lag), 2)}
            if mname != "all":
                sel_mean = rr["y"]["mean"]
                rr["verdict"] = ("합격" if rr["y"]["t"] >= 3 and rr["y"]["h1"] > 0 and rr["y"]["h2"] > 0 else
                                 "후보" if rr["y"]["t"] >= 2 and rr["y"]["h1"] > 0 and rr["y"]["h2"] > 0 else "기각")
            yr = {}
            for yy, g_ in W.groupby(W["wk"].dt.year):
                yr[int(yy)] = round(g_[f"{mname}_y"].mean(), 3)
            rr["by_year_y"] = yr
            res[mname] = rr
        # 서프라이즈 단변량 진단: op_yoy 십분위별 규모매칭 초과(전 기간, 판정 밖)
        dd = df.dropna(subset=[f"y{h}", "op_yoy"])
        q = pd.qcut(dd["op_yoy"].rank(method="first"), 10, labels=False)
        res["op_yoy_decile_mean_y"] = [round(v, 3) for v in dd.groupby(q)[f"y{h}"].mean().tolist()]
        res["turn_up_mean_y"] = round(dd[dd.turn_up == 1][f"y{h}"].mean(), 3)
        res["turn_up_n"] = int((dd.turn_up == 1).sum())
        # kind 별 진단(판정 밖): 해당 kind 이벤트만의 주간 코호트 top10% (M0/M2), N≥10 주
        for kd in ("prelim", "pl30"):
            dk = d[d.kind == kd]
            wk_ = []
            for wk, gdf in dk.groupby("wk"):
                if len(gdf) < 10:
                    continue
                k = math.ceil(len(gdf) * 0.1)
                wk_.append({"all": gdf[f"y{h}"].mean() - COST, "M0": gdf.nlargest(k, "M0")[f"y{h}"].mean() - COST,
                            "M2": gdf.nlargest(k, "M2")[f"y{h}"].mean() - COST})
            if wk_:
                K_ = pd.DataFrame(wk_)
                res[f"kind_{kd}"] = {"weeks": len(K_), **{c: {"mean": round(K_[c].mean(), 3), "t": round(nw_t(K_[c], lag), 2)} for c in K_}}
        res["pl30_pre_share"] = round(float(d[d.kind == "pl30"]["pl30_pre"].mean()), 3) if (d.kind == "pl30").any() else None
        res["weeks_per_year"] = {int(k): int(v) for k, v in W["wk"].dt.year.value_counts().sort_index().items()}
        res["M2_pick_event_median_y"] = None
        results[f"H{h}"] = res
        log(json.dumps({f"H{h}": res}, ensure_ascii=False, default=str))
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


def cmd_check():
    import html as _h
    import io
    import random
    import re
    import zipfile
    import pandas as pd
    df = pd.read_csv(OUT / "events_features.csv.gz", dtype={"t": str, "rcept_no": str})
    fin_rows = df[(df.kind == "prelim") & (df.is_annual == 0)]
    rnd = random.Random(7)
    idx = rnd.sample(list(fin_rows.index), min(30, len(fin_rows)))
    out = []
    for i in idx:
        r = fin_rows.loc[i]
        q = urllib.parse.urlencode({"crtfc_key": KEY, "rcept_no": r.rcept_no})
        try:
            b = urllib.request.urlopen(urllib.request.Request(f"https://opendart.fss.or.kr/api/document.xml?{q}",
                                                              headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read()
            time.sleep(SLEEP)
            z = zipfile.ZipFile(io.BytesIO(b))
            def _dec(bb):
                for enc in ("utf-8", "cp949"):
                    try:
                        return bb.decode(enc)
                    except UnicodeDecodeError:
                        pass
                return bb.decode("utf-8", "ignore")
            x = " ".join(_dec(z.read(n)) for n in z.namelist())
        except Exception as e:  # noqa: BLE001
            out.append({"rcept_no": r.rcept_no, "err": str(e)[:80]})
            continue
        t = re.sub(r"(?is)<(style|script)[^>]*>.*?</>", " ", x)
        t = re.sub(r"\s+", " ", _h.unescape(re.sub(r"<[^>]+>", " ", t)))
        rec = {"rcept_no": r.rcept_no, "t": r.t, "ed": r.ed, "api_op_yoy": r.op_yoy}
        # 원문 '영업이익 당해실적 … 누계실적' 행의 토큰에서, (값, 다음 토큰=증감률%)이 자기일관적인 쌍을 찾는다.
        # 첫 쌍 = 직전분기(전기), 마지막 쌍 = 전년동기. 서식(열 개수)이 공시마다 달라 위치 대신 산술 일관성으로 식별.
        m = re.search(r"영업이익\s*(?:\(손실\))?\s*당해실적(.*?)누계실적", t)
        def f(s_):
            s_ = s_.replace(",", "").replace("△", "-").replace("(", "-").replace(")", "").replace("%", "")
            try:
                return float(s_)
            except ValueError:
                return None
        if m:
            tok = m.group(1).split()
            cur = f(tok[0]) if tok else None
            pairs = []
            for k in range(1, len(tok) - 1):
                a_, b_ = f(tok[k]), tok[k + 1]
                if cur is None or a_ is None or a_ == 0:
                    continue
                if b_ in ("흑자전환", "적자전환", "적자지속", "적자확대", "적자축소"):
                    ok_ = (b_ == "흑자전환" and a_ < 0 < cur) or (b_ == "적자전환" and cur < 0 < a_) or (b_.startswith("적자") and a_ < 0 and cur < 0 and b_ != "적자전환")
                else:
                    bv = f(b_)
                    ok_ = bv is not None and abs((cur - a_) / abs(a_) * 100 - bv) <= max(0.6, 0.01 * abs(bv))
                if ok_:
                    pairs.append(a_)
            if len(pairs) >= 2:
                rec.update({"doc_cur": cur, "doc_ly": pairs[-1], "doc_op_yoy": pct(cur, pairs[-1])})
            else:
                rec["snip"] = m.group(0)[:200]
        else:
            rec["snip"] = t[t.find("영업이익"):t.find("영업이익") + 160] if "영업이익" in t else "no_op"
        out.append(rec)
    ok = [o for o in out if o.get("doc_op_yoy") is not None]
    close = [o for o in ok if abs(o["doc_op_yoy"] - o["api_op_yoy"]) <= 0.05 + 0.05 * abs(o["doc_op_yoy"])]
    sgn = [o for o in ok if (o["doc_op_yoy"] > 0) == (o["api_op_yoy"] > 0)]
    summ = {"sampled": len(out), "parsed": len(ok), "within_5pct": len(close), "same_sign": len(sgn)}
    log("check", summ)
    (OUT / "check.json").write_text(json.dumps({"summary": summ, "rows": out}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    c = sys.argv[1] if len(sys.argv) > 1 else ""
    {"list": cmd_list, "fin": cmd_fin, "prices": cmd_prices, "prices_retry": lambda: cmd_prices(True), "run": cmd_run, "check": cmd_check}[c]()
