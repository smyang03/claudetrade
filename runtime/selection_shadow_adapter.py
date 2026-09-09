"""Verified exchange clocks and bounded, order-incapable forward observation."""
from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from contextlib import closing
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from bot.session_date import is_known_market_holiday

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
_worker_lock = threading.Lock()
_last_started = float('-inf')
_last_market = None


def aware_now(value=None):
    value = value or datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('timezone-aware timestamp required')
    return value


@lru_cache(maxsize=2)
def _calendar(market):
    import exchange_calendars
    return exchange_calendars.get_calendar({'KR': 'XKRX', 'US': 'XNYS'}[market])


def build_clock(market: str, now=None) -> dict:
    zone = ZoneInfo({'KR': 'Asia/Seoul', 'US': 'America/New_York'}[market])
    current = aware_now(now).astimezone(zone)
    day = current.date().isoformat()
    cal = _calendar(market)
    valid = bool(cal.is_session(day)) and not is_known_market_holiday(market, day)
    dates = [str(d.date()) for d in cal.sessions if str(d.date()) <= day
             and not is_known_market_holiday(market, str(d.date()))]
    clock = dict(market=market, session_date=day, now=current.isoformat(),
                 session_dates=dates, is_session=valid, calendar=cal.name)
    if valid:
        clock['open_at'] = cal.session_open(day).to_pydatetime().astimezone(zone).isoformat()
        clock['close_at'] = cal.session_close(day).to_pydatetime().astimezone(zone).isoformat()
    return clock


def normalize_quote(market, raw, now=None):
    current = aware_now(now)
    try:
        if not isinstance(raw, dict) or any(not raw.get(f) for f in ('price_at', 'requested_at', 'received_at')):
            raise ValueError('missing source timestamp')
        price = float(raw['price'])
        observed = aware_now(raw['price_at'])
        requested = aware_now(raw['requested_at'])
        received = aware_now(raw['received_at'])
        if not math.isfinite(price) or price <= 0 or not 0 <= (current - observed).total_seconds() <= 60:
            raise ValueError('invalid or stale observed price')
        if requested > received or received > current or observed > received:
            raise ValueError('invalid quote timing')
        clock = build_clock(market, observed)
        if not clock['is_session'] or not aware_now(clock['open_at']) <= observed <= aware_now(clock['close_at']):
            raise ValueError('outside regular session')
        return dict(price=price, price_at=observed.isoformat(), requested_at=requested.isoformat(),
                    received_at=received.isoformat(), session_date=clock['session_date'],
                    source=raw['source'], price_kind='LAST_PRICE_PAPER')
    except (KeyError, TypeError, ValueError):
        evidence = {key: raw[key] for key in ('source', 'requested_at', 'received_at', 'price_at', 'price_kind')
                    if isinstance(raw, dict) and isinstance(raw.get(key), str)}
        return {**evidence, 'status': 'NO_VERIFIED_QUOTE', 'reason': 'missing, invalid or stale source timestamp'}


