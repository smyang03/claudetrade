from __future__ import annotations

from typing import Any

from interface.v2_ops_summary import V2_TELEGRAM_COMMANDS, build_v2_ops_summary


def _ko_bool(value: Any) -> str:
    return "예" if bool(value) else "아니오"


def _ko_onoff(value: Any) -> str:
    return "켜짐" if bool(value) else "꺼짐"


def _ko_pathb_mode(value: Any) -> str:
    mapping = {
        "min_size_live": "최소금액 실매매",
        "disabled": "중지",
        "off": "중지",
    }
    raw = str(value or "")
    return mapping.get(raw, raw or "-")


def _ko_runtime_mode(value: Any) -> str:
    mapping = {
        "live": "실전",
        "paper": "모의",
    }
    raw = str(value or "")
    return mapping.get(raw.lower(), raw or "-")


def _ko_health_status(value: Any) -> str:
    mapping = {
        "ok": "정상",
        "stale": "오래됨",
        "missing": "없음",
        "error": "오류",
        "running": "실행중",
        "stopped": "중지",
        "unknown": "미확인",
        "healthy": "정상",
        "unhealthy": "비정상",
    }
    raw = str(value or "")
    return mapping.get(raw.lower(), raw or "-")


def _ko_actor(value: Any) -> str:
    mapping = {
        "default": "기본값",
        "telegram": "텔레그램",
        "operator": "운영자",
    }
    raw = str(value or "")
    return mapping.get(raw.lower(), raw or "-")


def _ko_resolution(value: Any) -> str:
    mapping = {
        "unresolved": "미해결",
        "path_a_origin_possible": "A플랜 기인 가능",
        "broker_no_evidence": "계좌 근거 없음",
        "broker_truth_unavailable": "계좌 조회 실패",
        "ambiguous_broker_truth": "계좌 근거 애매함",
        "pathb_fill_recovered": "B플랜 체결 복구",
        "pathb_open_order_recovered": "B플랜 미체결 주문 복구",
        "session_end_unresolved": "세션 종료 미해결",
    }
    raw = str(value or "")
    return mapping.get(raw, raw or "-")


def _ko_control_reason(value: Any) -> str:
    mapping = {
        "pathb_on": "B플랜 켜짐",
        "pathb_off": "B플랜 꺼짐",
        "pathb_kill": "B플랜 긴급 중지",
        "pathb_closeall": "B플랜 전체 청산",
        "manual_halt": "수동 중지",
        "panic": "긴급 중지",
    }
    raw = str(value or "")
    return mapping.get(raw, raw or "")


def handle_v2_command(text: str, bot: Any, *, market_override: str | None = None) -> str:
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
    if cmd in {"/buy_capacity", "/capacity"}:
        return _format_buy_capacity(summary)
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
        return _pathb_closeall(bot, market=market_override)
    return "지원하지 않는 V2 명령입니다. 사용 가능: " + ", ".join(V2_TELEGRAM_COMMANDS)


def _format_health(summary: dict[str, Any]) -> str:
    health = summary.get("system_health") or {}
    broker_truth = summary.get("broker_truth") or {}
    markets = broker_truth.get("markets") if isinstance(broker_truth.get("markets"), dict) else {}
    lines = [
        "<b>V2 상태 점검</b>",
        f"봇 실행: {_ko_bool(health.get('bot_alive'))}",
        f"브로커: KR={_ko_health_status(health.get('broker_status', {}).get('KR'))} US={_ko_health_status(health.get('broker_status', {}).get('US'))}",
        f"웹소켓: {_ko_health_status(health.get('websocket_status'))}",
        f"주문상태 불명: {health.get('order_unknown_count', 0)}",
        f"ORDER_UNKNOWN: {health.get('order_unknown_count', 0)}",
        f"미해결 주문: {health.get('unresolved_pending_orders', 0)}",
        f"마지막 생명주기 이벤트: {health.get('last_lifecycle_event_time') or '-'}",
        f"broker_truth: KR={_ko_bool('KR' in markets)} US={_ko_bool('US' in markets)}",
    ]
    for market in ("KR", "US"):
        data = markets.get(market) if isinstance(markets.get(market), dict) else {}
        lines.append(
            f"계좌 조회 {market}: 누락={_ko_bool(data.get('missing', True))} "
            f"오래됨={_ko_bool(data.get('stale', True))} 마지막={data.get('last_success_at') or '-'}"
        )
        if data.get("error"):
            lines.append(f"계좌 조회 {market} 오류: {data.get('error')}")
    return "\n".join(lines)


