# -*- coding: utf-8 -*-
"""캐너리 신호 물질화 + 리허설 원장 (2026-09-09, 승인 경로 1단계 — 실주문 브리지 무변경).

매일 21:05 래퍼에서 실행. 캐너리 정책(config/canary_policy.json)의 우선순위 arm마다 **다음 세션**의 매수 후보 1종목을 계산해
  state/canary_signals_{KR,US}.json (profit_strategy_signals_v1 스키마, strategy_id="CANARY_<ARM>", weight=50,000/시장 cap)
을 쓴다. 2단계(첫 CANDIDATE_STRONG 후 별도 세션)에서 profit_strategy_order_bridge가 이 파일을 읽게 배선하면 승인은
`.env.live`+`v2_start_config.json`의 PROFIT_STRATEGY_ENABLED_IDS 한 줄 + 재시작이다. 그 전까지 브리지는 이 파일을 읽지 않는다.

리허설 원장 state/canary_rehearsal.jsonl: "오늘 캐너리가 켜져 있었다면 이 종목·이 수량" — 시가·정산은 `settle`이 일봉 CSV로 채운다.
산출물은 경로가 돌았다는 사실·수량·가격이다. 성적은 가상 북이 낸다(여기서 수익 보고 금지).
후보 계산: 마지막 완결 봉 기준 discovery_pools.featurize + event_features + arm filter → 전일 거래대금 큰순 1종목(K=1).
US는 탐색 체인이 "다음 봉"이 있어야 후보를 만드는 구조라(Codex P1) 여기서는 마지막 봉만으로 직접 계산한다.
사용: python tools/canary_materializer.py [materialize|settle|both]
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
POLICY = ROOT / "config" / "canary_policy.json"
GATE = ROOT / "state" / "forward_gate_state.json"
LEDGER = ROOT / "state" / "canary_rehearsal.jsonl"
ORDER_KRW = 50000.0
CAP_KRW = {"KR": 100000.0, "US": 300000.0}   # PROFIT_STRATEGY_MAX_ORDER_KRW_{KR,US} 현행값 — 낮추지 않고 weight로 5만원을 만든다
FX_USDKRW_FALLBACK = 1390.0


def _load(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _next_session(market: str, today: date) -> str:
    d = today + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def _last_bar_candidates(market: str) -> dict[str, list[dict]]:
    """마지막 완결 봉 기준 풀별 후보(특성·이벤트 결합 포함). {pool_id: [cand]}"""
    import discovery_pools as dp
    d = dp.US_DIR if market == "US" else dp.KR_DIR
    prefix = "us_" if market == "US" else "kr_"
    out: dict[str, list[dict]] = {}
    latest = ""
    for p in d.glob(f"{prefix}*.csv"):
        b = dp._load_bars(p)
        if len(b) < dp.MIN_HISTORY + 1:
            continue
        latest = max(latest, b[-1][0])
    if not latest:
        return out
    regime = dp._index_regime(market)
    for p in d.glob(f"{prefix}*.csv"):
        b = dp._load_bars(p)
        if len(b) < dp.MIN_HISTORY + 1 or b[-1][0] != latest:
            continue
        i = len(b) - 1
        if not dp.bar_complete(b[i][0], market):
            continue
        f = dp.featurize(b, i, market, dp.volume_context(b))
        if f is None:
            continue
        t = p.stem[len(prefix):]; f["ticker"] = t; f["signal_date"] = b[i][0]
        hits = dp.pool_pass(f, market)
        if f["dvol"] is not None and f["dvol"] >= (dp.US_DVOL_MIN_M if market == "US" else dp.KR_DVOL_MIN_EOK) and (market == "US" or f["price"] >= 1000):
            f.update(dp.event_features(t, b[i][0], market, [x[0] for x in b], i))
            hits = hits + dp.event_pool_pass(f, market)
        f["regime"] = {**regime.get(b[i][0], {}), "breadth_down_pct": None}
        for pid in hits:
            out.setdefault(pid, []).append(dict(f))
    return out


def materialize() -> dict:
    import virtual_books as vb
    pol = _load(POLICY, {}); verdicts = (_load(GATE, {}) or {}).get("verdicts", {})
    arms = {s["id"]: s for s in vb.STRATEGIES}
    today = date.today(); now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = {}
    for market in ("KR", "US"):
        cands = _last_bar_candidates(market)
        signals = []; planned = []
        for arm_id in pol.get("priority_queue") or []:
            s = arms.get(arm_id)
            if not s or s.get("universe") != ("xkr" if market == "KR" else "xus"):
                continue
            pool = cands.get(s.get("pool"), [])
            passers = [c for c in pool if vb.candidate_filter_pass(c, s.get("filter") or {})]
            if not passers:
                continue
            pick = max(passers, key=lambda c: c.get("dvol") or 0.0)   # K=1: 전일 거래대금 1위(캐너리는 arm당 1종목)
            sess = _next_session(market, today)
            price_krw = float(pick["price"]) * (FX_USDKRW_FALLBACK if market == "US" else 1.0)
            qty = int(ORDER_KRW // price_krw) if price_krw > 0 else 0
            sig = {"strategy_id": f"CANARY_{arm_id.upper()}", "source_strategy": f"canary_{arm_id}", "market": market, "ticker": pick["ticker"],
                   "entry_session_date": sess, "signal_date": pick["signal_date"], "known_at": now, "rank": 1, "priority": 1.0,
                   "weight": round(ORDER_KRW / CAP_KRW[market], 4), "hold_sessions": int(s.get("hold", vb.HOLD_SESSIONS)),
                   "tp_pct": float(s.get("tp", vb.TP)) / 100.0, "sl_pct": abs(float(s.get("sl", vb.SL))) / 100.0,
                   "gate_verdict": verdicts.get(arm_id), "canary_allowed": verdicts.get(arm_id) == "CANDIDATE_STRONG",
                   "signal_provider": "canary_materializer/discovery_pools(last_bar)", "execution_price_provider": "KIS_ONLY",
                   "pool_n": len(pool), "passers_n": len(passers), "prev_close": pick["price"], "qty_planned_at_prev_close": qty}
            signals.append(sig)
            planned.append({"kind": "planned", "session_date": sess, "market": market, "strategy_id": sig["strategy_id"], "arm": arm_id,
                            "ticker": pick["ticker"], "qty_planned": qty, "prev_close": pick["price"], "order_krw": ORDER_KRW,
                            "gate_verdict": verdicts.get(arm_id), "status": "REHEARSAL_PLANNED", "planned_at": now,
                            "hold_sessions": sig["hold_sessions"], "tp_pct": sig["tp_pct"], "sl_pct": sig["sl_pct"]})
        payload = {"schema_version": "profit_strategy_signals_v1", "authority": "SIGNAL_ONLY_NO_BROKER_AUTHORITY", "market": market,
                   "session_date": _next_session(market, today), "generated_at": now, "signals": signals, "errors": [],
                   "status": "healthy", "note": "캐너리 1단계 — 브리지 미배선. 승인 절차는 config/canary_policy.json approval_procedure"}
        out = ROOT / "state" / f"canary_signals_{market}.json"
        out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        have = {(r.get("session_date"), r.get("strategy_id")) for r in _jsonl(LEDGER)}
        with LEDGER.open("a", encoding="utf-8") as fh:
            for r in planned:
                if (r["session_date"], r["strategy_id"]) not in have:
                    fh.write(json.dumps(r, ensure_ascii=False) + "\n")
        result[market] = {"signals": len(signals), "tickers": [(x["strategy_id"], x["ticker"]) for x in signals]}
        print(f"[CANARY] {market} 다음 세션 {payload['session_date']} 신호 {len(signals)}: {result[market]['tickers']}")
    return result


def _jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def settle() -> int:
    """리허설 행에 세션 시가(진입 가정)·보유 만기 종가를 일봉 CSV로 채운다(수익 보고용 아님 — 경로 실측)."""
    rows = _jsonl(LEDGER); n = 0
    for r in rows:
        if r.get("kind") != "planned" or r.get("status") == "REHEARSAL_SETTLED":
            continue
        d = ROOT / "data" / "price" / ("us" if r["market"] == "US" else "kr") / f"{'us' if r['market']=='US' else 'kr'}_{r['ticker']}.csv"
        if not d.exists():
            continue
        bars = [x for x in csv.reader(d.open(encoding="utf-8-sig")) if x and x[0][:2] == "20"]
        dates = [x[0] for x in bars]
        if r["session_date"] not in dates:
            continue
        i = dates.index(r["session_date"]); o = float(bars[i][1])
        if r.get("entry_open") is None:
            r["entry_open"] = o; r["qty_at_open"] = int(r["order_krw"] // (o * (FX_USDKRW_FALLBACK if r["market"] == "US" else 1.0))) if o > 0 else 0
            r["status"] = "REHEARSAL_OPENED"; n += 1
        h = int(r.get("hold_sessions") or 7)
        if i + h < len(dates):
            r["exit_close_at_hold"] = float(bars[i + h][4]); r["status"] = "REHEARSAL_SETTLED"; n += 1
    if n:
        tmp = LEDGER.with_suffix(".tmp"); tmp.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in rows), encoding="utf-8"); tmp.replace(LEDGER)
    print(f"[CANARY] 리허설 정산 갱신 {n}")
    return n


def main() -> int:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "both"
    if cmd in ("materialize", "both"):
        materialize()
    if cmd in ("settle", "both"):
        settle()
    return 0


if __name__ == "__main__":
    sys.exit(main())
