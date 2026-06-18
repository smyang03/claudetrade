from __future__ import annotations

"""교훈 forward-validation 레이어 (컴포넌트①②의 코어).

설계: docs/important/LESSON_QUALITY_CONFIG_PIPELINE_DESIGN_20260617.md

핵심: 현 lesson 시스템은 빈도/severity로만 채점(forward 검증 0). 이 모듈은 교훈을 **반사실
forward gain**(했더라면 − 실제)으로 채점하고, validity가 국면×시장 의존이므로 **국면 인덱싱**해
별도 store에 누적한다. 라이브 매매에는 무접촉 — 점수만 매겨 store에 쌓고, 적용 훅은 config가
enforce일 때만 조정값을 반환(기본 OFF면 {} → 현행 동작 불변).

안전계약:
- 기본 OFF(`LESSON_VALIDATION_ENABLED=false`, `LESSON_VALIDATION_APPLY_MODE=off`).
- `get_runtime_adjustments()`는 enforce가 아니면 항상 {} → 켜기 전엔 라이브 영향 0.
- 조정은 CLAUDE.md bounded control 범위 내로만 clamp. hard safety/brain 무접촉.
- brain.json 자동변이 아님 — 별도 store(`data/lesson_validation.db`)에만 흐름.
"""

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

try:
    from runtime_paths import get_runtime_path
except Exception:  # 테스트/독립 실행 fallback
    get_runtime_path = None


# ---- config (기본 전부 OFF/보수적) ----

def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "y", "on")


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        return default


def is_enabled() -> bool:
    return _env_bool("LESSON_VALIDATION_ENABLED", False)


def apply_mode() -> str:
    """off | shadow | enforce. 기본 off."""
    mode = str(os.getenv("LESSON_VALIDATION_APPLY_MODE", "off")).strip().lower()
    return mode if mode in ("off", "shadow", "enforce") else "off"


def _min_wo() -> int:
    return _env_int("LESSON_VALIDATION_MIN_WO", 30)


def _min_tr() -> int:
    return _env_int("LESSON_VALIDATION_MIN_TR", 10)


def _gain_threshold() -> float:
    return _env_float("LESSON_VALIDATION_GAIN_THRESHOLD", 1.0)


def _min_sessions() -> int:
    return _env_int("LESSON_VALIDATION_MIN_SESSIONS", 2)


def _cost_floor() -> float:
    """왕복 거래비용(%). would_be가 이걸 넘어야 '절대 수익' = 진짜 적용가치(forward≠net 보정)."""
    return _env_float("LESSON_VALIDATION_COST_FLOOR_PCT", 0.5)


def _max_age_days() -> int:
    """검증 셀 신선도(일). 이보다 오래된 셀은 적용 무시 → 디폴트(기존값) fallback. 0=무제한."""
    return _env_int("LESSON_VALIDATION_MAX_AGE_DAYS", 45)


def _min_confidence() -> float:
    """적용 최소 confidence. 미만 valid 셀은 적용 안 함(= 디폴트 fallback). 신뢰 낮은 셀 차단."""
    return _env_float("LESSON_VALIDATION_MIN_CONFIDENCE", 0.3)


def _is_fresh(updated_at: str | None) -> bool:
    """updated_at이 max_age 내면 True. 파싱 실패/없음은 보수적으로 stale(False) 처리."""
    max_age = _max_age_days()
    if max_age <= 0:
        return True
    if not updated_at:
        return False
    try:
        ts = datetime.fromisoformat(str(updated_at))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - ts).days
        return age <= max_age
    except Exception:
        return False


# ---- lesson → bounded control 매핑 (컴포넌트③, CLAUDE.md Claude post-tuning 범위) ----
# value: (control_name, base_step, bound_abs). enforce일 때만 사용.
LESSON_CONTROL_MAP: dict[str, tuple[str, float, float]] = {
    # '승격/완화'형 교훈(would_be vs actual 프레임) → trade_ready cutoff 소폭 완화(− = 완화)
    "watch_only_missed_runup_ratio": ("entry_priority_cutoff_adjust", -0.02, 0.05),
    "trade_ready_signal_conversion": ("entry_priority_cutoff_adjust", -0.02, 0.05),
    # 후속 패밀리(별도 스코어링 필요, 현재 미활성):
    #  - unanimous(만장일치 컨트라리안): 신호-present forward 프레임 + size_adj 노출축소. score_cell
    #    (would_be vs actual)에 안 맞아 score_signal_cell 별도 구현 필요.
    #  - signal_fired: 진입필터 가치 = '완화 금지' guard 성격(독립 조정 아님).
}


