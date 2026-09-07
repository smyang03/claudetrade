# -*- coding: utf-8 -*-
"""KR 공시 이벤트 레인 v1 — 실시간 DART 감시 → 분류·본문 파싱 → 유령 체결 (SHADOW, 2026-09-04).

운영자 결정(09-03 밤): "빠른 게임은 KR 공시·촉매(소액+한국어 비정형+LLM), 느린 게임은 US".
이 모듈은 **주문 권한이 없다**(AUTHORITY = SHADOW). 실주문은 별도 canary 계약·운영자 ACK 후.

계약 v1 (사전등록 가설 — 검증 전 숫자, 100개 신호 shadow 후 재평가):
- 대상: 단일판매·공급계약체결(계약액/최근매출 ≥ 30%, 비관계사, 정정 아님), 무상증자결정(1주당 0.5주 이상).
  자기주식취득·최대주주변경·잠정실적·유상증자는 v1에서 관측만(observe) — 진입 없음.
- 제외: 정정/기재정정, 관계사, 감지 시점 전일 종가 대비 +8% 초과, 가격 1,000원 미만, 20일 평균 거래대금 20억 미만.
- 진입: 감지 시점 현재가 + 0.3% 슬리피지, 포지션 25만원 정수주. 동시 3개, 하루 최대 6건.
- 출구: +8% 익절 / −4% 손절 / 30분 시점 +2% 미만이면 청산 / 15:20 전량 청산. 오버나이트 금지.
- 원장: data/shadow/kr_event_signals.jsonl(본 공시 전부, 분류·판단·지연시각), kr_event_phantom.jsonl(OPEN/CLOSE).
- 시세: 네이버 폴링(tools/analysis_quotes) — KIS 호가 부하를 피한다. 체결·스프레드 미반영(canary가 보정).
- 지연 계측: ts_detected(우리가 본 시각)·ts_classified·ts_decided. DART 접수시각은 list API에 없어 v1은
  감지 시각을 이벤트 시각으로 쓴다(KIND 연동은 후속). 이 한계는 원장 필드에 명시.
"""
from __future__ import annotations

import html as _html
import io
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
_MID = chr(0x318D)  # DART 원문의 가운뎃점(호환 자모) — 리터럴 금지(mojibake 검사)
KST = timezone(timedelta(hours=9))
AUTHORITY = "SHADOW_ONLY_NO_ORDER_AUTHORITY"

SIGNAL_LEDGER = ROOT / "data" / "shadow" / "kr_event_signals.jsonl"
PHANTOM_LEDGER = ROOT / "data" / "shadow" / "kr_event_phantom.jsonl"
STATE_PATH = ROOT / "state" / "kr_event_lane_state.json"
KR_PRICE_DIR = ROOT / "data" / "price" / "kr"
# 3시점 관측 원장(09-06, Codex 리뷰 2차 연구 설계): 감지·본문 확보·판단 완료 시점 가격 + 5분/30분/15:20/종가 결과, 탈락 공시 포함
OBS_LEDGER = ROOT / "data" / "shadow" / "kr_event_observations.jsonl"
DOC_DIR = ROOT / "data" / "shadow" / "kr_event_docs"      # 본문 원문 보관 → 나중에 같은 표본으로 규칙만 vs 규칙+LLM 재현
OBS_KINDS = ("supply_contract", "bonus_issue", "buyback", "share_cancellation", "stock_split", "prelim_earnings")
OBS_HORIZONS_MIN = (5, 30)

CONTRACT = {
    "version": "kr_event_v1",
    "supply_ratio_min_pct": 30.0,
    "bonus_ratio_min": 0.5,
    "max_runup_pct": 8.0,
    "min_price": 1000.0,
    "min_dvol20_krw": 2_000_000_000,
    "order_krw": 250_000,
    "slippage_pct": 0.3,
    "max_open": 3,
    "max_new_per_day": 6,
    "tp_pct": 8.0,
    "sl_pct": -4.0,
    "time_stop_min": 30,
    "time_stop_min_gain_pct": 2.0,
    "eod_exit_hhmm": "15:20",
    "entry_cutoff_hhmm": "15:10",   # 이후 감지분은 진입 안 함(EOD 청산 10분 전, 09-06 Codex 지적)
    "fee_rt_pct": 0.21,
}

KINDS = {
    "supply_contract": ("단일판매" + _MID + "공급계약체결", "단일판매·공급계약체결", "단일판매 공급계약체결", "공급계약체결"),
    "bonus_issue": ("무상증자결정", "무상증자 결정"),
    "buyback": ("자기주식취득결정", "자기주식 취득 결정", "자기주식취득 결정"),
    "major_holder_change": ("최대주주변경",),
    "prelim_earnings": ("영업(잠정)실적", "잠정실적", "매출액또는손익구조"),
    "rights_offering": ("유상증자결정", "유상증자 결정"),
    # 09-06 코퍼레이트 액션 확대(관측만): 소각은 취득보다 강한 신호 가설, 액면분할은 유동성 이벤트
    "share_cancellation": ("주식소각결정", "주식소각 결정", "자기주식소각"),
    "stock_split": ("주식분할결정", "주식분할 결정", "액면분할"),
}
ENTER_KINDS = ("supply_contract", "bonus_issue", "prelim_earnings")   # 09-08 N6: 잠정실적 흑자전환(파서 조건 통과 시만 ENTER)
# NXT 시간외 단계(09-06 프로브: KIS inquire-price NX/UN 지원, NX 가격이 KRX 종가와 다르게 움직임 — 마감 후 공시를 당일 저녁에 대응)
# 정규장 포지션은 15:41에 강제 청산하고 NXT 단계는 별도 venue로 시작한다. 이월 없음.
AFTER_HOURS = {"start_hhmm": "15:41", "end_hhmm": "20:01", "entry_cutoff_hhmm": "19:40", "eod_exit_hhmm": "19:55",
               "min_nx_volume": 1,             # NX 누적 거래량 0이면 체결 가정 불가 → SKIP no_nx_volume
               "max_last_trade_age_min": 10}   # 마지막 체결이 10분보다 오래되면 SKIP nx_stale (체결 시각을 못 받으면 통과하되 기록)
# 공식 NXT 애프터마켓은 15:40~20:00(주문 접수와 체결 시간 구분). 러너는 15:41부터 NX 시세로 전환한다.
CONTRACT_NXT = {**CONTRACT, "version": "kr_event_v1_nxt", "entry_cutoff_hhmm": AFTER_HOURS["entry_cutoff_hhmm"],
                "eod_exit_hhmm": AFTER_HOURS["eod_exit_hhmm"]}
