#!/usr/bin/env python3
"""KR 현금배당 증가 공시 — 사전등록 §3 검정 (read-only 연구, 2026-10-30).

사전등록 정본: docs/reports/preregistration_q4_2026_20261022.md §3 (기준 동결 — 이 스크립트는 기준을 바꾸지 않는다).
운영 파일·봇·state·config·.env* 는 건드리지 않는다. 산출물은 data/analysis/kr_dividend_20261030/ 와
docs/reports/kr_dividend_increase_test_20261030.md 뿐. DART 키는 .env.live 의 DART_API_KEY 를 읽고 출력하지 않는다.

서브커맨드
  list      : DART list.json pblntf_detail_ty=I001(수시공시 — 현금ㆍ현물배당결정은 I001 서식이라 pblntf_ty=I 중 I001 로 충분)
              3개월 창·전 페이지. 2019-01-01~2026-09-29 는 기존 캐시(kr_event_20260930/list, 같은 파라미터)를 재사용하고
              2014-01-01~2018-12-31(2014 = 2015 이벤트의 전년 DPS 용)·2026-09-30~어제만 새로 받는다.
  docs      : 원공시 document.xml(OpenAPI) — 연도 층화 round-robin 순서, 원본 zip 캐시(raw/), 결과 색인(docs_index.jsonl).
  docs_web  : 같은 대상의 DART 웹 뷰어 본문(OpenAPI 한도 밖, 1 req/s 수준) — docs 와 반대 끝에서 채운다(보조 경로, src=web).
  alot      : 전년 DPS 보조 — alotMatter(전년 사업보고서 11011, 보통주 주당 현금배당금 당기값). 주 행 후보(시가배당률 ≥ 2%)
              중 직전 같은 종류 공시로 전년 DPS 를 못 찾은 결산배당만 조회(호출 절약, 결과 무관 선택).
  parse     : 원문 → 1주당 배당금(보통주)·시가배당률(보통주)·배당구분·배당종류·기준일. events_parsed.csv, coverage.csv.
  prices    : 네이버 fchart 수정주가 일봉(분석 전용 소스, KIS 아님) count=3300 — list 에 나온 종목코드 ∪ yf 캐시 ∪ data/price/kr.
  test      : 사전등록 검정.

호출 가드: OpenAPI 호출 간 0.5s, **KST 달력일당 상한 KRD_DAILY_CAP(기본 8000)을 디스크 카운터(calls_YYYYMMDD.json)로 유지**
(라이브 공시 레인과 일 20,000 한도 공유), 평일 KST 07:20~20:00 OpenAPI 대량 호출 중단(기존 규약). 상한 도달 시 저장 후 정상 종료.

규칙(사전등록 §3 그대로 + 구현 세부 — 결과 계산 전 고정, 2026-10-30 03:3x KST)
- 표본: report_nm 공백 제거·가운뎃점(· U+00B7 / ㆍ U+318D) 정규화 후 정확히 '현금ㆍ현물배당결정' 인 원공시.
  '[' 로 시작(기재정정·첨부추가 등 후속 서류)은 제외. '(자회사의 주요경영사항)'은 자회사 DPS 라 모회사 이벤트가 아니므로 분모·분자 모두
  제외하고 건수만 따로 보고. 주식배당결정·부동산투자회사금전배당결정·주주명부폐쇄(기준일)결정 제외.
- 수집률(검정 불가 조건) = 연도별 (DPS 파싱 성공 원공시) / (DART list 원공시 건수), 2015~ 합계 기준 90%.
  필드 결측(시가배당률 '-' 등)은 별도 열.
- 회계기간 키: 결산배당 → 기준일 월 ≤ 5 이면 FY = 기준일 연도−1, 아니면 기준일 연도(2024~ 기준일 후행 체계 대응).
  분기·중간배당 → (기준일 연도, 기준일 분기). 기준일이 비면 접수일로 같은 규칙. 같은 (회사, 구분, 기간) 원공시가 여럿이면 가장 이른 것.
- 전년 DPS: ① 같은 회사·같은 구분·전년 기간 원공시의 DPS(가장 이른 본) ② 없으면 결산배당에 한해 alotMatter(FY−1 사업보고서) 당기 DPS.
  정정본 값으로 빈칸을 채우지 않는다. 전년 DPS 0·결측 → 증가율 정의 불가(배당 개시) → 주 행 제외, 건수 보고.
- 주 행: DPS 전년 대비 ≥ +20% 그리고 시가배당률 ≥ 2% (현금배당 — 배당종류 '현물'만인 건 제외).
- 시점: 신호일 D = 접수일 이하 마지막 거래일(장 마감 후 접수 가정), 진입 = D 다음 거래일 시가, H20 = 진입 세션 1로 센 20번째 종가.
  비용 0.25% 왕복. 거래일 달력 = 069500 봉.
- 가격: 네이버 fchart 수정주가(상폐 종목 없음 = 생존편향, 대체 없음). 창 안 어떤 봉이라도 시가·종가가 전일 종가 대비 ±30.5% 밖이면 결측.
- 벤치 ① 규모매칭(주): kr_event_20260930 과 같은 기계 — D 적격(D 종가 ≥ 1,000, dvol20 ≥ 20억, 이력 ≥ 25봉, 다음 시가 > 0) 전 종목
  dvol20 십분위, 이벤트 종목 십분위(적격 미달은 최하 십분위 클립) 같은 창 등가중. ② 069500(KOSPI200). ③ 배당결정 공시 종목 전체(기저).
  1차 모집단 = D 적격 이벤트(벤치와 같은 문턱), 전체는 참고.
- t: 진입일 클러스터(같은 진입일 등가중 1관측) Newey-West, lag 19. IS = 진입 2015~2022, OOS = 2023~.
- 합격: t ≥ 2.64 그리고 OOS 연도(2023~2026) 부호 과반(≥3/4 양) 그리고 2024 제외 시 같은 부호. t 는 전기간 t 를 판정값으로 쓰고
  OOS t 와 IS 부호를 함께 보고한다(사전등록 문구가 전기간/OOS 를 특정하지 않음 — 둘이 갈리면 운영자 판단 항목으로 표기).
"""
from __future__ import annotations

