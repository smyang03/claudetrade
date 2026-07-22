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

★ 반사실 보고 규칙 (2026-07-23 계약):
  어떤 수정이 "N건을 회복시킨다"고 주장하려면, 수정 전/후 규칙을 **같은 실데이터 전량에
  돌려 delta를 낸 뒤** 보고한다. 단위 테스트 통과나 케이스 주입만으로 효과를 주장하지 않는다.
  실제로 P0-1(data_quality 강등 면제)은 케이스 주입에서 7건을 고치는 것처럼 보였으나
  85,889행 A/B에서 변화 0건이었다 — 라벨 부재가 원인이 아니라 공변량이었기 때문이다.

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


# ---------------------------------------------------------------- 축 5·6 (2026-07-23 추가)

# 각 필드가 어느 소비처에서 실제로 읽히는지. 출처는 코드 실측이다.
#   evidence  = runtime/live_evidence_pack.classify_live_evidence_state / build_live_evidence_pack
#   route     = runtime/action_routing.route_candidate_action (execution_context 경유)
FIELD_CONSUMERS = {
    "current_price":          ("evidence", "route"),
    "ret_3m_pct":             ("evidence",),
    "ret_5m_pct":             ("evidence",),
    "opening_range_break":    ("evidence",),
    "vwap_distance_pct":      ("evidence",),
    "volume_ratio_open":      ("evidence",),
    "momentum_state":         ("evidence",),
    "pullback_from_high_pct": ("evidence",),
    "spread_bps":             ("evidence",),      # fade_recovered_shadow(KR)에서만
    "time_normalized_rvol":   (),                 # 계산되지만 읽는 소비처가 없다
    "vwap":                   (),
    "opening_range_high":     (),
}


def audit_field_flow(since: str, sample: int = 20000) -> None:
    """축 5 — 필드의 [생성]→[전달]→[소비] 흐름.

    누수는 세 유형으로 갈린다. 이 셋을 구분해야 처방이 갈린다.
      빠짐   : 소비처가 읽는데 생성 자체가 없다
      안넘김 : 생성됐는데 다음 단계 컨텍스트로 전달되지 않는다
      안씀   : 전달까지 됐는데 아무도 읽지 않는다
    """
    con = _con(AUDIT_DB)
    if not con:
        return
    gen: Counter = Counter()
    ctx: Counter = Counter()
    n = 0
    for pof, pj in con.execute(
        "SELECT post_open_features_json, payload_json FROM audit_candidate_rows "
        "WHERE session_date>=? AND post_open_features_json NOT IN ('','{}','null') "
        f"LIMIT {int(sample)}", (since,)
    ):
        n += 1
        try:
            feats = json.loads(pof) or {}
        except (TypeError, ValueError):
            feats = {}
        try:
            gate = (json.loads(pj) or {}).get("runtime_gate") or {}
        except (TypeError, ValueError):
            gate = {}
        for key in FIELD_CONSUMERS:
            if feats.get(key) is not None:
                gen[key] += 1
            if isinstance(gate, dict) and gate.get(key) is not None:
                ctx[key] += 1

    print(f"  표본 {n}행 — [생성] post_open_features → [전달] runtime_gate ctx")
    print(f"    {'필드':24s} {'생성':>7s} {'전달':>7s} {'소비처':<18s} 판정")
    for key, consumers in FIELD_CONSUMERS.items():
        g, c = gen[key], ctx[key]
        who = ",".join(consumers) if consumers else "(없음)"
        verdict = ""
        if g == 0 and consumers:
            verdict = "★빠짐 — 소비처가 읽는데 생성 0"
        elif g >= 100 and c == 0 and "route" in consumers:
            verdict = "★안넘김 — 생성되나 route ctx 미전달"
        elif g >= 100 and c == 0 and not consumers:
            verdict = "★안씀 — 계산만 하고 소비처 없음"
        elif g >= 100 and not consumers:
            verdict = "안씀(전달은 됨)"
        elif g and c and c < g * 0.5:
            verdict = f"전달률 {c/g*100:.0f}% — 부분 누락"
        print(f"    {key:24s} {g:7d} {c:7d} {who:<18s} {verdict}")


