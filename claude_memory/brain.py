"""
brain.py - Claude ?먮떒 硫붾え由?愿由?
brain.json ?쎄린 / ?낅뜲?댄듃 / ?붿빟 ?앹꽦
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


# ?? 湲곕낯 ?쎄린/?곌린 ????????????????????????????????????????????????????????????

def load() -> dict:
    with _BRAIN_LOCK:
        source = BRAIN_PATH if BRAIN_PATH.exists() else REPO_BRAIN_PATH
        with open(source, "r", encoding="utf-8") as f:
            return _ensure_extensions(json.load(f))


def save(brain: dict):
    with _BRAIN_LOCK:
        brain = _ensure_extensions(brain)
        brain["meta"]["last_updated"] = date.today().isoformat()
        brain["meta"]["version"] += 1
        with open(BRAIN_PATH, "w", encoding="utf-8") as f:
            json.dump(brain, f, ensure_ascii=False, indent=2)


# ?? 遺꾩꽍媛 ?깃낵 ?낅뜲?댄듃 ??????????????????????????????????????????????????????

def update_analyst(market: str, analyst: str, hit: bool, recent_days: list):
    """
    留ㅼ씪 postmortem ???몄텧
    analyst: 'bull' | 'bear' | 'neutral'
    hit: True=?곸쨷, False=誘몄쟻以?
    recent_days: 理쒓렐 30??湲곕줉 由ъ뒪??
    """
    brain = load()
    perf  = brain["markets"][market]["analyst_performance"][analyst]

    perf["total"] += 1
    if hit:
        perf["hit"] += 1
    else:
        perf["miss"] += 1
    perf["rate"] = round(perf["hit"] / perf["total"], 3)

    # 理쒓렐 7??
    r7 = [d for d in recent_days[-7:] if f"{analyst}_result" in d]
    if r7:
        h7 = sum(1 for d in r7 if d.get(f"{analyst}_result") == "HIT")
        perf["recent_7d"] = {"total": len(r7), "hit": h7,
                              "rate": round(h7 / len(r7), 3)}

    # 理쒓렐 30??
    r30 = [d for d in recent_days[-30:] if f"{analyst}_result" in d]
    if r30:
        h30 = sum(1 for d in r30 if d.get(f"{analyst}_result") == "HIT")
        perf["recent_30d"] = {"total": len(r30), "hit": h30,
                               "rate": round(h30 / len(r30), 3)}

    # ?몃젋???먮떒 (理쒓렐 30??湲곗? 鍮꾧탳)
    recent_30d_rate = perf.get("recent_30d", {}).get("rate", perf["rate"])
    if perf["recent_7d"]["rate"] > recent_30d_rate + 0.05:
        perf["trend"] = "improving"
    elif perf["recent_7d"]["rate"] < recent_30d_rate - 0.05:
        perf["trend"] = "declining"
    else:
        perf["trend"] = "stable"

    save(brain)


# ?? 紐⑤뱶 ?깃낵 ?낅뜲?댄듃 ????????????????????????????????????????????????????????

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


# ?? ?꾨왂 ?깃낵 ?낅뜲?댄듃 ????????????????????????????????????????????????????????

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

    lessons = m.setdefault("execution_lessons", [])
    if action == "buy_failed" and reason not in ("pending_order", "already_holding"):
        lessons.append(f"{strategy} 吏꾩엯 ?ㅽ뙣 ?⑦꽩: {reason}")
    elif action == "sell_failed":
        lessons.append(f"泥?궛 ?ㅽ뙣 ?⑦꽩: {reason}")
    elif action == "sell_filled" and pnl_pct < 0:
        lessons.append(f"?먯떎 泥?궛 ?먯씤 ?먭?: {reason}")
    elif action == "sell_filled" and pnl_pct > 0:
        lessons.append(f"?섏씡 泥?궛 ?좏슚 ?⑦꽩: {reason}")
    m["execution_lessons"] = lessons[-12:]
    save(brain)


def _build_execution_summary(market_data: dict) -> tuple[str, str]:
    patterns = market_data.get("execution_patterns", {}) or {}
    lessons = market_data.get("execution_lessons", []) or []

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
                f"?꾩쟻 {item.get('count', 0)}??"
                f"(留덉?留? {recency}) "
                f"avg_pnl={item.get('avg_pnl_pct', 0):+.2f}%"
            )
        pattern_text = "\n".join(pattern_lines)
    else:
        pattern_text = "  ?꾩쭅 ?놁쓬 (?숈뒿 以?"

    if lessons:
        lesson_text = "\n".join(f"  - {lesson}" for lesson in lessons[-8:])
    else:
        lesson_text = "  ?꾩쭅 ?놁쓬"

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

# ?? ?댁뒋 ?⑦꽩 ?낅뜲?댄듃 ????????????????????????????????????????????????????????

def update_issue_pattern(market: str, pattern_update: dict):
    """
    Claude postmortem??諛섑솚???⑦꽩 ?낅뜲?댄듃 ?곸슜
    pattern_update ?덉떆:
    {
      "matched_id": "P001",       ??湲곗〈 ?⑦꽩 ID (?놁쑝硫??좉퇋)
      "type": "媛쒕퀎湲곗뾽_?뺤젙?몄옱",
      "description": "...",
      "bull_hit": true,
      "pnl_pct": 1.8,
      "insight_update": "..."     ??insight ?섏젙 (optional)
    }
    """
    brain = load()
    patterns = brain["markets"][market]["issue_patterns"]

    matched_id = pattern_update.get("matched_id")
    existing   = next((p for p in patterns if p["id"] == matched_id), None)

    if existing:
        # 湲곗〈 ?⑦꽩 ?낅뜲?댄듃
        existing["count"] += 1
        field = "bull_hit" if pattern_update.get("bull_hit") else "bear_hit"
        existing[field] = existing.get(field, 0) + 1
        existing["bull_accuracy"] = round(
            existing.get("bull_hit", 0) / existing["count"], 3
        )
        existing["bear_accuracy"] = round(
            existing.get("bear_hit", 0) / existing["count"], 3
        )
        # ?됯퇏 pnl ?낅뜲?댄듃
        prev = existing.get("avg_pnl_when_followed", 0.0)
        cnt  = existing["count"]
        existing["avg_pnl_when_followed"] = round(
            (prev * (cnt - 1) + pattern_update.get("pnl_pct", 0)) / cnt, 4
        )
        if pattern_update.get("insight_update"):
            existing["insight"] = pattern_update["insight_update"]
        if pattern_update.get("example"):
            existing.setdefault("examples", []).append(
                pattern_update["example"]
            )
            existing["examples"] = existing["examples"][-5:]  # 理쒓렐 5媛쒕쭔

    else:
        # ?좉퇋 ?⑦꽩 異붽?
        new_id = f"P{len(patterns) + 1:03d}"
        new_pattern = {
            "id":          new_id,
            "type":        pattern_update.get("type", "unknown"),
            "description": pattern_update.get("description", ""),
            "count":       1,
            "bull_hit":    1 if pattern_update.get("bull_hit") else 0,
            "bear_hit":    1 if not pattern_update.get("bull_hit") else 0,
            "bull_accuracy": 1.0 if pattern_update.get("bull_hit") else 0.0,
            "bear_accuracy": 0.0 if pattern_update.get("bull_hit") else 1.0,
            "best_strategy": pattern_update.get("best_strategy", "unknown"),
            "best_mode":     pattern_update.get("best_mode", "unknown"),
            "avg_pnl_when_followed": pattern_update.get("pnl_pct", 0.0),
            "insight":  pattern_update.get("insight", ""),
            "examples": [pattern_update["example"]]
                         if pattern_update.get("example") else []
        }
        patterns.append(new_pattern)

    save(brain)


# ?? ?쒕떇 ?⑦꽩 ?낅뜲?댄듃 ????????????????????????????????????????????????????????

def update_tuning_pattern(market: str, pattern_key: str,
                           correct: bool, new_insight: str = None,
                           new_threshold: float = None):
    brain = load()
    tp = brain["markets"][market]["tuning_patterns"]
    today_str = datetime.now().date().isoformat()

    if pattern_key not in tp:
        tp[pattern_key] = {"count": 0, "correct": 0, "rate": 0.0, "insight": "", "last_seen": today_str}

    tp[pattern_key]["count"] += 1
    if correct:
        tp[pattern_key]["correct"] += 1
    tp[pattern_key]["rate"] = round(
        tp[pattern_key]["correct"] / tp[pattern_key]["count"], 3
    )
    tp[pattern_key]["last_seen"] = today_str
    if new_insight:
        tp[pattern_key]["insight"] = new_insight
    if new_threshold is not None:
        tp[pattern_key]["current_threshold"] = new_threshold

    save(brain)


# ?? 理쒓렐 ?쇰퀎 湲곕줉 異붽? ???????????????????????????????????????????????????????

def add_daily_record(market: str, record: dict):
    """
    record ?덉떆:
    {
      "date": "2026-03-19",
      "mode": "MODERATE_BULL",
      "pnl_pct": 0.64,
      "win": true,
      "bull_result": "HIT",
      "bear_result": "MISS",
      "neutral_result": "PARTIAL",
      "bull_reason": "HBM4 怨꾩빟 二쇨? 寃ъ씤",
      "bear_reason": "愿??諛쒗몴 ?곌린濡?誘몄뒪",
      "kospi_change": 0.82
    }
    """
    brain = load()
    recent = brain["markets"][market]["recent_days"]
    new_date = record.get("date", "")

    # 媛숈? ?좎쭨 ?덉퐫?쒓? ?대? ?덉쑝硫???뼱? (backfill 以묐났 諛⑹?)
    existing_idx = next((i for i, r in enumerate(recent) if r.get("date") == new_date), None)
    if existing_idx is not None:
        recent[existing_idx] = record
    else:
        recent.append(record)
        brain["meta"][f"trained_days_{'kr' if market == 'KR' else 'us'}"] += 1
        brain["markets"][market]["trained_days"] += 1

    brain["markets"][market]["recent_days"] = recent[-60:]  # 理쒓렐 60?쇰쭔 蹂닿?
    save(brain)


# ?? beliefs ?낅뜲?댄듃 ??????????????????????????????????????????????????????????

def update_beliefs(market: str, beliefs_update: dict):
    """
    Claude postmortem??諛섑솚??beliefs ?낅뜲?댄듃
    beliefs_update ?덉떆:
    {
      "market_regime": "媛뺤꽭??,
      "bull_reliability": "high",
      "bear_reliability": "low",
      "best_strategy": "紐⑤찘?",
      "new_lesson": "愿???⑤룆 寃쎄퀬???좊ː????쓬",
      "add_avoid": "CAUTIOUS 怨쇰룄 ?ъ슜",
      "add_emphasize": "Bull ?뺤젙?몄옱"
    }
    """
    brain = load()
    beliefs = brain["markets"][market]["current_beliefs"]

    for key in ["market_regime", "bull_reliability",
                "bear_reliability", "best_strategy"]:
        if key in beliefs_update:
            beliefs[key] = beliefs_update[key]

    if "new_lesson" in beliefs_update:
        beliefs.setdefault("learned_lessons", []).append(
            beliefs_update["new_lesson"]
        )
        beliefs["learned_lessons"] = beliefs["learned_lessons"][-10:]

    if "add_avoid" in beliefs_update:
        beliefs.setdefault("avoid", [])
        if beliefs_update["add_avoid"] not in beliefs["avoid"]:
            beliefs["avoid"].append(beliefs_update["add_avoid"])

    if "add_emphasize" in beliefs_update:
        beliefs.setdefault("emphasize", [])
        if beliefs_update["add_emphasize"] not in beliefs["emphasize"]:
            beliefs["emphasize"].append(beliefs_update["add_emphasize"])

    save(brain)


# ?? 媛쒕퀎 遺꾩꽍媛 留욎땄 ?쇰뱶諛??앹꽦 ??????????????????????????????????????????????

def _count_consecutive_result(recent_days: list, analyst_type: str,
                               target: str = "MISS") -> int:
    """理쒓렐 ?좎쭨遺????닚?쇰줈 ?곗냽 target 寃곌낵 ?잛닔 怨꾩궛."""
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
    媛?遺꾩꽍媛?먭쾶 ?먯떊??怨쇨굅 ?곸쨷瑜좊쭔 ?곕줈 ?쇰뱶諛?
    analyst_type: 'bull' | 'bear' | 'neutral'
    """
    brain = load()
    m     = brain["markets"][market]
    perf  = m["analyst_performance"][analyst_type]
    total = perf["total"]
    rate  = perf["rate"] * 100
    r7    = perf["recent_7d"]["rate"] * 100
    r7n   = perf["recent_7d"]["total"]
    trend = perf["trend"]
    recent_days = m.get("recent_days", [])

    if total < 5:
        return (f"[媛쒖씤 ?ㅼ쟻] ?곗씠??遺議?({total}?? ??"
                f"?꾩쭅 ?듦퀎媛 ?놁쑝??湲곕낯 ?깊뼢?쇰줈 ?먮떒?섏꽭??")

    # ?곗냽 ?ㅽ뙣/?깃났 媛먯?
    consec_miss = _count_consecutive_result(recent_days, analyst_type, "MISS")
    consec_hit  = _count_consecutive_result(recent_days, analyst_type, "HIT")

    # ?곗냽 ?ㅽ뙣 寃쎄퀬 (3???댁긽)
    if consec_miss >= 5:
        consec_msg = (
            f"??寃쎄퀬: 理쒓렐 {consec_miss}???곗냽 ?ㅽ뙣. "
            f"?꾩옱 ?쒖옣?먯꽌 ?뱀떊??{analyst_type.upper()} ?깊뼢? 泥닿퀎?곸쑝濡??由ш퀬 ?덉뒿?덈떎. "
            f"?ㅻ뒛? 諛섎뱶??NEUTRAL ?댄븯濡?stance瑜???텛?몄슂. "
            f"AGGRESSIVE/MODERATE_BULL ?좏깮 ??諛섎뱶??紐낇솗??洹쇨굅瑜??붽뎄?⑸땲??"
        )
    elif consec_miss >= 3:
        consec_msg = (
            f"?좑툘 二쇱쓽: 理쒓렐 {consec_miss}???곗냽 ?ㅽ뙣. "
            f"stance瑜?1~2?④퀎 蹂댁닔?곸쑝濡?議곗젙?섏꽭??"
        )
    elif consec_hit >= 3:
        consec_msg = (
            f"??理쒓렐 {consec_hit}???곗냽 ?곸쨷. ?꾩옱 ?먮떒 湲곗????좊ː?섏꽭??"
        )
    else:
        consec_msg = ""

    # ?꾩껜 ?곸쨷瑜좊퀎 硫붿떆吏
    if rate < 25:
        rate_msg = (
            f"?꾨컲 ?곸쨷瑜?留ㅼ슦 ??쓬({rate:.0f}%) ??"
            f"?ㅻ뒛? 諛섎뱶??NEUTRAL 諛⑺뼢?쇰줈 1?④퀎 ?꾪솕?섏꽭??"
        )
    elif rate < 40:
        rate_msg = "?꾨컲 ?곸쨷瑜???쓬 ???뺤떊???놁쑝硫?NEUTRAL 履쎌쑝濡????④퀎 ?꾪솕?섏꽭??"
    elif rate > 65:
        rate_msg = "?꾨컲 ?곸쨷瑜??믪쓬 ???먯떊???먮떒???좊ː?섏꽭??"
    else:
        rate_msg = "?곸쨷瑜?蹂댄넻 ???좏샇媛 紐낇솗???뚮쭔 媛뺥븳 stance瑜??좏깮?섏꽭??"

    # ?몃젋??硫붿떆吏 (?곗냽 ?ㅽ뙣媛 ?덉쑝硫?trend "stable" ?ㅽ뙋 諛⑹?)
    if consec_miss >= 3:
        trend_msg = ""   # ?곗냽 ?ㅽ뙣 硫붿떆吏濡?異⑸텇
    elif trend == "declining":
        trend_msg = "理쒓렐 ?먮떒??鍮쀫굹媛??異붿꽭 ??stance瑜?1?④퀎 蹂댁닔?곸쑝濡?議곗젙?섏꽭??"
    elif trend == "improving":
        trend_msg = "理쒓렐 ?먮떒????留욊퀬 ?덉뒿?덈떎 ???꾩옱 ?깊뼢???좊ː?섏꽭??"
    else:
        trend_msg = "?먮떒 ?뺥솗?꾧? ?덉젙?곸엯?덈떎 ???꾩옱 湲곗????좎??섏꽭??"

    parts = [
        f"[{analyst_type.upper()} 媛쒖씤 ?ㅼ쟻] "
        f"?꾩쟻 {rate:.1f}% ({total}?? | 理쒓렐7??{r7:.1f}% ({r7n}嫄?",
    ]
    if consec_msg:
        parts.append(consec_msg)
    if trend_msg:
        parts.append(f"??{trend_msg}")
    parts.append(rate_msg)

    return "\n".join(parts)


