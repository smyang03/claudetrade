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

from runtime_paths import get_runtime_path

# ── 가격 설정 ─────────────────────────────────────────────────────────────────
PRICE_INPUT_PER_M  = 3.00   # $ per million input tokens
PRICE_OUTPUT_PER_M = 15.00  # $ per million output tokens

USAGE_PATH = get_runtime_path("state", "api_usage.json")
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


# ── 퍼블릭 API ─────────────────────────────────────────────────────────────────

def record(input_tokens: int, output_tokens: int, label: str = ""):
    """API 호출 결과를 기록 (analysts.py 등에서 호출)"""
    cost = _calc_cost(input_tokens, output_tokens)
    today = date.today().isoformat()

    data = _load()

    # 누적
    data["total"]["input_tokens"]  += input_tokens
    data["total"]["output_tokens"] += output_tokens
    data["total"]["cost_usd"]      = round(data["total"]["cost_usd"] + cost, 6)

    # 일별
    if today not in data["daily"]:
        data["daily"][today] = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0}
    data["daily"][today]["input_tokens"]  += input_tokens
    data["daily"][today]["output_tokens"] += output_tokens
    data["daily"][today]["cost_usd"]       = round(data["daily"][today]["cost_usd"] + cost, 6)
    data["daily"][today]["calls"]         += 1

    # 세션 로그 (최근 100개 유지)
    data["sessions"].append({
        "ts":            datetime.now().strftime("%H:%M:%S"),
        "date":          today,
        "label":         label,
        "input_tokens":  input_tokens,
        "output_tokens": output_tokens,
        "cost_usd":      round(cost, 6),
    })
    data["sessions"] = data["sessions"][-100:]

    _save(data)


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
        "daily_7": daily_7,
    }
