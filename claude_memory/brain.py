"""
brain.py - Claude 판단 메모리 관리
brain.json 읽기 / 업데이트 / 요약 생성
"""

import json
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional
from runtime_paths import get_runtime_path
from filelock import FileLock

REPO_BRAIN_PATH = Path(__file__).parent / "brain.json"
BRAIN_PATH = get_runtime_path("state", "brain.json")
_BRAIN_LOCK = FileLock(str(BRAIN_PATH) + ".lock", timeout=10)


def _ensure_extensions(brain: dict):
    for market in ("KR", "US"):
        m = brain["markets"][market]
        m.setdefault("execution_patterns", {})
        m.setdefault("execution_lessons", [])
        m.setdefault("execution_stats", {
            "buy_order": 0,
            "buy_failed": 0,
            "sell_filled": 0,
            "sell_failed": 0,
        })
    return brain


def _normalize_recent_days(rows: list, max_items: int = 60) -> list:
    merged: dict[str, dict] = {}
    order: list[str] = []
    for row in rows or []:
        date_key = str((row or {}).get("date", "") or "").strip()
        if not date_key:
            continue
        if date_key not in merged:
            order.append(date_key)
        merged[date_key] = dict(row or {})
    return [merged[key] for key in order][-max_items:]


def _merge_debate_entry(base: dict, overlay: dict) -> dict:
    merged = dict(base or {})
    overlay = dict(overlay or {})
    for key in ("date", "r1", "r2", "changes", "consensus_shifted"):
        value = overlay.get(key)
        if value not in (None, "", [], {}):
            merged[key] = value
    if overlay.get("outcome") is not None:
        merged["outcome"] = overlay.get("outcome")
    else:
        merged.setdefault("outcome", (base or {}).get("outcome"))
    return merged


def _normalize_debate_history(rows: list, max_items: int = 30) -> list:
    merged: dict[str, dict] = {}
    order: list[str] = []
    for row in rows or []:
        date_key = str((row or {}).get("date", "") or "").strip()
        if not date_key:
            continue
        if date_key not in merged:
            order.append(date_key)
            merged[date_key] = dict(row or {})
        else:
            merged[date_key] = _merge_debate_entry(merged[date_key], row or {})
    return [merged[key] for key in order][-max_items:]


