from __future__ import annotations

"""전면 파이프라인 시뮬레이션 — 대량 케이스를 9단계에 통과시켜 어디서 몇 %가 죽는지 뽑는다.

왜 이 방식인가:
  2026-07-22 하루에 누수 3건이 나왔는데 전부 실전에서만 드러났다. 코드 grep으로는
  "배선이 있다"로 보이고, 한 건씩 파고들어야 겨우 잡혔다. 그 방식으로는 다음 누수도
  돈을 잃고 나서야 보인다.

무엇을 하는가:
  원장(audit_candidate_rows)에는 evidence 계산의 실제 입력(post_open_features_json)과
  route 계산의 실제 execution_context(payload_json.runtime_gate)가 남아 있다. 그래서
  라이브 판정을 **API 호출 없이 순수 함수로 재생**할 수 있다.

    build_live_evidence_pack()   ← post_open_features_json
    route_candidate_action()     ← payload_json.runtime_gate + claude_action

  재생값을 원장 기록값과 대조해 하네스 자체의 충실도를 먼저 증명하고(--fidelity),
  그 다음 반사실(결측 필드를 하나씩 채우면 몇 건이 살아나는가)을 돌린다.

주의(2026-07-22에 배운 것):
  - 세션 단위로 볼 것. 거래 단위 개선이 세션 단위에서 소멸한 사례가 13건이다.
  - 평균 뒤 분포를 볼 것.
  - 전부 읽기 전용. 라이브 상태·주문에 영향을 주지 않는다.

  python tools/pipeline_simulation.py --since 2026-07-01
  python tools/pipeline_simulation.py --since 2026-07-01 --fidelity
"""

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.live_evidence_pack import build_live_evidence_pack  # noqa: E402
from runtime.action_routing import route_candidate_action  # noqa: E402

AUDIT_DB = ROOT / "data" / "audit" / "candidate_audit.db"

# judge가 "무언가 하자"고 낸 액션. WATCH/AVOID/EXPIRED는 판정 자체가 관망이다.
ACTIONABLE = {"BUY_READY", "PROBE_READY", "ADD_READY", "PULLBACK_WAIT"}
# evidence가 확인 축으로 요구하는 필드(live_evidence_pack.CONFIRMATION_FIELDS와 동일)
CONFIRM_FIELDS = ("opening_range_break", "vwap_distance_pct", "volume_ratio_open")


def _con() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{AUDIT_DB}?mode=ro", uri=True, timeout=60)
    con.execute("PRAGMA busy_timeout=50000")
    con.row_factory = sqlite3.Row
    return con


def _jloads(text) -> dict | list | None:
    if not text or text in ("", "{}", "null", "[]"):
        return None
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def load_rows(since: str, market: str | None, limit: int | None) -> list[sqlite3.Row]:
    con = _con()
    q = (
        "SELECT market, ticker, session_date, known_at, in_prompt, prompt_excluded_reason, "
        "claude_action, claude_trade_ready, post_open_features_json, payload_json, "
        "evidence_data_state, evidence_action_ceiling, evidence_missing_fields_json, "
        "route_final_action, route_route, route_runtime_gate_reason, route_original_action, "
        "no_submit_reason_code, filled_count, entry_price, exit_price, exit_reason, pnl_pct, "
        "data_quality "
        "FROM audit_candidate_rows WHERE session_date>=?"
    )
    args: list = [since]
    if market:
        q += " AND market=?"
        args.append(market)
    if limit:
        q += f" LIMIT {int(limit)}"
    return con.execute(q, args).fetchall()


# ---------------------------------------------------------------- 재생 (replay)

def replay_evidence(row: sqlite3.Row, overrides: dict | None = None) -> dict | None:
    """evidence pack 재생. overrides로 결측 필드를 채워 반사실을 만든다."""
    feats = _jloads(row["post_open_features_json"])
    if not isinstance(feats, dict):
        return None
    if overrides:
        feats = {**feats, **overrides}
        # 결측 해소 반사실에서는 fail-closed 단락도 함께 풀어야 효과가 보인다.
        if feats.get("data_quality") == "minute_missing":
            feats["data_quality"] = "minute_partial"
        feats.pop("fail_closed", None)
    return build_live_evidence_pack(
        market=str(row["market"]),
        ticker=str(row["ticker"]),
        features=feats,
        action={"action": row["claude_action"] or "WATCH"},
    )


