from __future__ import annotations

"""전환 깔때기 리뷰 — selection→trade_ready→signal→traded (read-only).

"selection ranking은 좋은데(KR 알파 유의) 왜 무매수인가"의 답은 ranking이 주문으로
전환되는 깔때기에 있다. 이 도구는 ticker_selection_log를 (market, date, ticker) 단위로
dedup해 4단계 전환율을 측정한다:

  candidate → trade_ready(승격) → signal_fired(전략 발동) → traded(체결)

어느 관문에서 새는지(승격 실패 vs 발동 실패 vs 체결 실패)를 시장별·월별로 드러낸다.

한계: trade_ready=1인데 signal_fired=0인 사장의 "사유"(blocked_reason/veto_reason)는
ticker_selection_log에 거의 기록되지 않는다(실측 100% none) — 사유 추적은 candidate_audit/
로그 등 별도 소스가 필요(후속 과제). 입력은 로컬 sqlite뿐, 외부 호출 없음.
"""

import argparse
import json
import sqlite3
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SEL_DB = ROOT / "data" / "ticker_selection_log.db"


@dataclass
class FunnelRow:
    market: str
    stratum: str               # 월(YYYY-MM) 또는 "ALL"
    candidates: int
    ready: int
    fired: int
    traded: int
    promote_rate_pct: float    # ready/candidates (승격)
    fire_rate_pct: float | None  # fired/ready (전략 발동)
    fill_rate_pct: float | None  # traded/fired (체결)
    leak_stage: str            # 가장 크게 새는 관문


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.execute("PRAGMA busy_timeout=8000")
    return conn


def _leak_stage(promote: float, fire: float | None, fill: float | None) -> str:
    """전환율이 가장 낮은(=가장 크게 새는) 관문."""
    stages = [("승격(rank→ready)", promote)]
    if fire is not None:
        stages.append(("발동(ready→signal)", fire))
    if fill is not None:
        stages.append(("체결(signal→traded)", fill))
    return min(stages, key=lambda x: x[1])[0]


def load_funnel(sel_db: Path, bot_mode: str | None, stratify: str,
                since: str | None) -> list[FunnelRow]:
    conn = _connect_ro(sel_db)
    try:
        where = "1=1"
        params: list[Any] = []
        if bot_mode:
            where += " AND bot_mode=?"
            params.append(bot_mode)
        if since:
            where += " AND date>=?"
            params.append(since)
        rows = conn.execute(
            f"SELECT market, substr(date,1,7) ym, "
            f"MAX(trade_ready) tr, MAX(signal_fired) sf, MAX(traded) td "
            f"FROM ticker_selection_log WHERE {where} "
            f"GROUP BY market, date, ticker",
            params,
        ).fetchall()
    finally:
        conn.close()

    agg: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for market, ym, tr, sf, td in rows:
        key = (str(market), ym if stratify == "month" else "ALL")
        a = agg[key]
        a[0] += 1
        a[1] += int(tr or 0)
        a[2] += int(sf or 0)
        a[3] += int(td or 0)

    out: list[FunnelRow] = []
    for (market, stratum) in sorted(agg):
        c, r, f, t = agg[(market, stratum)]
        promote = r / c * 100 if c else 0.0
        fire = f / r * 100 if r else None
        fill = t / f * 100 if f else None
        out.append(FunnelRow(
            market=market, stratum=stratum, candidates=c, ready=r, fired=f, traded=t,
            promote_rate_pct=round(promote, 1),
            fire_rate_pct=round(fire, 1) if fire is not None else None,
            fill_rate_pct=round(fill, 1) if fill is not None else None,
            leak_stage=_leak_stage(promote, fire, fill),
        ))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="전환 깔때기 리뷰 (read-only)")
    ap.add_argument("--market", choices=["KR", "US", "both"], default="both")
    ap.add_argument("--stratify", choices=["month", "none"], default="month")
    ap.add_argument("--bot-mode", default="live", help="live/paper/all (기본 live)")
    ap.add_argument("--since", default=None, help="날짜 하한 YYYY-MM-DD")
    ap.add_argument("--sel-db", default=str(DEFAULT_SEL_DB))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    sel_db = Path(args.sel_db)
    if not sel_db.exists():
        print(f"[ERR] DB 없음: {sel_db}")
        return 2

    mode = None if args.bot_mode == "all" else args.bot_mode
    rows = load_funnel(sel_db, mode, args.stratify, args.since)
    if args.market != "both":
        rows = [r for r in rows if r.market == args.market]

    if args.json:
        print(json.dumps([asdict(r) for r in rows], ensure_ascii=False, indent=2))
    else:
        print(f"=== 전환 깔때기 (mode={args.bot_mode}, stratify={args.stratify}) ===")
        print(f"  {'mkt':3} {'기간':8} {'cand':>6} {'ready':>6} {'fired':>6} {'trd':>5} "
              f"{'승격':>6} {'발동':>6} {'체결':>6}  최대누수")
        for r in rows:
            fr = f"{r.fire_rate_pct}%" if r.fire_rate_pct is not None else "-"
            fl = f"{r.fill_rate_pct}%" if r.fill_rate_pct is not None else "-"
            print(f"  {r.market:3} {r.stratum:8} {r.candidates:>6} {r.ready:>6} {r.fired:>6} "
                  f"{r.traded:>5} {r.promote_rate_pct:>5}% {fr:>6} {fl:>6}  {r.leak_stage}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
