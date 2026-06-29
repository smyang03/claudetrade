"""minority_report/postmortem.py - 장 마감 후 사후 분석

변경 이력:
- trade_log 파라미터 추가 → 당일 체결 내역을 Claude에게 전달
- 전략별 성과 자동 집계 → BrainDB.update_strategy_performance()
- judgment_log에 trade_log + postmortem 원본 보존 (파인튜닝 raw 데이터)
- best_trade / worst_trade / worst_trade_reason 필드 추가
- HALT / 거래 없는 날 postmortem 스킵 안전장치
"""
import os, json, re, sys, time, uuid
import anthropic
from pathlib import Path
from typing import Any
sys.path.insert(0, str(Path(__file__).parent.parent))
from logger import get_judgment_logger, get_minority_logger
from claude_memory import brain as BrainDB
from credit_tracker import record as credit_record
from minority_report.consensus import is_available_judgment
from minority_report.raw_call_logger import save as save_raw_call

log          = get_minority_logger()
judgment_log = get_judgment_logger()
client       = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
MODEL        = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

_POSTMORTEM_PLACEHOLDER_LESSONS = {
    "오류로 자동 보정",
    "API 오류로 자동 보정",
    "postmortem 응답 실패",
    "HALT 세션 또는 거래 없음",
}


def _postmortem_fresh_brain_active() -> bool:
    """사후분석 프롬프트에 레거시 brain summary를 주입할지 결정.

    시장판단(_brain_context_for_judge)·selection(_v2_fresh_brain_selection_active)은
    이미 동일 정책으로 레거시 brain/correction_guide를 차단하는데, postmortem 경로에만
    가드가 빠져 stale brain 요약(예: 급락장 시점 correction_guide 시대의 누적 요약)이
    매 사후분석에 계속 주입되던 누락을 메운다. 단일 출처(analysts) 정책을 재사용한다.
    import 실패 시 현행 동작(주입)을 유지해 회귀를 만들지 않는다.
    """
    try:
        from minority_report.analysts import _v2_fresh_brain_selection_active
        return _v2_fresh_brain_selection_active()
    except Exception:
        return False


def _append_lesson_candidate(
    market: str, date: str, key_lesson: str,
    bull_result: str, excluded: bool, consensus_hit: str = ""
) -> None:
    if excluded or _is_placeholder_lesson(key_lesson):
        return
    try:
        import time as _time
        from datetime import datetime as _dt, timedelta as _td
        from runtime_paths import get_runtime_path
        path = Path(get_runtime_path("state", "lesson_candidates.json"))
        store: dict = {"generated_at": "", "markets": {"KR": [], "US": []}}
        if path.exists():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    store = loaded
                    store.setdefault("markets", {}).setdefault("KR", [])
                    store.setdefault("markets", {}).setdefault("US", [])
            except Exception:
                pass
        market_list: list = store.get("markets", {}).get(market, [])
        new_id = f"{date}_{market}"
        if any(isinstance(c, dict) and c.get("id") == new_id for c in market_list):
            return
        severity = "high" if bull_result == "MISS" else "info"
        expires = (_dt.now() + _td(days=7)).strftime("%Y-%m-%dT%H:%M:%S+09:00")
        now_str = _dt.now().strftime("%Y-%m-%dT%H:%M:%S+09:00")
        market_list.append({
            "id": new_id,
            "market": market,
            "scope": "selection",
            "target_prompt_scope": "selection",
            "allowed_prompt_scopes": ["selection"],
            "breached": bull_result == "MISS",
            "claude_actionable": True,
            "ops_flag": False,
            "action_hint": key_lesson.strip(),
            "summary": key_lesson.strip()[:100],
            "metric_key": "postmortem_lesson",
            "sample_count": 1,
            "min_sample": 1,
            "severity": severity,
            "confidence": 0.7,
            "generated_at": now_str,
            "expires_at": expires,
            "quality_version": "postmortem.v1",
            "source": "postmortem",
            "truth_status": "manual_review_required",
            "requires_operator_approval": True,
            "prompt_visible_after_approval": True,
            "hit_result": bull_result,
            "hit_result_source": "bull_result",
            "consensus_hit": consensus_hit,
        })
        store["markets"][market] = market_list
        path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning(f"[lesson_candidates] append 실패: {e}")


def _is_placeholder_lesson(text: str) -> bool:
    text = (text or "").strip()
    if not text:
        return True
    if text in _POSTMORTEM_PLACEHOLDER_LESSONS:
        return True
    return ("자동 보정" in text) or ("응답 실패" in text)


def _extract_json(text: str) -> dict:
    """Extract and lightly repair JSON from an LLM response."""

    def _fix(s: str) -> str:
        return re.sub(r",(\s*[}\]])", r"\1", s.strip())

    def _close_balanced(s: str) -> str:
        stack = []
        in_string = False
        escaped = False
        for ch in s:
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in "{[":
                stack.append(ch)
            elif ch in "}]":
                if stack:
                    stack.pop()
        repaired = s
        if in_string:
            repaired += '"'
        while stack:
            opener = stack.pop()
            repaired += "}" if opener == "{" else "]"
        return _fix(repaired)

    def _loads(candidate: str) -> dict:
        fixed = _fix(candidate)
        try:
            return json.loads(fixed)
        except Exception:
            return json.loads(_close_balanced(fixed))

    candidates = []
    for m in re.finditer(r"```(?:json)?\s*(.*?)\s*```", text or "", re.DOTALL):
        block = (m.group(1) or "").strip()
        if "{" in block:
            candidates.append(block)
    start = (text or "").find("{")
    end = (text or "").rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start:end + 1])
    elif start != -1:
        candidates.append(text[start:])

    last_error = None
    for candidate in candidates:
        try:
            return _loads(candidate)
        except Exception as exc:
            last_error = exc
    raise ValueError(f"JSON extract failed: {str(last_error or '')[:160]} | {(text or '')[:200]}")


