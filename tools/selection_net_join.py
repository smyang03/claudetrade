#!/usr/bin/env python3
"""selection↔net 조인 복구 (read-only) — v2_decision_id 직결.

목적(핸드오프 §5-A DO#3): "selection 근거↔실현 net 조인 살리기".
기존 `v2_decision_fill_links`는 MATCHED 1.4%(12/852)뿐이고 AMBIGUOUS 205건이 떠 있었다.

진단(이 도구로 확인): AMBIGUOUS 205 = **전원 claude_price(PathB)**. legacy `decisions`
테이블엔 BUY_SIGNAL/filled 행이 0건(전부 Path A 스크리너 SKIPPED/WATCH 스냅샷). 즉
PathB 진입엔 *원래 legacy Path A BUY 행이 없다*. SKIPPED에 강제 매칭하면 거짓 링크 날조다.
→ "1.4% 매칭률"은 PathB에 무의미한 Path A 링크를 분모로 잡은 잘못된 측정.

진짜 조인은 legacy decisions를 우회하고 **v2_decision_id로 직결**한다.
  - 실현 net: `data/ml/decisions.db` v2_learning_performance (v2_decision_id 키, closed=1).
  - selection 근거: `data/v2_event_store.db` lifecycle_events (decision_id 키)
      · CLAUDE_TRADE_READY  → ready 플래그(patha_trade_ready / not_patha_trade_ready),
                              strategy_hint, ticker_origin.reason, timing_style.
      · CLAUDE_PRICE_PLAN_CREATED → plan.confidence, context regime 버킷.

이 조인은 PathB에서 사실상 100% 회수된다(아래 커버리지 출력). 라이브 행동을 바꾸지 않는다
(쓰기/주문/네트워크 없음). 다운스트림(capture·출혈버킷·A4 가드)이 selection 근거로 net을
가를 수 있게 하는 측정 배관이다.
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"
EVENT_DB = ROOT / "data" / "v2_event_store.db"

FEE_PCT = {"US": 0.5, "KR": 0.5}
FX_SPREAD_PCT = {"US": 0.2, "KR": 0.0}


def _net_of(market, pnl, pnl_net, basis) -> float | None:
    mkt = str(market or "").upper()
    basis = str(basis or "")
    if pnl_net is not None and basis in ("measured", "backfilled_exact", "backfilled_fee_only"):
        net = float(pnl_net)
        if basis == "backfilled_fee_only":
            net -= FX_SPREAD_PCT.get(mkt, 0.0)
        return net
    if pnl is None:
        return None
    return float(pnl) - FEE_PCT.get(mkt, 0.5) - FX_SPREAD_PCT.get(mkt, 0.0)


def _agg(nets: list[float]) -> str:
    if not nets:
        return "N=0"
    n = len(nets)
    w = sum(1 for x in nets if x > 0)
    num = sum(x for x in nets if x > 0)
    den = -sum(x for x in nets if x < 0)
    pf = (num / den) if den > 0 else float("inf")
    return f"N={n} win={w/n*100:.0f}% net_avg={sum(nets)/n:+.3f}% net_sum={sum(nets):+.1f}% PF={pf:.2f}"


def _load_net(con: sqlite3.Connection) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in con.execute(
        """SELECT v2_decision_id, market, session_date, strategy, path_type,
                  market_regime, pnl_pct, pnl_pct_net, net_basis
           FROM v2_learning_performance
           WHERE closed=1 AND runtime_mode='live'"""
    ):
        vid, market, sd, strat, ptype, regime, pnl, pnl_net, basis = r
        net = _net_of(market, pnl, pnl_net, basis)
        if net is None:
            continue
        out[vid] = {
            "market": str(market or "").upper(),
            "session_date": str(sd or "")[:10],
            "strategy": strat or "",
            "path_type": ptype or "",
            "regime": regime or "(미상)",
            "net": net,
        }
    return out


def _load_selection(con: sqlite3.Connection) -> dict[str, dict]:
    """decision_id -> selection 근거 (ready proxy·strategy_hint·confidence·regime).

    ready proxy: 이벤트 스토어 ticker_origin 기준. patha_trade_ready=True는 어떤 이벤트에도
    기록돼 있지 않으므로(전부 None/False), improvement_net_monitor와 동일하게
    "not_patha_trade_ready=0(미표기) ≈ ready=1"로 본다. 한 decision_id에 스냅샷이 여럿이라
    하나라도 not_patha_trade_ready=True면 ready=0(보수적). 정밀 ready는 진입시점 스냅샷 배선 필요.
    """
    sel: dict[str, dict] = collections.defaultdict(dict)
    not_ready: dict[str, bool] = collections.defaultdict(bool)
    for did, payload in con.execute(
        "SELECT decision_id, payload_json FROM lifecycle_events WHERE event_type='CLAUDE_TRADE_READY'"
    ):
        try:
            pl = json.loads(payload)
        except json.JSONDecodeError:
            continue
        origin = pl.get("ticker_origin") or {}
        if origin.get("not_patha_trade_ready"):
            not_ready[did] = True
        entry = sel[did]
        entry.setdefault("origin_reason", origin.get("reason") or "")
        entry.setdefault("strategy_hint", pl.get("strategy_hint") or "")
        entry.setdefault("timing_style", pl.get("timing_style") or "")
    for did, entry in sel.items():
        entry["ready"] = not not_ready.get(did, False)
    for did, payload in con.execute(
        "SELECT decision_id, payload_json FROM lifecycle_events WHERE event_type='CLAUDE_PRICE_PLAN_CREATED'"
    ):
        try:
            pl = json.loads(payload)
        except json.JSONDecodeError:
            continue
        plan = pl.get("plan") or {}
        conf = plan.get("confidence")
        ctx = (plan.get("context_components_at_creation") or {})
        sel[did].setdefault("confidence", conf)
        sel[did].setdefault("plan_regime", ctx.get("consensus_mode") or ctx.get("risk_mode") or "")
    return sel


def _conf_bucket(c) -> str:
    if c is None:
        return "conf:(없음)"
    try:
        c = float(c)
    except (TypeError, ValueError):
        return "conf:(없음)"
    if c < 0.5:
        return "conf:<0.5"
    if c < 0.6:
        return "conf:0.5-0.6"
    if c < 0.7:
        return "conf:0.6-0.7"
    return "conf:>=0.7"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ml-db", default=str(ML_DB))
    ap.add_argument("--event-db", default=str(EVENT_DB))
    ap.add_argument("--dump", help="조인 결과 JSONL 경로(선택). 미지정시 출력만.")
    args = ap.parse_args()

    mlc = sqlite3.connect(f"file:{args.ml_db}?mode=ro", uri=True)
    evc = sqlite3.connect(f"file:{args.event_db}?mode=ro", uri=True)
    print(f"net DB: {args.ml_db}\nselection DB: {args.event_db}\n(read-only)\n")

    net = _load_net(mlc)
    sel = _load_selection(evc)

    # 조인
    joined = []
    no_sel = 0
    for vid, nrow in net.items():
        srow = sel.get(vid)
        if srow is None:
            no_sel += 1
            continue
        joined.append({"v2_decision_id": vid, **nrow, **srow})

    total = len(net)
    print("## 커버리지 (구 v2_decision_fill_links MATCHED 1.4% 대체)")
    print(f"  실현 net(closed,live) 캐노니컬: {total}")
    print(f"  selection 근거 조인 성공: {len(joined)}/{total} ({len(joined)/total*100:.1f}%)")
    print(f"  selection 이벤트 없음: {no_sel}\n")

    pathb = [j for j in joined if (j["path_type"] or j["strategy"]) == "claude_price" or j["strategy"] == "claude_price"]
    print(f"## PathB(claude_price) selection↔net — N={len(pathb)}")
    for mkt in ("KR", "US"):
        nets = [j["net"] for j in pathb if j["market"] == mkt]
        print(f"  [{mkt}] 전체 {_agg(nets)}")
        # ready proxy별 (event-store ticker_origin; not_patha_trade_ready 미표기 ≈ ready=1)
        for flag, lab in ((True, "ready~1"), (False, "ready~0")):
            ns = [j["net"] for j in pathb if j["market"] == mkt and j["ready"] == flag]
            print(f"    {lab:8s} {_agg(ns)}")
    print()

    print("## confidence 버킷별 net (PathB)")
    by_conf: dict[str, list[float]] = collections.defaultdict(list)
    for j in pathb:
        by_conf[_conf_bucket(j.get("confidence"))].append(j["net"])
    for k in sorted(by_conf):
        print(f"  {k:14s} {_agg(by_conf[k])}")
    print()

    print("## 국면(market_regime)별 net (PathB) — 출혈버킷 분석 입력")
    by_reg: dict[str, list[float]] = collections.defaultdict(list)
    for j in pathb:
        by_reg[str(j["regime"])].append(j["net"])
    for k in sorted(by_reg, key=lambda x: sum(by_reg[x])):
        print(f"  {k:16s} {_agg(by_reg[k])}")
    print()

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as f:
            for j in joined:
                f.write(json.dumps(j, ensure_ascii=False) + "\n")
        print(f"조인 결과 {len(joined)}건 → {args.dump}")

    mlc.close()
    evc.close()


if __name__ == "__main__":
    main()
