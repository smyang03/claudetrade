# -*- coding: utf-8 -*-
"""월간 계좌 손익 리포트 (2026-10-30, 시스템 점검 docs/reports/system_results_review_20261030.md §5-4).

브로커 기간손익(KIS TTTC8715R·TTTS3039R, 조회 전용)으로 그 달의 시장·종목별 실현 손익과 비용을 집계하고,
브로커 스냅샷(state/live_broker_truth_snapshot.json)의 보유 평가·현금으로 월말 계좌 총자산을 함께 적는다.
레인 원장(패닉·캐너리·T1·파킹)의 같은 달 마감 손익을 브로커 실현과 나란히 둬 대사 차이를 보인다.

usage: python tools/account_monthly_report.py [YYYY-MM]   (기본: 지난달)
출력: data/analysis/account_monthly/<YYYY-MM>.json, .md   (런타임 산출물 — 커밋하지 않는다)
주문 없음. KIS 조회 3~5회(국내 1·해외 거래소 3). 장중 시세 루프 아님.
"""
from __future__ import annotations

import calendar
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
OUT = ROOT / "data" / "analysis" / "account_monthly"
SH = ROOT / "data" / "shadow"
LANES = {"panic": ("kr_panic_etf_micro_live.jsonl", "close", "exit_session", "pnl_krw"),
         "canary": ("kr_buyback_canary_live.jsonl", "position", None, "pnl_krw"),
         "t1": ("us_earnings_t1_live.jsonl", "close", "exit_session", "pnl_krw")}


def month_range(ym: str | None) -> tuple[str, str, str]:
    if ym:
        y, m = (int(x) for x in ym.split("-"))
    else:
        first = date.today().replace(day=1) - timedelta(days=1)
        y, m = first.year, first.month
    last = calendar.monthrange(y, m)[1]
    return f"{y:04d}-{m:02d}", f"{y:04d}{m:02d}01", f"{y:04d}{m:02d}{last:02d}"


def _read(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def summarize_broker(kr_rows: list[dict], us_rows: list[dict]) -> dict:
    """순수 함수 — KR rlzt_pfls·fee·tl_tax, US ovrs_rlzt_pfls_amt·stck_sll_tlex(원화) 합산. 종목별 상위 손익."""
    kr = {"realized": 0.0, "cost": 0.0, "sells": 0}
    us = {"realized": 0.0, "cost": 0.0, "sells": 0}
    by = defaultdict(float)
    for r in kr_rows:
        kr["realized"] += float(r.get("rlzt_pfls") or 0)
        kr["cost"] += float(r.get("fee") or 0) + float(r.get("tl_tax") or 0)
        kr["sells"] += 1 if float(r.get("sll_qty") or 0) > 0 else 0
        by[f"KR {r.get('pdno')} {r.get('prdt_name', '')}"] += float(r.get("rlzt_pfls") or 0)
    for r in us_rows:
        us["realized"] += float(r.get("ovrs_rlzt_pfls_amt") or 0)
        us["cost"] += float(r.get("stck_sll_tlex") or 0)
        us["sells"] += 1
        by[f"US {r.get('ovrs_pdno')} {r.get('ovrs_item_name', '')}"] += float(r.get("ovrs_rlzt_pfls_amt") or 0)
    tot = kr["realized"] + us["realized"]
    return {"KR": {k: round(v) for k, v in kr.items()}, "US": {k: round(v) for k, v in us.items()},
            "realized_total": round(tot), "cost_total": round(kr["cost"] + us["cost"]),
            "pre_cost_total": round(tot + kr["cost"] + us["cost"]),
            "by_ticker": {k: round(v) for k, v in sorted(by.items(), key=lambda kv: kv[1])}}


def lane_closes(ym: str) -> dict:
    out = {}
    for lane, (fname, kind, dkey, pkey) in LANES.items():
        rows = _read(SH / fname)
        s = 0.0
        n = 0
        for r in rows:
            if r.get("kind") != kind or r.get(pkey) is None:
                continue
            if kind == "position" and r.get("status") != "CLOSED":
                continue
            d = str(r.get(dkey) or r.get("at") or "")[:7]
            if d == ym:
                s += float(r[pkey]); n += 1
        out[lane] = {"n": n, "pnl_krw": round(s)}
    return out


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ym, d0, d1 = month_range(argv[1] if len(argv) > 1 else None)
    from dotenv import load_dotenv
    load_dotenv(str(ROOT / ".env.live"), override=True)
    import kis_api
    from net_ready import wait_dns
    wait_dns(["openapi.koreainvestment.com"], tag="acct")
    tok_kr = kis_api.get_access_token(market="KR")
    kr = kis_api.inquire_period_trade_profit_kr(tok_kr, d0, d1, max_pages=30)
    us_rows = []
    tok_us = kis_api.get_access_token(market="US")
    for ex in ("NASD", "NYSE", "AMEX"):           # 공란("")은 NASD와 같은 값 — 중복이라 제외(09-24 실측)
        us_rows += kis_api.inquire_period_profit_us(tok_us, d0, d1, exchange_code=ex, max_pages=30)["rows"]
    rep = {"month": ym, "range": [d0, d1], "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "broker": summarize_broker(kr["rows"], us_rows), "lanes": lane_closes(ym)}
    try:
        from daily_ops_report import account_total_krw
        snap = json.loads((ROOT / "state" / "live_broker_truth_snapshot.json").read_text(encoding="utf-8"))
        a_kr = snap["markets"]["KR"].get("account_summary", {})
        a_us = snap["markets"]["US"].get("account_summary", {})
        rep["account_total_krw_now"] = round(account_total_krw(a_kr, a_us))
        rep["snapshot_at"] = snap.get("generated_at")
    except (OSError, ValueError, KeyError) as exc:
        rep["account_total_krw_now"] = None
        rep["snapshot_error"] = str(exc)[:120]
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{ym}.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    b = rep["broker"]
    lines = [f"# 계좌 월간 손익 {ym}", "",
             f"- 실현(비용 차감 후): **{b['realized_total']:,}원** · 비용 {b['cost_total']:,}원 · 비용 전 {b['pre_cost_total']:,}원",
             f"- KR 실현 {b['KR']['realized']:,} (매도 {b['KR']['sells']}건) · US 실현 {b['US']['realized']:,} (매도 {b['US']['sells']}건)",
             f"- 계좌 총자산(조회 시점): {rep['account_total_krw_now']:,}원" if rep.get("account_total_krw_now") else "- 계좌 총자산: 스냅샷 없음",
             "", "| 레인 | 마감 건수 | 원장 손익 |", "|---|---:|---:|"]
    lines += [f"| {k} | {v['n']} | {v['pnl_krw']:,} |" for k, v in rep["lanes"].items()]
    lines += ["", "종목별(실현):"] + [f"- {k}: {v:,}" for k, v in b["by_ticker"].items()]
    (OUT / f"{ym}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:4]))
    print("saved", OUT / f"{ym}.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
