from __future__ import annotations

import json
import os
import time
from typing import Any

from minority_report.claude_utils import extract_json, claude_response_meta, response_text, thinking_extra_body
from minority_report.prompt_contracts import PULLBACK_ZONE_RULE


def _log_buy_ready_gate(market: str, allowed: bool, reason: str, risk_context: dict | None) -> None:
    """즉시매수 게이트 판정을 시장·사유별로 한 번씩만 남긴다.

    매 judge 호출마다 찍으면 세션당 수십 줄이 되어 묻힌다. (시장, 사유) 조합이
    바뀔 때만 기록하면 "오늘 왜 한 건도 안 나왔나"를 한 줄로 답할 수 있다.
    """
    from logger import get_trading_logger

    key = f"{market}|{reason}"
    if _BUY_READY_GATE_LOGGED.get(key):
        return
    _BUY_READY_GATE_LOGGED[key] = True
    regime = str((risk_context or {}).get("market_regime") or "미상")
    get_trading_logger().info(
        f"[즉시매수 게이트] {market} allowed={allowed} 사유={reason} 국면={regime}"
    )


_BUY_READY_GATE_LOGGED: dict[str, bool] = {}

ALLOWED_ACTIONS = {"BUY_READY", "PROBE_READY", "PULLBACK_WAIT", "WAIT_RECHECK", "REJECT"}
ALLOWED_ROUTES = {"plan_a", "path_b", "wait", "reject"}
PATHB_REQUIRED_FIELDS = ("buy_zone_low", "buy_zone_high", "sell_target", "stop_loss", "hold_days", "confidence")
PATHB_BAD_DATA_QUALITY = {"minute_missing", "missing", "bad", "stale", "invalid", "fail_closed"}


# judge 입력에서 제거할 디버그성 키 — Claude에 정보 0, 토큰/혼란 비용만 (2026-06-12 실측:
# us_kis_ranking_shadow_* 등 null 키가 매 호출 전송, spread_bps 12/12 결측 상태로 전송)
_DEBUG_KEY_PATTERNS = ("shadow_error", "shadow_path", "future_fields_ignored", "cache_skipped_reason", "degraded_reason")


