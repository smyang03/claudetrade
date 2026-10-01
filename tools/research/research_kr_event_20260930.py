#!/usr/bin/env python3
"""KR DART 이벤트 — 라이브 쉐도우 한 달 채점(Part 1) + 2019~2026 DART 백필 검증(Part 2) (read-only 연구, 2026-09-30).

운영 파일·봇·state·config 는 건드리지 않는다. 산출물은 data/analysis/kr_event_20260930/ 와
docs/reports/kr_event_20260930.md 뿐. runtime/kr_event_lane.py 는 파서·분류만 read-only import.
DART 키는 .env.live 의 DART_API_KEY 를 dotenv 로 읽고 출력하지 않는다. Anthropic API 호출 없음.

서브커맨드
  list    : DART list.json (pblntf_detail_ty I001 수시공시 · B001 주요사항보고서) 2019-01-01 ~ 2026-09-29, 3개월 창·전 페이지.
            창별 jsonl 로 저장(재개 가능). 이벤트 종류 분류는 kr_event_lane.classify_title + 최대주주 하위유형.
  ds005   : 구조화 API(corp_code 단위, 전 기간) — 유상증자(piicDecsn)·무상증자(fricDecsn)·유무상(pifricDecsn)·
            자기주식취득(tsstkAqDecsn)·자기주식신탁(tsstkAqTrctrCnsDecsn).
  shares  : 자사주 규모 분모 — stockTotqySttus(주식총수현황, 직전 사업연도 사업보고서) 발행주식총수.
  docs    : 단일판매·공급계약체결 원공시 원문(document.xml) → parse_supply_contract. T2 캐시(2025-09~) 재사용.
  prices  : Part 1 에서 data/price/kr 에 없는 종목만 네이버 fchart 일봉(분석 전용 소스)으로 보충.
  part1   : 라이브 쉐도우(2026-09-04~) 채점.
  part2   : 백필 판정.
공통 가드: OpenAPI 호출 간 0.5s, 실행당 상한(MAX_CALLS), 평일 KST 07:20~20:00 에는 OpenAPI 대량 호출 중단
          (라이브 레인이 같은 키로 20초마다 list 를 친다 — 일 한도 공유).

=====================================================================================================
사전등록 (Part 2 결과 파일이 하나도 없을 때 작성·고정 — 2026-09-30 01:0x KST)
=====================================================================================================
## A. 표본
- 원천: DART list.json I001(단일판매·공급계약체결, 최대주주변경 계열, 주식소각결정) + B001(유상증자결정, 무상증자결정,
  유무상증자결정, 자기주식취득결정, 자기주식취득신탁계약체결결정) + 잠정실적은 T1kr(model_T1kr_earnings_20260928)
  events_features 재사용(같은 list 원천, I002).
- 기간: rcept_dt 2019-01-01 ~ 2026-09-29. **IS = 진입일 2019~2022, OOS = 2023~2026.**
- 원공시만: **제목이 '[' 로 시작하는 공시는 전부 제외**([기재정정]·[첨부정정]·[첨부추가]·[발행조건확정] 등 — 전부 원 결정 이후의
  후속 서류라 별도 이벤트로 세면 같은 결정이 다른 날짜로 중복된다). (01:2x 수정: 처음엔 [첨부추가]·[발행조건확정] 유지로 적었으나
  list 제목 분포만 보고, 결과 계산 전에 바꿈.)
  **list 의 rm 필드('정'=나중에 정정됨, '철'=나중에 철회)는 사후 정보라 표본 선택에 쓰지 않는다**(진단만).
- 같은 (종목, 신호일 D, 규칙종류) 중복은 rcept_no 가장 이른 1건. 종목코드 공란(비상장·상폐 후 코드 소거)·가격 없는 종목은
  탈락 — 종류×연도별 탈락 건수를 보고(생존편향 측정).
## B. 시점
- DART list 는 접수일만 있다(접수시각 없음). KIND 시각은 KRX 약관상 자동수집 안 함 → **모든 공시를 접수일 장 마감 후로 가정.**
  신호일 D = rcept_dt 이하 마지막 거래일(주말·휴일 공시는 직전 거래일), **진입 = D 다음 거래일 시가.**
  장중 공시의 당일 반응은 놓친다(보수적). 당일 종가 진입 변형은 시각이 없으므로 하지 않는다.
- 거래일 달력 = 069500 봉 날짜.
## C. 결과
- r_h = 진입 시가 → 진입 세션을 1로 센 h 번째 세션 종가, h ∈ {1, 5, 10, 20}. 비용 0.25% 왕복 차감.
- 가격 오류 가드(사전 고정): 창(D 종가 기준 진입 시가 ~ 청산 종가) 안 어떤 봉이라도 시가·종가가 전일 종가 대비 ±30.5% 밖이면
  그 결과는 결측(미수정 분할·병합). 벤치 유니버스에도 같은 규칙.
- 벤치 ① 규모매칭(판정): D 에 적격(D 종가 ≥ 1,000, D까지 20봉 평균 거래대금 dvol20 ≥ 20억, 이력 ≥ 25봉, 다음 시가 > 0)인
  전 종목을 dvol20 십분위로 나누고, 이벤트 종목 dvol20 이 들어가는 십분위(적격 미달이면 최하 십분위로 클립)의 같은 창 등가중 평균.
  ② 069500(KOSPI200) ③ 229200(KOSDAQ150) 같은 창. 초과 ex_h = r_h − 0.25 − bench_h.
- 가격 원천: yfinance 백필(price_backfill_yf, auto_adjust, 2018-01~2026-09-11, 1,746종목) ∪ T1kr 추가 수집(prices/).
  거래대금 = 종가×거래량(yfinance 거래대금 미제공). data/price/kr 을 이어 붙이지 않는다 → 2026-09-11 이후 청산분은 미성숙(결측).
- **1차 모집단 = D 적격 이벤트**(벤치와 같은 문턱). 전체(비적격 포함)는 참고.
## D. 통계
- 날짜 클러스터: 같은 진입일 이벤트를 등가중 평균해 1개 관측 → 진입일 시계열의 평균, Newey-West t(lag = h−1).
- **1차 보유 = h5.** h1·h10·h20 은 보조 보고(판정 밖).
- 연도 부호: 진입 연도별 날짜평균 부호. "연도 부호 다수" = OOS 연도(2023·2024·2025·2026) 중 ≥3 양(+).
- **합격(확정) = OOS h5 규모매칭 초과 > 0 AND NW t ≥ 2.64 AND OOS 연도 부호 ≥ 3/4 AND IS h5 초과 > 0.**
  후보 = OOS 초과 > 0 & t ≥ 2.0 & 연도 부호 ≥ 3/4 (IS 부호 무관). 그 밖은 기각.
  검정 수 = 아래 규칙 수(약 22) — 2.64 문턱은 다중검정 보정이 아니다, 표에 규칙 수를 병기.
## E. 규칙(부분집단, 결과 보기 전 고정)
- 공급계약(원문 파싱 kr_event_lane.parse_supply_contract): S0 전체 · S1 매출대비 ≥ 10% · S2 ≥ 30% · S3 ≥ 30% & 관계사 아님(False 확정)
  · 파싱 실패(ratio 결측)는 S0 에만 들어가고 건수 보고.
  원문 수집이 일 한도를 넘으면: 연도 층화 무작위 표본(seed 20260930, 고정 비율 = 가용 호출 / 전체, 결과·성공여부 무관)으로 줄이고
  연도별 n_sampled/n_total 을 보고한다.
- 무상증자(fricDecsn 1주당 배정 보통주): B0 전체 · B1 배정 ≥ 0.5 · B2 ≥ 1.0 · B3 유무상증자결정 전체(pifricDecsn, 참고).
- 자사주: Y0 직접취득 전체 · Y1 직접취득 취득예정주식/발행주식총수 ≥ 1% · Y2 신탁계약 전체 ·
  Y3 신탁 계약금액/(D 종가×발행주식총수) ≥ 1%. 발행주식총수 = stockTotqySttus(직전 사업연도 사업보고서, 보통주 발행주식총수).
  분모를 못 구하면 Y1/Y3 에서 빠지고 건수 보고(대체 분모 사용 안 함).
- DS005 조인(01:5x 수리, 결과 확정 전): API 가 결정당 최신본만 주므로 후속 서류 rcept_no 를 같은 회사·종류의 직전 원공시로
  되감아 붙인다(가장 이른 본 우선). 값이 정정본에서 올 수 있다 — 쓰는 필드는 증자방식·배정비율·취득예정주식·계약금액처럼
  결정 시점에 정해지는 것뿐이지만 정정으로 바뀐 값이 섞일 수 있다(한계). fields_via 로 원/후속 출처를 남긴다.
- 유상증자(piicDecsn 증자방식): R0 전체 · R1 제3자배정 · R2 주주배정(실권주 일반공모 포함) · R3 일반공모.
- 최대주주변경(제목): M0 전체 · M1 '양수도' 포함 · M2 '담보' 포함 · M3 그 밖(단순 변경·해제 등).
- 잠정실적(T1kr 피처, 재무 API 재구성 — 값이 이후 정기보고서 수치라는 lookahead 한계): P0 전체 · P1 흑자전환(turn_up) & 매출 전년비 > 0
  (라이브 N6 규칙) · P2 영업이익 전년비 ≥ +100% & 영업이익률 > 0.
- 소각(참고, 메모리상 기각): C0 주식소각결정 전체.
## F. 생존편향
- 가격 원천이 2026-09 현재 상장 종목뿐 → 상폐 종목 공시 탈락. 종류×연도별 (list 건수, 코드 공란, 가격 없음) 보고.
  유상증자(특히 제3자배정)·최대주주변경은 이후 상폐 비율이 높아 초과가 위로 편향된다(기각 방향 판정에는 안전, 합격엔 위험).

=====================================================================================================
사전등록 2차 (운영 조율자 지시 '다음 행동 3', 2026-09-30 05:1x KST — 아래 결과 계산 전에 고정)
=====================================================================================================
공통: 비용·벤치·t·연도 규칙은 1차와 같다(날짜 클러스터 NW, lag h−1, IS 2019~22 / OOS 2023~26).
  **합격 = OOS 초과 > 0 & t ≥ 2.64 & OOS 연도 ≥ 3/4 양(+) & IS 같은 부호(> 0).**
  가격 원천(사전 점검 결과 반영): yfinance 캐시는 일부 무상증자 권리락을 수정하지 않는다(2024년 1:1 무상 7건이 −50% 봉,
  비율 0.3 은 −23% 로 ±30.5% 가드를 통과해 새어 들어감) → **이벤트 종목 가격은 네이버 fchart 수정주가**(권리락 수정 확인:
  315640·408900·145170·340930)로 바꾼다. 벤치 유니버스는 yf 캐시 그대로(가드 적용). yf 기반 값은 비교용으로 같이 낸다.
  네이버도 상폐 종목은 없다(생존편향 동일).
(a) 무상증자 h20 1차: 규칙 B0 전체 · B1 배정≥0.5 · B2 배정≥1.0 (1차에서 정한 집합 그대로), 1차 보유 h20(h5·h10 보조).
  - 권리락일 = 신주배정기준일(fricDecsn nstk_asstd) 직전 거래일(1영업일 전, 002070 2026-08-03 기준일 → 07-31 권리락 확인).
  - 가드: 권리락일 봉은 상하한이 권리락 기준가로 다시 잡히므로 ±30.5% 대신 ±45% 를 쓴다(그 밖의 봉은 ±30.5%).
  - 신주 매도 제약: 진입 시 산 기존 주식 1주는 권리락 뒤 (1+배정) 주가 된다. 수정주가 수익은 신주를 청산일 가격에 판다고
    가정하므로, **권리락일 ≤ 청산일 < 신주 상장일(nstk_lstprd)** 이면 가치 비중 배정/(1+배정) 만큼은 상장일(그날 없으면 다음
    거래일) 시가에 판 것으로 계산한다: R = [1/(1+b)·P청산 + b/(1+b)·P상장일시가] / P진입 − 1 (수정주가). 상장일 가격이 아직
    없으면(미성숙) 결측. 이 값이 1차 결과이고 제약 없는 수정주가 수익은 보조.
(b) 자사주 저유동 변형: 모집단 = D 종가 ≥ 1,000, 이력 ≥ 25봉, **5억 ≤ dvol20 < 20억**. 규칙 L0 직접취득 전체 · L1 직접 ≥1% 발행주식
  · L2 신탁 전체 · L3 신탁 ≥1% 시총(1차 Y0~Y3 과 같은 정의). 1차 보유 h5(h20 보조).
  - 벤치 = 같은 날 같은 대역(5~20억) 적격 전 종목을 dvol20 삼분위로 나눈 같은 삼분위 등가중(무비용).
  - 비용 = 0.25% + 스프레드 추정. execution_shortfall_ledger 는 69건·대부분 US/대형주·시가 대비 체결차라 호가 스프레드
    추정에 못 쓴다 → **Abdi-Ranaldo(2017) 고저종 추정치**(D 이전 20봉, 음수는 0, 왕복 = 스프레드 전체 1회)를 이벤트별로 차감.
    참고로 고정 0.25%+0.5% 도 보고.
(c) Y1 견고성(규칙 재정의 없음): 전체기간·OOS 각각 연도 1개씩 뺀 h5 초과(leave-one-year-out), 2024 를 밸류업 이전
  (01-01~02-25)/이후(02-26~) 로 가른 값, 이후 구간을 뺀 OOS, 네이버 가격 교차검증.
(d) 매수금지 필터: virtual_books.db 의 xkr_fallen3 CLOSED 거래(신호일 D, 원장 net_pct = 레인 계약 결과) 전체와 chg ≤ −5 부분집합
  (c_kr_fallen* 공통 조건). 플래그(공시일 rcept_dt ∈ (D 이전 5번째 거래일, D], 원공시 기준):
  F1 유상증자 제3자배정(DS005 증자방식) · F2 최대주주변경 단순(M3: 제목에 양수도·담보 없음) · F3 정정공시(I001/B001 제목 접두에 '정정')
  · F_any 합집합. 통계 = 플래그 거래가 있는 날마다 (플래그 평균 net − 같은 날 비플래그 평균 net), 그 날짜 시계열 NW t(lag 6).
  **매수금지 필터 합격 = 차이 < 0 & t ≤ −2.0 & 두 반기(날짜 중앙값 분할) 모두 음(−).** 제거 거래 수·제거로 바뀌는 평균도 보고.
  주의: c_kr_fallen_nomajor 는 이미 최대주주변경 0~7일을 빼고 있다(F2 와 겹침).

## Part 1 (라이브 쉐도우 2026-09-04 ~ 2026-09-29, 결과 관측 후 서술 — 판정 아님, 기술 통계)
- 대상: kind ≠ other 이고 종목코드 있는 행(rcept_no 중복은 첫 행). 진입가:
  결정시각 09:00 ≤ t < 15:20 이고 quote.price 있으면 그 값(intraday) / 09:00 이전 결정 → 그날 시가 /
  15:20 이후 → 다음 세션 시가 / 장중인데 호가 없음(정정 SKIP 등) → 다음 세션 시가(entry_basis 로 분리 표기).
- h ∈ {1,3,5,10}, 진입 세션 = 1. 비용 0.25%. 벤치 = 규모매칭(data/price/kr 유니버스, 같은 규칙) · 069500 · 229200 같은 창
  (intraday 진입은 벤치를 진입일 시가→ 청산 종가로 근사).
- 속도 분해(장중 감지분): 전일종가 → 감지시 호가(quote_detect) → 결정시 호가(quote) → 당일 종가 → 다음 시가.
  ts_detected 는 20초 폴링 감지시각이지 DART 접수시각이 아니다 — '우리 전에 움직인 몫'은 하한.
"""
from __future__ import annotations