def audit_placeholder_columns(since: str) -> None:
    """축 5b — 값은 채워져 있는데 고유값이 1개인 컬럼(= 실제 데이터가 아닌 placeholder)."""
    con = _con(AUDIT_DB)
    if not con:
        return
    targets = ["atr_pct", "volume_ratio", "from_high_pct", "candidate_quality_score",
               "trainer_prompt_score", "cohort_reliability", "entry_delay_min",
               "position_mfe_pct", "position_mae_pct", "us_early_entry_size_mult"]
    # ★ 컬럼 존재를 먼저 확인해야 한다. SQLite는 존재하지 않는 식별자를 큰따옴표로 감싸면
    #   문자열 리터럴로 해석한다 — "atr_pct"가 문자열이 되어 전 행 non-null·고유값 1로
    #   보이고, 없는 컬럼이 placeholder로 오진된다(2026-07-23에 실제로 겪음).
    existing = {r[1] for r in con.execute("PRAGMA table_info(audit_candidate_rows)")}
    print("\n  값은 있으나 고유값 1개 = placeholder 의심 / 컬럼 부재는 별도 표기")
    for c in targets:
        if c not in existing:
            print(f"    {c:26s} ★원장에 컬럼 자체가 없음")
            continue
        try:
            total, filled, uniq = con.execute(
                f"SELECT COUNT(*), SUM([{c}] IS NOT NULL AND [{c}]!=''), COUNT(DISTINCT [{c}]) "
                f"FROM audit_candidate_rows WHERE session_date>=?", (since,)).fetchone()
        except sqlite3.Error:
            continue
        filled = filled or 0
        if not total:
            continue
        mark = ""
        if filled >= 1000 and uniq <= 1:
            mark = "  ★placeholder"
        elif filled == 0:
            mark = "  ★미수집"
        print(f"    {c:26s} 값보유 {filled:6d}/{total:<6d} 고유값 {uniq:5d}{mark}")


def _pick_cases(since: str, per_bucket: int) -> list:
    """축 6 시드 — 다양한 종목 × 시나리오를 원장에서 뽑는다.

    한 종목이 시나리오를 독식하지 않도록 ticker당 1건으로 제한한다
    (평균의 오류: 종목 편중이 결론을 만든다).
    """
    con = _con(AUDIT_DB)
    if not con:
        return []
    scenarios = [
        ("S1 judge BUY_READY", "claude_action='BUY_READY'"),
        ("S2 judge PULLBACK_WAIT", "claude_action='PULLBACK_WAIT'"),
        ("S3 judge PROBE_READY", "claude_action='PROBE_READY'"),
        ("S4 ceiling 강등", "evidence_action_ceiling IN ('PROBE_READY','WATCH') AND in_prompt=1"),
        ("S5 route 차단", "route_final_action='WATCH' AND claude_action NOT IN ('WATCH','')"),
        ("S6 실제 체결", "filled_count>0"),
    ]
    out = []
    for label, where in scenarios:
        seen = set()
        rows = con.execute(
            "SELECT market, ticker, session_date, known_at, claude_action, "
            "post_open_features_json, payload_json, evidence_data_state, "
            "evidence_action_ceiling, route_final_action, route_route, filled_count "
            f"FROM audit_candidate_rows WHERE session_date>=? AND {where} "
            "AND post_open_features_json NOT IN ('','{}','null') "
            "ORDER BY session_date DESC LIMIT 4000", (since,)).fetchall()
        for r in rows:
            key = (r[0], r[1])
            if key in seen:
                continue
            seen.add(key)
            out.append((label, r))
            if len(seen) >= per_bucket:
                break
    return out