class ExistingQuoteProvider:
    """US reads existing observations only; KR uses the shared Naver throttle."""

    def __init__(self):
        self.cursor = 0
        self.diagnostics = {}

    def __call__(self, market, tickers):
        tickers = sorted(set(tickers))
        self.diagnostics = {'mode': 'CACHE_ONLY' if market == 'US' else 'NAVER_BOUNDED',
                            'required_count': len(tickers), 'requested_count': 0,
                            'last_available_price_at': None}
        result = {t: {'status': 'NO_VERIFIED_QUOTE', 'reason': 'no observation or batch budget exhausted'} for t in tickers}
        if market == 'US':
            from kis_api import get_observed_finnhub_quote
            for ticker in tickers:
                raw = get_observed_finnhub_quote(ticker)
                if raw:
                    result[ticker] = normalize_quote(market, raw)
                    self._record_available_timestamp(raw)
        elif tickers:
            from tools.analysis_quotes import get_quote_kr
            offset = self.cursor % len(tickers)
            batch = (tickers[offset:] + tickers[:offset])[:4]
            self.cursor = (offset + len(batch)) % len(tickers)
            for ticker in batch:
                self.diagnostics['requested_count'] += 1
                raw = get_quote_kr(ticker, timeout=2.0)
                result[ticker] = normalize_quote(market, raw)
                self._record_available_timestamp(raw)
        self.diagnostics['unverified_count'] = sum(q.get('status') == 'NO_VERIFIED_QUOTE' for q in result.values())
        self.diagnostics['missing_reason'] = ('no fresh original request/price timestamp available'
                                              if self.diagnostics['unverified_count'] else None)
        return result

    def _record_available_timestamp(self, raw):
        stamp = raw.get('price_at') if isinstance(raw, dict) else None
        if not isinstance(stamp, str):
            return
        try:
            parsed = aware_now(stamp)
            prior = self.diagnostics['last_available_price_at']
            if prior is None or parsed > aware_now(prior):
                self.diagnostics['last_available_price_at'] = stamp
        except (TypeError, ValueError):
            pass


def _with_missing_schedule_bounds(path, clock):
    """Fetch only missing D7 bounds for held cohorts, before the write lock.

    session_dates already comes from the verified calendar/holiday contract.
    Persisted bounds are reused; no full historical open/close rebuild is needed.
    """
    dates = clock.get('session_dates') or []
    indices = {day: index for index, day in enumerate(dates)}
    needed = set()
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        for (entry,) in db.execute('SELECT DISTINCT entry_session FROM positions WHERE market=?', (clock['market'],)):
            index = indices.get(entry)
            if index is not None and index + 6 < len(dates):
                day = dates[index + 6]
                if day < clock['session_date']:
                    needed.add(day)
        needed = {day for day in needed if not db.execute(
            'SELECT 1 FROM session_observations WHERE market=? AND session_date=?',
            (clock['market'], day)).fetchone()}
    bounds = {}
    if needed:
        cal = _calendar(clock['market'])
        zone = ZoneInfo({'KR': 'Asia/Seoul', 'US': 'America/New_York'}[clock['market']])
        for day in sorted(needed):
            bounds[day] = dict(open_at=cal.session_open(day).to_pydatetime().astimezone(zone).isoformat(),
                               close_at=cal.session_close(day).to_pydatetime().astimezone(zone).isoformat())
    return {**clock, 'schedule_bounds': bounds}


