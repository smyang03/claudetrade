from __future__ import annotations

"""좌측꼬리 청산사유 귀인 — 큰 손실이 어느 청산 경로에서 나오나 (read-only).

left_tail_attribution이 "군집 vs 개별"을 가렸다면, 이 도구는 개별 좌측꼬리가 어느 청산
경로(자동손절 loss_cap/hard_stop vs 재량매도 vs 브로커 강제매도 vs 장마감)에서 나오는지를
lifecycle_events(v2_event_store)의 CLOSED 이벤트 close_reason + payload pnl_pct로 직접
집계한다. v2_canonical 우회 — 이벤트 자체에 reason과 pnl이 있어 매칭 모호성이 없다.

목적: "손절이 작동하는데도 손실이 큰가(폭/지연)" vs "손절 외 경로(재량·강제매도)가 문제인가"
를 구분해 처방(손절폭 조정 vs 경로 점검)을 가린다. 손절폭은 운영자 확인 필수 파라미터이므로
이 도구는 진단만 하고 변경하지 않는다. read-only.
"""

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EV_DB = ROOT / "data" / "v2_event_store.db"

AUTO_STOP = {"CLOSED_LOSS_CAP", "CLOSED_HARD_STOP", "CLOSED_STOP_LOSS", "CLOSED_TRAIL_STOP"}
DISCRETION = {"CLOSED_USER_MANUAL", "CLOSED_CLAUDE_SELL", "CLOSED_CLAUDE_INTRADAY_SELL"}
FORCED = {"CLOSED_AUDITED_BROKER_SELL"}


def bucket(rc: str | None) -> str:
    rc = rc or "?"
    if rc in AUTO_STOP:
        return "자동손절"
    if rc in DISCRETION:
        return "재량매도"
    if rc in FORCED:
        return "브로커강제매도"
    return "기타(" + rc.replace("CLOSED_", "") + ")"


@dataclass
class ReasonStat:
    reason_code: str
    n: int
    mean_pct: float
    median_pct: float
    worst_pct: float


@dataclass
class MarketTail:
    market: str
    closed_n: int
    tail_n: int
    threshold: float
    auto_stop_n: int
    auto_stop_mean: float | None
    auto_stop_deep_n: int      # 자동손절인데 -5% 초과 손실(폭/지연 의심)
    bucket_tail: dict[str, int]
    tail_reasons: list[ReasonStat]
    verdict: str

    def deep_label(self) -> str:
        if self.auto_stop_n == 0:
            return "-5% 초과 0"
        return f"-5% 초과 {self.auto_stop_deep_n}건({self.auto_stop_deep_n / self.auto_stop_n * 100:.0f}%)"


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)
    conn.execute("PRAGMA busy_timeout=12000")
    return conn


def load_closes(ev_db: Path, runtime_mode: str | None) -> dict[str, dict[str, list[float]]]:
    conn = _connect_ro(ev_db)
    try:
        rows = conn.execute(
            "SELECT market, runtime_mode, reason_code, payload_json "
            "FROM lifecycle_events WHERE event_type='CLOSED'"
        ).fetchall()
    finally:
        conn.close()
    data: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for market, mode, rc, pj in rows:
        if runtime_mode and mode != runtime_mode:
            continue
        try:
            p = json.loads(pj) if pj else {}
        except (json.JSONDecodeError, TypeError):
            continue
        pnl = p.get("pnl_pct")
        if pnl is None:
            continue
        data[str(market)][str(rc or "?")].append(float(pnl))
    return data


def analyze(market: str, rc_map: dict[str, list[float]], threshold: float, deep: float) -> MarketTail:
    allpnl = [x for v in rc_map.values() for x in v]
    tail_by_reason: dict[str, list[float]] = defaultdict(list)
    bucket_tail: dict[str, int] = defaultdict(int)
    for rc, pnls in rc_map.items():
        for x in pnls:
            if x <= threshold:
                tail_by_reason[rc].append(x)
                bucket_tail[bucket(rc)] += 1
    auto = [x for rc, v in rc_map.items() if rc in AUTO_STOP for x in v]
    auto_deep = [x for x in auto if x <= deep]
    tail_n = sum(len(v) for v in tail_by_reason.values())

    reasons = sorted(
        (ReasonStat(rc, len(v), round(mean(v), 2), round(median(v), 2), round(min(v), 2))
         for rc, v in tail_by_reason.items()),
        key=lambda s: s.n, reverse=True,
    )

    # 판정
    auto_tail = bucket_tail.get("자동손절", 0)
    nonauto_tail = tail_n - auto_tail
    if tail_n == 0:
        verdict = f"좌측꼬리(<={threshold}%) 없음"
    elif auto and len(auto_deep) / len(auto) >= 0.10:
        verdict = (f"자동손절이 {len(auto_deep)}/{len(auto)}건 {deep}% 초과까지 지연/폭 — "
                   "손절폭·트리거 점검 후보")
    elif nonauto_tail >= auto_tail:
        verdict = ("좌측꼬리가 자동손절보다 재량/강제/장마감 경로에서 다수 — "
                   "손절폭보다 해당 경로 점검")
    else:
        verdict = "좌측꼬리 대부분 자동손절 경로, 자동손절 전반 건강"

    return MarketTail(
        market=market, closed_n=len(allpnl), tail_n=tail_n, threshold=threshold,
        auto_stop_n=len(auto),
        auto_stop_mean=round(mean(auto), 2) if auto else None,
        auto_stop_deep_n=len(auto_deep),
        bucket_tail=dict(bucket_tail), tail_reasons=reasons, verdict=verdict,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="좌측꼬리 청산사유 귀인 (read-only)")
    ap.add_argument("--market", choices=["KR", "US", "both"], default="both")
    ap.add_argument("--threshold", type=float, default=-3.0, help="좌측꼬리 pnl%% 기준(기본 -3)")
    ap.add_argument("--deep", type=float, default=-5.0, help="자동손절 지연 의심 기준(기본 -5)")
    ap.add_argument("--runtime-mode", default="live", help="live/paper/all (기본 live)")
    ap.add_argument("--ev-db", default=str(DEFAULT_EV_DB))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    ev_db = Path(args.ev_db)
    if not ev_db.exists():
        print(f"[ERR] DB 없음: {ev_db}")
        return 2

    mode = None if args.runtime_mode == "all" else args.runtime_mode
    data = load_closes(ev_db, mode)
    markets = ["KR", "US"] if args.market == "both" else [args.market]
    out = [analyze(m, data.get(m, {}), args.threshold, args.deep) for m in markets if data.get(m)]

    if args.json:
        print(json.dumps([asdict(o) for o in out], ensure_ascii=False, indent=2))
    else:
        print(f"=== 좌측꼬리 청산사유 귀인 (mode={args.runtime_mode}, "
              f"threshold={args.threshold}%, deep={args.deep}%) ===")
        for o in out:
            print(f"\n[{o.market}] CLOSED {o.closed_n}건 중 좌측꼬리 {o.tail_n}건")
            print(f"  버킷별 좌측꼬리: {o.bucket_tail}")
            for s in o.tail_reasons:
                print(f"    {s.reason_code:30} n={s.n:3d} mean={s.mean_pct:+.2f}% "
                      f"median={s.median_pct:+.2f}% worst={s.worst_pct:+.2f}%")
            am = f"{o.auto_stop_mean:+.2f}%" if o.auto_stop_mean is not None else "n/a"
            print(f"  자동손절 {o.auto_stop_n}건 평균 {am}, {o.deep_label()}")
            print(f"  판정: {o.verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