import bisect
import csv
import html
import io
import json
import math
import os
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
OUT = ROOT / "data" / "analysis" / "kr_dividend_20261030"
LIST_DIR = OUT / "list"
OLD_LIST = ROOT / "data" / "analysis" / "kr_event_20260930" / "list"
RAW = OUT / "raw"
PX = OUT / "px"
DOC_IDX = OUT / "docs_index.jsonl"
REPORT = ROOT / "docs" / "reports" / "kr_dividend_increase_test_20261030.md"
KST = timezone(timedelta(hours=9))
SLEEP = 0.5
DAILY_CAP = int(os.getenv("KRD_DAILY_CAP", "8000"))
COST = 0.25
H = 20
IS_END = "2022-12-31"
OLD_LIST_END = date(2026, 9, 29)

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env.live")
except Exception:
    pass
KEY = (os.getenv("DART_API_KEY") or "").strip()


class Budget(Exception):
    pass


# ── DART 호출(디스크 일 카운터) ───────────────────────────────────────────────
def _cnt_path() -> Path:
    return OUT / f"calls_{datetime.now(KST):%Y%m%d}.json"


def calls_today() -> int:
    p = _cnt_path()
    try:
        return int(json.loads(p.read_text(encoding="utf-8"))["calls"])
    except Exception:
        return 0


_CNT = {"n": None, "since_flush": 0}


def _bump() -> int:
    if _CNT["n"] is None or _CNT.get("day") != datetime.now(KST).strftime("%Y%m%d"):
        _CNT["n"] = calls_today()
        _CNT["day"] = datetime.now(KST).strftime("%Y%m%d")
    _CNT["n"] += 1
    _CNT["since_flush"] += 1
    if _CNT["since_flush"] >= 20:
        flush_counter()
    return _CNT["n"]


def flush_counter() -> None:
    if _CNT["n"] is None:
        return
    OUT.mkdir(parents=True, exist_ok=True)
    p = _cnt_path()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"calls": _CNT["n"], "cap": DAILY_CAP, "at": datetime.now(KST).isoformat()}), encoding="utf-8")
    tmp.replace(p)
    _CNT["since_flush"] = 0


def blocked_now() -> bool:
    now = datetime.now(KST)
    if os.getenv("KRD_IGNORE_BLOCK") == "1":
        return False
    return now.weekday() < 5 and "07:20" <= now.strftime("%H:%M") < "20:00"


