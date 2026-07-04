from __future__ import annotations

"""Path B 취소 plan 사후성과 — "취소가 기회손실인지 손실회피인지" 누적 측정 (read-only).

배경(2026-06-30~07-01 분석): US 강세장에서 취소된 plan이 종가까지 상승 유지(6/29 US
종가 +3.41%, 올라감 17/22)되어 과보수 기회손실 정황. 반면 KR은 취소가 손실 회피
(6/29 067290 종가 -15.4%). 시장별·국면별로 정반대라 단일 세션으로 단정 불가 →
취소시점가 → 취소후 고점/저점/종가(되돌림)를 분봉으로 재구성해 다국면 누적.

판정 가이드(종가 기준, 분봉 마감 완결 세션):
  - 종가평균 > +0.3% 지속 + zone 도달(눌림 옴) 높음 = 과보수(눌림 기회 막음)
  - 종가평균 < -0.3% = 정당(손실 회피)
  - 고점-종가(되돌림) 큼 = 장중 급등 환상(cap-widen 패턴, 추격해도 못 먹음)

소스: v2_event_store.db v2_path_runs(status=CANCELLED) + data/price/minute/{kr|us}.
mode=ro + busy_timeout. 외부 API·brain·DB 쓰기 없음. 5분봉 yfinance 추정 한계.
"""

import argparse
import csv
import json
import os
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EV_DB = ROOT / "data" / "v2_event_store.db"
MIN_DIR = ROOT / "data" / "price" / "minute"


def _avg(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.mean(xs), 2) if xs else None


def _load_bars(market: str, ticker: str):
    m = market.lower()
    path = MIN_DIR / m / f"{m}_{ticker}.csv"
    if not path.exists():
        return None
    out = []
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                out.append((r["ts"], float(r["high"]), float(r["low"]), float(r["close"])))
            except (KeyError, ValueError, TypeError):
                continue
    return out


def _utc_to_kst(s: str):
    try:
        dt = datetime.fromisoformat(str(s).replace("+00:00", ""))
        return dt + timedelta(hours=9)
    except (ValueError, TypeError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--market", default="US", choices=["US", "KR"])
    ap.add_argument("--since", default="2026-01-01", help="session_date >= (YYYY-MM-DD)")
    ap.add_argument("--hours", type=float, default=10.0, help="취소 후 추적 시간(세션 마감 커버)")
    args = ap.parse_args()

    conn = sqlite3.connect(f"file:{EV_DB}?mode=ro", uri=True, timeout=10)
    conn.execute("PRAGMA busy_timeout=10000")
    rows = conn.execute(
        "SELECT session_date,updated_at,plan_json FROM v2_path_runs "
        "WHERE market=? AND path_type='claude_price' AND status='CANCELLED' AND session_date>=?",
        (args.market, args.since),
    ).fetchall()
    conn.close()

    by_reason = defaultdict(list)
    by_month = defaultdict(list)
    recs = []
    skipped = 0
    for sdate, ua, pj in rows:
        try:
            d = json.loads(pj)
        except (json.JSONDecodeError, TypeError):
            continue
        ref = d.get("reference_price")
        bzh = d.get("buy_zone_high")
        tk = d.get("ticker")
        kdt = _utc_to_kst(ua)
        if not (isinstance(ref, (int, float)) and ref > 0 and kdt and tk):
            skipped += 1
            continue
        bars = _load_bars(args.market, str(tk))
        if not bars:
            skipped += 1
            continue
        kst = kdt.isoformat()[:16]
        end = (kdt + timedelta(hours=args.hours)).isoformat()[:16]
        seg = [b for b in bars if kst <= b[0][:16] <= end]
        if not seg:
            skipped += 1
            continue
        hi = max(b[1] for b in seg)
        lo = min(b[2] for b in seg)
        close = seg[-1][3]
        peak_pct = (hi - ref) / ref * 100
        low_pct = (lo - ref) / ref * 100
        close_pct = (close - ref) / ref * 100
        reached = (lo <= bzh) if isinstance(bzh, (int, float)) else None
        rec = {
            "ticker": tk, "reason": d.get("cancel_reason") or "?",
            "month": str(sdate)[:7], "peak": peak_pct, "low": low_pct,
            "close": close_pct, "reached": reached,
        }
        recs.append(rec)
        by_reason[rec["reason"]].append(rec)
        by_month[rec["month"]].append(rec)

    print(f"=== Path B 취소 사후성과 | {args.market} | since {args.since} ===")
    print(f"분석 {len(recs)}건 (분봉/데이터 부족 스킵 {skipped})\n")
    if not recs:
        return

    def summarize(label, sub):
        peaks = [r["peak"] for r in sub]
        closes = [r["close"] for r in sub]
        lows = [r["low"] for r in sub]
        pk, cl = _avg(peaks), _avg(closes)
        up = sum(1 for c in closes if c > 0.3)
        dn = sum(1 for c in closes if c < -0.3)
        reached = sum(1 for r in sub if r["reached"] is True)
        gb = round((pk or 0) - (cl or 0), 2)
        cl_disp = f"{cl:+}" if isinstance(cl, (int, float)) else "--"
        print(f"  {label:42} N={len(sub):>3} | 고점+{pk}% 종가{cl_disp}% 저점{_avg(lows)}% | 되돌림{gb}p | "
              f"올{up}/빠{dn} | 눌림{reached}/{len(sub)}")

    print("-- 전체 --")
    summarize("ALL", recs)
    print("\n-- cancel_reason별 --")
    for r in sorted(by_reason, key=lambda k: -len(by_reason[k])):
        summarize(str(r)[:42], by_reason[r])
    print("\n-- 월별(국면) --")
    for m in sorted(by_month):
        summarize(m, by_month[m])

    print("\n[판정] 종가평균 >+0.3% & 눌림도달 높음 = 과보수(눌림기회 막음) / "
          "<-0.3% = 정당(손실회피) / 되돌림 크면 장중급등 환상(추격 못먹음). "
          "단일~소국면·5m추정 한계, 다국면 누적 후 판정.")


if __name__ == "__main__":
    main()
