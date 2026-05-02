"""
minority_report/hold_advisor.py — TP 도달 시 분석가 3명 HOLD/SELL 합의

TRAILING_ANALYST_ENABLED=true 일 때만 호출됨.
기본값 false → 트레일링 스탑 즉시 활성화.
"""
import os
import json
import time
import anthropic
from datetime import datetime
from pathlib import Path
from typing import Optional
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_trading_logger
from minority_report.claude_utils import extract_json
from credit_tracker import record as credit_record
from runtime_paths import get_runtime_path
from minority_report.raw_call_logger import save as save_raw_call
from minority_report.prompt_contracts import COMMON_DECISION_CONTRACT, HARD_SOFT_RULE_CONTRACT

try:
    from phase1_trainer.digest_builder import build_intraday_advisor_context as _build_rt_ctx
    _RT_CTX_AVAILABLE = True
except Exception:
    _RT_CTX_AVAILABLE = False

log    = get_trading_logger()
client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
MODEL  = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

PERSONAS = {
    "bull": "당신은 15년 경력의 성장주 모멘텀 트레이더입니다. 추세가 살아있으면 보유를 선호합니다.",
    "bear": "당신은 헤지펀드 리스크 매니저입니다. 이익 실현 타이밍을 중시하고 욕심을 경계합니다.",
    "neutral": "당신은 퀀트 통계 분석가입니다. 데이터 기반으로 냉정하게 판단합니다.",
}

PERSONA_FOCUS = {
    "bull": "Focus on upside continuation, trend persistence, and whether remaining reward justifies holding.",
    "bear": "Focus on downside risk, event risk, and whether open profit should be protected now.",
    "neutral": "Focus on ATR/statistical fit, peak-to-current drawdown, and expected value of holding.",
}

TRAIL_GUIDE = """Trail guide:
- 0.02 = tight protection; use when profit has reached target and momentum is fading or giveback risk is high.
- 0.03 = normal protection; use when signals are mixed and volatility is ordinary.
- 0.04 = wider room; use when trend is intact but normal pullbacks are likely.
- 0.05 = widest room; use only for strong trend continuation with high noise, not for weak positions."""

HOLD_DECISION_STAGES = {
    "TP_REVIEW",
    "PRE_SESSION",
    "INTRADAY_REVIEW",
    "MAX_HOLD",
    "PRE_CLOSE_CARRY",
    "SOFT_EXIT",
    "MANUAL_REVIEW",
}

STAGE_DEFAULT_POLICIES = {
    "TP_REVIEW": "SELL unless a trend-continuation exception justifies trailing.",
    "PRE_SESSION": "HOLD unless overnight or pre-session risk is broken.",
    "INTRADAY_REVIEW": "HOLD unless risk/reward has deteriorated or thesis is invalid.",
    "MAX_HOLD": "SELL unless there is a clear one-review carry exception.",
    "PRE_CLOSE_CARRY": "SELL unless broker-truth is trusted and carry risk is acceptable.",
    "SOFT_EXIT": "SELL unless the soft exit is premature and risk is protected.",
    "MANUAL_REVIEW": "HOLD unless the supplied review context supports SELL.",
}


def _normalize_stage(decision_stage: Optional[str]) -> str:
    stage = str(decision_stage or "TP_REVIEW").strip().upper()
    return stage if stage in HOLD_DECISION_STAGES else "MANUAL_REVIEW"


def _stage_policy(decision_stage: str, default_policy: Optional[str] = None) -> str:
    return str(default_policy or STAGE_DEFAULT_POLICIES.get(decision_stage, STAGE_DEFAULT_POLICIES["MANUAL_REVIEW"]))


def _fallback_vote(reason: str, decision_stage: str = "TP_REVIEW", default_policy: str = "") -> dict:
    stage = _normalize_stage(decision_stage)
    return {
        "action": "HOLD",
        "confidence": 0.0,
        "trail_pct": 0.03,
        "sell_urgency": "wait",
        "protective_stop": 0.0,
        "next_review_min": 30,
        "invalid_if": "",
        "reason": reason,
        "fallback": True,
        "decision_stage": stage,
        "default_policy": default_policy or _stage_policy(stage),
    }