def _get(url: str, timeout: float = 25.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def _precheck() -> None:
    if blocked_now():
        raise Budget("blocked window (평일 07:20~20:00 KST, 라이브 레인 시간)")
    n = _CNT["n"] if _CNT["n"] is not None and _CNT.get("day") == datetime.now(KST).strftime("%Y%m%d") else calls_today()
    if n >= DAILY_CAP:
        raise Budget(f"daily cap {DAILY_CAP} reached")


def dart(endpoint: str, **params) -> dict:
    _precheck()
    q = urllib.parse.urlencode({"crtfc_key": KEY, **params})
    for attempt in range(3):
        _bump()
        try:
            d = json.loads(_get(f"https://opendart.fss.or.kr/api/{endpoint}.json?{q}"))
            time.sleep(SLEEP)
            if d.get("status") == "020":
                raise Budget("DART 020 limit")
            return d
        except Budget:
            raise
        except Exception:
            time.sleep(2.0 * (attempt + 1))
    return {"status": "ERR"}


def dart_doc(rc: str) -> tuple[str, bytes | None]:
    """document.xml → (status, zip bytes). status: ok / nofile(014 등 확정 실패) / err(재시도 대상)."""
    _precheck()
    q = urllib.parse.urlencode({"crtfc_key": KEY, "rcept_no": rc})
    for attempt in range(3):
        _bump()
        try:
            b = _get(f"https://opendart.fss.or.kr/api/document.xml?{q}")
            time.sleep(SLEEP)
            if b[:2] == b"PK":
                return "ok", b
            s = b[:400].decode("utf-8", "ignore")
            m = re.search(r"<status>(\d+)</status>", s) or re.search(r'"status"\s*:\s*"(\d+)"', s)
            st = m.group(1) if m else "?"
            if st == "020":
                raise Budget("DART 020 limit")
            if st in ("013", "014"):
                return f"nofile_{st}", None
            return f"err_{st}", None
        except Budget:
            raise
        except Exception:
            time.sleep(2.0 * (attempt + 1))
    return "err_net", None


# ── list ────────────────────────────────────────────────────────────────────
def windows(d0: date, end: date) -> list[tuple[date, date]]:
    out = []
    while d0 <= end:
        m = d0.month + 2
        y = d0.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        nxt = date(y + (1 if m == 12 else 0), 1 if m == 12 else m + 1, 1)
        d1 = min(nxt - timedelta(days=1), end)
        out.append((d0, d1))
        d0 = d1 + timedelta(days=1)
    return out


def list_windows() -> list[tuple[date, date]]:
    a = os.getenv("KRD_LIST_FROM")
    b = os.getenv("KRD_LIST_TO")
    if a and b:     # dry run 용
        return [(date.fromisoformat(a), date.fromisoformat(b))]
    end = datetime.now(KST).date() - timedelta(days=1)
    return windows(date(2014, 1, 1), date(2018, 12, 31)) + windows(OLD_LIST_END + timedelta(days=1), end)


def cmd_list() -> None:
    LIST_DIR.mkdir(parents=True, exist_ok=True)
    for a, b in list_windows():
        fn = LIST_DIR / f"I001_{a:%Y%m%d}_{b:%Y%m%d}.jsonl"
        if fn.exists():
            continue
        rows, page, tot = [], 1, None
        try:
            while True:
                d = dart("list", bgn_de=f"{a:%Y%m%d}", end_de=f"{b:%Y%m%d}", pblntf_detail_ty="I001",
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
            flush_counter()
            print(f"[list] stop: {e} calls_today={calls_today()} (창 {a}~{b} 미완료, 재실행 시 이 창부터)", flush=True)
            raise
        except Exception as e:
            print(f"[list] {a} fail: {e}", flush=True)
            continue
        tmp = fn.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        tmp.replace(fn)
        flush_counter()
        print(f"[list] {a}~{b} rows={len(rows)} pages={tot} calls_today={calls_today()}", flush=True)


TARGET = "현금ㆍ현물배당결정"


def norm_nm(nm: str) -> str:
    return re.sub(r"\s+", "", (nm or "").replace("·", "ㆍ").replace("‧", "ㆍ").replace("・", "ㆍ"))


def list_files() -> list[Path]:
    fs = []
    for fn in sorted(OLD_LIST.glob("I001_*.jsonl")):
        fs.append(fn)
    for fn in sorted(LIST_DIR.glob("I001_*.jsonl")):
        fs.append(fn)
    return fs


def load_list() -> tuple[list[dict], Counter]:
    """원공시 목록(rcept_no 중복 제거) + 분류 카운터(연도, 분류)."""
    ev, cnt, seen = [], Counter(), set()
    for fn in list_files():
        for l in fn.open(encoding="utf-8"):
            r = json.loads(l)
            nm = norm_nm(r.get("report_nm"))
            if TARGET not in nm or r["rcept_no"] in seen:
                continue
            seen.add(r["rcept_no"])
            y = r["rcept_dt"][:4]
            if nm.startswith("["):
                cnt[(y, "followup")] += 1
                continue
            if "자회사" in nm:
                cnt[(y, "subsidiary")] += 1
                continue
            if not nm.startswith(TARGET):
                cnt[(y, "other_variant")] += 1
                continue
            if nm != TARGET:    # '(분기배당)'·'(중간배당)'·'(제53기)' 등 괄호 부기 원공시 — 원공시로 포함(05:0x 수정, 결과 계산 전)
                cnt[(y, "orig_with_suffix")] += 1
            cnt[(y, "orig")] += 1
            ev.append({"rcept_no": r["rcept_no"], "rcept_dt": r["rcept_dt"], "corp_code": r["corp_code"],
                       "name": r.get("corp_name"), "tk": (r.get("stock_code") or "").strip(),
                       "corp_cls": r.get("corp_cls"), "rm": r.get("rm") or ""})
    ev.sort(key=lambda x: x["rcept_no"])
    return ev, cnt


# ── docs ────────────────────────────────────────────────────────────────────
def doc_index() -> dict[str, dict]:
    idx = {}
    if DOC_IDX.exists():
        for l in DOC_IDX.open(encoding="utf-8"):
            try:
                x = json.loads(l)
            except Exception:
                continue
            prev = idx.get(x["rcept_no"])
            if prev is None or not prev["status"].startswith("ok") or x["status"].startswith("ok"):
                idx[x["rcept_no"]] = x
    return idx


def have_doc(rc: str) -> bool:
    return (RAW / f"{rc}.zip").exists() or (RAW / f"{rc}.web.txt").exists()


def doc_todo(order_rev: bool = False) -> list[dict]:
    ev, _ = load_list()
    lo = os.getenv("KRD_DOC_FROM", "20140101")
    hi = os.getenv("KRD_DOC_TO", "99991231")
    idx = doc_index()
    by = defaultdict(list)
    for e in ev:
        rc = e["rcept_no"]
        if not (lo <= e["rcept_dt"] <= hi):
            continue
        if have_doc(rc) or (idx.get(rc, {}).get("status", "").startswith("nofile")):
            continue
        by[e["rcept_dt"][:4]].append(e)
    # 연도 층화 round-robin(부분 수집이어도 연도별 수집률이 고르게)
    yrs = sorted(by)
    out, i = [], 0
    while any(by[y] for y in yrs):
        for y in yrs:
            if i < len(by[y]):
                out.append(by[y][i])
        i += 1
        if all(i >= len(by[y]) for y in yrs):
            break
    return out[::-1] if order_rev else out


def _log_doc(rec: dict) -> None:
    with DOC_IDX.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def cmd_docs() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    todo = doc_todo()
    print(f"[docs] todo={len(todo)} calls_today={calls_today()} cap={DAILY_CAP}", flush=True)
    for n, e in enumerate(todo):
        rc = e["rcept_no"]
        if have_doc(rc):
            continue
        try:
            st, b = dart_doc(rc)
        except Budget as ex:
            flush_counter()
            print(f"[docs] stop: {ex} done_this_run={n} remaining={len(todo) - n} calls_today={calls_today()}", flush=True)
            return
        if b:
            tmp = RAW / f"{rc}.zip.tmp"
            tmp.write_bytes(b)
            tmp.replace(RAW / f"{rc}.zip")
        _log_doc({"rcept_no": rc, "status": st, "src": "api", "at": datetime.now(KST).isoformat(timespec="seconds")})
        if n % 200 == 0:
            flush_counter()
            print(f"[docs] {n}/{len(todo)} calls_today={calls_today()} {datetime.now(KST):%H:%M:%S}", flush=True)
    flush_counter()
    print(f"[docs] done calls_today={calls_today()}", flush=True)


def _web(u: str) -> str:
    b = _get(u, 25)
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp949", "ignore")


def web_doc_text(rc: str) -> str:
    h = _web(f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rc}")
    time.sleep(0.5)
    m = re.search(r'viewDoc\("(\d+)", "(\d+)", "0", "0", "0", "(\w+)"', h)
    if not m:
        return ""
    v = _web(f"https://dart.fss.or.kr/report/viewer.do?rcpNo={rc}&dcmNo={m.group(2)}&eleId=0&offset=0&length=0&dtd={m.group(3)}")
    time.sleep(0.5)
    return flatten(v)


def cmd_docs_web() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    fwd = os.getenv("KRD_WEB_FORWARD") == "1"     # 두 번째 웹 프로세스: 앞 끝부터(API docs 와 같은 순서)
    todo = doc_todo(order_rev=not fwd)
    lim = int(os.getenv("KRD_WEB_MAX", "100000"))
    print(f"[docs_web] todo={len(todo)} ({'앞' if fwd else '반대'} 끝부터) max={lim}", flush=True)
    for n, e in enumerate(todo[:lim]):
        rc = e["rcept_no"]
        if have_doc(rc):
            continue
        txt, st = "", "err_web"
        for attempt in range(2):
            try:
                txt = web_doc_text(rc)
                st = "ok_web" if txt else "err_web_noview"
                break
            except Exception:
                time.sleep(5)
        if txt:
            tmp = RAW / f"{rc}.web.txt.tmp"
            tmp.write_text(txt, encoding="utf-8")
            tmp.replace(RAW / f"{rc}.web.txt")
        _log_doc({"rcept_no": rc, "status": st, "src": "web", "at": datetime.now(KST).isoformat(timespec="seconds")})
        if n % 200 == 0:
            print(f"[docs_web] {n}/{len(todo)} {datetime.now(KST):%H:%M:%S}", flush=True)


# ── parse ───────────────────────────────────────────────────────────────────
def flatten(t: str) -> str:
    t = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", t)
    t = re.sub(r"(?is)\.xforms[^{]*\{[^}]*\}", " ", t)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))).strip()


