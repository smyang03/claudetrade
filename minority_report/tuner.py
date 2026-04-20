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


def _format_positions_summary(positions: list) -> str:
    """튜너 프롬프트용 포지션 요약 텍스트"""
    if not positions:
        return "  (보유 포지션 없음)"
    lines = []
    for p in positions:
        ticker  = p.get("ticker", "-")
        qty     = int(p.get("qty", 0) or 0)
        entry   = float(p.get("entry", 0) or 0)
        cp      = float(p.get("current_price", entry) or entry)
        pnl_pct = float(p.get("pnl_pct", 0) or 0)
        strat   = p.get("strategy", "-")
        sl      = float(p.get("sl", 0) or 0)
        tp      = float(p.get("tp", 0) or 0)
        sl_str  = f" SL={sl:,.0f}" if sl > 0 else ""
        tp_str  = f" TP={tp:,.0f}" if tp > 0 else ""
        lines.append(
            f"  {ticker} {qty}주 진입={entry:,.0f} 현재={cp:,.0f} "
            f"수익률={pnl_pct:+.1f}% 전략={strat}{sl_str}{tp_str}"
        )
    return "\n".join(lines)


def tune(market: str, elapsed_min: int, current_state: dict,
         morning_judgment: dict, brain_summary: str) -> dict:
    """
    Intraday tuning: compare current state vs. morning judgment.
    Returns: {action, mode, size_adj, sl_adj, reason, warning}
    """
    prev_mode = morning_judgment.get("consensus", {}).get("mode", "CAUTIOUS")
    positions_text = _format_positions_summary(current_state.get("positions", []))

    _slope = current_state.get("index_slope_30m")
    if _slope is None:
        _slope_str = "N/A (첫 튜닝)"
    elif _slope > 0:
        _slope_str = f"+{_slope:.2f}%p (상승 중)"
    elif _slope < 0:
        _slope_str = f"{_slope:.2f}%p (하락 중)"
    else:
        _slope_str = "0.00%p (횡보)"

    prompt = f"""당신은 장중 튜닝 분석가입니다. 아침 판단과 현재 상태를 비교하고 JSON으로만 응답하세요.

아침 모드: {prev_mode}
아침 Bull 근거: {morning_judgment.get('judgments', {}).get('bull', {}).get('key_reason', '')}
아침 Bear 근거: {morning_judgment.get('judgments', {}).get('bear', {}).get('key_reason', '')}
{brain_summary[:300]}

현재 상태 (경과 {elapsed_min}분):
  지수 변동 (개장 대비): {current_state.get('index_change', 0):+.2f}%
  최근 30분 기울기: {_slope_str}
  거래량 추세: {current_state.get('volume_trend', 'normal')}
  경고: {current_state.get('alerts', []) or '없음'}

보유 포지션:
{positions_text}

판단 기준:
- 30분 기울기가 아침 판단과 같은 방향이면 MAINTAIN
- 지수 변동이 아침 판단과 반대이고 30분 기울기도 반대 방향이면 REVERSE 검토
- 단, 30분 기울기 N/A(첫 튜닝)이면 지수 변동(개장 대비)만으로 판단
- 포지션에 손실이 있고 지수도 약세면 TIGHTEN (SL 조정)

JSON으로만 응답:
{{"action":"MAINTAIN|TIGHTEN|REVERSE","mode":"{prev_mode} 또는 조정된 모드",
  "size_adj":0,"sl_adj":0.0,"reason":"한 문장 근거 (수치 포함)",
  "warning":"경고 또는 null"}}"""

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