def _fallback_trade_label(trade):
    if not trade:
        return None
    ticker = str(trade.get("ticker", "-") or "-")
    strategy = str(trade.get("strategy", "-") or "-")
    try:
        pnl_pct = float(trade.get("pnl_pct", 0) or 0)
    except Exception:
        pnl_pct = 0.0
    try:
        pnl = float(trade.get("pnl", trade.get("pnl_krw", 0)) or 0)
    except Exception:
        pnl = 0.0
    return f"{ticker} {pnl_pct:+.2f}% ({pnl:+,.0f} KRW) ({strategy})"


def _build_fallback_postmortem(
    *,
    code_bull: str,
    code_bear: str,
    code_neutral: str,
    sells: list,
    actual_result: dict,
    error: Exception,
) -> dict:
    best = max(sells, key=lambda t: t.get("pnl_pct", t.get("pnl", 0)), default=None)
    worst = min(sells, key=lambda t: t.get("pnl_pct", t.get("pnl", 0)), default=None)
    sell_count = int(actual_result.get("trades", len(sells)) or 0)
    pnl_krw = float(actual_result.get("pnl_krw", 0) or 0)
    issue_type = "postmortem_parse_error"
    if actual_result.get("execution_contaminated"):
        issue_type = "execution_contaminated_postmortem_parse_error"
    return {
        "bull_result": code_bull,
        "bear_result": code_bear,
        "neutral_result": code_neutral,
        "bull_why": "Code-scored fallback after postmortem parse failure.",
        "bear_why": "Code-scored fallback after postmortem parse failure.",
        "neutral_why": "Code-scored fallback after postmortem parse failure.",
        "key_lesson": (
            f"Postmortem JSON parse failed; verified {sell_count} closed trades and "
            f"{pnl_krw:+,.0f} KRW realized PnL from code-level records."
        ),
        "best_trade": _fallback_trade_label(best),
        "worst_trade": _fallback_trade_label(worst),
        "worst_trade_reason": (
            f"Code fallback selected the lowest realized sell PnL; reason={str((worst or {}).get('reason', '') or '-')}"
            if worst else ""
        ),
        "issue_type": issue_type,
        "issue_desc": f"LLM postmortem JSON parse failed; raw response saved. error={str(error)[:120]}",
        "pattern_id": None,
        "_system_error": True,
        "_skip_issue_pattern": True,
        "_system_error_detail": str(error)[:160],
        "brain_updates": {"bull_reliability_change": "stable",
                          "bear_reliability_change": "stable",
                          "new_lesson": None, "market_regime": "unknown"},
        "correction_guide": {"bull_adjustments": [], "bear_adjustments": [],
                             "tuning_rules": [], "today_notes": "postmortem_parse_fallback"},
    }


def _market_system_label(market: str) -> str:
    return "미국 주식 자동매매 시스템" if str(market or "").upper() == "US" else "한국 주식 자동매매 시스템"


def _format_prompt_pnl(row: dict) -> str:
    if row.get("pnl_usd") is not None:
        try:
            return f"${float(row.get('pnl_usd') or 0):+,.2f}"
        except Exception:
            pass
    try:
        return f"{float(row.get('pnl', 0) or 0):+,.0f} KRW"
    except Exception:
        return "0 KRW"


def _format_trade_log(trade_log: list, market: str = "") -> str:
    """체결 내역을 Claude 프롬프트용 텍스트로 변환한다."""
    if not trade_log:
        return "  (체결 없음)"
    lines = []
    for t in trade_log:
        side  = "매수" if t.get("side") == "buy" else "매도"
        pnl_s = f" PnL {_format_prompt_pnl(t)}" if t.get("pnl", 0) or t.get("pnl_usd") is not None else ""
        lines.append(
            f"  [{side}] {t.get('ticker','-')} {t.get('qty',0)}주"
            f"@{t.get('price', t.get('entry', 0)):,} "
            f"시장:{str(market or t.get('market','-')).upper()} 전략:{t.get('strategy','-')}{pnl_s}"
        )
    return "\n".join(lines)


def _strategy_pnl(trade_log: list) -> dict:
    """전략별 PnL 집계 {strategy: [pnl_pct, ...]}"""
    result: dict = {}
    sells = [t for t in trade_log if t.get("side") == "sell" and "pnl_pct" in t]
    for t in sells:
        s = t.get("strategy", "unknown")
        if s in ("broker_sync", "broker_balance", "", None):
            continue
        result.setdefault(s, []).append(t["pnl_pct"])
    return result


