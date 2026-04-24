"""minority_report/tuner.py - intraday tuning"""

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
_RUNTIME_ADJUSTMENT_BOUNDS = {
    "momentum_wait_adjust_min": (-15, 15),
    "entry_priority_cutoff_adjust": (-0.08, 0.08),
    "kr_momentum_atr_cap_adjust": (-0.02, 0.03),
    "kr_momentum_atr_cap_high_adjust": (-0.02, 0.03),
}


def _is_overloaded_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "529" in text or "overloaded" in text or "overload" in text


def _format_positions_summary(positions: list) -> str:
    if not positions:
        return "  (보유 포지션 없음)"
    lines = []
    for pos in positions:
        ticker = pos.get("ticker", "-")
        qty = int(pos.get("qty", 0) or 0)
        entry = float(pos.get("entry", 0) or 0)
        current_price = float(pos.get("current_price", entry) or entry)
        pnl_pct = float(pos.get("pnl_pct", 0) or 0)
        strategy = pos.get("strategy", "-")
        sl = float(pos.get("sl", 0) or 0)
        tp = float(pos.get("tp", 0) or 0)
        sl_str = f" SL={sl:,.0f}" if sl > 0 else ""
        tp_str = f" TP={tp:,.0f}" if tp > 0 else ""
        lines.append(
            f"  {ticker} {qty}주 진입={entry:,.0f} 현재={current_price:,.0f} "
            f"손익률={pnl_pct:+.1f}% 전략={strategy}{sl_str}{tp_str}"
        )
    return "\n".join(lines)


def _coerce_runtime_adjustments(result: dict) -> dict:
    normalized = dict(result or {})
    for key, (low, high) in _RUNTIME_ADJUSTMENT_BOUNDS.items():
        raw_value = normalized.get(key, 0)
        try:
            value = float(raw_value or 0)
        except (TypeError, ValueError):
            value = 0.0
        value = max(low, min(high, value))
        if key == "momentum_wait_adjust_min":
            normalized[key] = int(round(value))
        else:
            normalized[key] = round(value, 4)
    return normalized


def _runtime_adjustment_summary(result: dict) -> str:
    wait_adj = int(result.get("momentum_wait_adjust_min", 0) or 0)
    cutoff_adj = float(result.get("entry_priority_cutoff_adjust", 0.0) or 0.0)
    atr_adj = float(result.get("kr_momentum_atr_cap_adjust", 0.0) or 0.0)
    atr_high_adj = float(result.get("kr_momentum_atr_cap_high_adjust", 0.0) or 0.0)
    return (
        f"wait={wait_adj:+d}m "
        f"cutoff={cutoff_adj:+.2f} "
        f"kr_atr={atr_adj:+.2f}/{atr_high_adj:+.2f}"
    )


def _default_result(prev_mode: str, reason: str, warning=None) -> dict:
    return {
        "action": "MAINTAIN",
        "mode": prev_mode,
        "size_adj": 0,
        "sl_adj": 0.0,
        "momentum_wait_adjust_min": 0,
        "entry_priority_cutoff_adjust": 0.0,
        "kr_momentum_atr_cap_adjust": 0.0,
        "kr_momentum_atr_cap_high_adjust": 0.0,
        "reason": reason,
        "warning": warning,
    }


