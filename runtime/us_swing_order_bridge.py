from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from bot.session_date import KST
from kis_api import get_price
from logger import get_trading_logger
from preopen.scheduler import regular_open_dt
from runtime_paths import get_runtime_path
from runtime.us_swing_order_handoff import (
    evaluate_handoff,
    load_handoff_signals,
    record_handoff_result,
    resolve_handoff_authority,
)


log = get_trading_logger()
OPERATOR_MICRO_OVERRIDE_ACK = "I_ACCEPT_MICRO_WITHOUT_FORWARD"


def _write_execution_status(
    bot: Any,
    *,
    session_date: str,
    result: dict[str, Any],
    research_authority: dict[str, Any] | None = None,
    execution_authority: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist live execution truth separately from research authority."""

    research = dict(research_authority or {})
    execution = dict(execution_authority or research)
    raw_submit = bot._runtime_bool("US_SWING_ORDER_SUBMIT_ENABLED", False)
    live_ack = str(bot._runtime_value("US_SWING_ORDER_LIVE_ACK", "") or "")
    configured_max_order_krw = float(bot._runtime_float("US_SWING_ORDER_MAX_KRW", 250000.0))
    absolute_order_cap_krw = float(execution.get("absolute_order_cap_krw") or 0.0)
    if absolute_order_cap_krw > 0:
        effective_order_cap_krw = min(configured_max_order_krw, absolute_order_cap_krw)
        order_cap_source = "operator_absolute_cap"
    else:
        effective_order_cap_krw = min(
            configured_max_order_krw,
            float(getattr(getattr(bot, "risk", None), "max_order_krw", 0.0) or 0.0)
            * float(execution.get("size_multiplier") or 0.0),
        )
        order_cap_source = "risk_budget_multiplier"
    payload = {
        "schema_version": "us_swing_execution_status_v1",
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "session_date": str(session_date or ""),
        "configured_mode": str(bot._runtime_value("US_SWING_AUTHORITY_MODE", "shadow") or "shadow").lower(),
        "research_authority": research,
        "execution_authority": execution,
        "operator_override_applied": bool(execution.get("operator_forward_override")),
        "allowed_to_emit_orders": bool(execution.get("allowed_to_emit_orders")),
        "submit_enabled": bool(raw_submit),
        "live_ack_verified": bool(getattr(bot, "is_paper", False)) or live_ack == "I_ACCEPT_LIVE_US_SWING",
        "max_order_krw": configured_max_order_krw,
        "effective_order_cap_krw": effective_order_cap_krw,
        "order_cap_source": order_cap_source,
        "entry_window_min": {
            "start": int(bot._runtime_int("US_SWING_ORDER_MIN_OPEN_MIN", 5)),
            "end": int(bot._runtime_int("US_SWING_ORDER_MAX_OPEN_MIN", 30)),
        },
        "last_result": dict(result),
        "status": str(result.get("status") or "UNKNOWN"),
        "reason": str(result.get("reason") or ""),
    }
    path = get_runtime_path("state", "us_swing_execution_status.json")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
    except Exception as exc:
        log.error(f"[US swing handoff] execution status write failed: {exc}")
    return result


def _operator_micro_override(bot: Any, authority: dict[str, Any], configured_mode: str) -> dict[str, Any]:
    """Permit a bounded MICRO trial (slots 3, one new per day) while preserving every non-forward block.

    The tracker was silent before its scheduler repair, so an operator may
    explicitly accept missing forward maturity.  Historical, sealed execution,
    quote, cash, slot, common-buy and live-ACK guards remain mandatory.
    """

    if str(configured_mode or "").lower() != "micro":
        return authority
    ack = str(bot._runtime_value("US_SWING_OPERATOR_MICRO_OVERRIDE_ACK", "") or "")
    blockers = [str(item) for item in authority.get("blockers") or []]
    if ack != OPERATOR_MICRO_OVERRIDE_ACK or not blockers:
        return authority
    allowed = {
        "forward_sessions_insufficient",
        "forward_matured_insufficient",
        "forward_mean_below_hurdle",
        "forward_profit_factor_below_hurdle",
    }
    if any(blocker not in allowed for blocker in blockers):
        return authority
    return {
        **authority,
        "eligible_mode": "micro_operator_trial",
        "effective_mode": "micro",
        "allowed_to_emit_orders": True,
        "size_multiplier": 0.10,
        "absolute_order_cap_krw": float(bot._runtime_float("US_SWING_ORDER_MAX_KRW", 250000.0)),
        "order_cap_source": "operator_config_absolute",
        # 2026-08-02 운영자 결정(토론 합의안): 슬롯 3/일1건. 일일 신규 리스크는 불변이고
        # D5 보유가 겹치며 최대 3포지션 동시 보유. 일일 확대(3/일)는 day_losers 전환 후
        # forward ≥30건 + 순성과 양수 확인 후에만 재론한다.
        # 2026-08-20 운영자 결정(B안): 슬롯 3→5, 주문상한 100만→76만. 일1건은 불변.
        #   D5 보유 x 일1건의 정상상태 동시보유가 5개라 슬롯 3이 진입률을 0.6건/일로
        #   깎고 있었다(실측 0.54건/일). 투입 300만→380만(+27%), 표본 +85%.
        # 최악 동시 SL = 주문상한 × 슬롯 × 25%.
        #   08-14 30만→50만(37.5만) → 08-17 100만·슬롯3(75만) → **08-20 76만·슬롯5 = 95만**
        #   (KIS 총자산 약 18.4%). 상한이나 슬롯을 바꿀 때 이 값도 같이 고친다.
        "max_new_per_day": 1,
        "max_open_slots": 5,
        "operator_forward_override": True,
        "operator_forward_override_blockers": blockers,
        "warnings": [*(authority.get("warnings") or []), "operator_micro_forward_override_active"],
    }


def _dollar_volume_by_ticker(con: sqlite3.Connection, session_date: str) -> dict[str, float]:
    """급락일 거래대금(백만달러) — candidate_pool_all에 러너가 기록한 값.

    러너(22:20)가 브리지(22:35)보다 먼저 도므로 당일 값이 존재한다.
    결손이면 빈 dict을 돌려주고 호출부가 fail-open(현행 rank1)으로 간다.
    """
    try:
        rows = con.execute(
            "SELECT ticker, dollar_vol FROM candidate_pool_all WHERE session_date=?",
            (str(session_date),),
        ).fetchall()
    except sqlite3.Error:
        return {}
    out: dict[str, float] = {}
    for ticker, dvol in rows:
        try:
            value = float(dvol or 0.0)
        except (TypeError, ValueError):
            continue
        if value > 0:
            out[str(ticker or "").upper()] = value / 1e6
    return out


def _max_daily_return_21d(ticker: str, session_date: str) -> float | None:
    """MAX = 신호일 직전 21거래일 중 최대 일간 상승률(%). 가격 CSV에서 계산.

    Path A의 anti-chase가 쓰는 max_daily_ret_21d와 같은 개념이지만, 스윙 브리지는
    Path A 후보 dict에 접근하지 않으므로 여기서 직접 만든다. no-lookahead:
    session_date **미만** 바만 사용한다.
    """
    try:
        path = get_runtime_path("data", "price", "us", f"us_{str(ticker).upper()}.csv")
        if not path.exists():
            return None
        import csv

        rows = []
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                date = str(row.get("date") or "")[:10]
                if date and date < str(session_date):
                    try:
                        rows.append((date, float(row.get("close") or 0.0)))
                    except (TypeError, ValueError):
                        continue
        rows.sort()
        closes = [c for _, c in rows[-21:] if c > 0]
        if len(closes) < 6:
            return None
        gains = [(closes[i] / closes[i - 1] - 1) * 100 for i in range(1, len(closes))]
        return max(gains) if gains else None
    except Exception:
        return None


def _apply_max_lottery_floor(
    bot: Any, session_date: str, signals: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """MAX(복권형) 하한 — 저MAX 급락주는 반전이 약하다.

    2026-08-20 리서치(227세션 백테스트, 밴드 표본 n=137):
      MAX 3분위가 단조 증가 — 하위 +0.38% / 중위 +4.43% / 상위 +6.00%
      ATR을 통제해도 살아남는다: ATR중상 ∩ MAX하위 −0.13% vs MAX상위 +6.30%
      (클러스터t 5.97, 승 74%). 반대로 MAX 통제 시 ATR 차이는 2.1%p로 축소.
      → MAX가 진짜 변수이고 ATR은 그림자. ATR 축은 별도로 기각됨.
    밴드 + MAX>=8: 클러스터t 2.63 → 4.61, 합계 −4%, 빈도 60%→48%.

    문헌: 복권 수요가 패자 가격을 더 왜곡 → 더 큰 반전(MAX 효과 / lottery demand).

    fail-open: MAX를 못 구한 종목은 통과시킨다(가격 CSV 결손이 매매를 막지 않게).
    """
    if not bot._runtime_bool("US_SWING_MAX_FLOOR_ENABLED", False):
        return signals, {"applied": False, "reason": "disabled"}
    floor = float(bot._runtime_float("US_SWING_MAX_FLOOR_PCT", 8.0))
    kept, dropped, unknown = [], [], []
    for signal in signals:
        ticker = str(signal.get("ticker") or "").upper()
        value = _max_daily_return_21d(ticker, session_date)
        if value is None:
            unknown.append(ticker)
            kept.append(signal)
            continue
        (kept if value >= floor else dropped).append(
            signal if value >= floor else {"ticker": ticker, "max_pct": round(value, 1)}
        )
    meta = {"applied": True, "floor_pct": floor,
            "dropped": [d.get("ticker") for d in dropped],
            "unknown": unknown}
    if unknown:
        log.warning(f"[US swing handoff] MAX 계산 불가 {unknown} — fail-open 통과")
    if not kept:
        log.info(f"[US swing handoff] MAX 하한({floor:.0f}%) 통과 후보 없음 — 배제 {meta['dropped']}")
        return [], meta
    if dropped:
        log.info(f"[US swing handoff] MAX 하한({floor:.0f}%) 배제 {meta['dropped']}")
    return kept, meta


def _apply_dollar_volume_band(
    bot: Any, con: sqlite3.Connection, session_date: str, signals: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """거래대금 밴드 재선택 — 밴드 안에서 최상위 랭크를 고른다.

    2026-08-20 발견(228세션 백테스트, 계약 동일 TP12/SL25/D5·비용0.5%):
      현행 rank1 무조건 = 평균 +0.03% (t=0.04)  ← 사실상 무엣지
      밴드 재선택(100~500M)  = 평균 +3.67% (t=4.80), 승 64%, 진입빈도 61% 유지
    반분할(전반 t+4.47 / 후반 t+2.67)·경계 민감도(100~400·100~600·150~500 모두 t>3.8)
    모두 통과. 배제군은 전 구간 음수(<100M −2.04 / 500~1000M −1.21 / ≥1000M −1.27).

    문헌 근거: 단기 반전은 유동성 공급의 대가다. 거래대금이 과소면 투매가 아니라
    조용한 흘러내림(프리미엄 없음), 과대면 뉴스 주도 펀더멘털 재평가라 되돌아오지
    않는다(NBER w30917 Reversals and the Returns to Liquidity Provision 등).

    fail-open: 거래대금을 하나도 못 읽으면 현행 동작(rank1)으로 간다 — 관측 결손이
    매매를 멈추면 안 된다. 다만 소리내어 남기고 사유를 상태에 붙인다.
    """
    if not bot._runtime_bool("US_SWING_DVOL_BAND_ENABLED", False):
        return signals, {"applied": False, "reason": "disabled"}
    lo = float(bot._runtime_float("US_SWING_DVOL_BAND_MIN_M", 100.0))
    hi = float(bot._runtime_float("US_SWING_DVOL_BAND_MAX_M", 500.0))
    dvol = _dollar_volume_by_ticker(con, session_date)
    if not dvol:
        log.warning(
            "[US swing handoff] 거래대금 밴드 skip — candidate_pool_all 결손 "
            f"(session={session_date}) → 현행 rank1로 fail-open"
        )
        return signals, {"applied": False, "reason": "dollar_volume_unavailable",
                         "band_min_m": lo, "band_max_m": hi}
    in_band, out_band = [], []
    for signal in signals:
        ticker = str(signal.get("ticker") or "").upper()
        value = dvol.get(ticker)
        entry = {"ticker": ticker, "rank": int(signal.get("rank") or 0),
                 "dollar_vol_m": round(value, 1) if value is not None else None}
        (in_band if (value is not None and lo <= value < hi) else out_band).append(entry)
        if value is not None and lo <= value < hi:
            continue
    kept = [s for s in signals
            if (dvol.get(str(s.get("ticker") or "").upper()) is not None
                and lo <= dvol[str(s.get("ticker") or "").upper()] < hi)]
    meta = {"applied": True, "band_min_m": lo, "band_max_m": hi,
            "in_band": in_band, "out_band": out_band}
    if not kept:
        log.info(
            f"[US swing handoff] 거래대금 밴드({lo:.0f}~{hi:.0f}M) 통과 후보 없음 — "
            f"밖 {[(e['ticker'], e['dollar_vol_m']) for e in out_band]}"
        )
        return [], meta
    log.info(
        f"[US swing handoff] 거래대금 밴드 재선택: "
        f"{[(e['ticker'], e['rank'], e['dollar_vol_m']) for e in in_band]} "
        f"(밖 {[(e['ticker'], e['dollar_vol_m']) for e in out_band]})"
    )
    return kept, meta


def _us_swing_attribution_manifest(con: sqlite3.Connection) -> frozenset[str]:
    """실제 제출 이력(handoff SUBMITTED)이 있는 티커 집합.

    2026-08-07: 재시작·브로커 주입으로 포지션의 source_strategy가 사라지면
    슬롯 계산에서 빠져 과다 노출이 된다(fail-open). 제출 원장을 truth로
    귀속을 복원한다. 무귀속 US 포지션 전면 차단은 쓰지 않는다 — 코어 승격
    보유(SCHG류)까지 막는 전역 지뢰가 된다(A1 셧다운 교훈).
    """
    try:
        rows = con.execute(
            "SELECT DISTINCT ticker FROM signals WHERE handoff_status='SUBMITTED'"
        ).fetchall()
    except sqlite3.Error:
        return frozenset()
    return frozenset(str(t or "").upper() for (t,) in rows if t)


def _current_us_swing_open_slots(bot: Any, manifest: frozenset[str] = frozenset()) -> int:
    tickers: set[str] = set()
    sources = [
        *(getattr(getattr(bot, "risk", None), "positions", []) or []),
        *(getattr(bot, "pending_orders", []) or []),
    ]
    for item in sources:
        if str(item.get("market") or "").upper() != "US":
            continue
        source = str(item.get("source_strategy") or item.get("strategy_used") or "").lower()
        ticker = str(item.get("ticker") or "").upper()
        if not ticker:
            continue
        if source == "us_swing_5d":
            tickers.add(ticker)
        elif not source and ticker in manifest:
            # 귀속 소실 복원: 우리가 제출한 이력이 있는 무귀속 보유는 슬롯을 점유한다.
            log.warning(f"[US swing handoff] {ticker} 무귀속 보유를 제출 이력으로 us_swing 슬롯에 귀속(fail-closed)")
            tickers.add(ticker)
    return len(tickers)


def _snapshot_unhealthy(snapshot: dict | None) -> bool:
    return (
        not snapshot
        or bool(snapshot.get("missing"))
        or bool(snapshot.get("stale"))
        or bool(str(snapshot.get("error", "") or "").strip())
    )


def _has_broker_truth_open_order(bot: Any, ticker: str) -> bool:
    # fail-closed: 스냅샷 실패·missing·stale·error를 전부 "주문 있음"으로 간주해 신규 제출을 막는다.
    # _broker_truth_open_buy_orders()는 이 실패들을 빈 목록으로 삼켜 중복 주문 위험이 있으므로
    # (Codex 리뷰 2026-08-02) 스냅샷 상태를 직접 본다. 단 단순 TTL 경과(stale)로 정상 매수가
    # 막히지 않게 unhealthy면 1회 강제 갱신 후 재평가한다. 그래도 unhealthy면 진입보다 보호 우선.
    ticker_key = str(ticker or "").upper()
    snapshot: dict | None
    try:
        snapshot = bot._broker_truth_market_snapshot("US", force=False, ttl_sec=60)
        if _snapshot_unhealthy(snapshot):
            snapshot = bot._broker_truth_market_snapshot("US", force=True, ttl_sec=60)
    except Exception as exc:
        log.warning(f"[US swing handoff] broker snapshot failed {ticker_key}: {exc} — fail-closed")
        return True
    if _snapshot_unhealthy(snapshot):
        log.warning(
            f"[US swing handoff] broker snapshot unhealthy {ticker_key} "
            f"(missing={bool((snapshot or {}).get('missing'))} stale={bool((snapshot or {}).get('stale'))} "
            f"error={str((snapshot or {}).get('error', '') or '')[:80]}) — fail-closed"
        )
        return True
    rows = list((snapshot or {}).get("open_orders", []) or [])
    return any(str(row.get("ticker") or row.get("symbol") or "").upper() == ticker_key for row in rows)


def run_us_swing_handoff(bot: Any) -> dict[str, Any]:
    session_date = bot._current_session_date_str("US")
    configured_mode = str(bot._runtime_value("US_SWING_AUTHORITY_MODE", "shadow") or "shadow")
    db_path = Path(str(bot._runtime_value("US_SWING_SHADOW_DB", "data/analysis/us_swing_shadow.db")))
    policy_path = Path(str(bot._runtime_value("US_SWING_POLICY_PATH", "config/us_swing_accelerated.json")))
    historical_path = Path(str(bot._runtime_value(
        "US_SWING_HISTORICAL_EVIDENCE_PATH", "state/us_swing_historical_evidence.json"
    )))
    execution_path = Path(str(bot._runtime_value(
        "US_SWING_EXECUTION_EVIDENCE_PATH", "state/us_swing_execution_evidence.json"
    )))
    if not db_path.exists() or not policy_path.exists() or not historical_path.exists() or not execution_path.exists():
        return _write_execution_status(
            bot,
            session_date=session_date,
            result={"status": "BLOCKED", "reason": "handoff_artifact_missing"},
        )
    con = sqlite3.connect(db_path)
    try:
        research_authority = resolve_handoff_authority(
            configured_mode=configured_mode,
            con=con,
            policy_path=policy_path,
            historical_path=historical_path,
            execution_path=execution_path,
        )
        authority = _operator_micro_override(bot, research_authority, configured_mode)
        # rank2 폴백 (2026-08-16 운영자 승인, proposal_rank2_fallback_20260812):
        # rank1이 **종목 고유 가드**로 죽은 날에만 rank2를 평가한다. 일공통 차단
        # (창 종료·슬롯·일한도·권한)은 이월 금지 — 그날은 사는 날이 아니기 때문이다.
        # 켜져 있어도 rank1이 통과하면 rank2는 평가조차 되지 않는다(아래 break).
        fallback_on = bot._runtime_bool("US_SWING_RANK2_FALLBACK_ENABLED", False)
        base_limit = max(1, int(authority.get("max_new_per_day") or 1))
        pick_limit = base_limit + 1 if fallback_on else base_limit
        # 밴드가 켜지면 풀 전체에서 재선택해야 하므로 넓게 읽는다(정책 top_k=10).
        band_on = bot._runtime_bool("US_SWING_DVOL_BAND_ENABLED", False)
        signals = load_handoff_signals(
            con,
            session_date=session_date,
            limit=10 if band_on else pick_limit,
        )
        if not signals:
            return _write_execution_status(
                bot,
                session_date=session_date,
                result={"status": "SKIPPED", "reason": "no_handoff_signal", "authority": authority},
                research_authority=research_authority,
                execution_authority=authority,
            )
        signals, band_meta = _apply_dollar_volume_band(bot, con, session_date, signals)
        if band_meta.get("applied") and not signals:
            return _write_execution_status(
                bot,
                session_date=session_date,
                result={"status": "SKIPPED", "reason": "dvol_band_no_candidate",
                        "authority": authority, "dvol_band": band_meta},
                research_authority=research_authority,
                execution_authority=authority,
            )
        # MAX 하한은 밴드 뒤에 온다(검정 순서와 동일: 밴드 위에 얹은 축).
        signals, max_meta = _apply_max_lottery_floor(bot, session_date, signals)
        if max_meta.get("applied") and not signals:
            return _write_execution_status(
                bot,
                session_date=session_date,
                result={"status": "SKIPPED", "reason": "max_floor_no_candidate",
                        "authority": authority, "dvol_band": band_meta, "max_floor": max_meta},
                research_authority=research_authority,
                execution_authority=authority,
            )
        if band_meta.get("applied") or max_meta.get("applied"):
            # 밴드 통과분은 원 랭크가 3·7일 수 있다. 아래 랭크 게이트(rank2 폴백용)가
            # 이를 "폴백 후보"로 오인하지 않도록 밴드 내 순위를 따로 붙인다.
            # 원 rank는 귀속 태그(us_swing_5d_rank_N)에 그대로 쓰이므로 보존한다.
            for position, signal in enumerate(signals, start=1):
                signal["_band_position"] = position
            signals = signals[:pick_limit]
        raw_submit_enabled = bot._runtime_bool("US_SWING_ORDER_SUBMIT_ENABLED", False)
        live_ack = str(bot._runtime_value("US_SWING_ORDER_LIVE_ACK", "") or "")
        ack_ok = bool(bot.is_paper) or live_ack == "I_ACCEPT_LIVE_US_SWING"
        submit_enabled = bool(raw_submit_enabled and ack_ok)
        if raw_submit_enabled and not ack_ok:
            log.error("[US swing handoff] submit switch ignored: live acknowledgement missing")
        bot._sync_runtime_with_broker()
        current_open_slots = _current_us_swing_open_slots(
            bot, manifest=_us_swing_attribution_manifest(con)
        )
        results: list[dict[str, Any]] = []
        window_end_min = float(bot._runtime_int("US_SWING_ORDER_MAX_OPEN_MIN", 30))
        elapsed_since_open_min = (
            datetime.now(KST) - regular_open_dt("US", session_date)
        ).total_seconds() / 60.0
        # rank2 폴백 이월 사유(종목 고유). 이 목록 밖 사유는 그날 전체가 사는 날이
        # 아니라는 뜻이므로 이월하지 않는다.
        fallback_carry_reasons = {
            "price_chase_above_contract",
            "open_gap_outside_contract",
            "open_fade_below_contract",
            "provider_fresh_quote_incomplete",
            "independent_prev_close_missing",
            "independent_reference_mismatch",
            "already_holding",
            "pending_order_exists",
            "same_day_reentry_blocked",
        }
        for signal in signals:
            ticker = str(signal.get("ticker") or "").upper()
            signal_rank = int(signal.get("rank") or 0)
            # 밴드 재선택 시에는 밴드 내 순위로 게이트한다(원 rank는 귀속용으로 보존).
            gate_rank = int(signal.get("_band_position") or signal_rank)
            if gate_rank > base_limit:
                if not fallback_on:
                    continue
                prior = [r for r in results if int(r.get("rank") or 0) <= base_limit]
                carried = any(
                    str(r.get("reason") or "") in fallback_carry_reasons for r in prior
                )
                if not carried:
                    results.append({
                        "status": "SKIPPED",
                        "reason": "rank2_fallback_not_triggered",
                        "ticker": ticker,
                        "rank": signal_rank,
                        "prior_reasons": [str(r.get("reason") or "") for r in prior],
                    })
                    continue
            # 2026-08-11: 창 종료 후에는 어떤 경로로도 submit에 도달하지 못한다(창 체크가
            # budget/submit보다 앞). 이미 실질 사유가 기록된 신호를 밤새 재평가하며 KIS를
            # 폴링하고 expired로 덮어쓰는 것을 여기서 끊는다 (STEP 08-10: 22:30~04:56 폴링 실측).
            if elapsed_since_open_min > window_end_min and str(signal.get("handoff_reason") or ""):
                results.append({
                    "status": "SKIPPED",
                    "reason": "window_closed_prior_reason_kept",
                    "ticker": ticker,
                    "prior_reason": str(signal.get("handoff_reason") or ""),
                })
                continue
            try:
                quote = get_price(
                    ticker,
                    bot._token_for_market("US"),
                    market="US",
                    allow_fallback=False,
                )
            except Exception as exc:
                quote = {}
                log.warning(f"[US swing handoff] provider-fresh quote failed {ticker}: {exc}")
            reentry = bot._same_day_reentry_state(ticker, "US")
            decision = evaluate_handoff(
                signal=signal,
                authority=authority,
                now=datetime.now(KST),
                regular_open=regular_open_dt("US", session_date),
                handoff_enabled=True,
                submit_enabled=submit_enabled,
                quote=quote,
                fx_rate=float(getattr(bot, "usd_krw_rate", 0) or 0),
                base_order_budget_krw=float(getattr(bot.risk, "max_order_krw", 0) or 0),
                available_budget_krw=float(bot._market_budget_available("US")),
                cash_krw=float(bot._broker_orderable_cash_krw("US")),
                broker_trust_level=str(bot._broker_trust_level("US") or "unknown"),
                already_holding=bool(bot._has_open_position(ticker, "US")),
                pending_order=bool(
                    bot._has_pending_order(ticker, "US")
                    or _has_broker_truth_open_order(bot, ticker)
                ),
                same_day_reentry_allowed=bool(reentry.get("allowed", False)),
                current_open_slots=current_open_slots,
                min_open_min=bot._runtime_int("US_SWING_ORDER_MIN_OPEN_MIN", 5),
                max_open_min=bot._runtime_int("US_SWING_ORDER_MAX_OPEN_MIN", 30),
                min_probability=bot._runtime_float("US_SWING_ORDER_MIN_PROB", 0.55),
                min_predicted_net_pct=bot._runtime_float("US_SWING_ORDER_MIN_PREDICTED_NET_PCT", 0.25),
                absolute_hurdles_enforced=bot._runtime_bool(
                    "US_SWING_ORDER_ABSOLUTE_HURDLES_ENFORCED", False
                ),
                max_abs_gap_pct=bot._runtime_float("US_SWING_ORDER_MAX_ABS_GAP_PCT", 3.0),
                max_reference_deviation_pct=bot._runtime_float("US_SWING_ORDER_MAX_REFERENCE_DEVIATION_PCT", 1.0),
                max_chase_pct=bot._runtime_float("US_SWING_ORDER_MAX_CHASE_PCT", 0.5),
                max_fade_from_open_pct=bot._runtime_float("US_SWING_ORDER_MAX_FADE_PCT", 2.0),
                max_order_krw=bot._runtime_float("US_SWING_ORDER_MAX_KRW", 250000.0),
            )
            if decision.status == "WAIT":
                results.append(decision.to_dict())
                continue
            if decision.would_submit:
                common_gate = bot._new_buy_block_state(
                    "US", ticker, "us_swing_5d", profit_evidence=dict(signal)
                )
                if not bool(common_gate.get("allowed", True)):
                    decision = replace(
                        decision,
                        status="BLOCKED",
                        reason=f"common_buy_gate:{common_gate.get('reason') or 'blocked'}",
                        would_submit=False,
                        allowed_to_submit=False,
                        details={**decision.details, "common_buy_gate": common_gate},
                    )
            if decision.status == "REHEARSAL_READY":
                record_handoff_result(con, decision=decision)
                results.append(decision.to_dict())
                break
            if not decision.allowed_to_submit:
                record_handoff_result(con, decision=decision)
                results.append(decision.to_dict())
                continue
            mode = str((getattr(bot, "today_judgment", {}) or {}).get("consensus", {}).get("mode", "CAUTIOUS"))
            submit_exception = ""
            try:
                order_ok = bot._submit_micro_probe_buy_order(
                    market="US",
                    ticker=ticker,
                    name=str((quote or {}).get("name") or ticker),
                    qty=int(decision.qty),
                    raw_price=float(decision.quote_price or 0.0),
                    risk_price_krw=float(decision.details.get("price_krw") or 0.0),
                    tp_pct=bot._runtime_float("US_SWING_ORDER_TP_DECIMAL", 0.12),
                    sl_pct=bot._runtime_float("US_SWING_ORDER_SL_DECIMAL", 0.25),
                    max_hold=5,
                    mode=mode,
                    # rank2 폴백 경유분은 태그로 분리(반증 기준 "폴백 정산 10건" 집계용)
                    selected_reason=(
                        f"us_swing_5d_rank_{decision.rank}_fallback"
                        if signal_rank > base_limit
                        else f"us_swing_5d_rank_{decision.rank}"
                    ),
                    source_strategy="us_swing_5d",
                    entry_priority_score=float(signal.get("probability") or 0.0),
                    tsdb_id=-1,
                    isdb_id=0,
                    signal_at=str(signal.get("created_at") or ""),
                    signal_row=dict(signal),
                    probe_meta={
                        "reason": f"us_swing_{decision.authority_mode}",
                        "original_qty": int(decision.qty),
                        "adjusted_qty": int(decision.qty),
                        "original_order_cost_krw": float(decision.order_cost_krw),
                        "adjusted_order_cost_krw": float(decision.order_cost_krw),
                        "order_budget_krw": float(decision.details.get("spend_cap_krw") or 0.0),
                        "min_effective_order_krw": 0.0,
                        "oversize_ratio": 1.0,
                    },
                )
            except Exception as exc:
                order_ok = False
                submit_exception = str(exc)
            order_no = ""
            submit_outcome = dict(getattr(bot, "_last_micro_probe_submit_result", {}) or {})
            if str(submit_outcome.get("order_no") or ""):
                order_no = str(submit_outcome.get("order_no") or "")
            if order_ok and not order_no:
                matches = [
                    item for item in (getattr(bot, "pending_orders", []) or [])
                    if str(item.get("market") or "").upper() == "US"
                    and str(item.get("ticker") or "").upper() == ticker
                ]
                if matches:
                    order_no = str(matches[-1].get("order_no") or "")
            outcome_status = str(submit_outcome.get("status") or "").upper()
            if order_ok and order_no and outcome_status != "UNKNOWN":
                record_handoff_result(con, decision=decision, order_no=order_no, submitted=True)
                results.append({**decision.to_dict(), "submitted": True, "order_no": order_no})
                break
            if outcome_status == "UNKNOWN" or (order_ok and not order_no) or submit_exception:
                unknown = replace(
                    decision,
                    status="ORDER_UNKNOWN",
                    reason="broker_submit_outcome_unknown",
                    allowed_to_submit=False,
                    details={
                        **decision.details,
                        "submit_outcome": submit_outcome,
                        "submit_exception": submit_exception[:240],
                    },
                )
                try:
                    bot._v2_record_order_unknown(
                        "US",
                        ticker,
                        {
                            "ticker": ticker,
                            "market": "US",
                            "qty": int(decision.qty),
                            "order_no": order_no,
                            "source_strategy": "us_swing_5d",
                        },
                        "US swing broker submission outcome unknown",
                    )
                except Exception:
                    pass
                record_handoff_result(con, decision=unknown, order_no=order_no)
                results.append(unknown.to_dict())
                break
            failed = replace(
                decision,
                status="SUBMIT_FAILED",
                reason="existing_order_path_rejected",
                allowed_to_submit=False,
            )
            record_handoff_result(con, decision=failed)
            results.append(failed.to_dict())
        return _write_execution_status(
            bot,
            session_date=session_date,
            result={"status": "EVALUATED", "authority": authority, "results": results},
            research_authority=research_authority,
            execution_authority=authority,
        )
    finally:
        con.close()