_BULL_STANCES    = {"AGGRESSIVE", "MODERATE_BULL", "MILD_BULL", "CAUTIOUS"}
_NEUTRAL_STANCES = {"NEUTRAL"}
_BEAR_STANCES    = {"MILD_BEAR", "CAUTIOUS_BEAR"}
_AVOID_STANCES   = {"DEFENSIVE", "HALT"}   # 방향 예측 아님 — 노출 회피가 맞았는가로 판정

_HIT_THRESHOLD   = 0.5   # 방향성 판단 HIT 최소 임계값
_FLAT_THRESHOLD  = 0.5   # NEUTRAL HIT: |시장| <= 0.5%
_FLAT_PARTIAL    = 1.5   # NEUTRAL PARTIAL: 0.5~1.5%
_AVOID_MISS      = 1.0   # DEFENSIVE MISS: 시장 >= +1.0% (상승 기회 놓침)


def _code_judge_hit_miss(stance: str, market_change_pct: float) -> str:
    """
    분석가 스탠스 + 실제 시장 등락으로 HIT/MISS/PARTIAL 결과를 계산한다.
    Claude 자기평가 영향 제거용.

    BULL/BEAR: 방향 예측 정확도 (PARTIAL 없음 — 방향 일치 여부만)
    - BULL HIT:  시장 > 0%
    - BULL MISS: 시장 <= 0%
    - BEAR HIT:  시장 < 0%
    - BEAR MISS: 시장 >= 0%

    NEUTRAL: 횡보 예측 정확도
    - HIT: |시장| <= 0.5%, PARTIAL: <= 1.5%, MISS: > 1.5%

    DEFENSIVE/HALT: 노출 회피 적절성("왜 노출을 줄이는가")
    - HIT:    시장 < -0.5%  (리스크 회피 판단 정당)
    - PARTIAL: -0.5% <= 시장 < +1.0% (중립, 회피가 과하지는 않음)
    - MISS:   시장 >= +1.0% (강한 상승 놓침, 회피가 잘못된 판단)
    """
    chg = market_change_pct
    abs_chg = abs(chg)

    if stance in _BULL_STANCES:
        return "HIT" if chg > 0 else "MISS"
    elif stance in _BEAR_STANCES:
        return "HIT" if chg < 0 else "MISS"
    elif stance in _AVOID_STANCES:
        if chg < -_HIT_THRESHOLD:  return "HIT"
        if chg < _AVOID_MISS:       return "PARTIAL"
        return "MISS"
    else:  # NEUTRAL
        if abs_chg <= _FLAT_THRESHOLD:  return "HIT"
        if abs_chg <= _FLAT_PARTIAL:    return "PARTIAL"
        return "MISS"


def _stance_dir(stance: str) -> str:
    s = str(stance or "").strip().upper()
    if s in _BULL_STANCES:
        return "up"
    if s in _BEAR_STANCES:
        return "down"
    if s in _AVOID_STANCES:
        return "avoid"
    return "flat"


def _scoring_consistency_meta(judgments: dict, analyst_available: dict, code_results: dict) -> dict:
    """채점 다의성 측정(읽기전용, 채점/학습 로직 변경 없음).

    각 역할 분석가 stance 방향과 코드 채점 결과를 대조해, HIT 판정 역할들의 방향이
    갈리는지 기록한다 — scoring_ambiguous: up/down/flat 중 2개 이상 동시 HIT,
    scoring_multi_dir_hit: up·down 정면 충돌. lesson의 hit_result가 교훈 내용이
    아니라 bull-role 방향 적중(bull_result)임을 가시화하는 메타.
    """
    per_role: dict = {}
    hit_dirs: list = []
    for role in ("bull", "bear", "neutral"):
        if not analyst_available.get(role):
            per_role[role] = {"dir": "unavailable", "result": "UNAVAILABLE"}
            continue
        d = _stance_dir((judgments.get(role) or {}).get("stance"))
        r = code_results.get(role)
        per_role[role] = {"dir": d, "result": r}
        if r == "HIT":
            hit_dirs.append(d)
    distinct = sorted(set(hit_dirs))
    return {
        "scoring_dirs_by_role": per_role,
        "scoring_hit_dirs": distinct,
        "scoring_ambiguous": len(distinct) >= 2,
        "scoring_multi_dir_hit": ("up" in distinct and "down" in distinct),
    }


def _format_decision_event_log(decision_event_log: list) -> str:
    if not decision_event_log:
        return "  (의사결정 로그 없음)"
    lines = []
    for e in decision_event_log[-20:]:
        ts = str(e.get("timestamp", ""))[11:19]
        ticker = e.get("ticker", "-")
        action = e.get("action", "-")
        reason = e.get("reason", "")
        detail = e.get("detail", "")
        qty = int(e.get("qty", 0) or 0)
        price_native = float(e.get("price_native", 0) or 0)
        price_krw = float(e.get("price_krw", 0) or 0)
        selected_reason = e.get("selected_reason", "")
        pieces = [
            f"[{ts}] {ticker} {action}",
            f"{qty}주" if qty else "",
            f"native {price_native:g}" if price_native else "",
            f"krw {price_krw:,.0f}" if price_krw else "",
            reason,
            detail,
            f"selected_reason: {selected_reason}" if selected_reason else "",
        ]
        lines.append("  " + " | ".join([p for p in pieces if p]))
    return "\n".join(lines)