def replay_route(row: sqlite3.Row, ceiling_override: str | None = None) -> dict | None:
    """route 재생. payload_json.runtime_gate가 그 시점의 실제 execution_context다."""
    payload = _jloads(row["payload_json"])
    if not isinstance(payload, dict):
        return None
    ctx = payload.get("runtime_gate")
    if not isinstance(ctx, dict) or not ctx:
        return None
    ctx = dict(ctx)
    if ceiling_override:
        ctx["evidence_action_ceiling"] = ceiling_override
        if ceiling_override == "BUY_READY":
            ctx["evidence_data_state"] = "confirmed"
    action = {
        "ticker": row["ticker"],
        "action": row["claude_action"] or "WATCH",
        "confidence": ctx.get("confidence") or payload.get("confidence") or 0.0,
        "current_price": ctx.get("current_price"),
        "max_entry_price": ctx.get("entry_price_cap") or 0.0,
    }
    try:
        dec = route_candidate_action(
            action,
            market=str(row["market"]),
            data_quality=str(ctx.get("data_quality") or "missing"),
            pathb_waiting=bool(ctx.get("pathb_waiting")),
            overextended=bool(ctx.get("overextended")),
            execution_context=ctx,
        )
    except Exception:  # 재생 실패는 통계에서 분리한다(가정하지 않는다)
        return None
    return {"final_action": dec.final_action, "route": dec.route,
            "reason": dec.reason, "gate": dec.runtime_gate_reason}


# ---------------------------------------------------------------- 축 1: 충실도

def report_fidelity(rows: list[sqlite3.Row]) -> None:
    """재생값이 원장 기록값을 얼마나 재현하는가. 이게 낮으면 이후 결론은 전부 무효다."""
    ev = Counter()
    rt = Counter()
    rt_mismatch: Counter = Counter()
    for r in rows:
        if r["evidence_data_state"]:
            pack = replay_evidence(r)
            if pack is None:
                ev["재생불가"] += 1
            else:
                ev["state_" + ("일치" if pack["data_state"] == r["evidence_data_state"] else "불일치")] += 1
                ev["ceil_" + ("일치" if pack["action_ceiling"] == r["evidence_action_ceiling"] else "불일치")] += 1
        if r["route_final_action"]:
            rep = replay_route(r)
            if rep is None:
                rt["재생불가"] += 1
            else:
                hit = rep["final_action"] == r["route_final_action"]
                rt["action_" + ("일치" if hit else "불일치")] += 1
                rt["route_" + ("일치" if (rep["route"] or "") == (r["route_route"] or "") else "불일치")] += 1
                if not hit:
                    rt_mismatch[f'{r["route_final_action"]}→{rep["final_action"]}'] += 1

    def pct(c: Counter, hit: str, miss: str) -> str:
        h, m = c[hit], c[miss]
        return f"{h}/{h+m} ({h/(h+m)*100:.2f}%)" if h + m else "n/a"

    print("[축0] 하네스 충실도 — 재생 vs 원장 기록")
    print(f"  evidence data_state    {pct(ev,'state_일치','state_불일치')}")
    print(f"  evidence action_ceiling {pct(ev,'ceil_일치','ceil_불일치')}   재생불가 {ev['재생불가']}")
    print(f"  route final_action     {pct(rt,'action_일치','action_불일치')}")
    print(f"  route 문자열           {pct(rt,'route_일치','route_불일치')}   재생불가 {rt['재생불가']}")
    if rt_mismatch:
        print("  route 불일치 유형: " + " · ".join(f"{k} {v}" for k, v in rt_mismatch.most_common(6)))


# ---------------------------------------------------------------- 축 2: 9단계 매트릭스

