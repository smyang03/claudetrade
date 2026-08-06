"""KR 급락 반등 micro 주문 브리지 (2026-08-04 사전 구현 — 기본 OFF 대기).

게이트(preregistered_falsification_criteria_20260804.md) 통과 + 운영자 승인 전까지
`KR_FALLEN_ORDER_HANDOFF_ENABLED=false`로 완전 비활성. 켜려면 세 조건이 모두 필요:
  KR_FALLEN_ORDER_HANDOFF_ENABLED=true
  KR_FALLEN_LIVE_ENABLED=true
  KR_FALLEN_LIVE_ACK=I_ACCEPT_LIVE_KR_FALLEN

설계 (us_swing 브리지 미러, 캘리브레이션 규약 준수):
- 후보: 직전 세션의 shadow 원장(kr_fallen_shadow.jsonl)에서 활성 규칙
  (KR_FALLEN_ACTIVE_RULE ∈ R1/R2/R3, 기본 R2 — 게이트 승자 확정 시 설정) 충족분.
  규칙 판정은 tools.kr_fallen_gate_report.rule_flags 재사용(정의 단일화).
- 진입: 개장 후 KR_FALLEN_MIN_OPEN_MIN(2)~MAX(20)분 창 — "익일 시가" 규약의 최근접 실행.
  극단 갭업 가드: 현재가가 신호일 종가 +10% 이상이면 스킵(TP 여지 소진 — 규약 이탈 아님,
  안 사는 쪽 보수 처리).
- 사이징: min(KR_FALLEN_ORDER_MAX_KRW=300,000, 예산, 현금) 내 정수 주.
- 한도: 일 KR_FALLEN_MAX_NEW_PER_DAY(1), 동시 KR_FALLEN_MAX_OPEN_SLOTS(1).
- 게이트: 공통 매수 게이트(_new_buy_block_state — RISK_HALTED 포함) 통과 필수.
- 계약: TP+12% / SL−25% / D5 — source_strategy="kr_fallen_5d"
  (risk_manager ISOLATED_STRATEGY_SOURCES·horizon exit에 등록 완료).
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from bot.session_date import KST
from kis_api import get_price
from logger import get_trading_logger
from preopen.scheduler import regular_open_dt
from runtime_paths import get_runtime_path
from tools.kr_fallen_gate_report import rule_flags

log = get_trading_logger()

LIVE_ACK = "I_ACCEPT_LIVE_KR_FALLEN"
SOURCE_STRATEGY = "kr_fallen_5d"
LEDGER = Path(__file__).resolve().parents[1] / "data" / "shadow" / "kr_fallen_shadow.jsonl"


def _write_status(bot: Any, session_date: str, result: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "schema_version": "kr_fallen_execution_status_v1",
        "generated_at": datetime.now(KST).isoformat(timespec="seconds"),
        "generated_at_semantics": "last_handoff_execution",
        "session_date": session_date,
        "active_rule": str(bot._runtime_value("KR_FALLEN_ACTIVE_RULE", "R2") or "R2"),
        "last_result": dict(result),
    }
    try:
        path = get_runtime_path("state", "kr_fallen_execution_status.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception as exc:
        log.error(f"[KR fallen handoff] status write failed: {exc}")
    _append_history(payload)
    return result


def _append_history(payload: dict[str, Any]) -> None:
    """상태 전이만 이력 원장에 append (2026-08-06).

    status 파일은 매 호출마다 덮어써서 마지막 결과만 남는다. 브리지는 진입창
    (개장 2~20분) 동안 엔트리 스캔 주기(2분)마다 호출되므로, 창이 끝나면
    창 안에서 무슨 판정이 있었는지가 사라진다 — 실제로 08-06 첫 가동일에
    파일 mtime만 보고 "창 안에 호출됐는지 불명"으로 오독했다.

    첫 실주문 이후에는 "왜 안 샀는가"(후보 없음 / 게이트 차단 / 예산 부족)를
    사후에 복원할 수 있어야 한다. 같은 (status, reason)이 연속되면 건너뛰어
    2분마다 같은 줄이 쌓이는 것을 막는다.
    """

    try:
        path = get_runtime_path("data", "shadow", "kr_fallen_handoff_history.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        result = payload.get("last_result") or {}
        session = str(payload.get("session_date") or "")
        signature = (
            session,
            str(result.get("status") or ""),
            str(result.get("reason") or ""),
            json.dumps(result.get("results") or [], ensure_ascii=False, sort_keys=True),
        )
        if path.exists():
            last_line = ""
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        last_line = line
            if last_line:
                try:
                    prev = json.loads(last_line)
                    prev_result = prev.get("last_result") or {}
                    prev_sig = (
                        str(prev.get("session_date") or ""),
                        str(prev_result.get("status") or ""),
                        str(prev_result.get("reason") or ""),
                        json.dumps(prev_result.get("results") or [], ensure_ascii=False, sort_keys=True),
                    )
                    if prev_sig == signature:
                        return
                except ValueError:
                    pass
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning(f"[KR fallen handoff] history append failed: {exc}")


def _rule_key(short: str) -> str:
    return {"R1": "R1_8조건", "R2": "R2_할인저변동", "R3": "R3_합집합"}.get(short.upper(), "R2_할인저변동")


def _load_candidates(prev_session: str, rule_short: str) -> list[dict]:
    if not LEDGER.exists():
        return []
    key = _rule_key(rule_short)
    out = []
    for line in LEDGER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("session_date") != prev_session:
            continue
        if rule_flags(row).get(key):
            out.append(row)
    # 우선순위: ma20 할인 깊은 순 (R2 계열의 실측 우선순위)
    out.sort(key=lambda r: (r.get("feats") or {}).get("ma20_disc") or 0.0)
    return out


def _open_slots(bot: Any) -> int:
    count = 0
    for item in [*(getattr(getattr(bot, "risk", None), "positions", []) or []),
                 *(getattr(bot, "pending_orders", []) or [])]:
        src = str(item.get("source_strategy") or item.get("strategy_used") or "").lower()
        if src == SOURCE_STRATEGY:
            count += 1
    return count


def _today_new_count(bot: Any, session_date: str) -> int:
    """오늘 진입한 kr_fallen 건수(일일 한도용).

    2026-08-05 사전 점검: 기존에는 슬롯 캡만 있어 "일 1건"이 슬롯=1일 때만
    우연히 지켜졌다. 슬롯을 3으로 올리면 핸드오프가 진입창(2~20분) 동안
    사이클마다 돌며 하루 3건까지 낼 수 있었다. 같은 날 진입-청산된 건은
    포지션 목록에서 빠져 셀 수 없는 한계가 있으나(드묾), 한도 방향으로는
    슬롯 캡이 이중 방어한다.
    """

    count = 0
    for item in [*(getattr(getattr(bot, "risk", None), "positions", []) or []),
                 *(getattr(bot, "pending_orders", []) or [])]:
        src = str(item.get("source_strategy") or item.get("strategy_used") or "").lower()
        if src != SOURCE_STRATEGY:
            continue
        entry_day = str(item.get("entry_session_date") or item.get("session_date") or "")
        if entry_day == str(session_date):
            count += 1
    return count


def run_kr_fallen_handoff(bot: Any) -> dict[str, Any]:
    session_date = bot._current_session_date_str("KR")
    live_enabled = bot._runtime_bool("KR_FALLEN_LIVE_ENABLED", False)
    ack_ok = str(bot._runtime_value("KR_FALLEN_LIVE_ACK", "") or "") == LIVE_ACK
    if not (live_enabled and ack_ok):
        return _write_status(bot, session_date, {"status": "DISABLED", "reason": "live_flag_or_ack_missing"})

    now = datetime.now(KST)
    opened = regular_open_dt("KR", session_date)
    minutes = (now - opened).total_seconds() / 60.0
    lo = int(bot._runtime_int("KR_FALLEN_MIN_OPEN_MIN", 2))
    hi = int(bot._runtime_int("KR_FALLEN_MAX_OPEN_MIN", 20))
    if not (lo <= minutes <= hi):
        return _write_status(bot, session_date, {"status": "SKIPPED", "reason": "outside_entry_window",
                                                 "minutes_since_open": round(minutes, 1)})

    max_slots = int(bot._runtime_int("KR_FALLEN_MAX_OPEN_SLOTS", 1))
    if _open_slots(bot) >= max_slots:
        return _write_status(bot, session_date, {"status": "BLOCKED", "reason": "strategy_open_slot_cap_reached"})
    max_new = int(bot._runtime_int("KR_FALLEN_MAX_NEW_PER_DAY", 1))
    if _today_new_count(bot, session_date) >= max_new:
        return _write_status(bot, session_date, {"status": "BLOCKED", "reason": "daily_new_entry_cap_reached"})

    # 직전 영업일 = 원장에서 오늘보다 앞선 가장 최근 세션
    sessions = set()
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    d = json.loads(line).get("session_date")
                    if d and d < session_date:
                        sessions.add(d)
                except ValueError:
                    continue
    if not sessions:
        return _write_status(bot, session_date, {"status": "SKIPPED", "reason": "no_prior_session_candidates"})
    prev_session = max(sessions)
    # 신호 신선도 가드(2026-08-05 사전 점검). 계약은 "익일 시가" 진입인데,
    # 마감 후 스캔이 조용히 죽으면 prev_session이 이틀 전 이상으로 밀려
    # 낡은 신호로 매수하게 된다. 주말·연휴를 감안해 달력 6일까지만 허용한다.
    try:
        gap_days = (datetime.strptime(session_date, "%Y-%m-%d")
                    - datetime.strptime(prev_session, "%Y-%m-%d")).days
    except ValueError:
        gap_days = 99
    if gap_days > int(bot._runtime_int("KR_FALLEN_SIGNAL_MAX_AGE_DAYS", 6)):
        return _write_status(bot, session_date, {
            "status": "BLOCKED", "reason": "stale_signal_scan_may_be_dead",
            "prev_session": prev_session, "gap_days": gap_days,
        })
    rule_short = str(bot._runtime_value("KR_FALLEN_ACTIVE_RULE", "R2") or "R2")
    candidates = _load_candidates(prev_session, rule_short)
    if not candidates:
        return _write_status(bot, session_date, {"status": "SKIPPED", "reason": "no_rule_candidates",
                                                 "prev_session": prev_session, "rule": rule_short})

    cap_krw = float(bot._runtime_float("KR_FALLEN_ORDER_MAX_KRW", 300000.0))
    results: list[dict] = []
    for row in candidates:
        ticker = str(row["ticker"])
        try:
            quote = get_price(ticker, bot._token_for_market("KR"), market="KR")
        except Exception as exc:
            results.append({"ticker": ticker, "status": "WAIT", "reason": f"quote_failed:{str(exc)[:60]}"})
            continue
        price = float((quote or {}).get("price") or 0.0)
        signal_close = float((row.get("feats") or {}).get("price") or 0.0)
        if price <= 0:
            results.append({"ticker": ticker, "status": "WAIT", "reason": "quote_invalid"})
            continue
        if signal_close > 0 and price >= signal_close * 1.10:
            results.append({"ticker": ticker, "status": "BLOCKED", "reason": "extreme_gap_up_tp_room_gone"})
            continue
        budget = min(cap_krw, float(bot._market_budget_available("KR")), float(bot._broker_orderable_cash_krw("KR")))
        qty = int(budget // price) if price > 0 else 0
        if qty <= 0:
            results.append({"ticker": ticker, "status": "BLOCKED", "reason": "micro_budget_cannot_buy_one_share"})
            continue
        gate = bot._new_buy_block_state("KR", ticker, SOURCE_STRATEGY, source_strategy=SOURCE_STRATEGY)
        if not bool(gate.get("allowed", True)):
            results.append({"ticker": ticker, "status": "BLOCKED",
                            "reason": f"common_buy_gate:{gate.get('reason') or 'blocked'}"})
            continue
        mode = str((getattr(bot, "today_judgment", {}) or {}).get("consensus", {}).get("mode", "CAUTIOUS"))
        ok = bot._submit_micro_probe_buy_order(
            market="KR", ticker=ticker, name=ticker, qty=qty,
            raw_price=price, risk_price_krw=price,
            tp_pct=0.12, sl_pct=0.25, max_hold=5, mode=mode,
            selected_reason=f"kr_fallen_{rule_short.lower()}",
            source_strategy=SOURCE_STRATEGY,
            entry_priority_score=abs(float((row.get("feats") or {}).get("ma20_disc") or 0.0)),
            tsdb_id=-1, isdb_id=0,
            signal_at=str(row.get("scanned_at") or ""),
            signal_row=dict(row.get("feats") or {}),
            probe_meta={"reason": f"kr_fallen_{rule_short.lower()}", "original_qty": qty,
                        "adjusted_qty": qty, "original_order_cost_krw": qty * price,
                        "adjusted_order_cost_krw": qty * price, "order_budget_krw": budget,
                        "min_effective_order_krw": 0.0, "oversize_ratio": 1.0},
        )
        results.append({"ticker": ticker, "status": "SUBMITTED" if ok else "SUBMIT_FAILED", "qty": qty, "price": price})
        if ok:
            break  # 일1건
    return _write_status(bot, session_date, {"status": "EVALUATED", "rule": rule_short,
                                             "prev_session": prev_session, "results": results})