# ?? Claude ?꾨＼?꾪듃???붿빟 ?앹꽦 ???????????????????????????????????????????????

def generate_prompt_summary(market: str) -> str:
    """
    留ㅼ씪 ?꾩묠 釉뚮━????Claude?먭쾶 二쇱엯???붿빟 ?띿뒪???앹꽦
    """
    brain = load()
    m     = brain["markets"][market]
    meta  = brain["meta"]

    if m["trained_days"] == 0:
        return f"[{market}] ?꾩쭅 ?숈뒿 ?곗씠???놁쓬. 湲곕낯媛믪쑝濡??먮떒?섏꽭??"

    perf     = m["analyst_performance"]
    modes    = m["mode_performance"]
    beliefs  = m["current_beliefs"]
    patterns = m["issue_patterns"]
    recent   = m["recent_days"][-5:]
    tuning   = m["tuning_patterns"]
    execution_txt, execution_lessons_txt = _build_execution_summary(m)
    selection_feedback_txt = get_recent_selection_feedback_text(market, days=20, max_chars=900)

    # 理쒓렐 5???붿빟
    recent_txt = ""
    for r in reversed(recent):
        win_mark = "WIN" if r.get("win") else "LOSS"
        recent_txt += (
            f"  {r['date']} {r['mode']:<18} "
            f"?ㅼ젣 {r.get('pnl_pct', 0):+.2f}%  {win_mark}\n"
        )

    # ?⑦꽩 ?곸쐞 3媛???description ?녿뒗 ??ぉ(誘몃텇瑜? ?쒖쇅
    top_patterns = sorted(
        [p for p in patterns if p.get("description", "").strip()],
        key=lambda x: x["count"], reverse=True
    )[:3]
    pattern_txt = ""
    for p in top_patterns:
        pattern_txt += (
            f"  [{p['id']}] {p['type']} ({p['count']}??\n"
            f"    Bull?곸쨷 {p['bull_accuracy']*100:.0f}%  "
            f"?됯퇏?섏씡 {p.get('avg_pnl_when_followed',0):+.2f}%\n"
            f"    ?몄궗?댄듃: {p['insight']}\n"
        )

    # ?쒕떇 ?⑦꽩
    tuning_txt = ""
    today_dt = datetime.now().date()
    for k, v in tuning.items():
        if v["count"] == 0:
            continue
        rate = v["rate"]
        cnt = v["count"]
        correct = v["correct"]
        insight = v.get("insight", "")

        # last_seen 湲곕컲 recency ?쒖떆
        last_seen_str = v.get("last_seen", "")
        if last_seen_str:
            try:
                days_ago = (today_dt - datetime.strptime(last_seen_str, "%Y-%m-%d").date()).days
                recency_tag = "" if days_ago <= 7 else f" [??{days_ago}?????대젰]"
            except Exception:
                recency_tag = ""
        else:
            recency_tag = " [???좎쭨 誘명솗??"

        tuning_txt += (
            f"  {k}{recency_tag}: {cnt}??以?{correct}???곸쨷 "
            f"({rate*100:.0f}%) ??{insight}\n"
        )

    # 紐⑤뱶蹂??깃낵 ?곸쐞
    best_mode = max(modes.items(),
                    key=lambda x: x[1]["avg_pnl"]
                    if x[1]["count"] > 0 else -99)

    # ?곗냽 ?ㅽ뙣 諛곗?
    def _consec_badge(atype: str) -> str:
        n = _count_consecutive_result(m.get("recent_days", []), atype, "MISS")
        return f" {n}x_miss" if n >= 3 else ""

    # 紐⑤뱶蹂??깃낵 ?띿뒪??(f-string ??\n 湲덉? ?고쉶)
    _nl = "\n"
    mode_perf_lines = []
    for _mode, _v in modes.items():
        if _v["count"] > 0:
            mode_perf_lines.append(
                f"  {_mode:<14}{_v['count']:>3}?? ?됯퇏 {_v['avg_pnl']:+.2f}%  ?밸쪧 {_v['win_rate']*100:.0f}%"
            )
        else:
            mode_perf_lines.append(f"  {_mode:<14}  ?? (?곗씠???놁쓬)")
    mode_perf_txt = _nl.join(mode_perf_lines)

    summary = f"""
?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺
[{market} ?쒖옣 ?먮떒 硫붾え由???{m['trained_days']}???숈뒿]
?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺

?뱤 遺꾩꽍媛 ?꾩쟻 ?좊ː??
  ?윟 Bull:    {perf['bull']['rate']*100:.1f}%  (理쒓렐7??{perf['bull']['recent_7d']['rate']*100:.1f}%  {perf['bull']['trend']}){_consec_badge('bull')}
  ?뵶 Bear:    {perf['bear']['rate']*100:.1f}%  (理쒓렐7??{perf['bear']['recent_7d']['rate']*100:.1f}%  {perf['bear']['trend']}){_consec_badge('bear')}
  ??Neutral: {perf['neutral']['rate']*100:.1f}%  (理쒓렐7??{perf['neutral']['recent_7d']['rate']*100:.1f}%  {perf['neutral']['trend']}){_consec_badge('neutral')}

?룇 紐⑤뱶蹂??됯퇏 ?섏씡 (理쒖쟻: {best_mode[0]} {best_mode[1]['avg_pnl']:+.2f}%)
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
{chr(10).join(f'  - {l}' for l in beliefs.get('learned_lessons', [])) or '  none'}
?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺?곣봺
???뺣낫瑜?諛뷀깢?쇰줈 ?ㅻ뒛 ?먮떒 ??媛以묒튂瑜?議곗젙?섏꽭??
"""
    summary += f"""

📌 Recent Selection Feedback
{selection_feedback_txt if selection_feedback_txt else '  아직 없음'}
"""
    summary += f"""

?숋툘 ?ㅽ뻾 ?⑦꽩
{execution_txt}

?쭬 ?ㅽ뻾 援먰썕
{execution_lessons_txt}
"""
    return summary