# 장전 단계(2026-09-08, 신규 전략 P9): 07:30~08:59 DART 공시를 감지·본문·판단까지 하고, 진입은 09:00 시가(첫 실시간 호가)로 유예한다.
# 장전 네이버 시세는 전일 종가라 decide()의 급등 게이트가 눈을 감는다 → 유예 진입 시점(09:00:30~)에 시가/전일종가를 다시 재서
# max_runup_pct 초과면 SKIP, max_open·max_new_per_day도 그때 재확인한다(advisor 지적). 코드 상수(env 아님).
PREOPEN = {"start_hhmm": "07:30", "open_hhmm": "09:00", "fill_from_hhmmss": "09:00:30", "fill_until_hhmm": "09:20"}
CONTRACT_PREOPEN = {**CONTRACT, "version": "kr_event_v1_preopen"}
PREOPEN_FILL_LEDGER = ROOT / "data" / "shadow" / "kr_event_preopen_fills.jsonl"
# 2단계 판단(09-08): 본문 대기 중 빠른 유령. 기본 OFF — 기존 판단 경로를 한 세션 관찰한 뒤 켠다(코드 상수, env 아님).
FAST_ENABLED = False               # 강제 ON 스위치(테스트용)
FAST_ENABLED_FROM = "2026-09-09"   # 운영자 결정(09-08 "내일"): 이 날짜부터 자동 ON — 러너가 매일 새로 뜨므로 코드 변경 없이 09-09 07:25부터 적용


def fast_active(now: "datetime | None" = None) -> bool:
    d = (now or now_kst()).date().isoformat()
    return bool(FAST_ENABLED or d >= FAST_ENABLED_FROM)
CONTRACT_FAST = {**CONTRACT, "version": "kr_event_v1_fast", "max_new_per_day": 3}
FAST_LEDGER = ROOT / "data" / "shadow" / "kr_event_fast.jsonl"


def fast_precheck(kind: str, is_correction: bool, quote: dict | None, liq: dict, *, now: datetime | None = None,
                  contract: dict = CONTRACT_FAST, fast_today: int = 0) -> tuple[bool, str]:
    """본문 없이 판단 가능한 조건만: 대상 종류·비정정·진입 마감 전·시세·유동성·급등·일일 fast 한도."""
    if not fast_active(now):
        return False, "fast_disabled"
    if is_correction or kind not in ("supply_contract", "bonus_issue"):
        return False, "not_fast_kind"
    if now is not None and now.strftime("%H:%M") >= contract.get("entry_cutoff_hhmm", "15:10"):
        return False, "after_entry_cutoff"
    if fast_today >= int(contract["max_new_per_day"]):
        return False, "fast_daily_cap"
    if not quote or not quote.get("price"):
        return False, "no_quote"
    px = float(quote["price"])
    if px < contract["min_price"]:
        return False, "price_lt_min"
    pc = liq.get("prev_close")
    if pc and (px / pc - 1.0) * 100.0 > contract["max_runup_pct"]:
        return False, "runup"
    dv = liq.get("dvol20_krw")
    if dv is None or dv < contract["min_dvol20_krw"]:
        return False, "liquidity"
    return True, "fast_ok"


def phase_of(now: "datetime") -> str:
    """PREOPEN(07:30~08:59) / KRX(09:00~15:40, 러너는 08:50부터 폴링) / NXT(15:41~20:00) / END. 주말은 END."""
    if now.weekday() >= 5:
        return "END"
    hhmm = now.strftime("%H:%M")
    if PREOPEN["start_hhmm"] <= hhmm < PREOPEN["open_hhmm"]:
        return "PREOPEN"
    if hhmm < AFTER_HOURS["start_hhmm"]:
        return "KRX"
    if hhmm < AFTER_HOURS["end_hhmm"]:
        return "NXT"
    return "END"


def eod_key(venue: str) -> str:
    """관측 원장 EOD 칸 이름: KRX 15:20 / NXT 19:55."""
    return "px_1520" if venue != "NXT" else "px_1955"
# 본문 지연 재시도 (09-04 실측: 감지 직후 document.xml 빈 응답 → 4건 ratio_missing 오판. 같은 문서가 수십 분 뒤엔 정상)
DOC_RETRY_GAP_SEC = 60      # 재시도 최소 간격
DOC_RETRY_MAX_SEC = 900     # 최초 감지 후 이 시간까지 재시도, 넘기면 SKIP doc_unavailable_after_retry 확정
# 종류별 출구 (09-03 DART 12개월 재생: 공급계약은 당일 반응이 전부 → 30분 게임 / 무상증자는 다음날 시가로도
# 5일 +7.4%·20일 +12.4% 드리프트 → 장중엔 EOD까지 들고, 다음날부터는 일봉 arm(kr_bonus_issue)이 이어받는다)
KIND_EXIT = {
    "supply_contract": {"tp_pct": 8.0, "sl_pct": -4.0, "time_stop_min": 30, "time_stop_min_gain_pct": 2.0},
    "bonus_issue": {"tp_pct": 15.0, "sl_pct": -7.0, "time_stop_min": None, "time_stop_min_gain_pct": None},
    "prelim_earnings": {"tp_pct": 8.0, "sl_pct": -4.0, "time_stop_min": None, "time_stop_min_gain_pct": None},   # N6: EOD까지(시간정지 없음)
}
_LOCK = threading.Lock()


# ── 시각 ─────────────────────────────────────────────────────────────────────
def now_kst() -> datetime:
    return datetime.now(KST)


def _iso(dt: datetime | None = None) -> str:
    return (dt or now_kst()).isoformat(timespec="seconds")


# ── 분류 ─────────────────────────────────────────────────────────────────────
def classify_title(report_name: str) -> tuple[str, bool]:
    """(kind, is_correction). kind는 KINDS 키 또는 'other'."""
    nm = str(report_name or "").strip()
    corr = ("정정" in nm) or ("[기재정정]" in nm) or ("[정정]" in nm)
    for kind, pats in KINDS.items():
        if any(p in nm for p in pats):
            return kind, corr
    return "other", corr


# ── DART ─────────────────────────────────────────────────────────────────────
def _dart_key() -> str:
    return str(os.getenv("DART_API_KEY", "") or "").strip()


def dart_list_today(session_date: str, *, types: tuple[str, ...] = ("I", "B"), timeout: float = 10.0,
                    opener: Callable[[str, float], bytes] | None = None) -> list[dict]:
    """오늘 공시 목록(최신순). 실패는 빈 리스트(호출자가 재시도)."""
    key = _dart_key()
    if not key:
        return []
    ymd = session_date.replace("-", "")
    out: list[dict] = []
    for ty in types:
        q = urllib.parse.urlencode({"crtfc_key": key, "bgn_de": ymd, "end_de": ymd, "pblntf_ty": ty,
                                    "page_no": 1, "page_count": 100})
        url = f"https://opendart.fss.or.kr/api/list.json?{q}"
        try:
            raw = opener(url, timeout) if opener else urllib.request.urlopen(url, timeout=timeout).read()
            d = json.loads(raw)
        except Exception:
            continue
        if d.get("status") not in ("000", "013"):  # 013 = 조회 결과 없음
            continue
        for r in d.get("list", []) or []:
            out.append({"rcept_no": r.get("rcept_no"), "corp_code": r.get("corp_code"), "corp_name": r.get("corp_name"),
                        "stock_code": (r.get("stock_code") or "").strip(), "report_nm": (r.get("report_nm") or "").strip(),
                        "rcept_dt": r.get("rcept_dt"), "ty": ty})
    return out


