"""Command line entry point for the isolated audit lab."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import ENTRY_MODELS, MARKETS, MARKET_DATA_DB, MARKET_DATA_DIR, RESULT_DIR, STRATEGIES, YFINANCE_DATA_DIR, AuditConfig
from .critical_flags import build_alert_plan, evaluate_critical_flags
from .data_manifest import build_manifest
from .db import init_database
from .event_engine import run_market_backtest
from .intraday_collector import YFinanceIntradayCollector, intraday_results_to_dicts
from .intraday_diagnostics import run_intraday_entry_diagnostics, write_intraday_diagnostic_bundle
from .intraday_entry_models import INTRADAY_ENTRY_MODELS
from .intraday_file_importer import discover_intraday_files, import_intraday_files, import_results_to_dicts
from .intraday_probe import probe_intraday_capability, write_intraday_capability
from .intraday_simulator import run_market_intraday_entry_backtest
from .intraday_targets import (
    allowed_intraday_universe_groups,
    build_intraday_target_rows,
    unique_target_symbols_by_market,
    write_intraday_target_files,
)
from .market_data_adapter import available_collected_tickers, collected_universe_group_map
from .network_diag import diagnose_network
from .matrix_runner import run_collected_matrix
from .reports import write_csv_report, write_json_report, write_report_bundle
from .strategy_policy import policy_names
from .universe import build_live_universe, write_universe_manifest
from .walk_forward import run_walk_forward
from .yfinance_collector import YFinanceDailyCollector, results_to_dicts


ProgressFunc = Callable[[str], None]


def _split_choice(value: str, choices: tuple[str, ...]) -> list[str]:
    value_u = str(value or "ALL").upper()
    if value_u == "ALL":
        return list(choices)
    return [value_u]


def _strategies(value: str) -> list[str]:
    if str(value or "ALL").upper() == "ALL":
        return list(STRATEGIES)
    return [str(value)]


def _intraday_entry_models(value: str) -> list[str]:
    raw = [part.strip() for part in str(value or "ALL").split(",") if part.strip()]
    if not raw or any(part.upper() == "ALL" for part in raw):
        return list(INTRADAY_ENTRY_MODELS)
    invalid = [part for part in raw if part not in INTRADAY_ENTRY_MODELS]
    if invalid:
        raise ValueError(f"invalid intraday entry model: {invalid}; valid={list(INTRADAY_ENTRY_MODELS)}")
    return raw


def setup_progress_logger(output_dir: Path, progress_log: str = "") -> tuple[ProgressFunc, Path]:
    log_path = Path(progress_log) if progress_log else output_dir / "logs" / "audit_progress.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("audit_lab.progress")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8-sig")
    file_handler.setFormatter(formatter)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    return logger.info, log_path


def run_audit(config: AuditConfig, progress: ProgressFunc | None = None, progress_interval: int = 10) -> dict:
    markets = _split_choice(config.market, MARKETS)
    strategies = _strategies(config.strategy)
    progress = progress or (lambda _message: None)
    progress(
        "감사 실행 시작 | "
        f"시장={config.market} 전략={config.strategy} 기간={config.start or '전체'}~{config.end or '전체'} "
        f"비용모델={config.cost_model} 종목제한={config.ticker_limit or '전체'}"
    )
    progress("데이터 매니페스트 생성 시작")
    manifest = build_manifest(config.market, ticker_limit=config.ticker_limit)
    progress(
        "데이터 매니페스트 생성 완료 | "
        f"시장수={len(manifest.get('summary', {}))} 종목수={len(manifest.get('tickers', []))}"
    )
    summary_rows: list[dict] = []
    flag_rows: list[dict] = []
    trade_rows: list[dict] = []
    walk_forward_rows: list[dict] = []
    total_jobs = len(markets) * len(strategies)
    job = 0

    for market in markets:
        for strategy in strategies:
            job += 1
            progress(f"전략 작업 시작 | 작업={job}/{total_jobs} 시장={market} 전략={strategy}")
            result = run_market_backtest(
                market=market,
                strategy=strategy,
                ticker_limit=config.ticker_limit,
                cost_model_name=config.cost_model,
                start=config.start,
                end=config.end,
                regime_timing=config.regime_timing,
                entry_timing=config.entry_timing,
                entry_model=config.entry_model,
                entry_day_exit_policy=config.entry_day_exit_policy,
                progress=progress,
                progress_interval=progress_interval,
            )
            stats = result["stats"]
            trade_rows.extend(result.get("trades", []))
            wf_rows: list[dict] = []
            if config.start and config.end:
                progress(f"워크포워드 검증 준비 | 시장={market} 전략={strategy}")
                wf_rows = run_walk_forward(
                    market=market,
                    strategy=strategy,
                    start=config.start,
                    end=config.end,
                    ticker_limit=config.ticker_limit,
                    cost_model_name=config.cost_model,
                    regime_timing=config.regime_timing,
                    entry_timing=config.entry_timing,
                    entry_model=config.entry_model,
                    entry_day_exit_policy=config.entry_day_exit_policy,
                    progress=progress,
                    progress_interval=progress_interval,
                )
                walk_forward_rows.extend(wf_rows)
            flags = evaluate_critical_flags(stats, walk_forward_rows=wf_rows, min_trades=config.min_trades)
            alert_plan = build_alert_plan(flags)
            summary = {
                "market": market,
                "strategy": strategy,
                **stats,
                "flags": len(flags),
                "critical_count": alert_plan["critical_count"],
                "claude_audit_candidate": alert_plan["claude_audit_candidate"],
            }
            summary_rows.append(summary)
            for flag in flags:
                flag_rows.append({"market": market, "strategy": strategy, **flag})
            progress(
                "전략 작업 완료 | "
                f"작업={job}/{total_jobs} 시장={market} 전략={strategy} "
                f"거래={stats['n_trades']} PF={stats['profit_factor']} 플래그={len(flags)}"
            )

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "phase": "1-4",
        "market": config.market,
        "strategy": config.strategy,
        "start": config.start,
        "end": config.end,
        "cost_model": config.cost_model,
        "entry_timing": config.entry_timing,
        "entry_model": config.entry_model,
        "entry_day_exit_policy": config.entry_day_exit_policy,
        "regime_timing": config.regime_timing,
        "ticker_limit": config.ticker_limit,
        "min_trades": config.min_trades,
        "manifest": manifest,
        "summary_rows": summary_rows,
        "flag_rows": flag_rows,
        "trade_rows": trade_rows,
        "walk_forward_rows": walk_forward_rows,
        "alert_plan": build_alert_plan(flag_rows),
    }
    progress("리포트 저장 시작")
    payload["output_paths"] = write_report_bundle(payload, config.output_dir)
    progress(f"리포트 저장 완료 | {payload['output_paths']}")
    progress("감사 실행 완료")
    return payload


def _build_universe_command(args: argparse.Namespace) -> dict:
    markets = _split_choice(args.market, MARKETS)
    output_root = Path(args.market_data_dir) / "universe"
    paths: dict[str, str] = {}
    counts: dict[str, int] = {}
    for market in markets:
        members = build_live_universe(market, recent_files=args.recent_files)
        path = write_universe_manifest(members, output_root / f"live_universe_{market}.json")
        paths[market] = str(path)
        counts[market] = len(members)
    return {"markets": markets, "counts": counts, "paths": paths}


def _collect_daily_command(args: argparse.Namespace, progress: ProgressFunc) -> dict:
    markets = _split_choice(args.market, MARKETS)
    members = []
    for market in markets:
        members.extend(build_live_universe(market, recent_files=args.recent_files))
    if args.ticker_limit and args.ticker_limit > 0:
        members = members[: args.ticker_limit]

    collector = YFinanceDailyCollector(
        data_dir=Path(args.data_dir),
        db_path=Path(args.db_path),
        sleep_seconds=args.sleep_seconds,
        max_retries=args.max_retries,
        storage_format=args.storage_format,
        progress=progress,
    )
    results = collector.collect(members, period=args.period, auto_adjust=not args.no_auto_adjust)
    rows = results_to_dicts(results)
    output_dir = Path(args.market_data_dir) / "collection_reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = write_json_report({"results": rows}, output_dir, f"daily_collection_{stamp}")
    csv_path = write_csv_report(rows, output_dir, f"daily_collection_{stamp}")
    return {
        "requested": len(rows),
        "success": sum(1 for row in rows if row.get("status") == "ok"),
        "failed": sum(1 for row in rows if row.get("status") != "ok"),
        "json": str(json_path),
        "csv": str(csv_path),
    }


def _collect_intraday_command(args: argparse.Namespace, progress: ProgressFunc) -> dict:
    markets = _split_choice(args.market, MARKETS)
    intervals = [part.strip() for part in args.intervals.split(",") if part.strip()]
    symbols_by_market: dict[str, list[str]] = {}
    explicit_symbols = [part.strip() for part in args.symbols.split(",") if part.strip()]
    for market in markets:
        symbols = explicit_symbols or available_collected_tickers(
            market,
            db_path=Path(args.db_path),
            min_quality=args.min_quality,
            timeframe="daily",
            limit=args.ticker_limit,
        )
        symbols_by_market[market] = symbols

    collector = YFinanceIntradayCollector(
        data_dir=Path(args.data_dir),
        db_path=Path(args.db_path),
        sleep_seconds=args.sleep_seconds,
        max_retries=args.max_retries,
        storage_format=args.storage_format,
        progress=progress,
    )
    period = "730d" if args.period == "max" else args.period
    all_rows: list[dict] = []
    for market, symbols in symbols_by_market.items():
        for interval in intervals:
            results = collector.collect(
                market=market,
                symbols=symbols,
                interval=interval,
                period=period,
                auto_adjust=not args.no_auto_adjust,
            )
            all_rows.extend(intraday_results_to_dicts(results))

    output_dir = Path(args.market_data_dir) / "collection_reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = write_json_report({"results": all_rows}, output_dir, f"intraday_collection_{stamp}")
    csv_path = write_csv_report(all_rows, output_dir, f"intraday_collection_{stamp}")
    return {
        "requested": len(all_rows),
        "success": sum(1 for row in all_rows if row.get("status") == "ok"),
        "failed": sum(1 for row in all_rows if row.get("status") != "ok"),
        "json": str(json_path),
        "csv": str(csv_path),
    }


def _import_intraday_command(args: argparse.Namespace) -> dict:
    if not args.input_path:
        return {"status": "input_path_required", "exit_code": 2}
    files = discover_intraday_files(Path(args.input_path))
    if not files:
        return {"status": "no_intraday_files_found", "exit_code": 1, "input_path": args.input_path}

    import_market = args.market if args.market != "ALL" else "US"
    results = import_intraday_files(
        files,
        market=import_market,
        timeframe=args.timeframe,
        db_path=Path(args.db_path),
        data_dir=Path(args.data_dir),
        storage_format=args.storage_format,
    )
    rows = import_results_to_dicts(results)
    output_dir = Path(args.market_data_dir) / "collection_reports"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = write_json_report({"results": rows}, output_dir, f"intraday_import_{stamp}")
    csv_path = write_csv_report(rows, output_dir, f"intraday_import_{stamp}")
    return {
        "status": "completed",
        "exit_code": 0,
        "import_market": import_market,
        "requested": len(rows),
        "success": sum(1 for row in rows if row.get("status") == "ok"),
        "failed": sum(1 for row in rows if row.get("status") != "ok"),
        "json": str(json_path),
        "csv": str(csv_path),
    }


def _selected_intraday_tickers(args: argparse.Namespace, *, market: str, strategy: str) -> list[str]:
    tickers = available_collected_tickers(
        market,
        db_path=Path(args.db_path),
        min_quality=args.min_quality,
        timeframe=args.timeframe,
        limit=0,
    )
    groups = allowed_intraday_universe_groups(args.policy, market=market, strategy=strategy)
    if groups is not None:
        group_map = collected_universe_group_map(market, db_path=Path(args.db_path), min_quality=args.min_quality)
        allowed = set(groups)
        tickers = [ticker for ticker in tickers if group_map.get(ticker) in allowed]
    if args.ticker_limit and args.ticker_limit > 0:
        tickers = tickers[: args.ticker_limit]
    return tickers


def _run_intraday_entry_command(args: argparse.Namespace, progress: ProgressFunc) -> dict:
    markets = _split_choice(args.market, MARKETS)
    strategies = _strategies(args.strategy)
    models = _intraday_entry_models(args.intraday_entry_model)
    summary_rows: list[dict] = []
    trade_rows: list[dict] = []
    target_rows: list[dict] = []
    error_rows: list[dict] = []

    for market in markets:
        for strategy in strategies:
            tickers = _selected_intraday_tickers(args, market=market, strategy=strategy)
            if not tickers:
                error_rows.append(
                    {
                        "market": market,
                        "strategy": strategy,
                        "timeframe": args.timeframe,
                        "policy": args.policy,
                        "reason": "NO_TICKERS_AFTER_POLICY_OR_QUALITY_FILTER",
                        "min_quality": args.min_quality,
                    }
                )
            target_rows.append(
                {
                    "market": market,
                    "strategy": strategy,
                    "timeframe": args.timeframe,
                    "policy": args.policy,
                    "ticker_count": len(tickers),
                    "symbols": ",".join(tickers),
                }
            )
            for model in models:
                result = run_market_intraday_entry_backtest(
                    market=market,
                    strategy=strategy,
                    tickers=tickers,
                    intraday_entry_model=model,
                    timeframe=args.timeframe,
                    cost_model_name=args.cost_model,
                    start=args.start,
                    end=args.end,
                    progress=progress,
                    progress_interval=args.progress_interval,
                )
                stats = result["stats"]
                summary_rows.append(
                    {
                        "market": market,
                        "strategy": strategy,
                        "entry_model": model,
                        "timeframe": args.timeframe,
                        "policy": args.policy,
                        "ticker_count": len(tickers),
                        **stats,
                    }
                )
                trade_rows.extend(result.get("trades", []))
                for error in result.get("error_rows", []):
                    error_rows.append({"market": market, "strategy": strategy, "entry_model": model, **error})

    payload = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "phase": "intraday_entry",
        "market": args.market,
        "strategy": args.strategy,
        "entry_model": args.intraday_entry_model,
        "timeframe": args.timeframe,
        "policy": args.policy,
        "summary_rows": summary_rows,
        "trade_rows": trade_rows,
        "target_rows": target_rows,
        "error_rows": error_rows,
        "flag_rows": [],
    }
    payload["output_paths"] = write_report_bundle(payload, Path(args.output_dir))
    return payload


def _export_intraday_targets_command(args: argparse.Namespace) -> dict:
    rows = build_intraday_target_rows(
        policy_name=args.policy,
        market=args.market,
        strategy=args.strategy,
        db_path=Path(args.db_path),
        min_quality=args.min_quality,
        ticker_limit=args.ticker_limit,
    )
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"intraday_targets_{args.policy}_{stamp}"
    output_dir = Path(args.market_data_dir) / "intraday_targets"
    paths = write_intraday_target_files(rows, output_dir=output_dir, name=name)
    symbols_by_market = unique_target_symbols_by_market(rows)
    return {
        "policy": args.policy,
        "rows": len(rows),
        "unique_symbols": sum(len(symbols) for symbols in symbols_by_market.values()),
        "symbols_by_market": symbols_by_market,
        "paths": paths,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Isolated backtest audit lab")
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=[
            "run",
            "init-db",
            "build-universe",
            "collect-daily",
            "collect-intraday",
            "diagnose-network",
            "export-intraday-targets",
            "import-intraday",
            "import-run-intraday",
            "run-intraday-diagnostics",
            "run-collected",
            "run-intraday-entry",
            "probe-intraday",
        ],
        help="command to execute",
    )
    parser.add_argument("--market", default="ALL", choices=["ALL", *MARKETS])
    parser.add_argument("--strategy", default="ALL")
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    parser.add_argument("--cost-model", default="realistic", choices=["none", "basic", "realistic"])
    parser.add_argument("--entry-timing", default="next_open", choices=["next_open", "same_close"])
    parser.add_argument("--entry-model", default="next_open", help=f"entry model, comma list, or ALL. valid={','.join(ENTRY_MODELS)}")
    parser.add_argument("--entry-day-exit-policy", default="allow", choices=["allow", "defer"])
    parser.add_argument("--regime-timing", default="previous_close", choices=["previous_close", "current_close"])
    parser.add_argument("--windows", default="official_2018", help="analysis window, comma list, or ALL")
    parser.add_argument("--min-quality", default="C", choices=["A", "B", "C", "FAIL"])
    parser.add_argument("--policy", default="none", choices=policy_names())
    parser.add_argument("--ticker-limit", type=int, default=0)
    parser.add_argument("--min-trades", type=int, default=30)
    parser.add_argument("--output-dir", default=str(RESULT_DIR))
    parser.add_argument("--market-data-dir", default=str(MARKET_DATA_DIR))
    parser.add_argument("--data-dir", default=str(YFINANCE_DATA_DIR))
    parser.add_argument("--db-path", default=str(MARKET_DATA_DB))
    parser.add_argument("--recent-files", type=int, default=5)
    parser.add_argument("--period", default="max")
    parser.add_argument("--storage-format", default="parquet", choices=["parquet", "csv"])
    parser.add_argument("--sleep-seconds", type=float, default=0.5)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--no-auto-adjust", action="store_true")
    parser.add_argument("--progress-log", default="", help="progress log path; defaults to output-dir/logs/audit_progress.log")
    parser.add_argument("--progress-interval", type=int, default=10, help="ticker interval for progress logging")
    parser.add_argument("--symbols", default="", help="comma-separated symbols for probe-intraday")
    parser.add_argument("--intervals", default="5m,15m", help="comma-separated intervals for probe-intraday")
    parser.add_argument("--timeframe", default="5m", choices=["5m", "15m", "30m", "60m"])
    parser.add_argument("--intraday-entry-model", default="opening_range_reclaim", help=f"intraday entry model, comma list, or ALL. valid={','.join(INTRADAY_ENTRY_MODELS)}")
    parser.add_argument("--intraday-output", default="", help="output json path for probe-intraday")
    parser.add_argument("--input-path", default="", help="file or directory for import-intraday")
    parser.add_argument("--opening-minutes", type=int, default=30)
    parser.add_argument("--deadline-minutes", type=int, default=180)
    parser.add_argument("--max-gap-pct", type=float, default=1.5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "init-db":
        path = init_database(Path(args.db_path))
        print(f"db_initialized: {path}")
        return 0

    if args.command == "build-universe":
        result = _build_universe_command(args)
        print("universe_built")
        for market, count in result["counts"].items():
            print(f"{market}: {count} symbols -> {result['paths'][market]}")
        return 0

    if args.command == "collect-daily":
        progress, log_path = setup_progress_logger(Path(args.market_data_dir), args.progress_log)
        progress(f"진행 로그 파일 | {log_path}")
        result = _collect_daily_command(args, progress)
        print("daily_collection_completed")
        print(f"progress_log: {log_path}")
        for key, value in result.items():
            print(f"{key}: {value}")
        return 0

    if args.command == "collect-intraday":
        progress, log_path = setup_progress_logger(Path(args.market_data_dir), args.progress_log)
        progress(f"진행 로그 파일 | {log_path}")
        result = _collect_intraday_command(args, progress)
        print("intraday_collection_completed")
        print(f"progress_log: {log_path}")
        for key, value in result.items():
            print(f"{key}: {value}")
        return 0

    if args.command == "diagnose-network":
        result = diagnose_network()
        print("network_diagnosis_completed")
        print(f"python_executable: {result['python_executable']}")
        print(f"all_ok: {result['all_ok']}")
        print(f"blocked_by_os_policy: {result['blocked_by_os_policy']}")
        print(f"recommendation: {result['recommendation']}")
        for check in result["checks"]:
            print(
                "check: "
                f"url={check['url']} status={check['status']} "
                f"status_code={check['status_code']} error={check['error_type']} {check['error_msg']}"
            )
        return 0

    if args.command == "export-intraday-targets":
        result = _export_intraday_targets_command(args)
        print("intraday_targets_exported")
        print(f"policy: {result['policy']}")
        print(f"rows: {result['rows']}")
        print(f"unique_symbols: {result['unique_symbols']}")
        for market, symbols in result["symbols_by_market"].items():
            print(f"{market}: {len(symbols)} symbols")
        for key, path in result["paths"].items():
            print(f"{key}: {path}")
        return 0

    if args.command == "import-intraday":
        result = _import_intraday_command(args)
        if result["status"] == "input_path_required":
            print("input_path_required")
            return int(result["exit_code"])
        if result["status"] == "no_intraday_files_found":
            print(f"no_intraday_files_found: {result['input_path']}")
            return int(result["exit_code"])
        print("intraday_import_completed")
        for key, value in result.items():
            if key not in {"status", "exit_code"}:
                print(f"{key}: {value}")
        return int(result["exit_code"])

    if args.command == "import-run-intraday":
        progress, log_path = setup_progress_logger(Path(args.output_dir), args.progress_log)
        progress(f"진행 로그 파일 | {log_path}")
        import_result = _import_intraday_command(args)
        if import_result["status"] == "input_path_required":
            print("input_path_required")
            return int(import_result["exit_code"])
        if import_result["status"] == "no_intraday_files_found":
            print(f"no_intraday_files_found: {import_result['input_path']}")
            return int(import_result["exit_code"])
        payload = _run_intraday_entry_command(args, progress)
        payload["intraday_import"] = import_result
        print("intraday_import_and_backtest_completed")
        print(f"progress_log: {log_path}")
        for key, value in import_result.items():
            if key not in {"status", "exit_code"}:
                print(f"import_{key}: {value}")
        for key, path in payload.get("output_paths", {}).items():
            print(f"{key}: {path}")
        return 0

    if args.command == "run-collected":
        output_dir = Path(args.output_dir)
        progress, log_path = setup_progress_logger(output_dir, args.progress_log)
        progress(f"진행 로그 파일 | {log_path}")
        payload = run_collected_matrix(
            market=args.market,
            strategy=args.strategy,
            entry_models=args.entry_model,
            analysis_windows=args.windows,
            cost_model=args.cost_model,
            ticker_limit=args.ticker_limit,
            min_quality=args.min_quality,
            min_trades=args.min_trades,
            policy_name=args.policy,
            db_path=Path(args.db_path),
            output_dir=output_dir,
            progress=progress,
            progress_interval=args.progress_interval,
        )
        print("collected_matrix_completed")
        print(f"progress_log: {log_path}")
        for key, path in payload.get("output_paths", {}).items():
            print(f"{key}: {path}")
        return 0

    if args.command == "run-intraday-entry":
        output_dir = Path(args.output_dir)
        progress, log_path = setup_progress_logger(output_dir, args.progress_log)
        progress(f"진행 로그 파일 | {log_path}")
        payload = _run_intraday_entry_command(args, progress)
        print("intraday_entry_backtest_completed")
        print(f"progress_log: {log_path}")
        for key, path in payload.get("output_paths", {}).items():
            print(f"{key}: {path}")
        return 0

    if args.command == "run-intraday-diagnostics":
        output_dir = Path(args.output_dir)
        progress, log_path = setup_progress_logger(output_dir, args.progress_log)
        progress(f"진행 로그 파일 | {log_path}")
        markets = _split_choice(args.market, MARKETS)
        strategies = _strategies(args.strategy)
        models = _intraday_entry_models(args.intraday_entry_model)
        payloads: list[dict] = []
        summary_rows: list[dict] = []
        detail_rows: list[dict] = []
        error_rows: list[dict] = []
        for market in markets:
            for strategy in strategies:
                tickers = _selected_intraday_tickers(args, market=market, strategy=strategy)
                if not tickers:
                    error_rows.append(
                        {
                            "market": market,
                            "strategy": strategy,
                            "reason": "NO_TICKERS_AFTER_POLICY_OR_QUALITY_FILTER",
                            "policy": args.policy,
                            "timeframe": args.timeframe,
                            "min_quality": args.min_quality,
                        }
                    )
                for model in models:
                    payload = run_intraday_entry_diagnostics(
                        market=market,
                        strategy=strategy,
                        tickers=tickers,
                        intraday_entry_model=model,
                        timeframe=args.timeframe,
                        start=args.start,
                        end=args.end,
                        db_path=Path(args.db_path),
                        opening_minutes=args.opening_minutes,
                        deadline_minutes=args.deadline_minutes,
                        max_gap_pct=args.max_gap_pct,
                        progress=progress,
                        progress_interval=args.progress_interval,
                    )
                    payloads.append(payload)
                    for row in payload.get("summary_rows", []):
                        summary_rows.append({"market": market, "strategy": strategy, "entry_model": model, **row})
                    detail_rows.extend(payload.get("diagnostic_rows", []))
                    error_rows.extend(payload.get("error_rows", []))
        combined = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "phase": "intraday_entry_diagnostics_combined",
            "market": args.market,
            "strategy": args.strategy,
            "entry_model": args.intraday_entry_model,
            "timeframe": args.timeframe,
            "policy": args.policy,
            "payloads": payloads,
            "summary_rows": summary_rows,
            "diagnostic_rows": detail_rows,
            "error_rows": error_rows,
        }
        combined["output_paths"] = write_intraday_diagnostic_bundle(combined, output_dir)
        print("intraday_diagnostics_completed")
        print(f"progress_log: {log_path}")
        for key, path in combined.get("output_paths", {}).items():
            print(f"{key}: {path}")
        return 0

    if args.command == "probe-intraday":
        markets = _split_choice(args.market, MARKETS)
        symbols = [part.strip() for part in args.symbols.split(",") if part.strip()]
        if not symbols:
            for market in markets:
                symbols.extend(
                    available_collected_tickers(
                        market,
                        db_path=Path(args.db_path),
                        min_quality=args.min_quality,
                        limit=args.ticker_limit or 5,
                    )
                )
        intervals = [part.strip() for part in args.intervals.split(",") if part.strip()]
        rows = probe_intraday_capability(symbols, intervals=intervals)
        output_path = Path(args.intraday_output) if args.intraday_output else Path(args.market_data_dir) / "intraday_capability.json"
        path = write_intraday_capability(rows, output_path)
        print("intraday_probe_completed")
        print(f"symbols: {len(symbols)}")
        print(f"rows: {len(rows)}")
        print(f"output: {path}")
        return 0

    config = AuditConfig(
        market=args.market,
        strategy=args.strategy,
        start=args.start,
        end=args.end,
        cost_model=args.cost_model,
        entry_timing=args.entry_timing,
        entry_model=args.entry_model,
        entry_day_exit_policy=args.entry_day_exit_policy,
        regime_timing=args.regime_timing,
        ticker_limit=args.ticker_limit,
        min_trades=args.min_trades,
        output_dir=Path(args.output_dir),
    )
    progress, log_path = setup_progress_logger(config.output_dir, args.progress_log)
    progress(f"진행 로그 파일 | {log_path}")
    payload = run_audit(config, progress=progress, progress_interval=args.progress_interval)
    print("audit_lab completed")
    print(f"progress_log: {log_path}")
    for key, path in payload.get("output_paths", {}).items():
        print(f"{key}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
