"""minority_report/tuner.py - intraday tuning"""
import json
import os
import sys
import time
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).parent.parent))

from claude_memory import brain as BrainDB
from credit_tracker import record as credit_record
from logger import get_minority_logger
from minority_report.claude_utils import extract_json
from minority_report.raw_call_logger import save as save_raw_call

log = get_minority_logger()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

_OVERLOAD_RETRY_SEC = 10
_OVERLOAD_MAX_SEC = 600
_VALID_MODES = {
    "AGGRESSIVE",
    "MODERATE_BULL",
    "MILD_BULL",
    "CAUTIOUS",
    "NEUTRAL",
    "MILD_BEAR",
    "CAUTIOUS_BEAR",
    "DEFENSIVE",
    "HALT",
}


def _is_overloaded_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "529" in text or "overloaded" in text or "overload" in text


def tune(market: str, elapsed_min: int, current_state: dict,
         morning_judgment: dict, brain_summary: str) -> dict:
    """
    Intraday tuning: compare current state vs. morning judgment.
    Returns: {action, mode, size_adj, sl_adj, reason, warning}
    """
    prev_mode = morning_judgment.get("consensus", {}).get("mode", "CAUTIOUS")
    prompt = f"""You are an intraday tuning analyst.
Compare the morning judgment with the current state and return JSON only.

Morning mode: {prev_mode}
Morning bull reason: {morning_judgment.get('judgments', {}).get('bull', {}).get('key_reason', '')}
{brain_summary[:300]}

Current state ({elapsed_min} minutes elapsed):
  Index move: {current_state.get('index_change', 0):+.2f}%
  Volume trend: {current_state.get('volume_trend', 'normal')}
  Positions: {json.dumps(current_state.get('positions', []), ensure_ascii=False)}
  Alerts: {current_state.get('alerts', [])}

JSON only:
{{"action":"MAINTAIN|TIGHTEN|REVERSE","mode":"adjusted mode",
  "size_adj":0,"sl_adj":0.0,"reason":"one sentence reason",
  "warning":"warning or null"}}"""

    started = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=400,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()
            result = extract_json(raw)
            if result.get("mode") not in _VALID_MODES:
                result["mode"] = prev_mode

            credit_record(
                resp.usage.input_tokens,
                resp.usage.output_tokens,
                f"tune_{elapsed_min}min",
            )
            save_raw_call(
                label=f"tune_{elapsed_min}min",
                prompt=prompt,
                raw_response=raw,
                parsed=result,
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                market=market,
            )
            log.info(
                f"[tuning {elapsed_min}m] {result.get('action', '-')} "
                f"{result.get('mode', '-')} | {result.get('reason', '')[:60]}"
            )
            key = f"{(elapsed_min // 30) * 30}min_tune"
            BrainDB.update_tuning_pattern(
                market,
                key,
                correct=(result.get("action") != "MAINTAIN"),
                new_insight=result.get("reason", ""),
            )
            return result
        except Exception as exc:
            if _is_overloaded_error(exc):
                waited = time.monotonic() - started
                if waited < _OVERLOAD_MAX_SEC:
                    remaining = max(0, int(_OVERLOAD_MAX_SEC - waited))
                    log.warning(
                        f"[tuning overload] {market} {elapsed_min}m attempt={attempt} "
                        f"retry in {_OVERLOAD_RETRY_SEC}s (remaining {remaining}s)"
                    )
                    time.sleep(_OVERLOAD_RETRY_SEC)
                    continue
                log.error(
                    f"[tuning overload] {market} {elapsed_min}m exceeded "
                    f"{_OVERLOAD_MAX_SEC // 60} minutes; keep previous mode"
                )
                return {
                    "action": "MAINTAIN",
                    "mode": prev_mode,
                    "size_adj": 0,
                    "sl_adj": 0.0,
                    "reason": f"Claude overload; skipped after {attempt} retries",
                    "warning": "OVERLOADED",
                }

            log.error(f"tuning error: {exc}")
            return {
                "action": "MAINTAIN",
                "mode": prev_mode,
                "size_adj": 0,
                "sl_adj": 0.0,
                "reason": f"error:{exc}",
                "warning": None,
            }