def audit_injection(since: str, per_bucket: int = 8) -> None:
    """축 6 — 실제 종목 데이터를 파이프라인에 직접 주입해 어디서 끊기는지 종목별로 지목.

    통계 집계로는 "몇 %가 죽는다"까지만 나온다. 종목 단위로 값을 넣어봐야
    "이 종목은 이 필드가 없어서 여기서 죽었고, 채우면 살아난다"가 나온다.
    """
    try:
        import sys as _sys
        if str(ROOT) not in _sys.path:
            _sys.path.insert(0, str(ROOT))
        from runtime.live_evidence_pack import build_live_evidence_pack
    except ImportError as exc:
        print(f"  evidence 모듈 임포트 실패: {exc}")
        return

    cases = _pick_cases(since, per_bucket)
    if not cases:
        print("  주입할 케이스 없음")
        return

    # ★ 확인 3필드만 보면 안 된다. evidence는 코어 모멘텀(ret_3m/ret_5m)도 함께 세고,
    #   그쪽이 비면 확인 필드를 다 채워도 partial에 남는다(DELL이 그 사례였다).
    confirm = ("opening_range_break", "vwap_distance_pct", "volume_ratio_open")
    core = ("ret_3m_pct", "ret_5m_pct", "current_price")
    tracked = confirm + core
    fillers = {"opening_range_break": False, "vwap_distance_pct": 0.0,
               "volume_ratio_open": 1.0, "ret_3m_pct": 0.0, "ret_5m_pct": 0.0,
               "current_price": 1.0}
    leak_kinds: Counter = Counter()

    print(f"  케이스 {len(cases)}건 (종목 중복 제외, 시나리오당 최대 {per_bucket}종목)")
    print("  ceiling은 원장/재생 둘 다 표시한다 — 어긋나면 그 자체가 전파 누수다.\n")
    print(f"    {'시나리오':20s} {'시장':4s} {'종목':9s} {'세션':11s} "
          f"{'원장ceil':11s} {'재생ceil':11s} {'결측필드':40s} 주입 반사실")
    last_label = None
    for label, r in cases:
        (mkt, tkr, sess, _known, c_act, pof, _pj, _st, ceil, _rfa, _rr, _fc) = r
        try:
            feats = json.loads(pof) or {}
        except (TypeError, ValueError):
            continue
        act = {"action": c_act or "WATCH"}
        pack = build_live_evidence_pack(market=mkt, ticker=tkr, features=feats, action=act)
        replayed = pack["action_ceiling"]
        missing = [f for f in tracked if feats.get(f) is None]

        cure = "-"
        if replayed != "BUY_READY":
            # ① 한 필드만 채워도 풀리는가 — 단일 병목 지목
            for f in missing:
                trial = build_live_evidence_pack(
                    market=mkt, ticker=tkr, features={**feats, f: fillers[f]}, action=act)
                if trial["action_ceiling"] == "BUY_READY":
                    cure = f"{f} 하나로 해소"
                    leak_kinds[f"단일필드 병목:{f}"] += 1
                    break
            else:
                if missing:
                    trial = build_live_evidence_pack(
                        market=mkt, ticker=tkr,
                        features={**feats, **{f: fillers[f] for f in missing}}, action=act)
                    if trial["action_ceiling"] == "BUY_READY":
                        cure = f"{len(missing)}필드 동시 필요({','.join(missing)})"
                        leak_kinds["복합 결측"] += 1
                    else:
                        # 필드를 다 채워도 안 풀리면 진짜 게이트를 지목한다(추측 금지).
                        # data_quality는 pack이 최종 판정한 값을 봐야 한다.
                        # features에 없으면 pack이 'unknown'으로 채우고 그게 곧 강등이다.
                        dq = str(trial.get("data_quality") or "")
                        raw_dq = feats.get("data_quality")
                        mom = str(feats.get("momentum_state") or "")
                        if trial.get("data_state") != "confirmed":
                            why = f"잔여 결측({trial.get('data_state')})"
                        elif mom == "fade":
                            why = "momentum_state=fade"
                        elif raw_dq is None and dq in {"unknown", "first_observed", "missing"}:
                            why = f"data_quality 미전달 → '{dq}' 대입되어 강등"
                        elif dq in {"first_observed", "unknown", "missing"}:
                            why = f"data_quality={dq} 게이트"
                        else:
                            why = f"미상(state=confirmed,dq={dq})"
                        cure = f"필드 무관 — {why}"
                        leak_kinds[f"필드 무관:{why}"] += 1
                else:
                    cure = "결측 없음 — 다른 단계에서 차단"
                    leak_kinds["evidence 통과(하류 차단)"] += 1

        # 이원화 점검: 같은 개념의 대체 필드가 실제로 존재하는가
        alt = ""
        if "volume_ratio_open" in missing and feats.get("time_normalized_rvol") is not None:
            alt = f"  ※rvol={feats['time_normalized_rvol']} 있으나 evidence 미인정"
            leak_kinds["필드 이원화(rvol 있으나 미사용)"] += 1

        diverge = "" if str(ceil or "") == replayed else "  ★원장≠재생"
        head = label if label != last_label else ""
        last_label = label
        print(f"    {head:20s} {mkt:4s} {tkr:9s} {sess:11s} "
              f"{str(ceil or '-'):11s} {replayed:11s} "
              f"{','.join(missing) or '(없음)':40s} {cure}{alt}{diverge}")

    print("\n  주입 결과 누수 유형 집계")
    for k, v in leak_kinds.most_common():
        print(f"    {k:40s} {v}건")


