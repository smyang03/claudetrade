"""
credit_tracker.py - Anthropic API 사용량 추적 및 비용 계산

claude-sonnet-4-6 가격 (2025년 기준):
  Input:  $3.00 / 1M tokens
  Output: $15.00 / 1M tokens

state/api_usage.json에 누적 저장
"""

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from runtime_paths import get_runtime_path

# ── 가격 설정 ─────────────────────────────────────────────────────────────────
PRICE_INPUT_PER_M  = 3.00   # $ per million input tokens
PRICE_OUTPUT_PER_M = 15.00  # $ per million output tokens
PRICE_BY_MODEL_PER_M = {
    "haiku": (
        float(os.getenv("CLAUDE_PRICE_HAIKU_INPUT_PER_M", "1.00")),   # Haiku 4.5: $1.00 (구: 0.80)
        float(os.getenv("CLAUDE_PRICE_HAIKU_OUTPUT_PER_M", "5.00")),  # Haiku 4.5: $5.00 (구: 4.00)
    ),
    "sonnet": (
        float(os.getenv("CLAUDE_PRICE_SONNET_INPUT_PER_M", str(PRICE_INPUT_PER_M))),
        float(os.getenv("CLAUDE_PRICE_SONNET_OUTPUT_PER_M", str(PRICE_OUTPUT_PER_M))),
    ),
    "opus": (
        float(os.getenv("CLAUDE_PRICE_OPUS_INPUT_PER_M", "15.00")),
        float(os.getenv("CLAUDE_PRICE_OPUS_OUTPUT_PER_M", "75.00")),
    ),
}

_IS_PAPER = str(os.getenv("KIS_IS_PAPER", "true")).strip().lower() != "false"
_MODE     = "paper" if _IS_PAPER else "live"
USAGE_PATH = get_runtime_path("state", f"{_MODE}_api_usage.json")
USAGE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _load() -> dict:
    if USAGE_PATH.exists():
        try:
            with open(USAGE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "total":    {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        "daily":    {},   # {"2026-03-21": {"input": .., "output": .., "cost_usd": ..}}
        "sessions": [],   # 세션별 상세 (최근 30개 유지)
    }


def _save(data: dict):
    with open(USAGE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _calc_cost(input_tokens: int, output_tokens: int) -> float:
    """USD 비용 계산"""
    return (input_tokens / 1_000_000 * PRICE_INPUT_PER_M
            + output_tokens / 1_000_000 * PRICE_OUTPUT_PER_M)


def _model_price(model: str) -> tuple[float, float]:
    model_l = str(model or "").lower()
    for key, price in PRICE_BY_MODEL_PER_M.items():
        if key in model_l:
            return price
    return PRICE_INPUT_PER_M, PRICE_OUTPUT_PER_M


def _float_env(name: str, default: Optional[float] = None) -> Optional[float]:
    raw = os.getenv(name, "")
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).replace(",", ""))
    except Exception:
        return default


