"""
Daily data refresh entrypoint.

Recommended schedule:
- KR open: 08:30
- KR close: 16:00
- US open: 22:00
- US close: 07:00
"""

import argparse
from contextlib import contextmanager
import json
import os
import subprocess
import sys
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from logger import get_trading_logger
from runtime_paths import get_runtime_path

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover
    psutil = None

log = get_trading_logger()


class UpdateAlreadyRunning(RuntimeError):
    pass


def _pid_alive(pid: int) -> bool:
    """PID 생존 확인 — Windows에서 os.kill(pid, 0)은 절대 쓰지 않는다.

    사고(2026-08-13): Windows의 os.kill(pid, 0)은 프로브가 아니라
    TerminateProcess(pid, 0)다 — "확인"이 대상 프로세스를 즉사시킨다.
    실측 피해 2건: ① pytest가 자기 lock pid를 확인하다 자살(전체 스위트 3연속
    93% 지점 사망), ② stale lock(죽은 pid)을 확인할 때마다 그 번호를 재사용한
    무고한 프로세스가 저격당하고, 저격 후 "살아있음"으로 오판해 update를 건너뜀.
    psutil 우선, Windows 폴백은 tasklist(읽기 전용)만 쓴다.
    """
    pid = int(pid or 0)
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if psutil is not None:
        try:
            return bool(psutil.pid_exists(pid))
        except Exception:
            return False
    if sys.platform.startswith("win"):
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return str(pid) in (result.stdout or "")
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


@contextmanager
def update_market_lock(market: str):
    """Cross-task singleton lock; clashing schedules exit without touching data."""

    market_key = str(market or "").upper()
    lock_path = get_runtime_path("state", f"update_data_{market_key}.lock.json")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    payload = {
        "pid": os.getpid(),
        "market": market_key,
        "token": token,
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    for attempt in range(2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            break
        except FileExistsError:
            try:
                existing = json.loads(lock_path.read_text(encoding="utf-8-sig"))
            except Exception:
                existing = {}
            owner_pid = int(existing.get("pid") or 0)
            if _pid_alive(owner_pid):
                raise UpdateAlreadyRunning(
                    f"{market_key} update already running pid={owner_pid}"
                )
            if attempt == 0:
                try:
                    lock_path.unlink()
                except FileNotFoundError:
                    pass
                continue
            raise UpdateAlreadyRunning(f"{market_key} update lock could not be acquired")
    try:
        yield lock_path
    finally:
        try:
            existing = json.loads(lock_path.read_text(encoding="utf-8-sig"))
            if existing.get("token") == token:
                lock_path.unlink()
        except FileNotFoundError:
            pass
        except Exception as exc:
            log.warning(f"{market_key} update lock cleanup failed: {exc}")


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

    targets = ["KR", "US"] if args.market == "ALL" else [args.market]
    for market in targets:
        try:
            with update_market_lock(market):
                run_kr_update() if market == "KR" else run_us_update()
        except UpdateAlreadyRunning as exc:
            log.warning(f"[update_data singleton] {exc}; duplicate invocation skipped")


if __name__ == "__main__":
    main()
