#!/usr/bin/env python3
"""슬리피지 인스트루먼트 (read-only) — 발주가↔체결가 갭 측정.

목적(핸드오프 §5-A DO#2): loss_cap 오버슈트(패널 주장 −2%→−2.76%, −24%p 회수)가
*실행 슬리피지* 때문인지 결판낸다. 원자료는 이미 `data/v2_event_store.db`에 있고
코드로 조인만 한다. 라이브 행동을 바꾸지 않는다(쓰기/주문/네트워크 없음).

세 가격을 구분한다.
  (1) 트리거 임계가 = loss_cap/stop이 *발동해야 하는* 가격. 포지션별 동적(budget/entry_value)
      이라 이 도구는 추정하지 않는다. (감지/갭다운 지연 = (1)→(2) 갭은 별도 측정 영역.)
  (2) 발주가  = ORDER_SENT.payload.price (우리가 주문에 실어 보낸 native 가격).
  (3) 체결가  = 매수 FILLED.fill_price_native / 매도 CLOSED.price (실제 체결 native).

이 도구가 측정하는 것 = *실행 슬리피지* = (2)↔(3) 갭. adverse(+)=우리에게 불리.
  - 매수 adverse = (체결 − 발주)/발주  (더 비싸게 샀으면 +)
  - 매도 adverse = (발주 − 체결)/발주  (더 싸게 팔렸으면 +)

조인 키: ORDER_SENT.execution_id ↔ (FILLED|CLOSED).execution_id.
  - FILLED 중복 스켈레톤(fill_price_native NULL) 행은 버린다.
  - 한 execution_id에 후보가 여럿이면 ORDER_SENT 이후 가장 가까운 시각의 체결을 택한다
    (매수/매도가 같은 execution_id를 재사용하는 소수 케이스 분리).

결판 읽는 법:
  - 매도 실행 슬리피지가 작거나 유리(adverse≤0)인데 loss_cap 실현손실이 임계보다 크다
    → 오버슈트는 실행이 아니라 (1)→(2) 감지/갭 지연. "체결 주문 수정"으론 못 줄인다.
  - 매도 실행 슬리피지가 크게 불리(adverse≫0) → 발주 방식(슬리피지 캡·마켓폴백) 손질로 회수 가능.
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENT_DB = ROOT / "data" / "v2_event_store.db"


def _j(payload: str, key: str):
    try:
        return json.loads(payload).get(key)
    except (json.JSONDecodeError, AttributeError):
        return None


def _num(v):
    try:
        f = float(v)
        return f if f != 0 else None
    except (TypeError, ValueError):
        return None


def _parse_ts(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s))
    except (TypeError, ValueError):
        return None


def _pick_closest(sent_at: str, candidates: list[tuple[str, float]]) -> float | None:
    """ORDER_SENT 시각 이후(없으면 전체)에서 시각이 가장 가까운 체결가."""
    if not candidates:
        return None
    sent_ts = _parse_ts(sent_at)
    if sent_ts is None:
        return candidates[0][1]
    after = [c for c in candidates if (_parse_ts(c[0]) or sent_ts) >= sent_ts]
    pool = after or candidates
    pool = sorted(pool, key=lambda c: abs(((_parse_ts(c[0]) or sent_ts) - sent_ts).total_seconds()))
    return pool[0][1]


def _agg(vals: list[float]) -> str:
    if not vals:
        return "N=0"
    n = len(vals)
    s = sorted(vals)
    mean = sum(vals) / n
    median = s[n // 2]
    adverse = sum(1 for x in vals if x > 0)
    p90 = s[min(n - 1, int(n * 0.9))]
    return (
        f"N={n} mean={mean:+.3f}% median={median:+.3f}% "
        f"p90={p90:+.3f}% adverse>{0}={adverse}/{n}"
    )


def _load_fill_index(con: sqlite3.Connection) -> dict[str, list[tuple[str, float]]]:
    """매수 체결: execution_id -> [(occurred_at, fill_price_native)]"""
    idx: dict[str, list[tuple[str, float]]] = collections.defaultdict(list)
    for eid, occ, payload in con.execute(
        "SELECT execution_id, occurred_at, payload_json "
        "FROM lifecycle_events WHERE event_type='FILLED'"
    ):
        price = _num(_j(payload, "fill_price_native"))
        if eid and price is not None:
            idx[eid].append((occ, price))
    return idx


def _load_closed_index(con: sqlite3.Connection) -> dict[str, list[tuple[str, float]]]:
    """매도 체결: execution_id -> [(occurred_at, exit_price_native)]"""
    idx: dict[str, list[tuple[str, float]]] = collections.defaultdict(list)
    for eid, occ, payload in con.execute(
        "SELECT execution_id, occurred_at, payload_json "
        "FROM lifecycle_events WHERE event_type='CLOSED'"
    ):
        price = _num(_j(payload, "price"))
        if eid and price is not None:
            idx[eid].append((occ, price))
    return idx


def _collect(con: sqlite3.Connection):
    fills = _load_fill_index(con)
    closes = _load_closed_index(con)
    # market -> side -> close_reason -> [adverse_pct]
    buckets: dict = collections.defaultdict(lambda: collections.defaultdict(lambda: collections.defaultdict(list)))
    coverage = collections.Counter()
    for eid, occ, market, payload in con.execute(
        "SELECT execution_id, occurred_at, market, payload_json "
        "FROM lifecycle_events WHERE event_type='ORDER_SENT'"
    ):
        side = _j(payload, "side")
        sent = _num(_j(payload, "price"))
        cr = _j(payload, "close_reason") or ("ENTRY" if side == "buy" else "(미상)")
        mkt = str(market or "").upper()
        if side not in ("buy", "sell"):
            coverage[f"{mkt}:side_unknown"] += 1
            continue
        if sent is None:
            coverage[f"{mkt}:{side}:sent_price_없음"] += 1
            continue
        candidates = (fills if side == "buy" else closes).get(eid)
        fill = _pick_closest(occ, candidates) if candidates else None
        if fill is None:
            coverage[f"{mkt}:{side}:체결_미매칭"] += 1
            continue
        if side == "buy":
            adverse = (fill - sent) / sent * 100.0
        else:
            adverse = (sent - fill) / sent * 100.0
        buckets[mkt][side][cr].append(adverse)
        coverage[f"{mkt}:{side}:매칭"] += 1
    return buckets, coverage


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(EVENT_DB))
    args = ap.parse_args()
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    print(f"DB: {args.db} (read-only)")
    print("실행 슬리피지 = 발주가(ORDER_SENT.price) ↔ 체결가(매수 FILLED / 매도 CLOSED). adverse(+)=불리.\n")

    buckets, coverage = _collect(con)

    for mkt in ("KR", "US"):
        if mkt not in buckets:
            continue
        print(f"## [{mkt}]")
        for side in ("buy", "sell"):
            crs = buckets[mkt].get(side)
            if not crs:
                continue
            allv = [x for v in crs.values() for x in v]
            label = "매수 entry" if side == "buy" else "매도 exit"
            print(f"  {label} 전체: {_agg(allv)}")
            for cr in sorted(crs, key=lambda k: -len(crs[k])):
                print(f"    {cr:32s} {_agg(crs[cr])}")
        print()

    # loss_cap 오버슈트 결판 한 줄
    print("## 결판 — loss_cap 오버슈트가 실행 슬리피지인가?")
    lc = []
    for mkt in buckets:
        lc += buckets[mkt].get("sell", {}).get("CLOSED_LOSS_CAP", [])
    lc += []
    hs = []
    for mkt in buckets:
        for cr in ("CLOSED_HARD_STOP", "CLOSED_CLAUDE_PRICE_STOP"):
            hs += buckets[mkt].get("sell", {}).get(cr, [])
    if lc:
        mean_lc = sum(lc) / len(lc)
        verdict = "실행 아님(감지/갭 지연)" if mean_lc <= 0.10 else "실행 기여 가능"
        print(f"  loss_cap 매도 실행 슬리피지 mean={mean_lc:+.3f}% (N={len(lc)}) → {verdict}")
    if hs:
        print(f"  stop(hard+claude) 매도 실행 슬리피지 mean={sum(hs)/len(hs):+.3f}% (N={len(hs)})")
    print("  주: 발주가↔체결가 갭이 작거나 유리하면, 임계가→발주가 갭(감지/갭다운)이 오버슈트의 원인.")
    print("      그 갭은 이 인스트루먼트 범위 밖(트리거 임계가는 포지션별 동적이라 별도 배선 필요).\n")

    print("## 커버리지 (조인 결과)")
    for k in sorted(coverage):
        print(f"  {k}: {coverage[k]}")
    con.close()


if __name__ == "__main__":
    main()
