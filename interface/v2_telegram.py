from __future__ import annotations

from typing import Any

from interface.v2_ops_summary import V2_TELEGRAM_COMMANDS, build_v2_ops_summary


def handle_v2_command(text: str, bot: Any) -> str:
    cmd = str(text or "").strip().split()[0].lower()
    summary = build_v2_ops_summary(bot=bot)
    if cmd == "/health":
        return _format_health(summary)
    if cmd == "/picks":
        return _format_picks(summary)
    if cmd == "/errors":
        return _format_errors(summary)
    if cmd == "/brain_pending":
        return _format_brain_pending(summary)
    if cmd == "/halt":
        return _halt(bot)
    if cmd == "/resume":
        return _resume(bot)
    if cmd == "/panic":
        return _panic(bot)
    if cmd == "/pathb_status":
        return _pathb_status(bot)
    if cmd == "/pathb_on":
        return _pathb_on(bot)
    if cmd == "/pathb_off":
        return _pathb_off(bot)
    if cmd == "/pathb_kill":
        return _pathb_kill(bot)
    if cmd == "/pathb_closeall":
        return _pathb_closeall(bot)
    return "Unsupported V2 command. Supported: " + ", ".join(V2_TELEGRAM_COMMANDS)


def _format_health(summary: dict[str, Any]) -> str:
    health = summary.get("system_health") or {}
    broker_truth = summary.get("broker_truth") or {}
    markets = broker_truth.get("markets") if isinstance(broker_truth.get("markets"), dict) else {}
    lines = [
        "<b>V2 Health</b>",
        f"bot_alive: {health.get('bot_alive')}",
        f"broker: KR={health.get('broker_status', {}).get('KR')} US={health.get('broker_status', {}).get('US')}",
        f"websocket: {health.get('websocket_status')}",
        f"ORDER_UNKNOWN: {health.get('order_unknown_count', 0)}",
        f"pending_orders: {health.get('unresolved_pending_orders', 0)}",
        f"last_lifecycle_event: {health.get('last_lifecycle_event_time') or '-'}",
    ]
    for market in ("KR", "US"):
        data = markets.get(market) if isinstance(markets.get(market), dict) else {}
        lines.append(
            f"broker_truth {market}: missing={bool(data.get('missing', True))} "
            f"stale={bool(data.get('stale', True))} last={data.get('last_success_at') or '-'}"
        )
        if data.get("error"):
            lines.append(f"broker_truth {market} error: {data.get('error')}")
    return "\n".join(lines)


def _format_picks(summary: dict[str, Any]) -> str:
    picks = summary.get("claude_picks") or []
    if not picks:
        return "<b>V2 Picks</b>\nNo Claude trade-ready picks for this session."
    lines = ["<b>V2 Picks</b>"]
    for pick in picks[-10:]:
        lines.append(f"{pick.get('market')} {pick.get('ticker')} {pick.get('decision_id')}")
    return "\n".join(lines)


def _format_errors(summary: dict[str, Any]) -> str:
    lifecycle = summary.get("lifecycle") or {}
    unknown = lifecycle.get("order_unknown") or []
    blocked = lifecycle.get("safety_blocked") or []
    unsupported = lifecycle.get("timing_unsupported") or []
    expired = lifecycle.get("timing_expired") or []
    lines = [
        "<b>V2 Errors</b>",
        f"ORDER_UNKNOWN: {len(unknown)}",
        f"SAFETY_BLOCKED: {len(blocked)}",
        f"TIMING_UNSUPPORTED: {len(unsupported)}",
        f"TIMING_EXPIRED: {len(expired)}",
    ]
    for event in (unknown + blocked + unsupported + expired)[-8:]:
        lines.append(
            f"{event.get('market')} {event.get('ticker')} {event.get('reason_code') or event.get('decision_id')}"
        )
    pathb_unknown = ((summary.get("path_b_live") or {}).get("order_unknown") or [])[-8:]
    if pathb_unknown:
        lines.append("")
        lines.append("<b>Path B ORDER_UNKNOWN</b>")
        for run in pathb_unknown:
            lines.append(
                f"{run.get('display_ticker') or run.get('ticker')} | "
                f"{run.get('order_unknown_resolution') or 'unresolved'} | "
                f"broker_pos={run.get('broker_position_evidence')} fill={run.get('broker_today_fill_evidence')}"
            )
    return "\n".join(lines)


