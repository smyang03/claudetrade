"""
Backfill decisions.db from historical price CSVs.

Design goals:
- no lookahead in feature generation: indicators are computed from the history available up to each row
- labels are forward returns, not the rule signal itself
- backfill rows are explicitly marked with data_source='backfill' and is_simulated=1
- when available, historical daily_judgment files are used for mode / stance / confidence context
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

_ROOT = Path(__file__).parent.parent
_PRICE_DIR = _ROOT / "data" / "price"
_JUDGMENT_DIR = _ROOT / "logs" / "daily_judgment"

sys.path.insert(0, str(_ROOT))

from indicators import calc_all
from ml.db_writer import init_db, write_decision
import strategy.gap_pullback as _gap
import strategy.mean_reversion as _mr
import strategy.momentum as _mom
import strategy.volatility_breakout as _vb

try:
    from logger import get_collector_logger

    _log = get_collector_logger()
except Exception:
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    _log = logging.getLogger("ml.backfill")

_FORWARD_DAYS = (1, 3, 5)
_judgment_cache: dict[tuple[str, str], dict] = {}


def _get_conn() -> sqlite3.Connection:
    from ml.db_writer import _get_conn as _base_conn

    return _base_conn()


def _existing_backfill_keys(market: str) -> set[tuple[str, str]]:
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT ticker, session_date FROM decisions WHERE market=? AND data_source='backfill'",
                (market,),
            ).fetchall()
        return {(r[0], r[1]) for r in rows}
    except Exception:
        return set()


def _calc_forward(dates: list[str], idx: int, closes: list[float]) -> dict[int, Optional[float]]:
    base = closes[idx]
    out: dict[int, Optional[float]] = {}
    for n in _FORWARD_DAYS:
        future_idx = idx + n
        if future_idx < len(dates) and base > 0:
            out[n] = round((closes[future_idx] - base) / base * 100, 4)
        else:
            out[n] = None
    return out


def _load_judgment_context(market: str, session_date: str) -> dict:
    key = (market, session_date)
    if key in _judgment_cache:
        return _judgment_cache[key]

    context = {
        "mode": "NEUTRAL",
        "mode_score": None,
        "bull_stance": "NEUTRAL",
        "bear_stance": "NEUTRAL",
        "neut_stance": "NEUTRAL",
        "bull_conf": None,
        "bear_conf": None,
        "neut_conf": None,
        "vix": None,
        "usd_krw": None,
    }

    path = _JUDGMENT_DIR / f"{session_date.replace('-', '')}_{market}.json"
    if not path.exists():
        _judgment_cache[key] = context
        return context

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        judgments = data.get("judgments") or {}
        consensus = data.get("consensus") or {}

        bull = judgments.get("bull") or {}
        bear = judgments.get("bear") or {}
        neutral = judgments.get("neutral") or {}

        context.update(
            {
                "mode": consensus.get("mode") or "NEUTRAL",
                "mode_score": consensus.get("weighted_score"),
                "bull_stance": bull.get("stance") or "NEUTRAL",
                "bear_stance": bear.get("stance") or "NEUTRAL",
                "neut_stance": neutral.get("stance") or "NEUTRAL",
                "bull_conf": bull.get("confidence"),
                "bear_conf": bear.get("confidence"),
                "neut_conf": neutral.get("confidence"),
            }
        )

        digest = data.get("digest_metrics") or {}
        if isinstance(digest, dict):
            context["vix"] = digest.get("vix")
            context["usd_krw"] = digest.get("usd_krw")
    except Exception as e:
        _log.warning(f"[backfill] judgment parse failed {path.name}: {e}")

    _judgment_cache[key] = context
    return context


def _process_ticker(
    market: str,
    ticker: str,
    csv_path: Path,
    existing: set[tuple[str, str]],
    since: Optional[str],
    dry_run: bool,
) -> tuple[int, int, int]:
    try:
        raw = pd.read_csv(csv_path, dtype={"date": str}).sort_values("date").reset_index(drop=True)
    except Exception as e:
        _log.warning(f"[backfill] failed to load {csv_path}: {e}")
        return 0, 0, 0

    if len(raw) < 61:
        return 0, 0, 0

    try:
        sig_df = calc_all(raw)
    except Exception as e:
        _log.warning(f"[backfill] calc_all failed {ticker}: {e}")
        return 0, 0, 0

    if "date" not in sig_df.columns:
        sig_df = sig_df.reset_index()

    dates = sig_df["date"].tolist()
    closes = sig_df["close"].tolist()

    inserted = 0
    skipped_existing = 0
    skipped_no_forward = 0

    for i, session_date in enumerate(dates):
        if since and session_date < since:
            continue
        if (ticker, session_date) in existing:
            skipped_existing += 1
            continue

        row = sig_df.iloc[i]
        fwd = _calc_forward(dates, i, closes)

        ctx = _load_judgment_context(market, session_date)
        mode = ctx["mode"] or "NEUTRAL"
        conf_candidates = [x for x in (ctx["bull_conf"], ctx["bear_conf"], ctx["neut_conf"]) if isinstance(x, (int, float))]
        avg_conf = sum(conf_candidates) / len(conf_candidates) if conf_candidates else 0.6

        mr_params = _mr.params(mode, avg_conf, market=market)
        vb_params = _vb.params(mode, conf=avg_conf, market=market)
        mom_params = _mom.params(mode, avg_conf, market=market)
        gap_params = _gap.params(mode, avg_conf, market=market)

        mr_fired = bool(_mr.signal(sig_df, i, mr_params))
        vb_fired = bool(_vb.signal(sig_df, i, vb_params))
        mom_fired = bool(_mom.signal(sig_df, i, mom_params))
        gap_fired = bool(_gap.signal(sig_df, i, gap_params))

        any_signal = mr_fired or vb_fired or mom_fired or gap_fired
        decision = "BUY_SIGNAL" if any_signal else "NO_SIGNAL"
        strategy_used = None
        if any_signal:
            names = []
            if mr_fired:
                names.append("mean_reversion")
            if vb_fired:
                names.append("volatility_breakout")
            if mom_fired:
                names.append("momentum")
            if gap_fired:
                names.append("gap_pullback")
            strategy_used = ",".join(names)

        def _g(col: str) -> Optional[float]:
            v = row.get(col)
            return None if pd.isna(v) else float(v)

        rsi_val = _g("rsi") or 50.0
        bb_val = _g("bb_pct") or 50.0

        vb_target = None
        vb_close_miss = None
        if i > 0:
            prev = sig_df.iloc[i - 1]
            prev_range = float(prev.get("high", 0)) - float(prev.get("low", 0))
            vb_target = float(prev.get("open", 0)) + prev_range * float(vb_params.get("k", 0.45))
            vb_close_miss = round((_g("close") or 0.0) - vb_target, 4)

        mom_diag = _mom.diagnostics(sig_df, i, mom_params)

        gap_min = float(gap_params.get("gap_min", 0.010))
        gap_val = (_g("gap_pct") or 0.0) / 100.0
        vol_avg20 = _g("vol_avg20") or 1.0
        volume = float(row.get("volume", 0))
        gap_vol_ratio = volume / vol_avg20 if vol_avg20 > 0 else 0.0
        gap_pullback_ok = int(float(row.get("low", 0)) >= float(row.get("open", 0)) * 0.995)

        record = {
            "market": market,
            "ticker": ticker,
            "session_date": session_date,
            "mode": mode,
            "mode_score": ctx["mode_score"],
            "bull_stance": ctx["bull_stance"],
            "bear_stance": ctx["bear_stance"],
            "neut_stance": ctx["neut_stance"],
            "bull_conf": ctx["bull_conf"],
            "bear_conf": ctx["bear_conf"],
            "neut_conf": ctx["neut_conf"],
            "vix": ctx["vix"],
            "usd_krw": ctx["usd_krw"],
            "price": _g("close"),
            "rsi": _g("rsi"),
            "bb_pct": _g("bb_pct"),
            "vol_ratio": _g("vol_ratio"),
            "macd": _g("macd"),
            "macd_signal": _g("macd_signal"),
            "ma20": _g("ma20"),
            "ma60": _g("ma60"),
            "atr": _g("atr"),
            "gap_pct": _g("gap_pct"),
            "change_pct": _g("change_pct"),
            "mr_rsi_thr": mr_params.get("rsi_thr", 32),
            "mr_bb_thr": mr_params.get("bb_thr", 20),
            "mr_rsi_miss": round(rsi_val - float(mr_params.get("rsi_thr", 32)), 2),
            "mr_bb_miss": round(bb_val - float(mr_params.get("bb_thr", 20)), 2),
            "mr_vol_ok": int((_g("vol_ratio") or 1.0) < 2.5),
            "mr_ma_ok": int((_g("close") or 0.0) > (_g("ma60") or 0.0) * float(mr_params.get("ma60_thr", 0.90))),
            "mr_fired": int(mr_fired),
            "vb_target": vb_target,
            "vb_close_miss": vb_close_miss,
            "vb_vol_ok": int((_g("vol_ratio") or 0.0) > float(vb_params.get("vol_mult", 1.6))),
            "vb_fired": int(vb_fired),
            "mom_ma_ok": int(mom_diag.get("ma_ok", False)),
            "mom_macd_ok": int(mom_diag.get("macd_ok", False)),
            "mom_vol_ok": int(mom_diag.get("vol_ok", False)),
            "mom_high_ok": int(mom_diag.get("high_ok", False)),
            "mom_fired": int(mom_fired),
            "gap_gap_miss": round(gap_val - gap_min, 4),
            "gap_vol_ok": int(gap_vol_ratio > float(gap_params.get("vol_mult", 1.5))),
            "gap_pullback_ok": gap_pullback_ok,
            "gap_fired": int(gap_fired),
            "decision": decision,
            "strategy_used": strategy_used,
            "data_source": "backfill",
            "is_simulated": 1,
        }

        if dry_run:
            print(
                f"[DRY] {market} {ticker:8s} {session_date} {decision:12s} "
                f"mode={mode:14s} f1d={_fmt(fwd.get(1))}"
            )
            inserted += 1
            continue

        decision_id = write_decision(record)
        if decision_id <= 0:
            _log.warning(f"[backfill] write failed {ticker} {session_date}")
            continue

        if fwd.get(1) is not None or fwd.get(3) is not None or fwd.get(5) is not None:
            try:
                with _get_conn() as conn:
                    conn.execute(
                        "UPDATE decisions SET forward_1d=?, forward_3d=?, forward_5d=? WHERE id=?",
                        (fwd.get(1), fwd.get(3), fwd.get(5), decision_id),
                    )
            except Exception as e:
                _log.warning(f"[backfill] forward update failed id={decision_id}: {e}")
        else:
            skipped_no_forward += 1

        inserted += 1
        existing.add((ticker, session_date))

    return inserted, skipped_existing, skipped_no_forward


def run(
    market: Optional[str] = None,
    since: Optional[str] = None,
    ticker_filter: Optional[str] = None,
    dry_run: bool = False,
) -> None:
    if not dry_run:
        init_db()

    markets = [market] if market else ["US", "KR"]
    total_inserted = 0
    total_skipped = 0

    for mkt in markets:
        mkt_dir = _PRICE_DIR / mkt.lower()
        if not mkt_dir.exists():
            _log.warning(f"[backfill] missing directory: {mkt_dir}")
            continue

        csv_files = sorted(mkt_dir.glob(f"{mkt.lower()}_*.csv"))
        existing = _existing_backfill_keys(mkt)
        _log.info(f"[backfill] {mkt}: csv={len(csv_files)} existing={len(existing)}")

        for csv_path in csv_files:
            ticker = csv_path.stem[len(mkt.lower()) + 1 :]
            if ticker_filter and ticker != ticker_filter:
                continue

            ins, sk_existing, sk_no_forward = _process_ticker(
                mkt, ticker, csv_path, existing, since, dry_run
            )
            total_inserted += ins
            total_skipped += sk_existing + sk_no_forward
            if ins > 0:
                _log.info(f"[backfill] {mkt} {ticker}: +{ins} rows (skip {sk_existing})")

    _log.info(f"[backfill] complete inserted={total_inserted} skipped={total_skipped}")


def _fmt(v: Optional[float]) -> str:
    return f"{v:+.2f}%" if v is not None else "N/A"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill decisions.db from historical price data")
    parser.add_argument("--market", choices=["KR", "US"], default=None)
    parser.add_argument("--since", default=None, metavar="YYYY-MM-DD")
    parser.add_argument("--ticker", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run(
        market=args.market,
        since=args.since,
        ticker_filter=args.ticker,
        dry_run=args.dry_run,
    )
