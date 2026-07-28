"""경로 기반 트레일링 청산 시뮬 — 비용·슬리피지 반영.

배경 (2026-07-28):
  logs/funnel/tail_capture_*.jsonl 실측에서 TAIL_CAPTURE 배선은 살아있으나
  활성화 임계 4%가 관측 MFE 분포의 p97.6에 위치해 90.8%가 pre_activation으로 끝났다.
  (관측 MFE 중앙 0.32% / p90 1.44% / p95 2.38%, MFE>=4%는 2.4%)

  1차 경로 시뮬(비용 미반영)은 A=1.5/G=0.5에서 실제 대비 +67.86%p를 냈으나,
  G=0.5%는 왕복비용(KR 0.21% / US 0.50%)과 같은 자리수라 비용 반영 전에는
  판단 근거가 될 수 없다. 이 스크립트는 그 판단을 위해 비용·슬리피지를 넣는다.

방법
  1. v2_canonical_performance에서 실제 청산건(pnl_pct_net NOT NULL)을 읽는다.
  2. logs/funnel/post_open_feature_snapshot_*.jsonl에서 (세션,시장,종목) 장중 경로를 만든다.
  3. 체결시각(earliest_fill_at) 이후 스냅샷만 남겨 peak를 전진 추적한다.
     - 되돌림 판정에 미래 정보를 쓰지 않는다(진입 이후 갱신된 고점만 사용).
  4. 청산 규칙: 하드스톱(항상) → MFE>=A 활성화 후 peak 대비 G% 되돌리면 청산
     → 미청산이면 마지막 스냅샷 가격(세션 마감 대용).
  5. gross에서 왕복수수료 + 슬리피지(진입/청산 각각)를 빼 net으로 환산한다.
     실제 net과 같은 비용 기준으로 비교하기 위해 실제건의 fee_pct_round_trip을 우선 사용.

한계 (보고 시 반드시 함께 낸다)
  - 스냅샷 간격(중앙 ~5분) 사이의 되돌림은 보이지 않는다 → 트레일 발동이 실제보다 늦다(보수적).
  - 체결가는 발동 시점 스냅샷 가격 + 슬리피지 가정이다. 실제 호가 스프레드는 반영하지 않는다.
  - 하드스톱으로 죽은 건을 트레일이 구제하지 못하도록 하드스톱을 먼저 적용한다.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sqlite3
import statistics
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DECISIONS_DB = ROOT / "data" / "ml" / "decisions.db"
SNAPSHOT_GLOB = str(ROOT / "logs" / "funnel" / "post_open_feature_snapshot_*.jsonl")

# 왕복 수수료(세금 포함) — CLAUDE.md 비용 계약
FEE_ROUND_TRIP = {"KR": 0.21, "US": 0.50}
# 슬리피지 캡(운영자 확인 필수 파라미터) — 편도 기준으로 환산
SLIPPAGE_ONE_WAY = {"KR": 0.30, "US": 0.20}


def _parse_ts(raw) -> datetime | None:
    if not raw:
        return None
    s = str(raw).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def load_paths(since: str) -> dict[tuple[str, str, str], list[tuple[datetime, float]]]:
    """(session_date, market, ticker) -> [(known_at, price), ...] 시간순."""
    paths: dict[tuple[str, str, str], list[tuple[datetime, float]]] = defaultdict(list)
    for f in sorted(glob.glob(SNAPSHOT_GLOB)):
        base = os.path.basename(f)
        parts = base.replace(".jsonl", "").split("_")
        if len(parts) < 2 or not parts[-2].isdigit():
            continue
        day = f"{parts[-2][:4]}-{parts[-2][4:6]}-{parts[-2][6:8]}"
        if day < since:
            continue
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                px = o.get("current_price")
                ts = _parse_ts(o.get("known_at") or o.get("written_at"))
                if not isinstance(px, (int, float)) or px <= 0 or ts is None:
                    continue
                key = (
                    str(o.get("market_session_date") or o.get("session_date") or ""),
                    str(o.get("market") or "").upper(),
                    str(o.get("ticker") or ""),
                )
                if not all(key):
                    continue
                paths[key].append((ts, float(px)))
    for k in paths:
        paths[k].sort(key=lambda x: x[0])
    return paths


def load_trades(since: str) -> list[dict]:
    con = sqlite3.connect(f"file:{DECISIONS_DB}?mode=ro", uri=True)
    con.execute("PRAGMA busy_timeout=8000")
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT session_date, market, ticker, entry_price, earliest_fill_at,
               last_closed_at, pnl_pct_net, pnl_pct, mfe_pct, fee_pct_round_trip,
               path_type, strategy
          FROM v2_canonical_performance
         WHERE pnl_pct_net IS NOT NULL
           AND entry_price > 0
           AND COALESCE(session_date, '') >= ?
        """,
        (since,),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def simulate(path: list[tuple[datetime, float]], entry: float, act_pct: float,
             give_pct: float, hard_stop_pct: float) -> tuple[float, str, int]:
    """gross %, 청산사유, 사용 스냅샷 수. 미래정보 미사용."""
    peak = entry
    used = 0
    last = entry
    for _ts, px in path:
        used += 1
        last = px
        if px <= entry * (1 - hard_stop_pct / 100.0):
            return (px / entry - 1) * 100.0, "hard_stop", used
        peak = max(peak, px)
        mfe = (peak / entry - 1) * 100.0
        if mfe >= act_pct and px <= peak * (1 - give_pct / 100.0):
            return (px / entry - 1) * 100.0, "trail", used
    return (last / entry - 1) * 100.0, "session_end", used


