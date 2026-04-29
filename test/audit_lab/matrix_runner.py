"""Collected-data matrix runner for strategy and entry-model audits."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import ANALYSIS_WINDOWS, ENTRY_MODEL_DEFAULTS, ENTRY_MODELS, MARKETS, MARKET_DATA_DB, RESULT_DIR, STRATEGIES
from .critical_flags import build_alert_plan, evaluate_critical_flags
from .db import connect, init_database, persist_backtest_result
from .event_engine import calc_stats, run_market_backtest
from .market_data_adapter import (
    available_collected_tickers,
    collected_manifest_rows,
    collected_universe_group_map,
    latest_collected_end_date,
    load_collected_price_frame,
)
from .reports import write_report_bundle
from .strategy_policy import allowed_universe_groups, policy_reason_rows


ProgressFunc = Callable[[str], None]


def _split_csv_choice(value: str | list[str] | tuple[str, ...], choices: tuple[str, ...]) -> list[str]:
    if isinstance(value, (list, tuple)):
        raw = [str(item) for item in value]
    else:
        raw = [part.strip() for part in str(value or "ALL").split(",") if part.strip()]
    if not raw or any(part.upper() == "ALL" for part in raw):
        return list(choices)
    valid = set(choices)
    invalid = [part for part in raw if part not in valid]
    if invalid:
        raise ValueError(f"invalid choice: {invalid}; valid={sorted(valid)}")
    return raw


def _split_market_choice(value: str) -> list[str]:
    value_u = str(value or "ALL").upper()
    if value_u == "ALL":
        return list(MARKETS)
    if value_u not in MARKETS:
        raise ValueError(f"invalid market: {value}")
    return [value_u]


def _window_names(value: str | list[str] | tuple[str, ...]) -> list[str]:
    return _split_csv_choice(value, tuple(ANALYSIS_WINDOWS.keys()))


def _window_bounds(name: str, market: str, *, db_path: Path, min_quality: str) -> tuple[str, str]:
    if name not in ANALYSIS_WINDOWS:
        raise ValueError(f"unknown analysis window: {name}")
    start, end = ANALYSIS_WINDOWS[name]
    latest = latest_collected_end_date(market, db_path=db_path, min_quality=min_quality)
    if not end:
        end = latest
    return start, end


def _entry_timing_for_model(entry_model: str) -> str:
    return "same_close" if entry_model == "same_close" else "next_open"


def _held0_stats(trades: list[dict]) -> dict:
    held0 = [trade for trade in trades if int(trade.get("held_days", 0) or 0) == 0]
    pnl = [float(trade.get("net_pnl_pct", 0.0) or 0.0) for trade in held0]
    losses = [value for value in pnl if value < 0]
    return {
        "held_days_0_count": len(held0),
        "held_days_0_avg_return": round(sum(pnl) / len(pnl), 6) if pnl else 0.0,
        "held_days_0_loss_rate": round(len(losses) / len(pnl) * 100.0, 3) if pnl else 0.0,
    }


def _enrich_trades(
    trades: list[dict],
    *,
    analysis_window: str,
    entry_model: str,
    group_map: dict[str, str],
) -> list[dict]:
    enriched: list[dict] = []
    for trade in trades:
        row = dict(trade)
        ticker = str(row.get("ticker") or row.get("symbol") or "")
        row["analysis_window"] = analysis_window
        row["entry_model"] = entry_model
        row["universe_group"] = group_map.get(ticker, "unknown")
        row["data_source"] = "yfinance_collected"
        enriched.append(row)
    return enriched


def _summary_row(
    *,
    run_id: str,
    market: str,
    strategy: str,
    entry_model: str,
    analysis_window: str,
    data_source: str,
    universe_group: str,
    ticker_count: int,
    trades: list[dict],
) -> dict:
    stats = calc_stats(trades)
    return {
        "run_id": run_id,
        "market": market,
        "strategy": strategy,
        "entry_model": entry_model,
        "analysis_window": analysis_window,
        "data_source": data_source,
        "universe_group": universe_group,
        "ticker_count": ticker_count,
        "regime": "ALL",
        "year": None,
        **stats,
        **_held0_stats(trades),
    }


def _held0_flags(stats: dict, *, min_count: int = 10) -> list[dict]:
    if int(stats.get("held_days_0_count", 0) or 0) < min_count:
        return []
    if float(stats.get("held_days_0_loss_rate", 0.0) or 0.0) < 60.0:
        return []
    if float(stats.get("held_days_0_avg_return", 0.0) or 0.0) >= 0.0:
        return []
    return [
        {
            "code": "HELD_DAY0_LOSS_CLUSTER",
            "severity": "critical",
            "message": "진입 당일 종료 거래의 손실 집중도가 높음",
            "metric": stats["held_days_0_loss_rate"],
            "threshold": 60.0,
        }
    ]


def _grouped_summary_rows(
    *,
    run_id: str,
    market: str,
    strategy: str,
    entry_model: str,
    analysis_window: str,
    ticker_count: int,
    trades: list[dict],
) -> list[dict]:
    groups = sorted({str(trade.get("universe_group") or "unknown") for trade in trades})
    rows: list[dict] = []
    for group in groups:
        group_trades = [trade for trade in trades if str(trade.get("universe_group") or "unknown") == group]
        rows.append(
            _summary_row(
                run_id=run_id,
                market=market,
                strategy=strategy,
                entry_model=entry_model,
                analysis_window=analysis_window,
                data_source="yfinance_collected",
                universe_group=group,
                ticker_count=ticker_count,
                trades=group_trades,
            )
        )
    return rows


def run_collected_matrix(
    *,
    market: str = "ALL",
    strategy: str = "ALL",
    entry_models: str | list[str] | tuple[str, ...] = "ALL",
    analysis_windows: str | list[str] | tuple[str, ...] = "official_2018",
    cost_model: str = "realistic",
    ticker_limit: int = 0,
    min_quality: str = "C",
    min_trades: int = 30,
    policy_name: str = "none",
    db_path: Path = MARKET_DATA_DB,
    output_dir: Path = RESULT_DIR,
    progress: ProgressFunc | None = None,
    progress_interval: int = 10,
) -> dict:
    progress = progress or (lambda _message: None)
    db_path = Path(db_path)
    output_dir = Path(output_dir)
    init_database(db_path)

    markets = _split_market_choice(market)
    strategies = _split_csv_choice(strategy, STRATEGIES)
    models = _split_csv_choice(entry_models, ENTRY_MODELS)
    windows = _window_names(analysis_windows)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    summary_rows: list[dict] = []
    flag_rows: list[dict] = []
    trade_rows: list[dict] = []
    manifest_rows: list[dict] = []
    total_jobs = len(markets) * len(strategies) * len(models) * len(windows)
    job = 0

    progress(
        "수집데이터 매트릭스 시작 | "
        f"시장={market} 전략={strategy} 진입모델={','.join(models)} 구간={','.join(windows)} 정책={policy_name}"
    )

    for mkt in markets:
        market_manifest = collected_manifest_rows(mkt, db_path=db_path, min_quality=min_quality)
        manifest_rows.extend(market_manifest)
        base_tickers = available_collected_tickers(mkt, db_path=db_path, min_quality=min_quality, limit=ticker_limit)
        group_map = collected_universe_group_map(mkt, db_path=db_path, min_quality=min_quality)
        if not base_tickers:
            progress(f"수집데이터 없음 | 시장={mkt} min_quality={min_quality}")
            continue

        for strat in strategies:
            for model in models:
                allowed_groups = allowed_universe_groups(policy_name, market=mkt, strategy=strat, entry_model=model)
                if allowed_groups == ():
                    progress(f"정책 제외 | 시장={mkt} 전략={strat} 진입모델={model} 정책={policy_name}")
                    continue
                tickers = (
                    base_tickers
                    if allowed_groups is None
                    else [ticker for ticker in base_tickers if group_map.get(ticker, "unknown") in allowed_groups]
                )
                if not tickers:
                    progress(
                        "정책 대상 종목 없음 | "
                        f"시장={mkt} 전략={strat} 진입모델={model} 그룹={allowed_groups or 'ALL'}"
                    )
                    continue
                for window in windows:
                    job += 1
                    start, end = _window_bounds(window, mkt, db_path=db_path, min_quality=min_quality)
                    combo_run_id = f"BT_{stamp}_{job:04d}"
                    progress(
                        "매트릭스 작업 시작 | "
                        f"작업={job}/{total_jobs} 시장={mkt} 전략={strat} 진입모델={model} "
                        f"구간={window} 기간={start or '전체'}~{end or '전체'} "
                        f"종목={len(tickers)} 그룹={allowed_groups or 'ALL'}"
                    )

                    result = run_market_backtest(
                        market=mkt,
                        strategy=strat,
                        tickers=tickers,
                        cost_model_name=cost_model,
                        start=start,
                        end=end,
                        entry_timing=_entry_timing_for_model(model),
                        entry_model=model,
                        max_entry_gap_pct=float(ENTRY_MODEL_DEFAULTS["gap_filter_max_gap_pct"]),
                        pullback_limit_pct=float(ENTRY_MODEL_DEFAULTS["pullback_limit_pct"]),
                        price_loader=lambda market_arg, ticker_arg: load_collected_price_frame(
                            market_arg,
                            ticker_arg,
                            db_path=db_path,
                        ),
                        progress=progress,
                        progress_interval=progress_interval,
                    )
                    enriched_trades = _enrich_trades(
                        result.get("trades", []),
                        analysis_window=window,
                        entry_model=model,
                        group_map=group_map,
                    )
                    overall = _summary_row(
                        run_id=combo_run_id,
                        market=mkt,
                        strategy=strat,
                        entry_model=model,
                        analysis_window=window,
                        data_source="yfinance_collected",
                        universe_group="ALL",
                        ticker_count=len(tickers),
                        trades=enriched_trades,
                    )
                    group_rows = _grouped_summary_rows(
                        run_id=combo_run_id,
                        market=mkt,
                        strategy=strat,
                        entry_model=model,
                        analysis_window=window,
                        ticker_count=len(tickers),
                        trades=enriched_trades,
                    )
                    flags = evaluate_critical_flags(overall, min_trades=min_trades)
                    flags.extend(_held0_flags(overall))
                    alert_plan = build_alert_plan(flags)

                    summary_rows.extend([overall, *group_rows])
                    trade_rows.extend(enriched_trades)
                    for flag in flags:
                        flag_rows.append(
                            {
                                "run_id": combo_run_id,
                                "market": mkt,
                                "strategy": strat,
                                "entry_model": model,
                                "analysis_window": window,
                                **flag,
                            }
                        )

                    run_info = {
                        "run_id": combo_run_id,
                        "market": mkt,
                        "strategy": strat,
                        "data_start": start,
                        "data_end": end,
                        "cost_model": cost_model,
                        "entry_model": model,
                        "params": {
                            "policy": policy_name,
                            "allowed_universe_groups": list(allowed_groups or ()),
                            "analysis_window": window,
                            "min_quality": min_quality,
                            "ticker_count": len(tickers),
                            "gap_filter_max_gap_pct": ENTRY_MODEL_DEFAULTS["gap_filter_max_gap_pct"],
                            "pullback_limit_pct": ENTRY_MODEL_DEFAULTS["pullback_limit_pct"],
                        },
                    }
                    with connect(db_path) as conn:
                        persist_backtest_result(
                            conn,
                            run_info=run_info,
                            trades=enriched_trades,
                            metrics=[overall, *group_rows],
                            flags=flags,
                        )

                    progress(
                        "매트릭스 작업 완료 | "
                        f"작업={job}/{total_jobs} 시장={mkt} 전략={strat} 진입모델={model} "
                        f"구간={window} 거래={overall['n_trades']} PF={overall['profit_factor']} "
                        f"플래그={len(flags)} critical={alert_plan['critical_count']}"
                    )

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "phase": "2-4_collected_matrix",
        "market": market,
        "strategy": strategy,
        "entry_models": models,
        "analysis_windows": windows,
        "cost_model": cost_model,
        "min_quality": min_quality,
        "policy": policy_name,
        "policy_rules": policy_reason_rows(policy_name),
        "ticker_limit": ticker_limit,
        "min_trades": min_trades,
        "manifest_rows": manifest_rows,
        "summary_rows": summary_rows,
        "flag_rows": flag_rows,
        "trade_rows": trade_rows,
        "alert_plan": build_alert_plan(flag_rows),
    }
    payload["output_paths"] = write_report_bundle(payload, output_dir)
    progress(f"수집데이터 매트릭스 완료 | 요약={len(summary_rows)} 거래={len(trade_rows)} 플래그={len(flag_rows)}")
    return payload