import csv
import html
import io
import json
import math
import os
import random
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "analysis" / "kr_event_20260930"
LIST_DIR = OUT / "list"
DOC_DIR = OUT / "docs"
REPORT = ROOT / "docs" / "reports" / "kr_event_20260930.md"
KST = timezone(timedelta(hours=9))
SLEEP = 0.5
MAX_CALLS = int(os.getenv("KRE_MAX_CALLS", "9000"))
COST = 0.25
_CALLS = 0

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env.live")
except Exception:
    pass
KEY = (os.getenv("DART_API_KEY") or "").strip()


class Budget(Exception):
    pass


def blocked_now() -> bool:
    now = datetime.now(KST)
    if os.getenv("KRE_IGNORE_BLOCK") == "1":
        return False
    return now.weekday() < 5 and "07:20" <= now.strftime("%H:%M") < "20:00"


def _get(url: str, timeout: float = 25.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def dart(endpoint: str, **params) -> dict:
    global _CALLS
    if blocked_now():
        raise Budget("blocked window (lane hours)")
    if _CALLS >= MAX_CALLS:
        raise Budget("MAX_CALLS")
    q = urllib.parse.urlencode({"crtfc_key": KEY, **params})
    for attempt in range(3):
        _CALLS += 1
        try:
            d = json.loads(_get(f"https://opendart.fss.or.kr/api/{endpoint}.json?{q}"))
            time.sleep(SLEEP)
            if d.get("status") == "020":        # 요청 제한 초과
                raise Budget("DART 020 limit")
            return d
        except Budget:
            raise
        except Exception:
            time.sleep(2.0 * (attempt + 1))
    return {"status": "ERR"}


# ── list ────────────────────────────────────────────────────────────────────
def cmd_list() -> None:
    LIST_DIR.mkdir(parents=True, exist_ok=True)
    wins = []
    d0 = date(2019, 1, 1)
    end = date(2026, 9, 29)
    while d0 <= end:
        m = d0.month + 2
        y = d0.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        nxt = date(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)
        d1 = min(nxt - timedelta(days=1), end)
        wins.append((d0, d1))
        d0 = d1 + timedelta(days=1)
    for ty in ("B001", "I001"):
        for a, b in wins:
            fn = LIST_DIR / f"{ty}_{a:%Y%m%d}_{b:%Y%m%d}.jsonl"
            if fn.exists():
                continue
            rows, page, tot = [], 1, None
            try:
                while True:
                    d = dart("list", bgn_de=f"{a:%Y%m%d}", end_de=f"{b:%Y%m%d}", pblntf_detail_ty=ty,
                             page_no=page, page_count=100)
                    st = d.get("status")
                    if st == "013":
                        break
                    if st != "000":
                        raise RuntimeError(f"status {st} {d.get('message')}")
                    tot = d.get("total_page", 1)
                    for r in d.get("list", []) or []:
                        rows.append({k: r.get(k) for k in ("rcept_no", "rcept_dt", "corp_code", "corp_name", "stock_code",
                                                           "corp_cls", "report_nm", "rm")})
                    if page >= tot:
                        break
                    page += 1
            except Budget as e:
                print(f"[list] stop: {e} calls={_CALLS}", flush=True)
                return
            except Exception as e:
                print(f"[list] {ty} {a} fail: {e}", flush=True)
                continue
            tmp = fn.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                for r in rows:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            tmp.replace(fn)
            print(f"[list] {ty} {a}~{b} rows={len(rows)} pages={tot} calls={_CALLS}", flush=True)


# ── 가격 행렬·벤치 공통 ──────────────────────────────────────────────────────
import numpy as np  # noqa: E402

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


# ── Part 1 ──────────────────────────────────────────────────────────────────
SIG = ROOT / "data" / "shadow" / "kr_event_signals.jsonl"
OBS = ROOT / "data" / "shadow" / "kr_event_observations.jsonl"
PX1 = OUT / "px_part1"


def naver_bars(code: str, count: int = 400) -> list[tuple]:
    u = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count={count}&requestType=0"
    try:
        s = _get(u, 20).decode("cp949", "ignore")
    except Exception:
        return []
    out = []
    for m in re.finditer(r'data="(\d{8})\|([\d.]+)\|[\d.]+\|[\d.]+\|([\d.]+)\|(\d+)"', s):
        d = m.group(1)
        out.append((f"{d[:4]}-{d[4:6]}-{d[6:]}", float(m.group(2)), float(m.group(3)), float(m.group(4))))
    time.sleep(0.3)
    return out


def load_part1_book(need: set[str]) -> Book:
    bars = {}
    for p in (ROOT / "data" / "price" / "kr").glob("kr_*.csv"):
        b = [r for r in read_csv_bars(p) if r[0] >= "2025-06-01"]
        if b:
            bars[p.stem[3:]] = b
    PX1.mkdir(parents=True, exist_ok=True)
    for t in sorted(need - set(bars)):
        fp = PX1 / f"{t}.json"
        if fp.exists():
            b = json.loads(fp.read_text(encoding="utf-8"))
        else:
            b = naver_bars(t)
            fp.write_text(json.dumps(b), encoding="utf-8")
        if b:
            bars[t] = [tuple(x) for x in b if x[0] >= "2025-06-01"]
    cal = [r[0] for r in bars["069500"]]
    return Book(bars, cal)


def _t(ts: str | None) -> str:
    return (ts or "")[11:16]


def cmd_part1() -> None:
    rows, seen = [], set()
    for l in SIG.open(encoding="utf-8"):
        r = json.loads(l)
        if r.get("kind") == "other" or not r.get("stock_code") or r["rcept_no"] in seen:
            continue
        seen.add(r["rcept_no"])
        rows.append(r)
    obs = {}
    for l in OBS.open(encoding="utf-8"):
        o = json.loads(l)
        obs.setdefault(o["rcept_no"], o)
    bk = load_part1_book({r["stock_code"] for r in rows} | {"069500", "229200"})
    HS1 = (1, 3, 5, 10)
    k2, q2 = bk.col.get("069500"), bk.col.get("229200")
    recs = []
    for r in rows:
        tk = r["stock_code"]
        j = bk.col.get(tk)
        sd = r["session_date"]
        if sd not in bk.ix:
            continue
        s0 = bk.ix[sd]
        td = _t(r.get("ts_decided") or r.get("ts_detected"))
        q = r.get("quote") or {}
        qpx = q.get("price") if q.get("venue") in (None, "KRX") else None
        if "09:00" <= td < "15:20" and qpx:
            basis, e, sig, epx = "intraday_quote", s0, s0 - 1, float(qpx)
        elif td < "09:00":
            basis, e, sig, epx = "same_day_open", s0, s0 - 1, None
        elif "09:00" <= td < "15:20":
            basis, e, sig, epx = "next_open_noquote", s0 + 1, s0, None
        else:
            basis, e, sig, epx = "next_open_after_close", s0 + 1, s0, None
        rec = {"rcept_no": r["rcept_no"], "tk": tk, "kind": r["kind"], "decision": r["decision"], "reason": r.get("reason", ""),
               "basis": basis, "sd": sd, "td": td, "entry": bk.cal[e] if e < len(bk.cal) else None,
               "latency": r.get("latency_sec"), "corr": r.get("is_correction")}
        if j is None or e >= len(bk.cal):
            rec["nopx"] = True
            recs.append(rec)
            continue
        dv = bk.dv20[sig, j]
        rec["elig"] = bool(bk.eligible(sig, e)[j]) if e < len(bk.cal) else None
        for h in HS1:
            rr = bk.fwd(j, e, h, entry_px=epx)
            rr = float(rr) if rr == rr else None
            rec[f"r{h}"] = rr
            # 다음 시가 통일 진입(비교용)
            rn = bk.fwd(j, s0 + 1 if basis != "same_day_open" else s0, h)
            rec[f"rn{h}"] = float(rn) - COST if rn == rn else None
            if rr is None:
                continue
            sb = bk.size_bench(sig, e, h, dv)
            ki = bk.fwd(k2, e, h)
            kq = bk.fwd(q2, e, h)
            rec[f"ex{h}"] = rr - COST - sb if sb == sb else None
            rec[f"xk{h}"] = rr - COST - float(ki) if ki == ki else None
            rec[f"xq{h}"] = rr - COST - float(kq) if kq == kq else None
        # 속도 분해 (%): 전일종가 → 감지 → 결정 → 당일종가 → 다음시가
        o = obs.get(r["rcept_no"])
        pc = (r.get("liq") or {}).get("prev_close") or bk.c[s0 - 1, j]
        pdet = (o or {}).get("px_detect") or (r.get("quote_detect") or {}).get("price")
        pdec = (o or {}).get("px_decide") or qpx
        cD = bk.c[s0, j]
        oN = bk.o[s0 + 1, j] if s0 + 1 < len(bk.cal) else np.nan
        def pct(a, b):
            return (b / a - 1) * 100 if a and b and a == a and b == b else None
        if "09:00" <= _t(r.get("ts_detected")) < "15:20":
            rec.update({"sp_pre": pct(pc, pdet), "sp_lat": pct(pdet, pdec), "sp_dayrest": pct(pdec, cD),
                        "sp_overnight": pct(cD, oN), "sp_detect_to_close": pct(pdet, cD)})
        else:
            rec.update({"sp_after_close_gap": pct(cD, oN)})
        recs.append(rec)
    (OUT / "part1_events.jsonl").write_text("\n".join(json.dumps(x, ensure_ascii=False) for x in recs), encoding="utf-8")

    def reason_cls(x):
        rs = x["reason"]
        if x["decision"] != "SKIP":
            return x["decision"]
        for k, v in (("correction", "correction"), ("ratio_", "ratio_lt_30"), ("after_entry", "after_cutoff"),
                     ("dvol20", "liquidity"), ("doc_unavail", "doc_unavailable"), ("relation", "relation"),
                     ("runup", "runup"), ("prelim", "prelim_parse/no_turn"), ("no_turn", "prelim_parse/no_turn"),
                     ("bonus_ratio", "bonus_ratio"), ("no_quote", "no_quote"), ("price_lt", "liquidity"), ("no_nx", "nxt"),
                     ("nx_stale", "nxt"), ("slots", "cap"), ("daily_cap", "cap"), ("llm_skip", "llm_skip"), ("amend", "amend")):
            if rs.startswith(k) or k in rs:
                return "SKIP:" + v
        return "SKIP:other"

    out = {"n_rows": len(recs), "groups": {}, "skip_reasons": {}, "speed": {}, "latency": {}}
    def summarize(sub):
        d = {"n": len(sub), "n_px": sum(1 for x in sub if x.get("r1") is not None),
             "basis": dict(Counter(x["basis"] for x in sub))}
        for h in HS1:
            for key in ("ex", "xk", "xq", "rn"):
                ys = date_series(sub, f"{key}{h}")
                m, t, n = nw_t(ys, h - 1)
                d[f"{key}{h}"] = (round(m, 2) if m == m else None, round(t, 2) if t == t else None, n)
            raw = [x[f"r{h}"] - COST for x in sub if x.get(f"r{h}") is not None]
            d[f"net{h}_mean"] = round(float(np.mean(raw)), 2) if raw else None
        return d
    for (k, dcs), sub in sorted(defaultdict(list, {}).items()):
        pass
    g = defaultdict(list)
    for x in recs:
        g[(x["kind"], x["decision"])].append(x)
        g[(x["kind"], "ALL")].append(x)
        g[("ALL", x["decision"])].append(x)
    for key, sub in sorted(g.items()):
        out["groups"]["|".join(key)] = summarize(sub)
    gs = defaultdict(list)
    for x in recs:
        gs[reason_cls(x)].append(x)
    for key, sub in sorted(gs.items()):
        out["skip_reasons"][key] = summarize(sub)
    def tb(x):
        t = x["td"]
        return "a_pre0900" if t < "09:00" else "b_0900_1510" if t < "15:10" else "c_1510_1600" if t < "16:00" else "d_after1600"
    out["time_bucket"] = {}
    gt = defaultdict(list)
    for x in recs:
        if x["reason"] != "correction":
            gt[tb(x)].append(x)
            gt[tb(x) + "|" + x["kind"]].append(x)
    for key, sub in sorted(gt.items()):
        out["time_bucket"][key] = summarize(sub)
    # 속도 분해
    sp = defaultdict(list)
    for x in recs:
        if x.get("sp_pre") is not None or x.get("sp_detect_to_close") is not None:
            sp[x["kind"]].append(x)
            sp["ALL"].append(x)
    for k, sub in sp.items():
        d = {"n": len(sub)}
        for f in ("sp_pre", "sp_lat", "sp_dayrest", "sp_overnight", "sp_detect_to_close"):
            v = [x[f] for x in sub if x.get(f) is not None]
            d[f] = (round(float(np.mean(v)), 2), round(float(np.median(v)), 2), len(v)) if v else None
        out["speed"][k] = d
    ac = [x.get("sp_after_close_gap") for x in recs if x.get("sp_after_close_gap") is not None]
    out["speed"]["after_close_gap_all"] = (round(float(np.mean(ac)), 2), round(float(np.median(ac)), 2), len(ac)) if ac else None
    lat = sorted(x["latency"] for x in recs if x.get("latency") and x["reason"] != "correction")
    if lat:
        out["latency"] = {"n": len(lat), "p10": lat[len(lat) // 10], "p50": lat[len(lat) // 2], "p90": lat[int(len(lat) * .9)],
                          "max": lat[-1]}
    out["decide_hour"] = dict(sorted(Counter(x["td"][:2] for x in recs).items()))
    out["enter_rows"] = [x for x in recs if x["decision"] == "ENTER"]
    (OUT / "part1_summary.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "enter_rows"}, ensure_ascii=False)[:6000])


# ── Part 2: 이벤트 추출 ─────────────────────────────────────────────────────
def event_kind(nm: str) -> str | None:
    from runtime.kr_event_lane import classify_title
    if nm.startswith("["):
        return None
    if "자기주식" in nm and "신탁계약" in nm:
        return "trust" if ("체결" in nm and "해지" not in nm) else None
    if "유무상증자" in nm:
        return "bonus_mixed"
    k, corr = classify_title(nm)
    if corr:
        return None
    return {"supply_contract": "supply", "bonus_issue": "bonus", "buyback": "buyback", "rights_offering": "rights",
            "major_holder_change": "major", "share_cancellation": "cancel"}.get(k)


def load_list_events() -> tuple[list[dict], dict]:
    ev, cov = [], Counter()
    seen = set()
    for fn in sorted(LIST_DIR.glob("*.jsonl")):
        ty = fn.name[:4]
        for l in fn.open(encoding="utf-8"):
            r = json.loads(l)
            nm = (r.get("report_nm") or "").strip()
            k = event_kind(nm)
            if not k or r["rcept_no"] in seen:
                continue
            # 종류-원천 정합(supply·major·cancel 은 I001, 나머지는 B001)
            if (k in ("supply", "major", "cancel")) != (ty == "I001"):
                continue
            seen.add(r["rcept_no"])
            sc = (r.get("stock_code") or "").strip()
            cov[(k, r["rcept_dt"][:4], "code" if sc else f"nocode_{r.get('corp_cls')}")] += 1
            if not sc:
                continue
            ev.append({"rcept_no": r["rcept_no"], "rcept_dt": r["rcept_dt"], "corp_code": r["corp_code"], "tk": sc,
                       "name": r.get("corp_name"), "report": nm, "kind": k, "rm": r.get("rm") or ""})
    return ev, cov


DS_EP = {"rights": "piicDecsn", "bonus": "fricDecsn", "bonus_mixed": "pifricDecsn", "buyback": "tsstkAqDecsn",
         "trust": "tsstkAqTrctrCnsDecsn"}


def cmd_ds005() -> None:
    ev, _ = load_list_events()
    fn = OUT / "ds005.jsonl"
    done = set()
    if fn.exists():
        for l in fn.open(encoding="utf-8"):
            x = json.loads(l)
            done.add((x["ep"], x["corp_code"]))
    todo = sorted({(DS_EP[e["kind"]], e["corp_code"]) for e in ev if e["kind"] in DS_EP} - done)
    print(f"[ds005] todo={len(todo)} done={len(done)}", flush=True)
    with fn.open("a", encoding="utf-8") as fh:
        for n, (ep, cc) in enumerate(todo):
            try:
                d = dart(ep, corp_code=cc, bgn_de="20181201", end_de="20260929")
            except Budget as e:
                print(f"[ds005] stop {e} calls={_CALLS}", flush=True)
                return
            if d.get("status") not in ("000", "013"):
                continue
            fh.write(json.dumps({"ep": ep, "corp_code": cc, "status": d.get("status"), "list": d.get("list") or []},
                                ensure_ascii=False) + "\n")
            if n % 200 == 0:
                fh.flush()
                print(f"[ds005] {n}/{len(todo)} calls={_CALLS}", flush=True)


def share_year(rcept_dt: str) -> str:
    y, m = int(rcept_dt[:4]), int(rcept_dt[4:6])
    return str(y - 1 if m >= 4 else y - 2)


def cmd_shares() -> None:
    ev, _ = load_list_events()
    fn = OUT / "shares.jsonl"
    done = set()
    if fn.exists():
        for l in fn.open(encoding="utf-8"):
            x = json.loads(l)
            done.add((x["corp_code"], x["year"]))
    todo = sorted({(e["corp_code"], share_year(e["rcept_dt"])) for e in ev if e["kind"] in ("buyback", "trust")} - done)
    print(f"[shares] todo={len(todo)}", flush=True)
    with fn.open("a", encoding="utf-8") as fh:
        for n, (cc, y) in enumerate(todo):
            try:
                d = dart("stockTotqySttus", corp_code=cc, bsns_year=y, reprt_code="11011")
            except Budget as e:
                print(f"[shares] stop {e} calls={_CALLS}", flush=True)
                return
            if d.get("status") not in ("000", "013"):
                continue
            sh = None
            for r in d.get("list") or []:
                if (r.get("se") or "").strip() == "보통주":
                    sh = _numv(r.get("istc_totqy"))
            if sh is None:
                for r in d.get("list") or []:
                    if (r.get("se") or "").strip() == "합계":
                        sh = _numv(r.get("istc_totqy"))
            fh.write(json.dumps({"corp_code": cc, "year": y, "status": d.get("status"), "shares": sh}) + "\n")
            if n % 200 == 0:
                fh.flush()
                print(f"[shares] {n}/{len(todo)} calls={_CALLS}", flush=True)


def _numv(s) -> float | None:
    try:
        s = str(s).replace(",", "").strip()
        return float(s) if s not in ("", "-") else None
    except ValueError:
        return None


# ── 공급계약 원문 (DART 웹 뷰어 — OpenAPI 한도 밖, 1 req/s 수준) ──────────────
T2_DOCS = ROOT / "data" / "analysis" / "model_T2_dart_20260927" / "docs"


def _web(u: str) -> str:
    b = _get(u, 25)
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp949", "ignore")


def web_doc_text(rc: str) -> str:
    h = _web(f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rc}")
    time.sleep(0.35)
    m = re.search(r'viewDoc\("(\d+)", "(\d+)", "0", "0", "0", "(\w+)"', h)
    if not m:
        return ""
    v = _web(f"https://dart.fss.or.kr/report/viewer.do?rcpNo={rc}&dcmNo={m.group(2)}&eleId=0&offset=0&length=0&dtd={m.group(3)}")
    time.sleep(0.35)
    v = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", v)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", v))).strip()