def _normalize_tuning_rules(rules: list, max_items: int = 12) -> list:
    normalized = []
    seen = set()
    for rule in rules or []:
        text = str(rule or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized[:max_items]


def _is_empty_correction_guide(guide: dict) -> bool:
    if not isinstance(guide, dict):
        return True
    for key in ("bull_adjustments", "bear_adjustments", "tuning_rules"):
        values = guide.get(key, []) or []
        if isinstance(values, str):
            values = [values]
        if any(str(item or "").strip() for item in values):
            return False
    return not str(guide.get("today_notes", "") or "").strip()


_PLACEHOLDER_TEXT_MARKERS = (
    "오류로 자동 판정",
    "응답 실패",
    "자동 판정",
    "자동 생성 실패",
)
_MOJIBAKE_TEXT_MARKERS = (
    "\ufffd",
    "?섏",
    "?ㅽ",
    "?좏",
    "?먯",
    "?꾩",
    "?댁",
    "泥",
    "紐",
    "筌",
    "揶",
    "醫",
    "諛",
    "袁",
    "癰",
    "疫",
    "野",
    "瑗",
    "濡",
    "遺",
    "鍮",
)
_EMPTY_TEXT_VALUES = {"", "-", "none", "n/a", "null", "없음", "해당 없음"}


def _has_mojibake_text(value) -> bool:
    text = str(value or "")
    return any(marker in text for marker in _MOJIBAKE_TEXT_MARKERS)


def _is_placeholder_text(value, *, empty_is_placeholder: bool = True) -> bool:
    text = str(value or "").strip()
    if empty_is_placeholder and text.lower() in _EMPTY_TEXT_VALUES:
        return True
    if any(marker in text for marker in _PLACEHOLDER_TEXT_MARKERS):
        return True
    return _has_mojibake_text(text)


def _is_prompt_safe_text(value, *, allow_empty: bool = False) -> bool:
    if value is None:
        return allow_empty
    text = str(value).strip()
    if allow_empty and not text:
        return True
    return not _is_placeholder_text(text)


def _clean_prompt_text_list(values: list, *, max_items: Optional[int] = None) -> list[str]:
    cleaned: list[str] = []
    seen = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text or _is_placeholder_text(text):
            continue
        if text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    if max_items is not None:
        return cleaned[-max_items:]
    return cleaned


def _is_valid_issue_pattern(pattern: dict) -> bool:
    if not isinstance(pattern, dict):
        return False
    description = pattern.get("description", "")
    insight = pattern.get("insight", "")
    if not _is_prompt_safe_text(description):
        return False
    return _is_prompt_safe_text(insight, allow_empty=True)


def _format_issue_pattern_summary(patterns: list) -> str:
    lines: list[str] = []
    for pattern in patterns or []:
        if not _is_valid_issue_pattern(pattern):
            continue
        insight = str(pattern.get("insight", "") or "").strip() or "none"
        lines.append(
            "\n".join([
                f"  [{pattern.get('id', '-')}] {pattern.get('type', 'unknown')} ({pattern.get('count', 0)}건)",
                f"    설명: {str(pattern.get('description', '')).strip()}",
                (
                    f"    Bull 적중률 {pattern.get('bull_accuracy', 0)*100:.0f}%  "
                    f"평균수익 {pattern.get('avg_pnl_when_followed', 0):+.2f}%"
                ),
                f"    인사이트: {insight}",
            ])
        )
    return "\n".join(lines)


def _format_tuning_pattern_summary(tuning: dict, today_dt: date) -> str:
    lines: list[str] = []
    for key, value in (tuning or {}).items():
        count = int(value.get("count", 0) or 0)
        if count == 0:
            continue
        adjusted = int(value.get("adjusted", value.get("correct", 0)) or 0)
        rate = float(value.get("adjusted_rate", value.get("rate", 0.0)) or 0.0)
        insight = str(value.get("insight", "") or "").strip()
        if _is_placeholder_text(insight):
            insight = ""
        last_seen_str = value.get("last_seen", "")
        if last_seen_str:
            try:
                days_ago = (today_dt - datetime.strptime(last_seen_str, "%Y-%m-%d").date()).days
                recency_tag = "" if days_ago <= 7 else f" [{days_ago}일 전]"
            except Exception:
                recency_tag = ""
        else:
            recency_tag = " [최근일 미확인]"
        insight_suffix = f" | {insight}" if insight else ""
        lines.append(
            f"  {key}{recency_tag}: {count}건 중 {adjusted}건 조정 "
            f"({rate*100:.0f}%){insight_suffix}"
        )
    return "\n".join(lines)


def _normalize_brain(brain: dict) -> dict:
    brain = _ensure_extensions(brain)
    meta = brain.setdefault("meta", {})
    correction_guide = brain.setdefault("correction_guide", {})
    for market in ("KR", "US"):
        market_payload = brain["markets"][market]
        market_payload["execution_lessons"] = _clean_prompt_text_list(
            market_payload.get("execution_lessons", []) or [],
            max_items=12,
        )
        beliefs = market_payload.setdefault("current_beliefs", {})
        beliefs["learned_lessons"] = _clean_prompt_text_list(
            beliefs.get("learned_lessons", []) or [],
            max_items=10,
        )
        recent_days = _normalize_recent_days(market_payload.get("recent_days", []) or [])
        market_payload["recent_days"] = recent_days
        market_payload["debate_history"] = _normalize_debate_history(
            market_payload.get("debate_history", []) or []
        )
        market_payload["trained_days"] = max(
            int(market_payload.get("trained_days", 0) or 0),
            len(recent_days),
        )
        guide = correction_guide.setdefault(market, {})
        guide["tuning_rules"] = _normalize_tuning_rules(guide.get("tuning_rules", []) or [])
    meta_key_map = {"KR": "trained_days_kr", "US": "trained_days_us"}
    for market, meta_key in meta_key_map.items():
        meta[meta_key] = max(
            int(meta.get(meta_key, 0) or 0),
            int(brain["markets"][market].get("trained_days", 0) or 0),
        )
    return brain


# ── 기본 읽기/쓰기 ────────────────────────────────────────────────────────────

def load() -> dict:
    with _BRAIN_LOCK:
        candidates = []
        if BRAIN_PATH.exists():
            candidates.append(BRAIN_PATH)
        if REPO_BRAIN_PATH.exists() and REPO_BRAIN_PATH not in candidates:
            candidates.append(REPO_BRAIN_PATH)
        last_error = None
        for source in candidates:
            try:
                with open(source, "r", encoding="utf-8-sig") as f:
                    return _normalize_brain(json.load(f))
            except Exception as e:
                last_error = e
                continue
        if last_error is not None:
            raise last_error
        raise FileNotFoundError("brain.json not found")


def save(brain: dict):
    with _BRAIN_LOCK:
        brain = _normalize_brain(brain)
        brain["meta"]["last_updated"] = date.today().isoformat()
        brain["meta"]["version"] += 1
        with open(BRAIN_PATH, "w", encoding="utf-8") as f:
            json.dump(brain, f, ensure_ascii=False, indent=2)


# 분석가 성과 업데이트

def update_analyst(market: str, analyst: str, hit: bool, recent_days: list):
    """
    매일 postmortem에서 호출한다.
    analyst: 'bull' | 'bear' | 'neutral'
    hit: True=적중, False=미적중
    recent_days: 최근 30일 기록 리스트
    """
    brain = load()
    perf  = brain["markets"][market]["analyst_performance"][analyst]

    perf["total"] += 1
    if hit:
        perf["hit"] += 1
    else:
        perf["miss"] += 1
    perf["rate"] = round(perf["hit"] / perf["total"], 3)

    # 최근 7일
    r7 = [d for d in recent_days[-7:] if f"{analyst}_result" in d]
    if r7:
        h7 = sum(1 for d in r7 if d.get(f"{analyst}_result") == "HIT")
        perf["recent_7d"] = {"total": len(r7), "hit": h7,
                              "rate": round(h7 / len(r7), 3)}

    # 최근 30일
    r30 = [d for d in recent_days[-30:] if f"{analyst}_result" in d]
    if r30:
        h30 = sum(1 for d in r30 if d.get(f"{analyst}_result") == "HIT")
        perf["recent_30d"] = {"total": len(r30), "hit": h30,
                               "rate": round(h30 / len(r30), 3)}

    # 최근 추세 판단 (최근 30일 기준과 비교)
    recent_30d_rate = perf.get("recent_30d", {}).get("rate", perf["rate"])
    if perf["recent_7d"]["rate"] > recent_30d_rate + 0.05:
        perf["trend"] = "improving"
    elif perf["recent_7d"]["rate"] < recent_30d_rate - 0.05:
        perf["trend"] = "declining"
    else:
        perf["trend"] = "stable"

    save(brain)


# 모드 성과 업데이트

def update_mode_performance(market: str, mode: str, pnl_pct: float, win: bool):
    brain = load()
    mode_map = brain["markets"][market]["mode_performance"]
    if mode not in mode_map:
        mode_map[mode] = {"count": 0, "avg_pnl": 0.0, "win_rate": 0.0}
    mp = mode_map[mode]

    prev_count = mp["count"]
    mp["count"] += 1
    mp["avg_pnl"] = round(
        (mp["avg_pnl"] * prev_count + pnl_pct) / mp["count"], 4
    )
    prev_wins = round(mp["win_rate"] * prev_count)
    mp["win_rate"] = round((prev_wins + (1 if win else 0)) / mp["count"], 3)

    save(brain)


# 전략 성과 업데이트

def update_strategy_performance(market: str, strategy: str,
                                  pnl_pct: float, win: bool):
    brain = load()
    sp = brain["markets"][market]["strategy_performance"]

    if strategy not in sp:
        sp[strategy] = {"count": 0, "win_rate": 0.0, "avg_pnl": 0.0}

    s = sp[strategy]
    prev_count = s["count"]
    s["count"] += 1
    s["avg_pnl"] = round(
        (s["avg_pnl"] * prev_count + pnl_pct) / s["count"], 4
    )
    prev_wins = round(s["win_rate"] * prev_count)
    s["win_rate"] = round((prev_wins + (1 if win else 0)) / s["count"], 3)

    save(brain)


def update_execution_pattern(market: str, event: dict):
    brain = load()
    m = brain["markets"][market]
    patterns = m.setdefault("execution_patterns", {})
    stats = m.setdefault("execution_stats", {
        "buy_order": 0,
        "buy_failed": 0,
        "sell_filled": 0,
        "sell_failed": 0,
    })

    action = event.get("action", "") or "unknown"
    strategy = event.get("strategy", "") or "unknown"
    reason = event.get("reason", "") or "unknown"
    detail = event.get("detail", "") or ""
    ticker = event.get("ticker", "") or ""
    selected_reason = event.get("selected_reason", "") or ""
    pnl_pct = float(event.get("pnl_pct", 0) or 0)
    success = action in ("buy_order", "sell_filled")
    if action in stats:
        stats[action] += 1

    key = f"{action}|{strategy}|{reason}"
    today_str = datetime.now().date().isoformat()
    item = patterns.setdefault(key, {
        "action": action,
        "strategy": strategy,
        "reason": reason,
        "count": 0,
        "success": 0,
        "fail": 0,
        "avg_pnl_pct": 0.0,
        "last_detail": "",
        "last_seen": today_str,
        "examples": [],
    })
    prev = item["count"]
    item["count"] += 1
    if success:
        item["success"] += 1
    else:
        item["fail"] += 1
    item["avg_pnl_pct"] = round((item["avg_pnl_pct"] * prev + pnl_pct) / max(item["count"], 1), 4)
    item["last_detail"] = detail or selected_reason
    item["last_seen"] = today_str
    example = {
        "date": datetime.now().date().isoformat(),
        "ticker": ticker,
        "detail": detail,
        "selected_reason": selected_reason,
        "pnl_pct": pnl_pct,
    }
    item.setdefault("examples", []).append(example)
    item["examples"] = item["examples"][-5:]

    lessons = _clean_prompt_text_list(m.setdefault("execution_lessons", []))
    if action == "buy_failed" and reason not in ("pending_order", "already_holding"):
        lessons.append(f"{strategy} 매수 실패 주요 사유: {reason}")
    elif action == "sell_failed":
        lessons.append(f"청산 실패 주요 사유: {reason}")
    elif action == "sell_filled" and pnl_pct < 0:
        lessons.append(f"손실 매도 주요 사유: {reason}")
    elif action == "sell_filled" and pnl_pct > 0:
        lessons.append(f"수익 청산 유효 패턴: {reason}")
    m["execution_lessons"] = _clean_prompt_text_list(lessons, max_items=12)
    save(brain)


def _build_execution_summary(market_data: dict) -> tuple[str, str]:
    patterns = market_data.get("execution_patterns", {}) or {}
    lessons = _clean_prompt_text_list(market_data.get("execution_lessons", []) or [])

    if patterns:
        top_items = sorted(
            patterns.values(),
            key=lambda x: (x.get("count", 0), x.get("success", 0)),
            reverse=True,
        )[:5]
        today = datetime.now().date()
        pattern_lines = []
        for item in top_items:
            last_seen_str = item.get("last_seen", "")
            if last_seen_str:
                try:
                    last_seen_date = datetime.strptime(last_seen_str, "%Y-%m-%d").date()
                    days_ago = (today - last_seen_date).days
                    if days_ago == 0:
                        recency = "today"
                    elif days_ago <= 7:
                        recency = f"{days_ago}d"
                    elif days_ago <= 30:
                        recency = f"{days_ago}d recent"
                    else:
                        recency = f"{days_ago}d stale"
                except Exception:
                    recency = "date_unknown"
            else:
                recency = "date_unknown"
            pattern_lines.append(
                "  "
                f"{item.get('action', 'unknown')} | "
                f"{item.get('strategy', 'unknown')} | "
                f"{item.get('reason', 'unknown')} | "
                f"누적 {item.get('count', 0)}건 "
                f"(최근 {recency}) "
                f"avg_pnl={item.get('avg_pnl_pct', 0):+.2f}%"
            )
        pattern_text = "\n".join(pattern_lines)
    else:
        pattern_text = "  아직 없음 (학습 중)"

    if lessons:
        lesson_text = "\n".join(f"  - {lesson}" for lesson in lessons[-8:])
    else:
        lesson_text = "  아직 없음"

    return pattern_text, lesson_text

def get_recent_selection_feedback_text(
    market: str,
    days: int = 20,
    max_chars: int = 900,
) -> str:
    try:
        import ticker_selection_db as _tsdb

        text = _tsdb.format_recent_selection_feedback(market, days=days)
        if not text:
            return ""
        if max_chars and len(text) > max_chars:
            text = text[:max_chars].rstrip()
        return text
    except Exception:
        return ""

# 이슈 패턴 업데이트

def _next_issue_pattern_id(patterns: list) -> str:
    max_id = 0
    for pattern in patterns or []:
        if not isinstance(pattern, dict):
            continue
        pattern_id = pattern.get("id")
        if not isinstance(pattern_id, str):
            continue
        if pattern_id.startswith("P") and pattern_id[1:].isdigit():
            max_id = max(max_id, int(pattern_id[1:]))
    return f"P{max_id + 1:03d}"


def update_issue_pattern(market: str, pattern_update: dict):
    """
    Claude postmortem 반환값으로 이슈 패턴 업데이트를 적용한다.
    pattern_update 예시:
    {
      "matched_id": "P001",       # 기존 패턴 ID (없으면 신규)
      "type": "개별기업_긍정뉴스",
      "description": "...",
      "bull_hit": true,
      "pnl_pct": 1.8,
      "insight_update": "..."     # insight 수정 (optional)
    }
    """
    brain = load()
    patterns = brain["markets"][market]["issue_patterns"]

    matched_id = pattern_update.get("matched_id")
    existing = None
    if matched_id:
        existing = next(
            (p for p in patterns if isinstance(p, dict) and p.get("id") == matched_id),
            None,
        )
    incoming_description = pattern_update.get("description", "")
    if not existing and not _is_prompt_safe_text(incoming_description):
        return

    if existing:
        # 기존 패턴 업데이트
        existing["count"] += 1
        field = "bull_hit" if pattern_update.get("bull_hit") else "bear_hit"
        existing[field] = existing.get(field, 0) + 1
        existing["bull_accuracy"] = round(
            existing.get("bull_hit", 0) / existing["count"], 3
        )
        existing["bear_accuracy"] = round(
            existing.get("bear_hit", 0) / existing["count"], 3
        )
        # 평균 PnL 업데이트
        prev = existing.get("avg_pnl_when_followed", 0.0)
        cnt  = existing["count"]
        existing["avg_pnl_when_followed"] = round(
            (prev * (cnt - 1) + pattern_update.get("pnl_pct", 0)) / cnt, 4
        )
        if pattern_update.get("insight_update") and _is_prompt_safe_text(pattern_update.get("insight_update")):
            existing["insight"] = pattern_update["insight_update"]
        if pattern_update.get("example"):
            existing.setdefault("examples", []).append(
                pattern_update["example"]
            )
            existing["examples"] = existing["examples"][-5:]  # 최근 5개만

    else:
        # 신규 패턴 추가
        new_id = _next_issue_pattern_id(patterns)
        pattern_type = pattern_update.get("type", "unknown")
        if _is_placeholder_text(pattern_type):
            pattern_type = "unknown"
        insight = pattern_update.get("insight", "")
        if not _is_prompt_safe_text(insight, allow_empty=True):
            insight = ""
        new_pattern = {
            "id":          new_id,
            "type":        pattern_type,
            "description": str(incoming_description).strip(),
            "count":       1,
            "bull_hit":    1 if pattern_update.get("bull_hit") else 0,
            "bear_hit":    1 if not pattern_update.get("bull_hit") else 0,
            "bull_accuracy": 1.0 if pattern_update.get("bull_hit") else 0.0,
            "bear_accuracy": 0.0 if pattern_update.get("bull_hit") else 1.0,
            "best_strategy": pattern_update.get("best_strategy", "unknown"),
            "best_mode":     pattern_update.get("best_mode", "unknown"),
            "avg_pnl_when_followed": pattern_update.get("pnl_pct", 0.0),
            "insight":  insight,
            "examples": [pattern_update["example"]]
                         if pattern_update.get("example") else []
        }
        patterns.append(new_pattern)

    save(brain)


# 튜닝 패턴 업데이트

def update_tuning_pattern(market: str, pattern_key: str,
                           correct: bool, new_insight: str = None,
                           new_threshold: float = None):
    brain = load()
    tp = brain["markets"][market]["tuning_patterns"]
    today_str = datetime.now().date().isoformat()

    if pattern_key not in tp:
        tp[pattern_key] = {
            "count": 0,
            "correct": 0,
            "rate": 0.0,
            "adjusted": 0,
            "adjusted_rate": 0.0,
            "metric_semantics": "adjusted_not_accuracy",
            "insight": "",
            "last_seen": today_str,
        }

    item = tp[pattern_key]
    item["adjusted"] = int(item.get("adjusted", item.get("correct", 0)) or 0)
    item["adjusted_rate"] = float(item.get("adjusted_rate", item.get("rate", 0.0)) or 0.0)
    item["metric_semantics"] = "adjusted_not_accuracy"
    item["count"] = int(item.get("count", 0) or 0) + 1
    if correct:
        item["adjusted"] += 1
    item["adjusted_rate"] = round(item["adjusted"] / item["count"], 3)
    item["correct"] = item["adjusted"]
    item["rate"] = item["adjusted_rate"]
    item["last_seen"] = today_str
    if new_insight and _is_prompt_safe_text(new_insight):
        item["insight"] = new_insight
    if new_threshold is not None:
        item["current_threshold"] = new_threshold

    save(brain)


# 최근 일별 기록 추가

def add_daily_record(market: str, record: dict):
    """
    record 예시:
    {
      "date": "2026-03-19",
      "mode": "MODERATE_BULL",
      "pnl_pct": 0.64,
      "win": true,
      "bull_result": "HIT",
      "bear_result": "MISS",
      "neutral_result": "PARTIAL",
      "bull_reason": "HBM4 계약 주가 견인",
      "bear_reason": "관세 발표 연기로 미스",
      "kospi_change": 0.82
    }
    """
    brain = load()
    recent = brain["markets"][market]["recent_days"]
    new_date = record.get("date", "")

    # 같은 날짜 레코드가 이미 있으면 덮어쓴다 (backfill 중복 방지)
    existing_idx = next((i for i, r in enumerate(recent) if r.get("date") == new_date), None)
    if existing_idx is not None:
        recent[existing_idx] = record
    else:
        recent.append(record)
        brain["meta"][f"trained_days_{'kr' if market == 'KR' else 'us'}"] += 1
        brain["markets"][market]["trained_days"] += 1

    brain["markets"][market]["recent_days"] = recent[-60:]  # 최근 60일만 보관
    save(brain)


# beliefs 업데이트

def update_beliefs(market: str, beliefs_update: dict):
    """
    Claude postmortem이 반환한 beliefs 업데이트.
    beliefs_update 예시:
    {
      "market_regime": "강세장",
      "bull_reliability": "high",
      "bear_reliability": "low",
      "best_strategy": "모멘텀",
      "new_lesson": "관망 경고가 유효했음",
      "add_avoid": "CAUTIOUS 과도 사용",
      "add_emphasize": "Bull 확정 근거"
    }
    """
    brain = load()
    beliefs = brain["markets"][market]["current_beliefs"]

    for key in ["market_regime", "bull_reliability",
                "bear_reliability", "best_strategy"]:
        if key in beliefs_update:
            beliefs[key] = beliefs_update[key]

    if "new_lesson" in beliefs_update:
        if _is_prompt_safe_text(beliefs_update["new_lesson"]):
            beliefs.setdefault("learned_lessons", []).append(
                str(beliefs_update["new_lesson"]).strip()
            )
        beliefs["learned_lessons"] = _clean_prompt_text_list(
            beliefs.get("learned_lessons", []),
            max_items=10,
        )

    if "add_avoid" in beliefs_update:
        beliefs.setdefault("avoid", [])
        if beliefs_update["add_avoid"] not in beliefs["avoid"]:
            beliefs["avoid"].append(beliefs_update["add_avoid"])

    if "add_emphasize" in beliefs_update:
        beliefs.setdefault("emphasize", [])
        if beliefs_update["add_emphasize"] not in beliefs["emphasize"]:
            beliefs["emphasize"].append(beliefs_update["add_emphasize"])

    save(brain)


# 개별 분석가 맞춤 피드백 생성

def _count_consecutive_result(recent_days: list, analyst_type: str,
                               target: str = "MISS") -> int:
    """최근 날짜부터 역순으로 연속 target 결과 개수를 계산한다."""
    key = f"{analyst_type}_result"
    count = 0
    for day in reversed(recent_days):
        if day.get(key) == target:
            count += 1
        else:
            break
    return count


def generate_analyst_summary(market: str, analyst_type: str) -> str:
    """
    개별 분석가에게 최근 성과와 주의점을 짧게 요약해 돌려준다.
    analyst_type: 'bull' | 'bear' | 'neutral'
    """
    brain = load()
    m = brain["markets"][market]
    perf = m["analyst_performance"][analyst_type]
    total = perf["total"]
    rate = perf["rate"] * 100
    r7 = perf["recent_7d"]["rate"] * 100
    r7n = perf["recent_7d"]["total"]
    trend = perf["trend"]
    recent_days = m.get("recent_days", [])

    if total < 5:
        return (
            f"[개인 성과] 데이터 부족({total}건). "
            f"아직 통계가 약하니 기본 성향 위주로 판단하세요."
        )

    consec_miss = _count_consecutive_result(recent_days, analyst_type, "MISS")
    consec_hit = _count_consecutive_result(recent_days, analyst_type, "HIT")

    if consec_miss >= 5:
        consec_msg = (
            f"강한 경고: 최근 {consec_miss}회 연속 실패입니다. "
            f"현재 시장에서 {analyst_type.upper()} 성향이 체계적으로 빗나가고 있습니다. "
            f"오늘은 최소 NEUTRAL 이하로 낮춰서 판단하고, "
            f"공격적 stance는 명확한 근거가 있을 때만 사용하세요."
        )
    elif consec_miss >= 3:
        consec_msg = (
            f"주의: 최근 {consec_miss}회 연속 실패입니다. "
            f"stance를 1~2단계 보수적으로 조정하세요."
        )
    elif consec_hit >= 3:
        consec_msg = (
            f"최근 {consec_hit}회 연속 적중입니다. 현재 판단 기조를 유지하세요."
        )
    else:
        consec_msg = ""

    if rate < 25:
        rate_msg = (
            f"누적 적중률이 매우 낮습니다({rate:.0f}%). "
            f"오늘은 기본적으로 NEUTRAL 방향으로 한 단계 완화하세요."
        )
    elif rate < 40:
        rate_msg = "누적 적중률이 낮습니다. 확신이 약하면 NEUTRAL 쪽으로 한 단계 완화하세요."
    elif rate > 65:
        rate_msg = "누적 적중률이 높습니다. 자신 있는 판단은 현재 기조를 유지하세요."
    else:
        rate_msg = "적중률이 보통 수준입니다. 신호가 명확할 때만 강한 stance를 선택하세요."

    if consec_miss >= 3:
        trend_msg = ""
    elif trend == "declining":
        trend_msg = "최근 판단이 빗나가는 추세입니다. stance를 한 단계 보수적으로 조정하세요."
    elif trend == "improving":
        trend_msg = "최근 판단이 좋아지고 있습니다. 현재 성향을 유지하세요."
    else:
        trend_msg = "판단 정확도가 안정적입니다. 현재 기조를 유지하세요."

    parts = [
        f"[{analyst_type.upper()} 개인 성과] 누적 {rate:.1f}% ({total}건) | 최근7일 {r7:.1f}% ({r7n}건)",
    ]
    if consec_msg:
        parts.append(consec_msg)
    if trend_msg:
        parts.append(trend_msg)
    parts.append(rate_msg)

    return "\n".join(parts)

def generate_prompt_summary(market: str) -> str:
    """Build the operational brain summary injected into Claude prompts."""
    brain = load()
    m = brain["markets"][market]

    if m.get("trained_days", 0) == 0:
        return f"[{market}] 아직 학습 데이터가 없습니다. 기본 규칙 중심으로 판단하세요."

    perf = m["analyst_performance"]
    modes = m["mode_performance"]
    beliefs = m["current_beliefs"]
    patterns = m.get("issue_patterns", [])
    recent = m.get("recent_days", [])[-5:]
    tuning = m.get("tuning_patterns", {})
    execution_txt, execution_lessons_txt = _build_execution_summary(m)
    selection_feedback_txt = get_recent_selection_feedback_text(market, days=20, max_chars=900)

    recent_lines = []
    for row in reversed(recent):
        win_mark = "WIN" if row.get("win") else "LOSS"
        recent_lines.append(
            f"  {row.get('date', '-')} {row.get('mode', 'unknown'):<18} "
            f"실현 {row.get('pnl_pct', 0):+.2f}%  {win_mark}"
        )
    recent_txt = "\n".join(recent_lines)

    top_patterns = sorted(
        [pattern for pattern in patterns if _is_valid_issue_pattern(pattern)],
        key=lambda item: item.get("count", 0),
        reverse=True,
    )[:3]
    pattern_txt = _format_issue_pattern_summary(top_patterns)
    tuning_txt = _format_tuning_pattern_summary(tuning, datetime.now().date())

    best_mode = max(
        modes.items(),
        key=lambda item: item[1].get("avg_pnl", -99) if item[1].get("count", 0) > 0 else -99,
        default=("unknown", {"avg_pnl": 0.0}),
    )

    def _consec_badge(atype: str) -> str:
        misses = _count_consecutive_result(m.get("recent_days", []), atype, "MISS")
        return f" {misses}x_miss" if misses >= 3 else ""

    mode_perf_lines = []
    for mode_name, mode_data in modes.items():
        if mode_data.get("count", 0) > 0:
            mode_perf_lines.append(
                f"  {mode_name:<14}{mode_data.get('count', 0):>3}건  "
                f"평균 {mode_data.get('avg_pnl', 0):+.2f}%  "
                f"승률 {mode_data.get('win_rate', 0)*100:.0f}%"
            )
        else:
            mode_perf_lines.append(f"  {mode_name:<14}  - (데이터 없음)")
    mode_perf_txt = "\n".join(mode_perf_lines)

    safe_lessons = _clean_prompt_text_list(beliefs.get("learned_lessons", []) or [])
    learned_lessons = "\n".join(f"  - {lesson}" for lesson in safe_lessons) or "  none"

    summary = f"""
============================================================
[{market} 시장 판단 메모리 | 학습 {m.get('trained_days', 0)}일]
============================================================

분석가 적중률 요약
  Bull:    {perf['bull']['rate']*100:.1f}%  (최근7일 {perf['bull']['recent_7d']['rate']*100:.1f}%  {perf['bull']['trend']}){_consec_badge('bull')}
  Bear:    {perf['bear']['rate']*100:.1f}%  (최근7일 {perf['bear']['recent_7d']['rate']*100:.1f}%  {perf['bear']['trend']}){_consec_badge('bear')}
  Neutral: {perf['neutral']['rate']*100:.1f}%  (최근7일 {perf['neutral']['recent_7d']['rate']*100:.1f}%  {perf['neutral']['trend']}){_consec_badge('neutral')}

모드별 평균 수익 (최상위 {best_mode[0]} {best_mode[1].get('avg_pnl', 0):+.2f}%)
{mode_perf_txt}

Recent issue patterns
{pattern_txt if pattern_txt else '  none'}

Tuning patterns
{tuning_txt if tuning_txt else '  none'}

Recent 5 sessions
{recent_txt if recent_txt else '  none'}

Current beliefs
  market_regime: {beliefs.get('market_regime', 'unknown')}
  bull_reliability: {beliefs.get('bull_reliability', 'unknown')}
  bear_reliability: {beliefs.get('bear_reliability', 'unknown')}
  best_strategy: {beliefs.get('best_strategy', 'unknown')}
  avoid: {', '.join(beliefs.get('avoid', [])) or 'none'}
  emphasize: {', '.join(beliefs.get('emphasize', [])) or 'none'}

Learned lessons
{learned_lessons}
============================================================
위 정보를 바탕으로 오늘 판단과 가중치를 조정하세요.
"""
    summary += f"""

Recent Selection Feedback
{selection_feedback_txt if selection_feedback_txt else '  아직 없음'}
"""
    summary += f"""

최근 실행 패턴
{execution_txt}

최근 실행 교훈
{execution_lessons_txt}
"""
    return summary

def save_debate_result(market: str, target_date: str, r1: dict, r2: dict):
    """
    R1/R2 토론 결과를 brain.json에 저장한다.
    r1, r2: {"bull":..., "bear":..., "neutral":...}
    """
    brain = load()
    m = brain["markets"][market]
    if "debate_history" not in m:
        m["debate_history"] = []

    changes = []
    for atype in ("bull", "bear", "neutral"):
        r1s = r1[atype].get("stance", "")
        r2s = r2[atype].get("stance", "")
        if r1s != r2s or r2[atype].get("changed"):
            changes.append({
                "analyst":   atype,
                "r1_stance": r1s,
                "r2_stance": r2s,
                "reason":    r2[atype].get("change_reason", ""),
            })

    entry = {
        "date":              target_date,
        "r1": {k: {"stance": r1[k].get("stance"), "confidence": r1[k].get("confidence"),
                   "key_reason": r1[k].get("key_reason", "")[:80]} for k in r1},
        "r2": {k: {"stance": r2[k].get("stance"), "confidence": r2[k].get("confidence"),
                   "key_reason": r2[k].get("key_reason", "")[:80]} for k in r2},
        "changes":           changes,
        "consensus_shifted": len(changes) > 0,
        "outcome":           None,   # postmortem 채점 전
    }

    # 최근 30개만 보관
    m["debate_history"].append(entry)
    m["debate_history"] = m["debate_history"][-30:]
    save(brain)


def get_debate_summary(market: str, n: int = 5) -> str:
    """
    최근 토론 기록 요약을 만들어 R2 프롬프트에 주입한다.
    핵심은 변경 후 성과와 변경 사유를 짧게 보여주는 것이다.
    """
    brain = load()
    history = brain["markets"][market].get("debate_history", [])
    if not history:
        return ""

    recent = history[-n:]
    lines = []

    change_results = [h for h in history if h["consensus_shifted"] and h["outcome"] is not None]
    keep_results = [h for h in history if not h["consensus_shifted"] and h["outcome"] is not None]
    change_hit_rate = (
        sum(1 for h in change_results if h["outcome"] == "correct") / len(change_results)
        if change_results else None
    )
    keep_hit_rate = (
        sum(1 for h in keep_results if h["outcome"] == "correct") / len(keep_results)
        if keep_results else None
    )

    stat_line = ""
    if change_hit_rate is not None:
        keep_rate_text = f"{keep_hit_rate*100:.0f}%" if keep_hit_rate is not None else "n/a"
        stat_line = (
            f"변경 후 적중률 {change_hit_rate*100:.0f}% ({len(change_results)}건) | "
            f"유지 시 적중률 {keep_rate_text} ({len(keep_results)}건)"
        )

    lines.append(f"[Debate history | recent {len(recent)}]")
    if stat_line:
        lines.append(f"  stats: {stat_line}")

    for h in reversed(recent):
        outcome_mark = {"correct": "OK", "wrong": "BAD"}.get(h.get("outcome"), "--")
        if h["changes"]:
            change_txt = ", ".join(
                f"{c['analyst'].upper()} {c['r1_stance']}->{c['r2_stance']} ({c['reason'][:30]})"
                for c in h["changes"]
            )
            lines.append(f"  {h['date']} {outcome_mark} changed {change_txt}")
        else:
            r1_modes = " ".join(f"{k}={v['stance']}" for k, v in h["r1"].items())
            lines.append(f"  {h['date']} {outcome_mark} kept: {r1_modes}")

    return "\n".join(lines)

def update_debate_outcome(market: str, target_date: str, correct: bool):
    """
    postmortem 결과에 맞춰 토론 결과가 맞았는지 업데이트한다.
    correct: True=합의 방향과 실제 결과가 일치
    """
    brain = load()
    history = brain["markets"][market].get("debate_history", [])
    for entry in reversed(history):
        if entry["date"] == target_date:
            entry["outcome"] = "correct" if correct else "wrong"
            save(brain)
            return


# hold_advisor 성과 누적

def update_hold_advisor_performance(
    market: str,
    ticker: str,
    decision: str,           # "HOLD" | "SELL"
    success: bool,
    extra_pnl_pct: float,    # HOLD: 목표가 이후 추가 수익%, SELL: 즉시 실현 수익%
):
    """
    TP 도달 후 hold_advisor 결정 결과를 brain.json에 누적한다.
    - HOLD 후 청산가 > tp_price: success=True
    - SELL 후 TP 즉시 실현 자체가 성공
    """
    brain = load()
    if "hold_advisor_performance" not in brain:
        brain["hold_advisor_performance"] = {
            "total": 0,
            "hold_count": 0, "hold_success": 0,
            "sell_count": 0,
            "hold_avg_extra_pnl": 0.0,
            "recent": [],
        }
    hp = brain["hold_advisor_performance"]

    hp["total"] += 1
    if decision == "HOLD":
        hp["hold_count"] += 1
        if success:
            hp["hold_success"] += 1
        # 누적 평균 추가 수익
        n = hp["hold_count"]
        hp["hold_avg_extra_pnl"] = round(
            (hp["hold_avg_extra_pnl"] * (n - 1) + extra_pnl_pct) / n, 4
        )
    else:
        hp["sell_count"] += 1

    # 최근 20건만 보관
    hp["recent"].append({
        "date":          date.today().isoformat(),
        "market":        market,
        "ticker":        ticker,
        "decision":      decision,
        "success":       success,
        "extra_pnl_pct": round(extra_pnl_pct, 4),
    })
    hp["recent"] = hp["recent"][-20:]

    save(brain)


# cross_market 업데이트

def update_cross_market(correlation: float, insight: str):
    brain = load()
    brain["cross_market"]["us_kr_correlation"] = round(correlation, 3)
    brain["cross_market"]["insight"] = insight
    brain["cross_market"]["learned"] = True
    save(brain)


# correction_guide 업데이트

def update_correction_guide(market: str, guide: dict):
    """
    최신 postmortem 결과를 바탕으로 Claude용 보정 지침을 업데이트한다.
    guide 예시:
    {
      "bull_adjustments": ["강한 추세일 때 모멘텀 가중치 1.3배"],
      "bear_adjustments": ["하락 변동성 확대 시 리스크 가중치 0.7배"],
      "tuning_rules":     ["첫 손절은 -0.5% 이상일 때만"],
      "today_notes":      "FOMC 발표 일정, 변동성 주의"
    }
    """
    guide = dict(guide or {})
    brain = load()
    correction_guide = brain.setdefault("correction_guide", {})
    current = correction_guide.setdefault(market, {})
    if _is_empty_correction_guide(guide) and not _is_empty_correction_guide(current):
        return
    brain["correction_guide"][market] = {
        **guide,
        "generated_date": date.today().isoformat()
    }
    save(brain)

def batch_update_all(market: str, updates: dict):
    """
    하루치 postmortem 결과를 한 번에 받아 brain.json을 업데이트한다.
    updates 예시:
    {
      "analyst_hits": {"bull": True, "bear": False, "neutral": True},
      "recent_days": [...],          # update_analyst_record용
      "mode": "MODERATE_BULL",
      "pnl_pct": 1.2,
      "win": True,
      "strategy": "momentum",
      "daily_record": {...},         # add_daily_record용
      "beliefs_update": {...},       # optional
      "correction_guide": {...},     # optional
    }
    """
    brain = load()
    recent_days = updates.get("recent_days", [])

def print_status():
    brain = load()
    meta  = brain["meta"]
    print(f"""
============================================================
          Brain 현재 상태
============================================================
버전:        v{meta['version']}
마지막 업데이트: {meta['last_updated']}
학습일수:    국내 {meta['trained_days_kr']}일 / 미국 {meta['trained_days_us']}일
    """)
    for mkt in ["KR", "US"]:
        m = brain["markets"][mkt]
        p = m["analyst_performance"]
        print(f"[{mkt}] trained={m['trained_days']}일 "
              f"Bull={p['bull']['rate']*100:.1f}%  "
              f"Bear={p['bear']['rate']*100:.1f}%  "
              f"Neutral={p['neutral']['rate']*100:.1f}%")


if __name__ == "__main__":
    print_status()
    print("\n[KR 요약]")
    print(generate_prompt_summary("KR"))
    print("\n[US 요약]")
    print(generate_prompt_summary("US"))