def _recent_selection_feedback_section(market: str) -> str:
    try:
        text = BrainDB.get_recent_selection_feedback_text(market, days=20, max_chars=900)
        if text:
            return f"\n[Recent selection feedback]\n{text}\n"
    except Exception as exc:
        log.debug(f"[postmortem] selection feedback skipped: {exc}")
    return ""


def _prompt_policy_exclusion(actual_result: dict, *, execution_learning_excluded: bool) -> tuple[bool, str]:
    explicit = actual_result.get("prompt_policy_excluded")
    explicit_reason = str(actual_result.get("policy_exclusion_reason") or "").strip()
    if execution_learning_excluded:
        return True, explicit_reason or "execution_learning_excluded"
    if explicit is not None:
        excluded = bool(explicit)
        return excluded, explicit_reason if excluded else ""
    if actual_result.get("selection_evidence_verified"):
        return True, "postmortem_policy_requires_approval"
    if actual_result.get("execution_contaminated"):
        return True, explicit_reason or "execution_contaminated"
    return True, "postmortem_policy_requires_approval"


def _postmortem_policy_candidate_runtime(actual_result: dict) -> tuple[str, str, bool]:
    runtime_mode = str(
        actual_result.get("runtime_mode")
        or actual_result.get("mode")
        or os.getenv("TRADING_RUNTIME_MODE", "")
        or os.getenv("RUNTIME_MODE", "")
        or ""
    ).strip()
    data_quality = str(actual_result.get("data_quality") or "").strip().upper()
    if not data_quality:
        data_quality = "CLEAN" if bool(actual_result.get("data_quality_clean", False)) else "DIRTY"
    forward_complete = bool(
        actual_result.get("forward_complete")
        or actual_result.get("forward_complete_at")
        or actual_result.get("selection_evidence_verified")
    )
    return runtime_mode, data_quality, forward_complete


def _submit_postmortem_policy_candidate(
    market: str,
    target_date: str,
    pm: dict,
    actual_result: dict,
    *,
    policy_exclusion_reason: str,
) -> dict[str, Any]:
    lesson = str(((pm.get("brain_updates") or {}).get("new_lesson") or pm.get("key_lesson") or "")).strip()
    correction_guide = pm.get("correction_guide") if isinstance(pm.get("correction_guide"), dict) else {}
    market_regime = str(((pm.get("brain_updates") or {}).get("market_regime") or "")).strip()
    issue_type = str(pm.get("issue_type") or "").strip()
    if pm.get("_system_error") or _is_placeholder_lesson(lesson):
        return {"submitted": False, "reason": "not_prompt_policy_candidate"}
    candidate = {
        "candidate_type": "market_lesson",
        "market": market,
        "source": "postmortem",
        "summary": lesson[:500],
        "prompt_visible": True,
        "requires_operator_approval": True,
        "truth_status": "manual_review_required",
        "target_prompt_scope": "selection",
        "allowed_prompt_scopes": ["selection"],
        "evidence": {
            "date": target_date,
            "bull_result": pm.get("bull_result"),
            "bear_result": pm.get("bear_result"),
            "neutral_result": pm.get("neutral_result"),
            "market_regime": market_regime,
            "issue_type": issue_type,
            "issue_desc": str(pm.get("issue_desc") or "")[:500],
            "pattern_id": pm.get("pattern_id"),
            "correction_guide": correction_guide,
            "policy_exclusion_reason": policy_exclusion_reason,
        },
    }
    runtime_mode, data_quality, forward_complete = _postmortem_policy_candidate_runtime(actual_result)
    try:
        from learning.approval_queue import BrainApprovalQueue

        submitted = BrainApprovalQueue().submit(
            candidate=candidate,
            runtime_mode=runtime_mode,
            data_quality=data_quality,
            forward_complete=forward_complete,
        )
    except Exception as exc:
        return {
            "submitted": False,
            "reason": f"approval_queue_error:{str(exc)[:160]}",
            "candidate_type": "market_lesson",
        }
    return {
        "submitted": bool(submitted),
        "reason": "queued" if submitted else "approval_queue_gate_rejected",
        "candidate_type": "market_lesson",
        "runtime_mode": runtime_mode,
        "data_quality": data_quality,
        "forward_complete": forward_complete,
    }