def cmd_docs() -> None:
    from runtime.kr_event_lane import parse_supply_contract
    ev, _ = load_list_events()
    sup = [e for e in ev if e["kind"] == "supply"]
    fn = OUT / "supply_fields.jsonl"
    done = set()
    if fn.exists():
        for l in fn.open(encoding="utf-8"):
            done.add(json.loads(l)["rcept_no"])
    todo = [e for e in sup if e["rcept_no"] not in done]
    frac = float(os.getenv("KRE_SUPPLY_FRAC", "1.0"))
    if frac < 1.0:          # 사전등록: 연도 층화 무작위(seed 20260930), 결과 무관
        rnd = random.Random(20260930)
        keep = {e["rcept_no"] for e in sup if rnd.random() < frac}
        todo = [e for e in todo if e["rcept_no"] in keep]
    print(f"[docs] supply total={len(sup)} todo={len(todo)}", flush=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)
    with fn.open("a", encoding="utf-8") as fh:
        for n, e in enumerate(todo):
            rc = e["rcept_no"]
            txt, src = "", ""
            p2 = T2_DOCS / f"{rc}.txt"
            if p2.exists():
                txt, src = p2.read_text(encoding="utf-8", errors="ignore"), "t2cache"
            else:
                for attempt in range(2):
                    try:
                        txt, src = web_doc_text(rc), "web"
                        break
                    except Exception:
                        time.sleep(3)
            f = parse_supply_contract(txt) if txt else {}
            fh.write(json.dumps({"rcept_no": rc, "src": src, "len": len(txt), "ratio_pct": f.get("ratio_pct"),
                                 "amount_krw": f.get("amount_krw"), "related_party": f.get("related_party"),
                                 "counterparty": f.get("counterparty")}, ensure_ascii=False) + "\n")
            if n % 100 == 0:
                fh.flush()
                print(f"[docs] {n}/{len(todo)} {datetime.now(KST):%H:%M:%S}", flush=True)


