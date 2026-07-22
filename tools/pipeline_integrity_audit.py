from __future__ import annotations

"""파이프라인 무결성 감사 — "코드는 맞는데 데이터가 안 흐르는" 누수를 잡는다.

왜 필요한가:
  2026-07-22 하루에만 같은 유형의 누수가 셋 나왔고 전부 정적 점검(코드 grep)으로는
  잡히지 않았다. 코드는 정상인데 데이터가 조건을 못 채워서 실전에서만 드러났다.

    rel_vol       계산 O → 원장 저장 X → 랭킹이 거래량을 못 봄
    BUY_READY     judge 판정 O → evidence 결측으로 PROBE_READY 강등 → 즉시매수 배선 미매칭
    volume_ratio  US 전량 1.0 placeholder → evidence partial → 위 강등의 직접 원인

  공통 구조는 [생성] → [저장] → [소비] 중 한 곳이 끊긴 것이다. 그래서 각 단계에
  실제 값이 몇 %나 있는지를 재고, 끊긴 지점을 지목한다.

점검 축:
  1. 필드 커버리지    — 원장별·시장별 NULL/placeholder 비율. 고유값 1개면 placeholder 의심.
  2. evidence 사슬    — 결측 필드 → action_ceiling 강등 → 진입 차단으로 이어지는 경로.
  3. 판정 전파율      — judge 판정이 route/진입까지 살아남는 비율(단계별 감쇠).
  4. 원장 간 갭       — A 원장에 있고 B 원장에 없는 지표(전파 누락 후보).

전부 읽기 전용이며 주문·상태를 건드리지 않는다.

  python tools/pipeline_integrity_audit.py
  python tools/pipeline_integrity_audit.py --since 2026-07-01 --market US
"""

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"
SEL_DB = ROOT / "data" / "ticker_selection_log.db"
ML_DB = ROOT / "data" / "ml" / "decisions.db"
EVENT_DB = ROOT / "data" / "v2_event_store.db"

# 값이 하나뿐이어도 정상인 필드(시장별 조회라 당연히 1개이거나, 설계상 단일값)
PLACEHOLDER_EXEMPT = {
    "market", "runtime_mode", "screener_seen", "session_date",
    # KR 전용 기능은 US에서 0이 정상이다(반대도 마찬가지).
    "strength_capture_shadow", "strength_capture_rules",
    "bullish_probe_shadow", "bullish_probe_selected", "bullish_probe_cost_pct",
}


def _con(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=60)
    con.execute("PRAGMA busy_timeout=50000")
    return con


def audit_field_coverage(since: str) -> None:
    """축 1 — 필드 커버리지. 한쪽 시장만 죽은 필드가 가장 위험하다."""
    con = _con(AUDIT_DB)
    if not con:
        print("  audit DB 없음")
        return
    cols = [r[1] for r in con.execute("PRAGMA table_info(audit_candidate_rows)")]
    skip = ("json", "_at", "key", "id", "name", "reason", "text", "summary",
            "version", "hash", "contract")
    targets = [c for c in cols if not any(k in c.lower() for k in skip)]
    findings: list[tuple] = []
    for c in targets:
        if c in PLACEHOLDER_EXEMPT:
            continue
        stat = {}
        for mk in ("US", "KR"):
            try:
                stat[mk] = con.execute(
                    f'SELECT COUNT(*), COUNT(DISTINCT "{c}") FROM audit_candidate_rows '
                    f'WHERE market=? AND session_date>=? AND "{c}" IS NOT NULL AND "{c}"!=""',
                    (mk, since),
                ).fetchone()
            except sqlite3.Error:
                stat[mk] = (0, 0)
        (un, uu), (kn, ku) = stat["US"], stat["KR"]
        if un < 300 and kn < 300:
            continue
        verdict = ""
        if un >= 300 and kn >= 300 and uu == 1 and ku > 1:
            verdict = "US만 단일값(placeholder 의심)"
        elif un >= 300 and kn >= 300 and ku == 1 and uu > 1:
            verdict = "KR만 단일값(placeholder 의심)"
        elif un >= 300 and kn >= 300 and uu == 1 and ku == 1:
            verdict = "양쪽 단일값"
        if verdict:
            findings.append((c, un, uu, kn, ku, verdict))
    print(f"  검사 {len(targets)}필드 → 의심 {len(findings)}건")
    for c, un, uu, kn, ku, v in findings:
        print(f"    {c:34s} US {un:6d}/{uu:<4d} KR {kn:6d}/{ku:<4d}  {v}")


