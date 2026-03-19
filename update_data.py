"""
update_data.py - 매일 장 시작 전 데이터 최신화 마스터 스크립트

실행 시점 권장:
  KR: 08:30 (KR 장 시작 08:50 전)
  US: 22:00 (US 장 시작 22:20 전)

Windows 작업 스케줄러 등록:
  schtasks /create /tn "claudetrade_kr" /tr "python E:\\code\\claudetrade\\update_data.py --market KR" /sc daily /st 08:30
  schtasks /create /tn "claudetrade_us" /tr "python E:\\code\\claudetrade\\update_data.py --market US" /sc daily /st 22:00

수동 실행:
  python update_data.py          # 전체
  python update_data.py --market KR
  python update_data.py --market US
"""

import sys
import argparse
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from logger import get_trading_logger

log = get_trading_logger()


def run_kr_update():
    today = date.today().strftime("%Y-%m-%d")
    log.info("=== KR 데이터 최신화 시작 ===")

    # 1. 주가 최신화 (KR만)
    log.info("[1/3] 주가 최신화")
    try:
        from phase1_trainer.price_collector import collect_kr_incremental
        import pandas as pd
        end_dt   = pd.Timestamp(date.today())
        start_dt = pd.Timestamp(date.today() - timedelta(days=500))
        collect_kr_incremental(start_dt, end_dt)
    except Exception as e:
        log.error(f"주가 최신화 실패: {e}")

    # 2. 뉴스 수집
    log.info("[2/3] KR 뉴스 수집")
    try:
        from phase1_trainer.kr_news_collector import collect_day
        collect_day(today)
    except Exception as e:
        log.error(f"KR 뉴스 수집 실패: {e}")

    # 3. supplement (수급/환율)
    log.info("[3/3] KR supplement")
    try:
        from phase1_trainer.supplement_collector import collect_kr_supplement
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        collect_kr_supplement(yesterday)  # 어제 수급 (오늘은 장 마감 후 업데이트됨)
    except Exception as e:
        log.error(f"KR supplement 실패: {e}")

    log.info("=== KR 데이터 최신화 완료 ===")


def run_us_update():
    today = date.today().strftime("%Y-%m-%d")
    log.info("=== US 데이터 최신화 시작 ===")

    # 1. 주가 최신화 (US만) - compact 100일 fetch 후 머지
    log.info("[1/3] US 주가 최신화")
    try:
        from phase1_trainer.price_collector import collect_us_incremental
        import pandas as pd
        end_dt   = pd.Timestamp(date.today())
        start_dt = pd.Timestamp(date.today() - timedelta(days=500))
        collect_us_incremental(start_dt, end_dt)
    except Exception as e:
        log.error(f"US 주가 최신화 실패: {e}")

    # 2. 뉴스 수집
    log.info("[2/3] US 뉴스 수집")
    try:
        from phase1_trainer.us_news_collector import collect_day
        collect_day(today)
    except Exception as e:
        log.error(f"US 뉴스 수집 실패: {e}")

    # 3. supplement (VIX)
    log.info("[3/3] US supplement")
    try:
        from phase1_trainer.supplement_collector import collect_us_supplement
        yesterday = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        collect_us_supplement(yesterday)
    except Exception as e:
        log.error(f"US supplement 실패: {e}")

    log.info("=== US 데이터 최신화 완료 ===")


def main():
    parser = argparse.ArgumentParser(description="일일 데이터 최신화")
    parser.add_argument("--market", choices=["KR", "US", "ALL"], default="ALL",
                        help="수집 대상 시장 (기본: ALL)")
    args = parser.parse_args()

    if args.market in ("KR", "ALL"):
        run_kr_update()

    if args.market in ("US", "ALL"):
        run_us_update()


if __name__ == "__main__":
    main()
