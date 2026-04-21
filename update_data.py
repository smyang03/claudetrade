"""
Daily data refresh entrypoint.

Recommended schedule:
- KR open: 08:30
- KR close: 16:00
- US open: 22:00
- US close: 07:00
"""

import argparse
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from logger import get_trading_logger

log = get_trading_logger()


def run_kr_update():
    today = date.today().strftime("%Y-%m-%d")
    log.info("=== KR data update start ===")

    log.info("[1/5] KR price update")
    try:
        import pandas as pd

        from phase1_trainer.price_collector import collect_kr_incremental

        end_dt = pd.Timestamp(
            date.today() if datetime.now().hour >= 16 else date.today() - timedelta(days=1)
        )
        start_dt = pd.Timestamp(date.today() - timedelta(days=500))
        collect_kr_incremental(start_dt, end_dt)
    except Exception as e:
        log.error(f"KR price update failed: {e}")

    log.info("[2/5] KR news update")
    try:
        from phase1_trainer.kr_news_collector import collect_day

        collect_day(today)
    except Exception as e:
        log.error(f"KR news update failed: {e}")

    log.info("[3/5] KR supplement update")
    try:
        from phase1_trainer.supplement_collector import collect_kr_supplement

        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        collect_kr_supplement(yesterday)
    except Exception as e:
        log.error(f"KR supplement update failed: {e}")

    log.info("[4/5] KR ML forward return update")
    try:
        from ml.forward_updater import run as forward_run

        forward_run(market="KR")
    except Exception as e:
        log.error(f"KR forward_updater failed: {e}")

    log.info("[5/5] KR ticker_selection_log forward return update")
    try:
        import ticker_selection_db as tsdb

        stats = tsdb.update_forward_returns(market="KR")
        log.info(
            "[ticker_selection_log KR] "
            f"pending={stats['pending']} updated={stats['updated']} "
            f"skipped={stats['skipped']} missing_csv={stats['missing_csv']}"
        )
    except Exception as e:
        log.error(f"KR ticker_selection_log updater failed: {e}")

    log.info("=== KR data update done ===")


def run_us_update():
    today = date.today().strftime("%Y-%m-%d")
    log.info("=== US data update start ===")

    log.info("[1/5] US price update")
    try:
        import pandas as pd

        from phase1_trainer.price_collector import collect_us_incremental

        end_dt = pd.Timestamp(date.today())
        start_dt = pd.Timestamp(date.today() - timedelta(days=500))
        collect_us_incremental(start_dt, end_dt)
    except Exception as e:
        log.error(f"US price update failed: {e}")

    log.info("[2/5] US news update")
    try:
        from phase1_trainer.us_news_collector import collect_day

        collect_day(today)
    except Exception as e:
        log.error(f"US news update failed: {e}")

    log.info("[3/5] US supplement update")
    try:
        from phase1_trainer.supplement_collector import collect_us_supplement

        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        collect_us_supplement(yesterday)
    except Exception as e:
        log.error(f"US supplement update failed: {e}")

    log.info("[4/5] US ML forward return update")
    try:
        from ml.forward_updater import run as forward_run

        forward_run(market="US")
    except Exception as e:
        log.error(f"US forward_updater failed: {e}")

    log.info("[5/5] US ticker_selection_log forward return update")
    try:
        import ticker_selection_db as tsdb

        stats = tsdb.update_forward_returns(market="US")
        log.info(
            "[ticker_selection_log US] "
            f"pending={stats['pending']} updated={stats['updated']} "
            f"skipped={stats['skipped']} missing_csv={stats['missing_csv']}"
        )
    except Exception as e:
        log.error(f"US ticker_selection_log updater failed: {e}")

    log.info("=== US data update done ===")


def main():
    parser = argparse.ArgumentParser(description="Refresh price/news/supplement data and forward returns")
    parser.add_argument(
        "--market",
        choices=["KR", "US", "ALL"],
        default="ALL",
        help="target market",
    )
    args = parser.parse_args()

    if args.market in ("KR", "ALL"):
        run_kr_update()

    if args.market in ("US", "ALL"):
        run_us_update()


if __name__ == "__main__":
    main()