def audit_evidence_chain(since: str, market: str) -> None:
    """축 2 — evidence 결측이 강등을 얼마나 유발했는지."""
    con = _con(AUDIT_DB)
    if not con:
        return
    rows = con.execute(
        "SELECT evidence_data_state, COUNT(*) FROM audit_candidate_rows "
        "WHERE market=? AND session_date>=? AND evidence_data_state IS NOT NULL AND evidence_data_state!='' "
        "GROUP BY 1 ORDER BY 2 DESC", (market, since)).fetchall()
    total = sum(n for _, n in rows) or 1
    print(f"  [{market}] evidence 상태: " +
          " · ".join(f"{k} {n}({n/total*100:.0f}%)" for k, n in rows))

    ceil = con.execute(
        "SELECT evidence_action_ceiling, COUNT(*) FROM audit_candidate_rows "
        "WHERE market=? AND session_date>=? AND evidence_action_ceiling IS NOT NULL AND evidence_action_ceiling!='' "
        "GROUP BY 1 ORDER BY 2 DESC", (market, since)).fetchall()
    demoted = sum(n for k, n in ceil if str(k).upper() in {"PROBE_READY", "WATCH"})
    print(f"  [{market}] action_ceiling: " + " · ".join(f"{k} {n}" for k, n in ceil))
    print(f"           → 강등(PROBE_READY/WATCH) {demoted}건")

    counter: Counter = Counter()
    n_rows = 0
    for (js,) in con.execute(
        "SELECT evidence_missing_fields_json FROM audit_candidate_rows "
        "WHERE market=? AND session_date>=? AND evidence_missing_fields_json "
        "IS NOT NULL AND evidence_missing_fields_json NOT IN ('', '[]')", (market, since)):
        try:
            arr = json.loads(js)
        except (TypeError, ValueError):
            continue
        n_rows += 1
        for f in arr:
            counter[str(f)] += 1
    if n_rows:
        print(f"  [{market}] 결측 있는 행 {n_rows}건 — 주범:")
        for k, v in counter.most_common(6):
            print(f"           {k:28s} {v:6d}건 ({v/n_rows*100:5.1f}%)")


def audit_propagation(since: str, market: str) -> None:
    """축 3 — judge 판정이 진입까지 살아남는 비율(단계별 감쇠)."""
    con = _con(AUDIT_DB)
    if not con:
        return
    stages = [
        ("후보 행", "SELECT COUNT(*) FROM audit_candidate_rows WHERE market=? AND session_date>=?"),
        ("프롬프트 진입", "SELECT COUNT(*) FROM audit_candidate_rows WHERE market=? AND session_date>=? AND in_prompt=1"),
        ("claude trade_ready", "SELECT COUNT(*) FROM audit_candidate_rows WHERE market=? AND session_date>=? AND claude_trade_ready=1"),
        ("route=PlanA", "SELECT COUNT(*) FROM audit_candidate_rows WHERE market=? AND session_date>=? AND route_route LIKE 'PlanA%'"),
        ("filled", "SELECT COUNT(*) FROM audit_candidate_rows WHERE market=? AND session_date>=? AND filled_count>0"),
    ]
    print(f"  [{market}] 단계별 잔존")
    prev = None
    for label, q in stages:
        try:
            n = con.execute(q, (market, since)).fetchone()[0]
        except sqlite3.Error:
            continue
        rate = f"{n/prev*100:5.1f}%" if prev else "  100%"
        drop = " ★급감" if prev and prev >= 50 and n / prev < 0.05 else ""
        print(f"           {label:20s} {n:7d}  잔존 {rate}{drop}")
        prev = n if n else prev


def audit_ledger_gap() -> None:
    """축 4 — 원장 간 지표 전파 갭."""
    def cols(path: Path, tbl: str) -> set:
        c = _con(path)
        if not c:
            return set()
        try:
            return {r[1] for r in c.execute(f"PRAGMA table_info({tbl})")}
        except sqlite3.Error:
            return set()
    sel = cols(SEL_DB, "ticker_selection_log")
    aud = cols(AUDIT_DB, "audit_candidate_rows")
    skip = ("_at", "_id", "id", "key", "json", "name", "reason", "hash",
            "version", "mode", "date", "ticker", "market", "status")
    def sig(s):
        return {c for c in s if not any(k in c.lower() for k in skip)}
    only_sel = sorted(sig(sel) - sig(aud))
    # 사후 라벨(forward/runup)은 audit에 없는 게 정상이다.
    label_kw = ("forward_", "max_runup", "max_drawdown", "traded", "signal_fired",
                "execution_", "trade_ready", "selection_rank", "watchlist_rank")
    real = [c for c in only_sel if not any(k in c for k in label_kw)]
    print(f"  selection_log에만 있는 지표 {len(only_sel)}개 (사후 라벨 제외 시 {len(real)}개)")
    for c in real:
        print(f"    {c}")


def main() -> int:
    ap = argparse.ArgumentParser(description="파이프라인 무결성 감사")
    ap.add_argument("--since", default="2026-07-01")
    ap.add_argument("--market", default="both", choices=["US", "KR", "both"])
    args = ap.parse_args()
    markets = ["US", "KR"] if args.market == "both" else [args.market]

    print(f"=== 파이프라인 무결성 감사 (since {args.since}) ===\n")
    print("[축1] 필드 커버리지 — 한쪽 시장만 죽은 필드가 가장 위험")
    audit_field_coverage(args.since)
    print("\n[축2] evidence 사슬 — 결측이 강등을 유발하는 경로")
    for mk in markets:
        audit_evidence_chain(args.since, mk)
    print("\n[축3] 판정 전파율 — 어느 단계에서 급감하는가")
    for mk in markets:
        audit_propagation(args.since, mk)
    print("\n[축4] 원장 간 전파 갭")
    audit_ledger_gap()
    print("\n판정: '★급감'과 'placeholder 의심'이 나온 지점이 누수 후보다.")
    print("      코드가 있어도 데이터가 조건을 못 채우면 실전에서만 드러난다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
