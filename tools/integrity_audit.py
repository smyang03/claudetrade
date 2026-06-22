#!/usr/bin/env python3
"""시스템 무결성 전수 검사 (standing integrity audit) — read-only, 결정론적.

`tools/integrity_check.py`(파이프라인 freshness/population)를 확장해, brain 화석화 버그
(correction_guide 5월 급락장 렌즈가 6월 강세장 selection에 35일째 주입)를 계기로
"AI 눈이 아니라 코드가 1차로 잡는" 결정론적 의심 목록을 만든다.

설계: docs/important/INTEGRITY_AUDIT_PLAN.md §4. 8개 모듈 전부 결정론적이며 주문/브로커/
Claude 호출 없음. 각 항목은 OK/WARN/ALERT + needs_ai_judgment(버그 vs 정상/과거데이터를
숫자만으로 못 가르는 항목)로 표시한다. 숫자는 단정이 아니라 토론 입력이다.

사용:
  python tools/integrity_audit.py            # 사람용 표
  python tools/integrity_audit.py --json      # JSON
출력 파일: state/integrity_audit_report.json
exit code: ALERT 있으면 1, 아니면 0(WARN은 0).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# integrity_check의 평가 헬퍼 재사용(over-build 방지).
# 스크립트 실행(tools/ on path)·모듈 import(tools.integrity_audit) 양쪽 지원.
try:
    from integrity_check import (  # noqa: E402
        _connect_ro,
        _parse_ts,
        evaluate_freshness,
        evaluate_population,
    )
except ImportError:  # pragma: no cover
    from tools.integrity_check import (  # noqa: E402
        _connect_ro,
        _parse_ts,
        evaluate_freshness,
        evaluate_population,
    )

ROOT = Path(__file__).resolve().parent.parent
ML_DB = ROOT / "data" / "ml" / "decisions.db"
FACTS_DB = ROOT / "data" / "ml" / "claude_decision_facts.db"
SELECTION_DB = ROOT / "data" / "ticker_selection_log.db"
AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
EVENT_DB = ROOT / "data" / "v2_event_store.db"
BRAIN_PATH = ROOT / "state" / "brain.json"
CONFIG_PATH = ROOT / "config" / "v2_start_config.json"
ENV_LIVE_PATH = ROOT / ".env.live"
STATE_DIR = ROOT / "state"
REPORT_PATH = STATE_DIR / "integrity_audit_report.json"

OK, WARN, ALERT = "OK", "WARN", "ALERT"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _item(module: str, check: str, status: str, value: str, threshold: str = "",
          *, needs_ai: bool = False, note: str = "") -> dict[str, Any]:
    return {"module": module, "check": check, "status": status, "value": value,
            "threshold": threshold, "needs_ai_judgment": needs_ai, "note": note}


def _null_pct(conn: sqlite3.Connection, table: str, col: str, where: str = "") -> tuple[int, int]:
    """(non_null, total). 빈 문자열도 NULL로 본다."""
    clause = f" WHERE {where}" if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM {table}{clause}").fetchone()[0]
    if total == 0:
        return 0, 0
    nn = conn.execute(
        f"SELECT COUNT(*) FROM {table}{clause}{' AND' if where else ' WHERE'} "
        f"[{col}] IS NOT NULL AND [{col}]!=''"
    ).fetchone()[0]
    return nn, total


# ---------------------------------------------------------------------------
# 모듈 1: db_null_coverage — 핵심 컬럼 충진율(배선 끊김 E형 탐지)
# ---------------------------------------------------------------------------
def check_db_null_coverage() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    m = "db_null_coverage"

    # fact_forward_outcome: 1d/3d/5d forward 라벨(선정품질 분석의 ground truth)
    if FACTS_DB.exists():
        try:
            with _connect_ro(FACTS_DB) as c:
                for col in ("forward_1d_pct", "forward_3d_pct", "forward_5d_pct"):
                    nn, tot = _null_pct(c, "fact_forward_outcome", col)
                    pct = 100.0 * nn / tot if tot else 0
                    status = ALERT if pct < 30 else (WARN if pct < 70 else OK)
                    items.append(_item(m, f"fact_forward_outcome.{col}", status,
                                       f"{pct:.0f}% ({nn}/{tot})", ">=70% 기대",
                                       needs_ai=(status != OK),
                                       note="선정→forward outcome 라벨. 0%면 fact 기반 분석 무효"))
        except sqlite3.Error as e:
            items.append(_item(m, "fact_forward_outcome", WARN, f"쿼리 실패: {e}"))

    # v2_learning_performance: 측정 핵심 컬럼(최근 14일 closed)
    if ML_DB.exists():
        try:
            cutoff = (_now().timestamp() - 14 * 86400)
            with _connect_ro(ML_DB) as c:
                rows = c.execute(
                    "SELECT market_regime,mfe_pct,mae_pct,pnl_pct_net,fx_change_pct,closed_at "
                    "FROM v2_learning_performance WHERE closed=1 AND closed_at IS NOT NULL"
                ).fetchall()
                recent = [r for r in rows if (_parse_ts(r["closed_at"]) or _now()).timestamp() >= cutoff]
                tot = len(recent)
                fields = [
                    ("market_regime", 70, 30, True, "진입국면. 6/21 배선복구 후 신규 채워지나?"),
                    ("mfe_pct", 70, 30, True, "Phase1c MFE. producer/sync 갭 의심"),
                    ("mae_pct", 70, 30, True, "Phase1c MAE"),
                    ("pnl_pct_net", 80, 50, False, "net 손익(수수료반영)"),
                    ("fx_change_pct", 50, 20, True, "환변동. KIS 미노출이면 설계상 낮음"),
                ]
                for f, warn_b, alert_b, ai, note in fields:
                    pop = sum(1 for r in recent if r[f] not in (None, "", 0, 0.0))
                    pct = 100.0 * pop / tot if tot else 0
                    if tot < 10:
                        status = OK
                    else:
                        status = ALERT if pct < alert_b else (WARN if pct < warn_b else OK)
                    items.append(_item(m, f"v2_learning.{f} (최근14일)", status,
                                       f"{pct:.0f}% ({pop}/{tot})", f">={warn_b}%",
                                       needs_ai=(ai and status != OK), note=note))
        except sqlite3.Error as e:
            items.append(_item(m, "v2_learning_performance", WARN, f"쿼리 실패: {e}"))

    # ticker_selection_log: traded 행 중 execution_decision_id 연결율(attribution 배선)
    if SELECTION_DB.exists():
        try:
            with _connect_ro(SELECTION_DB) as c:
                traded = c.execute("SELECT COUNT(*) FROM ticker_selection_log WHERE traded=1").fetchone()[0]
                eid = c.execute("SELECT COUNT(*) FROM ticker_selection_log WHERE traded=1 "
                                "AND execution_decision_id IS NOT NULL").fetchone()[0]
                pct = 100.0 * eid / traded if traded else 0
                status = ALERT if pct < 50 else (WARN if pct < 80 else OK)
                items.append(_item(m, "selection.execution_decision_id (traded행)", status,
                                   f"{pct:.0f}% ({eid}/{traded})", ">=80%",
                                   needs_ai=True,
                                   note="traded=0(watch_only)은 NULL 정상. PathB는 traded 미표기 가능"))
        except sqlite3.Error as e:
            items.append(_item(m, "ticker_selection_log", WARN, f"쿼리 실패: {e}"))
    return items


# ---------------------------------------------------------------------------
# 모듈 2: db_freshness — 멈춘 잡(D형). fact_* 빌드 정지가 핵심.
# ---------------------------------------------------------------------------
def check_db_freshness() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    m = "db_freshness"
    now = _now()
    if FACTS_DB.exists():
        try:
            with _connect_ro(FACTS_DB) as c:
                latest = c.execute("SELECT MAX(updated_at) FROM fact_forward_outcome").fetchone()[0]
                r = evaluate_freshness("fact_forward_outcome 갱신", latest, now,
                                       warn_days=7, fail_days=14,
                                       note="build_claude_decision_facts.py 수동전용. 멈추면 fact 분석 stale")
                items.append(_item(m, r["check"], ALERT if r["status"] == "FAIL" else r["status"],
                                   r["detail"], "<=7일", needs_ai=(r["status"] != OK), note=r["note"]))
        except sqlite3.Error as e:
            items.append(_item(m, "fact_forward_outcome 갱신", WARN, f"쿼리 실패: {e}"))
    return items


# ---------------------------------------------------------------------------
# 모듈 3: db_sync — 원장 간 행 일치(끊긴 sync)
# ---------------------------------------------------------------------------
def check_db_sync() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    m = "db_sync"
    if not (ML_DB.exists() and EVENT_DB.exists()):
        return items
    try:
        with _connect_ro(EVENT_DB) as ev:
            tables = {r[0] for r in ev.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            canon = None
            if "v2_decisions" in tables:
                canon = ev.execute("SELECT COUNT(*) FROM v2_decisions").fetchone()[0]
        with _connect_ro(ML_DB) as ml:
            learn = ml.execute("SELECT COUNT(*) FROM v2_learning_performance").fetchone()[0]
        if canon is not None:
            note = "v2_decisions(canonical) vs v2_learning_performance 행수"
            items.append(_item(m, "v2_decisions ↔ v2_learning 행수", OK,
                               f"canonical {canon} / learning {learn}", "참고치", needs_ai=True, note=note))
    except sqlite3.Error as e:
        items.append(_item(m, "v2 sync", WARN, f"쿼리 실패: {e}"))
    return items


# ---------------------------------------------------------------------------
# 모듈 4: brain_staleness — 화석화(A형). 날짜메타 age + 메타 부재.
# ---------------------------------------------------------------------------
def _age_days(date_str: str) -> float | None:
    ts = _parse_ts(date_str)
    return None if ts is None else (_now() - ts).total_seconds() / 86400.0


def check_brain_staleness() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    m = "brain_staleness"
    if not BRAIN_PATH.exists():
        return [_item(m, "brain.json", WARN, "파일 없음")]
    try:
        brain = json.loads(BRAIN_PATH.read_text(encoding="utf-8-sig"))
    except Exception as e:
        return [_item(m, "brain.json 파싱", ALERT, str(e))]

    cg = brain.get("correction_guide", {}) or {}
    for mk in ("KR", "US"):
        gd = (cg.get(mk, {}) or {}).get("generated_date")
        if not gd:
            items.append(_item(m, f"correction_guide[{mk}].generated_date", WARN,
                               "날짜메타 없음", needs_ai=True,
                               note="신선도 게이트 불가 → stale 주입 위험"))
            continue
        age = _age_days(gd)
        if age is None:
            status = WARN
            val = f"파싱불가({gd})"
        else:
            status = ALERT if age > 14 else (WARN if age > 7 else OK)
            val = f"{age:.0f}일 전 ({gd})"
        items.append(_item(m, f"correction_guide[{mk}] 신선도", status, val, "<=7일",
                           needs_ai=(status != OK),
                           note="급락장 렌즈 화석화 사례(5/18→6월 주입)"))

    # 날짜메타 부재 섹션(게이트 불가 — F형 가드누락의 전제)
    for mk in ("KR", "US"):
        payload = brain.get("markets", {}).get(mk, {})
        beliefs = payload.get("current_beliefs", {}) or {}
        no_date_sections = []
        if "learned_lessons" in beliefs and not beliefs.get("_generated_date"):
            no_date_sections.append("learned_lessons")
        if beliefs.get("market_regime") and not beliefs.get("_regime_date"):
            no_date_sections.append("market_regime")
        if payload.get("issue_patterns") and not any(p.get("date") or p.get("last_seen")
                                                     for p in payload["issue_patterns"]):
            no_date_sections.append("issue_patterns")
        if no_date_sections:
            items.append(_item(m, f"{mk} 날짜메타 부재 섹션", WARN,
                               ",".join(no_date_sections), "신선도 게이트 가능 구조",
                               needs_ai=True,
                               note="날짜 없어 stale 판별 불가 → 게이트 미적용"))
    return items


# ---------------------------------------------------------------------------
# 모듈 5: brain_pollution — 오염/과적합(B/F형).
# ---------------------------------------------------------------------------
US_TICKER_RE = re.compile(
    r"\b(SRPT|BRZE|PAYS|NVDA|TSLA|AAPL|AMD|AVGO|INTC|MSFT|GOOGL|META|AMZN|QQQ|SPY|"
    r"PLTR|SOFI|IREN|IONQ|MRVL|KLAC|TXG|CWAN|SYRE)\b"
)


def check_brain_pollution() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    m = "brain_pollution"
    if not BRAIN_PATH.exists():
        return items
    try:
        brain = json.loads(BRAIN_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return items

    for mk in ("KR", "US"):
        ips = brain.get("markets", {}).get(mk, {}).get("issue_patterns", []) or []
        if not ips:
            continue
        # count<=1 과적합 비율
        c1 = sum(1 for p in ips if int(p.get("count", 0) or 0) <= 1)
        ratio = 100.0 * c1 / len(ips)
        status = ALERT if ratio >= 90 else (WARN if ratio >= 70 else OK)
        items.append(_item(m, f"{mk} issue_patterns count<=1 비율", status,
                           f"{ratio:.0f}% ({c1}/{len(ips)})", "<70%",
                           needs_ai=True,
                           note="단일사건 교훈 과적합. count>=2 게이트 후보"))
        # description == insight 동일(매칭 깨짐)
        same = sum(1 for p in ips if (p.get("description") or "").strip()
                   and (p.get("description") or "").strip() == (p.get("insight") or "").strip())
        if same:
            items.append(_item(m, f"{mk} issue_patterns desc==insight", WARN,
                               f"{same}건", "0건", note="description-insight 매칭 깨짐"))
        # KR에 US티커 오염
        if mk == "KR":
            hits = [p for p in ips if US_TICKER_RE.search(
                (p.get("description") or "") + " " + (p.get("insight") or ""))]
            if hits:
                items.append(_item(m, "KR issue_patterns 내 US티커 오염", ALERT,
                                   f"{len(hits)}건", "0건", needs_ai=True,
                                   note="KR brain에 US종목 혼입(예: SRPT/BRZE/PAYS)"))
    return items


# ---------------------------------------------------------------------------
# 모듈 6: brain_injection_guards — 각 주입경로 V2 fresh brain 가드 정적검사.
# ---------------------------------------------------------------------------
INJECTION_PATHS = [
    ("① 시장판단", "trading_bot.py", "_v2_fresh_brain_policy_enabled", "_brain_context_for_judge"),
    ("② Selection", "minority_report/analysts.py", "_v2_fresh_brain_selection_active", "select_tickers"),
    ("③ Postmortem", "minority_report/postmortem.py", "_v2_fresh_brain", "generate_prompt_summary"),
    ("④ HoldAdvisor", "minority_report/hold_advisor.py", None, "_MEASURED_PRIORS"),
    ("⑤ ActiveLessons", "minority_report/active_lessons.py", None, "_select_items"),
]


def check_brain_injection_guards() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    m = "brain_injection_guards"
    for label, rel, guard_marker, anchor in INJECTION_PATHS:
        path = ROOT / rel
        if not path.exists():
            items.append(_item(m, f"{label} 파일", WARN, f"{rel} 없음"))
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        injects_brain = "generate_prompt_summary" in text or "correction_guide" in text
        if guard_marker is None:
            # brain 미참조 경로 — 주입 토큰 있으면 재확인
            status = WARN if injects_brain else OK
            items.append(_item(m, f"{label} 가드", status,
                               "brain 미참조(가드 N/A)" if not injects_brain else "주입 토큰 발견-재확인",
                               "N/A", needs_ai=injects_brain,
                               note="하드코딩/별도 store. brain 미주입이면 안전"))
            continue
        has_guard = guard_marker in text
        if has_guard:
            items.append(_item(m, f"{label} fresh brain 가드", OK, f"{guard_marker} 존재", "가드 필수"))
        else:
            status = ALERT if injects_brain else WARN
            items.append(_item(m, f"{label} fresh brain 가드", status,
                               f"{guard_marker} 부재" + (" + brain 주입함" if injects_brain else ""),
                               "가드 필수", needs_ai=True,
                               note="stale brain이 프롬프트에 무조건 주입될 위험"))
    return items


# ---------------------------------------------------------------------------
# 모듈 7: config_consistency — config↔env, 핵심 안전토글 단언.
# ---------------------------------------------------------------------------
SAFETY_ASSERTIONS = [
    ("CLAUDE_REVIEW_ALL_AUTOMATED_SELLS", "true", ALERT, "Path A 자동매도 전 Claude 리뷰 게이트"),
    ("US_MOMENTUM_LIVE_ENABLED", "true", WARN, "US 누적수익 경로"),
    ("KR_PATHB_WEAK_MFE_CUT_ENABLED", "false", WARN, "KR weak_mfe OFF(2026-06-16)"),
    ("US_PATHB_WEAK_MFE_CUT_ENABLED", "false", WARN, "US weak_mfe OFF"),
    ("KR_MOMENTUM_EARLY_ENTRY_ENABLED", "false", WARN, "KR momentum early OFF(2026-06-21)"),
    ("PATHB_US_LIVE_ENABLED", "true", WARN, "US PathB live"),
    ("PATHB_KR_LIVE_ENABLED", "true", WARN, "KR PathB live"),
]


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def check_config_consistency() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    m = "config_consistency"
    cfg_env: dict[str, str] = {}
    if CONFIG_PATH.exists():
        try:
            cfg_env = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("env_overrides", {}) or {}
        except Exception as e:
            items.append(_item(m, "config 파싱", ALERT, str(e)))
    env_live = _parse_env_file(ENV_LIVE_PATH)

    for key, expected, fail_status, desc in SAFETY_ASSERTIONS:
        cv = str(cfg_env.get(key, "")).lower()
        ev = str(env_live.get(key, "")).lower()
        # config(env_overrides)가 live에서 우선이므로 그 값을 우선 본다
        effective = cv if cv != "" else ev
        if effective == "":
            items.append(_item(m, f"{key}", WARN, "config/env 둘 다 미설정", expected,
                               needs_ai=True, note=desc))
        elif effective != expected:
            items.append(_item(m, f"{key}", fail_status, f"={effective} (기대 {expected})", expected,
                               note=desc))
        else:
            # config↔env drift(둘 다 있는데 다름)
            if cv and ev and cv != ev:
                items.append(_item(m, f"{key} drift", WARN, f"config={cv} env={ev}", "일치",
                                   note=desc))
            else:
                items.append(_item(m, f"{key}", OK, f"={effective}", expected, note=desc))
    return items


# ---------------------------------------------------------------------------
# 모듈 8: state_freshness — state 구파일 누적.
# ---------------------------------------------------------------------------
def check_state_freshness() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    m = "state_freshness"
    if not STATE_DIR.exists():
        return items
    now = time.time()
    stale_30d = 0
    total = 0
    for p in STATE_DIR.glob("*.json"):
        total += 1
        if (now - p.stat().st_mtime) > 30 * 86400:
            stale_30d += 1
    items.append(_item(m, "state/*.json 30일+ 미수정", WARN if stale_30d > 20 else OK,
                       f"{stale_30d}/{total} 파일", "<=20", note="구파일 누적 위생"))
    # brain.json 자체 신선도
    if BRAIN_PATH.exists():
        age = (now - BRAIN_PATH.stat().st_mtime) / 86400.0
        items.append(_item(m, "brain.json mtime", OK if age < 7 else WARN,
                           f"{age:.1f}일 전", "<7일", note="정책메모리 갱신 흐름"))
    return items


MODULES = [
    ("db_null_coverage", check_db_null_coverage),
    ("db_freshness", check_db_freshness),
    ("db_sync", check_db_sync),
    ("brain_staleness", check_brain_staleness),
    ("brain_pollution", check_brain_pollution),
    ("brain_injection_guards", check_brain_injection_guards),
    ("config_consistency", check_config_consistency),
    ("state_freshness", check_state_freshness),
]


def run_audit() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for _name, fn in MODULES:
        try:
            items += fn()
        except Exception as e:  # 한 모듈 실패가 전체를 죽이지 않게
            items.append(_item(_name, "모듈 실행", WARN, f"예외: {e}"))
    counts = {
        "alert": sum(1 for i in items if i["status"] == ALERT),
        "warn": sum(1 for i in items if i["status"] == WARN),
        "ok": sum(1 for i in items if i["status"] == OK),
        "needs_ai": sum(1 for i in items if i["needs_ai_judgment"]),
    }
    overall = ALERT if counts["alert"] else (WARN if counts["warn"] else OK)
    return {"generated_at": _now().isoformat(timespec="seconds"), "overall": overall,
            "counts": counts, "items": items}


def _to_text(payload: dict[str, Any]) -> str:
    icon = {OK: "🟢", WARN: "🟡", ALERT: "🔴"}
    c = payload["counts"]
    lines = [f"=== 무결성 감사 {payload['generated_at']} — 종합 {payload['overall']} "
             f"(ALERT {c['alert']} / WARN {c['warn']} / OK {c['ok']} / AI판정필요 {c['needs_ai']}) ==="]
    last_mod = None
    for it in payload["items"]:
        if it["module"] != last_mod:
            lines.append(f"\n[{it['module']}]")
            last_mod = it["module"]
        ai = " ⚖️AI판정" if it["needs_ai_judgment"] else ""
        thr = f" (기준 {it['threshold']})" if it["threshold"] else ""
        note = f"\n      · {it['note']}" if it["note"] else ""
        lines.append(f"  {icon[it['status']]} {it['check']}: {it['value']}{thr}{ai}{note}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="시스템 무결성 전수 검사 (read-only)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-write", action="store_true", help="report 파일 저장 안 함")
    args = ap.parse_args()
    payload = run_audit()
    if not args.no_write:
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_to_text(payload))
        print(f"\n저장: {REPORT_PATH}")
    return 1 if payload["counts"]["alert"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
