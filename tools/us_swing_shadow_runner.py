from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.us_swing_authority import evaluate_swing_authority, load_swing_policy
from runtime.us_swing_order_handoff import ensure_handoff_schema, summarize_forward_evidence
from tools.build_us_yahoo_point_in_time import BENCHMARKS, build_ticker_frame, _read_price
from tools.us_daily_alpha_walkforward import YAHOO_FEATURES, load_yahoo_dataset
from tools.us_swing_exit_counterfactual import simulate_exit


SCHEMA_VERSION = "us_swing_shadow_v1"
_BREADTH_COLUMNS = {
    "reference_close": "REAL",
    "breadth_context_date": "TEXT",
    "prior_spy_return_pct": "REAL",
    "prior_narrow_excess_pct": "REAL",
    "prior_rsp_spy_ratio_5d_pct": "REAL",
    "prior_adv_pct": "REAL",
    "breadth_context_state": "TEXT",
}


def ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS signals (
            signal_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            feature_date TEXT NOT NULL,
            model_version TEXT NOT NULL,
            rank INTEGER NOT NULL,
            alpha_score REAL,
            predicted_net_pct REAL,
            probability REAL,
            candidate_source TEXT,
            created_at TEXT NOT NULL,
            entry_date TEXT,
            entry_price REAL,
            exit_date TEXT,
            exit_price REAL,
            entry_fx REAL,
            exit_fx REAL,
            gross_usd_pct REAL,
            gross_krw_pct REAL,
            net_krw_pct REAL,
            status TEXT NOT NULL DEFAULT 'PENDING',
            data_quality TEXT NOT NULL DEFAULT 'point_in_time',
            error TEXT,
            PRIMARY KEY(signal_date, ticker)
        );
        CREATE INDEX IF NOT EXISTS idx_us_swing_signals_status ON signals(status, signal_date);
        CREATE TABLE IF NOT EXISTS runs (
            signal_date TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            report_json TEXT NOT NULL
        );
        """
    )
    existing = {str(row[1]) for row in con.execute("PRAGMA table_info(signals)")}
    for column, sql_type in _BREADTH_COLUMNS.items():
        if column not in existing:
            con.execute(f"ALTER TABLE signals ADD COLUMN {column} {sql_type}")
    con.commit()


def classify_breadth_context(narrow_excess_pct: float | None) -> str:
    if narrow_excess_pct is None or not np.isfinite(narrow_excess_pct):
        return "MISSING"
    if narrow_excess_pct <= -0.30:
        return "NARROW"
    if narrow_excess_pct >= 0.30:
        return "BROAD"
    return "BALANCED"


def load_breadth_context(
    *,
    feature_date: str,
    breadth_path: Path,
    adv_path: Path,
) -> dict[str, Any]:
    try:
        breadth = pd.read_csv(breadth_path)
        adv = pd.read_csv(adv_path)
    except (OSError, ValueError):
        return {"breadth_context_date": feature_date, "breadth_context_state": "MISSING"}
    for frame in (breadth, adv):
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    breadth = breadth.sort_values("date")
    breadth["spy_return_pct"] = pd.to_numeric(breadth["SPY"], errors="coerce").pct_change() * 100.0
    breadth["rsp_return_pct"] = pd.to_numeric(breadth["RSP"], errors="coerce").pct_change() * 100.0
    breadth["narrow_excess_pct"] = breadth["rsp_return_pct"] - breadth["spy_return_pct"]
    breadth["ratio_full"] = pd.to_numeric(breadth["RSP"], errors="coerce") / pd.to_numeric(
        breadth["SPY"], errors="coerce"
    )
    breadth["ratio_5d_pct"] = breadth["ratio_full"].pct_change(5) * 100.0
    merged = breadth.merge(adv[["date", "adv_pct"]], on="date", how="left")
    row = merged[merged["date"].eq(str(feature_date))]
    if row.empty:
        return {"breadth_context_date": feature_date, "breadth_context_state": "MISSING"}
    record = row.iloc[-1]

    def finite(name: str) -> float | None:
        value = pd.to_numeric(pd.Series([record.get(name)]), errors="coerce").iloc[0]
        return float(value) if pd.notna(value) and np.isfinite(value) else None

    narrow = finite("narrow_excess_pct")
    return {
        "breadth_context_date": str(feature_date),
        "prior_spy_return_pct": finite("spy_return_pct"),
        "prior_narrow_excess_pct": narrow,
        "prior_rsp_spy_ratio_5d_pct": finite("ratio_5d_pct"),
        "prior_adv_pct": finite("adv_pct"),
        "breadth_context_state": classify_breadth_context(narrow),
    }


def _benchmark_frame(price_dir: Path, *, before_date: str) -> pd.DataFrame:
    output: pd.DataFrame | None = None
    for ticker in BENCHMARKS:
        path = price_dir / f"us_{ticker}.csv"
        if not path.exists():
            return pd.DataFrame()
        data = build_ticker_frame(_read_price(path))
        data = data[data["date"].astype(str) < str(before_date)]
        selected = data[["date", "momentum_5d_pct", "momentum_20d_pct", "momentum_60d_pct"]].rename(
            columns={
                "momentum_5d_pct": f"{ticker.lower()}_momentum_5d_pct",
                "momentum_20d_pct": f"{ticker.lower()}_momentum_20d_pct",
                "momentum_60d_pct": f"{ticker.lower()}_momentum_60d_pct",
            }
        )
        output = selected if output is None else output.merge(selected, on="date", how="outer")
    return output if output is not None else pd.DataFrame()


def load_candidate_features(
    *, snapshot_path: Path, price_dir: Path, session_date: str, vetoes: dict[str, str] | None = None
) -> tuple[pd.DataFrame, list[str]]:
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    if str(payload.get("market", "")).upper() != "US":
        raise ValueError("snapshot market is not US")
    if str(payload.get("session_date", "")) != str(session_date):
        raise ValueError("snapshot session date mismatch")
    benchmark = _benchmark_frame(price_dir, before_date=session_date)
    if benchmark.empty:
        raise ValueError("benchmark price history missing")
    benchmark_feature_date = str(benchmark["date"].dropna().astype(str).max())
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    veto_map = {str(key).upper(): str(value) for key, value in (vetoes or {}).items()}
    for candidate in payload.get("candidates", []):
        if not isinstance(candidate, dict):
            continue
        ticker = str(candidate.get("ticker") or "").strip().upper()
        path = price_dir / f"us_{ticker}.csv"
        if not ticker or not path.exists():
            errors.append(f"{ticker or 'UNKNOWN'}:price_missing")
            continue
        features = build_ticker_frame(_read_price(path))
        features = features[features["date"].astype(str) < str(session_date)].merge(benchmark, on="date", how="left")
        if features.empty:
            errors.append(f"{ticker}:no_point_in_time_bar")
            continue
        row = features.iloc[-1].to_dict()
        if str(row.get("date") or "") != benchmark_feature_date:
            errors.append(f"{ticker}:stale_feature_date:{row.get('date')}!=benchmark:{benchmark_feature_date}")
            continue
        for window in (5, 20, 60):
            row[f"relative_strength_qqq_{window}d_pct"] = (
                row.get(f"momentum_{window}d_pct") - row.get(f"qqq_momentum_{window}d_pct")
                if pd.notna(row.get(f"momentum_{window}d_pct")) and pd.notna(row.get(f"qqq_momentum_{window}d_pct"))
                else np.nan
            )
        row.update(
            {
                "ticker": ticker,
                "candidate_source": str(candidate.get("source") or ""),
                "news_or_earnings_flag": bool(candidate.get("news_or_earnings_flag")),
                "snapshot_data_quality": str(candidate.get("data_quality") or ""),
                "veto_reason": veto_map.get(ticker, ""),
            }
        )
        rows.append(row)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame, errors
    frame = frame.replace([np.inf, -np.inf], np.nan)
    eligible = (
        frame["close"].ge(5.0)
        & frame["dollar_volume_20d"].ge(15_000_000.0)
        & frame["change_pct"].abs().le(25.0)
    )
    return frame[eligible].copy(), errors


def score_candidates(
    train: pd.DataFrame,
    candidates: pd.DataFrame,
    *,
    seeds: list[int],
    top_k: int,
) -> tuple[pd.DataFrame, str]:
    if candidates.empty:
        return candidates.copy(), ""
    if train.empty or train["target"].nunique() < 2:
        raise ValueError("training data insufficient")
    predicted: list[np.ndarray] = []
    probabilities: list[np.ndarray] = []
    for seed in seeds:
        regressor = HistGradientBoostingRegressor(
            learning_rate=0.05, max_iter=160, max_leaf_nodes=15,
            min_samples_leaf=35, l2_regularization=1.0, random_state=seed,
        )
        classifier = HistGradientBoostingClassifier(
            learning_rate=0.05, max_iter=160, max_leaf_nodes=15,
            min_samples_leaf=35, l2_regularization=1.0, random_state=seed,
        )
        regressor.fit(train[YAHOO_FEATURES], train["net_return_pct"])
        classifier.fit(train[YAHOO_FEATURES], train["target"])
        predicted.append(regressor.predict(candidates[YAHOO_FEATURES]))
        probabilities.append(classifier.predict_proba(candidates[YAHOO_FEATURES])[:, 1])
    scored = candidates.copy()
    scored["predicted_net_pct"] = np.mean(predicted, axis=0)
    scored["probability"] = np.mean(probabilities, axis=0)
    scored["net_rank"] = scored["predicted_net_pct"].rank(pct=True)
    scored["prob_rank"] = scored["probability"].rank(pct=True)
    scored["alpha_score"] = 0.5 * scored["net_rank"] + 0.5 * scored["prob_rank"]
    scored = scored.sort_values(["alpha_score", "predicted_net_pct"], ascending=False).head(max(1, top_k)).copy()
    scored["rank"] = np.arange(1, len(scored) + 1)
    identity = f"{train['session_date'].max()}|{len(train)}|{','.join(map(str, seeds))}"
    model_version = "us_swing_5d_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    return scored, model_version


def write_signals(
    con: sqlite3.Connection,
    *,
    signal_date: str,
    scored: pd.DataFrame,
    model_version: str,
) -> int:
    created_at = datetime.now(timezone.utc).isoformat()
    con.execute("DELETE FROM signals WHERE signal_date=? AND status='PENDING'", (signal_date,))
    written = 0
    for row in scored.to_dict("records"):
        con.execute(
            """
            INSERT OR IGNORE INTO signals (
                signal_date,ticker,feature_date,model_version,rank,alpha_score,
                predicted_net_pct,probability,candidate_source,created_at,status,data_quality,
                reference_close,
                breadth_context_date,prior_spy_return_pct,prior_narrow_excess_pct,
                prior_rsp_spy_ratio_5d_pct,prior_adv_pct,breadth_context_state
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                signal_date, str(row["ticker"]), str(row["date"]), model_version, int(row["rank"]),
                float(row["alpha_score"]), float(row["predicted_net_pct"]), float(row["probability"]),
                str(row.get("candidate_source") or ""), created_at, "PENDING", "point_in_time",
                float(row.get("close")) if pd.notna(row.get("close")) else None,
                row.get("breadth_context_date"), row.get("prior_spy_return_pct"),
                row.get("prior_narrow_excess_pct"), row.get("prior_rsp_spy_ratio_5d_pct"),
                row.get("prior_adv_pct"), row.get("breadth_context_state"),
            ),
        )
        written += int(con.execute("SELECT changes()").fetchone()[0])
    con.commit()
    return written


