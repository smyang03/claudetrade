# -*- coding: utf-8 -*-
"""자사주 매입 쉐도우 원장 (2026-09-30 운영자 승인) — 주문 없음. 신호 기록·정산·판정만.

동결 규칙(연구 코드를 그대로 복사해 쓴다 — 복사 블록마다 출처 줄 번호를 적었다. 연구 파일은 git 미추적이라 import 하지 않는다)
[KR] docs/reports/kr_event_20260930.md · tools/research/research_kr_event_20260930.py
  - 원천: DART list.json pblntf_detail_ty=B001(주요사항보고서), 접수일 rcept_dt.
  - 종류: 제목이 '['로 시작하면 제외, '자기주식'+'신탁계약' = 신탁(제외), '정정' 포함 제외, 자기주식취득결정 = 직접취득.
  - 규모: DS005 tsstkAqDecsn.aqpln_stk_ostk(취득예정 보통주) / stockTotqySttus(bsns_year=share_year, 11011) 보통주 istc_totqy
    (없으면 합계). 분모 없으면 규칙 밖(대체 분모 없음). DS005는 결정당 최신본만 준다 → 원공시 rcept_no가 없으면
    같은 회사 후속 서류('['로 시작) 중 다음 원공시 전 가장 이른 본을 쓴다(fields_via=followup).
  - 신호일 D = rcept_dt 이하 마지막 KRX 세션, 진입 = D 다음 세션 시가, h5(1차)·h20, 비용 0.25% 왕복.
  - Y1: D 종가 ≥ 1,000 · dvol20 ≥ 20억 · 이력 ≥ 25봉 · 다음 시가 > 0 · 취득주식/발행주식 ≥ 1%.
    벤치 = D 적격 유니버스 dvol20 십분위 같은 분위 등가중(≥30종목, 분위당 ≥3), KOSPI200 069500, KOSDAQ150 229200.
  - L1_lowliq(운영 조율자 추가 2026-09-30, 보고서 (b)): 같은 ≥1% 규칙, 5억 ≤ dvol20 < 20억. 이벤트 종목 가격 = 네이버 fchart
    수정주가, 비용 = 0.25% + 이벤트별 Abdi-Ranaldo 스프레드(D 이전 20봉) · 고정 0.75% 변형. 벤치 = 같은 대역 dvol 삼분위.
  - 같은 (종목, D) 직접취득 중복은 rcept_no 가장 이른 1건.
  - valueup_period = rcept_dt ≥ 2024-02-26 (기록만, 게이트 아님).
  - 라이브 가격 유니버스 = data/price/kr (연구 Part 1과 같은 소스). 결측 종목은 네이버 fchart 보충.
[US] docs/reports/us_events_20260930.md '2차' · tools/research/research_us_events_20260930.py (C-E2 v2)
  - EDGAR FTS form 8-K 질의 4종, adsh당 문서 EX-99 우선, items 에 2.02 있으면 제외.
  - parse_buyback_v2(스니펫) 승인 문장, 규모 = pct/100 | usd ÷ (전일 원주가 종가 × 최신 dei 발행주식) | shares ÷ dei.
    원주가 = yfinance Close[a] × (a 이후 분할 누적배수). 규모가 (0, 0.5] 밖이면 결측. ≥ 10% 채택.
  - 필터 close[a] ≥ $3, dv20[a] ≥ $1M. 같은 티커 60세션 dedupe — 규모·가격 필터 전(파싱된 2.02 없는 전체)에 적용(연구 run_e2v2 순서).
  - 진입 = file_date 다음 NYSE 정규장 시가(가상 체결 = 시가), H60(1차)·H20 open→open, 비용 0.50%,
    벤치 = 앵커 a(진입 전일) 우주(≥$3·≥$1M, ≥100종목) dv20 십분위(분위당 ≥5) · IWM.
  - acceptanceDateTime(submissions JSON, UTC 'Z') → ET 로 변환해 같이 기록(판정 진입은 file_date 기준).
판정(사전등록 게이트, 운영자 지시): 시장·태그별 만기 ≥ 30건 AND 진입일 클러스터 t ≥ 2.0 AND 평균 > 0 → '소액 실주문 검토' 메모만.

원장: data/shadow/kr_buyback_shadow.jsonl · us_buyback_shadow.jsonl (키 = KR|rcept_no · US|accession, 멱등)
정산: data/shadow/kr_buyback_shadow_settle.jsonl · us_buyback_shadow_settle.jsonl (키|h 단위)
사용:
  python tools/buyback_shadow.py kr-scan [--date YYYY-MM-DD [--to YYYY-MM-DD]] [--dry]
  python tools/buyback_shadow.py us-scan [--date YYYY-MM-DD [--to YYYY-MM-DD]] [--dry]
  python tools/buyback_shadow.py settle [--market kr|us|all] [--dry]
  python tools/buyback_shadow.py report
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SHADOW = ROOT / "data" / "shadow"
KR_LEDGER = SHADOW / "kr_buyback_shadow.jsonl"
KR_SETTLE = SHADOW / "kr_buyback_shadow_settle.jsonl"
US_LEDGER = SHADOW / "us_buyback_shadow.jsonl"
US_SETTLE = SHADOW / "us_buyback_shadow_settle.jsonl"
CACHE = SHADOW / "buyback_cache"
KR_PX = ROOT / "data" / "price" / "kr"
US_SEED = ROOT / "data" / "analysis" / "us_events_20260930" / "e2v2_events.csv.gz"
KST = timezone(timedelta(hours=9))

KR_COST = 0.25            # % 왕복
KR_FIXED_COST_L1 = 0.75   # % 왕복(고정 변형, 연구 lf = COST + 0.5)
KR_HS = (5, 20)
KR_RATIO_MIN = 0.01
KR_BAND_LO, KR_BAND_HI = 5e8, 2e9
VALUEUP_START = "2024-02-26"
US_COST = 0.005
US_HS = (20, 60)
US_SIZE_MIN = 0.10
US_DEDUPE = 60
GATE_N, GATE_T = 30, 2.0

# 2026 KRX 휴장일(069500 봉에서 2026-01~09 실측 확인: 01-01·02-16~18·03-02·05-01·05-05·05-25·06-03·07-17·08-17·09-24·09-25)
# 이후는 공휴일·대체공휴일 달력 기준 — 정산은 실제 069500 봉 달력으로 스냅한다.
KR_HOLIDAYS = {
    "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-02", "2026-05-01", "2026-05-05", "2026-05-25",
    "2026-06-03", "2026-07-17", "2026-08-17", "2026-09-24", "2026-09-25", "2026-10-05", "2026-10-09", "2026-12-25",
    "2026-12-31",
    "2027-01-01", "2027-02-08", "2027-02-09", "2027-03-01", "2027-03-03", "2027-05-05", "2027-05-13", "2027-08-16",
    "2027-09-14", "2027-09-15", "2027-09-16", "2027-10-04", "2027-10-11", "2027-12-27", "2027-12-31",
}


def log(tag: str, msg: str) -> None:
    print(f"[BUYBACK-{tag}] {msg}", flush=True)


# ------------------------------------------------------------------ HTTP (테스트에서 교체)
def _http_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0) -> bytes:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


HTTP: Callable[..., bytes] = _http_get


# ------------------------------------------------------------------ 원장 공통
def read_jsonl(p: Path) -> List[Dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def write_jsonl(p: Path, rows: List[Dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(p)


def _nz(x) -> Optional[float]:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


# ------------------------------------------------------------------ KRX 세션
def kr_is_session(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in KR_HOLIDAYS


def kr_prev_or_same(d: date) -> date:
    while not kr_is_session(d):
        d -= timedelta(days=1)
    return d


def kr_next_session(d: date) -> date:
    d += timedelta(days=1)
    while not kr_is_session(d):
        d += timedelta(days=1)
    return d


# ================================================================== 복사 블록 KR-1
# 출처: tools/research/research_kr_event_20260930.py 247~379행(MIN_HIST ~ date_series) 바이트 그대로.

MIN_HIST, DVOL_MIN, PX_MIN, GUARD = 25, 2e9, 1000.0, 0.305


def read_csv_bars(path: Path) -> list[tuple]:
    out = []
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.reader(fh):
                if len(r) >= 6 and r[0][:2] == "20":
                    try:
                        out.append((r[0][:10], float(r[1]), float(r[4]), float(r[5])))
                    except ValueError:
                        pass
    except Exception:
        return []
    return sorted(out)


class Book:
    """calendar(T) × tickers(N) 행렬: open, close, amt(=close×volume). 결측 NaN."""

    def __init__(self, bars: dict[str, list[tuple]], calendar: list[str]):
        self.cal = calendar
        self.ix = {d: i for i, d in enumerate(calendar)}
        self.tk = sorted(bars)
        self.col = {t: j for j, t in enumerate(self.tk)}
        T, N = len(calendar), len(self.tk)
        self.o = np.full((T, N), np.nan)
        self.c = np.full((T, N), np.nan)
        self.a = np.full((T, N), np.nan)
        for t, rows in bars.items():
            j = self.col[t]
            for d, o, c, v in rows:
                i = self.ix.get(d)
                if i is not None and c > 0:
                    self.o[i, j] = o if o > 0 else np.nan
                    self.c[i, j] = c
                    self.a[i, j] = c * v
        present = ~np.isnan(self.c)
        self.hist = np.cumsum(present, axis=0)
        # dvol20: 최근 20 달력행 중 존재 봉 평균(존재 ≥ 15)
        a0 = np.nan_to_num(self.a)
        cs = np.cumsum(a0, axis=0)
        cn = np.cumsum(present, axis=0)
        s20 = cs - np.vstack([np.zeros((20, N)), cs[:-20]])
        n20 = cn - np.vstack([np.zeros((20, N)), cn[:-20]])
        with np.errstate(invalid="ignore", divide="ignore"):
            self.dv20 = np.where(n20 >= 15, s20 / np.maximum(n20, 1), np.nan)
        # 가드: 직전 존재 종가 대비 시가·종가 ±30.5% 밖이면 bad
        prev = np.full((T, N), np.nan)
        last = np.full(N, np.nan)
        for i in range(T):
            prev[i] = last
            m = present[i]
            last = np.where(m, self.c[i], last)
        with np.errstate(invalid="ignore", divide="ignore"):
            bad = (np.abs(self.o / prev - 1) > GUARD) | (np.abs(self.c / prev - 1) > GUARD)
        self.badcum = np.cumsum(np.nan_to_num(bad.astype(float)), axis=0)
        self._bench: dict = {}

    def fwd(self, j: int | np.ndarray, e: int, h: int, entry_px=None):
        """진입 e 시가(또는 entry_px) → e+h-1 종가 수익(%) — 창 가드·결측 반영."""
        x = e + h - 1
        if x >= len(self.cal) or e < 1:
            return np.nan if np.isscalar(j) else np.full(len(j), np.nan)
        op = self.o[e, j] if entry_px is None else entry_px
        with np.errstate(invalid="ignore", divide="ignore"):
            r = (self.c[x, j] / op - 1.0) * 100.0
        badw = (self.badcum[x, j] - self.badcum[e - 1, j]) > 0
        if entry_px is not None:          # 장중 진입: 진입일 시가 점프는 이미 가격에 들어있음 → e 이후만
            badw = (self.badcum[x, j] - self.badcum[e, j]) > 0
        return np.where(badw, np.nan, r)

    def eligible(self, s: int, e: int):
        c = self.c[s]
        with np.errstate(invalid="ignore"):
            m = (c >= PX_MIN) & (self.dv20[s] >= DVOL_MIN) & (self.hist[s] >= MIN_HIST) & (self.o[e] > 0)
        return m

    def bench(self, s: int, e: int, h: int):
        """(decile edges, decile mean returns) — 신호일 s 적격 유니버스, e 시가→e+h-1 종가."""
        key = (s, e, h)
        if key in self._bench:
            return self._bench[key]
        m = self.eligible(s, e)
        idx = np.where(m)[0]
        res = (None, None)
        if len(idx) >= 30 and e + h - 1 < len(self.cal):
            dv = self.dv20[s, idx]
            r = self.fwd(idx, e, h)
            order = np.argsort(dv)
            edges = [dv[order[int(len(idx) * k / 10)]] for k in range(1, 10)]
            dec = np.searchsorted(edges, dv, side="right")
            means = []
            for k in range(10):
                rr = r[dec == k]
                rr = rr[~np.isnan(rr)]
                means.append(float(rr.mean()) if len(rr) >= 3 else np.nan)
            res = (edges, means)
        self._bench[key] = res
        return res

    def size_bench(self, s: int, e: int, h: int, dv: float) -> float:
        edges, means = self.bench(s, e, h)
        if edges is None:
            return float("nan")
        k = int(np.searchsorted(edges, dv, side="right")) if (dv == dv and dv is not None) else 0
        return means[k]


def nw_t(y: list[float], lag: int) -> tuple[float, float, int]:
    a = np.array([v for v in y if v == v], float)
    n = len(a)
    if n < 3:
        return (float(a.mean()) if n else float("nan"), float("nan"), n)
    m = a.mean()
    u = a - m
    g0 = (u @ u) / n
    v = g0
    for l in range(1, min(lag, n - 1) + 1):
        v += 2 * (1 - l / (lag + 1)) * (u[l:] @ u[:-l]) / n
    se = math.sqrt(max(v, 1e-12) / n)
    return float(m), float(m / se), n


def date_series(rows: list[dict], key: str, dkey: str = "entry") -> list[float]:
    by = defaultdict(list)
    for r in rows:
        v = r.get(key)
        if v is not None and v == v:
            by[r[dkey]].append(v)
    return [float(np.mean(by[d])) for d in sorted(by)]


# ================================================================== 복사 블록 KR-2
# 출처: tools/research/research_kr_event_20260930.py 1042~1097행(ar_spread · naver_ret · band_bench) 바이트 그대로.
def ar_spread(bars: list[tuple]) -> float | None:
    """Abdi-Ranaldo(2017) 스프레드(%) — bars = [(o,h,l,c,v)] 시간순 20봉."""
    if len(bars) < 10:
        return None
    vals = []
    for i in range(1, len(bars)):
        o, h, l, c, v = bars[i - 1]
        o2, h2, l2, c2, v2 = bars[i]
        if min(h, l, c, h2, l2) <= 0:
            continue
        eta0 = (math.log(h) + math.log(l)) / 2
        eta1 = (math.log(h2) + math.log(l2)) / 2
        vals.append((math.log(c) - eta0) * (math.log(c) - eta1))
    if not vals:
        return None
    s2 = 4 * float(np.mean(vals))
    return math.sqrt(max(s2, 0.0)) * 100.0


def naver_ret(nb: dict, cal: list[str], e: int, h: int, *, exdate: str | None = None) -> float | None:
    x = e + h - 1
    if x >= len(cal):
        return None
    ds = cal[e - 1:x + 1]
    if any(d not in nb for d in ds):
        return None
    for i in range(1, len(ds)):
        pc = nb[ds[i - 1]][3]
        lim = 0.45 if ds[i] == exdate else GUARD
        o, c = nb[ds[i]][0], nb[ds[i]][3]
        if pc <= 0 or o <= 0 or abs(o / pc - 1) > lim or abs(c / pc - 1) > lim:
            return None
    return (nb[cal[x]][3] / nb[cal[e]][0] - 1.0) * 100.0


def band_bench(bk: Book, s: int, e: int, h: int, dv: float, lo=5e8, hi=2e9) -> float:
    key = ("band", s, e, h)
    if key not in bk._bench:
        c = bk.c[s]
        with np.errstate(invalid="ignore"):
            m = (c >= PX_MIN) & (bk.dv20[s] >= lo) & (bk.dv20[s] < hi) & (bk.hist[s] >= MIN_HIST) & (bk.o[e] > 0)
        idx = np.where(m)[0]
        res = (None, None)
        if len(idx) >= 30 and e + h - 1 < len(bk.cal):
            d = bk.dv20[s, idx]
            r = bk.fwd(idx, e, h)
            order = np.argsort(d)
            edges = [d[order[len(idx) // 3]], d[order[2 * len(idx) // 3]]]
            dec = np.searchsorted(edges, d, side="right")
            res = (edges, [float(np.nanmean(r[dec == k])) if np.sum(~np.isnan(r[dec == k])) >= 3 else np.nan
                           for k in range(3)])
        bk._bench[key] = res
    edges, means = bk._bench[key]
    if edges is None:
        return float("nan")
    return means[int(np.searchsorted(edges, dv, side="right"))]


# ------------------------------------------------------------------ KR: 분류·필드
# 출처: runtime/kr_event_lane.py KINDS(69~79행) 와 research event_kind(576~588행)의 판정 순서를 read-only 복사.
KR_BUYBACK_PATS = ("자기주식취득결정", "자기주식 취득 결정", "자기주식취득 결정")
_KR_EARLIER_PATS = ("공급계약체결", "무상증자결정", "무상증자 결정")   # KINDS 에서 buyback 보다 먼저 매칭되는 종류


def kr_kind(nm: str) -> Optional[str]:
    """'buyback'(직접취득) · 'trust'(신탁, 제외 대상) · None/'other'. 원공시만('['로 시작 제외), 정정 제외."""
    nm = (nm or "").strip()
    if nm.startswith("["):
        return None
    if "자기주식" in nm and "신탁계약" in nm:
        return "trust" if ("체결" in nm and "해지" not in nm) else None
    if "유무상증자" in nm:
        return "bonus_mixed"
    corr = ("정정" in nm) or ("[기재정정]" in nm) or ("[정정]" in nm)
    if any(p in nm for p in _KR_EARLIER_PATS):
        return None if corr else "other"
    if any(p in nm for p in KR_BUYBACK_PATS):
        return None if corr else "buyback"
    return None if corr else "other"


# 출처: research share_year(645~647행) · _numv(683~688행) 그대로.
def share_year(rcept_dt: str) -> str:
    y, m = int(rcept_dt[:4]), int(rcept_dt[4:6])
    return str(y - 1 if m >= 4 else y - 2)


def _numv(s) -> float | None:
    try:
        s = str(s).replace(",", "").strip()
        return float(s) if s not in ("", "-") else None
    except ValueError:
        return None


# ------------------------------------------------------------------ DART (키는 읽기만, 출력·원장 기록 금지)
DART_SLEEP, DART_BACKOFF, DART_MAX_CALLS = 0.5, 2.0, 600
_DART_CALLS = [0]


class DartError(Exception):
    pass


def _dart_key() -> str:
    k = (os.getenv("DART_API_KEY") or "").strip()
    if k:
        return k
    try:  # 연구·레인과 같은 .env.live 의 DART_API_KEY (os.environ 은 건드리지 않는다)
        from dotenv import dotenv_values
        return str(dotenv_values(ROOT / ".env.live").get("DART_API_KEY") or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _scrub(msg, key: str = "") -> str:
    s = str(msg)
    if key:
        s = s.replace(key, "***")
    return re.sub(r"crtfc_key=[^&\s'\"]+", "crtfc_key=***", s)


def dart(endpoint: str, **params) -> dict:
    key = _dart_key()
    if not key:
        raise DartError("DART_API_KEY 없음")
    if _DART_CALLS[0] >= DART_MAX_CALLS:
        raise DartError("실행당 DART 호출 상한")
    q = urllib.parse.urlencode({"crtfc_key": key, **params})
    last = ""
    for attempt in range(3):
        _DART_CALLS[0] += 1
        try:
            d = json.loads(HTTP(f"https://opendart.fss.or.kr/api/{endpoint}.json?{q}", None, 25.0))
        except Exception as exc:  # noqa: BLE001
            last = _scrub(f"{type(exc).__name__}: {exc}", key)
            time.sleep(DART_BACKOFF * (attempt + 1))
            continue
        time.sleep(DART_SLEEP)
        if d.get("status") == "020":
            raise DartError("DART 020 요청 제한 초과")
        return d
    raise DartError(_scrub(f"{endpoint} 호출 실패 — {last}", key)) from None


def kr_list(bgn: date, end: date, corp_code: Optional[str] = None) -> List[dict]:
    rows, page = [], 1
    while True:
        p = {"bgn_de": bgn.strftime("%Y%m%d"), "end_de": end.strftime("%Y%m%d"), "pblntf_detail_ty": "B001",
             "page_no": page, "page_count": 100}
        if corp_code:
            p["corp_code"] = corp_code
        d = dart("list", **p)
        st = d.get("status")
        if st == "013":
            break
        if st != "000":
            raise DartError(f"list status {st}")
        rows += d.get("list") or []
        if page >= int(d.get("total_page") or 1):
            break
        page += 1
    return rows


def kr_bb_fields(corp_code: str, rcept_no: str, rcept_dt: str, today: date) -> Dict:
    """DS005 tsstkAqDecsn → 취득예정 보통주. 원공시 rcept_no 우선, 없으면 research followup_chain 규칙(후속 서류 가장 이른 본)."""
    d = dart("tsstkAqDecsn", corp_code=corp_code, bgn_de=rcept_dt, end_de=today.strftime("%Y%m%d"))
    items = {it.get("rcept_no"): it for it in (d.get("list") or [])} if d.get("status") == "000" else {}
    it, via = items.get(rcept_no), None
    if it is not None:
        via = "orig"
    elif items:
        rd = datetime.strptime(rcept_dt, "%Y%m%d").date()
        for r in sorted(kr_list(rd, today, corp_code), key=lambda x: x.get("rcept_no") or ""):
            rc = r.get("rcept_no") or ""
            if rc <= rcept_no:
                continue
            nm = (r.get("report_nm") or "").strip()
            if kr_kind(re.sub(r"^(\[[^\]]*\])+", "", nm).strip()) != "buyback":
                continue
            if not nm.startswith("["):
                break          # 다음 원공시 = 체인 끝
            if rc in items:
                it, via = items[rc], "followup"
                break
    return {"bb_shares": _numv(it.get("aqpln_stk_ostk")) if it else None, "fields_via": via,
            "ds005_status": d.get("status"), "ds005_n": len(items), "found": it is not None}


def kr_shares_out(corp_code: str, year: str) -> Optional[float]:
    """출처: research cmd_shares(650~680행) — 보통주 istc_totqy, 없으면 합계."""
    d = dart("stockTotqySttus", corp_code=corp_code, bsns_year=year, reprt_code="11011")
    if d.get("status") not in ("000", "013"):
        return None
    sh = None
    for r in d.get("list") or []:
        if (r.get("se") or "").strip() == "보통주":
            sh = _numv(r.get("istc_totqy"))
    if sh is None:
        for r in d.get("list") or []:
            if (r.get("se") or "").strip() == "합계":
                sh = _numv(r.get("istc_totqy"))
    return sh


# ------------------------------------------------------------------ KR 가격
NAVER_SLEEP = 0.3
_NAVER: Dict[str, Dict[str, tuple]] = {}


def naver_daily(code: str, count: int = 500) -> Dict[str, tuple]:
    """네이버 fchart 수정주가 일봉 {date: (o,h,l,c,v)} — 정규식은 research naver_full(1015~1034행) 그대로, 실행 내 메모만."""
    if code in _NAVER:
        return _NAVER[code]
    u = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count={count}&requestType=0"
    out: Dict[str, tuple] = {}
    for attempt in range(2):
        try:
            s = HTTP(u, None, 25.0).decode("cp949", "ignore")
            for m in re.finditer(r'data="(\d{8})\|([\d.]+)\|([\d.]+)\|([\d.]+)\|([\d.]+)\|(\d+)"', s):
                d = m.group(1)
                out[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = tuple(float(m.group(i)) for i in range(2, 7))
            break
        except Exception:  # noqa: BLE001
            time.sleep(2)
    time.sleep(NAVER_SLEEP)
    _NAVER[code] = out
    return out


def kr_load_book(start: str, need: set) -> Book:
    """data/price/kr 유니버스(연구 Part 1과 같은 소스) + 결측·지연 종목은 네이버 보충."""
    bars: Dict[str, list] = {}
    for p in KR_PX.glob("kr_*.csv"):
        b = [r for r in read_csv_bars(p) if r[0] >= start]
        if b:
            bars[p.stem[3:]] = b
    cal = [r[0] for r in bars["069500"]]
    for t in sorted(need):
        have = bars.get(t) or []
        if have and have[-1][0] >= cal[-1]:
            continue
        nb = naver_daily(t)
        got = {r[0] for r in have}
        add = [(d, v[0], v[3], v[4]) for d, v in nb.items() if d >= start and d not in got]
        if add:
            bars[t] = sorted(have + add)
    return Book(bars, cal)


# ------------------------------------------------------------------ KR 스캔
KR_RULES = ("Y1", "L1_lowliq")


def _kr_raw(r: dict) -> dict:
    return {k: (r.get(k) or "").strip() if isinstance(r.get(k), str) else r.get(k)
            for k in ("rcept_no", "rcept_dt", "corp_code", "corp_name", "stock_code", "corp_cls", "report_nm")}


def kr_build_row(raw: dict, old: Optional[dict], bk: Optional[Book], now: datetime, today: date) -> Dict:
    rc, rdt = raw["rcept_no"], raw["rcept_dt"]
    rd = datetime.strptime(rdt, "%Y%m%d").date()
    D = kr_prev_or_same(rd)
    e = kr_next_session(D)
    detected = (old or {}).get("detected_at_kst") or now.strftime("%Y-%m-%d %H:%M")
    det_dt = datetime.strptime(detected, "%Y-%m-%d %H:%M")
    row = {"key": f"KR|{rc}", "market": "KR", **raw, "kind": "buyback_direct",
           "signal_date": D.isoformat(), "entry_date": e.isoformat(),
           "valueup_period": rd.isoformat() >= VALUEUP_START, "hold_sessions": list(KR_HS), "cost_roundtrip_pct": KR_COST,
           "detected_at_kst": detected, "actionable": det_dt < datetime.combine(e, datetime.min.time()).replace(hour=9)}
    row["backfill"] = not row["actionable"]
    reasons: List[str] = []
    # 규모 필드(한 번 얻으면 재사용)
    bb = (old or {}).get("bb_shares")
    via = (old or {}).get("fields_via")
    found = bool((old or {}).get("bb_found"))
    if not found:
        f = kr_bb_fields(raw["corp_code"], rc, rdt, today)
        bb, via, found = f["bb_shares"], f["fields_via"], f["found"]
        row["ds005_status"] = f["ds005_status"]
    yr = share_year(rdt)
    so = (old or {}).get("shares_out")
    if so is None and (old or {}).get("shares_year") != yr:
        so = kr_shares_out(raw["corp_code"], yr)
    row.update({"bb_shares": bb, "bb_found": found, "fields_via": via, "shares_out": so, "shares_year": yr,
                "ratio": (bb / so) if (bb and so) else None})
    fields_pending = not found                       # DS005 에 아직 없음(지연) → 다음 실행에서 재조회
    if fields_pending:
        reasons.append("fields_pending(ds005)")
    elif not bb:                                     # 항목은 있으나 보통주 취득예정 없음('-'·0) → 확정 탈락
        reasons.append("no_bb_common_shares")
    elif not so:
        reasons.append("no_shares_out")
    elif row["ratio"] < KR_RATIO_MIN:
        reasons.append("ratio<1%")
    # 가격(D 종가·dvol20·이력)
    px_pending, tier = False, None
    if bk is None or D.isoformat() not in bk.ix:
        px_pending = True
        reasons.append("px_pending")
    else:
        s = bk.ix[D.isoformat()]
        j = bk.col.get(raw["stock_code"])
        if j is None or np.isnan(bk.c[s, j]):
            reasons.append("no_price")
        else:
            c, dv, hs = float(bk.c[s, j]), _nz(bk.dv20[s, j]), int(bk.hist[s, j])
            row.update({"close_D": c, "dvol20_D": dv, "hist_D": hs})
            if c < PX_MIN:
                reasons.append("close<1000")
            elif hs < MIN_HIST:
                reasons.append("hist<25")
            elif dv is None or dv < KR_BAND_LO:
                reasons.append("dvol20<5억")
            else:
                tier = "main" if dv >= DVOL_MIN else "band"
            if tier == "band":
                nb = naver_daily(raw["stock_code"])
                prior = [nb[bk.cal[i]] for i in range(max(0, s - 19), s + 1) if bk.cal[i] in nb]
                row["spread_ar_pct"] = ar_spread(prior)
                row["spread_n_bars"] = len(prior)
    row["tier"] = tier
    row["rule"] = ({"main": "Y1", "band": "L1_lowliq"}.get(tier)
                   if (tier and row["ratio"] is not None and row["ratio"] >= KR_RATIO_MIN) else None)
    row["fields_pending"], row["px_pending"] = fields_pending, px_pending
    row["pending"] = fields_pending or px_pending
    row["reject_reasons"] = reasons
    row["updated_at_kst"] = now.strftime("%Y-%m-%d %H:%M")
    return row


def kr_apply_dup(rows: Dict[str, Dict]) -> None:
    """같은 (종목, D) 직접취득은 rcept_no 가장 이른 1건(research part2 seen 규칙)."""
    first: Dict[Tuple[str, str], str] = {}
    for r in sorted(rows.values(), key=lambda x: x["rcept_no"]):
        k = (r.get("stock_code"), r["signal_date"])
        first.setdefault(k, r["rcept_no"])
    for r in rows.values():
        r["dup_same_day"] = first[(r.get("stock_code"), r["signal_date"])] != r["rcept_no"]
        r["qualifies"] = bool(r.get("rule") and not r["pending"] and not r["dup_same_day"])


def _daterange(d0: date, d1: date) -> List[date]:
    out, d = [], d0
    while d <= d1:
        out.append(d)
        d += timedelta(days=1)
    return out


def kr_scan(d0: date, d1: date, dry: bool, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(KST).replace(tzinfo=None)
    today = now.date()
    ledger = {r["key"]: r for r in read_jsonl(KR_LEDGER)}
    raws: Dict[str, dict] = {}
    per_day = {}
    for d in _daterange(d0, d1):
        rows = kr_list(d, d)
        bb = [r for r in rows if kr_kind(r.get("report_nm") or "") == "buyback"]
        per_day[d.isoformat()] = (len(rows), len(bb), sum(1 for r in bb if (r.get("stock_code") or "").strip()))
        for r in bb:
            x = _kr_raw(r)
            if x["stock_code"]:
                raws[x["rcept_no"]] = x
    # 대기(pending) 행은 날짜 창 밖이어도 재평가(최근 14일)
    for k, r in ledger.items():
        if r.get("pending") and r["rcept_no"] not in raws and \
                (today - datetime.strptime(r["rcept_dt"], "%Y%m%d").date()).days <= 14:
            raws[r["rcept_no"]] = {f: r.get(f) for f in ("rcept_no", "rcept_dt", "corp_code", "corp_name", "stock_code",
                                                          "corp_cls", "report_nm")}
    for d, (n, nb, nc) in per_day.items():
        log("KR", f"{d} B001 {n}건 · 직접취득 {nb}건(종목코드 {nc})")
    todo = [x for x in raws.values() if not (ledger.get(f"KR|{x['rcept_no']}") and
                                             not ledger[f"KR|{x['rcept_no']}"].get("pending"))]
    bk = None
    if todo:
        start = (min(kr_prev_or_same(datetime.strptime(x["rcept_dt"], "%Y%m%d").date()) for x in todo)
                 - timedelta(days=120)).isoformat()
        bk = kr_load_book(start, {x["stock_code"] for x in todo} | {"069500", "229200"})
    rows = dict(ledger)
    changed = []
    for x in sorted(todo, key=lambda z: z["rcept_no"]):
        k = f"KR|{x['rcept_no']}"
        rows[k] = kr_build_row(x, ledger.get(k), bk, now, today)
        changed.append(k)
    kr_apply_dup(rows)
    for k in changed:
        r = rows[k]
        log("KR", f"{'DRY ' if dry else ''}{r['rcept_dt']} {r['stock_code']} {r.get('corp_name', '')[:12]:12s} "
                  f"D={r['signal_date']} 진입={r['entry_date']} 비율={_pctf(r.get('ratio'))} via={r.get('fields_via')} "
                  f"종가={r.get('close_D')} dvol20={_eok(r.get('dvol20_D'))} tier={r.get('tier')} rule={r.get('rule')} "
                  f"qualifies={r['qualifies']} actionable={r['actionable']} spread={_f2(r.get('spread_ar_pct'))} "
                  f"{','.join(r['reject_reasons'])}{' DUP' if r['dup_same_day'] else ''}")
    q = [rows[k] for k in changed if rows[k]["qualifies"]]
    log("KR", f"처리 {len(changed)}건 · 채택 {len(q)}건 (Y1 {sum(r['rule'] == 'Y1' for r in q)} · "
              f"L1 {sum(r['rule'] == 'L1_lowliq' for r in q)}) · 대기 {sum(rows[k]['pending'] for k in changed)} · "
              f"DART 호출 {_DART_CALLS[0]}")
    if not dry and changed:
        write_jsonl(KR_LEDGER, sorted(rows.values(), key=lambda r: r["rcept_no"]))
    return 0


def _pctf(x) -> str:
    return "NA" if x is None else f"{x * 100:.2f}%"


def _f2(x) -> str:
    return "NA" if x is None else f"{x:.2f}"


def _eok(x) -> str:
    return "NA" if x is None else f"{x / 1e8:.1f}억"


# ------------------------------------------------------------------ KR 정산
def kr_settle_rows(rows: List[Dict], bk: Book, done: set) -> List[Dict]:
    cal = bk.cal
    k2, q2 = bk.col.get("069500"), bk.col.get("229200")
    out = []
    for r in rows:
        D = r["signal_date"]
        if D not in bk.ix:
            continue
        s = bk.ix[D]
        e = s + 1
        j = bk.col.get(r["stock_code"])
        if j is None or e >= len(cal):
            continue
        dv = float(bk.dv20[s, j]) if bk.dv20[s, j] == bk.dv20[s, j] else float("nan")
        nb = naver_daily(r["stock_code"])
        for h in KR_HS:
            sk = f"{r['key']}|h{h}"
            x = e + h - 1
            if sk in done or x >= len(cal):
                continue
            ry = bk.fwd(j, e, h)
            ry = float(ry) if ry == ry else None
            rn = naver_ret(nb, cal, e, h)
            ki, kq = bk.fwd(k2, e, h), bk.fwd(q2, e, h)
            ki = float(ki) if ki == ki else None
            kq = float(kq) if kq == kq else None
            rec = {"skey": sk, "key": r["key"], "rule": r["rule"], "stock_code": r["stock_code"], "h": h,
                   "entry": cal[e], "exit": cal[x], "entry_ledger": r["entry_date"], "entry_open": _nz(bk.o[e, j]),
                   "r_px": ry, "r_naver": rn, "kospi200": ki, "kosdaq150": kq, "valueup_period": r.get("valueup_period"),
                   "actionable": r.get("actionable"), "settled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            # 실주문 계약(10-30 대조): 캐너리는 e+h번째 세션 **시가**에 판다(장전 시장가·예약매도) — 연구 계약(e+h-1 종가)보다
            # 하룻밤 길다. 판정이 실제로 거래한 계약을 재도록 같이 기록한다. 다음 시가가 아직 없으면 None(보조 필드).
            if x + 1 < len(cal) and bk.o[e, j] == bk.o[e, j] and bk.o[e, j] > 0 and bk.o[x + 1, j] == bk.o[x + 1, j]:
                rec["exit_live"] = cal[x + 1]
                rec["r_live_open"] = round(float((bk.o[x + 1, j] / bk.o[e, j] - 1.0) * 100.0), 4)
            if r["rule"] == "Y1":
                rec["elig_final"] = bool(bk.eligible(s, e)[j])
                sb = bk.size_bench(s, e, h, dv)
                rec["size_bench"] = _nz(sb)
                rec["ex"] = ry - KR_COST - sb if (ry is not None and sb == sb) else None
                rec["xk"] = ry - KR_COST - ki if (ry is not None and ki is not None) else None
                rec["xq"] = ry - KR_COST - kq if (ry is not None and kq is not None) else None
                rec["nx"] = rn - KR_COST - sb if (rn is not None and sb == sb) else None
            else:
                c_ok = bk.c[s, j] >= PX_MIN and KR_BAND_LO <= dv < KR_BAND_HI and bk.hist[s, j] >= MIN_HIST
                rec["elig_final"] = bool(c_ok and bk.o[e, j] > 0)
                spr = r.get("spread_ar_pct")
                if spr is None:
                    prior = [nb[cal[i]] for i in range(max(0, s - 19), s + 1) if cal[i] in nb]
                    spr = ar_spread(prior)
                rec["spread_ar_pct"] = spr
                bb = band_bench(bk, s, e, h, dv)
                rec["band_bench"] = _nz(bb)
                ok = rn is not None and spr is not None
                rec["lx"] = rn - KR_COST - spr - bb if (ok and bb == bb) else None
                rec["lf"] = rn - KR_FIXED_COST_L1 - bb if (rn is not None and bb == bb) else None
                rec["lxk"] = rn - KR_COST - spr - ki if (ok and ki is not None) else None
                rec["lxq"] = rn - KR_COST - spr - kq if (ok and kq is not None) else None
                rec["ly"] = ry - KR_COST - spr - bb if (ry is not None and spr is not None and bb == bb) else None
            out.append(rec)
    return out


def kr_settle(dry: bool) -> int:
    rows = [r for r in read_jsonl(KR_LEDGER) if r.get("qualifies") and r.get("rule") in KR_RULES]
    old = {x["skey"]: x for x in read_jsonl(KR_SETTLE)}
    due = [r for r in rows if any(f"{r['key']}|h{h}" not in old for h in KR_HS)]
    if not due:
        log("KR", f"정산 대상 없음 (채택 {len(rows)} · 정산 {len(old)})")
        return 0
    start = (min(date.fromisoformat(r["signal_date"]) for r in due) - timedelta(days=120)).isoformat()
    bk = kr_load_book(start, {r["stock_code"] for r in due} | {"069500", "229200"})
    new = kr_settle_rows(due, bk, set(old))
    log("KR", f"신규 정산 {len(new)}건 (달력 끝 {bk.cal[-1]})")
    for x in new:
        main = x.get("ex") if x["rule"] == "Y1" else x.get("lx")
        log("KR", f"{x['rule']:9s} {x['stock_code']} h{x['h']} {x['entry']}→{x['exit']} 초과={_f2(main)}% "
                  f"KOSPI200대비={_f2(x.get('xk') if x['rule'] == 'Y1' else x.get('lxk'))}% elig={x['elig_final']}")
    if not dry and new:
        old.update({x["skey"]: x for x in new})
        write_jsonl(KR_SETTLE, list(old.values()))
    return 0


# ================================================================== 복사 블록 US-1
# 출처: tools/research/research_us_events_20260930.py 642~669행(TAG ~ num) 바이트 그대로.
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


# ================================================================== 복사 블록 US-2
# 출처: tools/research/research_us_events_20260930.py 846~907행(RETRO ~ parse_buyback_v2) 바이트 그대로.
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


# ------------------------------------------------------------------ US: v3 증액분 보정 (10-10)
# v2(연구 원본과 바이트 동일 — 계약 테스트)는 그대로 두고, 결과의 usd만 문장에서 다시 본다.
#   ① 명시 증액분("$750 million increase" / "increase of|by $X" / "additional $X") → X
#   ② "from $Y to $X" → X − Y
#   ③ 증액 동사 뒤 "to $X"만 있고 증액분이 없다(LEA "Increases ... to $1.5 Billion") → usd None(규모 미상)
# 근거: GEO 10-08 $1.25B(총액) 채택·LEA 09-24 총액 25.7% 채택. 검증 표본 T(총액 혼동) 17건 중 v2 잔존 오류 유형.
_U = r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(million|billion|mm|bn)?"
INC_EXPL = re.compile(_U + r"\s+(?:increase|addition|expansion)\b|"
                      r"(?:increase[sd]?|increasing|expand(?:s|ed|ing)?|upsiz\w*)\s+(?:\w+\s+){0,8}?(?:of|by)\s+"
                      r"(?:an?\s+)?(?:additional\s+)?(?:approximately\s+)?" + _U + r"|"
                      r"\badditional\s+(?:approximately\s+)?" + _U, re.I)
FROM_TO = re.compile(r"\bfrom\s+(?:approximately\s+)?" + _U + r"\s+to\s+(?:approximately\s+)?" + _U, re.I)
TOTAL_ONLY = re.compile(r"(?:increas|expand|rais|upsiz)\w*\b[^.]{0,160}?(?<!up )\bto\s+(?:approximately\s+)?" + _U, re.I)


def _usd(v, u):
    x = num(v)
    u = (u or "").lower()
    return x * (1e9 if u in ("billion", "bn") else (1e6 if u in ("million", "mm") else 1))


def parse_buyback_v3(snip, fdate):
    p = parse_buyback_v2(snip, fdate)
    if not p or p.get("usd") is None:
        return p
    s = next((x for x in (snip or "").split(" || ") if x.startswith(p["sent"])), p["sent"])
    m = INC_EXPL.search(s)
    if m:
        g = [x for x in m.groups()]
        for i in range(0, len(g), 2):
            if g[i]:
                v = _usd(g[i], g[i + 1])
                if v >= 1e5:
                    return {**p, "usd": v, "usd_basis": "increment"}
    m = FROM_TO.search(s)
    if m:
        a, b = _usd(m.group(1), m.group(2)), _usd(m.group(3), m.group(4))
        if b > a >= 0 and b - a >= 1e5:
            return {**p, "usd": b - a, "usd_basis": "from_to"}
    for m in TOTAL_ONLY.finditer(s):   # 배당 "to $0.50 per share" 같은 비자사주 금액은 제외
        if _usd(m.group(1), m.group(2)) >= 1e5 and not re.match(r"\s*per share", s[m.end():], re.I):
            return {**p, "usd": None, "usd_basis": "total_only"}
    return {**p, "usd_basis": "v2"}


# ------------------------------------------------------------------ US: 세션·시각
def _ins():
    """NYSE 달력·yfinance 행렬·연구 유니버스는 tools/us_insider_cluster_shadow.py 를 read-only import."""
    p = str(ROOT / "tools")
    if p not in sys.path:
        sys.path.insert(0, p)
    import us_insider_cluster_shadow as ins
    return ins


def us_is_session(d: date) -> bool:
    return _ins().is_session(d)


def us_next_session(d: date) -> date:
    return _ins().next_session(d)


def us_prev_or_same(d: date) -> date:
    return _ins().prev_or_same_session(d)


def us_sessions_between(a: date, b: date) -> int:
    """a(포함)~b(제외) NYSE 세션 수 = 연구 dedupe 의 진입 인덱스 차(p − lp)."""
    return int(np.busday_count(a.isoformat(), b.isoformat(), holidays=sorted(_ins().NYSE_HOLIDAYS)))


def _nth_sunday(y: int, m: int, n: int) -> date:
    d = date(y, m, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def utc_to_et(s: Optional[str]) -> Optional[datetime]:
    """SEC acceptanceDateTime('...Z', UTC — 2026-09-30 실측: 20:22:45Z = index 페이지 Accepted 16:22:45 EDT) → ET."""
    if not s:
        return None
    dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
    y = dt.year
    dst_on = datetime.combine(_nth_sunday(y, 3, 2), datetime.min.time()) + timedelta(hours=7)    # 02:00 EST
    dst_off = datetime.combine(_nth_sunday(y, 11, 1), datetime.min.time()) + timedelta(hours=6)  # 02:00 EDT
    return dt - timedelta(hours=4 if dst_on <= dt < dst_off else 5)


def now_et() -> datetime:
    return utc_to_et(datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"))


# ------------------------------------------------------------------ SEC (≤ 4 req/s, 저장소 UA, 429/403 백오프)
UA = "claudetrade research smyang03@gmail.com"  # research_us_events_20260930.py 90행 — 저장소 기존 SEC UA
SEC_RATE = 3.5
SEC_BACKOFF = 60.0
_SEC_LAST = [0.0]
_SEC_CALLS = [0]


def sec_get(url: str, tries: int = 6) -> Optional[bytes]:
    for k in range(tries):
        w = _SEC_LAST[0] + 1.0 / SEC_RATE - time.time()
        if w > 0:
            time.sleep(w)
        _SEC_LAST[0] = time.time()
        _SEC_CALLS[0] += 1
        try:
            return HTTP(url, {"User-Agent": UA, "Accept-Encoding": "identity"}, 40.0)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code in (403, 429):
                wait = min(600.0, SEC_BACKOFF * (k + 1))
                log("US", f"SEC {e.code} 백오프 {wait:.0f}s")
                time.sleep(wait)
                continue
            time.sleep(min(30.0, SEC_BACKOFF / 12 * (k + 1)))
        except Exception:  # noqa: BLE001
            time.sleep(min(30.0, SEC_BACKOFF / 20 * (k + 1)))
    return None


# 출처: research 136~138행 E2_Q (동결 질의 4종)
E2_Q = ['"repurchase program" authorized', '"repurchase program" approved',
        '"repurchase plan" authorized', '"buyback program" authorized']


def fts_day(q: str, d: date) -> Optional[List[Dict]]:
    """research fts_window(156~177행)와 같은 질의·필드, 하루 창."""
    rows, frm = [], 0
    while True:
        u = ("https://efts.sec.gov/LATEST/search-index?q=" + urllib.parse.quote(q)
             + f"&forms=8-K&dateRange=custom&startdt={d.isoformat()}&enddt={d.isoformat()}&from={frm}")
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
                         "items": ",".join(src.get("items") or []), "file_type": src.get("file_type") or "",
                         "form": src.get("form")})
        frm += len(hs)
        if not hs or frm >= tot or frm >= 9900:
            return rows


def pick_docs(hits: List[Dict]) -> List[Dict]:
    """adsh 당 문서 1개: EX-99 우선, 그다음 8-K 본문(research e2_events 102~108행 pri 규칙)."""
    best: Dict[str, Tuple[int, str, Dict]] = {}
    for h in hits:
        if h.get("form") != "8-K" or not h.get("adsh"):
            continue
        ft = h.get("file_type") or ""
        pri = 0 if ft.startswith("EX-99") else (1 if ft.startswith("8-K") else 2)
        k = (pri, h["id"])
        if h["adsh"] not in best or k < best[h["adsh"]][:2]:
            best[h["adsh"]] = (pri, h["id"], h)
    return [v[2] for v in best.values()]


def doc_url(cik, id_):
    # 출처: research doc_url(637~639행)
    adsh, fn = id_.split(":", 1)
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{adsh.replace('-', '')}/{fn}"


def ticker_map(today: date) -> Dict[int, str]:
    """SEC company_tickers.json(현재) — research ticker_map(246~251행) 규칙: CIK 첫 티커, '.'→'-'."""
    CACHE.mkdir(parents=True, exist_ok=True)
    fp = CACHE / f"company_tickers_{today.isoformat()}.json"
    if fp.exists():
        j = json.loads(fp.read_text(encoding="utf-8"))
    else:
        b = sec_get("https://www.sec.gov/files/company_tickers.json")
        if b is None:
            olds = sorted(CACHE.glob("company_tickers_*.json"))
            if not olds:
                return {}
            j = json.loads(olds[-1].read_text(encoding="utf-8"))
        else:
            j = json.loads(b)
            fp.write_text(json.dumps(j), encoding="utf-8")
    m: Dict[int, str] = {}
    for v in j.values():
        m.setdefault(int(v["cik_str"]), str(v["ticker"]).upper().replace(".", "-"))
    return m


def acceptance_raw(cik: int, adsh: str, memo: Dict) -> Optional[str]:
    if cik not in memo:
        b = sec_get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
        rec = {}
        if b:
            r = json.loads(b).get("filings", {}).get("recent", {})
            rec = dict(zip(r.get("accessionNumber", []), r.get("acceptanceDateTime", [])))
        memo[cik] = rec
    return memo[cik].get(adsh)


def dei_shares(cik: int, fdate: str) -> Dict:
    """최신 dei:EntityCommonStockSharesOutstanding (filed ≤ file_date · end ≤ file_date, 최신 end → 최신 filed).
    550일보다 오래되면 결측(research shares_asof 766~781행 규칙)."""
    b = sec_get(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{int(cik):010d}/dei/EntityCommonStockSharesOutstanding.json")
    if not b:
        return {"so": None, "so_end": None, "so_filed": None}
    facts = [f for f in json.loads(b).get("units", {}).get("shares", [])
             if f.get("end") and f.get("filed") and f["end"] <= fdate and f["filed"] <= fdate and (f.get("val") or 0) > 0]
    if not facts:
        return {"so": None, "so_end": None, "so_filed": None}
    f = max(facts, key=lambda x: (x["end"], x["filed"]))
    if (date.fromisoformat(fdate) - date.fromisoformat(f["end"])).days > 550:
        return {"so": None, "so_end": f["end"], "so_filed": f["filed"]}
    return {"so": float(f["val"]), "so_end": f["end"], "so_filed": f["filed"]}


# ------------------------------------------------------------------ US 가격(스캔 시점: 이벤트 종목 + IWM, 분할 이력 포함)
def us_fetch_bars(tickers: List[str], start: str, end: str) -> Dict[str, pd.DataFrame]:
    import yfinance as yf
    tickers = sorted(set(tickers))
    df = None
    for attempt in range(3):
        try:
            df = yf.download(tickers, start=start, end=end, auto_adjust=False, actions=True, group_by="ticker",
                             threads=2, progress=False)
            break
        except Exception as exc:  # noqa: BLE001
            log("US", f"yfinance 재시도 {type(exc).__name__}")
            time.sleep(30)
    out: Dict[str, pd.DataFrame] = {}
    if df is None or len(df) == 0:
        return out
    for t in tickers:
        try:
            sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
        except KeyError:
            continue
        sub = sub.dropna(subset=["Close"])
        if len(sub) == 0:
            continue
        sp = sub["Stock Splits"] if "Stock Splits" in sub else pd.Series(0.0, index=sub.index)
        out[t] = pd.DataFrame({"date": sub.index.strftime("%Y-%m-%d"), "open": sub["Open"].astype(float).values,
                               "close": sub["Close"].astype(float).values, "volume": sub["Volume"].astype(float).values,
                               "split": sp.fillna(0).astype(float).values})
    return out


US_FETCH: Callable[[List[str], str, str], Dict[str, pd.DataFrame]] = us_fetch_bars


def us_px_features(bars: Dict[str, pd.DataFrame], ticker: str, a: str) -> Dict:
    """앵커 a: 원주가 종가 = Close[a](분할 소급조정) × a 이후 분할 누적배수, dv20 = IWM 달력 20행(≥15) 평균 종가×거래량."""
    iwm = bars.get("IWM")
    if iwm is None or not len(iwm):
        return {"px_status": "no_iwm"}
    cal = sorted(iwm.date.unique())
    if a > cal[-1]:
        return {"px_status": "pending"}
    b = bars.get(ticker)
    if b is None or not len(b):
        return {"px_status": "no_price"}
    b = b.drop_duplicates("date").set_index("date")
    x = b.reindex(cal)
    dv = (x.close * x.volume).rolling(20, min_periods=15).mean()
    if a not in x.index or not np.isfinite(x.close.get(a, np.nan)):
        return {"px_status": "no_bar_a"}
    spl = b.split[(b.index > a) & (b.split > 0)]
    f = float(np.prod(spl.values)) if len(spl) else 1.0
    c = float(x.close[a])
    return {"px_status": "ok", "close_a_splitadj": c, "split_factor_after_a": f, "raw_close_a": c * f,
            "dv20_a": _nz(dv[a])}


def us_size(p: Dict, raw_close: Optional[float], so: Optional[float]) -> Tuple[Optional[float], Optional[str]]:
    """research e2v2_build(948~953행): pct 우선 → usd ÷ 원주가 시총 → shares ÷ 발행주식, (0, 0.5] 밖 결측."""
    if p.get("pct") is not None:
        v, basis = p["pct"] / 100, "pct"
    elif p.get("usd") is not None:
        v, basis = (p["usd"] / (raw_close * so) if (raw_close and so) else None), "usd"
    elif p.get("shares") is not None:
        v, basis = (p["shares"] / so if so else None), "shares"
    else:
        return None, None
    if v is None or not math.isfinite(v) or v > 0.5 or v <= 0:
        return None, basis
    return float(v), basis


def us_seed_chain(cutoff: str) -> Tuple[Dict[str, date], Optional[str]]:
    """연구 e2v2_events(2.02 없음·승인 문장 있음, file_date < cutoff, 2025+)를 dedupe 이력으로 — 유지된 마지막 진입일."""
    if not US_SEED.exists():
        return {}, None
    d = pd.read_csv(US_SEED, usecols=["adsh", "ticker", "file_date", "has202", "sent"])
    d = d[(~d.has202.astype(bool)) & d.sent.notna() & d.ticker.notna() & (d.file_date >= "2025-01-02")
          & (d.file_date < cutoff)]
    rows = sorted((us_next_session(date.fromisoformat(f)), str(t), a) for f, t, a in zip(d.file_date, d.ticker, d.adsh))
    kept: Dict[str, date] = {}
    for t0, tk, _ in rows:
        lp = kept.get(tk)
        if lp is None or us_sessions_between(lp, t0) >= US_DEDUPE:
            kept[tk] = t0
    return kept, (str(d.file_date.max()) if len(d) else None)


def us_apply_dedupe(ledger: Dict[str, Dict], new: List[Dict]) -> None:
    """research dedupe(339~348행): 같은 티커, 마지막 '유지' 진입 대비 60세션 미만이면 탈락. 규모·가격 필터 전 적용."""
    allf = [r["file_date"] for r in list(ledger.values()) + new]
    cutoff = min(allf) if allf else "9999-12-31"
    kept, seed_max = us_seed_chain(cutoff)
    for r in sorted(ledger.values(), key=lambda x: (x["entry_date"], x["accession"])):
        if r.get("ticker") and not r.get("dedupe_drop"):
            t0 = date.fromisoformat(r["entry_date"])
            if r["ticker"] not in kept or t0 > kept[r["ticker"]]:
                kept[r["ticker"]] = t0
    hist_ok = bool(seed_max and (date.fromisoformat(cutoff) - date.fromisoformat(seed_max)).days <= 7)
    for r in sorted(new, key=lambda x: (x["entry_date"], x["accession"])):
        r["dedupe_history_complete"] = hist_ok
        if not r.get("ticker"):
            r["dedupe_drop"] = False
            continue
        t0 = date.fromisoformat(r["entry_date"])
        lp = kept.get(r["ticker"])
        r["dedupe_prev_entry"] = lp.isoformat() if lp else None
        if lp is not None and us_sessions_between(lp, t0) < US_DEDUPE:
            r["dedupe_drop"] = True
        else:
            r["dedupe_drop"] = False
            kept[r["ticker"]] = t0


def us_finalize(r: Dict) -> None:
    reasons = list(r.get("reject_reasons") or [])
    if not r.get("ticker"):
        reasons.append("no_ticker")
    if r.get("dedupe_drop"):
        reasons.append("dedupe60")
    if r.get("px_status") != "ok":
        reasons.append(f"px_{r.get('px_status')}")
    else:
        if (r.get("raw_close_a") or 0) < 3:
            reasons.append("close<3")
        if (r.get("dv20_a") or 0) < 1e6:
            reasons.append("dv20<1M")
    if r.get("size") is None:
        reasons.append("size_na")
    elif r["size"] < US_SIZE_MIN:
        reasons.append("size<10%")
    r["reject_reasons"] = reasons
    r["qualifies"] = not reasons


def us_scan(d0: date, d1: date, dry: bool, now: Optional[datetime] = None) -> int:
    now = now or now_et()
    ledger = {r["key"]: r for r in read_jsonl(US_LEDGER)}
    hits, failed = [], []
    for d in _daterange(d0, d1):
        if d.weekday() >= 5:
            continue
        for q in E2_Q:
            rows = fts_day(q, d)
            if rows is None:
                failed.append(d.isoformat())
                break
            hits += rows
    if failed:
        log("US", f"FTS 실패 날짜 {sorted(set(failed))} — 다음 실행에서 재시도")
        hits = [h for h in hits if h.get("file_date") not in set(failed)]
    docs = pick_docs(hits)
    tmap = ticker_map(now.date()) if docs else {}
    n202 = nparse = nfetch = 0
    cands = []
    for h in sorted(docs, key=lambda x: (x["file_date"], x["adsh"])):
        key = f"US|{h['adsh']}"
        if key in ledger:
            continue
        items = [x for x in (h.get("items") or "").split(",") if x]
        if "2.02" in items:
            n202 += 1
            continue
        b = sec_get(doc_url(h["cik"], h["id"]))
        if b is None:
            nfetch += 1
            continue
        snip = snippet(html_text(b))
        p = parse_buyback_v2(snip, h["file_date"])
        if not p:
            nparse += 1
            continue
        s = p["sent"]
        tw, iw = bool(TOTALW.search(s)), bool(re.search(r"increas", s, re.I))
        fd = date.fromisoformat(h["file_date"])
        cands.append({"key": key, "market": "US", "rule": "C-E2", "accession": h["adsh"], "cik": h["cik"],
                      "company": h["name"], "ticker": tmap.get(int(h["cik"])) if h.get("cik") else None,
                      "file_date": h["file_date"], "items": h.get("items"), "doc": h["id"],
                      "doc_url": doc_url(h["cik"], h["id"]),
                      "anchor_date": us_prev_or_same(fd).isoformat(), "entry_date": us_next_session(fd).isoformat(),
                      "sentence": s, "usd": p["usd"], "shares_n": p["shares"], "pct": p["pct"], "new_kw": p["new_kw"],
                      "total_word": tw, "increase_word": iw, "ambiguous_total_vs_increment": bool(tw or iw),
                      "_v3": parse_buyback_v3(snip, h["file_date"]),
                      "hold_sessions": 60, "hold_aux": 20, "cost_roundtrip": US_COST})
    # 접수시각·가격·발행주식
    memo: Dict = {}
    tick = sorted({c["ticker"] for c in cands if c["ticker"]})
    bars: Dict[str, pd.DataFrame] = {}
    if tick:
        start = (min(date.fromisoformat(c["anchor_date"]) for c in cands) - timedelta(days=60)).isoformat()
        bars = US_FETCH(tick + ["IWM"], start, (now.date() + timedelta(days=1)).isoformat())
    new = []
    for c in cands:
        acc = acceptance_raw(int(c["cik"]), c["accession"], memo) if c.get("cik") else None
        et = utc_to_et(acc)
        c["acceptance_raw_utc"] = acc
        c["acceptance_et"] = et.strftime("%Y-%m-%d %H:%M:%S") if et else None
        if c["ticker"]:
            c.update(us_px_features(bars, c["ticker"], c["anchor_date"]))
        else:
            c["px_status"] = "no_ticker"
        if c.get("px_status") == "pending":
            log("US", f"가격 미게시 보류 {c['ticker']} a={c['anchor_date']}")
            continue
        c.update(dei_shares(int(c["cik"]), c["file_date"]) if (c.get("cik") and c["pct"] is None) else
                 {"so": None, "so_end": None, "so_filed": None})
        c["mcap_raw"] = (c["raw_close_a"] * c["so"]) if (c.get("raw_close_a") and c.get("so")) else None
        c["size"], c["size_basis"] = us_size({"pct": c["pct"], "usd": c["usd"], "shares": c["shares_n"]},
                                             c.get("raw_close_a"), c.get("so"))
        v3 = c.pop("_v3", None) or {}   # 주석 전용 — 채택은 사전등록 v2 규모로만(v3는 10-10 백테스트 t 2.50→2.17로 약화)
        c["usd_v3"], c["usd_basis_v3"] = v3.get("usd"), v3.get("usd_basis")
        c["size_v3"], _ = us_size({"pct": c["pct"], "usd": v3.get("usd"), "shares": c["shares_n"]},
                                  c.get("raw_close_a"), c.get("so"))
        c["detected_at_et"] = now.strftime("%Y-%m-%d %H:%M")
        c["actionable"] = now < datetime.combine(date.fromisoformat(c["entry_date"]), datetime.min.time()).replace(
            hour=9, minute=30)
        c["backfill"] = not c["actionable"]
        new.append(c)
    us_apply_dedupe(ledger, new)
    for c in new:
        us_finalize(c)
    log("US", f"FTS 문서 {len(docs)} · 2.02 제외 {n202} · 승인문장 없음 {nparse} · 본문 실패 {nfetch} · 후보 {len(new)} · "
              f"채택 {sum(c['qualifies'] for c in new)} · SEC 호출 {_SEC_CALLS[0]}")
    for c in sorted(new, key=lambda x: (x["file_date"], x["accession"])):
        log("US", f"{'DRY ' if dry else ''}{c['file_date']} {str(c['ticker']):6s} {c['accession']} 진입={c['entry_date']} "
                  f"접수ET={c['acceptance_et']} size={_pctf(c['size'])}({c['size_basis']}) usd={c['usd']} sh={c['shares_n']} "
                  f"pct={c['pct']} 원종가={_f2(c.get('raw_close_a'))} 분할배수={c.get('split_factor_after_a')} "
                  f"dv20={_f2((c.get('dv20_a') or 0) / 1e6)}M dei={c.get('so')} qualifies={c['qualifies']} "
                  f"actionable={c['actionable']} 모호={c['ambiguous_total_vs_increment']} {','.join(c['reject_reasons'])}")
        if c["qualifies"] or (c.get("size") or 0) >= US_SIZE_MIN:
            log("US", f"    문장: {c['sentence'][:300]}")
    if not dry and new:
        ledger.update({c["key"]: c for c in new})
        write_jsonl(US_LEDGER, sorted(ledger.values(), key=lambda r: (r["file_date"], r["accession"])))
    return 0


# ------------------------------------------------------------------ US 정산
def us_bench(M: Dict, e: int, t1: int, dv_evt: float) -> Tuple[float, float, int]:
    """research bench(268~289행) + edges(M(), 232~241행): a=e−1 우주(≥$3·≥$1M, ≥100) dv20 십분위, 분위당 ≥5, open→open."""
    a = e - 1
    dv = M["dv20"][a]
    ok = (M["close"][a] >= 3) & (dv >= 1e6) & np.isfinite(dv)
    iw = M["tix"].get("IWM")
    x1 = M["aopen"][t1]
    iwm = float(x1[iw] / M["aopen"][e, iw] - 1) if iw is not None else float("nan")
    if ok.sum() < 100 or not np.isfinite(dv_evt):
        return float("nan"), iwm, int(ok.sum())
    E = np.nanpercentile(dv[ok], np.arange(10, 100, 10))
    r = x1 / M["aopen"][e] - 1
    dec = np.searchsorted(E, np.where(np.isfinite(dv), dv, 0))
    k = int(np.searchsorted(E, dv_evt))
    mk = ok & np.isfinite(r) & (dec == k)
    if mk.sum() < 5:
        return float("nan"), iwm, int(mk.sum())
    return float(r[mk].mean()), iwm, int(mk.sum())


def us_exit_px(M: Dict, j: int, e: int, t1: int) -> float:
    """research exit_px(254~262행): aopen[t1], 결측이면 e..t1 마지막 유효 수정종가."""
    v = M["aopen"][t1, j]
    if np.isfinite(v):
        return float(v)
    c = M["aclose"][e:t1 + 1, j]
    f = np.where(np.isfinite(c))[0]
    return float(c[f[-1]]) if len(f) else float("nan")


def us_settle_rows(rows: List[Dict], M: Dict, done: set) -> List[Dict]:
    cal = [str(c) for c in M["cal"]]
    out = []
    for r in rows:
        j = M["tix"].get(r["ticker"])
        t0s = r["entry_date"]
        nxt = [i for i, c in enumerate(cal) if c >= t0s]
        if j is None or not nxt:
            continue
        e = nxt[0]
        for H in US_HS:
            sk = f"{r['key']}|H{H}"
            t1 = e + H
            if sk in done or t1 >= len(cal) or e < 1:
                continue
            o = M["aopen"][e, j]
            if not np.isfinite(o):
                continue
            ret = us_exit_px(M, j, e, t1) / float(o) - 1
            dv = M["dv20"][e - 1, j]
            sz, iwm, nb = us_bench(M, e, t1, float(dv) if np.isfinite(dv) else float("nan"))
            raw_open = float(o * M["close"][e, j] / M["aclose"][e, j]) if np.isfinite(M["aclose"][e, j]) else None
            out.append({"skey": sk, "key": r["key"], "rule": r["rule"], "ticker": r["ticker"], "H": H,
                        "entry": cal[e], "exit": cal[t1], "entry_ledger": t0s, "fill_open_raw": _nz(raw_open),
                        "ret": _nz(ret), "sz": _nz(sz), "sz_n": nb, "iwm": _nz(iwm),
                        "x_sz": _nz(ret - US_COST - sz), "x_iwm": _nz(ret - US_COST - iwm),
                        "size": r.get("size"), "dv20_a": r.get("dv20_a"), "actionable": r.get("actionable"),
                        "ambiguous_total_vs_increment": r.get("ambiguous_total_vs_increment"),
                        "settled_at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    return out


def us_settle(dry: bool) -> int:
    ins = _ins()
    rows = [r for r in read_jsonl(US_LEDGER) if r.get("qualifies")]
    old = {x["skey"]: x for x in read_jsonl(US_SETTLE)}
    today = date.today()
    due = [r for r in rows for H in US_HS
           if f"{r['key']}|H{H}" not in old and (today - date.fromisoformat(r["entry_date"])).days >= int(H * 1.4)]
    if not due:
        log("US", f"정산 대상 없음 (채택 {len(rows)} · 정산 {len(old)})")
        return 0
    s_min = min(date.fromisoformat(r["anchor_date"]) for r in due) - timedelta(days=45)
    tick = sorted(set(ins.universe_tickers()) | {r["ticker"] for r in due} | {"IWM"})
    CACHE.mkdir(parents=True, exist_ok=True)
    fp = CACHE / f"us_px_{s_min.isoformat()}_{today.isoformat()}.csv.gz"
    if fp.exists():
        px = pd.read_csv(fp)
    else:
        log("US", f"가격 수집 {len(tick)}종목 {s_min}~{today}")
        px = ins.fetch_prices(tick, s_min.isoformat(), (today + timedelta(days=1)).isoformat())
        if not dry:
            px.to_csv(fp, index=False, compression="gzip")
    M = ins.matrices(px)
    new = us_settle_rows(list({r["key"]: r for r in due}.values()), M, set(old))
    log("US", f"신규 정산 {len(new)}건")
    for x in new:
        log("US", f"{x['ticker']:6s} H{x['H']} {x['entry']}→{x['exit']} ret={_pctf(x['ret'])} "
                  f"x규모={_pctf(x['x_sz'])} xIWM={_pctf(x['x_iwm'])}")
    if not dry and new:
        old.update({x["skey"]: x for x in new})
        write_jsonl(US_SETTLE, list(old.values()))
    return 0


# ------------------------------------------------------------------ 판정
# 출처: research_us_events_20260930.py nw_t(351~362행) — US 는 이 정의(n<5 → nan, lag = H)
def us_nw_t(x, lags):
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


def cell_stats(recs: List[Dict], col: str, lag: int, market: str) -> Dict:
    """진입일 클러스터(같은 진입일 평균 = 관측 1개) 평균과 NW t(연구 lag: KR h−1, US H). US 는 분기 클러스터 t 도."""
    rs = [r for r in recs if r.get(col) is not None]
    if not rs:
        return {"n": 0, "dates": 0, "mean": None, "t": None}
    ys = date_series(rs, col, dkey="entry")
    if market == "KR":
        m, t, n = nw_t(ys, lag)
    else:
        m, n = float(np.mean(ys)), len(ys)
        t = us_nw_t(ys, lag)
    out = {"n": len(rs), "dates": n, "mean": _nz(m), "t": _nz(t),
           "median_evt": float(np.median([r[col] for r in rs])), "win": float(np.mean([r[col] > 0 for r in rs]))}
    if market == "US":
        q = defaultdict(list)
        for r in rs:
            y, mo = int(r["entry"][:4]), int(r["entry"][5:7])
            q[(y, (mo - 1) // 3)].append(r[col])
        qm = np.array([np.mean(v) for _, v in sorted(q.items())])
        out["q_t"] = _nz(qm.mean() / qm.std(ddof=1) * np.sqrt(len(qm))) if len(qm) > 2 and qm.std(ddof=1) > 0 else None
    return out


def gate(st: Dict) -> str:
    if (st.get("n") or 0) >= GATE_N and (st.get("mean") or 0) > 0 and (st.get("t") or 0) >= GATE_T:
        return "게이트 충족 — '소액 실주문 검토' (메모만, 운영자 결정)"
    return f"미달 (만기 {st.get('n') or 0}/{GATE_N}, t={_f2(st.get('t'))})"


def cmd_report(json_path: Optional[str] = None) -> int:
    out: Dict = {"KR": {}, "US": {}}
    kl, ks = read_jsonl(KR_LEDGER), read_jsonl(KR_SETTLE)
    ul, us = read_jsonl(US_LEDGER), read_jsonl(US_SETTLE)
    log("REPORT", f"KR 원장 {len(kl)}행 · 채택 {sum(1 for r in kl if r.get('qualifies'))} · 정산 {len(ks)} | "
                  f"US 원장 {len(ul)}행 · 채택 {sum(1 for r in ul if r.get('qualifies'))} · 정산 {len(us)}")
    kr_cells = {"Y1": [("ex", "규모매칭(1차)"), ("xk", "KOSPI200"), ("xq", "KOSDAQ150"), ("nx", "규모매칭·네이버가격")],
                "L1_lowliq": [("lx", "대역삼분위·AR스프레드(1차)"), ("lf", "대역삼분위·고정0.75%"), ("lxk", "KOSPI200·AR"),
                              ("lxq", "KOSDAQ150·AR"), ("ly", "대역삼분위·AR·원장가격")]}
    for rule, cols in kr_cells.items():
        for h in KR_HS:
            recs = [x for x in ks if x["rule"] == rule and x["h"] == h and x.get("elig_final")]
            for col, lab in cols:
                st = cell_stats(recs, col, h - 1, "KR")
                out["KR"][f"{rule}|h{h}|{col}"] = st
                log("REPORT", f"KR {rule:9s} h{h:<2d} {lab:22s} n={st['n']} 날짜={st['dates']} 평균={_f2(st['mean'])}% "
                              f"t={_f2(st['t'])}")
        prim = out["KR"][f"{rule}|h5|{cols[0][0]}"]
        out["KR"][f"{rule}|gate"] = gate(prim)
        log("REPORT", f"KR {rule} 1차(h5 {cols[0][1]}) 판정: {gate(prim)}")
    for H in US_HS:
        recs = [x for x in us if x["H"] == H]
        for col, lab in (("x_sz", "규모매칭(1차)"), ("x_iwm", "IWM")):
            st = cell_stats(recs, col, H, "US")
            out["US"][f"C-E2|H{H}|{col}"] = st
            log("REPORT", f"US C-E2 H{H:<3d} {lab:12s} n={st['n']} 날짜={st['dates']} "
                          f"평균={_pctf(st['mean'])} t={_f2(st['t'])} 분기t={_f2(st.get('q_t'))}")
    prim = out["US"]["C-E2|H60|x_sz"]
    out["US"]["C-E2|gate"] = gate(prim)
    log("REPORT", f"US C-E2 1차(H60 규모매칭) 판정: {gate(prim)}")
    if json_path:
        Path(json_path).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


# ------------------------------------------------------------------ CLI
def _dates(a, default_d0: date, default_d1: date) -> Tuple[date, date]:
    if a.date:
        d0 = date.fromisoformat(a.date)
        return d0, (date.fromisoformat(a.to) if a.to else d0)
    return default_d0, default_d1


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["kr-scan", "us-scan", "settle", "report"])
    ap.add_argument("--date")
    ap.add_argument("--to")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--market", choices=["kr", "us", "all"], default="all")
    ap.add_argument("--json")
    a = ap.parse_args(argv)
    try:
        if a.cmd == "kr-scan":
            if not a.dry:                                  # 10-30: 10-15 08:30 DNS 실패로 스캔 전체(대기 행 재평가 포함)가 중단돼 에스원을 놓쳤다
                from net_ready import wait_dns
                wait_dns(["opendart.fss.or.kr"], tag="BUYBACK-KR")
            t = datetime.now(KST).date()
            d0, d1 = _dates(a, t - timedelta(days=3), t)          # 주말 공시·DS005 지연분까지 멱등 재조회
            return kr_scan(d0, d1, a.dry)
        if a.cmd == "us-scan":
            y = now_et().date() - timedelta(days=1)
            d0, d1 = _dates(a, y - timedelta(days=3), y)          # 월요일에 금요일 file_date 포함
            return us_scan(d0, d1, a.dry)
        if a.cmd == "settle":
            rc = 0
            if a.market in ("kr", "all"):
                rc |= kr_settle(a.dry)
            if a.market in ("us", "all"):
                rc |= us_settle(a.dry)
            return rc
        return cmd_report(a.json)
    except DartError as exc:
        log("KR", f"DART 오류: {_scrub(exc, _dart_key())}")
        return 2
    except Exception as exc:  # noqa: BLE001 — 키가 섞인 URL 이 새지 않도록 메시지만 정제해 출력
        import traceback
        log("ERR", _scrub(f"{type(exc).__name__}: {exc} | {traceback.format_exc()}", _dart_key()))
        return 1


if __name__ == "__main__":
    sys.exit(main())