def stage_matrix(rows: list[sqlite3.Row], market: str) -> None:
    """단계별 생존/차단. 차단은 사유코드까지 집계한다."""
    sub = [r for r in rows if r["market"] == market]
    if not sub:
        return
    stages = ["1.후보생성", "2.프롬프트", "3.judge액션", "4.evidence", "5.route",
              "6.진입배선", "7.안전게이트", "8.체결", "9.청산"]
    alive = {s: 0 for s in stages}
    blocked: dict[str, Counter] = {s: Counter() for s in stages}
    sessions_alive: dict[str, set] = {s: set() for s in stages}

    for r in sub:
        sd = r["session_date"]
        alive["1.후보생성"] += 1
        sessions_alive["1.후보생성"].add(sd)

        if not r["in_prompt"]:
            blocked["2.프롬프트"][r["prompt_excluded_reason"] or "(사유없음)"] += 1
            continue
        alive["2.프롬프트"] += 1
        sessions_alive["2.프롬프트"].add(sd)

        act = str(r["claude_action"] or "").upper()
        if act not in ACTIONABLE:
            blocked["3.judge액션"][act or "(무응답)"] += 1
            continue
        alive["3.judge액션"] += 1
        sessions_alive["3.judge액션"].add(sd)

        ceil = str(r["evidence_action_ceiling"] or "").upper()
        if ceil in {"WATCH", "WAIT_CONFIRMATION"}:
            blocked["4.evidence"][f"ceiling={ceil}/{r['evidence_data_state']}"] += 1
            continue
        if act == "BUY_READY" and ceil == "PROBE_READY":
            blocked["4.evidence"]["BUY_READY→PROBE_READY 강등"] += 1
            continue
        alive["4.evidence"] += 1
        sessions_alive["4.evidence"].add(sd)

        rroute = str(r["route_route"] or "")
        if not rroute or rroute in {"WATCH", ""}:
            blocked["5.route"][str(r["route_final_action"] or "(미기록)") + ":" +
                               str(r["route_runtime_gate_reason"] or "-")] += 1
            continue
        alive["5.route"] += 1
        sessions_alive["5.route"].add(sd)

        # 즉시매수 배선은 route 문자열 완전일치 + 원본 요청이 BUY_READY일 때만 발화한다.
        if rroute != "PlanA.buy":
            blocked["6.진입배선"][f"route={rroute} (PlanA.buy 아님)"] += 1
            continue
        if str(r["route_original_action"] or "").upper() not in {"BUY_READY", ""}:
            blocked["6.진입배선"][f"original={r['route_original_action']}"] += 1
            continue
        alive["6.진입배선"] += 1
        sessions_alive["6.진입배선"].add(sd)

        if r["no_submit_reason_code"]:
            blocked["7.안전게이트"][str(r["no_submit_reason_code"])] += 1
            continue
        alive["7.안전게이트"] += 1
        sessions_alive["7.안전게이트"].add(sd)

        if not (r["filled_count"] or 0):
            blocked["8.체결"]["미체결(사유 미기록)"] += 1
            continue
        alive["8.체결"] += 1
        sessions_alive["8.체결"].add(sd)

        if r["exit_price"] is None:
            blocked["9.청산"]["exit_price 결측"] += 1
            continue
        alive["9.청산"] += 1
        sessions_alive["9.청산"].add(sd)

    print(f"\n  [{market}] 단계별 생존 (n={len(sub)}, 세션 {len({r['session_date'] for r in sub})}개)")
    prev = None
    for s in stages:
        n = alive[s]
        rate = f"{n/prev*100:6.2f}%" if prev else "  100%"
        mark = " ★급감" if prev and prev >= 30 and n / max(prev, 1) < 0.05 else ""
        print(f"    {s:12s} {n:7d}  잔존 {rate}  세션 {len(sessions_alive[s]):2d}{mark}")
        prev = n if n else prev
    for s in stages:
        if blocked[s]:
            top = " · ".join(f"{k} {v}" for k, v in blocked[s].most_common(4))
            print(f"      └ {s} 차단: {top}")


# ---------------------------------------------------------------- 축 3: 반사실