def dart_document_text(rcept_no: str, *, timeout: float = 20.0, opener: Callable[[str, float], bytes] | None = None,
                       max_chars: int = 6000) -> str:
    """OpenAPI document.xml(zip) → 태그 제거 텍스트. 실패는 빈 문자열."""
    key = _dart_key()
    if not key:
        return ""
    url = f"https://opendart.fss.or.kr/api/document.xml?crtfc_key={key}&rcept_no={rcept_no}"
    try:
        raw = opener(url, timeout) if opener else urllib.request.urlopen(url, timeout=timeout).read()
        z = zipfile.ZipFile(io.BytesIO(raw))
        x = z.read(z.namelist()[0]).decode("utf-8", "ignore")
    except Exception:
        return ""
    t = re.sub(r"<[^>]+>", " ", x)
    t = _html.unescape(re.sub(r"\s+", " ", t))
    return t[:max_chars]


# ── 본문 필드 추출 (정규식 정본, LLM은 판단 보조) ────────────────────────────
_NUM = r"([\d,]+(?:\.\d+)?)"


def _num(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def parse_supply_contract(text: str) -> dict[str, Any]:
    f: dict[str, Any] = {}
    m = re.search(r"계약금액\s*\(원\)\s*" + _NUM, text) or re.search(r"계약금액[^\d]{0,20}" + _NUM, text)
    f["amount_krw"] = _num(m.group(1)) if m else None
    m = re.search(r"최근\s*매출액\s*\(원\)\s*" + _NUM, text) or re.search(r"최근\s*매출액[^\d]{0,20}" + _NUM, text)
    f["recent_sales_krw"] = _num(m.group(1)) if m else None
    m = re.search(r"매출액\s*대비\s*\(%\)\s*" + _NUM, text) or re.search(r"매출액\s*대비[^\d]{0,10}" + _NUM, text)
    f["ratio_pct"] = _num(m.group(1)) if m else None
    if f["ratio_pct"] is None and f["amount_krw"] and f["recent_sales_krw"]:
        f["ratio_pct"] = round(f["amount_krw"] / f["recent_sales_krw"] * 100.0, 2)
    # 3. 계약상대(방) [이름] - 최근 매출액(원) … - 주요사업 … - 회사와의 관계 … - 회사와 최근 3년간 동종계약 이행여부 … 4.
    # 09-06 수리: 상대방은 다음 소항목 앞에서 끊고, 관계는 부정 표현(관계없음/해당없음/-)과 결측(항목 없음)을 가른다.
    m = re.search(r"계약상대(?:방)?\s*(.+?)\s*-\s*(?:최근\s*매출액|주요\s*사업|회사와의\s*관계|4\.)", text)
    f["counterparty"] = m.group(1).strip(" -") or None if m else None
    m = re.search(r"회사와의\s*관계\s*(.*?)\s*(?:-\s*회사와\s*최근|4\.)", text)
    f["relation"] = m.group(1).strip(" -") if m else None
    f["relation_found"] = bool(m)
    m = re.search(r"계약기간\s*시작일\s*(\d{4}-\d{2}-\d{2})\s*종료일\s*(\d{4}-\d{2}-\d{2})", text)
    f["period"] = (m.group(1), m.group(2)) if m else None
    m = re.search(r"선급금\s*유무\s*(유|무)", text)
    f["advance_payment"] = m.group(1) if m else None
    f["related_party"] = classify_relation(f["relation"], found=f["relation_found"])
    return f


_REL_NEG = ("관계없음", "관계 없음", "해당없음", "해당 없음", "해당사항없음", "해당사항 없음", "없음", "아님", "무")
_REL_POS = ("계열", "종속", "자회사", "모회사", "최대주주", "특수관계", "관계회사", "관계사", "지배", "임원", "대표이사")


def classify_relation(relation: str | None, *, found: bool) -> bool | None:
    """True=관계사, False=비관계사(항목이 있고 공란·부정 표현), None=확인 불가(항목 자체를 못 찾음).
    09-06 수리 전엔 '관계' 부분 문자열로 '관계없음'이 관계사, 결측이 비관계사로 잡혔다."""
    if not found:
        return None
    rel = re.sub(r"\s+", " ", str(relation or "")).strip(" -")
    if not rel:
        return False
    if any(rel == n or rel.endswith(n) for n in _REL_NEG):
        return False
    if any(k in rel for k in _REL_POS):
        return True
    return False


def parse_bonus_issue(text: str) -> dict[str, Any]:
    f: dict[str, Any] = {}
    m = re.search(r"1주당\s*신주배정\s*주식수[^\d]{0,30}" + _NUM, text)
    f["ratio_per_share"] = _num(m.group(1)) if m else None
    m = re.search(r"신주배정기준일\s*(\d{4}-\d{2}-\d{2})", text)
    f["record_date"] = m.group(1) if m else None
    return f


# ── LLM 판정 (선택, 실패해도 규칙만으로 진행) ────────────────────────────────
def llm_judge(kind: str, text: str, fields: dict[str, Any], *, timeout: float = 8.0) -> dict[str, Any]:
    if os.getenv("KR_EVENT_LLM_ENABLED", "true").lower() != "true" or not os.getenv("ANTHROPIC_API_KEY"):
        return {"available": False, "reason": "disabled_or_no_key"}
    try:
        import anthropic  # noqa
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""), timeout=timeout)
        model = os.getenv("KR_EVENT_LLM_MODEL", "claude-sonnet-4-6")
        prompt = (
            "너는 한국 주식 공시 심사자다. 아래 공시 본문을 읽고 JSON만 출력하라.\n"
            f"공시 종류: {kind}\n정규식 추출 필드: {json.dumps(fields, ensure_ascii=False)}\n"
            "판단 항목: related_party(계열·특수관계인 상대 여부 bool), new_business(기존 계약 연장·갱신이 아닌 신규 bool), "
            "cancellation_risk(해지·조건부·유보 등 불확실성 bool), quality('strong'|'weak'|'skip'), reason(한 문장).\n"
            "strong = 매출 대비 큰 신규 계약이고 상대가 외부이며 조건이 명확. skip = 관계사·갱신·조건부·형식적.\n\n"
            f"본문:\n{text[:4000]}"
        )
        resp = client.messages.create(model=model, max_tokens=300, messages=[{"role": "user", "content": prompt}])
        raw = "".join(getattr(b, "text", "") for b in resp.content)
        m = re.search(r"\{.*\}", raw, flags=re.S)
        d = json.loads(m.group(0)) if m else {}
        d["available"] = True
        d["model"] = model
        return d
    except Exception as exc:
        return {"available": False, "reason": str(exc)[:120]}