# ?? ?좊줎 湲곕줉 愿由?????????????????????????????????????????????????????????????

def save_debate_result(market: str, target_date: str, r1: dict, r2: dict):
    """
    R1?뭃2 ?좊줎 寃곌낵瑜?brain.json?????
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
        "outcome":           None,   # postmortem ??梨꾩?
    }

    # 理쒓렐 30?쇱튂留?蹂댁〈
    m["debate_history"].append(entry)
    m["debate_history"] = m["debate_history"][-30:]
    save(brain)


def get_debate_summary(market: str, n: int = 5) -> str:
    """
    理쒓렐 n???좊줎 ?⑦꽩 ?붿빟 ??R2 ?꾨＼?꾪듃??二쇱엯
    '蹂寃????곸쨷瑜?, '?대뼡 ?쇨굅媛 ?ㅻ뱷???덉뿀?? ??
    """
    brain = load()
    history = brain["markets"][market].get("debate_history", [])
    if not history:
        return ""

    recent = history[-n:]
    lines  = []

    # 蹂寃?vs ?좎? ?곸쨷瑜?
    change_results  = [h for h in history if h["consensus_shifted"] and h["outcome"] is not None]
    keep_results    = [h for h in history if not h["consensus_shifted"] and h["outcome"] is not None]
    change_hit_rate = (sum(1 for h in change_results if h["outcome"] == "correct") / len(change_results)
                       if change_results else None)
    keep_hit_rate   = (sum(1 for h in keep_results if h["outcome"] == "correct") / len(keep_results)
                       if keep_results else None)

    stat_line = ""
    if change_hit_rate is not None:
        stat_line = (f"?섍껄 蹂寃????곸쨷瑜?{change_hit_rate*100:.0f}% ({len(change_results)}嫄? | "
                     f"?좎? ??{keep_hit_rate*100:.0f}% ({len(keep_results)}嫄?")

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
    postmortem ???대떦 ???좊줎 寃곌낵媛 留욎븯?붿? ?낅뜲?댄듃
    correct: True=?⑹쓽 諛⑺뼢???ㅼ젣 寃곌낵? ?쇱튂
    """
    brain = load()
    history = brain["markets"][market].get("debate_history", [])
    for entry in reversed(history):
        if entry["date"] == target_date:
            entry["outcome"] = "correct" if correct else "wrong"
            save(brain)
            return


