from __future__ import annotations

"""장중 진입 shadow 사후 리뷰 — "샀다면 어땠을지" 전방 성과 재구성.

trading_bot._record_intraday_entry_shadow()가 미진입(WAIT_RECHECK/PULLBACK_WAIT/REJECT)
시점에 격리 funnel JSONL(logs/funnel/intraday_entry_shadow_<date>_<MKT>.jsonl)에 남긴
'would-enter' 스냅샷을, yfinance 분봉으로 전방 재구성해 다음을 산출한다.

- 결정 시점(would_entry_price) 이후 MFE / MAE
- +30m / +60m / 세션마감 전방 수익률
- 눌림 도달 여부(저점이 would_entry 대비 -PULLBACK_PCT 이하)
- 왕복 수수료 차감 net 이 +인 비율
- action / 시장국면(regime) 버킷별 집계

모든 입력은 로컬 JSONL + yfinance(사후 감사)뿐이며 broker/Claude 호출이 없다. 이 산출물은
shadow→enforce 전환 판단(US 우선, 표본·net+ 충족) 근거이지 라이브 게이팅 입력이 아니다.
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FUNNEL_DIR = ROOT / "logs" / "funnel"
KST = timezone(timedelta(hours=9))
DEFAULT_FEE_PCT = 0.5  # 왕복 근사(한투 미국 0.25%/편도, 우대 없음). KR도 보수적 동일 가정.
DEFAULT_PULLBACK_PCT = 0.5  # 눌림 판정 임계(would_entry 대비 -0.5%)
HORIZONS_MIN = (30, 60)


def _parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)  # funnel decided_at 은 KST naive 로 기록됨
    return dt.astimezone(timezone.utc)


def _load_shadow_rows(market: str, date_str: str) -> list[dict[str, Any]]:
    day = date_str.replace("-", "")
    rows: list[dict[str, Any]] = []
    patterns = [f"intraday_entry_shadow_{day}_{market}.jsonl"]
    if not date_str:
        patterns = [p.name for p in FUNNEL_DIR.glob(f"intraday_entry_shadow_*_{market}.jsonl")]
    for name in patterns:
        path = FUNNEL_DIR / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows


def _yf_symbol_candidates(market: str, ticker: str) -> list[str]:
    return [ticker] if market == "US" else [f"{ticker}.KS", f"{ticker}.KQ"]


def _reconstruct(market: str, rows: list[dict[str, Any]], interval: str, fee_pct: float, pullback_pct: float, sleep_sec: float) -> list[dict[str, Any]]:
    try:
        import time as _time

        import yfinance as yf
    except Exception as exc:  # pragma: no cover - 환경 의존
        print(f"[error] yfinance import 실패: {exc}", file=sys.stderr)
        return []

    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        tk = str(r.get("ticker") or "").strip()
        if tk:
            by_ticker[tk].append(r)

    out: list[dict[str, Any]] = []
    for ticker, items in by_ticker.items():
        decided = [_parse_dt(it.get("decided_at")) for it in items]
        decided = [d for d in decided if d is not None]
        if not decided:
            for it in items:
                out.append({**it, "status": "no_decided_at"})
            continue
        start = min(decided) - timedelta(hours=2)
        end = max(decided) + timedelta(days=1)
        df = None
        for sym in _yf_symbol_candidates(market, ticker):
            try:
                d = yf.download(sym, start=start.date(), end=end.date(), interval=interval, progress=False, auto_adjust=False)
            except Exception:
                d = None
            if d is not None and len(d) > 0:
                df = d
                break
        if df is None or len(df) == 0:
            for it in items:
                out.append({**it, "status": "no_data"})
            continue
        high, low, close = df["High"], df["Low"], df["Close"]
        for series_name in ("high", "low", "close"):
            s = {"high": high, "low": low, "close": close}[series_name]
            if hasattr(s, "columns"):
                if series_name == "high":
                    high = s.iloc[:, 0]
                elif series_name == "low":
                    low = s.iloc[:, 0]
                else:
                    close = s.iloc[:, 0]
        idx = df.index
        try:
            idx = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
        except Exception:
            pass

        for it in items:
            d0 = _parse_dt(it.get("decided_at"))
            entry = float(it.get("would_entry_price") or 0)
            if d0 is None or entry <= 0:
                out.append({**it, "status": "bad_input"})
                continue
            # 같은 거래일 마감까지 전방
            session_end = d0 + timedelta(hours=8)
            fwd_mask = (idx > d0) & (idx <= session_end)
            n = int(fwd_mask.sum())
            if n == 0:
                out.append({**it, "status": "no_forward_bars"})
                continue
            fwd_high = float(high[fwd_mask].max())
            fwd_low = float(low[fwd_mask].min())
            mfe = (fwd_high / entry - 1.0) * 100.0
            mae = (fwd_low / entry - 1.0) * 100.0
            pullback_hit = fwd_low <= entry * (1.0 - pullback_pct / 100.0)
            rec: dict[str, Any] = {
                **it,
                "status": "ok",
                "fwd_bars": n,
                "mfe_pct": round(mfe, 3),
                "mae_pct": round(mae, 3),
                "pullback_hit": bool(pullback_hit),
            }
            for hz in HORIZONS_MIN:
                hz_mask = (idx > d0) & (idx <= d0 + timedelta(minutes=hz))
                if int(hz_mask.sum()) > 0:
                    px = float(close[hz_mask].iloc[-1])
                    rec[f"ret_{hz}m_pct"] = round((px / entry - 1.0) * 100.0, 3)
                    rec[f"net_{hz}m_pct"] = round((px / entry - 1.0) * 100.0 - fee_pct, 3)
                else:
                    rec[f"ret_{hz}m_pct"] = None
                    rec[f"net_{hz}m_pct"] = None
            close_px = float(close[fwd_mask].iloc[-1])
            rec["ret_close_pct"] = round((close_px / entry - 1.0) * 100.0, 3)
            rec["net_close_pct"] = round((close_px / entry - 1.0) * 100.0 - fee_pct, 3)
            out.append(rec)
        try:
            _time.sleep(sleep_sec)
        except Exception:
            pass
    return out


def _agg(records: list[dict[str, Any]], key_fn) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in records:
        if r.get("status") != "ok":
            continue
        buckets[str(key_fn(r) or "?")].append(r)
    summary: dict[str, dict[str, Any]] = {}
    for k, items in sorted(buckets.items()):
        nets = [r["net_close_pct"] for r in items if r.get("net_close_pct") is not None]
        mfes = [r["mfe_pct"] for r in items if r.get("mfe_pct") is not None]
        maes = [r["mae_pct"] for r in items if r.get("mae_pct") is not None]
        net60 = [r["net_60m_pct"] for r in items if r.get("net_60m_pct") is not None]
        summary[k] = {
            "n": len(items),
            "pullback_hit_rate": round(sum(1 for r in items if r.get("pullback_hit")) / len(items), 3) if items else 0.0,
            "net_close_avg": round(mean(nets), 3) if nets else None,
            "net_close_median": round(median(nets), 3) if nets else None,
            "net_close_pos_rate": round(sum(1 for v in nets if v > 0) / len(nets), 3) if nets else None,
            "net_60m_avg": round(mean(net60), 3) if net60 else None,
            "mfe_avg": round(mean(mfes), 3) if mfes else None,
            "mae_avg": round(mean(maes), 3) if maes else None,
        }
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="장중 진입 shadow 사후 리뷰 (yfinance 전방 재구성)")
    ap.add_argument("--market", choices=["US", "KR"], default="US")
    ap.add_argument("--date", default="", help="세션일 YYYYMMDD/YYYY-MM-DD (생략 시 해당 시장 전체 파일)")
    ap.add_argument("--interval", default="5m")
    ap.add_argument("--fee", type=float, default=DEFAULT_FEE_PCT, help="왕복 수수료 %% (기본 0.5)")
    ap.add_argument("--pullback", type=float, default=DEFAULT_PULLBACK_PCT, help="눌림 판정 임계 %% (기본 0.5)")
    ap.add_argument("--sleep", type=float, default=0.4)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    rows = _load_shadow_rows(args.market, args.date)
    if not rows:
        print(f"[info] shadow 기록 없음 (market={args.market} date={args.date or 'ALL'})")
        return 0

    records = _reconstruct(args.market, rows, args.interval, args.fee, args.pullback, args.sleep)
    ok = [r for r in records if r.get("status") == "ok"]
    by_action = _agg(records, lambda r: r.get("action"))
    by_regime = _agg(records, lambda r: r.get("entry_market_regime"))

    report = {
        "market": args.market,
        "date": args.date or "ALL",
        "fee_pct": args.fee,
        "pullback_pct": args.pullback,
        "shadow_records": len(rows),
        "reconstructed_ok": len(ok),
        "status_counts": dict(_count_status(records)),
        "by_action": by_action,
        "by_regime": by_regime,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"=== 장중 진입 shadow 리뷰 [{args.market}] {args.date or 'ALL'} ===")
    print(f"shadow 기록 {len(rows)}건 → 재구성 성공 {len(ok)}건  | 수수료 왕복 {args.fee}%  눌림임계 -{args.pullback}%")
    print(f"status: {dict(_count_status(records))}")
    for title, agg in (("action별", by_action), ("국면(regime)별", by_regime)):
        print(f"\n[{title}]")
        if not agg:
            print("  (집계 가능한 ok 레코드 없음)")
            continue
        for k, v in agg.items():
            print(
                f"  {k:<18} n={v['n']:<3} 눌림도달={v['pullback_hit_rate']:.0%}  "
                f"net마감 avg={_fmt(v['net_close_avg'])} med={_fmt(v['net_close_median'])} +율={_fmt_rate(v['net_close_pos_rate'])}  "
                f"net60m={_fmt(v['net_60m_avg'])}  MFE={_fmt(v['mfe_avg'])} MAE={_fmt(v['mae_avg'])}"
            )
    print("\n주의: 사후 감사 라벨이다. 라이브 게이팅에 직접 쓰지 말 것. 전환은 US 우선·표본·net+ 충족 시.")
    return 0


def _count_status(records: list[dict[str, Any]]) -> dict[str, int]:
    c: dict[str, int] = defaultdict(int)
    for r in records:
        c[str(r.get("status") or "?")] += 1
    return c


def _fmt(v: Any) -> str:
    return f"{v:+.2f}%" if isinstance(v, (int, float)) else " n/a"


def _fmt_rate(v: Any) -> str:
    return f"{v:.0%}" if isinstance(v, (int, float)) else "n/a"


if __name__ == "__main__":
    raise SystemExit(main())
