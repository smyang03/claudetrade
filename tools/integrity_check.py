#!/usr/bin/env python3
"""정합성 상시 체크 (standing integrity check) — read-only.

봇/데이터 파이프라인의 '침묵 배선고장'을 자동 탐지한다. 2026-06-19 정합성 스윕에서 손으로 캔
두 부류를 앞으로는 자동으로 깃발 들게 한다:

  - A형(끊긴 배선): 필드가 계산되는데 하류(학습원장)로 안 흘러 NULL — mfe/mae/regime이 그 예.
    → 학습원장 핵심필드 population%로 탐지.
  - D형(죽은 잡): 측정/동기 잡이 조용히 멈춰 데이터가 stale — forward 측정기 3주 정지가 그 예.
    → 잡별 최신 이벤트 age(freshness)로 탐지.

추가로 sync 커버리지(CLOSED 이벤트 → 학습행)도 본다. 주문/브로커/Claude 호출 없음, DB read-only.

사용:
  python tools/integrity_check.py            # 사람용 표
  python tools/integrity_check.py --json      # JSON
exit code: FAIL 있으면 1, 아니면 0(WARN은 0).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ML_DB = ROOT / "data" / "ml" / "decisions.db"
DEFAULT_EVENT_DB = ROOT / "data" / "v2_event_store.db"
DEFAULT_AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"

OK, WARN, FAIL = "OK", "WARN", "FAIL"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _age_days(ts: datetime | None, now: datetime) -> float | None:
    if ts is None:
        return None
    return (now - ts).total_seconds() / 86400.0


def evaluate_freshness(name: str, latest: Any, now: datetime, *, warn_days: float, fail_days: float, note: str = "") -> dict[str, Any]:
    """잡 생존(stale) 평가 — D형 탐지. 최신 이벤트가 너무 오래되면 잡이 멈춘 것."""
    ts = _parse_ts(latest)
    age = _age_days(ts, now)
    if age is None:
        status = FAIL
        detail = "최신 기록 없음"
    elif age > fail_days:
        status = FAIL
        detail = f"{age:.1f}일 정체 (>{fail_days:g}일)"
    elif age > warn_days:
        status = WARN
        detail = f"{age:.1f}일 경과 (>{warn_days:g}일)"
    else:
        status = OK
        detail = f"{age:.1f}일 전"
    return {"check": name, "kind": "freshness", "status": status, "detail": detail, "note": note}


def evaluate_population(name: str, populated: int, total: int, *, warn_below: float, fail_below: float, min_sample: int = 10, note: str = "") -> dict[str, Any]:
    """필드 충진율 평가 — A형 탐지. 채워져야 할 필드가 비기 시작하면 배선이 끊긴 것."""
    if total < min_sample:
        return {"check": name, "kind": "population", "status": OK, "detail": f"표본 {total}<{min_sample} (판단보류)", "note": note}
    pct = 100.0 * populated / total
    if pct < fail_below:
        status = FAIL
    elif pct < warn_below:
        status = WARN
    else:
        status = OK
    return {"check": name, "kind": "population", "status": status, "detail": f"{pct:.0f}% 충진 ({populated}/{total})", "note": note}


def evaluate_ratio(name: str, num: int, den: int, *, warn_below: float, fail_below: float, note: str = "") -> dict[str, Any]:
    if den == 0:
        return {"check": name, "kind": "coverage", "status": OK, "detail": "대상 0건", "note": note}
    pct = 100.0 * num / den
    status = FAIL if pct < fail_below else (WARN if pct < warn_below else OK)
    return {"check": name, "kind": "coverage", "status": status, "detail": f"{pct:.0f}% 커버 ({num}/{den})", "note": note}


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def check_job_freshness(ml_db: Path, event_db: Path, audit_db: Path, now: datetime) -> list[dict[str, Any]]:
    """D형: 잡이 멈췄나. 각 파이프라인의 최신 산출 시각 age로 본다."""
    checks: list[dict[str, Any]] = []
    with _connect_ro(event_db) as ev:
        fm = ev.execute("SELECT MAX(occurred_at) FROM lifecycle_events WHERE event_type='FORWARD_MEASURED'").fetchone()[0]
        checks.append(evaluate_freshness("forward 측정기(FORWARD_MEASURED)", fm, now, warn_days=3, fail_days=5,
                                         note="세션마감 자동 측정. 5/27~6/19 3주 정지 사례"))
        closed = ev.execute("SELECT MAX(occurred_at) FROM lifecycle_events WHERE event_type='CLOSED'").fetchone()[0]
        checks.append(evaluate_freshness("CLOSED 이벤트(봇 청산 기록)", closed, now, warn_days=4, fail_days=7,
                                         note="장기 무청산이면 봇/체결 흐름 점검"))
    with _connect_ro(ml_db) as ml:
        synced = ml.execute("SELECT MAX(synced_at) FROM v2_learning_performance").fetchone()[0]
        checks.append(evaluate_freshness("학습원장 sync(synced_at)", synced, now, warn_days=2, fail_days=4,
                                         note="세션마감 자동 sync"))
    try:
        with _connect_ro(audit_db) as ac:
            out = ac.execute("SELECT MAX(updated_at) FROM audit_candidate_outcomes").fetchone()[0]
            checks.append(evaluate_freshness("후보 outcome 갱신", out, now, warn_days=3, fail_days=6,
                                             note="후보 forward 라벨 갱신 잡"))
    except sqlite3.Error:
        pass
    return checks


# 학습원장에서 '채워져야 하는' 핵심필드. (필드, 경고%미만, 실패%미만, 비고)
LEARNING_FIELDS = [
    ("pnl_pct", 95, 80, "실현 손익(gross). 거의 항상 있어야"),
    ("pnl_pct_net", 80, 50, "net 손익(수수료반영). 6/11+ 정상화"),
    ("mfe_pct", 70, 30, "Phase1c MFE. 6/19 배선 fix, 재시작후 청산부터 충진"),
    ("mae_pct", 70, 30, "Phase1c MAE. mfe와 동일"),
    ("market_regime", 70, 30, "진입국면. 6/19 배선 fix, 재시작후 충진"),
    ("close_reason", 99, 95, "청산사유. 항상 있어야"),
]


# D형 확장(2026-08-05): 파일 기반 파이프라인의 침묵 정지.
# 기존 D형 탐지는 DB 이벤트만 봐서, 판정 입력이 되는 CSV/JSON이 몇 주 멈춰도
# 아무도 깃발을 들지 않았다. 실측 사고:
#   - us_breadth_proxy_daily.csv가 2026-07-09에서 정지 → load_breadth_context가
#     매 세션 MISSING 반환(정산 50건 중 45건). 국면 분해가 diagnostic에서조차 불가.
#   - kr_breadth / adv_dec / vix_term은 25일 정지 상태로 발견됨.
# 값이 없으면 조용히 기본값으로 대체되는 구조라 결과만 보고는 알 수 없다.
# (path, warn_days, fail_days, note)
DATA_PIPELINE_FILES: tuple[tuple[str, float, float, str], ...] = (
    ("data/analysis/us_breadth_proxy_daily.csv", 4, 10, "US breadth 국면 판정 입력(us_swing)"),
    ("data/analysis/kr_breadth_proxy_daily.csv", 4, 10, "KR breadth 국면 판정 입력"),
    ("data/analysis/us_adv_dec_breadth_daily.csv", 4, 14, "US 등락비율 보조 입력"),
    ("data/analysis/kr_adv_dec_breadth_daily.csv", 4, 14, "KR 등락비율 보조 입력"),
    ("data/analysis/vix_term_daily.csv", 4, 14, "VIX term 국면 보조 입력"),
    ("data/earnings_calendar.json", 2, 5, "실적 이벤트(정보성 하락 배제 입력)"),
    ("data/analysis/kr_fallen_price_cache.json", 3, 7, "KR 급락 레인 스캔·정산 캐시"),
)


def check_data_pipeline_freshness(now: datetime) -> list[dict[str, Any]]:
    """판정 입력으로 쓰이는 파일이 조용히 멈췄는지 본다(D형 확장)."""

    checks: list[dict[str, Any]] = []
    for rel, warn_days, fail_days, note in DATA_PIPELINE_FILES:
        path = ROOT / rel
        if not path.exists():
            checks.append({
                "check": f"데이터 파이프라인 {rel}",
                "kind": "freshness",
                "status": FAIL,
                "detail": "파일 없음",
                "note": note,
            })
            continue
        latest = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        checks.append(evaluate_freshness(
            f"데이터 파이프라인 {rel}", latest, now,
            warn_days=warn_days, fail_days=fail_days, note=note,
        ))
    return checks


def check_sleeve_contract_exits(now: datetime) -> list[dict[str, Any]]:
    """계약 청산선을 넘겼는데 아직 보유 중인 sleeve 포지션을 잡는다.

    2026-08-05 실측 사고 회귀 감시. FRMI(us_swing_5d, TP12)가 목표가를 넘긴 채
    장 마감까지 청산되지 않았는데 어디에도 흔적이 없었다 — 조용한 실패였다.
    원인(보유 종목 시세 미갱신)은 고쳤지만, 같은 계열이 다시 생기면
    사람이 파헤치기 전에 여기서 깃발이 서야 한다.

    TP/SL을 넘긴 상태는 체결 지연·장 마감 등으로 잠시 존재할 수 있으므로
    WARN으로 올린다(FAIL은 오탐이 잦다). 반복되면 사람이 본다.
    """

    path = ROOT / "state" / "live_open_positions.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [{
            "check": "sleeve 계약 청산 감시",
            "kind": "contract",
            "status": WARN,
            "detail": f"포지션 파일 읽기 실패: {exc}",
            "note": str(path),
        }]
    rows = payload if isinstance(payload, list) else (payload.get("positions") or [])
    breaches: list[str] = []
    watched = 0
    for pos in rows:
        source = str(pos.get("source_strategy") or "").strip().lower()
        if source not in {"us_swing_5d", "kr_fallen_5d"}:
            continue
        watched += 1
        is_us = pos.get("display_currency") == "USD"
        entry = float(pos.get("display_avg_price") or 0) if is_us else float(pos.get("entry") or 0)
        cur = float(pos.get("display_current_price") or 0) if is_us else float(pos.get("current_price") or 0)
        if entry <= 0 or cur <= 0:
            continue
        tp_pct = float(pos.get("tp_pct") or 0)
        sl_pct = float(pos.get("sl_pct") or 0)
        ticker = str(pos.get("ticker") or "?")
        if tp_pct > 0 and cur >= entry * (1.0 + tp_pct):
            breaches.append(f"{ticker} TP초과 보유({cur:g} >= {entry * (1 + tp_pct):g})")
        elif sl_pct > 0 and cur <= entry * (1.0 - sl_pct):
            breaches.append(f"{ticker} SL이탈 보유({cur:g} <= {entry * (1 - sl_pct):g})")
    if not watched:
        return []
    if breaches:
        return [{
            "check": "sleeve 계약 청산 감시",
            "kind": "contract",
            "status": WARN,
            "detail": "; ".join(breaches),
            "note": "계약선을 넘겼는데 미청산 — 체결 지연이 아니면 청산 경로 점검",
        }]
    return [{
        "check": "sleeve 계약 청산 감시",
        "kind": "contract",
        "status": OK,
        "detail": f"{watched}건 계약선 이내",
        "note": "us_swing_5d / kr_fallen_5d TP·SL 계약 준수",
    }]


def check_learning_fields(ml_db: Path, now: datetime, window_days: int) -> list[dict[str, Any]]:
    """A형: 채워져야 할 필드가 최근 창에서 비기 시작했나."""
    checks: list[dict[str, Any]] = []
    cutoff = (now.timestamp() - window_days * 86400)
    with _connect_ro(ml_db) as ml:
        rows = ml.execute(
            "SELECT pnl_pct,pnl_pct_net,mfe_pct,mae_pct,market_regime,close_reason,closed_at "
            "FROM v2_learning_performance WHERE closed=1 AND closed_at IS NOT NULL"
        ).fetchall()
        recent = [r for r in rows if (_parse_ts(r["closed_at"]) or now).timestamp() >= cutoff]
        total = len(recent)
        for field, warn_b, fail_b, note in LEARNING_FIELDS:
            populated = sum(1 for r in recent if r[field] not in (None, "", 0, 0.0))
            checks.append(evaluate_population(f"학습원장 {field} (최근{window_days}일)", populated, total,
                                              warn_below=warn_b, fail_below=fail_b, note=note))
    return checks


def check_sync_coverage(ml_db: Path, event_db: Path, now: datetime, window_days: int) -> list[dict[str, Any]]:
    """CLOSED 이벤트가 학습행으로 동기됐나(사일런트 sync 누락)."""
    cutoff_date = datetime.fromtimestamp(now.timestamp() - window_days * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
    with _connect_ro(event_db) as ev:
        closed_ids = {r[0] for r in ev.execute(
            "SELECT DISTINCT decision_id FROM lifecycle_events WHERE event_type='CLOSED' AND session_date>=?",
            (cutoff_date,)) if r[0]}
    # isolated sleeve(us_swing_5d / kr_fallen_5d) 청산은 Claude decision 파이프라인
    # 밖에서 일어나므로 v2_learning_performance에 대응 행이 존재할 수 없다.
    # 성과는 전용 원장(us_swing_shadow.db, kr_fallen_shadow.jsonl)이 추적한다.
    # 이 커버리지에 포함하면 구조적으로 매칭 불가능한 건을 세어 영구 FAIL이 된다.
    # (2026-08-06: sleeve CLOSED 소급 주입 후 실측으로 확인)
    closed_ids = {cid for cid in closed_ids if not str(cid).startswith("sleeve_")}
    with _connect_ro(ml_db) as ml:
        # 학습행 session_date=진입일, CLOSED 창=청산일. 오버나이트/멀티데이 홀드는 진입일<청산일이라
        # 학습행에도 청산일 cutoff를 걸면 창 경계 청산이 가짜 미스매치가 된다(false-positive FAIL).
        # closed_ids가 이미 최근 청산으로 창을 한정하므로 학습행은 decision_id+closed=1로만 매칭한다.
        learn_ids = {r[0] for r in ml.execute(
            "SELECT DISTINCT v2_decision_id FROM v2_learning_performance WHERE closed=1")
            if r[0]}
    synced = len(closed_ids & learn_ids)
    return [evaluate_ratio(f"sync 커버리지 CLOSED→학습 (최근{window_days}일)", synced, len(closed_ids),
                           warn_below=90, fail_below=70,
                           note="CLOSED 이벤트가 학습원장에 반영된 비율")]


def run_integrity_check(ml_db: Path, event_db: Path, audit_db: Path, window_days: int) -> dict[str, Any]:
    now = _now_utc()
    checks: list[dict[str, Any]] = []
    checks += check_job_freshness(ml_db, event_db, audit_db, now)
    checks += check_data_pipeline_freshness(now)
    checks += check_sleeve_contract_exits(now)
    checks += check_learning_fields(ml_db, now, window_days)
    checks += check_sync_coverage(ml_db, event_db, now, window_days)
    n_fail = sum(1 for c in checks if c["status"] == FAIL)
    n_warn = sum(1 for c in checks if c["status"] == WARN)
    overall = FAIL if n_fail else (WARN if n_warn else OK)
    return {"generated_at": now.isoformat(timespec="seconds"), "overall": overall,
            "fail": n_fail, "warn": n_warn, "checks": checks, "window_days": window_days}


def _to_text(payload: dict[str, Any]) -> str:
    icon = {OK: "🟢", WARN: "🟡", FAIL: "🔴"}
    lines = [f"=== 정합성 체크 {payload['generated_at']} — 종합 {payload['overall']} (FAIL {payload['fail']} / WARN {payload['warn']}) ==="]
    last_kind = None
    titles = {"freshness": "[잡 생존 — D형 탐지]", "population": "[학습원장 충진 — A형 탐지]", "coverage": "[sync 커버리지]"}
    for c in payload["checks"]:
        if c["kind"] != last_kind:
            lines.append(titles.get(c["kind"], c["kind"]))
            last_kind = c["kind"]
        note = f"  · {c['note']}" if c.get("note") else ""
        lines.append(f"  {icon[c['status']]} {c['check']}: {c['detail']}{note}")
    return "\n".join(lines)


STATE_DIR = ROOT / "state"


def _alert_state_path() -> Path:
    return STATE_DIR / "integrity_check_alert.json"


def _load_alert_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_alert_state(path: Path, state: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _alert_items(payload: dict[str, Any], *, include_warn: bool) -> list[str]:
    bad = {FAIL, WARN} if include_warn else {FAIL}
    return sorted(f"{c['check']}={c['status']}" for c in payload["checks"] if c["status"] in bad)


def _fingerprint(items: list[str]) -> str:
    return hashlib.sha1("|".join(items).encode("utf-8")).hexdigest() if items else ""


def _maybe_send_telegram(payload: dict[str, Any], *, include_warn: bool, state_path: Path) -> str:
    """변동(악화/복구)이 있을 때만 텔레그램 전송 — 스팸 방지(live_guardian 알림 패턴)."""
    state = _load_alert_state(state_path)
    previous = str(state.get("fingerprint") or "")
    items = _alert_items(payload, include_warn=include_warn)
    fingerprint = _fingerprint(items)
    if fingerprint == previous:
        return "unchanged"
    recovered = not items and bool(previous)
    if recovered:
        message = "🟢 [정합성] 모든 항목 정상 복구"
    else:
        message = f"🔴 [정합성] FAIL {payload['fail']} / WARN {payload['warn']}\n" + "\n".join(f"  - {it}" for it in items)
    sent = False
    try:
        from telegram_reporter import send

        sent = bool(send(message))
    except Exception:
        sent = False
    _save_alert_state(state_path, {"fingerprint": fingerprint, "updated_at": _now_utc().isoformat(timespec="seconds")})
    return "sent" if sent else "send_skipped"


def _summary_line(payload: dict[str, Any]) -> str:
    return f"[정합성] {payload['generated_at']} 종합 {payload['overall']} (FAIL {payload['fail']}/WARN {payload['warn']})"


def main() -> int:
    parser = argparse.ArgumentParser(description="데이터 파이프라인 정합성 상시 체크 (read-only)")
    parser.add_argument("--ml-db", default=str(DEFAULT_ML_DB))
    parser.add_argument("--event-db", default=str(DEFAULT_EVENT_DB))
    parser.add_argument("--audit-db", default=str(DEFAULT_AUDIT_DB))
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--watch", action="store_true", help="상시 루프 실행(백그라운드 stack 탭용)")
    parser.add_argument("--interval-sec", type=int, default=600)
    parser.add_argument("--max-iterations", type=int, default=0, help="0=무한")
    parser.add_argument("--telegram-alert", action="store_true", help="FAIL 변동 시에만 텔레그램 알림")
    parser.add_argument("--alert-soft", action="store_true", help="WARN도 알림 fingerprint에 포함")
    args = parser.parse_args()

    def _run_once() -> dict[str, Any]:
        payload = run_integrity_check(Path(args.ml_db), Path(args.event_db), Path(args.audit_db), args.window_days)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
        else:
            print(_summary_line(payload) if args.watch else _to_text(payload), flush=True)
        if args.telegram_alert:
            result = _maybe_send_telegram(payload, include_warn=args.alert_soft, state_path=_alert_state_path())
            if args.watch:
                print(f"  telegram={result}", flush=True)
        return payload

    if not args.watch:
        return 1 if _run_once()["fail"] else 0

    iterations = 0
    while True:
        try:
            _run_once()
        except Exception as exc:  # 감시 루프는 어떤 오류에도 죽지 않는다
            print(f"[정합성] 체크 오류(계속): {exc}", flush=True)
        iterations += 1
        if args.max_iterations and iterations >= args.max_iterations:
            return 0
        time.sleep(max(30, int(args.interval_sec or 600)))


if __name__ == "__main__":
    raise SystemExit(main())