# ?? hold_advisor ?깃낵 ?꾩쟻 ????????????????????????????????????????????????????

def update_hold_advisor_performance(
    market: str,
    ticker: str,
    decision: str,           # "HOLD" | "SELL"
    success: bool,
    extra_pnl_pct: float,    # HOLD: ?몃젅???댄썑 異붽? ?섏씡%, SELL: 利됱떆 ?ㅽ쁽 ?섏씡%
):
    """
    TP ?꾨떖 ??hold_advisor 寃곗젙 寃곌낵瑜?brain.json???꾩쟻.
    - HOLD ??泥?궛媛 > tp_price : success=True
    - SELL ??TP 利됱떆 ?ㅽ쁽 ?먯껜媛 ?깃났
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
        # ?꾩쟻 ?됯퇏 異붽??섏씡
        n = hp["hold_count"]
        hp["hold_avg_extra_pnl"] = round(
            (hp["hold_avg_extra_pnl"] * (n - 1) + extra_pnl_pct) / n, 4
        )
    else:
        hp["sell_count"] += 1

    # 理쒓렐 20嫄?蹂닿?
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


# ?? ?щ줈?ㅻ쭏耳??낅뜲?댄듃 ???????????????????????????????????????????????????????

def update_cross_market(correlation: float, insight: str):
    brain = load()
    brain["cross_market"]["us_kr_correlation"] = round(correlation, 3)
    brain["cross_market"]["insight"] = insight
    brain["cross_market"]["learned"] = True
    save(brain)


# ?? correction_guide ?낅뜲?댄듃 ?????????????????????????????????????????????????

def update_correction_guide(market: str, guide: dict):
    """
    留ㅼ씪 postmortem ???댁씪 Claude?먭쾶 以?蹂댁젙 吏移??먮룞 ?앹꽦
    guide ?덉떆:
    {
      "bull_adjustments": ["?뺤젙?몄옱 ?멸툒 ???좊ː??1.3諛?],
      "bear_adjustments": ["愿???⑤룆 寃쎄퀬 ?좊ː??0.7諛?],
      "tuning_rules":     ["泥??쒕떇? -0.5% ?댁긽???뚮쭔"],
      "today_notes":      "FOMC 諛쒗몴 ?덉젙, 蹂?숈꽦 二쇱쓽"
    }
    """
    brain = load()
    brain["correction_guide"][market] = {
        **guide,
        "generated_date": date.today().isoformat()
    }
    save(brain)


# ?? 諛곗튂 ?낅뜲?댄듃 (?몄뀡 醫낅즺 ????踰덉뿉 ??? ????????????????????????????????

def batch_update_all(market: str, updates: dict):
    """
    ?몄뀡 醫낅즺 postmortem 寃곌낵瑜???踰덉뿉 brain.json??諛섏쁺?⑸땲??
    updates ?덉떆:
    {
      "analyst_hits": {"bull": True, "bear": False, "neutral": True},
      "recent_days": [...],          # update_analyst??
      "mode": "MODERATE_BULL",
      "pnl_pct": 1.2,
      "win": True,
      "strategy": "momentum",
      "daily_record": {...},         # add_daily_record??
      "beliefs_update": {...},       # optional
      "correction_guide": {...},     # optional
    }
    """
    brain = load()
    recent_days = updates.get("recent_days", [])

    # 遺꾩꽍媛 ?깃낵
    analyst_hits = updates.get("analyst_hits", {})
    for analyst, hit in analyst_hits.items():
        perf = brain["markets"][market]["analyst_performance"][analyst]
        perf["total"] += 1
        if hit:
            perf["hit"] += 1
        else:
            perf["miss"] += 1
        perf["rate"] = round(perf["hit"] / perf["total"], 3)

        r7 = [d for d in recent_days[-7:] if f"{analyst}_result" in d]
        if r7:
            h7 = sum(1 for d in r7 if d.get(f"{analyst}_result") == "HIT")
            perf["recent_7d"] = {"total": len(r7), "hit": h7,
                                  "rate": round(h7 / len(r7), 3)}
        r30 = [d for d in recent_days[-30:] if f"{analyst}_result" in d]
        if r30:
            h30 = sum(1 for d in r30 if d.get(f"{analyst}_result") == "HIT")
            perf["recent_30d"] = {"total": len(r30), "hit": h30,
                                   "rate": round(h30 / len(r30), 3)}
        recent_30d_rate = perf.get("recent_30d", {}).get("rate", perf["rate"])
        if perf["recent_7d"]["rate"] > recent_30d_rate + 0.05:
            perf["trend"] = "improving"
        elif perf["recent_7d"]["rate"] < recent_30d_rate - 0.05:
            perf["trend"] = "declining"
        else:
            perf["trend"] = "stable"

    # 紐⑤뱶 ?깃낵
    mode = updates.get("mode")
    pnl_pct = updates.get("pnl_pct", 0.0)
    win = updates.get("win", False)
    if mode:
        mode_map = brain["markets"][market]["mode_performance"]
        if mode not in mode_map:
            mode_map[mode] = {"count": 0, "avg_pnl": 0.0, "win_rate": 0.0}
        mp = mode_map[mode]
        prev_count = mp["count"]
        mp["count"] += 1
        mp["avg_pnl"] = round((mp["avg_pnl"] * prev_count + pnl_pct) / mp["count"], 4)
        prev_wins = round(mp["win_rate"] * prev_count)
        mp["win_rate"] = round((prev_wins + (1 if win else 0)) / mp["count"], 3)

    # ?꾨왂 ?깃낵
    strategy = updates.get("strategy")
    if strategy:
        sp = brain["markets"][market]["strategy_performance"]
        if strategy not in sp:
            sp[strategy] = {"count": 0, "win_rate": 0.0, "avg_pnl": 0.0}
        s = sp[strategy]
        prev_count = s["count"]
        s["count"] += 1
        s["avg_pnl"] = round((s["avg_pnl"] * prev_count + pnl_pct) / s["count"], 4)
        prev_wins = round(s["win_rate"] * prev_count)
        s["win_rate"] = round((prev_wins + (1 if win else 0)) / s["count"], 3)

    # ?쇰퀎 湲곕줉
    daily_record = updates.get("daily_record")
    if daily_record:
        recent = brain["markets"][market]["recent_days"]
        recent.append(daily_record)
        brain["markets"][market]["recent_days"] = recent[-60:]
        brain["meta"][f"trained_days_{'kr' if market == 'KR' else 'us'}"] += 1
        brain["markets"][market]["trained_days"] += 1

    # beliefs ?낅뜲?댄듃
    beliefs_update = updates.get("beliefs_update")
    if beliefs_update:
        beliefs = brain["markets"][market]["current_beliefs"]
        for key in ["market_regime", "bull_reliability", "bear_reliability", "best_strategy"]:
            if key in beliefs_update:
                beliefs[key] = beliefs_update[key]
        if "new_lesson" in beliefs_update:
            beliefs.setdefault("learned_lessons", []).append(beliefs_update["new_lesson"])
            beliefs["learned_lessons"] = beliefs["learned_lessons"][-10:]
        if "add_avoid" in beliefs_update:
            beliefs.setdefault("avoid", [])
            if beliefs_update["add_avoid"] not in beliefs["avoid"]:
                beliefs["avoid"].append(beliefs_update["add_avoid"])
        if "add_emphasize" in beliefs_update:
            beliefs.setdefault("emphasize", [])
            if beliefs_update["add_emphasize"] not in beliefs["emphasize"]:
                beliefs["emphasize"].append(beliefs_update["add_emphasize"])

    # correction_guide
    correction_guide = updates.get("correction_guide")
    if correction_guide:
        brain["correction_guide"][market] = {
            **correction_guide,
            "generated_date": date.today().isoformat()
        }

    save(brain)


# ?? ?곹깭 異쒕젰 ?????????????????????????????????????????????????????????????????

def print_status():
    brain = load()
    meta  = brain["meta"]
    print(f"""
?붴븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븮
??          Brain ?꾩옱 ?곹깭                    ??
?싢븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븧?먥븴
踰꾩쟾:      v{meta['version']}
留덉?留??낅뜲?댄듃: {meta['last_updated']}
?숈뒿?쇱닔:  援?궡 {meta['trained_days_kr']}??/ 誘멸뎅 {meta['trained_days_us']}??
    """)
    for mkt in ["KR", "US"]:
        m = brain["markets"][mkt]
        p = m["analyst_performance"]
        print(f"[{mkt}] trained={m['trained_days']}?? "
              f"Bull={p['bull']['rate']*100:.1f}%  "
              f"Bear={p['bear']['rate']*100:.1f}%  "
              f"Neutral={p['neutral']['rate']*100:.1f}%")


if __name__ == "__main__":
    print_status()
    print("\n[KR ?붿빟]")
    print(generate_prompt_summary("KR"))
    print("\n[US ?붿빟]")
    print(generate_prompt_summary("US"))


