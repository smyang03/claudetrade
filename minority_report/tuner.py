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
from runtime.tuning_bounds import RUNTIME_ADJUSTMENT_BOUNDS, coerce_runtime_adjustments

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
_RUNTIME_ADJUSTMENT_BOUNDS = RUNTIME_ADJUSTMENT_BOUNDS


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
    return coerce_runtime_adjustments(result)


def _format_runtime_bound(value: float) -> str:
    return f"{value:g}"


def _runtime_adjustment_bounds_text() -> str:
    lines = []
    for key, (low, high) in _RUNTIME_ADJUSTMENT_BOUNDS.items():
        suffix = " 정수" if key == "momentum_wait_adjust_min" else ""
        lines.append(f"- {key}: {_format_runtime_bound(low)} ~ {_format_runtime_bound(high)}{suffix}")
    return "\n".join(lines)


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


def _fmt_breadth(summary: dict) -> str:
    if not isinstance(summary, dict) or not summary.get("universe_count"):
        return "N/A"
    return (
        f"universe={summary.get('universe_count')} "
        f"adv/dec={summary.get('advancers', 0)}/{summary.get('decliners', 0)} "
        f"GC/DC={summary.get('golden_cross', 0)}/{summary.get('dead_cross', 0)} "
        f"RSI_OB/OS={summary.get('rsi_overbought', 0)}/{summary.get('rsi_oversold', 0)} "
        f"vol_spike={summary.get('volume_spike', 0)} "
        f"data={','.join(summary.get('data_quality_flags') or []) or 'ok'}"
    )


def _fmt_breadth_delta(morning: dict, current: dict) -> str:
    if not isinstance(morning, dict) or not isinstance(current, dict):
        return "N/A"
    if not morning.get("universe_count") or not current.get("universe_count"):
        return "N/A"

    def delta(key: str) -> int:
        return int(current.get(key, 0) or 0) - int(morning.get(key, 0) or 0)

    adv_ratio_delta = (
        float(current.get("advance_ratio", 0) or 0)
        - float(morning.get("advance_ratio", 0) or 0)
    ) * 100
    return (
        f"advance_ratio {adv_ratio_delta:+.0f}%p, "
        f"advancers {delta('advancers'):+d}, "
        f"decliners {delta('decliners'):+d}, "
        f"GC {delta('golden_cross'):+d}, "
        f"DC {delta('dead_cross'):+d}, "
        f"RSI_OB {delta('rsi_overbought'):+d}, "
        f"RSI_OS {delta('rsi_oversold'):+d}"
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
    morning_breadth = (
        current_state.get("morning_breadth")
        or (morning_judgment.get("digest_raw", {}) or {}).get("breadth_summary")
        or {}
    )
    current_breadth = (
        current_state.get("current_breadth")
        or current_state.get("breadth_summary")
        or {}
    )
    previous_tune_action = current_state.get("previous_tune_action") or "N/A"
    maintain_streak = int(current_state.get("maintain_streak", 0) or 0)

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
아침 breadth: {_fmt_breadth(morning_breadth)}
{brain_summary[:300]}

현재 상태 (경과 {elapsed_min}분):
  지수 변화(개장 대비): {current_state.get('index_change', 0):+.2f}%
  최근 30분 기울기: {slope_str}
  거래량 추세: {current_state.get('volume_trend', 'normal')}
  경고: {current_state.get('alerts', []) or '없음'}
  현재 breadth: {_fmt_breadth(current_breadth)}
  breadth 변화: {_fmt_breadth_delta(morning_breadth, current_breadth)}
  직전 tune action: {previous_tune_action}
  연속 MAINTAIN 횟수: {maintain_streak}
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
- 아침 thesis가 유지되는지는 breadth 변화와 섹터/리스크 데이터로 먼저 확인
- 개별 종목 근거가 반복되더라도 breadth가 악화되면 TIGHTEN/REVERSE를 검토
- 포지션 손실이 크고 지수도 약세면 TIGHTEN
- hard safety rule 자체는 건드리지 말고 아래 bounded override 안에서만 미세조정
- execution profile / ops review가 말하는 병목(trade_ready 전환 저조, watch_only missed, ATR 과차단) 해결에만 조정 사용
- 시장이 강하면 momentum wait을 줄이고 시장이 약하면 늘릴 수 있음
- entry priority cutoff는 소폭 완화/강화만 허용
- KR momentum ATR cap은 완화/강화만 허용

bounded override 범위:
{_runtime_adjustment_bounds_text()}

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
                model=MODEL,
            )
            save_raw_call(
                label=f"tune_{elapsed_min}min",
                prompt=prompt,
                raw_response=raw,
                parsed=result,
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                market=market,
                model=MODEL,
                prompt_mode="intraday_tune",
            )
            log.info(
                f"[intraday_tuning {elapsed_min}m] {result.get('action', '-')} "
                f"{result.get('mode', '-')} | {_runtime_adjustment_summary(result)} | "
                f"{result.get('reason', '')[:60]}"
            )
            key = f"{(elapsed_min // 30) * 30}min_tune"
            adjusted = result.get("action") != "MAINTAIN"
            # Legacy parameter name: BrainDB records this as adjustment frequency, not hit accuracy.
            BrainDB.update_tuning_pattern(
                market,
                key,
                correct=adjusted,
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