# ── 결정 규칙 ────────────────────────────────────────────────────────────────
def _bars_kr(ticker: str) -> list[tuple]:
    p = KR_PRICE_DIR / f"kr_{ticker}.csv"
    rows: list[tuple] = []
    if p.exists():
        import csv
        with p.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.reader(fh):
                if len(r) >= 6 and r[0][:2] == "20":
                    try:
                        rows.append((r[0], float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])))
                    except ValueError:
                        pass
    return sorted(rows)


def liquidity_snapshot(ticker: str) -> dict[str, Any]:
    b = _bars_kr(ticker)
    if len(b) < 21:
        return {"prev_close": None, "dvol20_krw": None, "bars": len(b)}
    last = b[-20:]
    return {"prev_close": b[-1][4], "dvol20_krw": sum(x[4] * x[5] for x in last) / 20.0, "bars": len(b),
            "last_bar_date": b[-1][0]}


# ── 오프라인 파서 (2026-09-08, Codex N6 잠정실적 흑자전환 · N5 공급계약 긍정 정정) — 레인 판단 경로에 연결하지 않은 순수 함수.
# 연결(장중 판단 후 호가 진입 규약)은 사전등록 §4 후속. 여기서는 본문 → 구조화 필드와 테스트만 둔다.
_MONEY = r"(-?[\d,]+(?:\.\d+)?)"


def parse_provisional_results(text: str) -> dict[str, Any]:
    """영업(잠정)실적 본문 → 당기·전년동기 매출액/영업이익(같은 단위, 같은 기간 기준의 표만).
    반환: {revenue_cur, revenue_prev_yr, op_cur, op_prev_yr, unit, basis(연결/별도/None), turnaround(bool|None), revenue_growth_pct}
    실패 필드는 None. 흑자전환 = 당기 영업이익 > 0 & 전년동기 ≤ 0. 누적(반기·연간) 표와 분기 표가 섞이면 첫 표(당해 분기)만 본다."""
    t = re.sub(r"\s+", " ", text or "")
    f: dict[str, Any] = {"revenue_cur": None, "revenue_prev_yr": None, "op_cur": None, "op_prev_yr": None, "unit": None,
                         "basis": None, "turnaround": None, "revenue_growth_pct": None}
    if not t:
        return f
    m = re.search(r"(연결|별도)", t)
    f["basis"] = m.group(1) if m else None
    m = re.search(r"\(단위\s*:\s*([^)]+)\)", t)
    f["unit"] = m.group(1).strip() if m else None

    def _row(label: str) -> tuple[float | None, float | None]:
        # 서식(DART 잠정실적 표): 열 순서 = 당해실적 · 전기실적 · 전기대비증감율 · 전년동기실적 · 전년동기대비증감율
        # → 라벨 뒤 토큰 5개 중 1번째(당해)·4번째(전년동기). 증감율 칸은 '흑자전환' 같은 한글일 수 있다.
        mm = re.search(label + r"\s+((?:\S+\s+){4}\S+)", t)
        if not mm:
            return None, None
        toks = mm.group(1).split()
        return _num(toks[0]) if re.fullmatch(r"-?[\d,]+(?:\.\d+)?", toks[0]) else None,             (_num(toks[3]) if re.fullmatch(r"-?[\d,]+(?:\.\d+)?", toks[3]) else None)

    f["revenue_cur"], f["revenue_prev_yr"] = _row(r"매출액")
    f["op_cur"], f["op_prev_yr"] = _row(r"영업\s*이익")
    if f["op_cur"] is not None and f["op_prev_yr"] is not None:
        f["turnaround"] = bool(f["op_cur"] > 0 and f["op_prev_yr"] <= 0)
    if f["revenue_cur"] and f["revenue_prev_yr"]:
        f["revenue_growth_pct"] = round((f["revenue_cur"] / f["revenue_prev_yr"] - 1.0) * 100.0, 2)
    return f


def parse_amendment_text(text: str) -> dict[str, Any]:
    """[기재정정] 공시 본문의 "정정사항 정정항목 정정전 정정후" 표 → 계약금액 전/후, 계약 종료일 전/후, 정정사유.
    09-08 실측 구조: "4. 정정사항 정정항목 정정전 정정후 5. 계약기간 시작일 : A 종료일 : B 시작일 : A 종료일 : C ...".
    금액은 "계약금액(원) X Y" 또는 "계약금액 X Y"(정정전 X, 정정후 Y). 못 찾으면 None."""
    t = re.sub(r"\s+", " ", text or "")
    out: dict[str, Any] = {"reason": None, "amount_before": None, "amount_after": None, "period_end_before": None, "period_end_after": None}
    m = re.search(r"정정\s*사유\s*(.{0,80}?)\s*\d\.\s*정정\s*사항", t)
    out["reason"] = m.group(1).strip() if m else None
    seg = t.split("정정후", 1)[1] if "정정후" in t else t
    m = re.search(r"계약\s*금액\s*(?:\(원\))?\s*" + _MONEY + r"\s+" + _MONEY, seg)
    if m:
        out["amount_before"], out["amount_after"] = _num(m.group(1)), _num(m.group(2))
    ends = re.findall(r"종료일\s*:\s*(\d{4}-\d{2}-\d{2})", seg)
    if len(ends) >= 2:
        out["period_end_before"], out["period_end_after"] = ends[0], ends[1]
    return out