def run(market: str, date: str, today_judgment: dict,
        actual_result: dict, digest_prompt: str,
        trade_log: list = None, decision_event_log: list = None) -> dict:
    """
    장 마감 후 Claude 사후 분석.

    Parameters
    ----------
    trade_log : 당일 체결 내역 (trading_bot의 self.risk.trade_log)
                없으면 빈 리스트로 처리
    """
    trade_log = trade_log or []
    decision_event_log = decision_event_log or []
    judgments      = today_judgment.get("judgments", {})
    consensus      = today_judgment.get("consensus", {})
    consensus_mode = consensus.get("mode", "CAUTIOUS")
    analyst_available = {
        role: is_available_judgment((judgments or {}).get(role) or {})
        for role in ("bull", "bear", "neutral")
    }
    analyst_unavailable_roles = [
        role for role, available in analyst_available.items()
        if not available
    ]
    trade_log      = trade_log or []
    decision_event_log = decision_event_log or []

    # HALT 이거나 판단 자체가 없는 날은 postmortem 스킵
    if not judgments or consensus_mode == "HALT":
        log.info(f"[postmortem skip] {date} {market} | HALT 또는 판단 없음")
        return {
            "bull_result": "PARTIAL", "bear_result": "PARTIAL", "neutral_result": "PARTIAL",
            "bull_why": "HALT 스킵", "bear_why": "HALT 스킵", "neutral_why": "HALT 스킵",
            "key_lesson": "HALT 세션 또는 거래 없음",
            "best_trade": None, "worst_trade": None, "worst_trade_reason": "",
            "issue_type": "HALT", "issue_desc": "", "pattern_id": None,
            "brain_updates": {"bull_reliability_change": "stable",
                              "bear_reliability_change": "stable",
                              "new_lesson": None, "market_regime": "unknown"},
            "correction_guide": {"bull_adjustments": [], "bear_adjustments": [],
                                 "tuning_rules": [], "today_notes": ""},
        }

    if _postmortem_fresh_brain_active():
        # V2 fresh brain: selection/시장판단과 동일하게 레거시 brain 요약을 주입하지 않는다.
        # stale correction_guide-시대 누적 요약이 다른 국면 사후분석에 새는 것을 차단.
        brain_summary = ""
        log.debug(f"[postmortem] V2 fresh brain — legacy brain summary skipped ({market})")
    else:
        brain_summary = BrainDB.generate_prompt_summary(market)  # 실패해도 무시하지 않음
    selection_feedback = _recent_selection_feedback_section(market)
    market_label = _market_system_label(market)
    trade_section = _format_trade_log(trade_log, market)
    decision_section = _format_decision_event_log(decision_event_log)

    sells  = [t for t in trade_log if t.get("side") == "sell" and "pnl" in t]
    wins   = [t for t in trade_log if t.get("side") == "sell" and t.get("pnl", 0) > 0]
    losses = [t for t in trade_log if t.get("side") == "sell" and t.get("pnl", 0) <= 0]

    # 거래가 없는 날에는 판단 평가와 보정 지침만 작성한다.
    if not sells:
        prompt = f"""당신은 {market_label}의 장마감 사후분석 AI입니다.
오늘은 체결된 매도 거래가 없습니다. 판단 적중 여부와 내일 보정 지침만 작성하세요.

[오늘 판단 요약]
  Bull:    {judgments.get('bull',{}).get('stance','-')} / {judgments.get('bull',{}).get('key_reason','-')}
  Bear:    {judgments.get('bear',{}).get('stance','-')} / {judgments.get('bear',{}).get('key_reason','-')}
  Neutral: {judgments.get('neutral',{}).get('stance','-')} / {judgments.get('neutral',{}).get('key_reason','-')}
  최종 합의: {consensus_mode}

[실제 시장 결과]
  시장 변화: {actual_result.get('market_change', 0):+.2f}%
  세션 손익: {actual_result.get('pnl_pct', 0):+.2f}%

[시장 컨텍스트]
{digest_prompt[:400]}
{selection_feedback}

[누적 학습 요약]
{brain_summary}

아래 형식의 JSON만 반환하세요:
{{
  "bull_result":   "HIT|MISS|PARTIAL",
  "bear_result":   "HIT|MISS|PARTIAL",
  "neutral_result":"HIT|MISS|PARTIAL",
  "bull_why":      "설명",
  "bear_why":      "설명",
  "neutral_why":   "설명",
  "best_trade":    null,
  "worst_trade":   null,
  "worst_trade_reason": "",
  "key_lesson":    "핵심 교훈",
  "issue_type":    "분류",
  "issue_desc":    "설명",
  "pattern_id":    null,
  "brain_updates": {{
    "bull_reliability_change": "up|down|stable",
    "bear_reliability_change": "up|down|stable",
    "new_lesson":    "새 교훈 또는 null",
    "market_regime": "시장 레짐"
  }},
  "correction_guide": {{
    "bull_adjustments":  ["조정안"],
    "bear_adjustments":  ["조정안"],
    "tuning_rules":      ["규칙"],
    "today_notes":       "메모"
  }}
}}"""
    else:
        # 거래가 있는 날에는 체결 결과와 판단 근거를 함께 평가한다.
        best = max(sells, key=lambda t: t.get("pnl_pct", t.get("pnl", 0)), default=None)
        worst = min(sells, key=lambda t: t.get("pnl_pct", t.get("pnl", 0)), default=None)
        best_s = (
            f"{best['ticker']} {best.get('pnl_pct', 0):+.2f}% ({_format_prompt_pnl(best)}) ({best.get('strategy','-')})"
            if best else "없음"
        )
        worst_s = (
            f"{worst['ticker']} {worst.get('pnl_pct', 0):+.2f}% ({_format_prompt_pnl(worst)}) ({worst.get('strategy','-')})"
            if worst else "없음"
        )

        prompt = f"""당신은 {market_label}의 장마감 사후분석 AI입니다.
오늘 체결된 거래와 실제 시장 결과를 보고, 판단 정확도와 실행 품질을 같이 평가하세요.

[오늘 판단 요약]
  Bull:    {judgments.get('bull',{}).get('stance','-')} / {judgments.get('bull',{}).get('key_reason','-')}
  Bear:    {judgments.get('bear',{}).get('stance','-')} / {judgments.get('bear',{}).get('key_reason','-')}
  Neutral: {judgments.get('neutral',{}).get('stance','-')} / {judgments.get('neutral',{}).get('key_reason','-')}
  최종 합의: {consensus_mode} (size={consensus.get('size','-')}%)

[오늘 체결 내역 ({len(trade_log)}건)]
{trade_section}
  최고 거래: {best_s}
  최악 거래: {worst_s}

[오늘 판단 후보/차단 로그 ({len(decision_event_log)}건)]
{decision_section}

[실제 시장 결과]
  시장 변화: {actual_result.get('market_change', 0):+.2f}%
  세션 손익: {actual_result.get('pnl_pct', 0):+.2f}%  {'WIN' if actual_result.get('win') else 'LOSS'}
  수익 청산: {len(wins)}건 / 손실 청산: {len(losses)}건

[시장 컨텍스트]
{digest_prompt[:350]}
{selection_feedback}

[누적 학습 요약]
{brain_summary}

[분석 요청]
1. 오늘 Bull/Bear/Neutral 판단의 적중 여부를 실제 시장 기준으로 평가하세요.
2. 최고 거래는 왜 잘됐는지, 최악 거래는 왜 실패했는지 구체적으로 설명하세요.
3. 손실 거래가 있다면 공통 원인을 분석하세요.
4. 같은 상황이 반복되면 내일 무엇을 다르게 할지 제안하세요.

아래 형식의 JSON만 반환하세요:
{{
  "bull_result":   "HIT|MISS|PARTIAL",
  "bear_result":   "HIT|MISS|PARTIAL",
  "neutral_result":"HIT|MISS|PARTIAL",
  "bull_why":      "설명",
  "bear_why":      "설명",
  "neutral_why":   "설명",
  "best_trade":    "티커 또는 null",
  "worst_trade":   "티커 또는 null",
  "worst_trade_reason": "설명",
  "key_lesson":    "핵심 교훈",
  "issue_type":    "분류",
  "issue_desc":    "설명",
  "pattern_id":    null,
  "brain_updates": {{
    "bull_reliability_change": "up|down|stable",
    "bear_reliability_change": "up|down|stable",
    "new_lesson":    "새 교훈 또는 null",
    "market_regime": "시장 레짐"
  }},
  "correction_guide": {{
    "bull_adjustments":  ["조정안"],
    "bear_adjustments":  ["조정안"],
    "tuning_rules":      ["규칙"],
    "today_notes":       "메모"
  }}
}}"""

    # 코드 기반 HIT/MISS 사전 계산 (Claude 응답 오염 제거)
    try:
        market_chg = float(actual_result.get("market_change"))
    except Exception:
        market_chg = 0.0
    code_bull = (
        _code_judge_hit_miss(judgments.get("bull", {}).get("stance", "NEUTRAL"), market_chg)
        if analyst_available.get("bull") else "UNAVAILABLE"
    )
    code_bear = (
        _code_judge_hit_miss(judgments.get("bear", {}).get("stance", "NEUTRAL"), market_chg)
        if analyst_available.get("bear") else "UNAVAILABLE"
    )
    code_neutral = (
        _code_judge_hit_miss(judgments.get("neutral", {}).get("stance", "NEUTRAL"), market_chg)
        if analyst_available.get("neutral") else "UNAVAILABLE"
    )
    log.info(f"[postmortem 코드보정] 시장변화 {market_chg:+.2f}% | "
             f"bull={code_bull} bear={code_bear} neutral={code_neutral}")

    try:
        started = time.monotonic()
        resp = client.messages.create(
            model=MODEL, max_tokens=2500,
            messages=[{"role": "user", "content": prompt}]
        )
        duration_ms = int((time.monotonic() - started) * 1000)
        raw = resp.content[0].text.strip()
        call_id = f"postmortem_{market}_{date}_{uuid.uuid4().hex[:10]}"
        credit_record(
            resp.usage.input_tokens, resp.usage.output_tokens, "postmortem", model=MODEL,
            cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        )
        save_raw_call(
            label="postmortem",
            prompt=prompt, raw_response=raw, parsed={},
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            market=market, call_date=date,
            model=MODEL,
            call_id=call_id,
            prompt_version="postmortem_v2_market_scoped",
            parse_stage="raw",
            duration_ms=duration_ms,
            extra={"_raw_only": True},
        )
        try:
            pm = _extract_json(raw)
        except Exception as parse_exc:
            save_raw_call(
                label="postmortem",
                prompt=prompt, raw_response=raw, parsed={},
                input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
                market=market, call_date=date,
                model=MODEL,
                call_id=call_id,
                prompt_version="postmortem_v2_market_scoped",
                parse_error=True,
                parse_stage="parse_failed",
                duration_ms=duration_ms,
                extra={"_raw_only": True, "repair_error": str(parse_exc)[:300]},
            )
            raise
        save_raw_call(
            label="postmortem",
            prompt=prompt, raw_response=raw, parsed=pm,
            input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens,
            market=market, call_date=date,
            model=MODEL,
            call_id=call_id,
            prompt_version="postmortem_v2_market_scoped",
            parse_error=False,
            parse_stage="parsed",
            duration_ms=duration_ms,
        )
        # Claude HIT/MISS를 코드 보정값으로 덮어쓴다 (응답 오염 제거)
        pm["bull_result"]    = code_bull
        pm["bear_result"]    = code_bear
        pm["neutral_result"] = code_neutral
    except Exception as e:
        log.error(f"postmortem 오류: {e}")
        pm = {
            "bull_result": code_bull,
            "bear_result": code_bear,
            "neutral_result": code_neutral,
            "bull_why": "응답 실패, 코드 보정",
            "bear_why": "응답 실패, 코드 보정",
            "neutral_why": "응답 실패, 코드 보정",
            "key_lesson": "",
            "best_trade": None, "worst_trade": None, "worst_trade_reason": "",
            "issue_type": "", "issue_desc": "", "pattern_id": None,
            "_system_error": True,
            "_skip_issue_pattern": True,
            "_system_error_detail": str(e)[:160],
            "brain_updates": {"bull_reliability_change": "stable",
                              "bear_reliability_change": "stable",
                              "new_lesson": None, "market_regime": "unknown"},
            "correction_guide": {"bull_adjustments": [], "bear_adjustments": [],
                                 "tuning_rules": [], "today_notes": ""},
        }

        pm = _build_fallback_postmortem(
            code_bull=code_bull,
            code_bear=code_bear,
            code_neutral=code_neutral,
            sells=sells,
            actual_result=actual_result,
            error=e,
        )

    for role in analyst_unavailable_roles:
        pm[f"{role}_result"] = "UNAVAILABLE"
        pm[f"{role}_why"] = "analyst unavailable during judgment"
    if analyst_unavailable_roles:
        pm["analyst_unavailable_roles"] = list(analyst_unavailable_roles)
        pm["analyst_available"] = dict(analyst_available)

    # 채점 다의성/거래0 측정(읽기전용 메타, 채점·학습 로직 불변 — Tier1-3 A)
    pm.update(_scoring_consistency_meta(
        judgments, analyst_available,
        {"bull": pm.get("bull_result"), "bear": pm.get("bear_result"), "neutral": pm.get("neutral_result")},
    ))
    # trades_zero: '매도 0건'이 아니라 '진입·청산 체결이 전무'로 판정(운영자 결정 2026-06-29).
    # PATHB_INTRADAY_ONLY=false(멀티데이 홀드)라 신규 진입만 하고 당일 청산이 없는 활동 세션이
    # 존재한다 — 이를 trades_zero로 잡으면 유효한 방향적중 학습이 통째 누락되므로, trade_log의
    # buy/sell 체결 전체로 활동을 본다. 매도건수 fallback과 OR 결합해 거짓 스킵을 최소화한다.
    _trade_activity = sum(
        1 for t in trade_log
        if str((t or {}).get("side", "") or "").lower() in ("buy", "sell")
    )
    _sells_reported = int(actual_result.get("trades", len(sells)) or 0)
    pm["trades_zero"] = (_trade_activity == 0 and _sells_reported == 0)

    execution_learning_excluded = bool(
        actual_result.get(
            "execution_learning_excluded",
            actual_result.get("execution_contaminated", False),
        )
    )
    prompt_policy_excluded, policy_exclusion_reason = _prompt_policy_exclusion(
        actual_result,
        execution_learning_excluded=execution_learning_excluded,
    )

    # ── brain 업데이트 ───────────────────────────────────────────────
    if not execution_learning_excluded and not pm.get("trades_zero"):
        recent = BrainDB.load()["markets"][market].get("recent_days", [])

        for role in ("bull", "bear", "neutral"):
            if not analyst_available.get(role):
                continue
            BrainDB.update_analyst(market, role, pm[f"{role}_result"] == "HIT", recent)
        # mode_performance는 '실현 성과'다 — 매도(청산)가 있을 때만 갱신한다. 매수만 한 멀티데이
        # 활동 세션은 실현 pnl=0이라 가짜 무승부(0-pnl 패)가 누적돼 mode_performance를 오염시키므로
        # 제외한다(Tier1-3B 의도 유지). update_analyst는 방향적중이라 매수만 해도 유효해 위에서 실행.
        if _sells_reported > 0:
            BrainDB.update_mode_performance(
                market, consensus_mode,
                actual_result.get("pnl_pct", 0), actual_result.get("win", False)
            )
    elif not execution_learning_excluded and pm.get("trades_zero"):
        # Tier1-3B: 진입·청산 체결이 전무한 세션은 실현 성과가 없어 mode_performance에 가짜 무승부
        # (pnl=0/win=False), update_analyst에 거래로 전환 안 된 방향적중이 누적돼 brain reliability를
        # 진동시킨다 → 갱신 제외. (매수만 한 활동 세션은 trades_zero=False라 정상 학습)
        log.info(f"[brain skip] {date} {market} 진입·청산 체결 0 — 성과 갱신 제외(가짜 무승부·미전환 방향적중 누적 방지)")

    bu = pm.get("brain_updates", {})
    # new_lesson 없으면 key_lesson을 fallback으로 사용
    lesson_to_save = bu.get("new_lesson") or pm.get("key_lesson")
    policy_candidate_result = _submit_postmortem_policy_candidate(
        market,
        date,
        pm,
        actual_result,
        policy_exclusion_reason=policy_exclusion_reason,
    )
    if policy_candidate_result.get("submitted"):
        log.info(f"[postmortem policy candidate] {date} {market} queued for approval")
    elif lesson_to_save and not pm.get("_system_error"):
        log.info(
            f"[postmortem policy candidate] {date} {market} not queued: "
            f"{policy_candidate_result.get('reason')}"
        )

    fallback_note = pm.get("key_lesson", "") if pm.get("_system_error") else ""
    daily_key_lesson = "" if execution_learning_excluded else pm.get("key_lesson", "")
    daily_issue_type = "" if execution_learning_excluded else pm.get("issue_type", "")
    if execution_learning_excluded and pm.get("_system_error") and pm.get("issue_type"):
        daily_issue_type = pm.get("issue_type", "")
    sell_count = int(actual_result.get("trades", len(sells)) or 0)

    BrainDB.add_daily_record(market, {
        "date":              date,
        "mode":              consensus_mode,
        "pnl_pct":           actual_result.get("pnl_pct", 0),
        "market_change":     market_chg,
        "win":               actual_result.get("win", False),
        "bull_result":       pm["bull_result"],
        "bear_result":       pm["bear_result"],
        "neutral_result":    pm["neutral_result"],
        "bull_stance":       judgments.get("bull", {}).get("stance", ""),
        "bear_stance":       judgments.get("bear", {}).get("stance", ""),
        "neutral_stance":    judgments.get("neutral", {}).get("stance", ""),
        "bull_reason":       judgments.get("bull", {}).get("key_reason", ""),
        "bear_reason":       judgments.get("bear", {}).get("key_reason", ""),
        "neutral_reason":    judgments.get("neutral", {}).get("key_reason", ""),
        "analyst_available": dict(analyst_available),
        "analyst_unavailable_roles": list(analyst_unavailable_roles),
        "key_lesson":        daily_key_lesson,
        "issue_type":        daily_issue_type,
        "postmortem_fallback_note": fallback_note,
        "best_trade":        pm.get("best_trade"),
        "worst_trade":       pm.get("worst_trade"),
        "worst_trade_reason": pm.get("worst_trade_reason", ""),
        "trades":            sell_count,
        "trades_zero":       pm.get("trades_zero", sell_count == 0),
        "scoring_hit_dirs":  pm.get("scoring_hit_dirs"),
        "scoring_ambiguous": pm.get("scoring_ambiguous"),
        "scoring_multi_dir_hit": pm.get("scoring_multi_dir_hit"),
        "execution_contaminated": bool(actual_result.get("execution_contaminated", False)),
        "execution_learning_excluded": execution_learning_excluded,
        "prompt_policy_excluded": prompt_policy_excluded,
        "policy_exclusion_reason": policy_exclusion_reason,
        "execution_warning": bool(actual_result.get("execution_warning", False)),
        "execution_issues": actual_result.get("execution_issues", []),
        "execution_issue_labels": actual_result.get("execution_issue_labels", []),
        "execution_issue_details": actual_result.get("execution_issue_details", []),
        "selection_feedback": BrainDB.get_recent_selection_feedback_text(market, days=20, max_chars=400),
        "policy_candidate": policy_candidate_result,
    })

    # lesson_candidates.json 자동 append
    consensus_hit_label = _code_judge_hit_miss(consensus_mode, market_chg) if consensus_mode else ""
    _append_lesson_candidate(
        market=market,
        date=date,
        key_lesson=daily_key_lesson,
        bull_result=pm["bull_result"],
        consensus_hit=consensus_hit_label,
        excluded=execution_learning_excluded or pm.get("_system_error", False),
    )

    # 전략별 성과 자동 업데이트
    if not execution_learning_excluded:
        for strat, pnls in _strategy_pnl(trade_log).items():
            avg_pnl = sum(pnls) / len(pnls)
            BrainDB.update_strategy_performance(market, strat, avg_pnl, avg_pnl > 0)

        # ── 토론 결과 정답 여부 업데이트 ─────────────────────────────────
        try:
            BrainDB.update_debate_outcome(market, date, actual_result.get("win", False))
        except Exception as e:
            log.warning(f"토론 결과 업데이트 실패: {e}")

    # 당일 Claude 보정 지침은 prompt-visible 정책 메모리이므로 approval candidate에만 보존한다.

    log.info(
        f"[postmortem {date}] Bull:{pm['bull_result']} Bear:{pm['bear_result']} "
        f"Neut:{pm['neutral_result']} | {pm.get('key_lesson','')[:60]}"
    )
    if pm.get("worst_trade"):
        log.warning(
            f"[worst_trade] {pm['worst_trade']} | {pm.get('worst_trade_reason','')}"
        )

    # JSONL 학습 로그 저장 (프롬프트 + 응답 + 거래 원본)
    judgment_log.info(
        f"[postmortem {date} {market}] "
        f"Bull:{pm['bull_result']} Bear:{pm['bear_result']} Neutral:{pm['neutral_result']}",
        extra={"extra": {
            "event":          "postmortem",
            "date":           date,
            "market":         market,
            "consensus_mode": consensus_mode,
            "actual_result":  actual_result,
            "trade_log":      trade_log,          # 당일 체결 원본 보존
            "postmortem":     pm,
            "strategy_pnl":   _strategy_pnl(trade_log),
        }},
    )

    return pm