def _strip_empty_fields(obj):
    """None/빈문자열/빈컬렉션 및 디버그 키 제거 (재귀). 정보 있는 필드는 보존."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if any(p in str(k) for p in _DEBUG_KEY_PATTERNS):
                continue
            cleaned = _strip_empty_fields(v)
            if cleaned in (None, "", [], {}):
                continue
            out[k] = cleaned
        return out
    if isinstance(obj, list):
        return [c for c in (_strip_empty_fields(v) for v in obj) if c not in (None, "", [], {})]
    return obj


def _market_key(market: str) -> str:
    return "US" if str(market or "").upper() == "US" else "KR"


def _ticker_key(ticker: Any, market: str) -> str:
    text = str(ticker or "").strip()
    return text.upper() if _market_key(market) == "US" else text


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return float(default)
        return float(str(value).replace(",", "").replace("$", "").strip())
    except Exception:
        return float(default)


def _pct_diff(value: float, reference: float) -> float | None:
    if value <= 0 or reference <= 0:
        return None
    return (float(value) / float(reference) - 1.0) * 100.0


def _pathb_support_levels(features: dict[str, Any] | None) -> dict[str, float]:
    raw = dict(features or {})
    current = _num(raw.get("current_price"), 0.0)
    levels: dict[str, float] = {}
    anchor = _num(raw.get("anchor_price"), 0.0)
    if anchor > 0:
        levels["open_anchor"] = anchor
    vwap_distance = raw.get("vwap_distance_pct")
    if current > 0 and vwap_distance not in (None, ""):
        denom = 1.0 + (_num(vwap_distance, 0.0) / 100.0)
        if denom > 0:
            levels["estimated_vwap"] = current / denom
    pullback = raw.get("pullback_from_high_pct")
    if current > 0 and pullback not in (None, ""):
        denom = 1.0 + (_num(pullback, 0.0) / 100.0)
        if denom > 0:
            levels["estimated_open_high"] = current / denom
    return levels


def _compact(value: Any, *, max_chars: int = 1200) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        text = str(value)
    if len(text) > max_chars:
        return text[: max(0, max_chars - 3)].rstrip() + "..."
    return text


def build_single_symbol_judge_prompt(
    *,
    market: str,
    ticker: str,
    candidate: dict[str, Any],
    features: dict[str, Any] | None = None,
    strategy_feasibility: dict[str, Any] | None = None,
    risk_context: dict[str, Any] | None = None,
) -> str:
    market_key = _market_key(market)
    ticker_key = _ticker_key(ticker, market_key)
    payload = _strip_empty_fields({
        "market": market_key,
        "ticker": ticker_key,
        "candidate": candidate or {},
        "post_open_features": features or {},
        "strategy_feasibility": strategy_feasibility or {},
        "risk_context": risk_context or {},
    })
    # 즉시매수(BUY_READY) 국면 게이트 — 허용 시 프롬프트에 즉시매수 옵션 노출(2026-07-21).
    buy_ready_ok, buy_ready_reason = _immediate_buy_allowed(market_key, risk_context)
    # 사유를 반드시 남긴다. 이 값이 없으면 "왜 즉시매수가 한 번도 안 나오는가"를
    # 사후에 알 수 없다(2026-07-22: 7/14 이후 BUY_READY 로그가 0건인데 게이트에서
    # 막힌 건지 judge가 선택하지 않은 건지 구분할 수 없었다). 판정에는 영향 없다.
    try:
        _log_buy_ready_gate(market_key, buy_ready_ok, buy_ready_reason, risk_context)
    except Exception:
        pass
    if buy_ready_ok:
        action_line = "Allowed action: BUY_READY, PULLBACK_WAIT, WAIT_RECHECK, REJECT.\n"
        route_line = "Allowed route: plan_a, path_b, wait, reject.\n"
        # 2026-07-23(2차): judge는 BUY_READY 플랜이 코드 게이트(validate_immediate_buy_plan)에서
        # RR·손절폭으로 거부된다는 사실을 몰랐다. 통과 불가능한 플랜을 짜면 강등되어 매수 낭비가
        # 된다. 게이트 값은 안 바꾸고, judge에게 그 값을 알려 애초에 통과 가능한 플랜을 짜게 한다.
        min_rr = judge_min_reward_risk(market_key)
        gate_line = (
            f"BUY_READY plan gate (system rejects the plan if violated): reward/risk = "
            f"(sell_target - current) / (current - stop_loss) must be >= {min_rr:g}, and stop_loss "
            f"must sit within 3% below current (tight). Choose sell_target and stop_loss that clear "
            f"this — do not propose a plan that will be rejected.\n"
        )
        buy_ready_guide = gate_line + (
            # 2026-07-23: BUY_READY 게이트를 국면(regime)이 이미 통과시킨 뒤에만 이 guide가 붙는다.
            # 여기서 "strong regime"을 다시 요구하면 게이트가 허용한 CAUTIOUS/NEUTRAL 국면에서
            # judge가 BUY_READY를 안 고르는 게이트-프롬프트 모순이 생긴다(국면 변경 무력화).
            # 판단 기준은 국면 이름이 아니라 이 후보의 모멘텀 강도다.
            "Use BUY_READY for immediate market entry when THIS candidate shows strong continuation/"
            "momentum (regardless of the regime label — the regime is already permitted) — do NOT wait "
            "for a pullback. This is an asymmetric bet: keep stop_loss TIGHT (small % below current) so "
            "losers are cut small, and hold winners to run. Most such entries make small gains and a few "
            "run large; judge the asymmetry of the setup, not the probability that this one wins. "
            "For BUY_READY (route=plan_a) include sell_target, stop_loss, hold_days, confidence, invalid_if. "
            "buy_zone is not required for BUY_READY.\n"
            # 스키마 예시가 PULLBACK_WAIT 하나뿐이라 one-shot 앵커가 눌림 쪽으로 당긴다.
            # BUY_READY 가 허용된 경우 매수 예시를 함께 보여 앵커를 상쇄한다(2026-07-23).
            'Example BUY_READY: {"ticker":"NVDA","market":"US","action":"BUY_READY","route":"plan_a",'
            '"confidence":0.7,"reason":"sustained momentum, strong RVOL","reference_price":202.4,'
            '"sell_target":214.0,"stop_loss":199.0,"hold_days":2,"invalid_if":"loses VWAP"}\n'
        )
    else:
        action_line = "Allowed action: PULLBACK_WAIT, WAIT_RECHECK, REJECT. Do not use BUY_READY or PROBE_READY.\n"
        route_line = "Allowed route: path_b, wait, reject.\n"
        buy_ready_guide = ""
    return (
        "You are deciding whether ONE already-screened live trading candidate can enter now.\n"
        "Return JSON only. Do not include markdown.\n"
        + action_line
        + route_line
        + buy_ready_guide
        + "You may judge setup quality, route, invalidation, and price plan. You must not decide order quantity, order amount, or override broker/risk gates.\n"
        "Use PULLBACK_WAIT only when the buy zone is anchored to structural support/retest evidence such as VWAP, open anchor, opening range breakout retest, or a controlled pullback from high.\n"
        f"{PULLBACK_ZONE_RULE} If it would fill immediately, use WAIT_RECHECK instead.\n"
        "For route=path_b or action=PULLBACK_WAIT, include buy_zone_low, buy_zone_high, sell_target, stop_loss, hold_days, confidence, invalid_if, structural_basis, zone_basis.\n"
        "If post-open features are missing/stale, the support zone is unclear, reward/risk is weak, or the setup has faded, use WAIT_RECHECK or REJECT.\n"
        "Use market-native prices: KR in KRW, US in USD.\n"
        "If the setup is noisy or needs more evidence, use WAIT_RECHECK with recheck_after_min.\n"
        "If the setup should not be traded today, use REJECT.\n"
        "Input:\n"
        f"{_compact(payload, max_chars=int(os.getenv('SINGLE_SYMBOL_JUDGE_INPUT_MAX_CHARS', '6500')))}\n"
        "JSON schema:\n"
        '{"ticker":"AAPL","market":"US","action":"PULLBACK_WAIT","route":"path_b","confidence":0.72,'
        '"reason":"short reason","invalid_if":"condition","recheck_after_min":5,'
        '"reference_price":202.4,'
        '"buy_zone_low":198.2,"buy_zone_high":201.0,"sell_target":208.0,"stop_loss":195.0,"hold_days":2,'
        '"structural_basis":"VWAP retest","zone_basis":"near VWAP/open-anchor support",'
        '"chase_above":203.5,"do_not_buy_if":["condition"]}'
    )


def parse_single_symbol_judge_response(raw: str) -> dict[str, Any]:
    return extract_json(str(raw or "").strip())


def _normalize_action(raw_action: Any) -> str:
    action = str(raw_action or "").strip().upper()
    aliases = {
        "BUY": "BUY_READY",
        "PROBE": "PROBE_READY",
        "WAIT": "WAIT_RECHECK",
        "HOLD": "WAIT_RECHECK",
        "AVOID": "REJECT",
        "NO_TRADE": "REJECT",
    }
    action = aliases.get(action, action)
    return action if action in ALLOWED_ACTIONS else "WAIT_RECHECK"


def _normalize_route(raw_route: Any, action: str) -> str:
    route = str(raw_route or "").strip().lower()
    route = {
        "pathb": "path_b",
        "path_b_wait": "path_b",
        "claude_price": "path_b",
        "plan_a_buy": "plan_a",
        "watch": "wait",
    }.get(route, route)
    if route not in ALLOWED_ROUTES:
        if action == "PULLBACK_WAIT":
            return "path_b"
        if action in {"BUY_READY", "PROBE_READY"}:
            return "plan_a"
        if action == "REJECT":
            return "reject"
        return "wait"
    return route


def _pullback_wait_late_mover_block(features: dict[str, Any] | None, result: dict[str, Any]) -> str:
    """late_mover 후보의 PULLBACK_WAIT 승격 차단 (#3 soft-block floor, single_symbol_judge 측).

    repeated_failed_ready는 route 레벨 gate_info 신호라 action_routing에서 처리하고,
    여기서는 features에 존재하는 momentum_state/freshness 기반 late_mover만 막는다.
    운영자 확정 신호(2026-06-10): repeated_failed_ready + late_mover 2종.
    """
    feature_pack = dict(features or {})
    momentum = str(feature_pack.get("momentum_state") or (result or {}).get("momentum_state") or "").strip().lower()
    if momentum == "late_mover":
        return "late_mover"
    freshness = str(feature_pack.get("freshness_verdict") or (result or {}).get("freshness_verdict") or "").strip().upper()
    if freshness in {"STALE", "LATE_CHASE"}:
        return "late_mover"
    return ""


def judge_min_reward_risk(market: str) -> float:
    """Use the same PATHB policy as plan creation, submit, and reload."""

    return resolve_min_reward_risk(market)


def validate_pathb_price_plan(
    result: dict[str, Any],
    *,
    features: dict[str, Any] | None = None,
    risk_context: dict[str, Any] | None = None,
) -> list[str]:
    missing = [key for key in PATHB_REQUIRED_FIELDS if result.get(key) in (None, "")]
    errors = [f"missing_{key}" for key in missing]
    low = _num(result.get("buy_zone_low"), 0.0)
    high = _num(result.get("buy_zone_high"), 0.0)
    target = _num(result.get("sell_target"), 0.0)
    stop = _num(result.get("stop_loss") or result.get("stop_reference"), 0.0)
    hold_days = _num(result.get("hold_days"), 0.0)
    confidence = _num(result.get("confidence"), 0.0)
    feature_pack = dict(features or {})
    current = _num(feature_pack.get("current_price"), 0.0)
    strict_features = bool(features) or bool((risk_context or {}).get("pathb_plan_before_registration_only"))
    min_reward_risk = judge_min_reward_risk(
        result.get("market") or (risk_context or {}).get("market") or ""
    )
    max_zone_width_pct = _num(os.getenv("SINGLE_SYMBOL_JUDGE_MAX_ZONE_WIDTH_PCT", "2.5"), 2.5)
    max_zone_above_current_pct = _num(os.getenv("SINGLE_SYMBOL_JUDGE_MAX_ZONE_ABOVE_CURRENT_PCT", "0.35"), 0.35)
    max_zone_support_distance_pct = _num(os.getenv("SINGLE_SYMBOL_JUDGE_MAX_SUPPORT_DISTANCE_PCT", "1.8"), 1.8)
    if low <= 0:
        errors.append("buy_zone_low_nonpositive")
    if high <= 0:
        errors.append("buy_zone_high_nonpositive")
    if high and low and high < low:
        errors.append("buy_zone_high_below_low")
    if target <= high:
        errors.append("sell_target_not_above_buy_zone")
    if stop <= 0:
        errors.append("stop_loss_nonpositive")
    elif low and stop >= low:
        errors.append("stop_loss_not_below_buy_zone")
    if hold_days < 1:
        errors.append("hold_days_below_one")
    if not (0.0 < confidence <= 1.0):
        errors.append("confidence_out_of_range")
    if not str(result.get("invalid_if") or "").strip():
        errors.append("missing_invalid_if")

    if strict_features:
        quality = str(feature_pack.get("data_quality") or "").strip().lower()
        if not feature_pack:
            errors.append("missing_post_open_features_for_pathb_plan")
        if current <= 0:
            errors.append("missing_current_price_for_pathb_plan")
        if quality in PATHB_BAD_DATA_QUALITY:
            errors.append("post_open_feature_quality_fail_closed")
        if str(feature_pack.get("momentum_state") or "").strip().lower() == "fade":
            errors.append("post_open_momentum_fade")
        if feature_pack.get("opening_range_break") is False and _num(feature_pack.get("vwap_distance_pct"), -999.0) <= 0:
            errors.append("orb_failed_without_vwap_support")
        if current > 0 and high > 0:
            high_vs_current = _pct_diff(high, current)
            if high_vs_current is not None and high_vs_current > max_zone_above_current_pct:
                errors.append("buy_zone_above_current_chase_risk")
            if high_vs_current is not None and high_vs_current < -6.0:
                errors.append("buy_zone_too_far_below_current")
        if current > 0 and low > 0 and high > 0:
            zone_width_pct = ((high - low) / current) * 100.0
            if zone_width_pct > max_zone_width_pct:
                errors.append("buy_zone_too_wide")
        if high > 0 and target > high and stop > 0 and stop < high:
            reward_pct = (target / high - 1.0) * 100.0
            risk_pct = (high / stop - 1.0) * 100.0
            reward_risk = reward_pct / risk_pct if risk_pct > 0 else 0.0
            if reward_risk < min_reward_risk:
                errors.append("reward_risk_below_min")
        if low > 0 and high > 0 and current > 0:
            levels = _pathb_support_levels(feature_pack)
            center = (low + high) / 2.0
            if not levels:
                errors.append("missing_structural_support_level")
            else:
                distances = [
                    abs((center / level - 1.0) * 100.0)
                    for level in levels.values()
                    if level > 0
                ]
                if distances and min(distances) > max_zone_support_distance_pct:
                    errors.append("buy_zone_not_near_structural_support")
        if not str(result.get("structural_basis") or result.get("zone_basis") or "").strip():
            errors.append("missing_structural_basis")
    return list(dict.fromkeys(errors))


# 즉시매수(BUY_READY) 필수 필드 — 눌림존(buy_zone) 불필요. entry=현재가.
IMMEDIATE_BUY_REQUIRED_FIELDS = ("sell_target", "stop_loss", "hold_days", "confidence")


def _shadow_markets() -> set[str]:
    """즉시매수를 '관측만' 할 시장. 여기 든 시장은 실주문이 나가지 않는다."""
    return {
        m.strip().upper()
        for m in str(os.getenv("SINGLE_SYMBOL_JUDGE_BUY_READY_SHADOW_MARKETS", "") or "")
        .replace(";", ",").split(",")
        if m.strip()
    }


def _immediate_buy_allowed(market: str, risk_context: dict[str, Any] | None) -> tuple[bool, str]:
    """즉시매수(BUY_READY) 허용 여부 — env 게이트 + 시장 + 국면 조건.

    2026-07-21 운영자 방향: 눌림 폐기, 좋은 후보 사서 관리. 단 US 강세일 급등은 추세
    이어짐(+0.45%)·KR 강세는 추격 죽음(−1.30%)이라 국면·시장 차등. 기본 off(가역).
    """
    if str(os.getenv("SINGLE_SYMBOL_JUDGE_ALLOW_BUY_READY", "false") or "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return False, "buy_ready_gate_off"
    mkt = _market_key(market)
    allowed_markets = {
        m.strip().upper()
        for m in str(os.getenv("SINGLE_SYMBOL_JUDGE_BUY_READY_MARKETS", "US") or "US").replace(";", ",").split(",")
        if m.strip()
    }
    if mkt not in allowed_markets:
        # shadow 시장: 프롬프트에는 BUY_READY를 노출해 judge 판정을 관측하되,
        # 결과는 normalize 단계에서 WAIT_RECHECK로 강등되어 실주문이 나가지 않는다.
        # KR은 즉시매수가 한 번도 관측된 적이 없다 — 금지 근거는 judge가 '거부한' 건이
        # 나빴다는 것이고(WAIT_RECHECK 건 반사실 -1.557%), 그건 judge가 '승인한' 건이
        # 나쁘다는 증거가 아니다(US는 거부 -1.075% vs 승인 +2.89%로 정반대였다).
        # 거래가 없어 데이터가 안 쌓이는 교착을 리스크 0으로 깨기 위한 경로다.
        if mkt in _shadow_markets():
            return True, "buy_ready_shadow_observe"
        return False, "buy_ready_market_not_allowed"
    regime = str((risk_context or {}).get("market_regime") or "").strip().upper()
    # 실제 consensus 국면 체계(minority_report/consensus.py STANCE_SCORE): 강세=AGGRESSIVE(1.0)·
    # MODERATE_BULL(0.7)·MILD_BULL(0.4). RISK_ON/STRONG_BULL/BULL은 존재하지 않는 값(2026-07-21 누수수정).
    allowed_regimes = {
        r.strip().upper()
        for r in str(os.getenv("SINGLE_SYMBOL_JUDGE_BUY_READY_REGIMES", "MILD_BULL,MODERATE_BULL,AGGRESSIVE") or "").replace(";", ",").split(",")
        if r.strip()
    }
    if allowed_regimes and regime and regime not in allowed_regimes:
        return False, f"buy_ready_regime_blocked:{regime}"
    if allowed_regimes and not regime:
        # 국면 미상이면 보수적으로 차단(fail-closed) — 강세 확인 못 하면 즉시매수 금지
        return False, "buy_ready_regime_unknown"
    return True, "buy_ready_allowed"


def validate_immediate_buy_plan(
    result: dict[str, Any],
    *,
    features: dict[str, Any] | None = None,
    risk_context: dict[str, Any] | None = None,
) -> list[str]:
    """즉시매수(BUY_READY) 검증 — 눌림존 협착 없이 손절 짧게·target·RR·품질만.

    entry=현재가(즉시 체결). 손절은 짧게 유지(IMMEDIATE_BUY_MAX_STOP_PCT), 러너로 이익 관리.
    """
    errors: list[str] = []
    fp = dict(features or {})
    # 현재가 조회 fallback — 즉시매수는 buy_zone 구조 검증이 없고 현재가는 target/stop의
    # 방향·거리를 재는 기준점일 뿐이다. judge 입력 후보에는 가격이 있는데 post_open_features만
    # 결측인 경우가 있어(2026-07-21 AMAT·HUT 실측: candidate.price=565.4999인데 features 없음)
    # 그 한 필드 때문에 완전한 플랜이 탈락했다. 실제 진입 tp/sl은 주문 시점 실시간가로 다시
    # 계산되고 손절은 하드캡으로 clamp되므로, 여기서는 사전 타당성 확인이면 충분하다.
    # 모든 fallback이 비면 아래 strict 검사가 그대로 차단한다(fail-closed 유지).
    current = _num(fp.get("current_price"), 0.0)
    if current <= 0:
        current = _num(fp.get("price"), 0.0)
    if current <= 0:
        current = _num(result.get("reference_price"), 0.0)
    target = _num(result.get("sell_target"), 0.0)
    stop = _num(result.get("stop_loss") or result.get("stop_reference"), 0.0)
    hold_days = _num(result.get("hold_days"), 0.0)
    confidence = _num(result.get("confidence"), 0.0)
    strict = bool(features) or bool((risk_context or {}).get("pathb_plan_before_registration_only"))
    min_reward_risk = judge_min_reward_risk(
        result.get("market") or (risk_context or {}).get("market") or ""
    )
    for key in IMMEDIATE_BUY_REQUIRED_FIELDS:
        if result.get(key) in (None, ""):
            errors.append(f"missing_{key}")
    if not str(result.get("invalid_if") or "").strip():
        errors.append("missing_invalid_if")
    if target <= 0:
        errors.append("sell_target_nonpositive")
    if stop <= 0:
        errors.append("stop_loss_nonpositive")
    if hold_days < 1:
        errors.append("hold_days_below_one")
    if not (0.0 < confidence <= 1.0):
        errors.append("confidence_out_of_range")
    if strict and current <= 0:
        errors.append("missing_current_price_for_immediate_buy")
    if current > 0:
        if target > 0 and target <= current:
            errors.append("sell_target_not_above_current")
        if stop > 0 and stop >= current:
            errors.append("stop_loss_not_below_current")
        if stop > 0 and stop < current:
            stop_dist_pct = (current - stop) / current * 100.0
            max_stop_pct = _num(os.getenv("IMMEDIATE_BUY_MAX_STOP_PCT", "3.0"), 3.0)
            if stop_dist_pct > max_stop_pct:
                errors.append("stop_loss_too_wide")  # 손절 짧게 원칙 — 넓은 손절 금지
        if target > current and stop > 0 and stop < current:
            reward_risk = (target - current) / (current - stop)
            if reward_risk < min_reward_risk:
                errors.append("reward_risk_below_min")
    if strict:
        quality = str(fp.get("data_quality") or "").strip().lower()
        if quality in PATHB_BAD_DATA_QUALITY:
            errors.append("post_open_feature_quality_fail_closed")
        if str(fp.get("momentum_state") or "").strip().lower() == "fade":
            errors.append("post_open_momentum_fade")
    return list(dict.fromkeys(errors))


def normalize_single_symbol_judge_result(
    result: dict[str, Any],
    *,
    features: dict[str, Any] | None = None,
    risk_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    raw = dict(result or {})
    market = _market_key(str(raw.get("market") or ""))
    ticker = _ticker_key(raw.get("ticker"), market)
    action = _normalize_action(raw.get("action"))
    route = _normalize_route(raw.get("route"), action)
    # 즉시매수(BUY_READY) 국면 게이트(2026-07-21): 허용되면 plan_a로 유지, 아니면 현행 강등.
    buy_ready_ok, buy_ready_reason = _immediate_buy_allowed(market, risk_context)
    if action == "BUY_READY" and buy_ready_ok:
        route = "plan_a"
    elif route == "path_b" and action in {"BUY_READY", "PROBE_READY"}:
        action = "PULLBACK_WAIT"
    elif action in {"BUY_READY", "PROBE_READY"} and not buy_ready_ok:
        # 게이트 off/국면 불가 시 즉시매수 요청을 눌림 대기로 강등(현행 안전 동작 보존)
        action = "PULLBACK_WAIT"
        route = "path_b"
    if action == "PULLBACK_WAIT":
        route = "path_b"
    if action == "WAIT_RECHECK":
        route = "wait"
    if action == "REJECT":
        route = "reject"

    pullback_soft_block_reason = ""
    pullback_soft_block_enforced = False
    if action == "PULLBACK_WAIT":
        pullback_soft_block_reason = _pullback_wait_late_mover_block(features, raw)
        if pullback_soft_block_reason:
            gate_mode = str(os.getenv("PULLBACK_WAIT_SOFT_BLOCK_GATE_MODE", "enforce") or "enforce").strip().lower()
            pullback_soft_block_enforced = gate_mode in {"live", "enforce", "block"}
            if pullback_soft_block_enforced:
                # late_mover는 per-cycle transient 분류이므로 REJECT(장기 TTL 락아웃)가 아니라
                # WAIT_RECHECK로 보류한다. action_routing의 WATCH 강등과 동일하게 다음 사이클
                # 재평가를 허용하고, registration만 막는다.
                action = "WAIT_RECHECK"
                route = "wait"

    raw_valid = raw.get("valid")
    errors = list(raw.get("errors") or []) if isinstance(raw.get("errors"), list) else []
    out = {
        **raw,
        "ticker": ticker,
        "market": market,
        "action": action,
        "route": route,
        "confidence": max(0.0, min(1.0, _num(raw.get("confidence"), 0.0))),
        "reason": str(raw.get("reason") or "").strip(),
        "invalid_if": str(raw.get("invalid_if") or raw.get("invalidation_condition") or "").strip(),
        "recheck_after_min": max(0.0, _num(raw.get("recheck_after_min"), 0.0)),
        "valid": bool(raw_valid) if raw_valid is not None else True,
        "errors": errors,
    }
    if raw.get("audit_reason") not in (None, ""):
        out["audit_reason"] = str(raw.get("audit_reason") or "")
    if raw.get("stop_loss") in (None, "") and raw.get("stop_reference") not in (None, ""):
        out["stop_loss"] = raw.get("stop_reference")
    if route == "path_b":
        errors = validate_pathb_price_plan(out, features=features, risk_context=risk_context)
        if errors:
            out["valid"] = False
            out["errors"] = errors
            out["action"] = "WAIT_RECHECK"
            out["route"] = "wait"
            out["reason"] = out["reason"] or "invalid_pathb_price_plan"
            out["audit_reason"] = "early_judge_pathb_price_plan_missing" if any(
                error.startswith("missing_") for error in errors
            ) else "early_judge_pathb_price_plan_invalid"
    elif route == "plan_a" and action == "BUY_READY":
        # 즉시매수 검증(2026-07-21): 눌림존 없이 손절 짧게·target·RR·품질. 실패 시 눌림 대기로
        # 강등(현행 안전 경로)이 아니라 WAIT_RECHECK — 즉시매수 부적합이면 다음 사이클 재평가.
        errors = validate_immediate_buy_plan(out, features=features, risk_context=risk_context)
        if errors:
            out["valid"] = False
            out["errors"] = errors
            out["action"] = "WAIT_RECHECK"
            out["route"] = "wait"
            out["reason"] = out["reason"] or "invalid_immediate_buy_plan"
            out["audit_reason"] = "early_judge_immediate_buy_missing" if any(
                error.startswith("missing_") for error in errors
            ) else "early_judge_immediate_buy_invalid"
        else:
            out["immediate_buy_gate"] = "allowed"
        # ★ shadow 시장은 검증 통과 여부와 무관하게 여기서 실주문 경로를 끊는다.
        #   프롬프트 노출(관측)과 주문 실행을 분리하는 유일한 지점이므로 절대 제거하지 말 것.
        #   판정 내용은 immediate_buy_shadow_*에 보존해 사후 반사실 검증에 쓴다.
        if _market_key((risk_context or {}).get("market") or out.get("market")) in _shadow_markets():
            out["immediate_buy_shadow"] = True
            out["immediate_buy_shadow_valid"] = bool(not out.get("errors"))
            out["immediate_buy_shadow_plan"] = {
                "reference_price": out.get("reference_price"),
                "sell_target": out.get("sell_target"),
                "stop_loss": out.get("stop_loss"),
                "confidence": out.get("confidence"),
                "reason": out.get("reason"),
                # 국면을 함께 남긴다. shadow 경로는 시장 체크에서 바로 통과하므로
                # 국면 게이트를 우회한다 — US는 강세 국면에서만 BUY_READY가 노출되는데
                # shadow 시장은 전 국면에서 관측된다. 나중에 US와 비교하거나 enforce를
                # 판단할 때 국면을 맞춰 걸러야 하므로 기록 자체에 넣어둔다.
                "market_regime": str((risk_context or {}).get("market_regime") or ""),
            }
            out["immediate_buy_gate"] = "shadow_observe"
            out["action"] = "WAIT_RECHECK"
            out["route"] = "wait"
            out["audit_reason"] = "immediate_buy_shadow_observed"
    if pullback_soft_block_reason:
        out["pullback_wait_soft_block_reason"] = pullback_soft_block_reason
        out["pullback_wait_soft_block_enforced"] = pullback_soft_block_enforced
        if pullback_soft_block_enforced:
            out["audit_reason"] = f"pullback_wait_soft_block:{pullback_soft_block_reason}"
            if not out["reason"]:
                out["reason"] = f"pullback_wait_soft_block:{pullback_soft_block_reason}"
    return out


def call_single_symbol_judge(
    *,
    market: str,
    ticker: str,
    candidate: dict[str, Any],
    features: dict[str, Any] | None = None,
    strategy_feasibility: dict[str, Any] | None = None,
    risk_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import anthropic
    from credit_tracker import record as credit_record
    from minority_report.raw_call_logger import save as save_raw_call

    # judge 전용 모델 오버라이드 (2026-06-12 운영자 승인 — A/B: Opus 플랜 산출 8/10 vs Sonnet 2/10,
    # 존 깊이 -0.88% vs -0.49%, 기하rr 2.4 vs 1.8, 지연 5.5s vs 9.1s). 언제든 env로 전환 가능.
    model = os.getenv("SINGLE_SYMBOL_JUDGE_MODEL", "") or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    max_tokens = int(float(os.getenv("SINGLE_SYMBOL_JUDGE_MAX_TOKENS", "900") or 900))
    prompt = build_single_symbol_judge_prompt(
        market=market,
        ticker=ticker,
        candidate=candidate,
        features=features or {},
        strategy_feasibility=strategy_feasibility or {},
        risk_context=risk_context or {},
    )
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
    started = time.perf_counter()
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
        extra_body=thinking_extra_body("single_symbol_judge"),
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    raw = response_text(resp)
    parse_error = False
    try:
        parsed = parse_single_symbol_judge_response(raw)
        normalized = normalize_single_symbol_judge_result(parsed, features=features or {}, risk_context=risk_context or {})
    except Exception as exc:
        parse_error = True
        normalized = {
            "ticker": _ticker_key(ticker, market),
            "market": _market_key(market),
            "action": "WAIT_RECHECK",
            "route": "wait",
            "valid": False,
            "errors": [f"parse_error:{type(exc).__name__}"],
            "reason": "single_symbol_judge_parse_failed",
        }
    try:
        credit_record(
            resp.usage.input_tokens, resp.usage.output_tokens, "single_symbol_judge", model=model,
            cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        )
    except Exception:
        pass
    try:
        _cm = claude_response_meta(resp)
        save_raw_call(
            label="single_symbol_judge",
            prompt=prompt,
            raw_response=raw,
            parsed=normalized,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            duration_ms=duration_ms,
            market=_market_key(market),
            model=model,
            parse_error=parse_error,
            parse_stage="single_symbol_judge_v1",
            prompt_version="single_symbol_judge_v1",
            cache_creation_input_tokens=_cm["cache_creation_input_tokens"],
            cache_read_input_tokens=_cm["cache_read_input_tokens"],
            request_id=_cm["request_id"],
            service_tier=_cm["service_tier"],
            extra={"ticker": _ticker_key(ticker, market)},
        )
    except Exception:
        pass
    return normalized
from execution.reward_risk_policy import resolve_min_reward_risk
