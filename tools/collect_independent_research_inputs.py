"""Dedicated research input collection; no broker, trading settings or legacy cache writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from runtime.independent_research_strategies import CONTRACTS, number


def utcnow():
    return datetime.now(timezone.utc)


def calendar_context(now):
    import exchange_calendars as ec
    from tools.canary_materializer import _next_session
    cal = ec.get_calendar("XNYS")
    days = cal.sessions_in_range((now - timedelta(days=550)).date().isoformat(), now.date().isoformat())
    completed = [str(d.date()) for d in days if cal.session_close(d).to_pydatetime() <= now]
    if len(completed) < 253:
        raise ValueError("calendar_history_short")
    return completed[-253:], _next_session("US", now)


def normalize_prices(frame, expected, observed_at):
    """Provider-adjusted prices are features observed NOW, never historical fills."""
    if "Adj Close" not in frame.columns or "Close" not in frame.columns:
        raise ValueError("adjusted_close_missing")
    by = {}
    for index, row in frame.iterrows():
        day = index.date().isoformat()
        if day not in expected:
            continue  # excludes today's still-forming bar
        if day in by:
            raise ValueError("duplicate_price_session")
        by[day] = {"session": day, "known_at": observed_at,
                   "adjusted_close": number(row["Adj Close"], True),
                   "raw_close": number(row["Close"], True),
                   "dividends": number(row.get("Dividends", 0)),
                   "stock_splits": number(row.get("Stock Splits", 0)),
                   "capital_gains": number(row.get("Capital Gains", 0))}
    if set(by) != set(expected):
        raise ValueError(f"missing_expected_sessions:{len(set(expected) - set(by))}")
    return {"price_basis": "split_and_distribution_adjusted", "source": "Yahoo/Adj Close via yfinance",
            "validation_scope": "provider_basis_positive_finite_complete_calendar_not_independent_price_audit",
            "bars": [by[day] for day in expected]}


def fetch_prices(ticker):
    import yfinance as yf
    return yf.Ticker(ticker).history(period="2y", interval="1d", auto_adjust=False,
                                     actions=True, repair=False, timeout=20)


def finnhub_key():
    key = os.getenv("FINNHUB_API_KEY") or os.getenv("FINNHUB_KEY")
    if key:
        return key
    from dotenv import dotenv_values
    for path in (ROOT / ".env.live", ROOT / ".env"):
        if path.exists():
            values = dotenv_values(path)
            key = values.get("FINNHUB_API_KEY") or values.get("FINNHUB_KEY")
            if key:
                return key
    return None


def fetch_fundamentals(now):
    key = finnhub_key()
    if not key:
        raise ValueError("finnhub_key_missing")
    start = (now - timedelta(days=7)).date().isoformat()
    end = (now + timedelta(days=21)).date().isoformat()
    url = f"https://finnhub.io/api/v1/calendar/earnings?from={start}&to={end}"
    request = urllib.request.Request(url, headers={"X-Finnhub-Token": key})
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = json.loads(response.read())
    if not isinstance(raw, dict) or not isinstance(raw.get("earningsCalendar"), list):
        raise ValueError("invalid_finnhub_response")
    return {"source": "finnhub/earnings-calendar", "observed_at": utcnow().isoformat(),
            "from": start, "to": end, "raw_response": raw,
            "publication_time_verified": False, "eps_basis_verified": False}


def atomic_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def archive(directory, payload):
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    path = directory / (digest + ".json")
    directory.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("x", encoding="utf-8") as fh:
            fh.write(encoded.decode("utf-8"))
    return {"path": str(path), "sha256": digest}


def collect(output, archive_dir):
    now = utcnow()
    prices, errors, references = {}, {}, {}
    expected, entry = [], None
    try:
        expected, entry = calendar_context(now)
    except Exception as exc:
        errors["calendar"] = type(exc).__name__  # never serialize credential-bearing exceptions
    if expected:
        for ticker in CONTRACTS["r_multiasset_trend_v1"]["universe"]:
            try:
                frame = fetch_prices(ticker)
                prices[ticker] = normalize_prices(frame, expected, utcnow().isoformat())
            except Exception as exc:
                errors[ticker] = type(exc).__name__
    fundamental_status = {"status": "BLOCKED", "reason": "guidance_and_comparable_basis_missing"}
    try:
        raw = fetch_fundamentals(now)
        references["finnhub"] = archive(archive_dir / "fundamentals", raw)
        rows = raw["raw_response"]["earningsCalendar"]
        fundamental_status.update(calendar_rows=len(rows),
            eps_estimates=sum(r.get("epsEstimate") is not None for r in rows),
            revenue_estimates=sum(r.get("revenueEstimate") is not None for r in rows),
            revenue_actuals=sum(r.get("revenueActual") is not None for r in rows),
            snapshot_observed_at=raw["observed_at"], source="finnhub/earnings-calendar")
    except Exception as exc:
        errors["fundamentals"] = type(exc).__name__
    legacy = ROOT / "data/shadow/earnings_pit_ledger.jsonl"
    if legacy.exists():
        try:
            rows = [json.loads(line) for line in legacy.read_text(encoding="utf-8").splitlines() if line.strip()]
            # Preserve original first_seen_at; importing now does not imply pre-announcement knowledge.
            references["legacy_eps"] = archive(archive_dir / "legacy_eps", {"source": str(legacy), "rows": rows})
            fundamental_status["legacy_eps_observations"] = len(rows)
        except (OSError, ValueError) as exc:
            errors["legacy_eps"] = type(exc).__name__
    observed = utcnow().isoformat()
    snapshot = {"schema_version": "independent_research_inputs_v1",
                "source": "independent_collector_v1:yfinance+finnhub", "observed_at": observed,
                "cutoff_at": observed, "asof_session": expected[-1] if expected else None,
                "entry_session": entry, "prices": prices, "earnings": [],
                "earnings_input_status": fundamental_status, "collection_errors": errors,
                "evidence_archives": references, "historical_use": "FEATURE_HISTORY_OBSERVED_NOW_NOT_BACKFILL_TRADES"}
    references["prices"] = archive(archive_dir / "prices", {"observed_at": observed, "prices": prices})
    atomic_json(output, snapshot)  # replace stale success with current failure too (fail closed)
    print(f"[RESEARCH INPUT] ETF {len(prices)}/4 asof={snapshot['asof_session']} "
          f"earnings_calendar={fundamental_status.get('calendar_rows', 0)} errors={errors}")
    return snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "data/shadow/independent_research_inputs_v1.json")
    parser.add_argument("--archive-dir", type=Path, default=ROOT / "data/shadow/independent_research_sources")
    args = parser.parse_args()
    collect(args.output, args.archive_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