def doc_text(rc: str) -> tuple[str, str]:
    p = RAW / f"{rc}.zip"
    if p.exists():
        try:
            z = zipfile.ZipFile(io.BytesIO(p.read_bytes()))
            parts = []
            for n in z.namelist():
                raw = z.read(n)
                for enc in ("utf-8", "cp949", "euc-kr"):
                    try:
                        parts.append(raw.decode(enc))
                        break
                    except UnicodeDecodeError:
                        continue
            return flatten(" ".join(parts)), "api"
        except Exception:
            return "", "api_badzip"
    p = RAW / f"{rc}.web.txt"
    if p.exists():
        return p.read_text(encoding="utf-8"), "web"
    return "", ""


NUM = r"(-?[\d,]+(?:\.\d+)?|-)"


def _num(s: str | None) -> float | None:
    if s is None:
        return None
    s = s.replace(",", "").strip()
    if s in ("", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _date(s: str | None) -> str | None:
    m = re.search(r"(\d{4})\s*[-.년/]\s*(\d{1,2})\s*[-.월/]\s*(\d{1,2})", s or "")
    if not m:
        return None
    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if not (1 <= mo <= 12 and 1 <= d <= 31):
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


def parse_div(txt: str) -> dict:
    out: dict = {}
    if not txt:
        return {"perr": "no_text"}
    if "정정신고" in txt[:400] or "정정관련 공시서류" in txt[:600]:
        out["is_correction_doc"] = True
    m = re.search(r"배당\s*구분\s*(결산\s*배당|중간\s*배당|분기\s*배당|[^\s\d]{1,10})", txt)
    out["div_kind_raw"] = m.group(1).replace(" ", "") if m else None
    k = out["div_kind_raw"] or ""
    out["div_kind"] = "결산" if "결산" in k else ("분기" if "분기" in k else ("중간" if "중간" in k else None))
    m = re.search(r"배당\s*종류\s*(현금\s*배당|현물\s*배당|현금\s*및\s*현물\s*배당|[^\s\d]{1,12})", txt)
    out["div_type"] = m.group(1).replace(" ", "") if m else None
    m = re.search(r"1\s*주\s*당\s*배\s*당\s*금\s*\(\s*원\s*\)\s*보\s*통\s*주\s*(?:식)?\s*" + NUM, txt)
    out["dps_raw"] = m.group(1) if m else None
    out["dps"] = _num(out["dps_raw"])
    m = re.search(r"시\s*가\s*배\s*당\s*[율률]\s*\(\s*%\s*\)\s*보\s*통\s*주\s*(?:식)?\s*" + NUM, txt)
    out["yield_raw"] = m.group(1) if m else None
    out["yld"] = _num(out["yield_raw"])
    m = re.search(r"배당\s*기준\s*일\s*([^\s]+(?:\s*[^\s]+){0,2})", txt)
    out["rec_date"] = _date(m.group(1)) if m else None
    if out["dps_raw"] is None:
        out["perr"] = "dps_not_found"
    elif out["dps"] is None:
        out["perr"] = "dps_blank"
    return out


def fiscal_key(kind: str | None, rec: str | None, rcept_dt: str) -> tuple | None:
    d = rec or f"{rcept_dt[:4]}-{rcept_dt[4:6]}-{rcept_dt[6:]}"
    y, m = int(d[:4]), int(d[5:7])
    if kind == "결산":
        return ("결산", y - 1 if m <= 5 else y)
    if kind in ("분기", "중간"):
        return ("interim", y, (m - 1) // 3 + 1)
    return None


def prev_key(k: tuple) -> tuple:
    return (k[0], k[1] - 1) + tuple(k[2:])


def load_alot() -> dict:
    p = OUT / "alot.jsonl"
    out = {}
    if p.exists():
        for l in p.open(encoding="utf-8"):
            x = json.loads(l)
            out[(x["corp_code"], x["year"])] = x
    return out


def build_events() -> tuple[list[dict], dict]:
    ev, cnt = load_list()
    idx = doc_index()
    for e in ev:
        txt, src = doc_text(e["rcept_no"])
        e["doc_src"] = src
        e["doc_status"] = idx.get(e["rcept_no"], {}).get("status", "not_fetched")
        e.update(parse_div(txt) if src else {"perr": "not_fetched" if e["doc_status"] == "not_fetched" else e["doc_status"]})
        e["fkey"] = fiscal_key(e.get("div_kind"), e.get("rec_date"), e["rcept_dt"]) if e.get("dps") is not None else None
    # 같은 (회사, 기간 키) 원공시 중 가장 이른 것만 이벤트(재공시 중복 방지) — ev 는 rcept_no 오름차순
    first = {}
    for e in ev:
        if e["fkey"] is None:
            continue
        kk = (e["corp_code"],) + e["fkey"]
        if kk in first:
            e["dup_of"] = first[kk]["rcept_no"]
        else:
            first[kk] = e
    alot = load_alot()
    for e in ev:
        e["prior_dps"], e["prior_src"] = None, None
        if e["fkey"] is None or e.get("dup_of"):
            continue
        p = first.get((e["corp_code"],) + prev_key(e["fkey"]))
        if p is not None and p["rcept_no"] < e["rcept_no"]:
            e["prior_dps"], e["prior_src"] = p["dps"], "prev_decision"
        elif e["fkey"][0] == "결산":
            a = alot.get((e["corp_code"], str(e["fkey"][1] - 1)))
            if a is not None:
                e["prior_dps"], e["prior_src"] = a.get("dps"), ("alotMatter" if a.get("dps") is not None else "alotMatter_none")
        if e["prior_dps"] and e["prior_dps"] > 0:
            e["dps_yoy"] = e["dps"] / e["prior_dps"] - 1.0
    return ev, cnt


def alot_targets(ev: list[dict]) -> list[tuple[str, str]]:
    need = set()
    for e in ev:
        if e.get("fkey") and e["fkey"][0] == "결산" and not e.get("dup_of") and e.get("prior_src") is None \
                and (e.get("yld") or 0) >= 2.0 and e["rcept_dt"] >= "20150101":
            need.add((e["corp_code"], str(e["fkey"][1] - 1)))
    return sorted(need)


def cmd_alot() -> None:
    ev, _ = build_events()
    have = load_alot()
    todo = [x for x in alot_targets(ev) if x not in have]
    print(f"[alot] todo={len(todo)} calls_today={calls_today()}", flush=True)
    with (OUT / "alot.jsonl").open("a", encoding="utf-8") as fh:
        for n, (cc, y) in enumerate(todo):
            try:
                d = dart("alotMatter", corp_code=cc, bsns_year=y, reprt_code="11011")
            except Budget as ex:
                flush_counter()
                print(f"[alot] stop: {ex} done={n} remaining={len(todo) - n}", flush=True)
                return
            st = d.get("status")
            if st not in ("000", "013"):
                continue
            dps = None
            for r in d.get("list") or []:
                se = re.sub(r"\s+", "", r.get("se") or "")
                knd = re.sub(r"\s+", "", r.get("stock_knd") or "")
                if "주당현금배당금" in se and (knd in ("보통주", "보통주식", "") or "보통" in knd):
                    dps = _num(r.get("thstrm"))
                    if dps is not None:
                        break
            fh.write(json.dumps({"corp_code": cc, "year": y, "status": st, "dps": dps}) + "\n")
            if n % 100 == 0:
                fh.flush()
                flush_counter()
                print(f"[alot] {n}/{len(todo)} calls_today={calls_today()}", flush=True)
    flush_counter()


COLS = ["rcept_no", "rcept_dt", "corp_code", "tk", "name", "corp_cls", "rm", "doc_status", "doc_src", "perr",
        "is_correction_doc", "div_kind_raw", "div_kind", "div_type", "dps_raw", "dps", "yield_raw", "yld", "rec_date",
        "fkey", "dup_of", "prior_dps", "prior_src", "dps_yoy"]


def cmd_parse() -> dict:
    ev, cnt = build_events()
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "events_parsed.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLS, extrasaction="ignore")
        w.writeheader()
        for e in ev:
            r = dict(e)
            r["fkey"] = "|".join(map(str, e["fkey"])) if e.get("fkey") else ""
            w.writerow(r)
    yrs = sorted({k[0] for k in cnt})
    rows = []
    for y in yrs:
        sub = [e for e in ev if e["rcept_dt"][:4] == y]
        r = {"year": y, "dart_list_orig": cnt[(y, "orig")], "subsidiary_excl": cnt[(y, "subsidiary")],
             "followup_excl": cnt[(y, "followup")], "other_variant": cnt[(y, "other_variant")], "orig_with_suffix": cnt[(y, "orig_with_suffix")],
             "fetched": sum(1 for e in sub if e["doc_src"]), "fetched_web": sum(1 for e in sub if e["doc_src"] == "web"),
             "nofile": sum(1 for e in sub if str(e["doc_status"]).startswith("nofile")),
             "parsed_dps": sum(1 for e in sub if e.get("dps") is not None),
             "dps_blank": sum(1 for e in sub if e.get("perr") == "dps_blank"),
             "dps_not_found": sum(1 for e in sub if e.get("perr") == "dps_not_found"),
             "yield_present": sum(1 for e in sub if e.get("yld") is not None),
             "kind_known": sum(1 for e in sub if e.get("div_kind")),
             "prior_found": sum(1 for e in sub if e.get("prior_dps")),
             "prior_prev_decision": sum(1 for e in sub if e.get("prior_src") == "prev_decision" and e.get("prior_dps")),
             "prior_alot": sum(1 for e in sub if e.get("prior_src") == "alotMatter" and e.get("prior_dps"))}
        # 수집률 = 원문 확보 + 보통주 DPS 필드 판독(숫자 또는 명시적 '-' = 보통주 무배당) / list 원공시
        r["collected_read"] = r["parsed_dps"] + r["dps_blank"]
        r["coverage_pct"] = round(100 * r["collected_read"] / r["dart_list_orig"], 1) if r["dart_list_orig"] else None
        r["coverage_numeric_pct"] = round(100 * r["parsed_dps"] / r["dart_list_orig"], 1) if r["dart_list_orig"] else None
        rows.append(r)
    with (OUT / "coverage.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    tot_l = sum(r["dart_list_orig"] for r in rows if r["year"] >= "2015")
    tot_p = sum(r["collected_read"] for r in rows if r["year"] >= "2015")
    cov = {"list_2015plus": tot_l, "parsed_2015plus": tot_p,
           "coverage_pct_2015plus": round(100 * tot_p / tot_l, 2) if tot_l else None,
           "perr": dict(Counter(e.get("perr") or "ok" for e in ev if e["rcept_dt"] >= "20150101"))}
    (OUT / "coverage_summary.json").write_text(json.dumps(cov, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in rows:
        print(r, flush=True)
    print(cov, flush=True)
    return cov


# ── prices (네이버 fchart, 분석 전용) ───────────────────────────────────────
def naver_full(code: str) -> list[tuple]:
    PX.mkdir(parents=True, exist_ok=True)
    fp = PX / f"{code}.json"
    if fp.exists():
        return [tuple(x) for x in json.loads(fp.read_text(encoding="utf-8"))]
    u = f"https://fchart.stock.naver.com/sise.nhn?symbol={code}&timeframe=day&count=3300&requestType=0"
    out, ok = [], False
    for attempt in range(3):
        try:
            s = _get(u, 30).decode("cp949", "ignore")
            for m in re.finditer(r'data="(\d{8})\|([\d.]+)\|([\d.]+)\|([\d.]+)\|([\d.]+)\|(\d+)"', s):
                d = m.group(1)
                out.append((f"{d[:4]}-{d[4:6]}-{d[6:]}", float(m.group(2)), float(m.group(5)), float(m.group(6))))
            ok = True
            break
        except Exception:
            time.sleep(3)
    time.sleep(0.25)
    if ok:
        fp.write_text(json.dumps(out), encoding="utf-8")
    return out


def universe_codes() -> list[str]:
    codes = set()
    for fn in list_files():
        for l in fn.open(encoding="utf-8"):
            r = json.loads(l)
            sc = (r.get("stock_code") or "").strip()
            if len(sc) == 6 and r.get("corp_cls") in ("Y", "K"):
                codes.add(sc)
    yf = ROOT / "data" / "price_backfill_yf" / "kr_fallen_price_cache_2018_2026.json"
    if yf.exists():
        codes |= set(json.loads(yf.read_text(encoding="utf-8")))
    for p in (ROOT / "data" / "price" / "kr").glob("kr_*.csv"):
        codes.add(p.stem[3:])
    codes |= {"069500"}
    return sorted(c for c in codes if re.fullmatch(r"[0-9A-Z]{6}", c))


def cmd_prices() -> None:
    codes = universe_codes()
    todo = [c for c in codes if not (PX / f"{c}.json").exists()]
    print(f"[prices] codes={len(codes)} todo={len(todo)}", flush=True)
    for n, c in enumerate(todo):
        naver_full(c)
        if n % 200 == 0:
            print(f"[prices] {n}/{len(todo)} {datetime.now(KST):%H:%M:%S}", flush=True)


# ── 가격 행렬·벤치 (kr_event_20260930 Book 과 같은 규칙) ──────────────────────
import numpy as np  # noqa: E402

MIN_HIST, DVOL_MIN, PX_MIN, GUARD = 25, 2e9, 1000.0, 0.305


class Book:
    def __init__(self, bars: dict[str, list[tuple]], calendar: list[str]):
        self.cal = calendar
        self.ix = {d: i for i, d in enumerate(calendar)}
        self.tk = sorted(bars)
        self.col = {t: j for j, t in enumerate(self.tk)}
        T, N = len(calendar), len(self.tk)
        self.o = np.full((T, N), np.nan)
        self.c = np.full((T, N), np.nan)
        a = np.full((T, N), np.nan)
        for t, rows in bars.items():
            j = self.col[t]
            for d, o, c, v in rows:
                i = self.ix.get(d)
                if i is not None and c > 0:
                    self.o[i, j] = o if o > 0 else np.nan
                    self.c[i, j] = c
                    a[i, j] = c * v
        present = ~np.isnan(self.c)
        self.hist = np.cumsum(present, axis=0, dtype=np.int32)
        cs = np.cumsum(np.nan_to_num(a), axis=0)
        del a
        cn = np.cumsum(present, axis=0, dtype=np.int32)
        s20 = cs - np.vstack([np.zeros((20, N)), cs[:-20]])
        del cs
        n20 = cn - np.vstack([np.zeros((20, N), dtype=np.int32), cn[:-20]])
        del cn
        with np.errstate(invalid="ignore", divide="ignore"):
            self.dv20 = np.where(n20 >= 15, s20 / np.maximum(n20, 1), np.nan)
        del s20, n20
        prev = np.full((T, N), np.nan)
        last = np.full(N, np.nan)
        for i in range(T):
            prev[i] = last
            last = np.where(present[i], self.c[i], last)
        with np.errstate(invalid="ignore", divide="ignore"):
            bad = (np.abs(self.o / prev - 1) > GUARD) | (np.abs(self.c / prev - 1) > GUARD)
        del prev
        self.badcum = np.cumsum(bad, axis=0, dtype=np.int32)
        del bad
        self._bench: dict = {}

    def fwd(self, j, e: int, h: int):
        x = e + h - 1
        if x >= len(self.cal) or e < 1:
            return np.nan if np.isscalar(j) else np.full(len(j), np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            r = (self.c[x, j] / self.o[e, j] - 1.0) * 100.0
        badw = (self.badcum[x, j] - self.badcum[e - 1, j]) > 0
        return np.where(badw, np.nan, r)

    def eligible(self, s: int, e: int):
        c = self.c[s]
        with np.errstate(invalid="ignore"):
            return (c >= PX_MIN) & (self.dv20[s] >= DVOL_MIN) & (self.hist[s] >= MIN_HIST) & (self.o[e] > 0)

    def bench(self, s: int, e: int, h: int):
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
        k = int(np.searchsorted(edges, dv, side="right")) if dv == dv else 0
        return means[k]


def nw_t(y: list[float], lag: int) -> tuple[float, float, int]:
    a = np.array([v for v in y if v == v], float)
    n = len(a)
    if n < 3:
        return (float(a.mean()) if n else float("nan"), float("nan"), n)
    m = a.mean()
    u = a - m
    v = (u @ u) / n
    for l in range(1, min(lag, n - 1) + 1):
        v += 2 * (1 - l / (lag + 1)) * (u[l:] @ u[:-l]) / n
    se = math.sqrt(max(v, 1e-12) / n)
    return float(m), float(m / se), n


def date_series(rows: list[dict], key: str) -> list[float]:
    by = defaultdict(list)
    for r in rows:
        v = r.get(key)
        if v is not None and v == v:
            by[r["entry"]].append(v)
    return [float(np.mean(by[d])) for d in sorted(by)]


def load_book() -> Book:
    bars = {}
    for fp in PX.glob("*.json"):
        b = [tuple(x) for x in json.loads(fp.read_text(encoding="utf-8")) if x[0] >= "2014-06-01"]
        if len(b) > 30:
            bars[fp.stem] = b
    cal = [r[0] for r in bars["069500"]]
    return Book(bars, cal)


def summarize(rows: list[dict], key: str) -> dict:
    d = {}
    for part, rr in (("ALL", rows), ("IS", [e for e in rows if e["entry"] <= IS_END]),
                     ("OOS", [e for e in rows if e["entry"] > IS_END]),
                     ("ALL_ex2024", [e for e in rows if e["entry"][:4] != "2024"]),
                     ("OOS_ex2024", [e for e in rows if e["entry"] > IS_END and e["entry"][:4] != "2024"])):
        m, t, n = nw_t(date_series(rr, key), H - 1)
        d[part] = {"mean": round(m, 3) if m == m else None, "t": round(t, 2) if t == t else None, "dates": n,
                   "events": sum(1 for e in rr if e.get(key) is not None)}
    yrs = {}
    for y in range(2015, 2027):
        rr = [e for e in rows if e["entry"][:4] == str(y)]
        m, t, n = nw_t(date_series(rr, key), H - 1)
        yrs[str(y)] = {"mean": round(m, 2) if m == m else None, "dates": n,
                       "events": sum(1 for e in rr if e.get(key) is not None)}
    d["years"] = yrs
    d["oos_years_pos"] = sum(1 for y in ("2023", "2024", "2025", "2026") if (yrs[y]["mean"] or 0) > 0 and yrs[y]["dates"] > 0)
    d["oos_years_n"] = sum(1 for y in ("2023", "2024", "2025", "2026") if yrs[y]["dates"] > 0)
    a = d["ALL"]
    t_ok = (a["t"] or 0) >= 2.64
    yr_ok = d["oos_years_pos"] * 2 > d["oos_years_n"] and d["oos_years_n"] > 0
    ex_ok = a["mean"] is not None and d["ALL_ex2024"]["mean"] is not None and (a["mean"] > 0) == (d["ALL_ex2024"]["mean"] > 0)
    d["checks"] = {"t_all_ge_2.64": t_ok, "oos_year_sign_majority": yr_ok, "ex2024_same_sign": ex_ok,
                   "t_oos_ge_2.64(참고)": (d["OOS"]["t"] or 0) >= 2.64, "is_same_sign(참고)":
                       (d["IS"]["mean"] or 0) > 0 and (d["OOS"]["mean"] or 0) > 0}
    d["pass"] = bool(t_ok and yr_ok and ex_ok and (a["mean"] or 0) > 0)
    return d


def cmd_test() -> None:
    ev, cnt = build_events()
    bk = load_book()
    cal = bk.cal
    k2 = bk.col["069500"]
    print(f"[test] events={len(ev)} tickers={len(bk.tk)} cal={cal[0]}~{cal[-1]}", flush=True)
    recs, drop = [], Counter()
    seen = set()
    for e in ev:
        if e["rcept_dt"] < "20150101" or e.get("dps") is None or e.get("dup_of"):
            continue
        if e.get("div_type") and "현금" not in e["div_type"]:
            drop["not_cash"] += 1
            continue
        d = f"{e['rcept_dt'][:4]}-{e['rcept_dt'][4:6]}-{e['rcept_dt'][6:]}"
        s = bisect.bisect_right(cal, d) - 1
        yr = e["rcept_dt"][:4]
        if s < 25 or s + 1 >= len(cal):
            drop[("nocal", yr)] += 1
            continue
        key = (e["tk"], s)
        if key in seen:
            drop["same_tk_day"] += 1
            continue
        seen.add(key)
        j = bk.col.get(e["tk"])
        if j is None or np.isnan(bk.c[s, j]):
            drop[("nopx", yr)] += 1
            continue
        ee = s + 1
        r = dict(e)
        r["entry"] = cal[ee]
        r["elig"] = bool(bk.eligible(s, ee)[j])
        dv = float(bk.dv20[s, j]) if bk.dv20[s, j] == bk.dv20[s, j] else float("nan")
        r["dv20"] = dv
        x = bk.fwd(j, ee, H)
        if x == x:
            x = float(x)
            sb = bk.size_bench(s, ee, H, dv)
            ki = bk.fwd(k2, ee, H)
            r["r20"] = x
            r["ex20"] = x - COST - sb if sb == sb else None
            r["xk20"] = x - COST - float(ki) if ki == ki else None
            r["net20"] = x - COST
        else:
            drop[("immature_or_guard", yr)] += 1
        r["main"] = bool(r.get("dps_yoy") is not None and r["dps_yoy"] >= 0.20 and (r.get("yld") or 0) >= 2.0)
        recs.append(r)
    # 기저(전체 배당결정) 대비: 같은 진입일 전체 배당 이벤트 평균 수익
    base_by = defaultdict(list)
    for r in recs:
        if r.get("r20") is not None and r["elig"]:
            base_by[r["entry"]].append(r["r20"])
    for r in recs:
        b = base_by.get(r["entry"])
        if r.get("r20") is not None and b and (len(b) > 1 or not r["elig"]):
            # 자기 자신 제외(적격 이벤트는 b 안에 자신이 있다) — 같은 날 다른 배당 이벤트가 없으면 결측
            others = (sum(b) - r["r20"]) / (len(b) - 1) if r["elig"] else float(np.mean(b))
            r["xb20"] = r["r20"] - others
    with (OUT / "test_events.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        cols = ["rcept_no", "rcept_dt", "tk", "name", "div_kind", "dps", "prior_dps", "prior_src", "dps_yoy", "yld",
                "entry", "elig", "dv20", "r20", "net20", "ex20", "xk20", "xb20", "main"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(recs)
    pops = {
        "MAIN_elig": [r for r in recs if r["main"] and r["elig"]],
        "MAIN_all": [r for r in recs if r["main"]],
        "BASE_all_div_elig": [r for r in recs if r["elig"]],
        "BASE_all_div_all": recs,
    }
    res = {"drop": {"|".join(map(str, k)) if isinstance(k, tuple) else k: v for k, v in sorted(drop.items(), key=str)},
           "n_recs": len(recs), "pops": {}}
    for nm, rows in pops.items():
        res["pops"][nm] = {"n": len(rows), "size": summarize(rows, "ex20"), "kospi200": summarize(rows, "xk20"),
                           "net": summarize(rows, "net20")}
        if nm.startswith("MAIN"):
            res["pops"][nm]["vs_base"] = summarize(rows, "xb20")
            vals = sorted(r["ex20"] for r in rows if r.get("ex20") is not None)
            if vals:
                res["pops"][nm]["dist_ex20"] = {"median": round(vals[len(vals) // 2], 2),
                                                "hit": round(sum(1 for v in vals if v > 0) / len(vals), 3),
                                                "p05": round(vals[len(vals) // 20], 2), "p95": round(vals[-len(vals) // 20 - 1], 2)}
                m = Counter(r["entry"][5:7] for r in rows)
                res["pops"][nm]["entry_month"] = dict(sorted(m.items()))
                res["pops"][nm]["per_year_events"] = dict(sorted(Counter(r["entry"][:4] for r in rows).items()))
        s = res["pops"][nm]["size"]
        print(f"{nm:20s} n={len(rows):5d} ALL={s['ALL']} IS={s['IS']} OOS={s['OOS']} ex24={s['ALL_ex2024']} "
              f"oos+={s['oos_years_pos']}/{s['oos_years_n']} pass={s['pass']}", flush=True)
    # 주 행 조건 결측 진단
    base = [r for r in recs]
    res["main_gate_diag"] = {
        "with_prior": sum(1 for r in base if r.get("dps_yoy") is not None),
        "no_prior": sum(1 for r in base if r.get("dps_yoy") is None),
        "yld_missing": sum(1 for r in base if r.get("yld") is None),
        "yoy_ge20": sum(1 for r in base if (r.get("dps_yoy") or -9) >= 0.2),
        "yld_ge2": sum(1 for r in base if (r.get("yld") or 0) >= 2.0),
        "main": sum(1 for r in base if r["main"]),
        "main_by_prior_src": dict(Counter(r.get("prior_src") for r in base if r["main"])),
        "no_prior_by_year": dict(sorted(Counter(r["rcept_dt"][:4] for r in base if r.get("dps_yoy") is None).items())),
        "yld_missing_by_year": dict(sorted(Counter(r["rcept_dt"][:4] for r in base if r.get("yld") is None).items())),
    }
    # 보조(판정 밖): 2026 이후 분리과세 요건 근사 — 배당성향 데이터 없음 → 산출 불가 표기
    res["aux_tax_split"] = "산출 불가: 배당성향(순이익) 미수집 — 판정 밖 보조 항목"
    (OUT / "results.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res["main_gate_diag"], ensure_ascii=False), flush=True)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    OUT.mkdir(parents=True, exist_ok=True)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd in ("list", "docs", "alot", "collect") and not KEY:
        print("DART_API_KEY 없음", flush=True)
        return
    if cmd == "list":
        try:
            cmd_list()
        except Budget:
            pass
    elif cmd == "docs":
        cmd_docs()
    elif cmd == "collect":      # list → docs → parse → alot 순서
        try:
            cmd_list()
        except Budget:
            cmd_parse()
            return
        cmd_docs()
        cmd_parse()
        cmd_alot()
        cmd_parse()
    elif cmd == "docs_web":
        cmd_docs_web()
    elif cmd == "alot":
        cmd_alot()
    elif cmd == "parse":
        cmd_parse()
    elif cmd == "prices":
        cmd_prices()
    elif cmd == "test":
        cmd_test()
    elif cmd == "calls":
        print(f"calls_today={calls_today()} cap={DAILY_CAP} blocked={blocked_now()}")
    else:
        print(__doc__)
    flush_counter()


if __name__ == "__main__":
    main()