# ---- 순수 스코어링 ----

def regime_from_consensus_mode(consensus_mode: str | None) -> str | None:
    """런타임 정합 regime — consensus_mode → risk_on/risk_off/mixed.

    백테스트(selection_log.consensus_mode)와 런타임(today_judgment consensus mode)이 같은
    `infer_market_regime`을 거쳐 같은 키를 쓰게 한다(인덱스 5일모멘텀은 런타임 미가용이라 미채택).
    mode가 비면 None → 적용 no-op(디폴트=기존값 유지).
    """
    if not consensus_mode:
        return None
    try:
        from runtime.adaptive_live_condition import infer_market_regime

        return infer_market_regime(str(consensus_mode))
    except Exception:
        return None


def index_momentum_regime(index_ret5_prior_pct: float | None) -> str | None:
    """공유 국면 라벨러 — 백테스트(session_regime)와 런타임이 같은 분류를 쓰게 한다.

    입력 = 진입일 *전일까지* 지수 5일 수익률(%). None이면 None(→ 적용 no-op, 안전).
    runtime infer_market_regime(RISK_ON/OFF)과 분류가 달라(STRONG_UP vs UP 구분이 핵심) 별도.
    """
    if index_ret5_prior_pct is None:
        return None
    r = index_ret5_prior_pct
    if r >= 3:
        return "STRONG_UP"
    if r >= 0:
        return "UP"
    if r > -3:
        return "DOWN"
    return "STRONG_DOWN"


def _median(values: list[float]) -> float | None:
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    n = len(vals)
    mid = n // 2
    return vals[mid] if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def counterfactual_gain(would_be_fwd: list[float], actual_fwd: list[float]) -> float | None:
    """gain = median(했더라면) − median(실제). + = 교훈 적용이 forward 개선."""
    wb, ac = _median(would_be_fwd), _median(actual_fwd)
    if wb is None or ac is None:
        return None
    return wb - ac


def sign_consistency_sessions(subperiod_gains: list[float | None], overall_gain: float | None) -> int:
    """부호 일관 sub-기간 수 — overall_gain과 같은 부호인 sub-기간만 카운트.

    sessions_confirmed의 올바른 정의: '존재한 월 수'가 아니라 '같은 부호로 재확인된 독립기간 수'.
    뒤집히는(불안정) 셀이 '확인됨'으로 통과하는 것을 막는다.
    """
    if overall_gain is None or not subperiod_gains:
        return 0
    sign = 1 if overall_gain >= 0 else -1
    return sum(1 for g in subperiod_gains if g is not None and (1 if g >= 0 else -1) == sign)


def score_cell(
    lesson_key: str,
    market: str,
    regime: str,
    would_be_fwd: list[float],
    actual_fwd: list[float],
    *,
    sessions_confirmed: int = 1,
    min_wo: int | None = None,
    min_tr: int | None = None,
    gain_threshold: float | None = None,
    min_sessions: int | None = None,
    cost_floor: float | None = None,
) -> dict[str, Any]:
    """한 (교훈, 시장, 국면) 셀의 반사실 채점. 순수 함수(IO 없음).

    verdict:
      insufficient(표본부족) / pending(부호확인부족) / invalid_block(함정) /
      valid_apply(상대개선 + 절대수익=would_be가 비용초과) /
      marginal(상대개선이지만 would_be가 비용미달 = '덜 잃음'일 뿐, 자동적용 안 함) / neutral
    """
    min_wo = _min_wo() if min_wo is None else min_wo
    min_tr = _min_tr() if min_tr is None else min_tr
    gain_threshold = _gain_threshold() if gain_threshold is None else gain_threshold
    min_sessions = _min_sessions() if min_sessions is None else min_sessions
    cost_floor = _cost_floor() if cost_floor is None else cost_floor

    n_wo, n_tr = len(would_be_fwd), len(actual_fwd)
    gain = counterfactual_gain(would_be_fwd, actual_fwd)
    would_be_med = _median(would_be_fwd)

    if n_wo < min_wo or n_tr < min_tr or gain is None:
        verdict = "insufficient"
    elif sessions_confirmed < min_sessions:
        verdict = "pending"  # 부호 일관 독립확인 부족
    elif gain <= -gain_threshold:
        verdict = "invalid_block"
    elif gain >= gain_threshold:
        # 절대 수익 게이트: would_be가 비용을 넘어야 진짜 적용가치(forward≠net 보정)
        if would_be_med is not None and would_be_med >= cost_floor:
            verdict = "valid_apply"
        else:
            verdict = "marginal"  # 덜 잃을 뿐 — 자동 적용 안 함
    else:
        verdict = "neutral"

    # confidence: 표본·gain크기·재확인 모두 반영, [0,1]
    if gain is None:
        confidence = 0.0
    else:
        c_n = min(1.0, min(n_wo, n_tr * 3) / max(min_wo, 1))
        c_g = min(1.0, abs(gain) / max(2 * gain_threshold, 0.5))
        c_s = min(1.0, sessions_confirmed / max(min_sessions, 1))
        confidence = round(c_n * c_g * c_s, 4)

    return {
        "lesson_key": lesson_key,
        "market": market,
        "regime": regime,
        "counterfactual_gain": None if gain is None else round(gain, 4),
        "would_be_med": None if would_be_med is None else round(would_be_med, 4),
        "n_tr": n_tr,
        "n_wo": n_wo,
        "sessions_confirmed": sessions_confirmed,
        "confidence": confidence,
        "verdict": verdict,
        "mapped_control": LESSON_CONTROL_MAP.get(lesson_key, (None,))[0],
    }