def audit_stream_liveness() -> None:
    """축 7 — funnel 스트림 생존.

    ★ 주의: '안 찍힘 = 누수'가 아니다. 조건부 로거는 이벤트가 없으면 안 찍히는 게 정상이다.
    그래서 상시/조건부를 표시하고, 조건부는 판정을 유보한다.
    """
    import re
    from datetime import datetime
    fdir = ROOT / "logs" / "funnel"
    if not fdir.exists():
        return
    # 코드 실측 기반 분류. 조건부는 "그 사건이 없으면 0건이 정상"이다.
    KIND = {
        "candidate_funnel_snapshot": "상시", "post_open_feature_snapshot": "상시",
        "gate_evaluation": "상시", "candidate_cycle_latency": "상시",
        "selection_intraday_evidence_coverage": "상시", "action_routing_shadow": "상시",
        "single_symbol_judge": "조건부(judge 호출 시)",
        "exit_lifecycle_decision": "조건부(청산후보·장중리뷰 발생 시, 쿨다운 중복억제)",
        "auto_sell_review_force_sell_bypass": "조건부(강제매도 임계 돌파 시)",
        "system_sell_bypass": "조건부(EXIT_LIFECYCLE_ALLOWLIST_LIVE off면 영구 0건)",
        "hold_advisor_cache_hard_guard_bypass": "조건부(hard_guard+review_all 동시)",
        "session_evidence_degraded": "조건부(엣지 트리거·장애 시에만)",
        "kr_plan_a_no_signal_pathb_shadow": "조건부(KR 전용)",
        "tail_capture": "조건부(TAIL_CAPTURE_MODE 기본 off면 영구 0건)",
        "fast_fill": "조건부(매수 미체결 데드존 진입 시)",
    }
    streams: dict = {}
    for name in fdir.iterdir():
        m = re.match(r"^(.*?)_(\d{8})_(KR|US)\.jsonl$", name.name)
        if not m:
            m2 = re.match(r"^(.*?)_(\d{4}-\d{2}-\d{2})(_(KR|US))?\.(jsonl|json)$", name.name)
            if not m2:
                continue
            stream, day = m2.group(1), m2.group(2).replace("-", "")
        else:
            stream, day = m.group(1), m.group(2)
        streams.setdefault(stream, []).append(day)

    print("  ★ '안 찍힘 = 누수' 아님. 조건부 로거는 사건이 없으면 0건이 정상이다.")
    print(f"    {'스트림':42s} {'마지막':10s} {'경과':>5s}  분류")
    today = datetime(2026, 7, 23)
    for stream, days in sorted(streams.items(), key=lambda kv: max(kv[1])):
        last = max(days)
        try:
            age = (today - datetime.strptime(last, "%Y%m%d")).days
        except ValueError:
            continue
        kind = KIND.get(stream, "미분류")
        mark = ""
        if kind == "상시" and age > 3:
            mark = "  ★상시인데 끊김"
        print(f"    {stream:42s} {last:10s} {age:4d}일  {kind}{mark}")


def main() -> int:
    ap = argparse.ArgumentParser(description="파이프라인 무결성 감사")
    ap.add_argument("--since", default="2026-07-01")
    ap.add_argument("--market", default="both", choices=["US", "KR", "both"])
    ap.add_argument("--cases", type=int, default=8, help="축6 시나리오당 종목 수")
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

    print("\n[축5] 필드 흐름 — [생성]→[전달]→[소비] 중 어디서 끊기는가")
    audit_field_flow(args.since)
    audit_placeholder_columns(args.since)

    print("\n[축6] 종목별 주입 검증 — 실제 데이터를 넣어 어디서 끊기는지 지목")
    audit_injection(args.since, per_bucket=args.cases)

    print("\n[축7] funnel 스트림 생존")
    audit_stream_liveness()
    print("\n판정: '★급감'과 'placeholder 의심'이 나온 지점이 누수 후보다.")
    print("      코드가 있어도 데이터가 조건을 못 채우면 실전에서만 드러난다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
