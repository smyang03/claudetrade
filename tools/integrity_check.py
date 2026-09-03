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
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # 스크립트 직접 실행 시 runtime/ 패키지 임포트용
    sys.path.insert(0, str(ROOT))
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


def collect_sleeve_mfe_paths() -> None:
    """A5 (2026-08-06): sleeve 포지션의 MFE 경로 필드를 청산 전에 보존한다.

    실측된 최강 판별력 — 진입 후 고점 선행 승률 4% vs 저점 선행 61% — 의 원료인
    peak_pnl_at/trough_pnl_at 필드가 포지션 청산과 함께 사라진다. 이 수집기가
    watch 주기(600s)마다 sleeve 포지션의 경로 필드를 upsert해 두면, 청산 후에도
    마지막 상태가 남아 "조기고점 정리" counterfactual의 표본이 된다.

    관측 전용 — 트레이딩 DB는 읽기만 하고 별도 jsonl에만 쓴다.
    한계(문서화): 진입 후 10분 내 청산되는 건은 캡처를 놓칠 수 있다.
    """

    positions_path = ROOT / "state" / "live_open_positions.json"
    out_path = ROOT / "data" / "shadow" / "sleeve_mfe_path.jsonl"
    try:
        payload = json.loads(positions_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    rows = payload if isinstance(payload, list) else (payload.get("positions") or [])
    sleeves = [p for p in rows
               if str(p.get("source_strategy") or "").lower() in {"us_swing_5d", "kr_fallen_5d"}]
    if not sleeves:
        return
    fields = ("ticker", "source_strategy", "entry_session_date", "display_avg_price",
              "display_current_price", "peak_pnl_pct", "peak_pnl_at",
              "trough_pnl_pct", "trough_pnl_at", "observed_mfe_pct", "observed_mae_pct",
              "observed_peak_at", "observed_low_at", "position_id", "order_no")
    try:
        existing: dict[str, str] = {}
        if out_path.exists():
            for line in out_path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                    existing[str(row.get("_key"))] = line
                except ValueError:
                    continue
        for pos in sleeves:
            key = f"{pos.get('ticker')}|{pos.get('entry_session_date')}|{pos.get('order_no') or pos.get('position_id')}"
            snap = {"_key": key, "_captured_at": _now_utc().isoformat(timespec="seconds"),
                    **{f: pos.get(f) for f in fields}}
            existing[key] = json.dumps(snap, ensure_ascii=False)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("".join(v + "\n" for v in existing.values()), encoding="utf-8")
    except OSError:
        return


# C1 (2026-08-06): 원장 성장 감시 — 신선도 게이트의 사각 보완.
# 파일이 매일 touch돼도 내용이 늘지 않으면(생산 정지) 못 잡던 것을 잡는다.
# 실측 배경: 새벽 스크리너 "0종목"을 게이트가 정상으로 보던 오탐/진탐 구분 불가.
# (name, kind, locator, min_recent, window_days, note)
LEDGER_GROWTH_CHECKS: tuple[tuple[str, str, str, int, int, str], ...] = (
    ("us_swing 신호 원장", "sqlite",
     "data/analysis/us_swing_shadow.db|SELECT COUNT(DISTINCT signal_date) FROM signals WHERE signal_date >= ?",
     2, 7, "US 세션마다 신호가 쌓여야 한다"),
    ("뉴스 3-arm 원장", "jsonl_dates",
     "data/shadow/us_swing_news_arm_shadow.jsonl|session_date",
     2, 7, "US 세션마다 arm 기록이 쌓여야 한다"),
)


def check_ledger_growth(now: datetime) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for name, kind, locator, min_recent, window_days, note in LEDGER_GROWTH_CHECKS:
        cutoff = (now - timedelta(days=window_days)).strftime("%Y-%m-%d")
        count = None
        try:
            if kind == "sqlite":
                db_rel, query = locator.split("|", 1)
                with _connect_ro(ROOT / db_rel) as con:
                    count = int(con.execute(query, (cutoff,)).fetchone()[0])
            elif kind == "jsonl_dates":
                file_rel, date_key = locator.split("|", 1)
                path = ROOT / file_rel
                if path.exists():
                    dates = set()
                    for line in path.read_text(encoding="utf-8").splitlines():
                        try:
                            value = str(json.loads(line).get(date_key) or "")
                        except ValueError:
                            continue
                        if value >= cutoff:
                            dates.add(value)
                    count = len(dates)
                else:
                    count = 0
        except Exception as exc:
            checks.append({"check": f"원장 성장 {name}", "kind": "growth", "status": WARN,
                           "detail": f"측정 실패: {exc}", "note": note})
            continue
        status = OK if count is not None and count >= min_recent else WARN
        checks.append({"check": f"원장 성장 {name}", "kind": "growth", "status": status,
                       "detail": f"최근 {window_days}일 {count}건 (기준 {min_recent}+)", "note": note})
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


def check_arm_picks_ledger(now: datetime) -> list[dict[str, Any]]:
    """22:36 관측기(arm_picks_realtime) 원장 결손 감시 (2026-09-03). 스케줄 작업엔 하트비트가 없다 —
    후보 풀의 최신 US 세션에 픽 원장 행이 없으면 WARN(관측기 미실행 또는 실패)."""
    name = "실시간 픽 원장(22:36 관측기)"
    pool = ROOT / "data" / "analysis" / "us_swing_shadow.db"
    ledger = ROOT / "data" / "shadow" / "arm_picks_realtime.jsonl"
    try:
        con = sqlite3.connect(f"file:{pool}?mode=ro", uri=True, timeout=5)
        try:
            latest = con.execute("SELECT MAX(session_date) FROM candidate_pool_all").fetchone()[0]
        finally:
            con.close()
    except Exception as exc:
        return [{"check": name, "kind": "phantom", "status": WARN, "detail": f"풀 조회 실패: {exc}", "note": ""}]
    if not latest:
        return [{"check": name, "kind": "phantom", "status": OK, "detail": "후보 풀 없음", "note": ""}]
    n = 0
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if f'"session_date": "{latest}"' in line:
                n += 1
    if n == 0:
        return [{"check": name, "kind": "phantom", "status": WARN,
                 "detail": f"최신 세션 {latest} 픽 원장 0행 — 관측기 미실행/실패 또는 밴드 후보 0", "note": "schtasks claudetrade_arm_picks_realtime 확인"}]
    return [{"check": name, "kind": "phantom", "status": OK, "detail": f"세션 {latest} 픽 {n}행", "note": ""}]


def check_virtual_entry_skips(now: datetime) -> list[dict[str, Any]]:
    """가상 북 진입 스킵 원장 + 가격 캐시 갱신 마커 (2026-09-03 KR 캐시 경합 수리).

    최근 36h의 봉 없음 스킵 중 no_bar_stale(캐시 미갱신/종목 미수집)이 있으면 WARN.
    awaiting_session(다음 세션 대기)은 정상이라 건수만 보인다. 마커는 시장별 end_date·나이."""
    name = "가상 북 진입 스킵·캐시 마커"
    ledger = ROOT / "data" / "shadow" / "virtual_books_entry_skips.jsonl"
    stale: list[str] = []
    awaiting = 0
    cutoff = (now.astimezone(timezone.utc) - timedelta(hours=36)) if now.tzinfo else (now - timedelta(hours=36))
    if ledger.exists():
        for line in ledger.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                ts = datetime.fromisoformat(str(row.get("ts")))
            except (ValueError, TypeError):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts < (cutoff if cutoff.tzinfo else cutoff.replace(tzinfo=timezone.utc)):
                continue
            if row.get("reason") == "no_bar_stale":
                stale.append(f"{row.get('strategy_id')}:{row.get('ticker')}@{row.get('session_date')}")
            else:
                awaiting += 1
    marks = []
    for m in ("KR", "US"):
        p = ROOT / "state" / f"price_update_marker_{m}.json"
        try:
            d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        except (OSError, ValueError):
            d = {}
        marks.append(f"{m}={d.get('end_date') or '없음'}{'' if d.get('ok', True) else '(실패)'}")
    detail = f"36h 스킵 stale {len(stale)} · 대기 {awaiting} · 마커 {' '.join(marks)}"
    if stale:
        return [{"check": name, "kind": "virtual", "status": WARN,
                 "detail": detail + " · " + ", ".join(stale[:6]),
                 "note": "가격 캐시 미갱신/종목 미수집 — update_data 로그·해당 CSV 확인"}]
    return [{"check": name, "kind": "virtual", "status": OK, "detail": detail, "note": ""}]


def check_phantom_isolation(now: datetime) -> list[dict[str, Any]]:
    """유령 포지션 격리 검사 (2026-09-03, 설계 정본 §0-2).

    유령(virtual=True)은 state/phantom_positions.json에만 있어야 하고, 실주문 포지션 파일
    (state/live_open_positions.json)에는 virtual 행이 없어야 한다. 실주문 복귀 시 가상 잔재가
    실자금 한도를 먹는 사고를 매일 여기서 막는다."""
    live_path = ROOT / "state" / "live_open_positions.json"
    phantom_path = ROOT / "state" / "phantom_positions.json"
    name = "유령 포지션 격리"
    try:
        live = json.loads(live_path.read_text(encoding="utf-8") or "[]") if live_path.exists() else []
        phantom = json.loads(phantom_path.read_text(encoding="utf-8") or "[]") if phantom_path.exists() else []
    except Exception as exc:
        return [{"check": name, "kind": "phantom", "status": WARN, "detail": f"파일 읽기 실패: {exc}", "note": ""}]
    leaked = [p.get("ticker") for p in live if isinstance(p, dict) and (p.get("virtual") or p.get("position_origin") == "phantom")]
    unmarked = [p.get("ticker") for p in phantom if isinstance(p, dict) and not p.get("virtual")]
    live_keys = {(str(p.get("market") or "US"), str(p.get("ticker") or "").upper()) for p in live if isinstance(p, dict)}
    dup = [p.get("ticker") for p in phantom if isinstance(p, dict)
           and ("US", str(p.get("ticker") or "").upper()) in live_keys]
    if leaked or unmarked:
        return [{"check": name, "kind": "phantom", "status": FAIL,
                 "detail": f"실주문 파일에 virtual 행 {leaked} / 유령 파일에 무표식 행 {unmarked}", "note": "격리 위반"}]
    note = f"유령 {len(phantom)}건 · 실주문 {len(live)}건" + (f" · 동일 종목 병존 {dup}(허용, 회계 분리)" if dup else "")
    return [{"check": name, "kind": "phantom", "status": OK, "detail": note, "note": "실주문 회계에 유령 없음"}]


def check_contract_env_drift(now: datetime) -> list[dict[str, Any]]:
    """shadow 원장의 계약 지문이 현재 live 설정과 어긋나는지 감시.

    2026-08-10 실측 사고: 절대 허들 폐지(C안) 후 봇은 재시작했지만 shadow runner를
    **스폰하는 부모**(preopen_scheduler)가 옛 env를 들고 있어, 그날 밤 shadow가
    구 계약(허들 true)으로 기록됐다 — 판정 코호트가 조용히 실주문과 갈라졌다.
    교훈: env 변경 시 재시작 대상은 "그 env를 읽는 프로세스 + 그걸 스폰하는 부모 전부".
    사람이 기억하는 대신 여기서 깃발이 서게 한다.

    비교: 현재 live 설정(.env.live + start-config env_overrides)으로 계산한 기대
    contract_id  vs  signals 테이블 최신 세션의 execution_shadow_contract_id.
    """

    try:
        from runtime.us_swing_execution_contract import resolve_execution_contract
    except Exception as exc:  # pragma: no cover - import 실패는 환경 문제
        return [{"check": "US swing 계약 env drift", "kind": "drift", "status": WARN,
                 "detail": f"계약 모듈 로드 실패: {exc}", "note": ""}]

    def _live_env() -> dict[str, str]:
        env: dict[str, str] = {}
        env_path = ROOT / ".env.live"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
        cfg_path = ROOT / "config" / "v2_start_config.json"
        if cfg_path.exists():
            try:
                overrides = json.loads(cfg_path.read_text(encoding="utf-8")).get("env_overrides") or {}
                env.update({str(k): str(v) for k, v in overrides.items()})
            except ValueError:
                pass
        return env

    def _expected_selection_policy(env: dict, truthy: set) -> dict:
        """`runtime.us_swing_order_bridge.resolve_selection_policy`와 같은 키·기본값.

        여기서는 os.environ이 아니라 `.env.live` + start-config를 읽은 env dict를 본다
        (이 검사의 목적이 "설정 정본과 실제 기록이 같은가"이므로).
        """
        band_on = str(env.get("US_SWING_DVOL_BAND_ENABLED", "false")).strip().lower() in truthy
        max_on = str(env.get("US_SWING_MAX_FLOOR_ENABLED", "false")).strip().lower() in truthy
        policy: dict = {"dvol_band_enabled": band_on, "max_floor_enabled": max_on}
        if band_on:
            policy["dvol_band_min_m"] = round(float(env.get("US_SWING_DVOL_BAND_MIN_M", "100") or 100.0), 4)
            policy["dvol_band_max_m"] = round(float(env.get("US_SWING_DVOL_BAND_MAX_M", "500") or 500.0), 4)
        if max_on:
            policy["max_floor_pct"] = round(float(env.get("US_SWING_MAX_FLOOR_PCT", "8") or 8.0), 4)
        # 픽 순서·신호 저장 폭 (2026-09-01 모델 제거) — resolve_selection_policy와 동일 키.
        pick_order = str(env.get("US_SWING_PICK_ORDER", "model_rank") or "model_rank").strip().lower()
        policy["pick_order"] = pick_order if pick_order in ("model_rank", "dvol_desc") else "model_rank"
        try:
            store_top_k = int(float(env.get("US_SWING_STORE_TOP_K", "0") or 0))
        except (TypeError, ValueError):
            store_top_k = 0
        if store_top_k:
            policy["signal_store_top_k"] = store_top_k
        return policy

    try:
        env = _live_env()
        policy = json.loads((ROOT / "config" / "us_swing_accelerated.json").read_text(encoding="utf-8"))
        configured_max = float(env.get("US_SWING_ORDER_MAX_KRW", "250000") or 250000.0)
        truthy = {"1", "true", "yes", "y", "on"}
        # max_hold·BE락은 resolve가 내부에서 os.environ을 본다 — 이 검사 프로세스의
        # env가 아니라 설정 정본(.env.live+start-config)을 보게 주입한다(2026-08-25).
        import os as _os
        _env_keys = ("US_SWING_MAX_HOLD_SESSIONS", "US_SWING_BE_LOCK_TRIGGER_PCT")
        _prev_env = {k: _os.environ.get(k) for k in _env_keys}
        for _k in _env_keys:
            if env.get(_k):
                _os.environ[_k] = env[_k]
        expected = resolve_execution_contract(
            policy=policy, effective_mode="micro",
            configured_max_order_krw=configured_max, base_order_budget_krw=500_000.0,
            absolute_order_cap_krw=configured_max,
            allowed_sources_raw=env.get("US_SWING_ALLOWED_SOURCES", ""),
            override_active=True,
            min_probability=float(env.get("US_SWING_ORDER_MIN_PROB", "0.55") or 0.55),
            min_predicted_net_pct=float(env.get("US_SWING_ORDER_MIN_PREDICTED_NET_PCT", "0.25") or 0.25),
            hurdles_enforced=str(env.get("US_SWING_ORDER_ABSOLUTE_HURDLES_ENFORCED", "false")).lower() in truthy,
            # 실주문 브리지·shadow 러너와 같은 env 키·기본값 (2026-08-21 env 승격)
            max_open_slots_override=int(env.get("US_SWING_MAX_OPEN_SLOTS", "5") or 5),
            max_new_per_day_override=int(env.get("US_SWING_MAX_NEW_PER_DAY", "1") or 1),
            # 선별 정책도 계약 지문에 들어간다 (2026-08-23, P1-3). 여기서 빼면
            # 이 드리프트 검사가 정상 상태를 매번 drift로 오인한다.
            selection_policy=_expected_selection_policy(env, truthy),
        )["contract_id"]
        for _k, _v in _prev_env.items():
            if _v is None:
                _os.environ.pop(_k, None)
            else:
                _os.environ[_k] = _v
        with _connect_ro(ROOT / "data" / "analysis" / "us_swing_shadow.db") as con:
            row = con.execute(
                """SELECT signal_date, COALESCE(execution_shadow_contract_id,'') FROM signals
                   WHERE COALESCE(execution_shadow_contract_id,'')<>''
                   ORDER BY signal_date DESC LIMIT 1"""
            ).fetchone()
    except Exception as exc:
        return [{"check": "US swing 계약 env drift", "kind": "drift", "status": WARN,
                 "detail": f"측정 실패: {exc}", "note": ""}]

    if not row:
        return [{"check": "US swing 계약 env drift", "kind": "drift", "status": OK,
                 "detail": "shadow 계약 기록 없음(표본 대기)", "note": ""}]
    session, actual = str(row[0]), str(row[1])
    status = OK if actual == expected else FAIL
    detail = (f"최신 {session} shadow={actual} / live 기대={expected}"
              + ("" if status == OK else " — runner 스폰 부모(preopen_scheduler) 재시작 필요 가능성"))
    return [{"check": "US swing 계약 env drift", "kind": "drift", "status": status,
             "detail": detail, "note": "env 변경 시 스폰 부모까지 재시작"}]


def _settings_env_sources() -> tuple[dict[str, str], dict[str, str]]:
    """설정 정본 두 소스를 각각 반환 — (.env.live, start-config env_overrides).

    live 반영 규칙상 두 소스가 일치해야 하므로, 합치지 않고 따로 돌려줘서
    불일치 자체를 검사할 수 있게 한다.
    """
    env_file: dict[str, str] = {}
    env_path = ROOT / ".env.live"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env_file[key.strip()] = value.strip()
    overrides: dict[str, str] = {}
    cfg_path = ROOT / "config" / "v2_start_config.json"
    if cfg_path.exists():
        try:
            raw = json.loads(cfg_path.read_text(encoding="utf-8")).get("env_overrides") or {}
            overrides = {str(k): str(v) for k, v in raw.items()}
        except ValueError:
            pass
    return env_file, overrides


def _latest_effective_config() -> tuple[str, dict[str, str]]:
    """봇이 마지막 시작 때 기록한 effective-config 스냅샷 (파일명, effective dict)."""
    cfg_dir = ROOT / "logs" / "config"
    candidates = sorted(cfg_dir.glob("effective_config_*_live.redacted.json"))
    if not candidates:
        return "", {}
    latest = candidates[-1]
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except ValueError:
        return latest.name, {}
    effective = payload.get("effective") or {}
    return latest.name, {str(k): str(v) for k, v in effective.items()}


def check_max_hold_drift(now: datetime) -> list[dict[str, Any]]:
    """sleeve 보유기간(max_hold_sessions) 소스·실효·러너 산출물 일치 감시 (2026-08-25 승인).

    D5→D7 전환처럼 contract_id 재료가 바뀔 때, 아래 층위가 갈라지면 코호트가
    조용히 쪼개진다: ① .env.live vs start-config(두 소스 일치 규칙) ② 봇 실효값
    (effective-config 스냅샷 = 재시작 여부) ③ 러너 산출물(state/us_swing_status.json
    — 스폰 부모가 옛 env를 들고 있는 08-10 사고 계열). ③은 러너가 다음 세션에
    갱신하는 파일이라 전환 직후 하루는 정상 지연일 수 있어 WARN까지만 든다.
    """
    checks: list[dict[str, Any]] = []
    env_file, overrides = _settings_env_sources()
    snap_name, effective = _latest_effective_config()
    for key, label in (("US_SWING_MAX_HOLD_SESSIONS", "US swing"), ("KR_FALLEN_MAX_HOLD_SESSIONS", "KR fallen")):
        a, b = env_file.get(key), overrides.get(key)
        if a is None and b is None:
            checks.append({"check": f"{label} max_hold 소스", "kind": "drift", "status": WARN,
                           "detail": f"{key}가 두 소스 모두 미정의 — 코드 기본값 의존", "note": ""})
            continue
        if a is not None and b is not None and a != b:
            checks.append({"check": f"{label} max_hold 소스", "kind": "drift", "status": FAIL,
                           "detail": f"{key} 두 소스 불일치: .env.live={a} / start-config={b}",
                           "note": "두 소스 동시변경 규칙 위반"})
            continue
        expected = b if b is not None else a
        eff = effective.get(key)
        if effective and eff != expected:
            checks.append({"check": f"{label} max_hold 실효", "kind": "drift", "status": FAIL,
                           "detail": f"설정={expected} vs 봇 실효({snap_name})={eff}",
                           "note": "봇 재시작 필요"})
        else:
            checks.append({"check": f"{label} max_hold", "kind": "drift", "status": OK,
                           "detail": f"소스·실효 일치 = {expected}", "note": ""})
    # ③ 러너 산출물 (US만 — 상태파일이 있는 레인)
    status_path = ROOT / "state" / "us_swing_status.json"
    expected_us = overrides.get("US_SWING_MAX_HOLD_SESSIONS") or env_file.get("US_SWING_MAX_HOLD_SESSIONS")
    if status_path.exists() and expected_us is not None:
        try:
            contract = (json.loads(status_path.read_text(encoding="utf-8")).get("execution_contract") or {})
            runner_val = contract.get("max_hold_sessions")
            if runner_val is not None and str(runner_val) != str(expected_us):
                checks.append({"check": "US swing max_hold 러너 산출물", "kind": "drift", "status": WARN,
                               "detail": f"설정={expected_us} vs us_swing_status.json={runner_val}",
                               "note": "다음 러너(22:20) 갱신 대기 가능 — 이틀째면 스폰 부모 재시작 의심"})
            else:
                checks.append({"check": "US swing max_hold 러너 산출물", "kind": "drift", "status": OK,
                               "detail": f"러너 계약 일치 = {runner_val}", "note": ""})
        except ValueError:
            checks.append({"check": "US swing max_hold 러너 산출물", "kind": "drift", "status": WARN,
                           "detail": "us_swing_status.json 파싱 실패", "note": ""})
    return checks


def check_stack_processes_alive(now: datetime) -> list[dict[str, Any]]:
    """라이브 스택 프로세스 생존 감시 — 08-28 사고 재발 방지 (2026-08-29 운영자 승인).

    실사고: preopen_scheduler가 하트비트 쓰기 실패(WinError 5)로 18:05에 조용히
    죽었는데, 로그·정합성·에러 카운트가 전부 정상이라 **11시간 동안 아무도 몰랐다.**
    그 사이 22:33 US 러너가 안 돌아 신호가 생성되지 않았고 진입 기회를 통째로
    잃었다("후보 없음"으로 보였다). 데이터만 감시하면 실행 주체의 죽음을 못 본다.

    프로세스별 심각도:
      FAIL — trading_bot·live_guardian·preopen_scheduler (죽으면 매매/스캔 정지)
      WARN — 그 외 보조 프로세스(대시보드·수집기 등)
    """
    pid_path = ROOT / "state" / "headless_live_stack_pids.json"
    if not pid_path.exists():
        return [{"check": "스택 프로세스 생존", "kind": "process", "status": WARN,
                 "detail": "PID 레지스트리 없음", "note": ""}]
    try:
        registry = json.loads(pid_path.read_text(encoding="utf-8-sig"))
    except ValueError:
        return [{"check": "스택 프로세스 생존", "kind": "process", "status": WARN,
                 "detail": "PID 레지스트리 파싱 실패", "note": ""}]
    if not isinstance(registry, dict):
        return [{"check": "스택 프로세스 생존", "kind": "process", "status": WARN,
                 "detail": "PID 레지스트리 형식 예상 밖", "note": ""}]
    critical = {"trading_bot", "live_guardian", "preopen_scheduler"}
    dead_critical: list[str] = []
    dead_other: list[str] = []
    for name, pid in registry.items():
        try:
            pid_int = int(pid)
        except (TypeError, ValueError):
            continue
        alive = False
        try:
            # Windows: os.kill(pid, 0)은 TerminateProcess다(08-13 지뢰) — 절대 쓰지 않는다.
            # tasklist 조회로만 생존을 판정한다.
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid_int}", "/NH"],
                capture_output=True, text=True, timeout=15,
            ).stdout
            alive = str(pid_int) in out
        except (OSError, subprocess.SubprocessError):
            continue
        if not alive:
            (dead_critical if name in critical else dead_other).append(f"{name}({pid_int})")
    if dead_critical:
        return [{"check": "스택 프로세스 생존", "kind": "process", "status": FAIL,
                 "detail": f"핵심 프로세스 사망: {', '.join(dead_critical)}"
                           + (f" · 보조: {', '.join(dead_other)}" if dead_other else ""),
                 "note": "재기동 필요 — 08-28 사고(러너 미실행) 계열"}]
    if dead_other:
        return [{"check": "스택 프로세스 생존", "kind": "process", "status": WARN,
                 "detail": f"보조 프로세스 사망: {', '.join(dead_other)}", "note": "매매는 계속되나 관측 손실"}]
    return [{"check": "스택 프로세스 생존", "kind": "process", "status": OK,
             "detail": f"{len(registry)}개 전부 생존", "note": ""}]


