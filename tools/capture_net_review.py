from __future__ import annotations

"""Capture / net 성과 리포트.

이 시스템의 핵심 비효율은 "잘 고른 종목의 상승(runup)을 얼마나 실현했는가"(capture)와
"수수료 차감 후 net이 손익분기를 넘었는가"이다. 이 도구는 v2_learning_performance(실현
체결-청산)와 ticker_selection_log(진입 종목의 3일 runup/drawdown)를 조인해 다음을 산출한다.

- 시장별 gross / net / 승률 / PF
- 청산경로별 gross / net / capture
- 보유시간 버킷별 net
- 종목별 net 기여

모든 입력은 로컬 sqlite이며 broker/API/Claude 호출이 없다. forward/runup 필드는 사후 감사
라벨이므로 라이브 게이팅에 직접 쓰면 안 된다(측정 전용).
"""

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ML_DB = ROOT / "data" / "ml" / "decisions.db"
DEFAULT_SEL_DB = ROOT / "data" / "ticker_selection_log.db"

# 왕복 수수료 근사(%). 한투 미국 온라인 0.25%/편도 = 0.5% 왕복, 수수료 우대 없음(운영자 확인 2026-06-13).
# KR 왕복 수수료/세금은 미확인이라 보수적으로 동일 가정 — CLI로 조정 가능.
DEFAULT_FEE_PCT = {"US": 0.5, "KR": 0.5}
# 환전 스프레드 왕복(%). US 해외주식은 매수(원→달러)·매도(달러→원) 환전 2회 → net에 별도 차감해야
# 정직(usd_krw 참조환율엔 미반영). 우대(0.1%/회)=0.2% / 무우대=~2%. 미확인이라 우대 기본, CLI 조정.
DEFAULT_FX_SPREAD_PCT = {"US": 0.2, "KR": 0.0}

HOLD_BUCKETS = [
    (0, 30, "0-30분"),
    (30, 120, "30분-2시간"),
    (120, 360, "2-6시간"),
    (360, 1440, "6-24시간"),
    (1440, 10 ** 9, "1일+"),
]


@dataclass
class Stat:
    n: int
    win_rate_pct: float
    gross_avg: float
    gross_sum: float
    net_avg: float
    net_sum: float
    net_win_rate_pct: float
    profit_factor_net: float | str
    median_gross: float
    best: float
    worst: float


def _stat(grosses: list[float], nets: list[float]) -> Stat | None:
    if not grosses:
        return None
    gw = [x for x in grosses if x > 0]
    nw = [x for x in nets if x > 0]
    nl = [x for x in nets if x <= 0]
    pf = round(sum(nw) / abs(sum(nl)), 2) if nl and sum(nl) != 0 else "inf"
    return Stat(
        n=len(grosses),
        win_rate_pct=round(len(gw) / len(grosses) * 100, 1),
        gross_avg=round(mean(grosses), 2),
        gross_sum=round(sum(grosses), 1),
        net_avg=round(mean(nets), 2),
        net_sum=round(sum(nets), 1),
        net_win_rate_pct=round(len(nw) / len(nets) * 100, 1),
        profit_factor_net=pf,
        median_gross=round(median(grosses), 2),
        best=round(max(grosses), 1),
        worst=round(min(grosses), 1),
    )


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _ticker_key(market: str, ticker: Any) -> str:
    text = str(ticker or "").strip()
    return text.upper() if str(market or "").upper() == "US" else text


