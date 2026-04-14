# -*- coding: utf-8 -*-
"""
strategy/param_tuner.py — Claude 파라미터 검토 레이어 (4th layer)

adaptive_params 3단계(base→regime→perf→guard) 결과를 Claude에게
한 번 더 검토 맡겨 시장 상황에 맞게 미세 조정한다.

호출 시점 (trading_bot.py에서 직접 호출):
  1. session_open  — 장 시작 시 1회 (전 전략 일괄)
  2. run_tuning    — 모드 변경 시 재검토
  3. REVERSE       — 긴급 반전 판단 시
  4. rescreen      — 장중 종목 재선택 시

환경변수:
  CLAUDE_PARAM_REVIEW=true   (기본 false → 이 레이어 비활성)
  ANTHROPIC_MODEL            (기본 claude-sonnet-4-6)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, date
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_ROOT     = Path(__file__).parent.parent
_DB_PATH  = _ROOT / "data" / "ml" / "decisions.db"
_ENABLED  = os.getenv("CLAUDE_PARAM_REVIEW", "false").lower() in ("1", "true", "yes")

# ── 가드레일: adaptive_params 출력 대비 허용 이동 범위 ────────────────────────
# Claude 가 제안해도 이 범위 밖은 클리핑
_GUARD: dict[str, tuple] = {
    "vol_mult":  ("mult", 0.70, 1.40),
    "tp_pct":    ("mult", 0.70, 1.50),
    "sl_pct":    ("mult", 0.70, 1.30),
    "size_mult": ("mult", 0.50, 1.20),
    "gap_min":   ("mult", 0.80, 1.40),
    "rsi_thr":   ("add",  5),
    "bb_thr":    ("add",  5),
    "max_hold":  ("mult", 0.50, 2.00),
}

# ── 전략명 한글 매핑 ─────────────────────────────────────────────────────────
_STRAT_KO = {
    "opening_range_pullback": "OR눌림",
    "gap_pullback":           "갭눌림",
    "momentum":               "모멘텀",
    "mean_reversion":         "평균회귀",
    "volatility_breakout":    "변동성돌파",
}

# ── 세션 내 캐시 (market → reviewed_params dict) ─────────────────────────────
_cache: dict[str, dict] = {}   # {"KR": {strategy: params, ...}, "US": {...}}
_cache_mode: dict[str, str] = {}


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_table() -> None:
    """param_sessions 테이블 생성 (없으면). init_db()에서도 호출됨."""
    ddl = """
    CREATE TABLE IF NOT EXISTS param_sessions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at      TEXT NOT NULL,
        session_date    TEXT NOT NULL,
        market          TEXT NOT NULL,
        brain_mode      TEXT NOT NULL,
        trigger         TEXT NOT NULL,
        vix             REAL,
        usd_krw         REAL,
        analyst_conf    REAL,
        strategy        TEXT NOT NULL,
        base_params     TEXT,
        claude_params   TEXT,
        claude_reason   TEXT,
        was_adjusted    INTEGER DEFAULT 0,
        signals_count   INTEGER,
        entries_count   INTEGER,
        wins            INTEGER,
        losses          INTEGER,
        avg_pnl_pct     REAL,
        total_pnl_krw   REAL
    );
    CREATE INDEX IF NOT EXISTS idx_ps_date_market
        ON param_sessions (session_date, market);
    """
    try:
        with _get_conn() as conn:
            conn.executescript(ddl)
    except Exception as e:
        log.warning("[param_tuner] DB 테이블 생성 실패: %s", e)


def _save_session(
    market: str,
    mode: str,
    trigger: str,
    strategy: str,
    base_params: dict,
    claude_params: dict,
    reason: str,
    context: dict,
) -> int:
    """검토 결과를 param_sessions에 기록. row id 반환."""
    try:
        was_adj = int(base_params != claude_params)
        now     = datetime.now().isoformat(timespec="seconds")
        today   = date.today().isoformat()
        with _get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO param_sessions
                  (created_at, session_date, market, brain_mode, trigger,
                   vix, usd_krw, analyst_conf, strategy,
                   base_params, claude_params, claude_reason, was_adjusted)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    now, today, market, mode, trigger,
                    context.get("vix"), context.get("usd_krw"),
                    context.get("analyst_conf"),
                    strategy,
                    json.dumps(base_params, ensure_ascii=False),
                    json.dumps(claude_params, ensure_ascii=False),
                    reason,
                    was_adj,
                ),
            )
            return cur.lastrowid or -1
    except Exception as e:
        log.warning("[param_tuner] save_session 오류: %s", e)
        return -1


def update_outcomes(
    session_ids: list[int],
    signals: int,
    entries: int,
    wins: int,
    losses: int,
    avg_pnl_pct: float,
    total_pnl_krw: float,
) -> None:
    """세션 종료 후 거래 결과 업데이트."""
    if not session_ids:
        return
    try:
        placeholders = ",".join("?" * len(session_ids))
        with _get_conn() as conn:
            conn.execute(
                f"""
                UPDATE param_sessions
                SET signals_count=?, entries_count=?, wins=?, losses=?,
                    avg_pnl_pct=?, total_pnl_krw=?
                WHERE id IN ({placeholders})
                """,
                [signals, entries, wins, losses, avg_pnl_pct, total_pnl_krw]
                + list(session_ids),
            )
    except Exception as e:
        log.warning("[param_tuner] update_outcomes 오류: %s", e)


def get_recent_history(market: str, days: int = 5) -> list[dict]:
    """최근 N일 param_sessions 요약 (Claude 프롬프트용)."""
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                """
                SELECT strategy, brain_mode, was_adjusted, claude_reason,
                       signals_count, entries_count, wins, losses, avg_pnl_pct
                FROM param_sessions
                WHERE market = ?
                  AND session_date >= date('now', ? || ' days')
                  AND entries_count IS NOT NULL
                ORDER BY session_date DESC
                LIMIT 20
                """,
                (market, f"-{days}"),
            ).fetchall()
        cols = ["strategy", "mode", "was_adjusted", "reason",
                "signals", "entries", "wins", "losses", "avg_pnl"]
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Guard rail
# ─────────────────────────────────────────────────────────────────────────────

def _apply_guard(proposed: dict, base: dict) -> dict:
    """base 대비 가드레일 적용. 범위 초과분 클리핑."""
    p = dict(proposed)
    for key, rule in _GUARD.items():
        if key not in p or key not in base:
            continue
        bv = base[key]
        if bv is None or bv == 0:
            continue
        if rule[0] == "mult":
            lo, hi = rule[1], rule[2]
            p[key] = round(max(bv * lo, min(bv * hi, p[key])), 4)
        elif rule[0] == "add":
            margin = rule[1]
            p[key] = max(bv - margin, min(bv + margin, p[key]))
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Claude 호출
# ─────────────────────────────────────────────────────────────────────────────

def _call_claude(
    market: str,
    mode: str,
    context: dict,
    all_params: dict[str, dict],
    history: list[dict],
) -> dict[str, dict]:
    """
    Claude에게 전 전략 파라미터를 일괄 검토 요청.

    Returns: {strategy: {"params": {...}, "reason": str}}
    """
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
        model  = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    except Exception as e:
        log.warning("[param_tuner] anthropic import 실패: %s", e)
        return {}

    # ── 파라미터 요약 블록 ────────────────────────────────────────────────────
    param_lines = []
    for strat, p in all_params.items():
        if p.get("disabled"):
            param_lines.append(f"  {_STRAT_KO.get(strat, strat)}: 비활성")
            continue
        parts = []
        for k in ("tp_pct", "sl_pct", "vol_mult", "gap_min", "rsi_thr",
                  "bb_thr", "size_mult", "max_hold"):
            if k in p:
                parts.append(f"{k}={p[k]}")
        param_lines.append(f"  {_STRAT_KO.get(strat, strat)}: {', '.join(parts)}")

    # ── 최근 성과 블록 ────────────────────────────────────────────────────────
    hist_lines = []
    for h in history[:8]:
        strat_ko = _STRAT_KO.get(h["strategy"], h["strategy"])
        adj_mark  = "✏️" if h["was_adjusted"] else "  "
        wr = f"{h['wins']}/{h['entries']} WR" if h.get("entries") else "데이터없음"
        pnl = f"avg={h['avg_pnl']:.1f}%" if h.get("avg_pnl") is not None else ""
        hist_lines.append(
            f"  {adj_mark} {strat_ko}({h['mode']}): {wr} {pnl} — {h.get('reason','')}"
        )

    vix     = context.get("vix", "-")
    usd_krw = context.get("usd_krw", "-")
    conf    = context.get("analyst_conf", "-")

    hist_block = "\n".join(hist_lines) if hist_lines else "  (이력 없음)"
    param_block = "\n".join(param_lines)

    adjustable_keys = list(_GUARD.keys())
    strat_list = [s for s, p in all_params.items() if not p.get("disabled")]

    prompt = f"""당신은 퀀트 리스크 매니저입니다. 오늘의 시장 환경을 고려해