def check_price_currency_consistency(ml_db: Path, now: datetime) -> list[dict[str, Any]]:
    """US 행의 진입가/청산가 통화 혼입 감시 — 0건이 정상 (2026-08-28 감사에서 발견).

    entry_price는 native(USD)인데 sleeve CLOSED의 exit_price는 내부 KRW 환산가라,
    US 행에서 두 컬럼의 통화가 갈렸다(실측 5건, exit/entry 1,000배대). 손익 컬럼은
    KRW 기준으로 따로 계산되어 net 판정은 무영향이었으나 가격 재계산·감사가 막힌다.
    수리 후(exit_price_native 우선) 신규 행은 정상이어야 하므로 상시 감시한다.
    """
    try:
        with _connect_ro(ml_db) as ml:
            rows = ml.execute(
                """SELECT session_date, ticker, entry_price, last_exit_price
                   FROM v2_canonical_performance
                   WHERE market='US' AND closed=1 AND entry_price>0 AND last_exit_price>0
                     AND last_exit_price/entry_price > 50
                   ORDER BY session_date DESC"""
            ).fetchall()
    except sqlite3.Error as exc:
        return [{"check": "US 가격 통화 정합", "kind": "alignment", "status": WARN,
                 "detail": f"조회 실패: {exc}", "note": ""}]
    # MXL(08-20 진입)은 수리 커밋(2026-08-28 16:08) **이후** 08-28 19:45에 청산됐으므로
    # 원래는 신규 혼입 = 회귀다. 다만 원인은 코드가 아니라 배포다 — 수리를 담은 trading_bot
    # 프로세스가 08-25 23:50 기동이라 청산 시점에 구코드가 CLOSED를 발행했고, 이 감시
    # 자체도 같은 이유로 돌지 않아 못 잡았다. CLOSED 이벤트에 native가 없어 복원 경로는
    # 기존 5건과 동일하게 막혀 있다(운영자 결정 2026-08-30). 재시작 이후 발생하는 혼입은
    # 배포 갭으로 설명되지 않으므로 그때는 진짜 회귀다.
    known = {("2026-08-12", "FA"), ("2026-08-17", "WIX"), ("2026-08-18", "FRVO"),
             ("2026-08-19", "AXTI"), ("2026-08-25", "RGTI"), ("2026-08-20", "MXL")}
    fresh = [(str(r[0]), str(r[1])) for r in rows if (str(r[0]), str(r[1])) not in known]
    if fresh:
        sample = ", ".join(f"{t}({d})" for d, t in fresh[:3])
        return [{"check": "US 가격 통화 정합", "kind": "alignment", "status": FAIL,
                 "detail": f"신규 혼입 {len(fresh)}건: {sample} — CLOSED payload의 exit_price_native 확인",
                 "note": "2026-08-28 수리 이후 신규 발생은 회귀"}]
    return [{"check": "US 가격 통화 정합", "kind": "alignment", "status": OK,
             "detail": f"신규 혼입 0건 (기지 {len(rows)}건은 수리 전 잔존 — net 판정 무영향)", "note": ""}]


