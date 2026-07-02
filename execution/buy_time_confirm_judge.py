from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import json
import os
import time
from typing import Any

from minority_report.claude_utils import extract_json, response_text, thinking_extra_body
from minority_report.prompt_contracts import COMMON_DECISION_CONTRACT


CLAUDE_DECISIONS = {"CONFIRM_BUY", "DEFER", "REJECT"}
INTERNAL_UNAVAILABLE = "CONFIRM_UNAVAILABLE_PROCEED"


def _market_key(market: str) -> str:
    return "US" if str(market or "").upper() == "US" else "KR"


def _ticker_key(ticker: Any, market: str) -> str:
    text = str(ticker or "").strip()
    return text.upper() if _market_key(market) == "US" else text


def _compact(value: Any, *, max_chars: int = 6500) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        text = str(value)
    if len(text) > max_chars:
        return text[: max(0, max_chars - 3)].rstrip() + "..."
    return text


def adverse_context(context: dict[str, Any] | None) -> bool:
    raw = dict(context or {})
    risk_mode = str(raw.get("risk_mode") or "").strip().upper()
    severity = str(raw.get("market_change_severity_bucket") or "").strip().lower()
    return risk_mode in {"RISK_OFF", "HALT"} or severity == "severe_down"


def unavailable_result(*, market: str, ticker: str, reason: str) -> dict[str, Any]:
    return {
        "ticker": _ticker_key(ticker, market),
        "market": _market_key(market),
        "decision": INTERNAL_UNAVAILABLE,
        "confirm_unavailable_reason": str(reason or "unavailable"),
        "confidence": 0.0,
        "reason": "Claude confirm unavailable; continue only if current context is not adverse.",
        "valid": False,
    }


