"""
bot/market_utils.py
시간/블랙아웃/장 진행도 유틸 — MarketUtilsMixin
trading_bot.py에서 이동 (로직 변경 없음)
"""

from datetime import date, datetime, timedelta, time as dt_time

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - python<3.9 fallback
    from datetime import timezone

    class ZoneInfo:  # type: ignore
        def __new__(cls, _name: str):
            return timezone(timedelta(hours=9))

from risk_manager import HARD_RULES
from logger import get_trading_logger

log = get_trading_logger()
KST = ZoneInfo("Asia/Seoul")

_EXCHANGE_MAP = {"KR": "XKRX", "US": "XNYS"}
_ec_cache: dict = {}


def _market_session_date(market: str, now_dt=None):
    """시장 세션 기준 날짜. US는 KST 자정~05:00 구간을 전일 세션으로 본다."""
    now_dt = now_dt or datetime.now(KST)
    d = now_dt.date()
    if market == "US" and now_dt.time() < dt_time(5, 0):
        return d - timedelta(days=1)
    return d


def _is_trading_day(market: str, check_date=None) -> bool:
    """오늘이 해당 시장의 정규 거래일인지 확인 (주말·공휴일 모두 처리)"""
    if check_date is None:
        check_date = _market_session_date(market)
    exchange = _EXCHANGE_MAP.get(market, "XNYS")
    try:
        import exchange_calendars as ec
        if exchange not in _ec_cache:
            _ec_cache[exchange] = ec.get_calendar(exchange)
        return bool(_ec_cache[exchange].is_session(str(check_date)))
    except Exception as e:
        # exchange_calendars 없거나 오류 시 주말만 체크
        log.warning(f"거래소 캘린더 조회 실패 ({exchange}): {e} — 주말 체크만 적용")
        return check_date.weekday() < 5