# ── Part 2: 판정 ────────────────────────────────────────────────────────────
YF_CACHE = ROOT / "data" / "price_backfill_yf" / "kr_fallen_price_cache_2018_2026.json"
YF_IDX = ROOT / "data" / "price_backfill_yf" / "kr"
T1KR = ROOT / "data" / "analysis" / "model_T1kr_earnings_20260928"
HS2 = (1, 5, 10, 20)
IS_END = "2022-12-31"


def load_part2_book() -> Book:
    raw = json.loads(YF_CACHE.read_text(encoding="utf-8"))
    bars = {t: [(x["d"], float(x["o"] or 0), float(x["c"] or 0), float(x["v"] or 0)) for x in rows if x.get("c")]
            for t, rows in raw.items()}
    del raw
    for p in (T1KR / "prices").glob("*.csv"):
        t = p.stem
        if t not in bars:
            b = read_csv_bars(p)
            if len(b) > 30:
                bars[t] = b
    for t in ("069500", "229200"):
        bars[t] = read_csv_bars(YF_IDX / f"kr_{t}.csv")
    cal = [r[0] for r in bars["069500"]]
    return Book(bars, cal)


def followup_chain() -> dict[str, str]:
    """후속 서류(정정·첨부추가·발행조건확정) rcept_no → 같은 회사·같은 종류의 직전 원공시 rcept_no.
    DS005 API 는 결정 1건당 '최신본' 1개만 돌려준다(원공시 뒤에 정정이 있으면 정정 rcept_no 로 나온다) —
    원공시 rcept_no 로만 맞추면 '나중에 정정되지 않은 건'만 남는 사후 선택이 된다(01:5x 발견, 결과 확정 전 수리)."""
    by = defaultdict(list)
    for fn in sorted(LIST_DIR.glob("B001_*.jsonl")):
        for l in fn.open(encoding="utf-8"):
            r = json.loads(l)
            nm = (r.get("report_nm") or "").strip()
            base = re.sub(r"^(\[[^\]]*\])+", "", nm).strip()
            k = event_kind(base)
            if k in DS_EP:
                by[(r["corp_code"], k)].append((r["rcept_no"], nm.startswith("[")))
    chain = {}
    for key, rows in by.items():
        last = None
        for rc, follow in sorted(rows):
            if not follow:
                last = rc
                chain[rc] = rc
            elif last:
                chain[rc] = last
    return chain