def _fx_map_from_research_db(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    con = sqlite3.connect(path)
    try:
        rows = con.execute("SELECT date, usdkrw FROM usdkrw_daily WHERE usdkrw IS NOT NULL").fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        con.close()
    return {str(date): float(value) for date, value in rows}


def refresh_fx_map(base: dict[str, float], *, start: str, end: str) -> dict[str, float]:
    output = dict(base)
    try:
        import yfinance as yf

        raw = yf.download(
            "KRW=X", start=start, end=end, interval="1d", auto_adjust=True,
            repair=True, progress=False, threads=False, multi_level_index=False,
        )
        if raw is not None and not raw.empty:
            frame = raw.reset_index().rename(columns={"Date": "date", "Close": "usdkrw"})
            frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
            frame["usdkrw"] = pd.to_numeric(frame["usdkrw"], errors="coerce")
            output.update({str(row.date): float(row.usdkrw) for row in frame.dropna(subset=["date", "usdkrw"]).itertuples()})
    except Exception:
        pass
    return output


def _latest_fx(fx_map: dict[str, float], date: str) -> float | None:
    eligible = [key for key in fx_map if str(key) <= str(date)]
    if not eligible:
        return None
    value = fx_map[max(eligible)]
    return float(value) if np.isfinite(value) and float(value) > 100 else None


def annotate_execution_shadow(
    con: sqlite3.Connection,
    *,
    signal_date: str,
    fx_map: dict[str, float],
    policy: dict[str, Any],
    base_order_budget_krw: float = 500_000.0,
) -> dict[str, Any]:
    """Select the exact MICRO path: one slot, rank1 only, whole shares."""
    ensure_handoff_schema(con)
    micro = dict((policy.get("authority_caps") or {}).get("micro") or {})
    multiplier = float(micro.get("size_multiplier") or 0.0)
    budget = float(base_order_budget_krw) * multiplier
    fx = _latest_fx(fx_map, signal_date)
    evaluated_at = datetime.now(timezone.utc).isoformat()
    prior = con.execute(
        """SELECT signal_date,execution_shadow_exit_date FROM signals
           WHERE execution_shadow_eligible=1 AND signal_date<?
           ORDER BY signal_date DESC LIMIT 1""",
        (str(signal_date),),
    ).fetchone()
    slot_free = True
    slot_reason = ""
    if prior:
        prior_date, actual_exit = str(prior[0]), str(prior[1] or "")
        if actual_exit:
            slot_free = str(signal_date) > actual_exit
            if not slot_free:
                slot_reason = f"slot_occupied_until:{actual_exit}"
        else:
            later_sessions = con.execute(
                "SELECT COUNT(DISTINCT signal_date) FROM signals WHERE signal_date>? AND signal_date<=?",
                (prior_date, str(signal_date)),
            ).fetchone()[0]
            slot_free = int(later_sessions or 0) >= int(
                (policy.get("execution_contract") or {}).get("max_hold_sessions", 5)
            )
            if not slot_free:
                slot_reason = f"slot_occupied_pending:{prior_date}"

    rows = con.execute(
        "SELECT ticker,rank,reference_close FROM signals WHERE signal_date=? ORDER BY rank",
        (str(signal_date),),
    ).fetchall()
    selected: dict[str, Any] = {}
    for ticker, rank, reference_close in rows:
        eligible = 0
        qty = 0
        price_krw = None
        reason = "rank_outside_micro_contract"
        if int(rank) == 1:
            reference = float(reference_close) if reference_close is not None else None
            price_krw = reference * fx if reference and fx else None
            if not slot_free:
                reason = slot_reason
            elif not fx:
                reason = "fx_missing"
            elif not reference or reference <= 0:
                reason = "reference_price_missing"
            else:
                qty = int(budget // price_krw) if budget > 0 else 0
                if qty <= 0:
                    reason = "micro_budget_cannot_buy_one_share"
                else:
                    eligible = 1
                    reason = "selected_rank1_whole_share"
                    selected = {"ticker": str(ticker), "rank": 1, "qty": qty}
        con.execute(
            """UPDATE signals SET execution_shadow_eligible=?,execution_shadow_reason=?,
                execution_shadow_qty=?,execution_shadow_budget_krw=?,
                execution_shadow_entry_proxy_usd=?,execution_shadow_entry_price_krw=?,
                execution_shadow_fx=?,execution_shadow_policy=?,execution_shadow_evaluated_at=?
                WHERE signal_date=? AND ticker=?""",
            (
                eligible,
                reason,
                qty,
                budget,
                reference_close,
                price_krw,
                fx,
                "rank1_skip_v1",
                evaluated_at,
                str(signal_date),
                str(ticker),
            ),
        )
    con.commit()
    return {
        "policy": "rank1_skip_v1",
        "budget_krw": budget,
        "fx": fx,
        "slot_free": slot_free,
        "slot_reason": slot_reason,
        "selected": selected,
    }


def expected_maturity_session(signal_date: str, max_hold_sessions: int) -> str:
    """Resolve the last session in an inclusive fixed-horizon US hold."""

    count = max(1, int(max_hold_sessions or 1))
    try:
        import exchange_calendars as ec

        calendar = ec.get_calendar("XNYS")
        # Positive windows include the anchor session. A five-session
        # inclusive hold starting Friday therefore matures Thursday.
        sessions = calendar.sessions_window(str(signal_date), count)
        return str(sessions[-1].date())
    except Exception:
        current = pd.Timestamp(str(signal_date))
        observed = 1
        while observed < count:
            current += pd.Timedelta(days=1)
            if current.weekday() < 5:
                observed += 1
        return current.strftime("%Y-%m-%d")


def summarize_active_execution_shadow(
    con: sqlite3.Connection,
    *,
    price_dir: Path,
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Expose legitimate one-slot maturity instead of labelling it stale."""

    max_hold = int((policy.get("execution_contract") or {}).get("max_hold_sessions", 5) or 5)
    rows = con.execute(
        """SELECT signal_date,ticker,entry_date,execution_shadow_entry_fill_usd,
                  execution_shadow_qty,execution_shadow_reason
           FROM signals
           WHERE execution_shadow_eligible=1
             AND execution_shadow_net_krw_pct IS NULL
           ORDER BY signal_date,ticker"""
    ).fetchall()
    active: list[dict[str, Any]] = []
    for signal_date, ticker, entry_date, entry_fill, qty, reason in rows:
        observed_sessions = 0
        latest_bar_date = ""
        path = price_dir / f"us_{str(ticker).upper()}.csv"
        if path.exists():
            try:
                bars = _read_price(path)
                future = bars[bars["date"].astype(str) >= str(signal_date)].sort_values("date")
                observed_sessions = int(len(future))
                if not future.empty:
                    latest_bar_date = str(future.iloc[-1]["date"])
            except Exception:
                pass
        expected_session = expected_maturity_session(str(signal_date), max_hold)
        maturity_due = observed_sessions >= max_hold
        active.append({
            "state": "MATURITY_DUE" if maturity_due else "ACTIVE_UNMATURED",
            "signal_date": str(signal_date),
            "ticker": str(ticker),
            "entry_date": str(entry_date or signal_date),
            "entry_fill_usd": float(entry_fill) if entry_fill is not None else None,
            "qty": int(qty or 0),
            "execution_shadow_reason": str(reason or ""),
            "observed_sessions": observed_sessions,
            "max_hold_sessions": max_hold,
            "expected_maturity_session": expected_session,
            "latest_bar_date": latest_bar_date,
            "maturity_due": maturity_due,
        })
    return {
        "state": "ACTIVE_UNMATURED" if active and not any(row["maturity_due"] for row in active) else (
            "MATURITY_DUE" if active else "IDLE"
        ),
        "active_count": len(active),
        "rows": active,
    }


def mature_pending(
    con: sqlite3.Connection,
    *,
    price_dir: Path,
    fx_map: dict[str, float],
    cost_pct: float,
    entry_slippage_pct: float = 0.5,
    tp_pct: float = 0.12,
    sl_pct: float = 0.25,
) -> dict[str, int]:
    ensure_handoff_schema(con)
    pending = con.execute(
        """SELECT signal_date,ticker,execution_shadow_eligible,execution_shadow_budget_krw
           FROM signals WHERE status='PENDING' ORDER BY signal_date,ticker"""
    ).fetchall()
    matured = 0
    waiting = 0
    execution_matured = 0
    execution_unaffordable = 0
    for signal_date, ticker, execution_eligible, execution_budget in pending:
        path = price_dir / f"us_{str(ticker).upper()}.csv"
        if not path.exists():
            waiting += 1
            continue
        bars = _read_price(path)
        # signal_date is the intended entry session produced before that session opens.
        # The feature bar is the prior session, so entry is signal_date open and the
        # outcome is the fifth session close including the entry session.
        future = bars[bars["date"].astype(str) >= str(signal_date)].sort_values("date")
        if int(execution_eligible or 0) == 1 and not future.empty:
            first = future.iloc[0]
            first_fx = fx_map.get(str(first["date"])) or _latest_fx(fx_map, str(first["date"]))
            shadow_entry = float(first["open"]) * (1.0 + float(entry_slippage_pct) / 100.0)
            budget = float(execution_budget or 0.0)
            shadow_qty = int(budget // (shadow_entry * first_fx)) if budget > 0 and first_fx else 0
            if shadow_qty <= 0:
                con.execute(
                    """UPDATE signals SET execution_shadow_eligible=0,
                        execution_shadow_reason='entry_open_unaffordable',execution_shadow_qty=0,
                        execution_shadow_entry_fill_usd=?,execution_shadow_entry_price_krw=?,
                        execution_shadow_fx=? WHERE signal_date=? AND ticker=?""",
                    (
                        shadow_entry,
                        shadow_entry * first_fx if first_fx else None,
                        first_fx,
                        signal_date,
                        ticker,
                    ),
                )
                execution_eligible = 0
                execution_unaffordable += 1
            else:
                con.execute(
                    """UPDATE signals SET entry_date=COALESCE(entry_date,?),
                        entry_price=COALESCE(entry_price,?),entry_fx=COALESCE(entry_fx,?),
                        execution_shadow_reason='entry_open_whole_share_confirmed',
                        execution_shadow_qty=?,execution_shadow_entry_fill_usd=?,
                        execution_shadow_entry_price_krw=?,execution_shadow_fx=?
                       WHERE signal_date=? AND ticker=?""",
                    (
                        str(first["date"]),
                        float(first["open"]),
                        first_fx,
                        shadow_qty,
                        shadow_entry,
                        shadow_entry * first_fx,
                        first_fx,
                        signal_date,
                        ticker,
                    ),
                )
        if len(future) < 5:
            waiting += 1
            continue
        entry = future.iloc[0]
        exit_row = future.iloc[4]
        entry_fx = fx_map.get(str(entry["date"]))
        exit_fx = fx_map.get(str(exit_row["date"]))
        if not entry_fx or not exit_fx:
            waiting += 1
            continue
        entry_price = float(entry["open"])
        exit_price = float(exit_row["close"])
        gross_usd = (exit_price / entry_price - 1.0) * 100.0
        gross_krw = ((exit_price / entry_price) * (exit_fx / entry_fx) - 1.0) * 100.0
        net_krw = gross_krw - float(cost_pct)
        execution_values: tuple[Any, ...] = (None, None, None, None, None)
        if int(execution_eligible or 0) == 1:
            budget = float(execution_budget or 0.0)
            execution_entry_price = entry_price * (1.0 + float(entry_slippage_pct) / 100.0)
            qty = int(budget // (execution_entry_price * entry_fx)) if budget > 0 else 0
            if qty <= 0:
                con.execute(
                    """UPDATE signals SET execution_shadow_eligible=0,
                        execution_shadow_reason='entry_open_unaffordable',execution_shadow_qty=0
                       WHERE signal_date=? AND ticker=?""",
                    (signal_date, ticker),
                )
                execution_unaffordable += 1
            else:
                contract_exit_date, contract_exit_price, contract_reason = simulate_exit(
                    future.head(5),
                    entry_price=execution_entry_price,
                    tp_pct=tp_pct,
                    sl_pct=sl_pct,
                    tie_break="sl_first",
                )
                contract_exit_fx = fx_map.get(str(contract_exit_date)) or _latest_fx(
                    fx_map, str(contract_exit_date)
                )
                if contract_exit_fx:
                    contract_net = (
                        (contract_exit_price / execution_entry_price) * (contract_exit_fx / entry_fx) - 1.0
                    ) * 100.0 - float(cost_pct)
                    contract_pnl = qty * execution_entry_price * entry_fx * contract_net / 100.0
                    execution_values = (
                        str(contract_exit_date),
                        float(contract_exit_price),
                        str(contract_reason),
                        float(contract_net),
                        float(contract_pnl),
                    )
                    con.execute(
                        """UPDATE signals SET execution_shadow_qty=?,
                            execution_shadow_entry_fill_usd=?,execution_shadow_entry_price_krw=?
                           WHERE signal_date=? AND ticker=?""",
                        (qty, execution_entry_price, execution_entry_price * entry_fx, signal_date, ticker),
                    )
                    execution_matured += 1
        con.execute(
            """
            UPDATE signals SET entry_date=?,entry_price=?,exit_date=?,exit_price=?,entry_fx=?,exit_fx=?,
                gross_usd_pct=?,gross_krw_pct=?,net_krw_pct=?,status='MATURED',error=NULL,
                execution_shadow_exit_date=?,execution_shadow_exit_price=?,
                execution_shadow_exit_reason=?,execution_shadow_net_krw_pct=?,execution_shadow_pnl_krw=?
            WHERE signal_date=? AND ticker=? AND status='PENDING'
            """,
            (
                str(entry["date"]), entry_price, str(exit_row["date"]), exit_price, entry_fx, exit_fx,
                gross_usd, gross_krw, net_krw,
                *execution_values,
                signal_date, ticker,
            ),
        )
        matured += 1
    con.commit()
    return {
        "matured_now": matured,
        "waiting": waiting,
        "execution_matured_now": execution_matured,
        "execution_unaffordable_now": execution_unaffordable,
    }


def _block_lcb(values: np.ndarray, *, seed: int = 20260710) -> float | None:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) < 5:
        return None
    rng = np.random.default_rng(seed)
    block = min(5, len(values))
    starts = np.arange(max(1, len(values) - block + 1))
    output = []
    for _ in range(2000):
        sample: list[float] = []
        while len(sample) < len(values):
            start = int(rng.choice(starts))
            sample.extend(values[start:start + block].tolist())
        output.append(float(np.mean(sample[:len(values)])))
    return float(np.quantile(output, 0.05))


def summarize_forward(con: sqlite3.Connection) -> dict[str, Any]:
    frame = pd.read_sql_query(
        """SELECT signal_date,ticker,net_krw_pct,breadth_context_state
           FROM signals WHERE status='MATURED' AND net_krw_pct IS NOT NULL""",
        con,
    )
    if frame.empty:
        return {"sessions": 0, "matured": 0, "critical_data_errors": []}
    daily = frame.groupby("signal_date", sort=True)["net_krw_pct"].mean()
    values = daily.to_numpy(dtype=float)
    positive = float(values[values > 0].sum())
    negative = float(-values[values < 0].sum())
    ordered = np.sort(values)[::-1]
    breadth_diagnostic: dict[str, Any] = {}
    for state, group in frame.groupby(frame["breadth_context_state"].fillna("MISSING")):
        state_daily = group.groupby("signal_date")["net_krw_pct"].mean().to_numpy(dtype=float)
        state_positive = float(state_daily[state_daily > 0].sum())
        state_negative = float(-state_daily[state_daily < 0].sum())
        breadth_diagnostic[str(state)] = {
            "sessions": int(len(state_daily)),
            "signals": int(len(group)),
            "mean_net_pct": float(state_daily.mean()),
            "profit_factor": float(state_positive / state_negative) if state_negative > 0 else None,
        }
    return {
        "sessions": int(len(daily)),
        "matured": int(len(frame)),
        "mean_net_pct": float(values.mean()),
        "win_rate": float((values > 0).mean()),
        "profit_factor": float(positive / negative) if negative > 0 else None,
        "block_lcb_pct": _block_lcb(values),
        "ex_top3_days_pct": float(ordered[3:].mean()) if len(ordered) > 3 else None,
        "critical_data_errors": [],
        "breadth_context_diagnostic_only": breadth_diagnostic,
    }


def summarize_executable_forward(con: sqlite3.Connection) -> dict[str, Any]:
    return summarize_forward_evidence(con)


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate and mature US 5-session swing shadow signals")
    parser.add_argument("--session-date", required=True)
    parser.add_argument("--snapshot", default="")
    parser.add_argument("--price-dir", default=str(ROOT / "data" / "price" / "us"))
    parser.add_argument("--research-db", default=str(ROOT / "data" / "analysis" / "us_yahoo_point_in_time.db"))
    parser.add_argument("--shadow-db", default=str(ROOT / "data" / "analysis" / "us_swing_shadow.db"))
    parser.add_argument("--policy", default=str(ROOT / "config" / "us_swing_accelerated.json"))
    parser.add_argument("--historical-evidence", default=str(ROOT / "state" / "us_swing_historical_evidence.json"))
    parser.add_argument("--execution-evidence", default=str(ROOT / "state" / "us_swing_execution_evidence.json"))
    parser.add_argument("--status-output", default=str(ROOT / "state" / "us_swing_status.json"))
    parser.add_argument("--veto-file", default="")
    parser.add_argument("--breadth", default=str(ROOT / "data" / "analysis" / "us_breadth_proxy_daily.csv"))
    parser.add_argument("--adv-breadth", default=str(ROOT / "data" / "analysis" / "us_adv_dec_breadth_daily.csv"))
    parser.add_argument("--authority-mode", default=os.getenv("US_SWING_AUTHORITY_MODE", "shadow"))
    parser.add_argument("--mature-only", action="store_true")
    args = parser.parse_args()
    policy = load_swing_policy(args.policy)
    shadow_path = Path(args.shadow_db)
    shadow_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(shadow_path)
    ensure_schema(con)
    ensure_handoff_schema(con)
    price_dir = Path(args.price_dir)
    earliest_pending = con.execute("SELECT MIN(signal_date) FROM signals WHERE status='PENDING'").fetchone()[0]
    fx_start = str(earliest_pending or (datetime.now(timezone.utc) - timedelta(days=45)).date())
    fx_end = str((datetime.now(timezone.utc) + timedelta(days=2)).date())
    fx_map = refresh_fx_map(
        _fx_map_from_research_db(Path(args.research_db)), start=fx_start, end=fx_end
    )
    maturity = mature_pending(
        con,
        price_dir=price_dir,
        fx_map=fx_map,
        cost_pct=float(policy.get("cost_pct", 0.50)),
        entry_slippage_pct=float(
            (policy.get("execution_contract") or {}).get("max_entry_slippage_pct", 0.5)
        ),
        tp_pct=float((policy.get("execution_contract") or {}).get("take_profit_pct", 0.12)),
        sl_pct=float((policy.get("execution_contract") or {}).get("catastrophe_stop_pct", 0.25)),
    )
    generated = 0
    candidates_n = 0
    model_version = ""
    feature_errors: list[str] = []
    selected: list[dict[str, Any]] = []
    applied_vetoes: list[dict[str, str]] = []
    breadth_context: dict[str, Any] = {}
    execution_shadow: dict[str, Any] = {}
    active_execution_shadow: dict[str, Any] = {}
    if not args.mature_only:
        snapshot = Path(args.snapshot) if args.snapshot else ROOT / "state" / f"preopen_US_{args.session_date.replace('-', '')}.json"
        veto_path = Path(args.veto_file) if args.veto_file else ROOT / "state" / f"us_swing_veto_{args.session_date.replace('-', '')}.json"
        veto_payload = _load_json(veto_path)
        raw_vetoes = veto_payload.get("vetoes") if isinstance(veto_payload.get("vetoes"), dict) else {}
        candidates, feature_errors = load_candidate_features(
            snapshot_path=snapshot, price_dir=price_dir, session_date=args.session_date, vetoes=raw_vetoes
        )
        candidates_n = len(candidates)
        vetoed = candidates[candidates["veto_reason"].astype(str).ne("")].copy()
        applied_vetoes = [
            {"ticker": str(row["ticker"]), "reason": str(row["veto_reason"])}
            for row in vetoed.to_dict("records")
        ]
        candidates = candidates[candidates["veto_reason"].astype(str).eq("")].copy()
        research_con = sqlite3.connect(args.research_db)
        try:
            train = load_yahoo_dataset(
                research_con, horizon=5, cost_pct=float(policy.get("cost_pct", 0.50))
            )
        finally:
            research_con.close()
        scored, model_version = score_candidates(
            train,
            candidates,
            seeds=[int(value) for value in policy.get("seeds", [20260710])],
            top_k=int(policy.get("top_k", 5)),
        )
        feature_date = str(scored["date"].iloc[0]) if not scored.empty else ""
        breadth_context = load_breadth_context(
            feature_date=feature_date,
            breadth_path=Path(args.breadth),
            adv_path=Path(args.adv_breadth),
        )
        for key, value in breadth_context.items():
            scored[key] = value
        generated = write_signals(
            con, signal_date=args.session_date, scored=scored, model_version=model_version
        )
        execution_shadow = annotate_execution_shadow(
            con,
            signal_date=args.session_date,
            fx_map=fx_map,
            policy=policy,
            base_order_budget_krw=500_000.0,
        )
        selected = [
            {
                "ticker": str(row["ticker"]), "rank": int(row["rank"]),
                "predicted_net_pct": float(row["predicted_net_pct"]),
                "probability": float(row["probability"]), "feature_date": str(row["date"]),
                "breadth_context_state": str(row.get("breadth_context_state") or "MISSING"),
            }
            for row in scored.to_dict("records")
        ]
    active_execution_shadow = summarize_active_execution_shadow(
        con,
        price_dir=price_dir,
        policy=policy,
    )
    model_forward = summarize_forward(con)
    forward = summarize_executable_forward(con)
    historical = _load_json(Path(args.historical_evidence))
    execution_evidence = _load_json(Path(args.execution_evidence))
    authority = evaluate_swing_authority(
        configured_mode=args.authority_mode,
        historical_evidence=historical,
        forward_evidence=forward,
        policy=policy,
        execution_evidence=execution_evidence,
    )
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "signal_date": args.session_date,
        "model_version": model_version,
        "eligible_candidates": candidates_n,
        "new_signals": generated,
        "selected": selected,
        "applied_vetoes": applied_vetoes,
        "breadth_context": breadth_context,
        "feature_errors": feature_errors,
        "maturity": maturity,
        "execution_shadow": execution_shadow,
        "active_execution_shadow": active_execution_shadow,
        "forward_evidence": forward,
        "model_forward_diagnostic_only": model_forward,
        "historical_evidence_present": bool(historical),
        "execution_evidence_present": bool(execution_evidence),
        "authority": authority.to_dict(),
        "order_integration": "WIRED_FAIL_CLOSED_DISABLED; separate handoff, submit, and live-ack locks",
    }
    con.execute(
        "INSERT OR REPLACE INTO runs(signal_date,created_at,report_json) VALUES (?,?,?)",
        (args.session_date, report["generated_at"], json.dumps(report, ensure_ascii=False, sort_keys=True)),
    )
    con.commit()
    con.close()
    output = Path(args.status_output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(output)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
