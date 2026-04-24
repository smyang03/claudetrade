"""Command line entry point for the isolated audit lab."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import MARKETS, MARKET_DATA_DB, MARKET_DATA_DIR, RESULT_DIR, STRATEGIES, YFINANCE_DATA_DIR, AuditConfig
from .critical_flags import build_alert_plan, evaluate_critical_flags
from .data_manifest import build_manifest
from .db import init_database
from .event_engine import run_market_backtest
from .reports import write_csv_report, write_json_report, write_report_bundle
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Isolated backtest audit lab")
    parser.add_argument(
        "command",
        nargs="?",
        default="run",
        choices=["run", "init-db", "build-universe", "collect-daily"],
        help="command to execute",
    )
    parser.add_argument("--market", default="ALL", choices=["ALL", *MARKETS])
    parser.add_argument("--strategy", default="ALL")
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    parser.add_argument("--cost-model", default="realistic", choices=["none", "basic", "realistic"])
    parser.add_argument("--entry-timing", default="next_open", choices=["next_open", "same_close"])
    parser.add_argument("--entry-day-exit-policy", default="allow", choices=["allow", "defer"])
    parser.add_argument("--regime-timing", default="previous_close", choices=["previous_close", "current_close"])
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

    config = AuditConfig(
        market=args.market,
        strategy=args.strategy,
        start=args.start,
        end=args.end,
        cost_model=args.cost_model,
        entry_timing=args.entry_timing,
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