def tune(market: str, elapsed_min: int, current_state: dict,
         morning_judgment: dict, brain_summary: str) -> dict:
    """
    Intraday tuning: compare current state vs. morning judgment.
    Returns action/mode/size/sl plus bounded runtime overrides.
    """
    prev_mode = morning_judgment.get("consensus", {}).get("mode", "CAUTIOUS")
    positions_text = _format_positions_summary(current_state.get("positions", []))
    runtime_overrides = current_state.get("runtime_overrides", {}) or {}

    slope = current_state.get("index_slope_30m")
    if slope is None:
        slope_str = "N/A (first sample)"
    elif slope > 0:
        slope_str = f"+{slope:.2f}%p (rising)"
    elif slope < 0:
        slope_str = f"{slope:.2f}%p (falling)"
    else:
        slope_str = "0.00%p (flat)"

    prompt = f"""당신은 intraday tuning analyst입니다. 아침 판단과 현재 시장 상태를 비교하고 JSON으로만 응답하세요.

아침 모드: {prev_mode}
아침 Bull 근거: {morning_judgment.get('judgments', {}).get('bull', {}).get('key_reason', '')}
아침 Bear 근거: {morning_judgment.get('judgments', {}).get('bear', {}).get('key_reason', '')}
{brain_summary[:300]}

현재 상태 (경과 {elapsed_min}분):
  지수 변화(개장 대비): {current_state.get('index_change', 0):+.2f}%
  최근 30분 기울기: {slope_str}
  거래량 추세: {current_state.get('volume_trend', 'normal')}
  경고: {current_state.get('alerts', []) or '없음'}
  현재 runtime 조정:
    momentum_wait_adjust_min={runtime_overrides.get('momentum_wait_adjust_min', 0)}
    entry_priority_cutoff_adjust={runtime_overrides.get('entry_priority_cutoff_adjust', 0.0)}
    kr_momentum_atr_cap_adjust={runtime_overrides.get('kr_momentum_atr_cap_adjust', 0.0)}
    kr_momentum_atr_cap_high_adjust={runtime_overrides.get('kr_momentum_atr_cap_high_adjust', 0.0)}
  execution profile: {current_state.get('execution_profile', 'N/A')}
  ops review: {current_state.get('ops_review_context', 'N/A')}

보유 포지션:
{positions_text}

판단 기준:
- 30분 기울기가 아침 판단과 같은 방향이면 MAINTAIN
- 지수 변화가 아침 판단과 반대이고 30분 기울기도 반대 방향이면 REVERSE 검토
- 단, 30분 기울기 N/A면 지수 변화 개장 대비만으로 판단
- 포지션 손실이 크고 지수도 약세면 TIGHTEN
- hard safety rule 자체는 건드리지 말고 아래 bounded override 안에서만 미세조정
- execution profile / ops review가 말하는 병목(trade_ready 전환 저조, watch_only missed, ATR 과차단) 해결에만 조정 사용
- 시장이 강하면 momentum wait을 줄이고 시장이 약하면 늘릴 수 있음
- entry priority cutoff는 소폭 완화/강화만 허용
- KR momentum ATR cap은 완화/강화만 허용

bounded override 범위:
- momentum_wait_adjust_min: -15 ~ 15 정수
- entry_priority_cutoff_adjust: -0.08 ~ 0.08
- kr_momentum_atr_cap_adjust: -0.02 ~ 0.03
- kr_momentum_atr_cap_high_adjust: -0.02 ~ 0.03

JSON으로만 응답:
{{"action":"MAINTAIN|TIGHTEN|REVERSE","mode":"{prev_mode} 또는 조정된 모드",
  "size_adj":0,"sl_adj":0.0,
  "momentum_wait_adjust_min":0,
  "entry_priority_cutoff_adjust":0.0,
  "kr_momentum_atr_cap_adjust":0.0,
  "kr_momentum_atr_cap_high_adjust":0.0,
  "reason":"한 문장 근거 (수치 포함)",
  "warning":"경고 또는 null"}}"""

    started = time.monotonic()
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = client.messages.create(
                model=MODEL,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.content[0].text.strip()
            result = _coerce_runtime_adjustments(extract_json(raw))
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
                f"[intraday_tuning {elapsed_min}m] {result.get('action', '-')} "
                f"{result.get('mode', '-')} | {_runtime_adjustment_summary(result)} | "
                f"{result.get('reason', '')[:60]}"
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
                return _default_result(
                    prev_mode,
                    f"Claude overload; skipped after {attempt} retries",
                    warning="OVERLOADED",
                )

            log.error(f"tuning error: {exc}")
            return _default_result(prev_mode, f"error:{exc}")