def contract_amendment_diff(orig: dict[str, Any], amended: dict[str, Any]) -> dict[str, Any]:
    """공급계약 원공시 vs 정정 파싱 필드 diff → 실제 증액(positive) / 기간만 변경 / 오기·감액 분류.
    입력은 parse_supply_contract 결과(amount, ratio_pct, counterparty, period_end 등 존재하는 키만 비교)."""
    def g(d, k):
        v = d.get(k)
        return float(v) if isinstance(v, (int, float)) else v
    a0, a1 = g(orig, "amount"), g(amended, "amount")
    out = {"amount_before": a0, "amount_after": a1, "amount_delta_pct": None, "counterparty_same": None, "period_end_same": None, "kind": "unknown"}
    if isinstance(a0, float) and isinstance(a1, float) and a0 > 0:
        out["amount_delta_pct"] = round((a1 / a0 - 1.0) * 100.0, 2)
    c0, c1 = orig.get("counterparty"), amended.get("counterparty")
    out["counterparty_same"] = (c0 == c1) if (c0 and c1) else None
    p0, p1 = orig.get("period_end"), amended.get("period_end")
    out["period_end_same"] = (p0 == p1) if (p0 and p1) else None
    d = out["amount_delta_pct"]
    if d is None:
        out["kind"] = "unknown"
    elif d >= 5.0 and out["counterparty_same"] is not False and out["period_end_same"] is not False:
        out["kind"] = "positive_increase"          # N5 신호 후보: 상대·종료일 불변·금액 ≥+5%
    elif abs(d) < 1.0 and out["period_end_same"] is False:
        out["kind"] = "period_only"
    elif d <= -5.0:
        out["kind"] = "decrease"
    else:
        out["kind"] = "minor_or_typo"
    return out


def decide(kind: str, is_correction: bool, fields: dict[str, Any], llm: dict[str, Any], quote: dict[str, Any] | None,
           liq: dict[str, Any], *, contract: dict[str, Any] = CONTRACT, open_n: int = 0, new_today: int = 0,
           now: datetime | None = None) -> tuple[str, str]:
    """(decision, reason). decision ∈ ENTER / OBSERVE / SKIP."""
    if is_correction:
        return "SKIP", "correction"
    if kind == "supply_contract_amend":
        d = fields.get("amount_delta_pct")
        if d is None or d < 5.0:
            return "SKIP", f"amend_delta_{d}"
    elif kind not in ENTER_KINDS:
        return "OBSERVE", f"kind_{kind}_observe_only"
    if now is not None and now.strftime("%H:%M") >= contract.get("entry_cutoff_hhmm", "15:10"):
        return "SKIP", "after_entry_cutoff"
    if kind == "supply_contract":
        r = fields.get("ratio_pct")
        if r is None:
            return "SKIP", "ratio_missing"
        if r < contract["supply_ratio_min_pct"]:
            return "SKIP", f"ratio_{r:.1f}_lt_{contract['supply_ratio_min_pct']:.0f}"
        rp = fields.get("related_party")
        if rp is True or (llm.get("available") and llm.get("related_party") is True):
            return "SKIP", "related_party"
        if rp is None and not (llm.get("available") and llm.get("related_party") is False):
            return "SKIP", "relation_unknown"  # 관계 항목을 못 찾음 — LLM이 명시적으로 비관계사라 하지 않으면 통과 금지
        if llm.get("available") and llm.get("quality") == "skip":
            return "SKIP", f"llm_skip:{str(llm.get('reason', ''))[:60]}"
    if kind == "bonus_issue":
        r = fields.get("ratio_per_share")
        if r is None or r < contract["bonus_ratio_min"]:
            return "SKIP", f"bonus_ratio_{r}"
    if kind == "prelim_earnings":   # N6 — 영업이익 부호 전환(당기>0 & 전년동기≤0) & 매출 증가만 ENTER, 파싱 실패는 전부 SKIP
        if fields.get("turnaround") is None:
            return "SKIP", "prelim_parse_failed"
        if fields.get("turnaround") is not True:
            return "SKIP", "no_turnaround"
        g = fields.get("revenue_growth_pct")
        if g is None or g <= 0:
            return "SKIP", f"revenue_growth_{g}"
    if not quote or not quote.get("price"):
        return "SKIP", "no_quote"
    if quote.get("venue") == "NXT":
        if float(quote.get("volume") or 0) < AFTER_HOURS["min_nx_volume"]:
            return "SKIP", "no_nx_volume"
        age = quote.get("last_trade_age_min")
        if age is not None and age > AFTER_HOURS["max_last_trade_age_min"]:
            return "SKIP", f"nx_stale_{age:.0f}min"   # 누적 거래량만으로는 '지금 거래되는가'를 못 본다(Codex 3차)
    px = float(quote["price"])
    if px < contract["min_price"]:
        return "SKIP", "price_lt_min"
    pc = liq.get("prev_close")
    if pc and (px / pc - 1.0) * 100.0 > contract["max_runup_pct"]:
        return "SKIP", f"runup_{(px / pc - 1) * 100:.1f}_gt_{contract['max_runup_pct']:.0f}"
    dv = liq.get("dvol20_krw")
    if dv is None:
        return "SKIP", "no_price_cache"
    if dv < contract["min_dvol20_krw"]:
        return "SKIP", f"dvol20_{dv / 1e8:.1f}억_lt_20억"
    if open_n >= contract["max_open"]:
        return "SKIP", "slots_full"
    if new_today >= contract["max_new_per_day"]:
        return "SKIP", "daily_cap"
    return "ENTER", "rules_pass" + ("+llm_" + str(llm.get("quality")) if llm.get("available") else "")


# ── NXT 시세 (KIS inquire-price, 시장구분 NX) ─────────────────────────────────
_NX_TOKEN: dict[str, Any] = {}


def kis_quote_nx(ticker: str, *, timeout: float = 8.0) -> dict[str, Any] | None:
    """넥스트레이드 현재가. 실패는 None. 토큰은 kis_api가 캐시한다."""
    try:
        import kis_api as k
        tok = _NX_TOKEN.get("token") or k.get_access_token(market="KR")
        _NX_TOKEN["token"] = tok
        resp = k._kis_get(f"{k.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
                          headers=k._headers(tok, "FHKST01010100"),
                          params={"FID_COND_MRKT_DIV_CODE": "NX", "FID_INPUT_ISCD": str(ticker)}, timeout=timeout)
        j = resp.json()
        o = j.get("output") or {}
        px = float(o.get("stck_prpr") or 0)
        if j.get("rt_cd") != "0" or px <= 0:
            return None
        q = {"ticker": str(ticker), "price": px, "open": float(o.get("stck_oprc") or 0), "high": float(o.get("stck_hgpr") or 0),
             "low": float(o.get("stck_lwpr") or 0), "volume": float(o.get("acml_vol") or 0),
             "change_pct": float(o.get("prdy_ctrt") or 0), "source": "kis_nx", "venue": "NXT"}
        q.update(kis_last_trade_nx(ticker, tok, timeout=timeout))
        return q
    except Exception:
        _NX_TOKEN.pop("token", None)
        return None