def normalize_buy_time_confirm_result(
    result: dict[str, Any],
    *,
    market: str,
    ticker: str,
    current_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = dict(result or {})
    decision = str(raw.get("decision") or raw.get("action") or "").strip().upper()
    aliases = {
        "BUY": "CONFIRM_BUY",
        "CONFIRM": "CONFIRM_BUY",
        "WAIT": "DEFER",
        "HOLD": "DEFER",
        "NO_TRADE": "REJECT",
        "AVOID": "REJECT",
    }
    decision = aliases.get(decision, decision)
    if decision not in CLAUDE_DECISIONS:
        return unavailable_result(market=market, ticker=ticker, reason="invalid_decision")
    try:
        confidence = float(raw.get("confidence") or 0.0)
    except Exception:
        confidence = 0.0
    out = {
        **raw,
        "ticker": _ticker_key(raw.get("ticker") or ticker, market),
        "market": _market_key(raw.get("market") or market),
        "decision": decision,
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": str(raw.get("reason") or "").strip(),
        "invalid_if": str(raw.get("invalid_if") or raw.get("invalidation_condition") or "").strip(),
        "reject_reason_code": str(raw.get("reject_reason_code") or "").strip(),
        "valid": True,
        "adverse_context": adverse_context(current_context),
    }
    return out


def build_buy_time_confirm_prompt(
    *,
    market: str,
    ticker: str,
    candidate: dict[str, Any],
    signal: dict[str, Any],
    current_context: dict[str, Any],
    selection_context: dict[str, Any],
) -> str:
    payload = {
        "market": _market_key(market),
        "ticker": _ticker_key(ticker, market),
        "candidate": candidate or {},
        "signal": signal or {},
        "current_context": current_context or {},
        "selection_context": selection_context or {},
    }
    return (
        "You are confirming whether one already-selected live trading candidate is still valid at order time.\n"
        "Return JSON only. Do not include markdown.\n"
        "Allowed decision: CONFIRM_BUY, DEFER, REJECT.\n"
        "Do not choose other actions such as BUY_READY, PROBE_READY, PULLBACK_WAIT, HOLD, or SELL.\n"
        "CONFIRM_BUY means the existing candidate and current signal are still valid now.\n"
        "DEFER means this cycle should skip and wait for a fresh selection or a later signal.\n"
        "REJECT means the candidate is currently unsuitable for this order-time setup, but not permanently banned.\n"
        "Input:\n"
        f"{_compact(payload, max_chars=int(os.getenv('BUY_TIME_CONFIRM_INPUT_MAX_CHARS', '6500') or 6500))}\n"
        "JSON schema:\n"
        '{"ticker":"AAPL","market":"US","decision":"CONFIRM_BUY","confidence":0.72,'
        '"reason":"setup still valid","invalid_if":"price loses VWAP","reject_reason_code":""}'
    )


def parse_buy_time_confirm_response(raw: str) -> dict[str, Any]:
    return extract_json(str(raw or "").strip())


def call_buy_time_confirm_judge(
    *,
    market: str,
    ticker: str,
    candidate: dict[str, Any],
    signal: dict[str, Any],
    current_context: dict[str, Any],
    selection_context: dict[str, Any],
    client: Any | None = None,
) -> dict[str, Any]:
    from credit_tracker import record as credit_record
    from minority_report.raw_call_logger import save as save_raw_call

    model = os.getenv("BUY_TIME_CONFIRM_MODEL") or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    max_tokens = int(float(os.getenv("BUY_TIME_CONFIRM_MAX_TOKENS", "700") or 700))
    timeout_ms = int(float(os.getenv("BUY_TIME_CONFIRM_TIMEOUT_MS", "2500") or 2500))
    timeout_sec = max(0.0, float(timeout_ms) / 1000.0)
    prompt = build_buy_time_confirm_prompt(
        market=market,
        ticker=ticker,
        candidate=candidate,
        signal=signal,
        current_context=current_context,
        selection_context=selection_context,
    )
    if client is None:
        import anthropic

        api_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    else:
        api_client = client

    def _create_response() -> Any:
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            # 공유 결정 계약(수량/브로커/게이트 불결정 경계)은 system으로 전달 — 인라인 재기술 제거
            "system": [{"type": "text", "text": COMMON_DECISION_CONTRACT}],
            "messages": [{"role": "user", "content": prompt}],
            "extra_body": thinking_extra_body("buy_time_confirm"),
        }
        if timeout_sec > 0:
            try:
                return api_client.messages.create(**kwargs, timeout=timeout_sec)
            except TypeError as exc:
                if "timeout" not in str(exc):
                    raise
        return api_client.messages.create(**kwargs)

    started = time.perf_counter()
    try:
        if timeout_sec > 0:
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(_create_response)
            try:
                resp = future.result(timeout=timeout_sec)
            except FuturesTimeoutError:
                future.cancel()
                return unavailable_result(market=market, ticker=ticker, reason="timeout")
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
        else:
            resp = _create_response()
        duration_ms = int((time.perf_counter() - started) * 1000)
        raw = response_text(resp)
    except Exception:
        return unavailable_result(market=market, ticker=ticker, reason="api_error")
    parse_error = False
    try:
        parsed = parse_buy_time_confirm_response(raw)
        normalized = normalize_buy_time_confirm_result(
            parsed,
            market=market,
            ticker=ticker,
            current_context=current_context,
        )
    except Exception:
        parse_error = True
        normalized = unavailable_result(market=market, ticker=ticker, reason="parse_fail")
    try:
        credit_record(
            resp.usage.input_tokens,
            resp.usage.output_tokens,
            "buy_time_confirm_judge",
            model=model,
            cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        )
    except Exception:
        pass
    try:
        save_raw_call(
            label="buy_time_confirm_judge",
            prompt=prompt,
            raw_response=raw,
            parsed=normalized,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            duration_ms=duration_ms,
            market=_market_key(market),
            model=model,
            parse_error=parse_error,
            parse_stage="buy_time_confirm_judge_v1",
            prompt_version="buy_time_confirm_judge_v1",
            extra={"ticker": _ticker_key(ticker, market)},
        )
    except Exception:
        pass
    return normalized