def _format_brain_pending(summary: dict[str, Any]) -> str:
    brain = summary.get("brain") or {}
    pending = brain.get("pending_approval") or []
    lines = ["<b>V2 Brain Pending</b>", f"count: {brain.get('pending_approval_count', 0)}"]
    for item in pending[-5:]:
        candidate = item.get("candidate") or {}
        lines.append(str(candidate.get("id") or candidate.get("pattern_key") or candidate)[:120])
    return "\n".join(lines)


def _halt(bot: Any) -> str:
    setattr(bot, "v2_manual_halt", True)
    if getattr(bot, "risk", None) is not None:
        bot.risk.halted = True
        bot.risk.halt_reason = "manual_halt"
    return "V2 HALT set. New entries are paused; protective exits remain active."


def _resume(bot: Any) -> str:
    setattr(bot, "v2_manual_halt", False)
    if getattr(bot, "risk", None) is not None:
        bot.risk.halted = False
        bot.risk.halt_reason = ""
    unknown = getattr(bot, "v2_order_unknown", None)
    if unknown is not None and hasattr(unknown, "clear_manual_resume"):
        unknown.clear_manual_resume()
    return "V2 RESUME set. Manual halt and ORDER_UNKNOWN market/global pauses were cleared."


def _panic(bot: Any) -> str:
    setattr(bot, "v2_manual_halt", True)
    if getattr(bot, "risk", None) is not None:
        bot.risk.halted = True
        bot.risk.halt_reason = "panic"
    return "V2 PANIC set. New entries are paused and close-all will be requested."


def _pathb_status(bot: Any) -> str:
    pathb = getattr(bot, "pathb", None)
    if pathb is None:
        return "<b>Path B</b>\nruntime unavailable"
    status = pathb.status()
    market = str(getattr(bot, "current_market", "") or "KR").upper()
    runtime_mode = str(status.get("runtime_mode") or getattr(bot, "_mode", "live") or "live")
    try:
        summary = build_v2_ops_summary(bot=bot, market=market, runtime_mode=runtime_mode)
        pathb_live = summary.get("path_b_live") or {}
        selection = pathb_live.get("selection") or {}
        broker_truth = summary.get("broker_truth") or {}
    except Exception:
        selection = {}
        broker_truth = {}
    lines = [
        "<b>Path B Claude Price</b>",
        f"enabled: {status.get('enabled')}",
        f"operator_enabled: {status.get('operator_enabled')}",
        f"emergency_disabled: {status.get('emergency_disabled')}",
        f"mode: {status.get('mode')} / runtime={status.get('runtime_mode')}",
        f"fixed_order_krw: {int(status.get('fixed_order_krw') or 0):,}",
        f"max_positions: {status.get('max_positions')} daily_entries: {status.get('max_daily_entries')}",
        f"min_confidence: {status.get('min_confidence')}",
    ]
    if status.get("updated_at"):
        lines.append(f"updated: {status.get('updated_at')} by {status.get('updated_by')}")
    if status.get("reason"):
        lines.append(f"reason: {status.get('reason')}")
    market_truth = ((broker_truth.get("markets") or {}).get(market) or {}) if isinstance(broker_truth, dict) else {}
    if market_truth:
        lines.append(
            f"broker_truth {market}: missing={bool(market_truth.get('missing', True))} "
            f"stale={bool(market_truth.get('stale', True))} positions={len(market_truth.get('positions') or [])} "
            f"last={market_truth.get('last_success_at') or '-'}"
        )
    counts = selection.get("counts") or {}
    if counts:
        lines.extend(
            [
                "",
                "<b>오늘 B플랜 판단 흐름</b>",
                (
                    f"Claude 입력 {counts.get('universe', 0)} | 관찰 {counts.get('watchlist', 0)} | "
                    f"원래 매수 {counts.get('raw_trade_ready', 0)} | 적용 매수 {counts.get('applied_trade_ready', 0)} | "
                    f"가격 목표 {counts.get('price_targets', 0)} | B플랜 {counts.get('registered_plans', 0)}"
                ),
            ]
        )
    no_plan = selection.get("no_plan_reasons") or []
    if no_plan:
        lines.append("미동작/대기 사유: " + ", ".join(_ko_pathb_selection_reason(item) for item in no_plan))
    missing = selection.get("missing_price_targets") or []
    if missing:
        lines.append("가격 목표 누락: " + ", ".join(str(x) for x in missing[:8]))
    watch_rows = selection.get("watch_rows") or []
    if watch_rows:
        lines.append("")
        lines.append("<b>관찰/차단 종목</b>")
        for row in watch_rows[:8]:
            lines.append(
                f"{row.get('display_ticker') or row.get('ticker')} | {_ko_pathb_watch_state(row.get('state'))} | "
                f"{str(row.get('reason') or row.get('filter_reason') or '')[:60]}"
            )
    return "\n".join(lines)