class MarketUtilsMixin:

    def _in_entry_blackout(self, market: str) -> bool:
        now = datetime.now(KST).time()
        no_new = HARD_RULES["no_new_entry_min"]
        no_late = HARD_RULES["close_before_min"]

        if market == "KR":
            open_t = dt_time(9, 0)
            close_t = dt_time(16, 0)
        else:
            # US session in KST: 22:30 ~ 05:00(next day)
            open_t = dt_time(22, 30)
            close_t = dt_time(5, 0)

        if market == "KR":
            # timezone-aware 비교 (KST 명시)
            start_block_end = (datetime.combine(date.today(), open_t, tzinfo=KST).timestamp() + no_new * 60)
            end_block_start = (datetime.combine(date.today(), close_t, tzinfo=KST).timestamp() - no_late * 60)
            now_ts = datetime.now(KST).timestamp()
            return now_ts < start_block_end or now_ts > end_block_start

        # US crossing midnight
        now_dt = datetime.now(KST)
        open_dt = datetime.combine(now_dt.date(), open_t, tzinfo=KST)
        close_dt = datetime.combine(now_dt.date(), close_t, tzinfo=KST)
        if now < close_t:
            open_dt = open_dt - timedelta(days=1)
        else:
            close_dt = close_dt + timedelta(days=1)

        start_block_end = open_dt.timestamp() + no_new * 60
        end_block_start = close_dt.timestamp() - no_late * 60
        now_ts = now_dt.timestamp()
        return now_ts < start_block_end or now_ts > end_block_start

    def _is_order_allowed_now(self, market: str) -> bool:
        """정규장 외 주문 차단 — 장전/장종료 주문 실패 반복 방지."""
        now_dt = datetime.now(KST)
        open_dt = self._market_open_anchor_dt(market)
        close_dt = self._market_close_anchor_dt(market)
        if open_dt <= now_dt < close_dt:
            return True
        if market == "US" and now_dt < open_dt:
            log.debug(f"[US 주문차단] 장 시작 전 ({now_dt.strftime('%H:%M')} < 22:30) — 주문 보류")
        else:
            log.debug(f"[{market} 주문차단] 정규장 외 시간 ({now_dt.strftime('%H:%M')}) — 주문 보류")
        return False

    def _intraday_session_progress(self, market: str) -> float:
        """
        Returns session progress in [0, 1].
        KR: 09:00~15:30, US(KST): 22:30~05:00
        """
        now_dt = datetime.now(KST)
        if market == "KR":
            start_dt = datetime.combine(now_dt.date(), dt_time(9, 0), tzinfo=KST)
            end_dt = datetime.combine(now_dt.date(), dt_time(15, 30), tzinfo=KST)
        else:
            start_dt = datetime.combine(now_dt.date(), dt_time(22, 30), tzinfo=KST)
            end_dt = datetime.combine(now_dt.date(), dt_time(5, 0), tzinfo=KST)
            if now_dt.time() < dt_time(5, 0):
                start_dt -= timedelta(days=1)
            else:
                end_dt += timedelta(days=1)

        total_sec = max(1.0, (end_dt - start_dt).total_seconds())
        elapsed_sec = (now_dt - start_dt).total_seconds()
        return max(0.0, min(1.0, elapsed_sec / total_sec))

    def _market_open_anchor_dt(self, market: str) -> datetime:
        """현재 세션의 실제 장 시작 시각(KST)을 반환한다."""
        now_dt = datetime.now(KST)
        if market == "KR":
            return datetime.combine(now_dt.date(), dt_time(9, 0), tzinfo=KST)
        open_dt = datetime.combine(now_dt.date(), dt_time(22, 30), tzinfo=KST)
        if now_dt.time() < dt_time(5, 0):
            open_dt -= timedelta(days=1)
        return open_dt

    def _market_close_anchor_dt(self, market: str) -> datetime:
        """현재 세션의 실제 장 종료 시각(KST)을 반환한다."""
        now_dt = datetime.now(KST)
        if market == "KR":
            return datetime.combine(now_dt.date(), dt_time(15, 30), tzinfo=KST)
        close_dt = datetime.combine(now_dt.date(), dt_time(5, 0), tzinfo=KST)
        if now_dt.time() >= dt_time(22, 30):
            close_dt += timedelta(days=1)
        return close_dt

    def _minutes_to_close(self, market: str) -> float:
        return (self._market_close_anchor_dt(market) - datetime.now(KST)).total_seconds() / 60.0

    def _next_market_open_dt(self, market: str) -> datetime:
        now_dt = datetime.now(KST)
        if market == "KR":
            open_dt = datetime.combine(now_dt.date(), dt_time(9, 0), tzinfo=KST)
            if now_dt >= open_dt:
                open_dt += timedelta(days=1)
            return open_dt
        open_dt = datetime.combine(now_dt.date(), dt_time(22, 30), tzinfo=KST)
        if now_dt >= open_dt:
            open_dt += timedelta(days=1)
        return open_dt

    def _market_elapsed_min(self, market: str) -> float:
        """실제 장 시작 시각 기준 경과 분. 재시작과 무관하게 고정된다."""
        now_dt = datetime.now(KST)
        open_dt = self._market_open_anchor_dt(market)
        return max(0.0, (now_dt - open_dt).total_seconds() / 60.0)

    def _project_intraday_volume(self, market: str, volume: float) -> float:
        """
        Project current intraday volume to a full-session estimate.
        Keeps early-session overprojection bounded.
        """
        try:
            volume = float(volume or 0)
        except Exception:
            return 0.0
        if volume <= 0:
            return 0.0

        progress = self._intraday_session_progress(market)
        if progress <= 0 or progress >= 1:
            return volume

        # Floor progress to 20% and cap multiplier to 3x to avoid open noise.
        effective_progress = max(progress, 0.20)
        multiplier = min(3.0, 1.0 / effective_progress)
        return volume * multiplier

    def _is_market_session_now(self, market: str) -> bool:
        """현재 해당 시장의 거래 시간 여부 (KST 기준)"""
        now_t = datetime.now(KST).time()
        if market == "KR":
            return dt_time(8, 40) <= now_t <= dt_time(16, 10)
        else:  # US
            return now_t >= dt_time(22, 30) or now_t < dt_time(5, 0)
