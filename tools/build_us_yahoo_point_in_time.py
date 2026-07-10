from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS = ("SPY", "QQQ", "IWM")


def _read_price(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date").drop_duplicates("date")


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(window, min_periods=window).mean()
    loss = (-delta.clip(upper=0)).rolling(window, min_periods=window).mean()
    rs = gain / loss.replace(0, np.nan)
    output = 100.0 - 100.0 / (1.0 + rs)
    output = output.mask((loss == 0) & (gain > 0), 100.0)
    return output.mask((loss == 0) & (gain == 0), 50.0)


def build_ticker_frame(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy().reset_index(drop=True)
    close = data["close"]
    previous = close.shift(1)
    daily_return = close.pct_change()
    prior_volume20 = data["volume"].shift(1).rolling(20, min_periods=10).mean()
    ema12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    macd = ema12 - ema26
    ma20 = close.rolling(20, min_periods=20).mean()
    ma60 = close.rolling(60, min_periods=40).mean()
    std20 = close.rolling(20, min_periods=20).std()
    true_range = pd.concat(
        [(data["high"] - data["low"]), (data["high"] - previous).abs(), (data["low"] - previous).abs()],
        axis=1,
    ).max(axis=1)
    data["rsi"] = _rsi(close)
    data["bb_pct"] = (close - (ma20 - 2.0 * std20)) / (4.0 * std20).replace(0, np.nan)
    data["volume_ratio"] = data["volume"] / prior_volume20.replace(0, np.nan)
    data["macd_pct"] = macd / close * 100.0
    data["macd_signal_pct"] = macd.ewm(span=9, adjust=False, min_periods=9).mean() / close * 100.0
    data["ma20_distance_pct"] = (close / ma20 - 1.0) * 100.0
    data["ma60_distance_pct"] = (close / ma60 - 1.0) * 100.0
    data["atr_pct"] = true_range.rolling(14, min_periods=14).mean() / close * 100.0
    data["gap_pct"] = (data["open"] / previous - 1.0) * 100.0
    data["change_pct"] = daily_return * 100.0
    data["momentum_5d_pct"] = (close / close.shift(5) - 1.0) * 100.0
    data["momentum_20d_pct"] = (close / close.shift(20) - 1.0) * 100.0
    data["momentum_60d_pct"] = (close / close.shift(60) - 1.0) * 100.0
    data["from_high_20d_pct"] = (close / close.rolling(20, min_periods=20).max() - 1.0) * 100.0
    data["realized_vol_20d_pct"] = daily_return.rolling(20, min_periods=20).std() * np.sqrt(252.0) * 100.0
    data["dollar_volume_20d"] = (close * data["volume"]).rolling(20, min_periods=10).mean()
    for horizon in (1, 3, 5):
        data[f"entry_date_{horizon}d"] = data["date"].shift(-1)
        data[f"exit_date_{horizon}d"] = data["date"].shift(-horizon)
        data[f"entry_open_{horizon}d"] = data["open"].shift(-1)
        data[f"exit_close_{horizon}d"] = close.shift(-horizon)
        data[f"gross_usd_{horizon}d_pct"] = (
            data[f"exit_close_{horizon}d"] / data[f"entry_open_{horizon}d"] - 1.0
        ) * 100.0
    return data.replace([np.inf, -np.inf], np.nan)


def _benchmark_features(price_dir: Path) -> pd.DataFrame:
    output: pd.DataFrame | None = None
    for ticker in BENCHMARKS:
        data = build_ticker_frame(_read_price(price_dir / f"us_{ticker}.csv"))
        selected = data[["date", "momentum_5d_pct", "momentum_20d_pct", "momentum_60d_pct", "realized_vol_20d_pct"]].rename(
            columns={
                "momentum_5d_pct": f"{ticker.lower()}_momentum_5d_pct",
                "momentum_20d_pct": f"{ticker.lower()}_momentum_20d_pct",
                "momentum_60d_pct": f"{ticker.lower()}_momentum_60d_pct",
                "realized_vol_20d_pct": f"{ticker.lower()}_realized_vol_20d_pct",
            }
        )
        output = selected if output is None else output.merge(selected, on="date", how="outer")
    return output if output is not None else pd.DataFrame()


def _fx_history(start: str, end: str) -> pd.DataFrame:
    import yfinance as yf

    raw = yf.download(
        "KRW=X",
        start=start,
        end=end,
        interval="1d",
        auto_adjust=True,
        repair=True,
        progress=False,
        threads=False,
        multi_level_index=False,
    )
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["date", "usdkrw"])
    output = raw.reset_index().rename(columns={"Date": "date", "Close": "usdkrw"})[["date", "usdkrw"]]
    output["date"] = pd.to_datetime(output["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    output["usdkrw"] = pd.to_numeric(output["usdkrw"], errors="coerce")
    return output.dropna().drop_duplicates("date")


def build_dataset(
    *,
    decisions_db: Path,
    price_dir: Path,
    cost_pct: float,
    min_price: float,
    min_dollar_volume: float,
    max_abs_change_pct: float,
) -> tuple[pd.DataFrame, dict[str, Any], pd.DataFrame]:
    con = sqlite3.connect(decisions_db)
    try:
        anchors = pd.read_sql_query(
            """
            SELECT DISTINCT session_date, upper(ticker) AS ticker
            FROM decisions
            WHERE market='US' AND data_source='backfill' AND is_simulated=1
              AND session_date IS NOT NULL AND ticker IS NOT NULL
            ORDER BY session_date, ticker
            """,
            con,
        )
    finally:
        con.close()
    benchmark = _benchmark_features(price_dir)
    pieces: list[pd.DataFrame] = []
    missing_files: list[str] = []
    for ticker, ticker_anchors in anchors.groupby("ticker"):
        path = price_dir / f"us_{ticker}.csv"
        if not path.exists():
            missing_files.append(str(ticker))
            continue
        features = build_ticker_frame(_read_price(path))
        selected = ticker_anchors.merge(features, left_on="session_date", right_on="date", how="left")
        pieces.append(selected)
    dataset = pd.concat(pieces, ignore_index=True) if pieces else pd.DataFrame()
    dataset = dataset.merge(benchmark, on="date", how="left")
    for window in (5, 20, 60):
        dataset[f"relative_strength_qqq_{window}d_pct"] = (
            dataset[f"momentum_{window}d_pct"] - dataset[f"qqq_momentum_{window}d_pct"]
        )
    valid_dates = dataset["date"].dropna().astype(str)
    if valid_dates.empty:
        raise ValueError("no anchor rows matched the Yahoo price cache")
    start = str(valid_dates.min())
    exit_columns = [f"exit_date_{horizon}d" for horizon in (1, 3, 5)]
    valid_exits = dataset[exit_columns].stack().dropna().astype(str)
    end_date = pd.to_datetime(valid_exits.max()) + pd.Timedelta(days=5)
    fx = _fx_history(start, end_date.strftime("%Y-%m-%d"))
    fx_map = dict(zip(fx["date"], fx["usdkrw"]))
    for horizon in (1, 3, 5):
        entry_fx = dataset[f"entry_date_{horizon}d"].map(fx_map)
        exit_fx = dataset[f"exit_date_{horizon}d"].map(fx_map)
        dataset[f"fx_change_{horizon}d_pct"] = (exit_fx / entry_fx - 1.0) * 100.0
        asset_factor = 1.0 + dataset[f"gross_usd_{horizon}d_pct"] / 100.0
        fx_factor = exit_fx / entry_fx
        dataset[f"gross_krw_{horizon}d_pct"] = (asset_factor * fx_factor - 1.0) * 100.0
        dataset[f"net_krw_{horizon}d_pct"] = dataset[f"gross_krw_{horizon}d_pct"] - float(cost_pct)
    before_filter = len(dataset)
    eligible = dataset[
        dataset["close"].ge(float(min_price))
        & dataset["dollar_volume_20d"].ge(float(min_dollar_volume))
        & dataset["change_pct"].abs().le(float(max_abs_change_pct))
    ].copy()
    report = {
        "anchor_rows": int(len(anchors)),
        "matched_rows": int(dataset["close"].notna().sum()),
        "eligible_rows": int(len(eligible)),
        "sessions": int(eligible["session_date"].nunique()),
        "tickers": int(eligible["ticker"].nunique()),
        "range": [str(eligible["session_date"].min()), str(eligible["session_date"].max())],
        "missing_price_files": missing_files,
        "filter": {
            "min_price": min_price,
            "min_dollar_volume": min_dollar_volume,
            "max_abs_change_pct": max_abs_change_pct,
        },
        "cost_pct": cost_pct,
        "feature_timing": "session close D; entry next trading session open; no future bars in features",
        "label": "next-open to D+h close, converted by KRW=X then cost deducted",
        "source": "local Yahoo-adjusted OHLCV cache + live yfinance KRW=X",
    }
    return eligible, report, fx


def _write_sqlite(dataset: pd.DataFrame, report: dict[str, Any], fx: pd.DataFrame, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(output)
    try:
        dataset.to_sql("us_yahoo_point_in_time", con, if_exists="replace", index=False)
        fx.to_sql("usdkrw_daily", con, if_exists="replace", index=False)
        con.execute("CREATE INDEX IF NOT EXISTS idx_us_yahoo_date_ticker ON us_yahoo_point_in_time(session_date,ticker)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_us_yahoo_ticker_date ON us_yahoo_point_in_time(ticker,session_date)")
        con.execute("CREATE TABLE IF NOT EXISTS build_metadata (built_at TEXT, report_json TEXT)")
        con.execute("DELETE FROM build_metadata")
        con.execute(
            "INSERT INTO build_metadata VALUES (?,?)",
            (datetime.now(timezone.utc).isoformat(), json.dumps(report, ensure_ascii=False, sort_keys=True)),
        )
        con.commit()
    finally:
        con.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build leakage-controlled US Yahoo point-in-time research dataset")
    parser.add_argument("--decisions-db", default=str(ROOT / "data" / "ml" / "decisions.db"))
    parser.add_argument("--price-dir", default=str(ROOT / "data" / "price" / "us"))
    parser.add_argument("--output", default=str(ROOT / "data" / "analysis" / "us_yahoo_point_in_time.db"))
    parser.add_argument("--cost-pct", type=float, default=0.50)
    parser.add_argument("--min-price", type=float, default=5.0)
    parser.add_argument("--min-dollar-volume", type=float, default=15_000_000.0)
    parser.add_argument("--max-abs-change-pct", type=float, default=25.0)
    args = parser.parse_args()
    dataset, report, fx = build_dataset(
        decisions_db=Path(args.decisions_db),
        price_dir=Path(args.price_dir),
        cost_pct=args.cost_pct,
        min_price=args.min_price,
        min_dollar_volume=args.min_dollar_volume,
        max_abs_change_pct=args.max_abs_change_pct,
    )
    _write_sqlite(dataset, report, fx, Path(args.output))
    print(json.dumps({"output": args.output, **report}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