def counterfactuals(rows: list[sqlite3.Row], market: str) -> None:
    """결측 필드를 하나씩 채우면 ceiling이 몇 건이나 풀리는가(H1/H2)."""
    sub = [r for r in rows
           if r["market"] == market
           and str(r["evidence_action_ceiling"] or "").upper() in {"PROBE_READY", "WATCH"}
           and r["post_open_features_json"]]
    if not sub:
        return
    print(f"\n  [{market}] 강등된 {len(sub)}건 대상 — 필드 하나만 채우면?")

    # 결측 빈도 먼저(분포 확인)
    miss = Counter()
    for r in sub:
        for f in (_jloads(r["evidence_missing_fields_json"]) or []):
            miss[str(f)] += 1
    print("    실제 결측 분포: " +
          " · ".join(f"{k} {v}({v/len(sub)*100:.0f}%)" for k, v in miss.most_common(5)))

    scenarios: dict[str, dict] = {
        "volume_ratio_open만 채움": {"volume_ratio_open": 1.0},
        "opening_range_break만 채움": {"opening_range_break": False},
        "vwap_distance_pct만 채움": {"vwap_distance_pct": 0.0},
        "확인 3필드 전부 채움": {"volume_ratio_open": 1.0, "opening_range_break": False,
                                 "vwap_distance_pct": 0.0},
    }
    base_recovered = 0
    for label, ov in scenarios.items():
        rec = 0
        for r in sub:
            pack = replay_evidence(r, overrides=ov)
            if pack and pack["action_ceiling"] == "BUY_READY":
                rec += 1
        if label.startswith("확인 3필드"):
            base_recovered = rec
        print(f"    {label:24s} → BUY_READY 회복 {rec:6d}건 ({rec/len(sub)*100:5.1f}%)")

    # H2 — time_normalized_rvol을 volume_ratio_open 대체로 인정하면(필드 이원화 통합)
    rvol_ok = rvol_recovered = 0
    for r in sub:
        feats = _jloads(r["post_open_features_json"]) or {}
        rv = feats.get("time_normalized_rvol")
        if rv is None:
            continue
        rvol_ok += 1
        pack = replay_evidence(r, overrides={"volume_ratio_open": rv})
        if pack and pack["action_ceiling"] == "BUY_READY":
            rvol_recovered += 1
    print(f"    [H2] time_normalized_rvol 보유 {rvol_ok}건 중 대체 인정 시 회복 {rvol_recovered}건")
    if base_recovered:
        print(f"         (참고: 3필드 전부 채움 상한 {base_recovered}건)")


def probe_wiring_counterfactual(rows: list[sqlite3.Row], market: str) -> None:
    """H3 — route가 PlanA.probe여도 즉시매수를 허용하면 몇 건이 추가로 살아나는가."""
    sub = [r for r in rows if r["market"] == market
           and str(r["claude_action"] or "").upper() == "BUY_READY"]
    if not sub:
        return
    routes = Counter(str(r["route_route"] or "(없음)") for r in sub)
    print(f"\n  [{market}] judge BUY_READY {len(sub)}건의 route 귀결: " +
          " · ".join(f"{k} {v}" for k, v in routes.most_common(6)))
    probe = [r for r in sub if str(r["route_route"] or "") == "PlanA.probe"]
    filled = sum(1 for r in sub if (r["filled_count"] or 0))
    print(f"    실제 체결 {filled}건 · PlanA.probe로 흘러 배선 미매칭 {len(probe)}건")

    # ceiling을 BUY_READY로 강제하면 route가 PlanA.buy로 바뀌는가(재생으로 확인)
    flipped = 0
    tried = 0
    for r in sub:
        if str(r["route_route"] or "") == "PlanA.buy":
            continue
        rep = replay_route(r, ceiling_override="BUY_READY")
        if rep is None:
            continue
        tried += 1
        if rep["route"] == "PlanA.buy":
            flipped += 1
    print(f"    ceiling을 BUY_READY로 강제 시 PlanA.buy 전환 {flipped}/{tried}건(재생 가능분)")


# ---------------------------------------------------------------- 축 4: 타이밍

def timing_analysis(rows: list[sqlite3.Row], market: str) -> None:
    """결측이 데이터 결함인가, 판정 시점이 이른 것인가.

    opening_range_break는 설계상 OR 창(KR 10분·US 15분)이 닫히기 전에는 존재할 수 없다.
    그런데도 결측 1위라면 이건 데이터 문제가 아니라 타이밍 문제다.
    """
    buckets = [(-1e9, 0, "개장 전"), (0, 15, "0~15분"), (15, 30, "15~30분"),
               (30, 60, "30~60분"), (60, 180, "1~3시간"), (180, 1e9, "3시간+")]
    agg: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        if r["market"] != market:
            continue
        feats = _jloads(r["post_open_features_json"])
        if not isinstance(feats, dict):
            continue
        el = feats.get("market_open_elapsed_min")
        if el is None:
            label = "elapsed 미기록"
        else:
            try:
                el = float(el)
            except (TypeError, ValueError):
                continue
            label = next(nm for lo, hi, nm in buckets if lo <= el < hi)
        state = str(r["evidence_data_state"] or "(미기록)")
        agg[label][state] += 1
        agg[label]["_n"] += 1
        for f in CONFIRM_FIELDS:
            if feats.get(f) is None:
                agg[label]["miss_" + f] += 1

    print(f"\n  [{market}] 판정 시점별 evidence 상태 — 결측이 타이밍 산물인지 확인")
    order = ["개장 전", "0~15분", "15~30분", "30~60분", "1~3시간", "3시간+", "elapsed 미기록"]
    for label in order:
        c = agg.get(label)
        if not c or not c["_n"]:
            continue
        n = c["_n"]
        conf = c["confirmed"] / n * 100
        orb = c["miss_opening_range_break"] / n * 100
        vol = c["miss_volume_ratio_open"] / n * 100
        vwap = c["miss_vwap_distance_pct"] / n * 100
        print(f"    {label:12s} n={n:6d}  confirmed {conf:5.1f}%   "
              f"결측 ORB {orb:5.1f}% · vol {vol:5.1f}% · vwap {vwap:5.1f}%")


