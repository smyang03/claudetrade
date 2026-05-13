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
import sys
import time
import uuid
from datetime import datetime, date
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
try:
    from logger import get_trading_logger
    log = get_trading_logger()
except Exception:
    log = logging.getLogger(__name__)

from minority_report.claude_utils import extract_json as _extract_json_shared

_PROD_DB_PATH = (_ROOT / "data" / "ml" / "decisions.db").resolve()
_DB_PATH  = _PROD_DB_PATH
_SESSION_STATE_PATH = _ROOT / "state" / "param_tuner_sessions.json"
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

class _ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        result = super().__exit__(exc_type, exc_value, traceback)
        self.close()
        return result


def _resolve_db_path() -> Path:
    override = os.environ.get("ML_DECISIONS_DB_PATH", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    legacy_path = Path(_DB_PATH).expanduser().resolve()
    if legacy_path != _PROD_DB_PATH:
        return legacy_path
    return _PROD_DB_PATH


def _get_conn() -> sqlite3.Connection:
    db_path = _resolve_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=10, factory=_ClosingConnection)
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
            cols = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(param_sessions)").fetchall()
            }
            if "session_key" not in cols:
                conn.execute("ALTER TABLE param_sessions ADD COLUMN session_key TEXT")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ps_session_key "
                "ON param_sessions (session_date, market, session_key)"
            )
    except Exception as e:
        log.warning("[param_tuner] DB 테이블 생성 실패: %s", e)


def _load_session_state() -> dict:
    try:
        with open(_SESSION_STATE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("[param_tuner] session state load 오류: %s", e)
        return {}


def _save_session_state(state: dict) -> None:
    try:
        _SESSION_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_SESSION_STATE_PATH, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning("[param_tuner] session state save 오류: %s", e)


def _session_key_for(market: str, session_date: Optional[str] = None, force_new: bool = False) -> str:
    current_date = session_date or date.today().isoformat()
    state = _load_session_state()
    item = state.get(market)
    if (
        not force_new
        and isinstance(item, dict)
        and item.get("session_date") == current_date
        and str(item.get("session_key", "")).strip()
    ):
        return str(item["session_key"]).strip()
    session_key = f"{current_date}:{uuid.uuid4().hex[:12]}"
    state[market] = {"session_date": current_date, "session_key": session_key}
    _save_session_state(state)
    return session_key


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
        session_key = _session_key_for(market, today)
        with _get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO param_sessions
                  (created_at, session_date, market, session_key, brain_mode, trigger,
                   vix, usd_krw, analyst_conf, strategy,
                   base_params, claude_params, claude_reason, was_adjusted)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    now, today, market, session_key, mode, trigger,
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
    market: str = "",
    session_date: str = "",
    strategy_outcomes: dict[str, dict] | None = None,
) -> None:
    """세션 종료 후 거래 결과 업데이트.

    session_ids가 비어있어도 market+session_date로 당일 NULL 행을 일괄 업데이트.
    (봇 재시작으로 _session_registry가 유실된 경우 대비)
    """
    def _vals(outcome: dict | None) -> list:
        if not outcome:
            return [0, 0, 0, 0, 0.0, 0.0]
        o_entries = int(outcome.get("entries", 0) or 0)
        o_wins = int(outcome.get("wins", 0) or 0)
        o_losses = int(outcome.get("losses", 0) or 0)
        return [
            int(outcome.get("signals", o_entries) or 0),
            o_entries,
            o_wins,
            o_losses,
            float(outcome.get("avg_pnl_pct", 0.0) or 0.0),
            float(outcome.get("total_pnl_krw", 0.0) or 0.0),
        ]

    try:
        with _get_conn() as conn:
            if session_ids and strategy_outcomes:
                placeholders = ",".join("?" * len(session_ids))
                rows = conn.execute(
                    f"SELECT id, strategy FROM param_sessions WHERE id IN ({placeholders})",
                    list(session_ids),
                ).fetchall()
                for sid, strategy in rows:
                    conn.execute(
                        """
                        UPDATE param_sessions
                        SET signals_count=?, entries_count=?, wins=?, losses=?,
                            avg_pnl_pct=?, total_pnl_krw=?
                        WHERE id=?
                        """,
                        _vals(strategy_outcomes.get(strategy)) + [sid],
                    )
            elif session_ids:
                placeholders = ",".join("?" * len(session_ids))
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
            elif market and session_date:
                session_key = _session_key_for(market, session_date)
                if strategy_outcomes:
                    rows = conn.execute(
                        """
                        SELECT id, strategy
                        FROM param_sessions
                        WHERE market=? AND session_date=? AND session_key=?
                          AND entries_count IS NULL
                        """,
                        [market, session_date, session_key],
                    ).fetchall()
                    for sid, strategy in rows:
                        conn.execute(
                            """
                            UPDATE param_sessions
                            SET signals_count=?, entries_count=?, wins=?, losses=?,
                                avg_pnl_pct=?, total_pnl_krw=?
                            WHERE id=?
                            """,
                            _vals(strategy_outcomes.get(strategy)) + [sid],
                        )
                else:
                    # 재시작으로 session_ids 유실 → 날짜+market 기준 NULL 행 일괄 업데이트
                    conn.execute(
                        """
                        UPDATE param_sessions
                        SET signals_count=?, entries_count=?, wins=?, losses=?,
                            avg_pnl_pct=?, total_pnl_krw=?
                        WHERE market=? AND session_date=? AND session_key=? AND entries_count IS NULL
                        """,
                        [signals, entries, wins, losses, avg_pnl_pct, total_pnl_krw,
                         market, session_date, session_key],
                    )
                log.info(f"[param_tuner] {market} {session_date} outcome 소급 업데이트 (재시작 복구)")
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
    asof    = context.get("asof", "-")

    hist_block = "\n".join(hist_lines) if hist_lines else "  (이력 없음)"
    param_block = "\n".join(param_lines)

    adjustable_keys = list(_GUARD.keys())
    strat_list = [s for s, p in all_params.items() if not p.get("disabled")]

    prompt = f"""당신은 퀀트 리스크 매니저입니다. 오늘의 시장 환경을 고려해
전략 파라미터를 미세 조정(또는 유지)하세요.

━━━ 현재 상황 (기준 {asof}) ━━━
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
            model=model, max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        parsed = _extract_json_shared(raw)

        # credit tracking
        try:
            from credit_tracker import record as _cr
            _cr(resp.usage.input_tokens, resp.usage.output_tokens, "param_tuner", model=model)
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
                model=model,
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
    _session_key_for(market, force_new=True)
    clear_cache(market)
