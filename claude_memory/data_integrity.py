"""
claude_memory/data_integrity.py

장 시작 전 데이터 무결성 자동 점검 및 수정 모듈.

session_open() 초입에 run_pre_session_check(market) 를 호출하면:
  - 안전하게 자동 수정 가능한 것은 즉시 수정
  - 수동 확인이 필요한 것은 WARNING 로그만 출력
  - 결과 딕셔너리 반환 (fixed / warnings)

점검 항목:
  1. [KR] 가격 CSV volume 오염 — 당일 volume ≤ 1 row 자동 제거
  2. execution_patterns — sell_failed 항목 자동 제거, last_seen 백필
  3. tuning_patterns — 특정 종목명 고착 자동 제거, last_seen 백필,
                       저정확도(< 15%) 미표시 항목 insight 업데이트
"""

from __future__ import annotations

import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

# ── 경로 설정 ──────────────────────────────────────────────────────────────────
_BASE_DIR = Path(__file__).resolve().parent.parent
_PRICE_KR_DIR = _BASE_DIR / "data" / "price" / "kr"

# ── 상수 ───────────────────────────────────────────────────────────────────────
# 시장별 티커 패턴 — KR insight에서 US 단어형 티커(BULL, OWL 등) 오인식 방지
_TICKER_RE_US = re.compile(
    r"\b("
    r"RGTI|NVDA|SOUN|EOSE|CLSK|SEDG|WULF|QBTS|IONQ|QUBT|FSLY|UPST|TEM|HOOD|"
    r"OKLO|TSLA|SNAP|BULL|OWL|DDOG|TTAN|UEC|WIX|SOFI|FRMI|BRZE|FIG|MLYS|"
    r"PATK|SEDG|RHI|AXTI|IFS|TEAM|WULF|SYM|ORCL|INTC"
    r")\b"
)
_TICKER_RE_KR = re.compile(
    r"\b("
    r"035420|027040|010170|037030|078150|011930|020180|069540|017900|215790|"
    r"049080|109070|203650|001440|047040|271050|046970|131760|038060|252670|"
    r"114800|005880|900300|032820|252710|251340|462330|233740|379800"
    r")\b"
)

def _get_ticker_re(market: str):
    if market == "US":
        return _TICKER_RE_US
    # KR: 숫자 코드만 제거 (영문 단어형 티커는 한국 텍스트에서 오인식 위험)
    return _TICKER_RE_KR

# 저정확도 기준 (rate < threshold → precision_hint 삽입)
_LOW_PRECISION_THRESHOLD = 0.15
# precision_hint 이미 삽입된 것으로 판단하는 키워드
_HINT_KEYWORDS = ("precision", "low reliability", "predictive value", "weak context")


# ── 내부 유틸 ──────────────────────────────────────────────────────────────────

def _precision_hint(rate: float, count: int, extra: str = "") -> str:
    if rate == 0.0 and count >= 10:
        msg = f"Zero precision over {count} trials; no predictive value at this window."
    elif rate < 0.10:
        msg = f"Historically low precision ({rate*100:.0f}% over {count} trials); treat as weak context only."
    else:
        msg = f"Below-average precision ({rate*100:.0f}% over {count} trials); use with caution."
    if extra:
        msg += f" {extra}"
    return msg


def _has_hint(insight: str) -> bool:
    lower = insight.lower()
    return any(kw in lower for kw in _HINT_KEYWORDS)


def _sanitize_insight(insight: str, rate: float, count: int, market: str = "KR", extra: str = "") -> tuple[str, list[str]]:
    """
    insight 에서 종목명 제거만 수행. 저정확도 hint는 precision_note에 분리 저장.
    반환: (새 insight, 변경 설명 목록)
    """
    changes: list[str] = []
    ticker_re = _get_ticker_re(market)

    # 종목명 제거
    if ticker_re.search(insight):
        insight = ticker_re.sub("[특정종목]", insight)
        insight = re.sub(r"(\[특정종목\][·,/·\s]*){2,}", "[특정종목] 등 ", insight)
        changes.append("종목명 제거")

    return insight, changes


# ── 점검 1: KR 가격 CSV volume 오염 ───────────────────────────────────────────