def _ko_pathb_selection_reason(value: Any) -> str:
    mapping = {
        "MARKET_NOT_SELECTED": "시장 선택 필요",
        "PATHB_CONFIG_DISABLED": "설정에서 B플랜 꺼짐",
        "PATHB_OPERATOR_DISABLED": "운영자가 B플랜 중지",
        "PATHB_EMERGENCY_DISABLED": "B플랜 긴급 중지",
        "NO_SELECTION_FILE": "오늘 Claude 판단 파일 없음",
        "NO_WATCHLIST": "Claude 관찰 종목 없음",
        "ALL_TRADE_READY_FILTERED": "매수 후보가 필터에서 모두 차단",
        "PRICE_TARGETS_EMPTY": "Claude 가격 목표 전체 누락",
        "MISSING_PRICE_TARGETS": "일부 매수 후보 가격 목표 누락",
        "NO_PATH_RUN_REGISTERED": "매수 후보는 있으나 B플랜 등록 없음",
        "NO_TRADE_READY": "매수 후보 없음",
    }
    return mapping.get(str(value or ""), str(value or ""))


def _ko_pathb_watch_state(value: Any) -> str:
    raw = str(value or "")
    mapping = {
        "WATCH_ONLY": "관찰만",
        "READY_NO_PATH_RUN": "등록 대기/누락",
        "MISSING_PRICE_TARGETS": "가격 목표 없음",
        "WAITING": "지정가 대기",
        "HIT": "매수가 도달",
        "ORDER_SENT": "매수 주문 전송",
        "ORDER_ACKED": "매수 주문 접수",
        "PARTIAL_FILLED": "부분 체결",
        "FILLED": "보유 중",
        "SELL_SENT": "매도 주문 전송",
        "SELL_ACKED": "매도 주문 접수",
        "CLOSED": "청산 완료",
        "EXPIRED": "매수 미도달",
        "CANCELLED": "취소",
        "ORDER_UNKNOWN": "주문상태 불명",
        "continuation_shadow_only": "연속상승 비활성",
    }
    if raw.startswith("slot_cap:"):
        return "전략 슬롯 초과:" + raw.split(":", 1)[1]
    if raw.startswith("slot_disabled:"):
        return "전략 슬롯 비활성:" + raw.split(":", 1)[1]
    return mapping.get(raw, raw)


def _pathb_on(bot: Any) -> str:
    pathb = getattr(bot, "pathb", None)
    if pathb is None:
        return "Path B runtime unavailable."
    pathb.set_enabled(True, updated_by="telegram", reason="pathb_on")
    return "Path B Claude Price live buy/sell is ON."


def _pathb_off(bot: Any) -> str:
    pathb = getattr(bot, "pathb", None)
    if pathb is None:
        return "Path B runtime unavailable."
    pathb.set_enabled(False, updated_by="telegram", reason="pathb_off")
    return "Path B is OFF for new Claude-price entries. Existing protective exits remain active."


def _pathb_kill(bot: Any) -> str:
    pathb = getattr(bot, "pathb", None)
    if pathb is None:
        return "Path B runtime unavailable."
    pathb.emergency_disable(updated_by="telegram", reason="pathb_kill")
    return "Path B KILL set. New Path B entries stopped, waiting plans cancelled, open Path B positions requested to close."


def _pathb_closeall(bot: Any) -> str:
    pathb = getattr(bot, "pathb", None)
    if pathb is None:
        return "Path B runtime unavailable."
    market = getattr(bot, "current_market", None) or "KR"
    count = pathb.close_all_open(str(market).upper(), reason="pathb_closeall")
    return f"Path B close-all requested for {market}: {count} position(s)."