def kis_last_trade_nx(ticker: str, token: str, *, timeout: float = 8.0) -> dict[str, Any]:
    """NX 최근 체결 시각(inquire-ccnl, FHKST01010300). 실패·미지원이면 {} — 게이트는 값이 있을 때만 작동하고, 없으면 기록만."""
    try:
        import kis_api as k
        resp = k._kis_get(f"{k.BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-ccnl",
                          headers=k._headers(token, "FHKST01010300"),
                          params={"FID_COND_MRKT_DIV_CODE": "NX", "FID_INPUT_ISCD": str(ticker)}, timeout=timeout)
        j = resp.json()
        rows = j.get("output") or []
        if j.get("rt_cd") != "0" or not rows:
            return {"last_trade_hhmmss": None, "last_trade_age_min": None}
        hh = str(rows[0].get("stck_cntg_hour") or "").zfill(6)
        now = now_kst()
        t = now.replace(hour=int(hh[:2]), minute=int(hh[2:4]), second=int(hh[4:6]), microsecond=0)
        age = (now - t).total_seconds() / 60.0
        return {"last_trade_hhmmss": hh, "last_trade_age_min": round(age, 1) if age >= 0 else None}
    except Exception:
        return {"last_trade_hhmmss": None, "last_trade_age_min": None}


# ── 원장 ─────────────────────────────────────────────────────────────────────
def save_doc_text(rcept_no: str, text: str) -> None:
    """본문 원문 보관(멱등). 실패는 조용히 — 판단 경로를 막지 않는다."""
    try:
        DOC_DIR.mkdir(parents=True, exist_ok=True)
        p = DOC_DIR / f"{rcept_no}.txt"
        if not p.exists():
            p.write_text(text, encoding="utf-8")
    except OSError:
        pass


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK, path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def load_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8")) if STATE_PATH.exists() else {}
    except (OSError, ValueError):
        return {}


def save_state(st: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, STATE_PATH)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