def net_of(gross_pct: float, market: str, fee_pct: float | None, with_slippage: bool) -> float:
    fee = fee_pct if isinstance(fee_pct, (int, float)) and fee_pct > 0 \
        else FEE_ROUND_TRIP.get(market, 0.50)
    slip = (SLIPPAGE_ONE_WAY.get(market, 0.20) * 2.0) if with_slippage else 0.0
    return gross_pct - fee - slip


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-06-01")
    ap.add_argument("--hard-stop", type=float, default=2.0)
    ap.add_argument("--no-slippage", action="store_true",
                    help="슬리피지 미반영(1차 시뮬과 동일 조건 재현용)")
    ap.add_argument("--market", default="", help="KR/US 한쪽만")
    args = ap.parse_args()

    paths = load_paths(args.since)
    trades = load_trades(args.since)
    if args.market:
        trades = [t for t in trades if str(t["market"]).upper() == args.market.upper()]

    matched: list[tuple[dict, list[tuple[datetime, float]]]] = []
    gaps: list[float] = []
    for t in trades:
        key = (str(t["session_date"]), str(t["market"]).upper(), str(t["ticker"]))
        series = paths.get(key) or []
        fill = _parse_ts(t.get("earliest_fill_at"))
        if fill is not None:
            series = [(ts, px) for ts, px in series if ts >= fill]
        if len(series) < 2:
            continue
        matched.append((t, series))
        for a, b in zip(series, series[1:]):
            gaps.append((b[0] - a[0]).total_seconds() / 60.0)

    print(f"경로 보유 조합 {len(paths):,}")
    print(f"{args.since} 이후 청산 {len(trades)}건 · 경로 결합 {len(matched)}건 "
          f"({len(matched)/max(1,len(trades))*100:.0f}%)")
    if gaps:
        gaps.sort()
        print(f"스냅샷 간격 중앙 {statistics.median(gaps):.1f}분 · p90 {gaps[int(len(gaps)*.9)]:.1f}분")
    if not matched:
        print("결합 0건 — 중단")
        return 1

    actual = sum(float(t["pnl_pct_net"]) for t, _ in matched)
    print(f"\n실제 net 합계 {actual:+.2f}%p (평균 {actual/len(matched):+.3f}%)")

    slip_on = not args.no_slippage
    print(f"비용 기준: 왕복수수료 {'실측 fee_pct_round_trip 우선' } + "
          f"슬리피지 {'편도 KR 0.30%/US 0.20% 양방향' if slip_on else '미반영'}")

    grid_a = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]
    grid_g = [0.5, 0.8, 1.0, 1.5, 2.0, 3.0]
    print(f"\n{'활성A':>5} {'반납G':>5} {'트레일':>5} {'스톱':>4} {'마감':>4} "
          f"{'net합':>10} {'평균':>9} {'vs실제':>10}")
    best = None
    for a in grid_a:
        for g in grid_g:
            tot = 0.0
            cnt = {"trail": 0, "hard_stop": 0, "session_end": 0}
            for t, series in matched:
                gross, why, _ = simulate(series, float(t["entry_price"]), a, g, args.hard_stop)
                cnt[why] += 1
                tot += net_of(gross, str(t["market"]).upper(), t.get("fee_pct_round_trip"), slip_on)
            delta = tot - actual
            print(f"{a:5.1f} {g:5.1f} {cnt['trail']:5d} {cnt['hard_stop']:4d} "
                  f"{cnt['session_end']:4d} {tot:+10.2f}%p {tot/len(matched):+8.3f}% {delta:+9.2f}%p")
            if best is None or tot > best[0]:
                best = (tot, a, g, cnt["trail"])
    if best:
        print(f"\n★최적 A={best[1]} G={best[2]} → {best[0]:+.2f}%p "
              f"(트레일 발동 {best[3]}건, 실제 대비 {best[0]-actual:+.2f}%p)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