def build_fields(ev: list[dict]) -> None:
    ds = defaultdict(dict)
    chain = followup_chain()
    if (OUT / "ds005.jsonl").exists():
        for l in (OUT / "ds005.jsonl").open(encoding="utf-8"):
            x = json.loads(l)
            for it in sorted(x["list"], key=lambda z: z.get("rcept_no") or ""):
                orig = chain.get(it.get("rcept_no"))
                if orig and orig not in ds[x["ep"]]:      # 같은 원공시에 여러 본이면 가장 이른 본(사후 정보 최소)
                    it = dict(it)
                    it["_via"] = "orig" if orig == it.get("rcept_no") else "followup"
                    ds[x["ep"]][orig] = it
    sh = {}
    if (OUT / "shares.jsonl").exists():
        for l in (OUT / "shares.jsonl").open(encoding="utf-8"):
            x = json.loads(l)
            sh[(x["corp_code"], x["year"])] = x.get("shares")
    sup = {}
    if (OUT / "supply_fields.jsonl").exists():
        for l in (OUT / "supply_fields.jsonl").open(encoding="utf-8"):
            x = json.loads(l)
            sup[x["rcept_no"]] = x
    for e in ev:
        k = e["kind"]
        it = ds.get(DS_EP.get(k, ""), {}).get(e["rcept_no"])
        e["has_fields"] = it is not None
        e["fields_via"] = (it or {}).get("_via")
        if k == "rights" and it:
            m = it.get("ic_mthn") or ""
            e["method"] = "3rd" if "제3자" in m else ("holder" if "주주배정" in m else ("public" if "일반공모" in m else "other"))
            n_new = (_numv(it.get("nstk_ostk_cnt")) or 0) + (_numv(it.get("nstk_estk_cnt")) or 0)
            pre = _numv(it.get("bfic_tisstk_ostk"))
            e["dilution"] = n_new / pre if pre else None
        elif k in ("bonus", "bonus_mixed") and it:
            e["bonus_ratio"] = _numv(it.get("nstk_ascnt_ps_ostk"))
        elif k == "buyback" and it:
            e["bb_shares"] = _numv(it.get("aqpln_stk_ostk"))
        elif k == "trust" and it:
            e["tr_amt"] = _numv(it.get("ctr_prc"))
        if k in ("buyback", "trust"):
            e["shares_out"] = sh.get((e["corp_code"], share_year(e["rcept_dt"])))
        if k == "supply":
            s = sup.get(e["rcept_no"])
            e["has_fields"] = bool(s and s.get("len"))
            e["sampled"] = s is not None
            if s:
                e["ratio"] = s.get("ratio_pct")
                e["related"] = s.get("related_party")
        if k == "major":
            nm = e["report"]
            e["mh_sub"] = "transfer" if "양수도" in nm else ("pledge" if "담보" in nm else "other")