def _coerce_vote(result: dict, decision_stage: str = "TP_REVIEW", default_policy: str = "") -> dict:
    stage = _normalize_stage(decision_stage)
    action = str((result or {}).get("action", "HOLD") or "HOLD").strip().upper()
    if action not in {"HOLD", "SELL"}:
        action = "HOLD"
    try:
        confidence = float((result or {}).get("confidence", 0.0) or 0.0)
    except Exception:
        confidence = 0.0
    try:
        trail_pct = float((result or {}).get("trail_pct", 0.03) or 0.03)
    except Exception:
        trail_pct = 0.03
    sell_urgency = str((result or {}).get("sell_urgency", "") or "").strip().lower()
    if sell_urgency not in {"now", "next_open", "wait"}:
        sell_urgency = "now" if action == "SELL" else "wait"
    try:
        protective_stop = float((result or {}).get("protective_stop", 0.0) or 0.0)
    except Exception:
        protective_stop = 0.0
    try:
        next_review_min = int(float((result or {}).get("next_review_min", 30) or 30))
    except Exception:
        next_review_min = 30
    return {
        "action": action,
        "confidence": max(0.0, min(1.0, confidence)),
        "trail_pct": max(0.02, min(0.05, trail_pct)),
        "sell_urgency": sell_urgency,
        "protective_stop": max(0.0, protective_stop),
        "next_review_min": max(5, min(240, next_review_min)),
        "invalid_if": str((result or {}).get("invalid_if", "") or "")[:240],
        "reason": str((result or {}).get("reason", "") or ""),
        "fallback": bool((result or {}).get("fallback", False)),
        "decision_stage": stage,
        "default_policy": default_policy or _stage_policy(stage),
    }