def _add_usage(
    bucket: dict,
    input_tokens: int,
    output_tokens: int,
    cost: float,
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> None:
    bucket["input_tokens"] = int(bucket.get("input_tokens", 0) or 0) + int(input_tokens)
    bucket["output_tokens"] = int(bucket.get("output_tokens", 0) or 0) + int(output_tokens)
    bucket["cost_usd"] = round(float(bucket.get("cost_usd", 0.0) or 0.0) + float(cost), 6)
    if cache_creation_input_tokens:
        bucket["cache_creation_tokens"] = int(bucket.get("cache_creation_tokens", 0) or 0) + int(cache_creation_input_tokens)
    if cache_read_input_tokens:
        bucket["cache_read_tokens"] = int(bucket.get("cache_read_tokens", 0) or 0) + int(cache_read_input_tokens)
    if "calls" in bucket:
        bucket["calls"] = int(bucket.get("calls", 0) or 0) + 1


def _calc_cost_for_model(
    input_tokens: int,
    output_tokens: int,
    model: str = "",
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
) -> float:
    input_per_m, output_per_m = _model_price(model)
    # input_tokens = cache 미포함 일반 토큰만 (API 실측 확인)
    # cache_creation: 캐시 쓰기 = 기본 요금의 125% (1.25x)
    # cache_read:     캐시 읽기 = 기본 요금의  10% (0.10x)
    cost = input_tokens / 1_000_000 * input_per_m + output_tokens / 1_000_000 * output_per_m
    if cache_creation_input_tokens:
        cost += cache_creation_input_tokens / 1_000_000 * input_per_m * 1.25
    if cache_read_input_tokens:
        cost += cache_read_input_tokens / 1_000_000 * input_per_m * 0.10
    return max(0.0, cost)


# ── 퍼블릭 API ─────────────────────────────────────────────────────────────────

def record(
    input_tokens: int,
    output_tokens: int,
    label: str = "",
    model: str = "",
    cache_creation_input_tokens: int = 0,
    cache_read_input_tokens: int = 0,
):
    """API 호출 결과를 기록 (analysts.py 등에서 호출)"""
    model_name = str(model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6") or "unknown")
    cost = _calc_cost_for_model(
        input_tokens, output_tokens, model_name,
        cache_creation_input_tokens, cache_read_input_tokens,
    )
    today = date.today().isoformat()

    data = _load()
    data.setdefault("total", {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
    data.setdefault("daily", {})
    data.setdefault("sessions", [])

    # 누적
    _add_usage(data["total"], input_tokens, output_tokens, cost, cache_creation_input_tokens, cache_read_input_tokens)

    # 일별
    if today not in data["daily"]:
        data["daily"][today] = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0}
    _add_usage(data["daily"][today], input_tokens, output_tokens, cost, cache_creation_input_tokens, cache_read_input_tokens)
    by_model = data.setdefault("by_model", {})
    model_bucket = by_model.setdefault(model_name, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0})
    _add_usage(model_bucket, input_tokens, output_tokens, cost, cache_creation_input_tokens, cache_read_input_tokens)
    daily_by_model = data["daily"][today].setdefault("by_model", {})
    daily_model_bucket = daily_by_model.setdefault(model_name, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0})
    _add_usage(daily_model_bucket, input_tokens, output_tokens, cost, cache_creation_input_tokens, cache_read_input_tokens)

    # 세션 로그 (최근 100개 유지)
    session_entry: dict = {
        "ts":            datetime.now().strftime("%H:%M:%S"),
        "date":          today,
        "label":         label,
        "model":         model_name,
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "cost_usd":      round(cost, 6),
    }
    if cache_creation_input_tokens:
        session_entry["cache_creation_tokens"] = cache_creation_input_tokens
    if cache_read_input_tokens:
        session_entry["cache_read_tokens"] = cache_read_input_tokens
    data["sessions"].append(session_entry)
    data["sessions"] = data["sessions"][-100:]

    _save(data)


def throttle_state(*, label: str = "", hard_exit: bool = False, estimated_cost_usd: float = 0.0) -> dict:
    """Return Claude budget throttle state without mutating usage.

    Hard risk/system-exit paths can pass hard_exit=True and remain allowed.
    Optional call sites can use tier to degrade or skip work.
    """
    if str(os.getenv("CLAUDE_BUDGET_THROTTLE_ENABLED", "false")).strip().lower() not in {"1", "true", "yes", "y", "on"}:
        return {"enabled": False, "allowed": True, "tier": "off", "label": label}
    data = _load()
    today = date.today().isoformat()
    td = data.get("daily", {}).get(today, {"cost_usd": 0.0})
    today_cost = float(td.get("cost_usd", 0.0) or 0.0) + max(0.0, float(estimated_cost_usd or 0.0))
    warn = _float_env("CLAUDE_DAILY_WARN_USD")
    hard = _float_env("CLAUDE_DAILY_BUDGET_USD")
    exempt_hard_exit = str(os.getenv("CLAUDE_BUDGET_HARD_EXIT_EXEMPT", "true")).strip().lower() in {"1", "true", "yes", "y", "on"}
    if hard_exit and exempt_hard_exit:
        return {
            "enabled": True,
            "allowed": True,
            "tier": "hard_exit_exempt",
            "label": label,
            "today_cost_usd": round(today_cost, 6),
            "daily_budget_usd": hard,
        }
    if hard is not None and today_cost >= hard:
        return {
            "enabled": True,
            "allowed": False,
            "tier": "hard_cap",
            "label": label,
            "today_cost_usd": round(today_cost, 6),
            "daily_budget_usd": hard,
        }
    if warn is not None and today_cost >= warn:
        return {
            "enabled": True,
            "allowed": True,
            "tier": "warn",
            "label": label,
            "today_cost_usd": round(today_cost, 6),
            "daily_budget_usd": hard,
            "daily_warn_usd": warn,
        }
    return {
        "enabled": True,
        "allowed": True,
        "tier": "normal",
        "label": label,
        "today_cost_usd": round(today_cost, 6),
        "daily_budget_usd": hard,
        "daily_warn_usd": warn,
    }


def summary(usd_krw: float = None) -> dict:
    """
    오늘/누적 사용량 요약 반환

    반환 예시:
    {
      "today":   {"calls": 5, "input": 3000, "output": 1200, "cost_usd": 0.023, "cost_krw": 31},
      "total":   {"input": 50000, "output": 20000, "cost_usd": 0.45, "cost_krw": 607},
      "daily_7": [{"date": "2026-03-21", "cost_usd": 0.023}, ...]
    }
    """
    if usd_krw is None:
        usd_krw = float(os.getenv("USD_KRW_RATE", "1350"))

    data  = _load()
    today = date.today().isoformat()
    td    = data["daily"].get(today, {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0})
    tot   = data["total"]
    daily_budget = os.getenv("CLAUDE_DAILY_BUDGET_USD", "").strip()
    monthly_budget = os.getenv("CLAUDE_MONTHLY_BUDGET_USD", "").strip()
    try:
        daily_budget_usd = float(daily_budget) if daily_budget else None
    except Exception:
        daily_budget_usd = None
    try:
        monthly_budget_usd = float(monthly_budget) if monthly_budget else None
    except Exception:
        monthly_budget_usd = None

    # 최근 7일
    all_days  = sorted(data["daily"].items())
    daily_7   = [
        {
            "date":     d,
            "calls":    v.get("calls", 0),
            "cost_usd": v["cost_usd"],
            "cost_krw": int(v["cost_usd"] * usd_krw),
        }
        for d, v in all_days[-7:]
    ]

    return {
        "today": {
            "calls":      td.get("calls", 0),
            "input":      td["input_tokens"],
            "output":     td["output_tokens"],
            "cost_usd":   round(td["cost_usd"], 4),
            "cost_krw":   int(td["cost_usd"] * usd_krw),
        },
        "total": {
            "input":    tot["input_tokens"],
            "output":   tot["output_tokens"],
            "cost_usd": round(tot["cost_usd"], 4),
            "cost_krw": int(tot["cost_usd"] * usd_krw),
        },
        "budget": {
            "daily_budget_usd": daily_budget_usd,
            "daily_remaining_usd": round(max(daily_budget_usd - td["cost_usd"], 0.0), 4) if daily_budget_usd is not None else None,
            "monthly_budget_usd": monthly_budget_usd,
            "monthly_remaining_usd": round(max(monthly_budget_usd - tot["cost_usd"], 0.0), 4) if monthly_budget_usd is not None else None,
        },
        "daily_7": daily_7,
    }