def _adjustment_for(cell: dict[str, Any]) -> tuple[str, float] | None:
    """valid_apply 셀 → (control, clamped adjustment). 아니면 None. 저신뢰 셀은 적용 안 함."""
    if cell.get("verdict") != "valid_apply":
        return None
    if (cell.get("confidence") or 0.0) < _min_confidence():
        return None  # 신뢰 부족 → 디폴트 fallback
    spec = LESSON_CONTROL_MAP.get(cell.get("lesson_key") or "")
    if not spec:
        return None
    control, base_step, bound = spec
    gain = cell.get("counterfactual_gain") or 0.0
    conf = cell.get("confidence") or 0.0
    raw = base_step * min(1.0, abs(gain) / 2.0) * conf
    raw = max(-bound, min(bound, raw))
    return control, round(raw, 5)


# ---- store (격리 DB, brain 무접촉) ----

_SCHEMA = """
CREATE TABLE IF NOT EXISTS validated_lesson (
    lesson_key TEXT, market TEXT, regime TEXT,
    counterfactual_gain REAL, n_tr INTEGER, n_wo INTEGER,
    sessions_confirmed INTEGER, confidence REAL, verdict TEXT,
    mapped_control TEXT, updated_at TEXT,
    PRIMARY KEY (lesson_key, market, regime)
)
"""


def _default_db_path() -> str:
    if get_runtime_path is not None:
        try:
            return str(get_runtime_path("data", "lesson_validation.db", make_parents=True))
        except Exception:
            pass
    return os.path.join("data", "lesson_validation.db")


def _connect(db_path: str | None) -> sqlite3.Connection:
    """쓰기용 — 스키마 보장 + WAL + busy timeout(동시접근 시 락 회피)."""
    con = sqlite3.connect(db_path or _default_db_path(), timeout=5.0)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
    except Exception:
        pass
    con.executescript(_SCHEMA)
    return con


def _connect_ro(db_path: str | None):
    """읽기용 — read-only. DB 없으면 None(→ 호출측이 빈 결과). 라이브 읽기가 배치 쓰기에 안 막힘."""
    path = db_path or _default_db_path()
    if not os.path.exists(path):
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
        con.execute("PRAGMA busy_timeout=5000")
        return con
    except Exception:
        return None


