"""원장 pnl 정합성 감사 (읽기 전용).

원장 pnl_pct의 정의(2026-08-01 실측 확정):
    pnl_pct = USD(native)수익률 + 환율변동 - 수수료  = KRW 기준 net
(entry, exit)로 재계산하면 USD gross가 나오므로 둘은 원래 다르다. 이 차이를
'오류'로 오독하지 않도록, 재계산값에서 fx_change_pct와 수수료 오프셋을 보정한 뒤
남는 잔차만 문제로 본다. 잔차와 fx_change_pct의 상관은 보정 전 -0.99였다.

진짜 문제로 보는 것:
1) 청산가(last_exit_price)가 없어 사후 검증 자체가 불가능한 건 (약 16%)
2) fx/수수료로 설명되지 않는 잔차

주의:
- 분할익절(profit_ladder)이 있으면 last_exit_price는 '마지막' 체결가라 평균과 다르다.
  그래서 qty=1(분할 불가) 건만 STRICT로 판정하고 qty>1은 참고용으로만 센다.
- 수수료 오프셋은 시기별로 다르다(2026-04 US는 0=원장이 gross, 06은 +0.25).
  상수로 가정하지 않고 market x 월 중앙값으로 실측한다.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ML_DB = ROOT / "data" / "ml" / "decisions.db"

# 오프셋(재계산gross - 원장pnl)은 시기별로 다르다. 2026-04 US는 0(원장이 gross),
# 2026-06 US는 +0.25(원장이 수수료 차감 후)로 실측됐다. 그래서 상수로 가정하지 않고
# market x 월 단위 중앙값으로 추정한 뒤, 그 기준선에서 벗어나는 잔차만 문제로 본다.
MIN_GROUP_FOR_OFFSET = 5


def _connect_ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{str(path).replace(chr(92), '/')}?mode=ro", uri=True, timeout=30)
    con.execute("PRAGMA busy_timeout=10000")
    return con


def _parse(ts: str):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def audit(ml_db: Path, tolerance: float, start_date: str) -> dict:
    con = _connect_ro(ml_db)
    rows = list(
        con.execute(
            """select market, ticker, session_date, qty, entry_price, last_exit_price,
                      pnl_pct, earliest_fill_at, last_closed_at, strategy,
                      fx_change_pct
               from v2_canonical_performance
               where closed=1 and runtime_mode='live' and ticker<>'SIMTK'
                 and session_date >= ?
               order by session_date""",
            (start_date,),
        )
    )
    con.close()

    total = len(rows)
    missing_exit: list[dict] = []
    unverifiable_fx: list[dict] = []
    findings: list[dict] = []
    checked_strict = 0
    checked_loose = 0

    # 1차 패스: market x 월 그룹별 오프셋(재계산 - 원장) 중앙값을 실측한다.
    raw: list[tuple] = []
    groups: dict[tuple, list[float]] = {}
    for (market, ticker, sdate, qty, entry, exit_px, pnl, fill_at, close_at, strategy,
         fx_chg) in rows:
        if not entry or entry <= 0 or pnl is None:
            continue
        if not exit_px or exit_px <= 0:
            missing_exit.append({"market": market, "ticker": ticker, "session_date": sdate})
            continue
        # 원장은 KRW 기준(USD수익률 + fx - 수수료)이라 fx 없이는 비교 자체가 성립하지 않는다.
        # 추정 환율로 메우면 원장에 근거 없는 수치가 섞이므로 '검증 불가'로 분리한다.
        if market == "US" and fx_chg is None:
            unverifiable_fx.append({"market": market, "ticker": ticker, "session_date": sdate})
            continue
        recalc = 100.0 * (exit_px / entry - 1.0)
        diff = (recalc + float(fx_chg or 0.0)) - pnl
        raw.append((market, ticker, sdate, qty, entry, exit_px, pnl, fill_at, close_at,
                    strategy, recalc, diff, float(fx_chg or 0.0)))
        if qty == 1.0:  # 오프셋 추정은 분할청산 오염이 없는 건으로만
            groups.setdefault((market, sdate[:7]), []).append(diff)

    offsets: dict[tuple, float] = {}
    for key, vals in groups.items():
        if len(vals) < MIN_GROUP_FOR_OFFSET:
            continue
        s = sorted(vals)
        offsets[key] = s[len(s) // 2]

    # 그룹 표본이 부족하면 시장 전체 중앙값으로 폴백
    market_fallback: dict[str, float] = {}
    by_market: dict[str, list[float]] = {}
    for (market, _ym), vals in groups.items():
        by_market.setdefault(market, []).extend(vals)
    for market, vals in by_market.items():
        s = sorted(vals)
        market_fallback[market] = s[len(s) // 2] if s else 0.0

    for (market, ticker, sdate, qty, entry, exit_px, pnl, fill_at, close_at,
         strategy, recalc, diff, fx_chg) in raw:
        offset = offsets.get((market, sdate[:7]))
        if offset is None:
            offset = market_fallback.get(market, 0.0)
        residual = diff - offset  # 그룹 기준선에서 벗어난 정도

        a, b = _parse(fill_at), _parse(close_at)
        hold_days = (b - a).total_seconds() / 86400.0 if a and b else None
        strict = (qty == 1.0)
        if strict:
            checked_strict += 1
        else:
            checked_loose += 1

        if abs(residual) > tolerance:
            findings.append({
                "market": market, "ticker": ticker, "session_date": sdate,
                "strategy": strategy or "", "qty": qty,
                "entry_price": round(entry, 4), "exit_price": round(exit_px, 4),
                "ledger_pnl_pct": round(pnl, 3),
                "recalc_gross_pct": round(recalc, 3),
                "fx_change_pct": round(fx_chg, 3),
                "residual_pct": round(residual, 3),
                "hold_days": round(hold_days, 2) if hold_days is not None else None,
                "multiday": bool(hold_days is not None and hold_days >= 1.0),
                "strict": strict,
            })

    strict_findings = [f for f in findings if f["strict"]]
    md = [f for f in strict_findings if f["multiday"]]
    intraday = [f for f in strict_findings if not f["multiday"]]

    return {
        "estimated_offsets": {f"{m}|{ym}": round(v, 3) for (m, ym), v in sorted(offsets.items())},
        "market_fallback_offset": {m: round(v, 3) for m, v in sorted(market_fallback.items())},
        "total_closed": total,
        "missing_exit_price": len(missing_exit),
        "missing_exit_pct": round(100.0 * len(missing_exit) / total, 1) if total else 0.0,
        "unverifiable_fx_missing": len(unverifiable_fx),
        "unverifiable_fx_pct": round(100.0 * len(unverifiable_fx) / total, 1) if total else 0.0,
        "verifiable": total - len(missing_exit) - len(unverifiable_fx),
        "checked_strict_qty1": checked_strict,
        "checked_loose_qty_gt1": checked_loose,
        "tolerance_pct": tolerance,
        "findings_strict": len(strict_findings),
        "findings_strict_multiday": len(md),
        "findings_strict_intraday": len(intraday),
        "findings": sorted(findings, key=lambda f: -abs(f["residual_pct"])),
        "missing_exit_sample": missing_exit[:20],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="원장 pnl 정합성 감사 (읽기 전용)")
    ap.add_argument("--ml-db", default=str(DEFAULT_ML_DB))
    ap.add_argument("--start-date", default="2026-01-01")
    ap.add_argument("--tolerance", type=float, default=0.15,
                    help="수수료 오프셋 보정 후 허용 잔차(%%p)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-rows", type=int, default=25)
    ap.add_argument("--fail-on-finding", action="store_true",
                    help="strict 불일치가 있으면 exit 1")
    args = ap.parse_args()

    report = audit(Path(args.ml_db), args.tolerance, args.start_date)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("=== 원장 pnl 정합성 감사 ===")
        print(f"  청산건 {report['total_closed']}건")
        print(f"  [검증 불가] 청산가 결손 {report['missing_exit_price']}건 ({report['missing_exit_pct']}%)")
        print(f"  [검증 불가] US fx_change_pct 결손 {report['unverifiable_fx_missing']}건 "
              f"({report['unverifiable_fx_pct']}%) - 원장이 KRW기준이라 fx 없이는 비교 불성립")
        print(f"  [검증 가능] {report['verifiable']}건")
        print(f"  검증 대상 qty=1 {report['checked_strict_qty1']}건 / qty>1 {report['checked_loose_qty_gt1']}건(참고)")
        print(f"  허용 잔차 ±{report['tolerance_pct']}%p "
              f"(원장=KRW기준 -> 재계산에 fx_change_pct + 월별 수수료오프셋 보정 후)")
        print(f"  실측 오프셋: {report['estimated_offsets']}")
        print()
        print(f"  ** qty=1 불일치 {report['findings_strict']}건 "
              f"(멀티데이 {report['findings_strict_multiday']} / 당일 {report['findings_strict_intraday']}) **")
        print()
        strict = [f for f in report["findings"] if f["strict"]]
        if strict:
            print(f"  {'mkt':4}{'ticker':8}{'date':12}{'entry':>10}{'exit':>10}"
                  f"{'원장':>9}{'재계산':>9}{'잔차':>8}{'보유일':>7}")
            for f in strict[:args.max_rows]:
                print(f"  {f['market']:4}{f['ticker']:8}{f['session_date']:12}"
                      f"{f['entry_price']:10.2f}{f['exit_price']:10.2f}"
                      f"{f['ledger_pnl_pct']:9.3f}{f['recalc_gross_pct']:9.3f}"
                      f"{f['residual_pct']:8.3f}{str(f['hold_days']):>7}")
        else:
            print("  (불일치 없음)")

    if args.fail_on_finding and report["findings_strict"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
