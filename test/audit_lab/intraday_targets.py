"""Build intraday data collection targets from audited strategy policies."""

from __future__ import annotations

from pathlib import Path

from .config import MARKETS, MARKET_DATA_DB, MARKET_DATA_DIR, STRATEGIES
from .market_data_adapter import collected_manifest_rows
from .reports import write_csv_report, write_json_report
from .strategy_policy import POLICIES


def _split_market(value: str) -> list[str]:
    value_u = str(value or "ALL").upper()
    if value_u == "ALL":
        return list(MARKETS)
    if value_u not in MARKETS:
        raise ValueError(f"invalid market: {value}")
    return [value_u]


def _split_strategy(value: str) -> list[str]:
    value_s = str(value or "ALL")
    if value_s.upper() == "ALL":
        return list(STRATEGIES)
    if value_s not in STRATEGIES:
        raise ValueError(f"invalid strategy: {value}")
    return [value_s]


def allowed_intraday_universe_groups(policy_name: str, *, market: str, strategy: str) -> tuple[str, ...] | None:
    """Return policy-approved universe groups for an intraday validation run.

    The intraday simulator validates a daily strategy with intraday entry
    confirmation, so it needs the union of approved groups across the policy's
    daily entry-model rules.
    """

    policy = POLICIES.get(str(policy_name or "none"))
    if policy is None:
        raise ValueError(f"unknown policy: {policy_name}")
    if not policy:
        return None
    groups: list[str] = []
    market_u = market.upper()
    for rule in policy:
        if rule.market == market_u and rule.strategy == strategy:
            groups.extend(rule.universe_groups)
    return tuple(dict.fromkeys(groups))


def build_intraday_target_rows(
    *,
    policy_name: str = "profit_guard_v2",
    market: str = "ALL",
    strategy: str = "ALL",
    db_path: Path = MARKET_DATA_DB,
    min_quality: str = "C",
    ticker_limit: int = 0,
) -> list[dict]:
    """Build rows describing which symbols need intraday data collection."""

    markets = _split_market(market)
    strategies = _split_strategy(strategy)
    policy = POLICIES.get(str(policy_name or "none"))
    if policy is None:
        raise ValueError(f"unknown policy: {policy_name}")

    rows: list[dict] = []
    selected_symbols: dict[str, set[str]] = {mkt: set() for mkt in markets}
    selected_rows: dict[str, set[str]] = {mkt: set() for mkt in markets}

    if not policy:
        for mkt in markets:
            manifest = collected_manifest_rows(mkt, db_path=db_path, min_quality=min_quality, timeframe="daily")
            for item in manifest:
                symbol = str(item["symbol"])
                if ticker_limit and len(selected_symbols[mkt]) >= ticker_limit and symbol not in selected_symbols[mkt]:
                    continue
                selected_symbols[mkt].add(symbol)
                rows.append(
                    {
                        "policy": policy_name,
                        "market": mkt,
                        "strategy": ",".join(strategies),
                        "daily_entry_model": "ALL",
                        "symbol": symbol,
                        "collection_symbol": symbol,
                        "universe_group": str(item.get("universe_group") or "unknown"),
                        "quality_grade": str(item.get("quality_grade") or ""),
                        "daily_start_date": str(item.get("start_date") or ""),
                        "daily_end_date": str(item.get("end_date") or ""),
                        "daily_row_count": int(item.get("row_count") or 0),
                        "reason": "policy=none: all collected daily symbols are eligible for audit only.",
                    }
                )
        return sorted(rows, key=lambda row: (row["market"], row["symbol"], row["strategy"], row["daily_entry_model"]))

    for rule in policy:
        if rule.market not in markets or rule.strategy not in strategies:
            continue
        manifest = collected_manifest_rows(rule.market, db_path=db_path, min_quality=min_quality, timeframe="daily")
        allowed_groups = set(rule.universe_groups)
        for item in manifest:
            symbol = str(item["symbol"])
            group = str(item.get("universe_group") or "unknown")
            if group not in allowed_groups:
                continue
            if ticker_limit and len(selected_symbols[rule.market]) >= ticker_limit and symbol not in selected_symbols[rule.market]:
                continue
            row_key = f"{symbol}|{rule.strategy}|{rule.entry_model}"
            if row_key in selected_rows[rule.market]:
                continue
            selected_symbols[rule.market].add(symbol)
            selected_rows[rule.market].add(row_key)
            rows.append(
                {
                    "policy": policy_name,
                    "market": rule.market,
                    "strategy": rule.strategy,
                    "daily_entry_model": rule.entry_model,
                    "symbol": symbol,
                    "collection_symbol": symbol,
                    "universe_group": group,
                    "quality_grade": str(item.get("quality_grade") or ""),
                    "daily_start_date": str(item.get("start_date") or ""),
                    "daily_end_date": str(item.get("end_date") or ""),
                    "daily_row_count": int(item.get("row_count") or 0),
                    "reason": rule.reason,
                }
            )

    return sorted(rows, key=lambda row: (row["market"], row["strategy"], row["daily_entry_model"], row["symbol"]))


def unique_target_symbols_by_market(rows: list[dict]) -> dict[str, list[str]]:
    symbols: dict[str, set[str]] = {}
    for row in rows:
        market = str(row.get("market") or "").upper()
        symbol = str(row.get("collection_symbol") or row.get("symbol") or "").upper()
        if not market or not symbol:
            continue
        symbols.setdefault(market, set()).add(symbol)
    return {market: sorted(items) for market, items in sorted(symbols.items())}


def write_intraday_target_files(rows: list[dict], *, output_dir: Path = MARKET_DATA_DIR / "intraday_targets", name: str) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "json": str(write_json_report({"targets": rows}, output_dir, name)),
        "csv": str(write_csv_report(rows, output_dir, name)),
    }
    for market, symbols in unique_target_symbols_by_market(rows).items():
        path = output_dir / f"{name}_{market}_symbols.txt"
        path.write_text("\n".join(symbols) + ("\n" if symbols else ""), encoding="utf-8")
        paths[f"{market.lower()}_symbols_txt"] = str(path)
    return paths
