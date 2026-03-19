"""
brain.py - Claude 판단 메모리 관리
brain.json 읽기 / 업데이트 / 요약 생성
"""

import json
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional

BRAIN_PATH = Path(__file__).parent / "brain.json"


# ── 기본 읽기/쓰기 ────────────────────────────────────────────────────────────

def load() -> dict:
    with open(BRAIN_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def save(brain: dict):
    brain["meta"]["last_updated"] = date.today().isoformat()
    brain["meta"]["version"] += 1
    with open(BRAIN_PATH, "w", encoding="utf-8") as f:
        json.dump(brain, f, ensure_ascii=False, indent=2)


# ── 분석가 성과 업데이트 ──────────────────────────────────────────────────────

def update_analyst(market: str, analyst: str, hit: bool, recent_days: list):
    """
    매일 postmortem 후 호출
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
    r7 = [d for d in recent_days[-7:] if analyst in d]
    if r7:
        h7 = sum(1 for d in r7 if d.get(f"{analyst}_result") == "HIT")
        perf["recent_7d"] = {"total": len(r7), "hit": h7,
                              "rate": round(h7 / len(r7), 3)}

    # 최근 30일
    r30 = [d for d in recent_days[-30:] if analyst in d]
    if r30:
        h30 = sum(1 for d in r30 if d.get(f"{analyst}_result") == "HIT")
        perf["recent_30d"] = {"total": len(r30), "hit": h30,
                               "rate": round(h30 / len(r30), 3)}

    # 트렌드 판단
    if perf["recent_7d"]["rate"] > perf["rate"] + 0.05:
        perf["trend"] = "improving"
    elif perf["recent_7d"]["rate"] < perf["rate"] - 0.05:
        perf["trend"] = "declining"
    else:
        perf["trend"] = "stable"

    save(brain)


# ── 모드 성과 업데이트 ────────────────────────────────────────────────────────

def update_mode_performance(market: str, mode: str, pnl_pct: float, win: bool):
    brain = load()
    mp = brain["markets"][market]["mode_performance"][mode]

    prev_count = mp["count"]
    mp["count"] += 1
    mp["avg_pnl"] = round(
        (mp["avg_pnl"] * prev_count + pnl_pct) / mp["count"], 4
    )
    prev_wins = round(mp["win_rate"] * prev_count)
    mp["win_rate"] = round((prev_wins + (1 if win else 0)) / mp["count"], 3)

    save(brain)


# ── 전략 성과 업데이트 ────────────────────────────────────────────────────────

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


# ── 이슈 패턴 업데이트 ────────────────────────────────────────────────────────

def update_issue_pattern(market: str, pattern_update: dict):
    """
    Claude postmortem이 반환한 패턴 업데이트 적용
    pattern_update 예시:
    {
      "matched_id": "P001",       ← 기존 패턴 ID (없으면 신규)
      "type": "개별기업_확정호재",
      "description": "...",
      "bull_hit": true,
      "pnl_pct": 1.8,
      "insight_update": "..."     ← insight 수정 (optional)
    }
    """
    brain = load()
    patterns = brain["markets"][market]["issue_patterns"]

    matched_id = pattern_update.get("matched_id")
    existing   = next((p for p in patterns if p["id"] == matched_id), None)

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
        # 평균 pnl 업데이트
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
            existing["examples"] = existing["examples"][-5:]  # 최근 5개만

    else:
        # 신규 패턴 추가
        new_id = f"P{len(patterns) + 1:03d}"
        new_pattern = {
            "id":          new_id,
            "type":        pattern_update.get("type", "미분류"),
            "description": pattern_update.get("description", ""),
            "count":       1,
            "bull_hit":    1 if pattern_update.get("bull_hit") else 0,
            "bear_hit":    1 if not pattern_update.get("bull_hit") else 0,
            "bull_accuracy": 1.0 if pattern_update.get("bull_hit") else 0.0,
            "bear_accuracy": 0.0 if pattern_update.get("bull_hit") else 1.0,
            "best_strategy": pattern_update.get("best_strategy", "미확정"),
            "best_mode":     pattern_update.get("best_mode", "미확정"),
            "avg_pnl_when_followed": pattern_update.get("pnl_pct", 0.0),
            "insight":  pattern_update.get("insight", ""),
            "examples": [pattern_update["example"]]
                         if pattern_update.get("example") else []
        }
        patterns.append(new_pattern)

    save(brain)


# ── 튜닝 패턴 업데이트 ────────────────────────────────────────────────────────

def update_tuning_pattern(market: str, pattern_key: str,
                           correct: bool, new_insight: str = None,
                           new_threshold: float = None):
    brain = load()
    tp = brain["markets"][market]["tuning_patterns"]

    if pattern_key not in tp:
        tp[pattern_key] = {"count": 0, "correct": 0, "rate": 0.0, "insight": ""}

    tp[pattern_key]["count"] += 1
    if correct:
        tp[pattern_key]["correct"] += 1
    tp[pattern_key]["rate"] = round(
        tp[pattern_key]["correct"] / tp[pattern_key]["count"], 3
    )
    if new_insight:
        tp[pattern_key]["insight"] = new_insight
    if new_threshold is not None:
        tp[pattern_key]["current_threshold"] = new_threshold

    save(brain)


# ── 최근 일별 기록 추가 ───────────────────────────────────────────────────────

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
    recent.append(record)
    brain["markets"][market]["recent_days"] = recent[-60:]  # 최근 60일만 보관
    brain["meta"][f"trained_days_{'kr' if market == 'KR' else 'us'}"] += 1
    brain["markets"][market]["trained_days"] += 1
    save(brain)


# ── beliefs 업데이트 ──────────────────────────────────────────────────────────

def update_beliefs(market: str, beliefs_update: dict):
    """
    Claude postmortem이 반환한 beliefs 업데이트
    beliefs_update 예시:
    {
      "market_regime": "강세장",
      "bull_reliability": "high",
      "bear_reliability": "low",
      "best_strategy": "모멘텀",
      "new_lesson": "관세 단독 경고는 신뢰도 낮음",
      "add_avoid": "CAUTIOUS 과도 사용",
      "add_emphasize": "Bull 확정호재"
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


# ── Claude 프롬프트용 요약 생성 ───────────────────────────────────────────────

def generate_prompt_summary(market: str) -> str:
    """
    매일 아침 브리핑 시 Claude에게 주입할 요약 텍스트 생성
    """
    brain = load()
    m     = brain["markets"][market]
    meta  = brain["meta"]

    if m["trained_days"] == 0:
        return f"[{market}] 아직 학습 데이터 없음. 기본값으로 판단하세요."

    perf     = m["analyst_performance"]
    modes    = m["mode_performance"]
    beliefs  = m["current_beliefs"]
    patterns = m["issue_patterns"]
    recent   = m["recent_days"][-5:]
    tuning   = m["tuning_patterns"]

    # 최근 5일 요약
    recent_txt = ""
    for r in reversed(recent):
        win_mark = "✅" if r.get("win") else "❌"
        recent_txt += (
            f"  {r['date']} {r['mode']:<18} "
            f"실제 {r.get('pnl_pct', 0):+.2f}%  {win_mark}\n"
        )

    # 패턴 상위 3개
    top_patterns = sorted(
        patterns, key=lambda x: x["count"], reverse=True
    )[:3]
    pattern_txt = ""
    for p in top_patterns:
        pattern_txt += (
            f"  [{p['id']}] {p['type']} ({p['count']}회)\n"
            f"    Bull적중 {p['bull_accuracy']*100:.0f}%  "
            f"평균수익 {p.get('avg_pnl_when_followed',0):+.2f}%\n"
            f"    인사이트: {p['insight']}\n"
        )

    # 튜닝 패턴
    tuning_txt = ""
    for k, v in tuning.items():
        if v["count"] > 0:
            tuning_txt += (
                f"  {k}: {v['count']}회 중 {v['correct']}회 적중 "
                f"({v['rate']*100:.0f}%) → {v['insight']}\n"
            )

    # 모드별 성과 상위
    best_mode = max(modes.items(),
                    key=lambda x: x[1]["avg_pnl"]
                    if x[1]["count"] > 0 else -99)

    summary = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[{market} 시장 판단 메모리 — {m['trained_days']}일 학습]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📊 분석가 누적 신뢰도
  🟢 Bull:    {perf['bull']['rate']*100:.1f}%  (최근7일 {perf['bull']['recent_7d']['rate']*100:.1f}%  {perf['bull']['trend']})
  🔴 Bear:    {perf['bear']['rate']*100:.1f}%  (최근7일 {perf['bear']['recent_7d']['rate']*100:.1f}%  {perf['bear']['trend']})
  ⚪ Neutral: {perf['neutral']['rate']*100:.1f}%  (최근7일 {perf['neutral']['recent_7d']['rate']*100:.1f}%  {perf['neutral']['trend']})

🏆 모드별 평균 수익 (최적: {best_mode[0]} {best_mode[1]['avg_pnl']:+.2f}%)
  AGGRESSIVE    {modes['AGGRESSIVE']['count']:>3}회  평균 {modes['AGGRESSIVE']['avg_pnl']:+.2f}%  승률 {modes['AGGRESSIVE']['win_rate']*100:.0f}%
  MODERATE_BULL {modes['MODERATE_BULL']['count']:>3}회  평균 {modes['MODERATE_BULL']['avg_pnl']:+.2f}%  승률 {modes['MODERATE_BULL']['win_rate']*100:.0f}%
  CAUTIOUS      {modes['CAUTIOUS']['count']:>3}회  평균 {modes['CAUTIOUS']['avg_pnl']:+.2f}%  승률 {modes['CAUTIOUS']['win_rate']*100:.0f}%
  DEFENSIVE     {modes['DEFENSIVE']['count']:>3}회  평균 {modes['DEFENSIVE']['avg_pnl']:+.2f}%  승률 {modes['DEFENSIVE']['win_rate']*100:.0f}%
  HALT          {modes['HALT']['count']:>3}회  평균 {modes['HALT']['avg_pnl']:+.2f}%  승률 {modes['HALT']['win_rate']*100:.0f}%

💡 반복 이슈 패턴 (상위 3)
{pattern_txt if pattern_txt else '  아직 없음 (학습 중)'}
🔧 튜닝 패턴
{tuning_txt if tuning_txt else '  아직 없음 (학습 중)'}
📅 최근 5일
{recent_txt if recent_txt else '  아직 없음'}
🧠 현재 시장 이해
  장세:        {beliefs.get('market_regime', '미확정')}
  Bull 신뢰도: {beliefs.get('bull_reliability', '미확정')}
  Bear 신뢰도: {beliefs.get('bear_reliability', '미확정')}
  최적 전략:   {beliefs.get('best_strategy', '미확정')}
  주의사항:    {', '.join(beliefs.get('avoid', [])) or '없음'}
  강조사항:    {', '.join(beliefs.get('emphasize', [])) or '없음'}

📚 학습된 교훈
{chr(10).join(f'  • {l}' for l in beliefs.get('learned_lessons', [])) or '  아직 없음'}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
이 정보를 바탕으로 오늘 판단 시 가중치를 조정하세요.
"""
    return summary


# ── 크로스마켓 업데이트 ───────────────────────────────────────────────────────

def update_cross_market(correlation: float, insight: str):
    brain = load()
    brain["cross_market"]["us_kr_correlation"] = round(correlation, 3)
    brain["cross_market"]["insight"] = insight
    brain["cross_market"]["learned"] = True
    save(brain)


# ── correction_guide 업데이트 ─────────────────────────────────────────────────

def update_correction_guide(market: str, guide: dict):
    """
    매일 postmortem 후 내일 Claude에게 줄 보정 지침 자동 생성
    guide 예시:
    {
      "bull_adjustments": ["확정호재 언급 시 신뢰도 1.3배"],
      "bear_adjustments": ["관세 단독 경고 신뢰도 0.7배"],
      "tuning_rules":     ["첫 튜닝은 -0.5% 이상일 때만"],
      "today_notes":      "FOMC 발표 예정, 변동성 주의"
    }
    """
    brain = load()
    brain["correction_guide"][market] = {
        **guide,
        "generated_date": date.today().isoformat()
    }
    save(brain)


# ── 상태 출력 ─────────────────────────────────────────────────────────────────

def print_status():
    brain = load()
    meta  = brain["meta"]
    print(f"""
╔══════════════════════════════════════════════╗
║           Brain 현재 상태                    ║
╚══════════════════════════════════════════════╝
버전:      v{meta['version']}
마지막 업데이트: {meta['last_updated']}
학습일수:  국내 {meta['trained_days_kr']}일 / 미국 {meta['trained_days_us']}일
    """)
    for mkt in ["KR", "US"]:
        m = brain["markets"][mkt]
        p = m["analyst_performance"]
        print(f"[{mkt}] trained={m['trained_days']}일  "
              f"Bull={p['bull']['rate']*100:.1f}%  "
              f"Bear={p['bear']['rate']*100:.1f}%  "
              f"Neutral={p['neutral']['rate']*100:.1f}%")


if __name__ == "__main__":
    print_status()
    print("\n[KR 요약]")
    print(generate_prompt_summary("KR"))
    print("\n[US 요약]")
    print(generate_prompt_summary("US"))