def check_canonical_session_alignment(ml_db: Path, event_db: Path, now: datetime) -> list[dict[str, Any]]:
    """canonical.session_date가 v2_decisions 정본과 어긋난 행 감시 — 0건이 정상 (2026-08-25 승인).

    d2215ea 결함 클래스(창 있는 sync + 청산일 백필 이벤트가 진입 정본을 가림)의
    재발 감시. 어긋난 행은 판정 코호트에서 조용히 빠지거나(strategy 소실)
    D5/D7·밴드 전후 같은 진입일 기준 코호트 분리를 오염시킨다.
    """
    cutoff = datetime.fromtimestamp(now.timestamp() - 60 * 86400, tz=timezone.utc).strftime("%Y-%m-%d")
    try:
        with _connect_ro(event_db) as ev:
            truth = {str(r[0]): str(r[1] or "") for r in ev.execute(
                "SELECT decision_id, session_date FROM v2_decisions WHERE session_date>=?", (cutoff,))}
        with _connect_ro(ml_db) as ml:
            rows = ml.execute(
                "SELECT v2_decision_id, session_date, ticker FROM v2_canonical_performance "
                "WHERE runtime_mode='live' AND session_date>=?", (cutoff,)).fetchall()
    except sqlite3.Error as exc:
        return [{"check": "canonical 진입일 정합 (최근60일)", "kind": "alignment", "status": WARN,
                 "detail": f"조회 실패: {exc}", "note": ""}]
    mismatched = [(str(r[0]), str(r[2]), str(r[1]), truth[str(r[0])])
                  for r in rows if str(r[0]) in truth and truth[str(r[0])] != str(r[1])]
    if mismatched:
        sample = ", ".join(f"{t}({have}→정본{want})" for _, t, have, want in mismatched[:3])
        return [{"check": "canonical 진입일 정합 (최근60일)", "kind": "alignment", "status": FAIL,
                 "detail": f"불일치 {len(mismatched)}건: {sample}",
                 "note": "sync 재실행으로 복구 후 원인 조사 (d2215ea 계열)"}]
    return [{"check": "canonical 진입일 정합 (최근60일)", "kind": "alignment", "status": OK,
             "detail": f"대조 {len(rows)}행 불일치 0", "note": ""}]


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
    checks += check_ledger_growth(now)
    checks += check_sleeve_contract_exits(now)
    checks += check_contract_env_drift(now)
    checks += check_phantom_isolation(now)
    checks += check_arm_picks_ledger(now)
    checks += check_virtual_entry_skips(now)
    checks += check_max_hold_drift(now)
    checks += check_canonical_session_alignment(ml_db, event_db, now)
    checks += check_price_currency_consistency(ml_db, now)
    checks += check_stack_processes_alive(now)
    collect_sleeve_mfe_paths()  # A5 관측 수집(판정 아님) — watch 주기마다 경로 보존
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