def run_cycle(root, clock: dict, quote_provider) -> dict:
    from runtime.selection_shadow_book import SelectionShadowBook
    from tools.selection_shadow_runner import collect_snapshot
    path = Path(root) / 'data/shadow/selection_forward.db'
    book = SelectionShadowBook(path)
    market, session = clock['market'], clock['session_date']
    now = aware_now(clock['now'])
    book.expire_pending(clock)
    book.diagnose_coverage(clock)
    source_info = {'mode': ('NO_QUOTE_PROVIDER' if quote_provider is None else
                           ('CACHE_ONLY' if market == 'US' else 'NAVER_BOUNDED')),
                   'last_available_price_at': None, 'missing_reason': 'outside regular session',
                   'required_count': 0, 'requested_count': 0, 'unverified_count': 0}
    if not clock.get('is_session', True):
        book.record_status(market, session, 'CLOSED', clock['now'], source_info)
        return {'status': 'CLOSED', 'errors': [], 'quote_source': source_info}
    opened, closed = aware_now(clock['open_at']), aware_now(clock['close_at'])
    if now < opened - timedelta(minutes=30) or now > closed:
        book.tick(_with_missing_schedule_bounds(path, clock), {})
        book.record_status(market, session, 'CLOSED', clock['now'], source_info)
        return {'status': 'CLOSED', 'errors': [], 'quote_source': source_info}
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        prior = db.execute('SELECT status,completed_at FROM snapshots WHERE market=? AND session_date=?',
                           (market, session)).fetchone()
    frozen = prior and prior[0] in {'READY', 'EMPTY'}
    retry_due = not prior or (now - aware_now(prior[1])).total_seconds() >= 300
    if now <= opened + timedelta(minutes=45) and not frozen and retry_due:
        snapshot = collect_snapshot(root, market, session)
        book.record_snapshot(snapshot)
        if snapshot['status'] not in {'READY', 'EMPTY'}:
            book.record_status(market, session, snapshot['status'], snapshot['completed_at'], {})
    fresh = build_clock(market)
    if fresh['session_date'] != session or not fresh['is_session']:
        book.record_status(market, session, 'CLOSED', fresh['now'], {'reason': 'session changed during collection'})
        return {'status': 'CLOSED', 'errors': []}
    book.decide(fresh)
    book.expire_pending(fresh)
    tickers = book.required_tickers(market)
    in_session = aware_now(fresh['open_at']) <= aware_now(fresh['now']) <= aware_now(fresh['close_at'])
    provider_called = bool(in_session and quote_provider)
    quotes = quote_provider(market, tickers) if provider_called else {}
    final_clock = build_clock(market)
    if final_clock['session_date'] != session or not final_clock['is_session']:
        book.record_status(market, session, 'CLOSED', final_clock['now'], {'reason': 'session changed during quotes'})
        return {'status': 'CLOSED', 'errors': []}
    result = book.tick(_with_missing_schedule_bounds(path, final_clock), quotes)
    diagnostics = dict(getattr(quote_provider, 'diagnostics', {'mode': 'INJECTED'})) if provider_called else {
        **source_info, 'required_count': len(tickers), 'unverified_count': len(tickers)}
    book.record_status(market, session, 'QUOTE_SOURCE', final_clock['now'], diagnostics)
    if aware_now(final_clock['now']) > opened + timedelta(minutes=45) and not frozen:
        book.record_status(market, session, 'ENTRY_WINDOW_MISSED', final_clock['now'], diagnostics)
    elif aware_now(final_clock['now']) < opened or aware_now(final_clock['now']) > closed:
        book.record_status(market, session, 'CLOSED', final_clock['now'], diagnostics)
    result['quote_source'] = diagnostics
    return result


def maybe_start(bot, market: str) -> None:
    """One process-wide worker, no queue; alternate enabled markets every >=15s."""
    global _last_started, _last_market
    enabled = set(getattr(bot, 'enabled_markets', {'KR', 'US'})) & {'KR', 'US'}
    if market not in enabled or not _worker_lock.acquire(blocking=False):
        return
    current = time.monotonic()
    if current - _last_started < 15 or (len(enabled) > 1 and market == _last_market):
        _worker_lock.release()
        return
    _last_started, _last_market = current, market
    provider = getattr(bot, '_selection_shadow_quote_provider', None)
    if provider is None:
        provider = ExistingQuoteProvider()
        bot._selection_shadow_quote_provider = provider

    def work():
        try:
            run_cycle(ROOT, build_clock(market), provider)
        except Exception as exc:
            log.warning('selection shadow %s observation failed: %s', market, type(exc).__name__)
            try:
                from runtime.selection_shadow_book import SelectionShadowBook
                now = aware_now().astimezone(ZoneInfo('Asia/Seoul' if market == 'KR' else 'America/New_York'))
                SelectionShadowBook(ROOT / 'data/shadow/selection_forward.db').record_status(
                    market, now.date().isoformat(), 'ERROR', now.isoformat(), {'error_type': type(exc).__name__})
            except Exception:
                log.warning('selection shadow error persistence unavailable')
        finally:
            _worker_lock.release()
    try:
        threading.Thread(target=work, name='selection-shadow', daemon=True).start()
    except Exception:
        _worker_lock.release()
        log.warning('selection shadow worker could not start')