def upsert_cells(cells: list[dict[str, Any]], db_path: str | None = None) -> int:
    """채점 결과를 store에 누적. 같은 셀 재확인 시 sessions_confirmed 보존/증가는 호출측 책임."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    con = _connect(db_path)
    try:
        con.executemany(
            "INSERT INTO validated_lesson "
            "(lesson_key,market,regime,counterfactual_gain,n_tr,n_wo,sessions_confirmed,confidence,verdict,mapped_control,updated_at) "
            "VALUES(:lesson_key,:market,:regime,:counterfactual_gain,:n_tr,:n_wo,:sessions_confirmed,:confidence,:verdict,:mapped_control,:updated_at) "
            "ON CONFLICT(lesson_key,market,regime) DO UPDATE SET "
            "counterfactual_gain=excluded.counterfactual_gain,n_tr=excluded.n_tr,n_wo=excluded.n_wo,"
            "sessions_confirmed=excluded.sessions_confirmed,confidence=excluded.confidence,"
            "verdict=excluded.verdict,mapped_control=excluded.mapped_control,updated_at=excluded.updated_at",
            [dict(c, updated_at=now) for c in cells],
        )
        con.commit()
        return len(cells)
    finally:
        con.close()


def get_validated_cells(market: str, regime: str, db_path: str | None = None) -> list[dict[str, Any]]:
    """라이브 읽기 — read-only. DB/테이블 없으면 빈 리스트(크래시·락 없음 = 디폴트 fallback)."""
    con = _connect_ro(db_path)
    if con is None:
        return []
    try:
        has = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='validated_lesson'"
        ).fetchone()
        if not has:
            return []
        cols = [d[1] for d in con.execute("PRAGMA table_info(validated_lesson)").fetchall()]
        rows = con.execute(
            "SELECT * FROM validated_lesson WHERE market=? AND regime=?", (market, regime)
        ).fetchall()
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []
    finally:
        con.close()


def _compute_adjustments(market: str, regime: str | None, db_path: str | None = None) -> dict[str, float]:
    """국면셀의 validated 교훈 → bounded control 조정(gain가중 합산 + block-priority + clamp).

    컴포넌트④ 조합: 같은 control에 valid_apply가 여럿이면 gain×confidence 가중합. 단 같은 control에
    invalid_block 셀이 하나라도 있으면 그 control은 **차단(무조정)** = 함정 방어(block 우선).
    """
    if regime is None:
        return {}
    # 신선도: stale 셀은 적용에서 제외(= 디폴트 기존값 fallback). 단 invalid_block은 stale이어도
    # 보수적으로 유지(함정 방어는 오래돼도 끄지 않음).
    cells = [
        c for c in get_validated_cells(market, regime, db_path)
        if c.get("verdict") == "invalid_block" or _is_fresh(c.get("updated_at"))
    ]
    # control별 차단 집합 (invalid_block 셀의 mapped_control)
    blocked: set[str] = set()
    for cell in cells:
        if cell.get("verdict") == "invalid_block":
            spec = LESSON_CONTROL_MAP.get(cell.get("lesson_key") or "")
            if spec:
                blocked.add(spec[0])
    out: dict[str, float] = {}
    bounds: dict[str, float] = {}
    for cell in cells:
        adj = _adjustment_for(cell)
        if adj is None:
            continue
        control, value = adj
        if control in blocked:
            continue  # block 우선
        out[control] = out.get(control, 0.0) + value
        spec = LESSON_CONTROL_MAP.get(cell.get("lesson_key") or "")
        if spec:
            bounds[control] = spec[2]
    for control, bound in bounds.items():
        out[control] = round(max(-bound, min(bound, out[control])), 5)
    return out


def get_shadow_adjustments(market: str, regime: str | None, db_path: str | None = None) -> dict[str, float]:
    """shadow/관측용 — enabled면 '적용했을' 조정을 반환(실제 반영 아님, 로깅용). disabled면 {}."""
    if not is_enabled():
        return {}
    return _compute_adjustments(market, regime, db_path)


def get_runtime_adjustments(market: str, regime: str | None, db_path: str | None = None) -> dict[str, float]:
    """라이브 적용 훅. **enforce가 아니면 항상 {} → 라이브 영향 0.**"""
    if not is_enabled() or apply_mode() != "enforce":
        return {}
    return _compute_adjustments(market, regime, db_path)


def apply_to_tuner_overrides(
    result: dict[str, Any],
    market: str,
    regime: str | None,
    db_path: str | None = None,
    logger=None,
) -> dict[str, Any]:
    """tuner 결과(bounded overrides)에 validated 교훈을 layer.

    - disabled/off: 무접촉(no-op).
    - shadow: '적용했을' 조정을 로깅만, result 무변경.
    - enforce: result의 해당 control에 조정을 더하고 control별 bound로 재clamp.
    regime이 None(미정합/데이터부족)이면 어느 모드든 no-op → 안전.
    """
    if not is_enabled() or regime is None:
        return result
    mode = apply_mode()
    if mode == "off":
        return result
    if mode == "shadow":
        shadow = get_shadow_adjustments(market, regime, db_path)
        if shadow and logger is not None:
            try:
                logger.info(f"[lesson_validation shadow] {market}/{regime} would_apply={shadow}")
            except Exception:
                pass
        return result
    # enforce
    adj = get_runtime_adjustments(market, regime, db_path)
    for control, value in adj.items():
        if control not in result:
            continue
        bound = next((b for (c, _s, b) in LESSON_CONTROL_MAP.values() if c == control), None)
        merged = (result.get(control) or 0.0) + value
        if bound is not None:
            merged = max(-bound, min(bound, merged))
        result[control] = round(merged, 5)
    if adj and logger is not None:
        try:
            logger.info(f"[lesson_validation enforce] {market}/{regime} applied={adj}")
        except Exception:
            pass
    return result