def live_path_funnel(since: str) -> None:
    """라이브 진입 경로 — 2026-07-08 rule_direct 전환 이후의 실제 결정 경로.

    audit_candidate_rows의 claude_action/claude_trade_ready는 selection Claude의 것이고
    7/08 운영자 결정으로 퇴역했다. 지금 진입을 정하는 건 single_symbol_judge다.
    그 원장은 DB가 아니라 logs/funnel/single_symbol_judge_*.jsonl에 있다.
    """
    import glob
    since_c = since.replace("-", "")
    per: dict[tuple, Counter] = defaultdict(Counter)
    elapsed_agg: dict[tuple, Counter] = defaultdict(Counter)
    open_min = {"KR": 9 * 60, "US": 22 * 60 + 30}  # KST 기준 개장(7월/EDT)
    buckets = [(-1e9, 0, "개장 전"), (0, 10, "0~10분"), (10, 20, "10~20분"),
               (20, 40, "20~40분"), (40, 90, "40~90분"), (90, 1e9, "90분+")]

    for path in sorted(glob.glob(str(ROOT / "logs" / "funnel" / "single_symbol_judge_*.jsonl"))):
        base = Path(path).name.replace("single_symbol_judge_", "").replace(".jsonl", "")
        try:
            sess, mkt = base.rsplit("_", 1)
        except ValueError:
            continue
        if sess < since_c:
            continue
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                rec = _jloads(line)
                if not isinstance(rec, dict):
                    continue
                act = str(rec.get("action") or "?")
                per[mkt][act] += 1
                per[mkt]["_n"] += 1
                if rec.get("applied"):
                    per[mkt]["_applied"] += 1
                w = str(rec.get("written_at") or "")
                if len(w) >= 16 and "T" in w:
                    try:
                        el = int(w[11:13]) * 60 + int(w[14:16]) - open_min.get(mkt, 0)
                    except ValueError:
                        continue
                    if el < -200:
                        el += 1440  # 자정 넘김(US)
                    lab = next(nm for lo, hi, nm in buckets if lo <= el < hi)
                    elapsed_agg[(mkt, lab)][act] += 1
                    elapsed_agg[(mkt, lab)]["_n"] += 1

    print("[축5] 라이브 진입 경로 — single_symbol_judge (7/08 rule_direct 전환 이후의 실제 경로)")
    for mkt, c in sorted(per.items()):
        n = c["_n"]
        print(f"  [{mkt}] 총 호출 {n}건 · applied {c['_applied']}건")
        print("       판정: " + " · ".join(f"{k} {v}({v/n*100:.1f}%)"
                                          for k, v in c.most_common() if not k.startswith("_")))
    print("\n  예산이 언제 쓰이고 무엇을 낳는가(유효산출 = BUY_READY + PULLBACK_WAIT)")
    for mkt in ("US", "KR"):
        tot = sum(elapsed_agg[(mkt, l)]["_n"] for _, _, l in buckets if (mkt, l) in elapsed_agg)
        if not tot:
            continue
        print(f"    --- {mkt} (n={tot}) ---")
        for _, _, lab in buckets:
            c = elapsed_agg.get((mkt, lab))
            if not c:
                continue
            n = c["_n"]
            prod = c["BUY_READY"] + c["PULLBACK_WAIT"]
            print(f"      {lab:8s} 호출 {n:4d} (예산 {n/tot*100:4.1f}%)  "
                  f"BUY_READY {c['BUY_READY']:2d} · PULLBACK_WAIT {c['PULLBACK_WAIT']:3d} · "
                  f"WAIT_RECHECK {c['WAIT_RECHECK']:3d}  → 유효산출 {prod/n*100:5.1f}%")