# ── 유령 포지션 ──────────────────────────────────────────────────────────────
def open_phantom(sig: dict[str, Any], quote: dict[str, Any], *, contract: dict[str, Any] = CONTRACT,
                 notify: Callable[[str], Any] | None = None, now: datetime | None = None) -> dict[str, Any] | None:
    px = float(quote["price"]) * (1.0 + contract["slippage_pct"] / 100.0)
    qty = int(contract["order_krw"] // px)
    if qty <= 0:
        return None
    pos = {
        "event": "OPEN", "authority": AUTHORITY, "contract": contract["version"],
        "rcept_no": sig["rcept_no"], "ticker": sig["stock_code"], "name": sig.get("corp_name"), "kind": sig["kind"],
        "session_date": sig["session_date"], "opened_at": _iso(now), "entry": round(px, 2), "qty": qty,
        "notional_krw": round(px * qty), "quote_source": quote.get("source"), "peak_pct": 0.0, "trough_pct": 0.0,
        "basis": sig.get("basis"), "venue": quote.get("venue") or "KRX",
    }
    _append(PHANTOM_LEDGER, pos)
    if notify:
        try:
            notify(f"🧪 [VIRTUAL] KR 이벤트 유령 진입 {pos['ticker']} {pos.get('name') or ''} {pos['kind']} [{pos['venue']}] "
                   f"{qty}주@{px:,.0f} — {sig.get('basis', '')}")
        except Exception:
            pass
    return pos


def fill_preopen_entries(deferred: list[dict[str, Any]], quote_fn: Callable[[str], dict[str, Any] | None], *,
                         now: datetime, open_n: int, new_today: int, contract: dict[str, Any] = CONTRACT_PREOPEN,
                         notify: Callable[[str], Any] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """장전에 ENTER 판단된 공시를 09:00 시가로 진입. 반환 (opened_positions, fill_rows).
    재검사: 시가/전일종가 급등(max_runup_pct)·min_price·max_open·max_new_per_day·호가 없음 → SKIP 기록(유령 없음)."""
    opened: list[dict[str, Any]] = []; rows: list[dict[str, Any]] = []
    for d in deferred:
        it, row = d.get("item") or {}, d.get("row") or {}
        sig = {**it, **row}
        q = quote_fn(it.get("stock_code", ""))
        base = {"event": "PREOPEN_FILL", "contract": contract["version"], "rcept_no": sig.get("rcept_no"), "ticker": sig.get("stock_code"),
                "name": sig.get("corp_name"), "kind": sig.get("kind"), "session_date": sig.get("session_date"),
                "decided_at": row.get("ts_decided"), "filled_at": _iso(now), "px_prev_close": (row.get("liq") or {}).get("prev_close"),
                "px_open_quote": (q or {}).get("price"), "basis": sig.get("basis")}
        reason = None
        if not q or not q.get("price"):
            reason = "no_quote_at_open"
        else:
            px = float(q["price"]); pc = base["px_prev_close"]
            if pc and (px / float(pc) - 1.0) * 100.0 > contract["max_runup_pct"]:
                reason = f"open_runup_{(px / float(pc) - 1) * 100:.1f}_gt_{contract['max_runup_pct']:.0f}"
            elif px < contract["min_price"]:
                reason = "price_lt_min"
            elif open_n + len(opened) >= contract["max_open"]:
                reason = "max_open"
            elif new_today + len(opened) >= contract["max_new_per_day"]:
                reason = "max_new_per_day"
        if reason:
            rows.append({**base, "decision": "SKIP", "reason": reason}); _append(PREOPEN_FILL_LEDGER, rows[-1]); continue
        pos = open_phantom(sig, q, contract=contract, notify=notify, now=now)
        if pos:
            opened.append(pos); rows.append({**base, "decision": "ENTER", "entry": pos["entry"], "qty": pos["qty"]})
        else:
            rows.append({**base, "decision": "SKIP", "reason": "qty_zero"})
        _append(PREOPEN_FILL_LEDGER, rows[-1])
    return opened, rows


def _close_row(pos: dict[str, Any], px: float, reason: str, now: datetime, *, contract: dict[str, Any],
               unpriced: bool = False) -> dict[str, Any]:
    pnl = (px / pos["entry"] - 1.0) * 100.0
    net = pnl - contract["fee_rt_pct"]
    held_min = (now - datetime.fromisoformat(pos["opened_at"])).total_seconds() / 60.0
    row = {**pos, "event": "CLOSE", "closed_at": _iso(now), "exit": px, "exit_reason": reason,
           "gross_pct": round(pnl, 3), "net_pct": round(net, 3), "pnl_krw": round(pos["notional_krw"] * net / 100.0),
           "held_min": round(held_min, 1), "unpriced_exit": bool(unpriced)}
    row.pop("time_checked", None)
    _append(PHANTOM_LEDGER, row)
    return row


def evaluate_phantoms(open_positions: list[dict[str, Any]], quote_fn: Callable[[str], dict[str, Any] | None],
                      *, now: datetime | None = None, contract: dict[str, Any] = CONTRACT,
                      notify: Callable[[str], Any] | None = None,
                      force_close: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """열린 유령 평가. (남은 포지션, 청산 행) 반환. 청산 행은 원장에 기록.

    pos 딕셔너리는 사이클 간 상태(peak/trough/time_checked/last_px)를 들고 다닌다 — 러너가 state 파일에 영속화한다
    (09-06 수리: 매 사이클 OPEN 원장에서 재생성하면 30분 점검이 반복되고 MFE/MAE가 마지막 사이클 값만 남는다).
    시세가 없을 때: EOD 이후면 마지막 관측가로 청산(EOD_LASTQUOTE, unpriced_exit=true), force_close면 마지막 관측가
    또는 진입가로 강제 청산(EOD_FORCED). 오버나이트 이월은 없다."""
    now = now or now_kst()
    keep, closed = [], []
    for pos in open_positions:
        c_pos = CONTRACT_NXT if pos.get("venue") == "NXT" else contract
        eod = now.replace(hour=int(c_pos["eod_exit_hhmm"][:2]), minute=int(c_pos["eod_exit_hhmm"][3:]), second=0)
        q = quote_fn(pos["ticker"])
        if not q or not q.get("price"):
            if force_close or now >= eod:
                last = pos.get("last_px")
                if last or force_close:
                    row = _close_row(pos, float(last or pos["entry"]), "EOD_LASTQUOTE" if last else "EOD_FORCED",
                                     now, contract=contract, unpriced=True)
                    closed.append(row)
                    continue
            keep.append(pos)
            continue
        px = float(q["price"])
        pnl = (px / pos["entry"] - 1.0) * 100.0
        pos["last_px"] = px
        pos["last_quote_at"] = _iso(now)
        pos["peak_pct"] = max(pos.get("peak_pct", 0.0), pnl)
        pos["trough_pct"] = min(pos.get("trough_pct", 0.0), pnl)
        opened = datetime.fromisoformat(pos["opened_at"])
        held_min = (now - opened).total_seconds() / 60.0
        ex = {**{k: contract[k] for k in ("tp_pct", "sl_pct", "time_stop_min", "time_stop_min_gain_pct")},
              **KIND_EXIT.get(pos.get("kind", ""), {})}
        ts_min = ex.get("time_stop_min")
        reason = None
        if pnl >= ex["tp_pct"]:
            reason = "TP"
        elif pnl <= ex["sl_pct"]:
            reason = "SL"
        elif ts_min is not None and held_min >= ts_min and pnl < ex["time_stop_min_gain_pct"] and not pos.get("time_checked"):
            reason = "TIME_STOP"
        elif force_close or now >= eod:
            reason = "EOD"
        if ts_min is not None and held_min >= ts_min:
            pos["time_checked"] = True
        if reason is None:
            keep.append(pos)
            continue
        row = _close_row(pos, px, reason, now, contract=contract)
        closed.append(row)
        if notify:
            try:
                notify(f"🧪 [VIRTUAL] KR 이벤트 유령 청산 {pos['ticker']} {reason} net {row['net_pct']:+.2f}% ({held_min:.0f}분)")
            except Exception:
                pass
    return keep, closed


def open_positions_from_ledger(session_date: str) -> list[dict[str, Any]]:
    """원장 기준 미청산 유령(해당 세션). 상태 파일이 없을 때의 복구용 — 사이클 간 상태는 러너 state가 정본."""
    rows = read_jsonl(PHANTOM_LEDGER)
    opened = {r["rcept_no"]: r for r in rows if r.get("event") == "OPEN" and r.get("session_date") == session_date}
    closed = {r["rcept_no"] for r in rows if r.get("event") == "CLOSE"}
    return [dict(v) for k, v in opened.items() if k not in closed]


def finalize_orphans(today: str, *, now: datetime | None = None, contract: dict[str, Any] = CONTRACT) -> list[dict[str, Any]]:
    """이전 세션 OPEN인데 CLOSE가 없는 유령을 ORPHAN_UNPRICED로 마감(진입가, unpriced_exit=true). 계약상 오버나이트 없음이므로
    원장 정합용이며 손익 표본에서는 unpriced_exit로 제외한다."""
    now = now or now_kst()
    rows = read_jsonl(PHANTOM_LEDGER)
    closed = {r["rcept_no"] for r in rows if r.get("event") == "CLOSE"}
    out = []
    for r in rows:
        if r.get("event") == "OPEN" and r.get("session_date", "") < today and r["rcept_no"] not in closed:
            out.append(_close_row(dict(r), float(r["entry"]), "ORPHAN_UNPRICED", now, contract=contract, unpriced=True))
            closed.add(r["rcept_no"])
    return out


# ── 한 사이클 ────────────────────────────────────────────────────────────────
def basis_text(kind: str, fields: dict[str, Any], llm: dict[str, Any]) -> str:
    if kind == "supply_contract":
        amt = fields.get("amount_krw"); r = fields.get("ratio_pct")
        parts = [f"공급계약 {amt / 1e8:,.0f}억" if amt else "공급계약", f"매출대비 {r:.0f}%" if r is not None else "매출대비 ?",
                 f"상대 {fields.get('counterparty') or '?'}",
                 {True: "관계사", False: "외부"}.get(fields.get("related_party"), "관계불명")]
        if fields.get("advance_payment"):
            parts.append(f"선급금 {fields['advance_payment']}")
    elif kind == "bonus_issue":
        parts = [f"무상증자 1주당 {fields.get('ratio_per_share')}주"]
    elif kind == "prelim_earnings":
        parts = [f"잠정실적 {fields.get('basis') or ''} 영업이익 {fields.get('op_prev_yr')}→{fields.get('op_cur')}"
                 + (" 흑자전환" if fields.get("turnaround") else ""), f"매출 {fields.get('revenue_growth_pct')}%"]
    elif kind == "supply_contract" and fields.get("amount_delta_pct") is not None:
        parts = [f"공급계약 정정 {fields.get('kind')} 금액 {fields.get('amount_delta_pct'):+.1f}%", f"사유 {str(fields.get('reason') or '')[:30]}"]
    else:
        parts = [kind]
    if llm.get("available"):
        parts.append(f"LLM {llm.get('quality')}: {str(llm.get('reason', ''))[:50]}")
    return " · ".join(parts)


def _latencies(row: dict[str, Any], t0: datetime, now: datetime | None, mono_start: float) -> dict[str, float]:
    """latency_sec = 최초 감지(ts_detected)→판단 총 지연(본문 재시도 대기 + 이번 처리), proc_sec = 이번 호출의 실제 처리 시간(단조 시계).
    09-06 수리 2회: 재시도 호출 시작 기준 → 최초 감지 기준; 주입 시각을 종료 시각으로도 써서 proc_sec=0이던 결함(Codex 3차) → 단조 시계 실측."""
    proc = time.monotonic() - mono_start
    try:
        t_first = datetime.fromisoformat(str(row.get("ts_detected")))
    except (TypeError, ValueError):
        t_first = t0
    if now is None:
        latency = (now_kst() - t_first).total_seconds()
    else:
        latency = (now - t_first).total_seconds() + proc
    return {"latency_sec": round(latency, 2), "proc_sec": round(proc, 3)}


def _stamped(q: dict[str, Any] | None) -> dict[str, Any] | None:
    """시세에 실제 수신 시각(quoted_at, 실시계)을 붙인다. 3시점 관측은 이 시각으로 각 단계를 구분한다."""
    if not q:
        return None
    return {**q, "quoted_at": _iso()}


def process_disclosure(item: dict[str, Any], *, session_date: str, quote_fn: Callable[[str], dict[str, Any] | None],
                       open_n: int, new_today: int, doc_fn: Callable[[str], str] | None = None,
                       llm_fn: Callable[[str, str, dict], dict] | None = None,
                       contract: dict[str, Any] = CONTRACT, first_seen: str | None = None,
                       doc_attempts: int = 0, final: bool = False, now: datetime | None = None) -> dict[str, Any]:
    """공시 1건 → 분류·본문·판단·원장. 반환 행에 decision 포함.

    본문(document.xml)이 아직 비어 있으면(09-04 실측: 감지 직후 4건 전부 빈 본문 → ratio_missing 오판) 원장에 쓰지 않고
    decision=PENDING을 돌려준다. 러너가 DOC_RETRY_GAP_SEC 간격으로 재시도하고, DOC_RETRY_MAX_SEC를 넘기면 final=True로
    호출해 SKIP doc_unavailable로 확정 기록한다. first_seen은 최초 감지 시각(재시도 시 지연 계산 기준)."""
    t0 = now or now_kst()
    mono0 = time.monotonic()
    kind, corr = classify_title(item.get("report_nm", ""))
    row: dict[str, Any] = {"authority": AUTHORITY, "contract": contract["version"], "session_date": session_date,
                           "rcept_no": item.get("rcept_no"), "stock_code": item.get("stock_code"),
                           "corp_name": item.get("corp_name"), "report_nm": item.get("report_nm"), "kind": kind,
                           "is_correction": corr, "ts_detected": first_seen or _iso(t0),
                           "event_time_note": "DART list에 접수시각 없음 — 감지시각을 이벤트 시각으로 씀(v1)"}
    if kind == "other" or not item.get("stock_code"):
        row.update({"decision": "IGNORE", "reason": "not_target_or_no_stock", "ts_decided": _iso()})
        _append(SIGNAL_LEDGER, row)
        return row
    fields: dict[str, Any] = {}
    llm: dict[str, Any] = {"available": False}
    # 3시점 관측(09-06 Codex 3차): 감지 시점 시세는 본문 fetch 전에, 첫 시도에만, 관측 대상 종류 전부(소각·분할·자사주 포함).
    # 없으면 결측(None)으로 남긴다 — 다른 단계 가격으로 메우지 않는다.
    if kind in OBS_KINDS and not corr and doc_attempts == 0:
        row["quote_detect"] = _stamped(quote_fn(item["stock_code"]))
    if kind in ENTER_KINDS and not corr:
        text = (doc_fn or dart_document_text)(item["rcept_no"])
        row["doc_attempts"] = doc_attempts + 1
        if not text.strip():
            if not final:
                row.update({"decision": "PENDING", "reason": "doc_unavailable", "ts_decided": _iso()})
                return row  # 원장에 쓰지 않음 — 러너가 재시도
            row.update({"fields": {}, "llm": llm, "quote": None, "liq": {}, "decision": "SKIP",
                        "reason": "doc_unavailable_after_retry", "ts_decided": _iso(),
                        **_latencies(row, t0, now, mono0), "basis": basis_text(kind, {}, llm)})
            _append(SIGNAL_LEDGER, row)
            return row
        row["ts_parsed"] = _iso()
        row["quote_doc"] = _stamped(quote_fn(item["stock_code"]))   # 본문 확보 시점 시세(파싱·LLM 전)
        fields = (parse_supply_contract(text) if kind == "supply_contract"
                  else (parse_provisional_results(text) if kind == "prelim_earnings" else parse_bonus_issue(text)))
        save_doc_text(item["rcept_no"], text)
        if kind == "supply_contract" and fields.get("ratio_pct") is not None and fields["ratio_pct"] >= contract["supply_ratio_min_pct"]:
            llm = (llm_fn or llm_judge)(kind, text, fields)
            row["ts_classified"] = _iso()
    amend: dict[str, Any] | None = None
    if kind == "supply_contract" and corr:
        # N5(09-08): 정정 공시는 일괄 SKIP이었다 → 본문의 정정전/후 표를 읽어 실제 증액만 별도 계약(kr_event_v1_amend)으로 판단.
        # 금액 전/후를 못 읽으면 OBSERVE(진입 없음). 관계·매출비율 확인이 없으므로 증액 ≥5% & 상대·종료일 불변만 통과.
        text = (doc_fn or dart_document_text)(item["rcept_no"])
        row["doc_attempts"] = doc_attempts + 1
        if not text.strip() and not final:
            row.update({"decision": "PENDING", "reason": "doc_unavailable", "ts_decided": _iso()})
            return row
        am = parse_amendment_text(text) if text.strip() else {}
        diff = contract_amendment_diff({"amount": am.get("amount_before"), "period_end": am.get("period_end_before")},
                                       {"amount": am.get("amount_after"), "period_end": am.get("period_end_after")}) if am else {"kind": "unknown"}
        amend = {**am, **diff}
        fields = dict(amend)
        if text.strip():
            save_doc_text(item["rcept_no"], text)
    quote = _stamped(quote_fn(item["stock_code"])) if kind in ENTER_KINDS and (not corr or amend) else None   # 판단 시점 시세
    liq = liquidity_snapshot(item["stock_code"]) if kind in ENTER_KINDS and (not corr or amend) else {}
    if amend is not None:
        if amend.get("kind") == "positive_increase":
            c_am = {**contract, "version": "kr_event_v1_amend"}
            decision, reason = decide("supply_contract_amend", False, fields, llm, quote, liq, contract=c_am, open_n=open_n,
                                      new_today=new_today, now=t0)
            row["contract"] = c_am["version"]
        else:
            decision, reason = "OBSERVE", f"amend_{amend.get('kind', 'unknown')}"
    else:
        decision, reason = decide(kind, corr, fields, llm, quote, liq, contract=contract, open_n=open_n, new_today=new_today,
                                  now=t0)
    row.update({"fields": fields, "llm": llm, "quote": quote, "liq": {k: v for k, v in liq.items() if k != "bars"},
                "decision": decision, "reason": reason, "ts_decided": _iso(),
                **_latencies(row, t0, now, mono0), "basis": basis_text(kind, fields, llm)})
    _append(SIGNAL_LEDGER, row)
    return row