RULES = {
    "S0 공급계약 전체": ("supply", lambda e: e.get("sampled")),
    "S1 공급 매출대비≥10%": ("supply", lambda e: (e.get("ratio") or 0) >= 10),
    "S2 공급 ≥30%": ("supply", lambda e: (e.get("ratio") or 0) >= 30),
    "S3 공급 ≥30% & 비관계사": ("supply", lambda e: (e.get("ratio") or 0) >= 30 and e.get("related") is False),
    "B0 무상증자 전체": ("bonus", lambda e: True),
    "B1 무상 배정≥0.5": ("bonus", lambda e: (e.get("bonus_ratio") or 0) >= 0.5),
    "B2 무상 배정≥1.0": ("bonus", lambda e: (e.get("bonus_ratio") or 0) >= 1.0),
    "B3 유무상증자 전체": ("bonus_mixed", lambda e: True),
    "Y0 자사주 직접취득 전체": ("buyback", lambda e: True),
    "Y1 직접취득 ≥1% 발행주식": ("buyback", lambda e: e.get("bb_shares") and e.get("shares_out")
                              and e["bb_shares"] / e["shares_out"] >= 0.01),
    "Y2 자사주 신탁 전체": ("trust", lambda e: True),
    "Y3 신탁 ≥1% 시총": ("trust", lambda e: e.get("tr_amt") and e.get("shares_out") and e.get("closeD")
                        and e["tr_amt"] / (e["closeD"] * e["shares_out"]) >= 0.01),
    "R0 유상증자 전체": ("rights", lambda e: True),
    "R1 제3자배정": ("rights", lambda e: e.get("method") == "3rd"),
    "R2 주주배정": ("rights", lambda e: e.get("method") == "holder"),
    "R3 일반공모": ("rights", lambda e: e.get("method") == "public"),
    "M0 최대주주변경 전체": ("major", lambda e: True),
    "M1 최대주주 양수도": ("major", lambda e: e.get("mh_sub") == "transfer"),
    "M2 최대주주 담보": ("major", lambda e: e.get("mh_sub") == "pledge"),
    "M3 최대주주 기타": ("major", lambda e: e.get("mh_sub") == "other"),
    "P0 잠정실적 전체": ("prelim", lambda e: True),
    "P1 흑자전환&매출↑(N6)": ("prelim", lambda e: e.get("turn_up") == 1 and (e.get("rev_yoy") or -1) > 0),
    "P2 영업익 YoY≥+100% & 흑자": ("prelim", lambda e: (e.get("op_yoy") or -99) >= 1.0 and (e.get("op_margin") or -1) > 0),
    "C0 주식소각 전체": ("cancel", lambda e: True),
}