def ledger_integrity(since: str) -> None:
    """원장 무결성 — 후보 원장의 체결·성과 축이 살아 있는가.

    이 축이 죽으면 "후보 → 체결" 귀속이 불가능해지고, 죽은 컬럼을 0으로 읽어
    "체결 0건"이라는 잘못된 진단이 나온다. 그래서 결론보다 먼저 확인한다.
    """
    con = _con()
    print("\n[축6] 원장 무결성 — 체결축이 살아 있는가")
    last = con.execute(
        "SELECT MAX(session_date) FROM audit_candidate_rows WHERE filled_count>0").fetchone()[0]
    n_recent = con.execute(
        "SELECT COUNT(*), SUM(filled_count>0), SUM(entry_price IS NOT NULL) "
        "FROM audit_candidate_rows WHERE session_date>=?", (since,)).fetchone()
    print(f"  audit_candidate_rows: filled_count>0 마지막 세션 = {last}")
    print(f"    since {since}: 행 {n_recent[0]} · filled>0 {n_recent[1] or 0} · entry_price {n_recent[2] or 0}")

    ev = ROOT / "data" / "v2_event_store.db"
    if ev.exists():
        c2 = sqlite3.connect(f"file:{ev}?mode=ro", uri=True, timeout=60)
        c2.execute("PRAGMA busy_timeout=50000")
        rows = c2.execute(
            "SELECT substr(created_at,1,7) m, event_type, COUNT(*) FROM lifecycle_events "
            "WHERE event_type IN ('FILLED','CLOSED','ORDER_SENT') AND created_at>='2026-05-01' "
            "GROUP BY 1,2 ORDER BY 1").fetchall()
        agg: dict[str, Counter] = defaultdict(Counter)
        for m, et, n in rows:
            agg[m][et] = n
        print("  대조군 lifecycle_events(실체결 원장):")
        for m in sorted(agg):
            c = agg[m]
            print(f"    {m}  ORDER_SENT {c['ORDER_SENT']:4d} · FILLED {c['FILLED']:4d} · CLOSED {c['CLOSED']:4d}")
        print("  → 두 원장이 어긋나면 후보 원장의 체결축은 신뢰할 수 없다.")


def judge_exposure(rows: list[sqlite3.Row], market: str) -> None:
    """judge가 본 후보의 evidence 상태 분포 — 프롬프트 노출 시점 품질."""
    sub = [r for r in rows if r["market"] == market and r["in_prompt"]]
    if not sub:
        return
    c = Counter(str(r["evidence_data_state"] or "(미기록)") for r in sub)
    n = len(sub)
    print(f"    프롬프트 노출 {n}건의 evidence: " +
          " · ".join(f"{k} {v}({v/n*100:.0f}%)" for k, v in c.most_common()))


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="전면 파이프라인 시뮬레이션")
    ap.add_argument("--since", default="2026-07-01")
    ap.add_argument("--market", default="both", choices=["US", "KR", "both"])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--fidelity", action="store_true", help="충실도 검증만 실행")
    args = ap.parse_args()

    markets = ["US", "KR"] if args.market == "both" else [args.market]
    rows = load_rows(args.since, None if args.market == "both" else args.market, args.limit)
    print(f"=== 전면 파이프라인 시뮬레이션 (since {args.since}, n={len(rows)}) ===")
    print(f"    재생 대상: evidence={sum(1 for r in rows if r['post_open_features_json'])} "
          f"route={sum(1 for r in rows if r['payload_json'])}\n")

    report_fidelity(rows)
    if args.fidelity:
        return 0

    print("\n[축1] 9단계 통과 매트릭스")
    for mk in markets:
        stage_matrix(rows, mk)
        judge_exposure(rows, mk)

    print("\n[축2] 반사실 — 결측 해소 시 회복량 (H1/H2)")
    for mk in markets:
        counterfactuals(rows, mk)

    print("\n[축3] 배선 반사실 — 강등≠금지 (H3)")
    for mk in markets:
        probe_wiring_counterfactual(rows, mk)

    print("\n[축4] 판정 시점 분석 — 결측이 데이터 결함인가 타이밍인가")
    for mk in markets:
        timing_analysis(rows, mk)

    print()
    live_path_funnel(args.since)
    ledger_integrity(args.since)

    print("\n판정: 잔존율이 급감한 단계와, 반사실 회복량이 큰 필드가 다음 작업 지점이다.")
    print("      단 [축1] 2~3단계는 7/08에 퇴역한 selection 경로다 — 라이브 판단은 [축5]로 본다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