def _check_volume_contamination() -> tuple[list[str], list[str]]:
    """당일 volume ≤ 1 인 row를 CSV에서 자동 제거."""
    fixed: list[str] = []
    warnings: list[str] = []
    today_ts = None

    try:
        import pandas as pd
        today_ts = pd.Timestamp(date.today())
    except ImportError:
        warnings.append("[volume_check] pandas 없음 — 스킵")
        return fixed, warnings

    if not _PRICE_KR_DIR.exists():
        warnings.append(f"[volume_check] KR 가격 디렉터리 없음: {_PRICE_KR_DIR}")
        return fixed, warnings

    for csv_path in _PRICE_KR_DIR.glob("kr_*.csv"):
        try:
            df = pd.read_csv(csv_path, parse_dates=["date"])
            mask = df["date"] == today_ts
            if not mask.any():
                continue
            today_vol = df.loc[mask, "volume"].iloc[0]
            if today_vol <= 1:
                df_clean = df[~mask].copy()
                df_clean.to_csv(csv_path, index=False, encoding="utf-8-sig")
                fixed.append(f"[volume] {csv_path.stem}: volume={today_vol:.0f} 제거")
        except Exception as e:
            warnings.append(f"[volume_check] {csv_path.stem}: {e}")

    return fixed, warnings


# ── 점검 2: execution_patterns ─────────────────────────────────────────────────

def _check_execution_patterns(market: str, brain: dict) -> tuple[list[str], list[str]]:
    fixed: list[str] = []
    warnings: list[str] = []
    today_str = date.today().isoformat()

    m = brain["markets"].get(market, {})
    patterns: dict = m.get("execution_patterns", {})
    keys_to_delete = []

    for k, v in patterns.items():
        # sell_failed 항목 자동 제거
        if "sell_failed" in k:
            keys_to_delete.append(k)
            fixed.append(f"[exec/{market}] sell_failed 제거: {k} (count={v.get('count',0)})")
            continue

        # last_seen 백필
        if "last_seen" not in v:
            examples = v.get("examples", [])
            last_date = next((ex.get("date","") for ex in reversed(examples) if ex.get("date","")), today_str)
            v["last_seen"] = last_date
            fixed.append(f"[exec/{market}] last_seen 백필: {k} → {last_date}")

    for k in keys_to_delete:
        del patterns[k]

    return fixed, warnings


# ── 점검 3: tuning_patterns ────────────────────────────────────────────────────

def _check_tuning_patterns(market: str, brain: dict) -> tuple[list[str], list[str]]:
    fixed: list[str] = []
    warnings: list[str] = []
    today_str = date.today().isoformat()

    m = brain["markets"].get(market, {})
    tp: dict = m.get("tuning_patterns", {})

    # KR 30min_tune에 volume 이슈 원인 주석 추가
    _kr_30min_extra = (
        "Note: low rate may partly reflect prior volume data quality issues (corrected)."
        if market == "KR" else ""
    )

    for k, v in tp.items():
        cnt = v.get("count", 0)
        rate = v.get("rate", 0.0)
        insight = v.get("insight", "")

        # last_seen 백필
        if "last_seen" not in v:
            v["last_seen"] = today_str
            fixed.append(f"[tune/{market}] last_seen 백필: {k} → {today_str}")

        if cnt == 0:
            continue

        extra = _kr_30min_extra if (market == "KR" and k == "30min_tune") else ""
        new_insight, changes = _sanitize_insight(insight, rate, cnt, market, extra)

        if changes:
            v["insight"] = new_insight
            for c in changes:
                fixed.append(f"[tune/{market}] {k}: {c}")

        # 저정확도 hint는 precision_note에 분리 저장 (insight 직접 수정 없음)
        if cnt > 0 and rate < _LOW_PRECISION_THRESHOLD and not v.get("precision_note"):
            v["precision_note"] = _precision_hint(rate, cnt, extra)
            fixed.append(f"[tune/{market}] {k}: precision_note 추가(rate={rate*100:.0f}%)")

    return fixed, warnings


# ── 공개 진입점 ────────────────────────────────────────────────────────────────

def run_pre_session_check(market: str) -> dict:
    """
    장 시작 전 데이터 무결성 점검. session_open() 초입에 호출.

    반환:
      {
        "fixed":    ["수정 내역 ..."],
        "warnings": ["경고 내역 ..."],
      }
    """
    from claude_memory.brain import load, save  # 지연 임포트 (순환 방지)

    all_fixed: list[str] = []
    all_warnings: list[str] = []

    # 1. KR volume 오염 점검 (KR 장 전에만 의미 있음)
    if market == "KR":
        f, w = _check_volume_contamination()
        all_fixed.extend(f)
        all_warnings.extend(w)

    # 2~3. brain.json 점검 (KR/US 공통)
    brain = load()
    f, w = _check_execution_patterns(market, brain)
    all_fixed.extend(f)
    all_warnings.extend(w)

    f, w = _check_tuning_patterns(market, brain)
    all_fixed.extend(f)
    all_warnings.extend(w)

    if all_fixed or all_warnings:
        save(brain)

    return {"fixed": all_fixed, "warnings": all_warnings}