전략 파라미터를 미세 조정(또는 유지)하세요.

━━━ 현재 상황 ━━━
  시장: {market}  모드: {mode}
  VIX: {vix}  USD/KRW: {usd_krw}  분석가 평균 confidence: {conf}

━━━ 3단계 산출 파라미터 (base→regime→perf→guard) ━━━
{param_block}

━━━ 최근 {len(history)}개 세션 성과 ━━━
{hist_block}

━━━ 조정 지침 ━━━
• 조정 가능 키: {adjustable_keys}
• 조정이 필요 없으면 기존 값 그대로 반환
• VIX > 30이면 vol_mult ↑, tp_pct ↓ 고려
• 최근 win rate 낮으면 진입 필터 강화 (vol_mult ↑, gap_min ↑)
• size_mult는 0.5 미만으로 내리지 마세요

활성 전략 목록: {strat_list}

JSON으로만 응답 (비활성 전략 제외):
{{
  "adjustments": {{
    "<strategy_name>": {{
      "params": {{"<key>": <value>, ...}},
      "reason": "한 문장"
    }},
    ...
  }},
  "overall_reason": "전체 판단 한 문장"
}}"""

    try:
        resp = client.messages.create(
            model=model, max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        if "```" in raw:
            raw = raw.split("```")[1].replace("json", "").strip()
        parsed = json.loads(raw)

        # credit tracking
        try:
            from credit_tracker import record as _cr
            _cr(resp.usage.input_tokens, resp.usage.output_tokens, "param_tuner")
        except Exception:
            pass

        # raw call logging
        try:
            from minority_report.raw_call_logger import save as _rcl
            _rcl(
                label="param_tuner",
                prompt=prompt, raw_response=raw, parsed=parsed,
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                market=market,
            )
        except Exception:
            pass

        return parsed.get("adjustments", {}), parsed.get("overall_reason", "")
    except Exception as e:
        log.warning("[param_tuner] Claude 호출 오류: %s", e)
        return {}, ""


# ─────────────────────────────────────────────────────────────────────────────
# 메인 진입점
# ─────────────────────────────────────────────────────────────────────────────

def claude_review(
    market: str,
    mode: str,
    all_params: dict[str, dict],
    context: Optional[dict] = None,
    trigger: str = "session_open",
    force: bool = False,
) -> dict[str, dict]:
    """
    Claude 파라미터 검토 — 4th layer.

    Parameters
    ----------
    market     : "KR" | "US"
    mode       : 현재 brain_mode
    all_params : {strategy: adaptive_params 출력} (3단계 적용 후)
    context    : {"vix": float, "usd_krw": float, "analyst_conf": float}
    trigger    : "session_open" | "mode_change" | "reverse" | "rescreen"
    force      : True이면 캐시 무시하고 재호출

    Returns
    -------
    {strategy: adjusted_params_dict}  — disabled 전략은 그대로 통과
    """
    global _cache, _cache_mode

    if not _ENABLED:
        return {s: p for s, p in all_params.items()}

    ctx = context or {}

    # 캐시 히트 — 같은 모드, force=False
    if not force and _cache_mode.get(market) == mode and market in _cache:
        log.debug("[param_tuner] 캐시 사용 — market=%s mode=%s", market, mode)
        return _cache[market]

    log.info("[param_tuner] Claude 검토 시작 — market=%s mode=%s trigger=%s",
             market, mode, trigger)

    # disabled 전략 분리
    active  = {s: p for s, p in all_params.items() if not p.get("disabled")}
    disabled = {s: p for s, p in all_params.items() if p.get("disabled")}

    history = get_recent_history(market, days=5)

    adjustments, overall_reason = _call_claude(market, mode, ctx, active, history)

    # 결과 병합 + 가드레일
    result: dict[str, dict] = {}
    session_ids: list[int] = []

    for strat, base_p in active.items():
        adj = adjustments.get(strat, {})
        if adj and isinstance(adj.get("params"), dict):
            proposed = dict(base_p)
            proposed.update(adj["params"])
            final_p = _apply_guard(proposed, base_p)
            reason  = adj.get("reason", overall_reason)
        else:
            final_p = base_p
            reason  = overall_reason or "조정 없음"

        result[strat] = final_p

        # DB 저장
        sid = _save_session(
            market=market, mode=mode, trigger=trigger,
            strategy=strat, base_params=base_p, claude_params=final_p,
            reason=reason, context=ctx,
        )
        if sid > 0:
            session_ids.append(sid)

    # disabled 전략 그대로 포함
    result.update(disabled)

    # 캐시 업데이트
    _cache[market]      = result
    _cache_mode[market] = mode

    # session_ids를 호출 측에 전달할 수 없으므로 로컬 저장
    _session_registry.setdefault(market, []).extend(session_ids)

    log.info(
        "[param_tuner] 완료 — market=%s mode=%s 조정된 전략=%s | %s",
        market, mode,
        [s for s in active if result.get(s) != active.get(s)],
        overall_reason,
    )
    return result


def clear_cache(market: Optional[str] = None) -> None:
    """캐시 초기화 (장 종료 또는 강제 재검토 시)."""
    global _cache, _cache_mode
    if market:
        _cache.pop(market, None)
        _cache_mode.pop(market, None)
    else:
        _cache.clear()
        _cache_mode.clear()


# ── 세션 id 레지스트리 (outcome 업데이트용) ──────────────────────────────────
_session_registry: dict[str, list[int]] = {}


def get_session_ids(market: str) -> list[int]:
    """현재 세션에서 기록된 param_session id 목록."""
    return list(_session_registry.get(market, []))


def reset_session(market: str) -> None:
    """세션 시작 시 레지스트리 초기화."""
    _session_registry[market] = []
    clear_cache(market)