def load_closed(ml_db: Path, runtime_mode: str | None) -> list[dict[str, Any]]:
    conn = _connect_ro(ml_db)
    try:
        sql = "SELECT * FROM v2_learning_performance WHERE closed=1 AND pnl_pct IS NOT NULL"
        params: list[Any] = []
        if runtime_mode:
            sql += " AND runtime_mode=?"
            params.append(runtime_mode)
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def load_runup(sel_db: Path) -> dict[tuple[str, str, str], dict[str, float]]:
    """(market, ticker_key, date) -> {runup_3d, drawdown_3d}. 같은 키 다수면 runup 최대값."""
    out: dict[tuple[str, str, str], dict[str, float]] = {}
    if not sel_db.exists():
        return out
    conn = _connect_ro(sel_db)
    try:
        rows = conn.execute(
            "SELECT market, ticker, date, max_runup_3d, max_drawdown_3d "
            "FROM ticker_selection_log WHERE max_runup_3d IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()
    for r in rows:
        key = (str(r["market"] or "").upper(), _ticker_key(r["market"], r["ticker"]), str(r["date"] or "")[:10])
        runup = float(r["max_runup_3d"])
        prev = out.get(key)
        if prev is None or runup > prev["runup_3d"]:
            out[key] = {"runup_3d": runup, "drawdown_3d": float(r["max_drawdown_3d"] or 0.0)}
    return out


def _hold_minutes(row: dict[str, Any]) -> float | None:
    try:
        f = datetime.fromisoformat(row["filled_at"])
        c = datetime.fromisoformat(row["closed_at"])
        return (c - f).total_seconds() / 60.0
    except Exception:
        return None


def load_mfe(ml_db: Path) -> dict[str, float]:
    """mfe_backfill_yf(yfinance 실측 MFE)를 v2_decision_id로 로드. 실제 '기회' 측정용."""
    out: dict[str, float] = {}
    try:
        con = _connect_ro(ml_db)
        for r in con.execute(
            "SELECT v2_decision_id,mfe_pct FROM mfe_backfill_yf WHERE mfe_pct IS NOT NULL AND source!='no_bars'"):
            out[str(r[0])] = float(r[1])
        con.close()
    except Exception:
        pass
    return out


def build_report(
    closed: list[dict[str, Any]],
    runup: dict[tuple[str, str, str], dict[str, float]],
    fee_pct: dict[str, float],
    mfe_map: dict[str, float] | None = None,
    fx_spread_pct: dict[str, float] | None = None,
) -> dict[str, Any]:
    fx_spread_pct = fx_spread_pct if fx_spread_pct is not None else DEFAULT_FX_SPREAD_PCT
    mfe_map = mfe_map or {}

    def net_of(row: dict[str, Any]) -> float:
        mkt = str(row.get("market") or "").upper()
        # 환전 스프레드는 native-% net(수수료만)에 안 잡힘 → 별도 차감해야 정직(US 환전 2회).
        fx = fx_spread_pct.get(mkt, 0.0)
        # measured net(수수료 반영, FX 스프레드 미반영)이 있으면 우선, 없으면 수수료 근사.
        if str(row.get("net_basis") or "") == "measured" and row.get("pnl_pct_net") is not None:
            return float(row["pnl_pct_net"]) - fx
        return float(row["pnl_pct"]) - fee_pct.get(mkt, 0.5) - fx

    def mfe_of(row: dict[str, Any]) -> float | None:
        # 라이브 ledger mfe_pct(Phase 1c tick기반, mark_closed 배선분) 우선 — 정확·held-window.
        # 과거분(배선 전, ledger NULL)은 yfinance 백필 fallback.
        v = row.get("mfe_pct")
        if v not in (None, 0):
            return float(v)
        return mfe_map.get(str(row.get("v2_decision_id") or ""))

    def mfe_source(row: dict[str, Any]) -> str | None:
        if row.get("mfe_pct") not in (None, 0):
            return "ledger"
        if mfe_map.get(str(row.get("v2_decision_id") or "")) is not None:
            return "backfill"
        return None

    report: dict[str, Any] = {"by_market": {}, "by_close_reason": {}, "by_hold_bucket": {}, "by_ticker": {}, "capture": {}, "mfe_capture": {}, "by_month": {}}

    # 측정 재정의: 실제 MFE 기반 net capture + 월별 국면 분해
    for market in ("US", "KR"):
        rows = [r for r in closed if str(r["market"]).upper() == market]
        if not rows:
            continue
        pairs = [(net_of(r), mfe_of(r)) for r in rows]
        pairs = [(n, m) for n, m in pairs if m and m > 0]
        # mfe 출처 커버리지(ledger=라이브배선 / backfill=yfinance) — Phase0→1 전환 가시화.
        src = {"ledger": 0, "backfill": 0, "none": 0}
        for r in rows:
            src[mfe_source(r) or "none"] += 1
        if pairs:
            net_sum = sum(n for n, _ in pairs)
            mfe_sum = sum(m for _, m in pairs)
            report["mfe_capture"][market] = {
                "n": len(pairs),
                "avg_mfe_pct": round(mfe_sum / len(pairs), 2),
                "net_capture_ratio": round(net_sum / mfe_sum, 3) if mfe_sum else None,
                "mfe_source": src,
            }
        months: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            months[str(r.get("session_date") or "")[:7]].append(net_of(r))
        report["by_month"][market] = {
            mo: {"n": len(v), "net_avg": round(mean(v), 2),
                 "win_pct": round(sum(1 for x in v if x > 0) / len(v) * 100, 0)}
            for mo, v in sorted(months.items()) if mo
        }

    # 시장별
    for market in ("US", "KR"):
        rows = [r for r in closed if str(r["market"]).upper() == market]
        st = _stat([float(r["pnl_pct"]) for r in rows], [net_of(r) for r in rows])
        if st:
            over = sum(1 for r in rows if float(r["pnl_pct"]) > fee_pct.get(market, 0.5) + fx_spread_pct.get(market, 0.0))
            report["by_market"][market] = {
                **asdict(st),
                "net_breakeven_pass_pct": round(over / len(rows) * 100, 1),
            }

    # 청산경로별(시장 분리) + capture
    for market in ("US", "KR"):
        cr_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in closed:
            if str(r["market"]).upper() == market:
                cr_map[str(r["close_reason"] or "NONE")].append(r)
        bucket: dict[str, Any] = {}
        for cr, rows in sorted(cr_map.items(), key=lambda kv: -sum(net_of(x) for x in kv[1])):
            st = _stat([float(r["pnl_pct"]) for r in rows], [net_of(r) for r in rows])
            if not st:
                continue
            # capture: 실현 gross / runup_3d (runup>0.5%인 매칭건만)
            caps = []
            for r in rows:
                key = (market, _ticker_key(market, r["ticker"]), str(r["session_date"] or "")[:10])
                ru = runup.get(key)
                if ru and ru["runup_3d"] > 0.5:
                    caps.append(float(r["pnl_pct"]) / ru["runup_3d"] * 100)
            entry = asdict(st)
            if caps:
                entry["capture_pct"] = round(mean(caps), 1)
                entry["capture_n"] = len(caps)
            bucket[cr] = entry
        report["by_close_reason"][market] = bucket

    # 보유시간 버킷별 net (US/KR 합산 + 시장별)
    for market in ("ALL", "US", "KR"):
        rows = [r for r in closed if market == "ALL" or str(r["market"]).upper() == market]
        bucket = {}
        for lo, hi, label in HOLD_BUCKETS:
            grp = [r for r in rows if (_hold_minutes(r) is not None and lo <= _hold_minutes(r) < hi)]
            st = _stat([float(r["pnl_pct"]) for r in grp], [net_of(r) for r in grp])
            if st:
                bucket[label] = asdict(st)
        report["by_hold_bucket"][market] = bucket

    # capture 요약 (시장별: 진입종목 runup 평균 vs 실현 평균)
    for market in ("US", "KR"):
        pairs = []
        for r in closed:
            if str(r["market"]).upper() != market:
                continue
            key = (market, _ticker_key(market, r["ticker"]), str(r["session_date"] or "")[:10])
            ru = runup.get(key)
            if ru and ru["runup_3d"] > 0.5:
                pairs.append((float(r["pnl_pct"]), ru["runup_3d"]))
        if pairs:
            avg_real = mean(p[0] for p in pairs)
            avg_runup = mean(p[1] for p in pairs)
            report["capture"][market] = {
                "n": len(pairs),
                "avg_realized_gross_pct": round(avg_real, 2),
                "avg_runup_3d_pct": round(avg_runup, 2),
                "capture_ratio_pct": round(avg_real / avg_runup * 100, 1) if avg_runup else None,
            }

    # 종목별 net 기여(2회 이상)
    for market in ("US", "KR"):
        tk_map: dict[str, list[float]] = defaultdict(list)
        for r in closed:
            if str(r["market"]).upper() == market:
                tk_map[str(r["ticker"])].append(net_of(r))
        multi = {t: {"n": len(v), "net_sum": round(sum(v), 1), "net_avg": round(mean(v), 2)} for t, v in tk_map.items() if len(v) >= 2}
        report["by_ticker"][market] = dict(sorted(multi.items(), key=lambda kv: kv[1]["net_sum"]))

    return report


def to_markdown(payload: dict[str, Any]) -> str:
    rep = payload["report"]
    lines: list[str] = []
    lines.append(f"# Capture / Net 성과 리뷰 ({payload['generated_at']})")
    lines.append("")
    lines.append(f"- 대상: closed={payload['basis']['closed_trades']}건, runtime_mode={payload['basis']['runtime_mode']}")
    lines.append(f"- 수수료 가정(왕복%): {payload['basis']['fee_pct']}")
    lines.append(f"- 환전 스프레드 가정(왕복%): {payload['basis'].get('fx_spread_pct', {})} (US 환전 2회, 우대0.2/무우대~2)")
    lines.append(f"- selection runup 매칭: {payload['basis']['runup_keys']}건")
    lines.append("")
    lines.append("## 시장별 (gross vs net)")
    lines.append("| 시장 | n | 승률 | gross평균 | gross합 | net평균 | net합 | net승률 | net손익분기통과 | PF(net) |")
    lines.append("|---|--|--|--|--|--|--|--|--|--|")
    for m, s in rep["by_market"].items():
        lines.append(f"| {m} | {s['n']} | {s['win_rate_pct']}% | {s['gross_avg']:+}% | {s['gross_sum']:+}% | {s['net_avg']:+}% | {s['net_sum']:+}% | {s['net_win_rate_pct']}% | {s['net_breakeven_pass_pct']}% | {s['profit_factor_net']} |")
    lines.append("")
    lines.append("## [측정 재정의] 실제 MFE 기반 net capture (실측 yfinance MFE 조인)")
    lines.append("> 분모를 forward(runup_3d, 선정일 종가·미체결 착시) 대신 **실제 보유 중 MFE**로 측정.")
    lines.append("| 시장 | n | 평균 MFE | **net capture(net합/mfe합)** |")
    lines.append("|---|--|--|--|")
    for m, s in rep.get("mfe_capture", {}).items():
        lines.append(f"| {m} | {s['n']} | {s['avg_mfe_pct']:+}% | {s['net_capture_ratio']} |")
    lines.append("")
    lines.append("## [측정 재정의] 월별 국면 분해 (net)")
    lines.append("| 시장 | 월 | n | net평균 | net승률 |")
    lines.append("|---|--|--|--|--|")
    for m, months in rep.get("by_month", {}).items():
        for mo, s in months.items():
            lines.append(f"| {m} | {mo} | {s['n']} | {s['net_avg']:+}% | {s['win_pct']:.0f}% |")
    lines.append("")
    lines.append("## Capture (참고용 — forward runup_3d 기반, 착시 주의)")
    lines.append("> ⚠️ runup_3d는 선정일 종가·N일버티기·미체결 포함(forward 착시). 위 MFE capture를 우선 신뢰.")
    lines.append("| 시장 | n | 실현평균(gross) | runup_3d평균 | capture |")
    lines.append("|---|--|--|--|--|")
    for m, s in rep["capture"].items():
        lines.append(f"| {m} | {s['n']} | {s['avg_realized_gross_pct']:+}% | {s['avg_runup_3d_pct']:+}% | {s['capture_ratio_pct']}% |")
    lines.append("")
    for market in ("US", "KR"):
        bucket = rep["by_close_reason"].get(market, {})
        if not bucket:
            continue
        lines.append(f"## {market} 청산경로별 (net 기준 정렬)")
        lines.append("| 청산경로 | n | net평균 | net합 | net승률 | capture |")
        lines.append("|---|--|--|--|--|--|")
        for cr, s in bucket.items():
            cap = f"{s.get('capture_pct')}% (n={s.get('capture_n')})" if "capture_pct" in s else "-"
            lines.append(f"| {cr} | {s['n']} | {s['net_avg']:+}% | {s['net_sum']:+}% | {s['net_win_rate_pct']}% | {cap} |")
        lines.append("")
    lines.append("## 보유시간 버킷별 net (ALL)")
    lines.append("| 버킷 | n | net평균 | net승률 |")
    lines.append("|---|--|--|--|")
    for label, s in rep["by_hold_bucket"].get("ALL", {}).items():
        lines.append(f"| {label} | {s['n']} | {s['net_avg']:+}% | {s['net_win_rate_pct']}% |")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture/net 성과 리뷰 (로컬 sqlite 전용, 호출 없음).")
    parser.add_argument("--ml-db", default=str(DEFAULT_ML_DB))
    parser.add_argument("--sel-db", default=str(DEFAULT_SEL_DB))
    parser.add_argument("--runtime-mode", default="live", help="live/paper, 빈값이면 전체")
    parser.add_argument("--fee-us", type=float, default=DEFAULT_FEE_PCT["US"])
    parser.add_argument("--fee-kr", type=float, default=DEFAULT_FEE_PCT["KR"])
    parser.add_argument("--fx-us", type=float, default=DEFAULT_FX_SPREAD_PCT["US"], help="US 환전 스프레드 왕복%(우대0.2/무우대~2)")
    parser.add_argument("--fx-kr", type=float, default=DEFAULT_FX_SPREAD_PCT["KR"])
    parser.add_argument("--output-dir", default=str(ROOT / "docs" / "reports"))
    parser.add_argument("--stamp", default=datetime.now().strftime("%Y%m%d_%H%M%S"))
    parser.add_argument("--print", action="store_true", help="markdown을 stdout에도 출력")
    args = parser.parse_args()

    fee_pct = {"US": args.fee_us, "KR": args.fee_kr}
    fx_spread_pct = {"US": args.fx_us, "KR": args.fx_kr}
    runtime_mode = args.runtime_mode or None
    closed = load_closed(Path(args.ml_db), runtime_mode)
    runup = load_runup(Path(args.sel_db))
    mfe_map = load_mfe(Path(args.ml_db))
    report = build_report(closed, runup, fee_pct, mfe_map, fx_spread_pct)

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "basis": {
            "closed_trades": len(closed),
            "net_measured": sum(1 for r in closed if str(r.get("net_basis") or "") == "measured"),
            "runtime_mode": runtime_mode or "all",
            "fee_pct": fee_pct,
            "fx_spread_pct": fx_spread_pct,
            "runup_keys": len(runup),
            "notes": [
                "로컬 sqlite 전용 — broker/API/Claude 호출 없음.",
                "net = gross pnl_pct - 왕복 수수료 - US 환전 스프레드(왕복, 환전2회). 정밀 net은 Phase 1b 백필 후 별도.",
                "runup_3d/capture는 사후 감사 라벨이므로 라이브 게이팅에 직접 사용 금지.",
            ],
        },
        "report": report,
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"capture_net_review_{args.stamp}.json"
    md_path = output_dir / f"capture_net_review_{args.stamp}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md = to_markdown(payload)
    md_path.write_text(md, encoding="utf-8")
    print(f"json: {json_path}")
    print(f"markdown: {md_path}")
    if args.print:
        print("\n" + md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