def load_prelim() -> list[dict]:
    import gzip
    out = []
    with gzip.open(T1KR / "events_features.csv.gz", "rt", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["ed"] < "2019-01-01":
                continue
            f = lambda k: float(r[k]) if r.get(k) not in (None, "") else None  # noqa: E731
            out.append({"rcept_no": r["rcept_no"], "tk": r["t"], "kind": "prelim", "entry_date": r["ed"],
                        "rcept_dt": r["rcept_no"][:8], "turn_up": f("turn_up"), "rev_yoy": f("rev_yoy"),
                        "op_yoy": f("op_yoy"), "op_margin": f("op_margin"), "has_fields": True})
    return out


def cmd_part2() -> None:
    ev, cov = load_list_events()
    build_fields(ev)
    ev += load_prelim()
    bk = load_part2_book()
    print(f"[part2] events={len(ev)} tickers={len(bk.tk)} cal={bk.cal[0]}~{bk.cal[-1]}", flush=True)
    cal = bk.cal
    import bisect
    k2, q2 = bk.col["069500"], bk.col["229200"]
    recs, nopx = [], Counter()
    seen = set()
    for e in sorted(ev, key=lambda x: x["rcept_no"]):
        if e["kind"] == "prelim":
            if e["entry_date"] not in bk.ix:
                nopx[("prelim", "nocal")] += 1
                continue
            s = bk.ix[e["entry_date"]] - 1
        else:
            d = f"{e['rcept_dt'][:4]}-{e['rcept_dt'][4:6]}-{e['rcept_dt'][6:]}"
            s = bisect.bisect_right(cal, d) - 1
        if s < 25 or s + 1 >= len(cal):
            continue
        key = (e["tk"], s, e["kind"])
        if key in seen:
            continue
        seen.add(key)
        j = bk.col.get(e["tk"])
        yr = cal[s + 1][:4]
        if j is None or np.isnan(bk.c[s, j]):
            nopx[(e["kind"], yr)] += 1
            continue
        ee = s + 1
        e = dict(e)
        e["closeD"] = float(bk.c[s, j])
        e["entry"] = cal[ee]
        e["elig"] = bool(bk.eligible(s, ee)[j])
        dv = float(bk.dv20[s, j]) if bk.dv20[s, j] == bk.dv20[s, j] else float("nan")
        for h in HS2:
            r = bk.fwd(j, ee, h)
            if r != r:
                continue
            r = float(r)
            sb = bk.size_bench(s, ee, h, dv)
            ki, kq = bk.fwd(k2, ee, h), bk.fwd(q2, ee, h)
            e[f"r{h}"] = r
            e[f"ex{h}"] = r - COST - sb if sb == sb else None
            e[f"xk{h}"] = r - COST - float(ki) if ki == ki else None
            e[f"xq{h}"] = r - COST - float(kq) if kq == kq else None
        recs.append(e)
    with (OUT / "part2_events.jsonl").open("w", encoding="utf-8") as fh:
        for e in recs:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")
    res = {"coverage_list": {"|".join(k): v for k, v in sorted(cov.items())},
           "nopx": {"|".join(map(str, k)): v for k, v in sorted(nopx.items())},
           "fields_missing": {}, "rules": {}}
    for kind in ("supply", "bonus", "bonus_mixed", "buyback", "trust", "rights", "major", "cancel", "prelim"):
        sub = [e for e in recs if e["kind"] == kind]
        res["fields_missing"][kind] = {"n": len(sub), "no_fields": sum(1 for e in sub if not e.get("has_fields")),
                                       "no_shares": sum(1 for e in sub if kind in ("buyback", "trust") and not e.get("shares_out")),
                                       "supply_sampled": sum(1 for e in sub if e.get("sampled")),
                                       "supply_ratio_missing": sum(1 for e in sub if e.get("sampled") and e.get("ratio") is None)}
    for name, (kind, fn) in RULES.items():
        allr = [e for e in recs if e["kind"] == kind and fn(e)]
        el = [e for e in allr if e["elig"]]
        d = {"n_all": len(allr), "n_elig": len(el)}
        for part, rows in (("IS", [e for e in el if e["entry"] <= IS_END]), ("OOS", [e for e in el if e["entry"] > IS_END])):
            for h in HS2:
                for key in ("ex", "xk", "xq"):
                    m, t, n = nw_t(date_series(rows, f"{key}{h}"), h - 1)
                    d[f"{part}_{key}{h}"] = [round(m, 3) if m == m else None, round(t, 2) if t == t else None, n]
            d[f"{part}_n"] = len(rows)
        yrs = {}
        for y in range(2019, 2027):
            rows = [e for e in el if e["entry"][:4] == str(y)]
            m, t, n = nw_t(date_series(rows, "ex5"), 4)
            yrs[str(y)] = [round(m, 2) if m == m else None, n]
        d["years_ex5"] = yrs
        oos_pos = sum(1 for y in ("2023", "2024", "2025", "2026") if (yrs[y][0] or 0) > 0 and yrs[y][1] > 0)
        all_pos = sum(1 for y in yrs if (yrs[y][0] or 0) > 0)
        d["oos_years_pos"], d["all_years_pos"] = oos_pos, f"{all_pos}/{sum(1 for y in yrs if yrs[y][1])}"
        m_all, t_all, n_all = nw_t(date_series(allr, "ex5"), 4)
        d["ALLpop_ex5"] = [round(m_all, 3) if m_all == m_all else None, round(t_all, 2) if t_all == t_all else None, n_all]
        m_oos, t_oos = d["OOS_ex5"][0], d["OOS_ex5"][1]
        is5 = d["IS_ex5"][0]
        if m_oos is not None and m_oos > 0 and t_oos is not None and t_oos >= 2.64 and oos_pos >= 3 and (is5 or 0) > 0:
            v = "확정"
        elif m_oos is not None and m_oos > 0 and t_oos is not None and t_oos >= 2.0 and oos_pos >= 3:
            v = "후보"
        else:
            v = "기각"
        d["verdict"] = v
        # 진단(판정 밖): OOS 이벤트 분포
        oo = [e for e in el if e["entry"] > IS_END and e.get("ex5") is not None]
        if oo:
            xs = sorted(e["ex5"] for e in oo)
            k5 = max(1, len(xs) // 20)
            trimmed = [e for e in oo if xs[k5 - 1] < e["ex5"] < xs[-k5]] if len(xs) > 40 else oo
            mt, tt, nt = nw_t(date_series(trimmed, "ex5"), 4)
            months = max(1, (int(oo[-1]["entry"][:4]) - 2023) * 12 + int(oo[-1]["entry"][5:7]))
            d["diag_oos"] = {"n_ev": len(oo), "median_ex5": round(xs[len(xs) // 2], 2),
                             "hit": round(sum(1 for x in xs if x > 0) / len(xs), 3),
                             "trim5_ex5": [round(mt, 2), round(tt, 2), nt], "events_per_month": round(len(oo) / months, 2),
                             "raw_net5_mean": round(float(np.mean([e["r5"] - COST for e in oo])), 2)}
        res["rules"][name] = d
        print(f"{name:28s} n={d['n_elig']:5d} IS5={d['IS_ex5']} OOS5={d['OOS_ex5']} OOS20={d['OOS_ex20']} "
              f"yrs+={oos_pos}/4 {v}", flush=True)
    (OUT / "part2_results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")


# ── 2차 사전등록 (a)~(d) ────────────────────────────────────────────────────
PXN = OUT / "px_naver"


def naver_full(code: str) -> dict[str, tuple]:
    """네이버 fchart 수정주가 일봉 {date: (o,h,l,c,v)} — 캐시."""
    PXN.mkdir(parents=True, exist_ok=True)
    fp = PXN / f"{code}.json"
    if fp.exists():
        return {d: tuple(v) for d, v in json.loads(fp.read_text(encoding="utf-8")).items()}
    u = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count=2400&requestType=0"
    out = {}
    for attempt in range(2):
        try:
            s = _get(u, 25).decode("cp949", "ignore")
            for m in re.finditer(r'data="(\d{8})\|([\d.]+)\|([\d.]+)\|([\d.]+)\|([\d.]+)\|(\d+)"', s):
                d = m.group(1)
                out[f"{d[:4]}-{d[4:6]}-{d[6:]}"] = tuple(float(m.group(i)) for i in range(2, 7))
            break
        except Exception:
            time.sleep(2)
    time.sleep(0.3)
    fp.write_text(json.dumps(out), encoding="utf-8")
    return out


def _kdate(s: str | None) -> str | None:
    m = re.search(r"(\d{4})\D+(\d{1,2})\D+(\d{1,2})", s or "")
    return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


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


def rule_stats(rows: list[dict], key: str, h: int) -> dict:
    d = {}
    for part, rr in (("IS", [e for e in rows if e["entry"] <= IS_END]), ("OOS", [e for e in rows if e["entry"] > IS_END])):
        m, t, n = nw_t(date_series(rr, key), h - 1)
        d[part] = [round(m, 3) if m == m else None, round(t, 2) if t == t else None, n, len([e for e in rr if e.get(key) is not None])]
    yrs = {}
    for y in range(2019, 2027):
        m, t, n = nw_t(date_series([e for e in rows if e["entry"][:4] == str(y)], key), h - 1)
        yrs[str(y)] = [round(m, 2) if m == m else None, n]
    d["years"] = yrs
    d["oos_pos"] = sum(1 for y in ("2023", "2024", "2025", "2026") if (yrs[y][0] or 0) > 0 and yrs[y][1] > 0)
    m, t = d["OOS"][0], d["OOS"][1]
    ok = m is not None and m > 0 and (t or 0) >= 2.64 and d["oos_pos"] >= 3 and (d["IS"][0] or 0) > 0
    cand = m is not None and m > 0 and (t or 0) >= 2.0 and d["oos_pos"] >= 3
    d["verdict"] = "확정" if ok else ("후보" if cand else "기각")
    return d


def cmd_ext() -> None:
    import bisect
    ev, _ = load_list_events()
    build_fields(ev)
    ds_bonus = {}
    for l in (OUT / "ds005.jsonl").open(encoding="utf-8"):
        x = json.loads(l)
        if x["ep"] == "fricDecsn":
            for it in x["list"]:
                ds_bonus[it["rcept_no"]] = it
    chain = followup_chain()
    bon_it = {}
    for rc, it in sorted(ds_bonus.items()):
        o = chain.get(rc)
        if o and o not in bon_it:
            bon_it[o] = it
    bk = load_part2_book()
    cal = bk.cal
    res = {}
    # ---------------- (a) 무상 h20 ----------------
    A = []
    seen = set()
    for e in sorted([e for e in ev if e["kind"] == "bonus"], key=lambda x: x["rcept_no"]):
        d = f"{e['rcept_dt'][:4]}-{e['rcept_dt'][4:6]}-{e['rcept_dt'][6:]}"
        s = bisect.bisect_right(cal, d) - 1
        if s < 25 or s + 1 >= len(cal) or (e["tk"], s) in seen:
            continue
        seen.add((e["tk"], s))
        j = bk.col.get(e["tk"])
        nb = naver_full(e["tk"])
        if cal[s] not in nb:
            continue
        ee = s + 1
        it = bon_it.get(e["rcept_no"]) or {}
        b = e.get("bonus_ratio")
        rec_d, lst_d = _kdate(it.get("nstk_asstd")), _kdate(it.get("nstk_lstprd"))
        exd = cal[bisect.bisect_left(cal, rec_d) - 1] if rec_d and bisect.bisect_left(cal, rec_d) > 0 else None
        lst_i = bisect.bisect_left(cal, lst_d) if lst_d else None
        dv = float(bk.dv20[s, j]) if j is not None and bk.dv20[s, j] == bk.dv20[s, j] else float("nan")
        elig = bool(bk.eligible(s, ee)[j]) if j is not None else False
        r0 = {"tk": e["tk"], "entry": cal[ee], "b": b, "exd": exd, "lst": lst_d, "elig": elig, "yf": j is not None}
        for h in (5, 10, 20):
            r = naver_ret(nb, cal, ee, h, exdate=exd)
            if r is None:
                continue
            x = ee + h - 1
            rc = r
            if b and exd and lst_i is not None and exd <= cal[x] and lst_i > x:
                if lst_i < len(cal) and cal[lst_i] in nb:
                    p_l = nb[cal[lst_i]][0]
                    rc = ((1 / (1 + b)) * nb[cal[x]][3] + (b / (1 + b)) * p_l) / nb[cal[ee]][0] * 100 - 100
                    r0[f"constrained{h}"] = True
                else:
                    rc = None
            sb = bk.size_bench(s, ee, h, dv)
            if sb == sb:
                r0[f"exu{h}"] = r - COST - sb
                if rc is not None:
                    r0[f"ex{h}"] = rc - COST - sb
        A.append(r0)
    res["a"] = {"n_events": len(A), "n_elig": sum(1 for x in A if x["elig"]),
                "n_exd": sum(1 for x in A if x["exd"]), "n_constrained20": sum(1 for x in A if x.get("constrained20")),
                "rules": {}}
    for name, fn in (("B0 무상 전체", lambda x: True), ("B1 배정≥0.5", lambda x: (x["b"] or 0) >= 0.5),
                     ("B2 배정≥1.0", lambda x: (x["b"] or 0) >= 1.0)):
        rows = [x for x in A if x["elig"] and fn(x)]
        res["a"]["rules"][name] = {"h20": rule_stats(rows, "ex20", 20), "h20_unconstrained": rule_stats(rows, "exu20", 20),
                                   "h10": rule_stats(rows, "ex10", 10), "h5": rule_stats(rows, "ex5", 5)}
        # 진단: 권리락·상장일 대비 진입 위치
        pre = [x for x in rows if x["exd"] and x["exd"] >= x["entry"]]
        res["a"]["rules"][name]["n_exd_after_entry"] = len(pre)
    # ---------------- (b) 저유동 자사주 ----------------
    Bv = []
    seen = set()
    for e in sorted([e for e in ev if e["kind"] in ("buyback", "trust")], key=lambda x: x["rcept_no"]):
        d = f"{e['rcept_dt'][:4]}-{e['rcept_dt'][4:6]}-{e['rcept_dt'][6:]}"
        s = bisect.bisect_right(cal, d) - 1
        if s < 25 or s + 1 >= len(cal) or (e["tk"], s, e["kind"]) in seen:
            continue
        seen.add((e["tk"], s, e["kind"]))
        j = bk.col.get(e["tk"])
        if j is None or np.isnan(bk.c[s, j]):
            continue
        dv = float(bk.dv20[s, j])
        ee = s + 1
        band = (bk.c[s, j] >= PX_MIN) and (5e8 <= dv < 2e9) and (bk.hist[s, j] >= MIN_HIST) and (bk.o[ee, j] > 0)
        main = bool(bk.eligible(s, ee)[j])
        if not (band or main):
            continue
        nb = naver_full(e["tk"])
        prior = [nb[cal[i]] for i in range(max(0, s - 19), s + 1) if cal[i] in nb]
        spr = ar_spread(prior)
        closeD = float(bk.c[s, j])
        sh = e.get("shares_out")
        big = (e["kind"] == "buyback" and e.get("bb_shares") and sh and e["bb_shares"] / sh >= 0.01) or \
              (e["kind"] == "trust" and e.get("tr_amt") and sh and e["tr_amt"] / (closeD * sh) >= 0.01)
        r0 = {"tk": e["tk"], "kind": e["kind"], "entry": cal[ee], "band": bool(band), "main": main, "big": bool(big),
              "spread": spr, "rcept_dt": e["rcept_dt"]}
        for h in (1, 5, 10, 20):
            rn = naver_ret(nb, cal, ee, h)
            ry = bk.fwd(j, ee, h)
            if band:
                bb = band_bench(bk, s, ee, h, dv)
                if bb == bb:
                    if rn is not None and spr is not None:
                        r0[f"lx{h}"] = rn - COST - spr - bb
                        r0[f"lf{h}"] = rn - COST - 0.5 - bb
                    if ry == ry and spr is not None:
                        r0[f"ly{h}"] = float(ry) - COST - spr - bb
            if main:
                sb = bk.size_bench(s, ee, h, dv)
                if sb == sb:
                    if rn is not None:
                        r0[f"nx{h}"] = rn - COST - sb
                    if ry == ry:
                        r0[f"yx{h}"] = float(ry) - COST - sb
        Bv.append(r0)
    res["b"] = {"spread_median_band": float(np.median([x["spread"] for x in Bv if x["band"] and x["spread"] is not None])),
                "spread_median_main": float(np.median([x["spread"] for x in Bv if x["main"] and x["spread"] is not None])),
                "rules": {}}
    for name, fn in (("L0 저유동 직접 전체", lambda x: x["kind"] == "buyback"),
                     ("L1 저유동 직접 ≥1%", lambda x: x["kind"] == "buyback" and x["big"]),
                     ("L2 저유동 신탁 전체", lambda x: x["kind"] == "trust"),
                     ("L3 저유동 신탁 ≥1%", lambda x: x["kind"] == "trust" and x["big"])):
        rows = [x for x in Bv if x["band"] and fn(x)]
        res["b"]["rules"][name] = {"n": len(rows), "h5": rule_stats(rows, "lx5", 5), "h5_fixed0.5": rule_stats(rows, "lf5", 5),
                                   "h5_yf": rule_stats(rows, "ly5", 5), "h20": rule_stats(rows, "lx20", 20),
                                   "h1": rule_stats(rows, "lx1", 1)}
    # ---------------- (c) Y1 견고성 ----------------
    y1 = [x for x in Bv if x["main"] and x["kind"] == "buyback" and x["big"]]
    c = {"naver_h5": rule_stats(y1, "nx5", 5), "yf_h5": rule_stats(y1, "yx5", 5), "loyo_all": {}, "loyo_oos": {}}
    for key in ("yx5", "nx5"):
        for y in range(2019, 2027):
            rr = [x for x in y1 if x["entry"][:4] != str(y)]
            m, t, n = nw_t(date_series(rr, key), 4)
            c["loyo_all"][f"{key}_-{y}"] = [round(m, 2), round(t, 2), n]
            if y >= 2023:
                rr = [x for x in y1 if x["entry"] > IS_END and x["entry"][:4] != str(y)]
                m, t, n = nw_t(date_series(rr, key), 4)
                c["loyo_oos"][f"{key}_-{y}"] = [round(m, 2), round(t, 2), n]
        for lab, fn in (("2024_pre_0226", lambda x: "2024-01-01" <= x["entry"] < "2024-02-26"),
                        ("2024_post_0226", lambda x: "2024-02-26" <= x["entry"] <= "2024-12-31"),
                        ("OOS_ex_2024post", lambda x: x["entry"] > IS_END and not ("2024-02-26" <= x["entry"] <= "2024-12-31")),
                        ("post_0226_all", lambda x: x["entry"] >= "2024-02-26"),
                        ("2023", lambda x: x["entry"][:4] == "2023")):
            rr = [x for x in y1 if fn(x)]
            m, t, n = nw_t(date_series(rr, key), 4)
            c[f"{key}_{lab}"] = [round(m, 2) if m == m else None, round(t, 2) if t == t else None, n]
    # 밸류업 전후 공시 빈도(전체 직접취득 ≥1%, 적격)
    cnt = Counter(("pre" if x["entry"] < "2024-02-26" else "post") + x["entry"][:4] for x in y1)
    c["counts"] = dict(sorted(cnt.items()))
    res["c"] = c
    # ---------------- (d) 매수금지 필터 ----------------
    res["d"] = avoid_filter(ev, cal)
    (OUT / "ext_results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    with (OUT / "ext_events_a.jsonl").open("w", encoding="utf-8") as fh:
        for x in A:
            fh.write(json.dumps(x, ensure_ascii=False) + "\n")
    with (OUT / "ext_events_b.jsonl").open("w", encoding="utf-8") as fh:
        for x in Bv:
            fh.write(json.dumps(x, ensure_ascii=False) + "\n")
    print(json.dumps(res, ensure_ascii=False)[:20000])


def avoid_filter(ev: list[dict], cal: list[str]) -> dict:
    import bisect
    import sqlite3
    from contextlib import closing
    flags = defaultdict(list)       # tk -> [(rcept_dt, flag)]
    for e in ev:
        if e["kind"] == "rights" and e.get("method") == "3rd":
            flags[e["tk"]].append((e["rcept_dt"], "F1"))
        if e["kind"] == "major" and e.get("mh_sub") == "other":
            flags[e["tk"]].append((e["rcept_dt"], "F2"))
    for fn in sorted(LIST_DIR.glob("*.jsonl")):
        if fn.name[5:9] < "2025":
            continue
        for l in fn.open(encoding="utf-8"):
            r = json.loads(l)
            nm = (r.get("report_nm") or "").strip()
            pre = re.match(r"^((\[[^\]]*\])+)", nm)
            sc = (r.get("stock_code") or "").strip()
            if sc and pre and "정정" in pre.group(1):
                flags[sc].append((r["rcept_dt"], "F3"))
    db = ROOT / "data" / "shadow" / "virtual_books.db"
    with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)) as con:
        rows = con.execute("select session_date, ticker, net_pct, meta from trades where strategy_id='xkr_fallen3' "
                           "and status='CLOSED' and net_pct is not null").fetchall()
    T = []
    # 거래일 달력: 레인 신호일 자체(공시 창 계산용) — 069500 캘린더가 09-11 에서 끝나므로 신호일 집합으로 보충
    cal2 = sorted(set(cal) | {r[0] for r in rows})
    for sd, tk, net, meta in rows:
        try:
            chg = (json.loads(meta).get("feat") or {}).get("chg")
        except Exception:
            chg = None
        i = bisect.bisect_left(cal2, sd)
        lo = cal2[i - 5].replace("-", "") if i >= 5 else "00000000"
        hi = sd.replace("-", "")
        fl = {f for (d, f) in flags.get(tk, []) if lo < d <= hi}
        T.append({"sd": sd, "tk": tk, "net": net, "chg": chg, "F1": "F1" in fl, "F2": "F2" in fl, "F3": "F3" in fl,
                  "F_any": bool(fl)})
    out = {"n_trades": len(T), "range": [min(t["sd"] for t in T), max(t["sd"] for t in T)]}
    for sub_name, sub in (("xkr_fallen3", T), ("chg<=-5", [t for t in T if t["chg"] is not None and t["chg"] <= -5])):
        dd = {}
        for f in ("F1", "F2", "F3", "F_any"):
            by = defaultdict(lambda: [[], []])
            for t in sub:
                by[t["sd"]][0 if t[f] else 1].append(t["net"])
            diffs = [(d, np.mean(a) - np.mean(b)) for d, (a, b) in sorted(by.items()) if a and b]
            ys = [v for _, v in diffs]
            m, tt, n = nw_t(ys, 6)
            half = len(ys) // 2
            h1 = float(np.mean(ys[:half])) if half else float("nan")
            h2 = float(np.mean(ys[half:])) if ys[half:] else float("nan")
            nf = sum(1 for t in sub if t[f])
            allm = float(np.mean([t["net"] for t in sub]))
            keep = [t["net"] for t in sub if not t[f]]
            ok = m == m and m < 0 and tt <= -2.0 and h1 < 0 and h2 < 0
            dd[f] = {"n_flag": nf, "days": n, "diff": round(m, 3) if m == m else None, "t": round(tt, 2) if tt == tt else None,
                     "half1": round(h1, 3), "half2": round(h2, 3),
                     "flag_mean_net": round(float(np.mean([t["net"] for t in sub if t[f]])), 3) if nf else None,
                     "pool_mean_net": round(allm, 3), "pool_mean_after_remove": round(float(np.mean(keep)), 3),
                     "verdict": "합격" if ok else "기각"}
        out[sub_name] = {"n": len(sub), **dd}
    return out


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "ext":
        cmd_ext()
        sys.exit(0)
    {"list": cmd_list, "part1": cmd_part1, "ds005": cmd_ds005, "shares": cmd_shares, "docs": cmd_docs,
     "part2": cmd_part2}[cmd]()