def _ask_one(analyst_type: str, pos: dict, market: str,
             digest_prompt: str, rt_context: str = "",
             decision_stage: str = "TP_REVIEW",
             default_policy: Optional[str] = None,
             minutes_to_close: Optional[float] = None,
             force_exit_window: bool = False) -> dict:
    decision_stage = _normalize_stage(decision_stage)
    default_policy_text = _stage_policy(decision_stage, default_policy)
    # entry: open_positions(KRW) 우선, 없으면 display_avg_price(USD) 폴백
    entry = float(pos.get("entry", 0) or 0)
    if entry <= 0:
        entry = float(pos.get("avg_price", 0) or pos.get("display_avg_price", 0) or 0)
    if entry <= 0:
        raise ValueError(f"[hold_advisor] entry=0 — 진입가 미확정, 호출 불가 ({pos.get('ticker','-')})")

    # US: display_avg_price(USD) 기준으로 표시, KRW 환산값을 괄호에 병기
    # KR: KRW 단위 그대로 표시
    disp_entry = float(pos.get("display_avg_price", 0) or 0)
    disp_cp    = float(pos.get("display_current_price", 0) or 0)
    # USD/KRW 환율: entry(KRW) / disp_entry(USD)로 역산
    fx_rate = (entry / disp_entry) if (market == "US" and disp_entry > 0) else 0.0

    if market == "US" and disp_entry > 0:
        show_entry = disp_entry
        show_cp    = disp_cp if disp_cp > 0 else float(pos.get("current_price", entry) or entry) / fx_rate
        show_tp    = round(float(pos.get("tp", 0) or 0) / fx_rate, 2)
        show_sl    = round(float(pos.get("sl", 0) or 0) / fx_rate, 2)
        show_trail = round(float(pos.get("trail_sl", 0) or 0) / fx_rate, 2)
        ccy = "USD"
        # KRW 환산값 (괄호 병기용)
        krw_entry  = int(entry)
        krw_cp     = int(show_cp * fx_rate)
        krw_tp     = int(float(pos.get("tp", 0) or 0))
        krw_sl     = int(float(pos.get("sl", 0) or 0))
        krw_trail  = int(float(pos.get("trail_sl", 0) or 0))
        def _p(usd, krw): return f"${usd:,.2f} (≈{krw:,}원)" if krw > 0 else f"${usd:,.2f}"
    else:
        show_entry = entry
        show_cp    = float(pos.get("current_price", entry) or entry)
        show_tp    = float(pos.get("tp", 0) or 0)
        show_sl    = float(pos.get("sl", 0) or 0)
        show_trail = float(pos.get("trail_sl", 0) or 0)
        ccy = "KRW"
        krw_entry = krw_cp = krw_tp = krw_sl = krw_trail = 0
        def _p(val, _krw=0): return f"{val:,.0f}원"

    cp      = show_cp
    pnl_pct = (show_cp / show_entry - 1) * 100 if show_entry else 0
    ticker  = pos.get("ticker", "-")
    strat   = pos.get("strategy", "-")
    held    = pos.get("held_days", 0)
    # 장중 보유시간(분) 계산
    held_min: Optional[int] = None
    _entry_time = pos.get("entry_time")
    if _entry_time:
        try:
            from datetime import datetime as _dt
            _et = _dt.fromisoformat(_entry_time)
            _now = _dt.now(_et.tzinfo) if _et.tzinfo is not None else _dt.now()
            held_min = max(0, int((_now - _et).total_seconds() / 60))
        except Exception:
            pass
    peak_pnl_pct = float(pos.get("peak_pnl_pct") or 0)
    mode_str = pos.get("mode", "")
    tp      = show_tp
    sl      = show_sl
    trailing = bool(pos.get("trailing", False))
    trail_sl = show_trail
    tp_triggered = bool(pos.get("tp_triggered", False))
    status_bits = []
    if tp > 0:
        status_bits.append(f"TP={_p(tp, krw_tp)}")
    if sl > 0:
        status_bits.append(f"SL={_p(sl, krw_sl)}")
    if tp_triggered:
        status_bits.append("TP 도달 상태")
    if trailing:
        _tr_str = f"트레일링 활성(trail_sl={_p(trail_sl, krw_trail)})" if trail_sl > 0 else "트레일링 활성"
        status_bits.append(_tr_str)
    status_line = " / ".join(status_bits) if status_bits else "별도 TP/SL 상태 정보 없음"

    # 진입가/현재가 표시 (US: USD + KRW 병기)
    if market == "US":
        entry_str = _p(show_entry, krw_entry)
        cp_str    = _p(show_cp, krw_cp)
    else:
        entry_str = f"{show_entry:,.0f}원"
        cp_str    = f"{show_cp:,.0f}원"

    # 보유시간 표시: 장중이면 분 단위, 아니면 일 단위
    if held_min is not None:
        held_str = f"{held_min}분" if held_min < 1440 else f"{held}일 {held_min % 1440}분"
    else:
        held_str = f"{held}일"
    # 고점 대비 현재 이격
    drawdown_str = ""
    if peak_pnl_pct > 0 and pnl_pct < peak_pnl_pct:
        dd = peak_pnl_pct - pnl_pct
        drawdown_str = f"  고점 수익률: {peak_pnl_pct:+.2f}%  (현재 고점 대비 -{dd:.2f}%p 하락)\n"
    elif peak_pnl_pct > 0:
        drawdown_str = f"  고점 수익률: {peak_pnl_pct:+.2f}%  (현재 고점 유지)\n"
    mode_line = f"  시장 모드: {mode_str}\n" if mode_str else ""

    context_text = rt_context or (digest_prompt[:300] if digest_prompt else "  (정보 없음)")

    prompt = f"""{PERSONAS[analyst_type]}

{COMMON_DECISION_CONTRACT}
{HARD_SOFT_RULE_CONTRACT}

목표가에 도달한 포지션을 계속 보유할지 판단하세요.

━━━ 포지션 ━━━
  종목: {ticker} ({market}, {ccy})  전략: {strat}
  진입가: {entry_str}  현재가: {cp_str}  수익률: {pnl_pct:+.2f}%
  보유시간: {held_str}
{drawdown_str}{mode_line}  포지션 상태: {status_line}

━━━ 현재 시장 (실시간) ━━━
{context_text}

HOLD(보유) 또는 SELL(청산) 중 하나를 선택하고,
HOLD 시 트레일링 폭(trail_pct: 0.02~0.05)을 제안하세요.

Decision stage:
- decision_stage: {decision_stage}
- default_policy: {default_policy_text}
- minutes_to_close: {minutes_to_close if minutes_to_close is not None else "unknown"}
- force_exit_window: {bool(force_exit_window)}
- System hard exits override any HOLD output.

Perspective focus:
{PERSONA_FOCUS.get(analyst_type, "")}

{TRAIL_GUIDE}

JSON으로만 응답:
{{
  "action": "HOLD" or "SELL",
  "confidence": 0.0~1.0,
  "sell_urgency": "now|next_open|wait",
  "trail_pct": 0.03,
  "protective_stop": 0.0,
  "next_review_min": 30,
  "invalid_if": "price loses VWAP",
  "reason": "한 문장"
}}"""

    try:
        resp = client.messages.create(
            model=MODEL, max_tokens=700,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        result = extract_json(raw)
        credit_record(resp.usage.input_tokens, resp.usage.output_tokens, "hold_advisor", model=MODEL)
        save_raw_call(
            label=f"hold_advisor_{analyst_type}",
            prompt=prompt, raw_response=raw, parsed=result,
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            market=market,
            model=MODEL,
            prompt_version="hold_advisor_v3",
            extra={"decision_stage": decision_stage, "default_policy": default_policy_text},
        )
        return _coerce_vote(result, decision_stage=decision_stage, default_policy=default_policy_text)
    except Exception as e:
        log.warning(f"[hold_advisor:{analyst_type}] 오류 → HOLD fallback: {e}")
        return _fallback_vote("error", decision_stage=decision_stage, default_policy=default_policy_text)


def ask(
    pos: dict,
    market: str,
    digest_prompt: str = "",
    delay: float = 0.5,
    decision_stage: str = "TP_REVIEW",
    default_policy: Optional[str] = None,
    minutes_to_close: Optional[float] = None,
    force_exit_window: bool = False,
) -> dict:
    """
    분석가 3명 합의 → HOLD/SELL 결정.

    Returns
    -------
    {
        "action": "HOLD" | "SELL",
        "trail_pct": 0.03,
        "votes": {"bull": ..., "bear": ..., "neutral": ...},
    }
    """
    ticker  = pos.get("ticker", "-")
    decision_stage = _normalize_stage(decision_stage or pos.get("decision_stage"))
    default_policy_text = _stage_policy(decision_stage, default_policy or pos.get("default_policy"))
    if minutes_to_close is None and pos.get("minutes_to_close") not in (None, ""):
        try:
            minutes_to_close = float(pos.get("minutes_to_close"))
        except Exception:
            minutes_to_close = None

    # entry=0이면 Claude가 "데이터 오류"로 일관되게 SELL 판단 → 의미없는 호출 차단
    _entry = float(pos.get("entry", 0) or 0)
    if _entry <= 0:
        _entry = float(pos.get("avg_price", 0) or pos.get("display_avg_price", 0) or 0)
    if _entry <= 0:
        log.warning(f"[hold_advisor] {ticker} entry=0 → 호출 차단 (진입가 미확정), HOLD 반환")
        return {
            "action": "HOLD",
            "trail_pct": 0.03,
            "votes": {},
            "confidence": 0.0,
            "decision_stage": decision_stage,
            "default_policy": default_policy_text,
        }

    # 실시간 컨텍스트 1회만 조회 (3명이 공유)
    rt_ctx = ""
    if _RT_CTX_AVAILABLE:
        try:
            result = _build_rt_ctx(market)
            if isinstance(result, dict) and result.get("ok"):
                rt_ctx = result["text"]
        except Exception:
            pass

    votes   = {}
    for atype in ("bull", "bear", "neutral"):
        votes[atype] = _ask_one(
            atype,
            pos,
            market,
            digest_prompt,
            rt_ctx,
            decision_stage=decision_stage,
            default_policy=default_policy_text,
            minutes_to_close=minutes_to_close,
            force_exit_window=force_exit_window,
        )
        time.sleep(delay)

    hold_score = sum(
        v["confidence"] for v in votes.values() if v["action"] == "HOLD"
    )
    sell_score = sum(
        v["confidence"] for v in votes.values() if v["action"] == "SELL"
    )
    action = "SELL" if sell_score > hold_score and sell_score >= 0.7 else "HOLD"

    # trail_pct: HOLD 투표한 분석가들의 평균
    hold_voters = [v for v in votes.values() if v["action"] == "HOLD"]
    trail_pct   = (
        sum(v["trail_pct"] for v in hold_voters) / len(hold_voters)
        if hold_voters else 0.03
    )
    action_voters = [v for v in votes.values() if v["action"] == action]
    confidence = max((float(v.get("confidence", 0.0) or 0.0) for v in action_voters), default=0.0)
    sell_urgency = "wait"
    if action == "SELL":
        urgencies = [str(v.get("sell_urgency", "") or "") for v in action_voters]
        sell_urgency = "now" if "now" in urgencies else ("next_open" if "next_open" in urgencies else "wait")
    protective_stop = max((float(v.get("protective_stop", 0.0) or 0.0) for v in votes.values()), default=0.0)
    next_review_min = min((int(v.get("next_review_min", 30) or 30) for v in votes.values()), default=30)
    reason = ""
    invalid_if = ""
    for vote in action_voters:
        if not reason and vote.get("reason"):
            reason = str(vote.get("reason", ""))[:500]
        if not invalid_if and vote.get("invalid_if"):
            invalid_if = str(vote.get("invalid_if", ""))[:240]

    log.info(
        f"[hold_advisor] {ticker} → {action} "
        f"(HOLD {hold_score:.2f} vs SELL {sell_score:.2f}) trail={trail_pct:.2f}"
    )

    # ── 결정 시점 JSONL 기록 ──────────────────────────────────────────────────
    _log_decision(ticker, market, pos, action, trail_pct, votes, decision_stage, default_policy_text)

    return {
        "action": action,
        "trail_pct": round(trail_pct, 3),
        "votes": votes,
        "confidence": round(confidence, 4),
        "sell_urgency": sell_urgency,
        "protective_stop": protective_stop,
        "next_review_min": next_review_min,
        "reason": reason,
        "invalid_if": invalid_if,
        "decision_stage": decision_stage,
        "default_policy": default_policy_text,
    }


def _log_decision(ticker: str, market: str, pos: dict,
                  action: str, trail_pct: float, votes: dict,
                  decision_stage: str = "TP_REVIEW",
                  default_policy: str = ""):
    """hold_advisor 결정을 JSONL 파일에 기록"""
    try:
        log_dir = get_runtime_path("logs", "hold_advisor", make_parents=False)
        log_dir.mkdir(parents=True, exist_ok=True)
        today   = datetime.now().strftime("%Y-%m-%d")
        log_file = log_dir / f"decisions_{today}.jsonl"

        entry_price = float(pos.get("entry", 0) or 0)
        current_price = float(pos.get("current_price", 0) or 0)
        tp_price = float(pos.get("tp", 0) or 0)
        price_currency = "KRW"
        if str(market or "").upper() == "US":
            display_entry = float(pos.get("display_avg_price", 0) or 0)
            display_current = float(pos.get("display_current_price", 0) or 0)
            if display_entry > 0:
                fx_rate = (entry_price / display_entry) if entry_price > 0 else 0.0
                entry_price = display_entry
                if display_current > 0:
                    current_price = display_current
                elif current_price > 1000 and fx_rate > 0:
                    current_price = current_price / fx_rate
                display_tp = float(pos.get("display_tp_price", 0) or 0)
                if display_tp > 0:
                    tp_price = display_tp
                elif tp_price > 1000 and fx_rate > 0:
                    tp_price = tp_price / fx_rate
                price_currency = "USD"

        entry = {
            "ts":         datetime.now().isoformat(timespec="seconds"),
            "ticker":     ticker,
            "market":     market,
            "entry":      entry_price,
            "tp_price":   tp_price,
            "current":    current_price,
            "price_currency": price_currency,
            "pnl_pct":    round((current_price / entry_price - 1) * 100, 3) if entry_price and current_price else 0.0,
            "held_days":  pos.get("held_days", 0),
            "decision":   action,
            "decision_stage": decision_stage,
            "default_policy": default_policy,
            "trail_pct":  trail_pct,
            "votes": {k: {"action": v["action"], "confidence": v["confidence"],
                          "reason": v["reason"]} for k, v in votes.items()},
            "outcome":    None,   # 청산 후 채워짐
        }
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning(f"[hold_advisor] 결정 로그 기록 실패: {e}")