def _format_picks(summary: dict[str, Any]) -> str:
    picks = summary.get("claude_picks") or []
    if not picks:
        return "<b>V2 매수 후보</b>\n이번 세션 Claude 매수 후보가 없습니다."
    lines = ["<b>V2 매수 후보</b>"]
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
        "<b>V2 오류/차단</b>",
        f"주문상태 불명: {len(unknown)}",
        f"안전 차단: {len(blocked)}",
        f"타이밍 미지원: {len(unsupported)}",
        f"타이밍 만료: {len(expired)}",
    ]
    for event in (unknown + blocked + unsupported + expired)[-8:]:
        lines.append(
            f"{event.get('market')} {event.get('ticker')} {event.get('reason_code') or event.get('decision_id')}"
        )
    pathb_unknown = ((summary.get("path_b_live") or {}).get("order_unknown") or [])[-8:]
    if pathb_unknown:
        lines.append("")
        lines.append("<b>B플랜 주문상태 불명</b>")
        for run in pathb_unknown:
            lines.append(
                f"{run.get('display_ticker') or run.get('ticker')} | "
                f"{_ko_resolution(run.get('order_unknown_resolution') or 'unresolved')} | "
                f"계좌보유={_ko_bool(run.get('broker_position_evidence'))} 당일체결={_ko_bool(run.get('broker_today_fill_evidence'))}"
            )
    return "\n".join(lines)


def _format_brain_pending(summary: dict[str, Any]) -> str:
    brain = summary.get("brain") or {}
    pending = brain.get("pending_approval") or []
    lines = ["<b>V2 Brain 승인 대기</b>", f"건수: {brain.get('pending_approval_count', 0)}"]
    for item in pending[-5:]:
        candidate = item.get("candidate") or {}
        lines.append(str(candidate.get("id") or candidate.get("pattern_key") or candidate)[:120])
    return "\n".join(lines)


def _format_krw(value: Any) -> str:
    try:
        return f"{int(round(float(value or 0))):,} KRW"
    except Exception:
        return "0 KRW"


def _format_pct(value: Any) -> str:
    try:
        return f"{float(value or 0):.1f}%"
    except Exception:
        return "0.0%"


def _format_buy_capacity(summary: dict[str, Any]) -> str:
    pathb_live = summary.get("path_b_live") if isinstance(summary.get("path_b_live"), dict) else {}
    capacity = pathb_live.get("execution_capacity") if isinstance(pathb_live.get("execution_capacity"), dict) else {}
    if not capacity:
        return "<b>Buy Capacity</b>\nNo capacity snapshot is available."
    lines = ["<b>Buy Capacity</b>"]
    for market in ("KR", "US"):
        row = capacity.get(market) if isinstance(capacity.get(market), dict) else {}
        if not row:
            continue
        cap_pct = float(row.get("max_gross_exposure_pct") or 0)
        cap_amount = row.get("gross_exposure_cap_krw") if cap_pct > 0 else None
        remaining = row.get("gross_exposure_remaining_krw") if cap_pct > 0 else row.get("orderable_cash_krw")
        reasons = row.get("capacity_block_reasons") if isinstance(row.get("capacity_block_reasons"), list) else []
        reason_text = ", ".join(str(item) for item in reasons) if reasons else "OK"
        cap_text = (
            f"{_format_krw(cap_amount)} ({_format_pct(row.get('gross_exposure_pct'))}/{_format_pct(cap_pct)})"
            if cap_pct > 0
            else "no analyst cap"
        )
        cap_source = str(row.get("gross_cap_source") or "analyst_consensus")
        cap_mode = str(row.get("gross_cap_mode") or "auto")
        policy_text = "manual" if cap_source == "manual_config" else "Claude"
        lines.extend(
            [
                f"{market}: held {_format_krw(row.get('position_exposure_krw'))} / cap {cap_text} [{cap_mode}/{policy_text}]",
                (
                    f"  orderable {_format_krw(row.get('orderable_cash_krw'))} | "
                    f"remaining {_format_krw(remaining)} | today {_format_krw(row.get('today_buy_capacity_krw'))}"
                ),
                (
                    f"  fixed orders {row.get('today_entry_capacity_orders', 0)} "
                    f"({_format_krw(row.get('today_fixed_order_capacity_krw'))}) | {reason_text}"
                ),
            ]
        )
    return "\n".join(lines)


def _halt(bot: Any) -> str:
    setattr(bot, "v2_manual_halt", True)
    if getattr(bot, "risk", None) is not None:
        bot.risk.halted = True
        bot.risk.halt_reason = "manual_halt"
    return "HALT\nV2 거래중지가 설정되었습니다. 신규 진입은 멈추고 보호 청산은 유지됩니다."


def _resume(bot: Any) -> str:
    setattr(bot, "v2_manual_halt", False)
    if getattr(bot, "risk", None) is not None:
        bot.risk.halted = False
        bot.risk.halt_reason = ""
    unknown = getattr(bot, "v2_order_unknown", None)
    if unknown is not None and hasattr(unknown, "clear_manual_resume"):
        unknown.clear_manual_resume()
    return "RESUME\nV2 재개가 설정되었습니다. 수동 중지와 주문상태 불명으로 인한 시장/전체 일시정지를 해제했습니다."


def _panic(bot: Any) -> str:
    setattr(bot, "v2_manual_halt", True)
    if getattr(bot, "risk", None) is not None:
        bot.risk.halted = True
        bot.risk.halt_reason = "panic"
    return "V2 긴급 중지가 설정되었습니다. 신규 진입을 멈추고 전체 청산을 요청합니다."


def _pathb_status(bot: Any) -> str:
    pathb = getattr(bot, "pathb", None)
    if pathb is None:
        return "<b>B플랜</b>\n런타임을 사용할 수 없습니다."
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
        "<b>B플랜(클로드 가격)</b>",
        f"사용 상태: {_ko_onoff(status.get('enabled'))}",
        f"enabled: {bool(status.get('enabled'))}",
        f"운영자 허용: {_ko_onoff(status.get('operator_enabled'))}",
        f"긴급 중지: {_ko_bool(status.get('emergency_disabled'))}",
        f"모드: {_ko_pathb_mode(status.get('mode'))} / 런타임={_ko_runtime_mode(status.get('runtime_mode'))}",
        f"1회 주문금액: {int(status.get('fixed_order_krw') or 0):,}원",
        f"최대 보유: {status.get('max_positions')}개 / 하루 진입: {status.get('max_daily_entries')}회",
        f"최소 신뢰도: {status.get('min_confidence')}",
    ]
    if status.get("updated_at"):
        lines.append(f"갱신: {status.get('updated_at')} / {_ko_actor(status.get('updated_by'))}")
    if status.get("reason"):
        lines.append(f"사유: {_ko_control_reason(status.get('reason'))}")
    market_truth = ((broker_truth.get("markets") or {}).get(market) or {}) if isinstance(broker_truth, dict) else {}
    if market_truth:
        lines.append(
            f"계좌 조회 {market}: 누락={_ko_bool(market_truth.get('missing', True))} "
            f"오래됨={_ko_bool(market_truth.get('stale', True))} 보유={len(market_truth.get('positions') or [])}개 "
            f"마지막={market_truth.get('last_success_at') or '-'}"
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
            reason = str(row.get("reason") or _ko_pathb_watch_state(row.get("filter_reason")) or "")[:60]
            lines.append(
                f"{row.get('display_ticker') or row.get('ticker')} | {_ko_pathb_watch_state(row.get('state'))} | "
                f"{reason}"
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
        "LIVE_POSITION_HELD": "보유 중",
        "LIVE_POSITION_HELD_STALE": "보유 중(조회 지연)",
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
        return "B플랜 런타임을 사용할 수 없습니다."
    pathb.set_enabled(True, updated_by="telegram", reason="pathb_on")
    return "ON\nB플랜(클로드 가격) 실시간 매수/매도가 켜졌습니다."


def _pathb_off(bot: Any) -> str:
    pathb = getattr(bot, "pathb", None)
    if pathb is None:
        return "B플랜 런타임을 사용할 수 없습니다."
    pathb.set_enabled(False, updated_by="telegram", reason="pathb_off")
    return "OFF\nB플랜 신규 클로드 가격 진입이 꺼졌습니다. 기존 보호 청산은 유지됩니다."


def _pathb_kill(bot: Any) -> str:
    pathb = getattr(bot, "pathb", None)
    if pathb is None:
        return "B플랜 런타임을 사용할 수 없습니다."
    pathb.emergency_disable(updated_by="telegram", reason="pathb_kill")
    return "KILL\nB플랜 긴급 중지가 설정되었습니다. 신규 진입 중지, 대기 플랜 취소, 보유 B플랜 포지션 청산 요청을 실행합니다."


def _pathb_closeall(bot: Any, *, market: str | None = None) -> str:
    pathb = getattr(bot, "pathb", None)
    if pathb is None:
        return "B플랜 런타임을 사용할 수 없습니다."
    target_market = str(market or getattr(bot, "current_market", None) or "KR").upper()
    count = pathb.close_all_open(target_market, reason="pathb_closeall")
    return f"close-all\n{target_market} B플랜 전체 청산 요청: {count}개 포지션"
