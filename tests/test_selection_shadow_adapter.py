from datetime import datetime

import pytest

from runtime.selection_shadow_adapter import build_clock, normalize_quote


@pytest.mark.parametrize('stamp,opening,closing', [
    ('2026-07-08T23:00:00+09:00', '2026-07-08T09:30:00-04:00', '2026-07-08T16:00:00-04:00'),
    ('2026-01-09T05:50:00+09:00', '2026-01-08T09:30:00-05:00', '2026-01-08T16:00:00-05:00'),
    ('2026-11-27T23:50:00+09:00', '2026-11-27T09:30:00-05:00', '2026-11-27T13:00:00-05:00'),
])
def test_verified_us_sessions(stamp, opening, closing):
    clock = build_clock('US', datetime.fromisoformat(stamp))
    assert clock['open_at'] == opening
    assert clock['close_at'] == closing
    assert clock['now'][:10] == clock['session_date']


def test_kr_holiday_override_and_calendar_failure(monkeypatch):
    assert not build_clock('KR', datetime.fromisoformat('2026-07-17T10:00:00+09:00'))['is_session']
    import runtime.selection_shadow_adapter as adapter
    monkeypatch.setattr(adapter, '_calendar', lambda market: (_ for _ in ()).throw(RuntimeError('offline')))
    with pytest.raises(RuntimeError):
        build_clock('KR', datetime.fromisoformat('2026-09-10T10:00:00+09:00'))


def test_missing_schedule_uses_early_close_once_for_shared_held_cohort(tmp_path, monkeypatch):
    import runtime.selection_shadow_adapter as adapter
    from runtime.selection_shadow_book import SelectionShadowBook, read_report
    first = build_clock('US', '2026-11-18T09:36:00-05:00')
    resumed = build_clock('US', '2026-11-30T10:30:00-05:00')
    path = tmp_path / 'data/shadow/selection_forward.db'
    book = SelectionShadowBook(path)
    book.record_snapshot(dict(market='US', session_date='2026-11-18', signal_date='2026-11-17',
                              status='READY', collected_at='2026-11-18T09:34:00-05:00',
                              completed_at='2026-11-18T09:34:01-05:00', candidates=[{'ticker':'AAA','dvol':200_000_000}]))
    book.decide(first)
    book.tick(first, {'AAA': dict(price=100, price_at=first['now'], requested_at=first['now'], received_at=first['now'],
                                 session_date=first['session_date'], source='fixture', price_kind='LAST_PRICE_PAPER')})
    actual_calendar = adapter._calendar('US')
    requested = []
    class CalendarProxy:
        def session_open(self, day):
            requested.append(('open', day))
            return actual_calendar.session_open(day)
        def session_close(self, day):
            requested.append(('close', day))
            return actual_calendar.session_close(day)
    monkeypatch.setattr(adapter, '_calendar', lambda market: CalendarProxy())
    monkeypatch.setattr(adapter, 'build_clock', lambda *args: resumed)
    adapter.run_cycle(tmp_path, resumed, lambda *args: {})
    adapter.run_cycle(tmp_path, resumed, lambda *args: {})
    report = read_report(path)
    assert len(report['positions']) == 3
    assert {p['scheduled_exit_at'] for p in report['positions']} == {'2026-11-27T12:45:00-05:00'}
    assert requested == [('open', '2026-11-27'), ('close', '2026-11-27')]


def test_quote_requires_observed_timestamp_and_preserves_provenance():
    now = datetime.fromisoformat('2026-09-10T10:00:00+09:00')
    raw = dict(price=100, requested_at='2026-09-10T09:59:58+09:00', received_at=now.isoformat(), source='naver_polling')
    assert normalize_quote('KR', raw, now)['status'] == 'NO_VERIFIED_QUOTE'
    valid = normalize_quote('KR', dict(raw, price_at='2026-09-10T09:59:59+09:00'), now)
    assert valid['price_kind'] == 'LAST_PRICE_PAPER'
    assert valid['session_date'] == '2026-09-10'
    assert valid['requested_at'] == raw['requested_at']
    assert normalize_quote('KR', dict(raw, price_at='2026-09-10T09:58:59+09:00'), now)['status'] == 'NO_VERIFIED_QUOTE'


def test_cycle_freezes_once_and_fresh_postcollection_clock(tmp_path, monkeypatch):
    from tests.test_selection_shadow_runner import prices
    from runtime.selection_shadow_book import read_report
    import runtime.selection_shadow_adapter as adapter
    prices(tmp_path, 'KR', '005930')
    actual_clock = adapter.build_clock
    times = iter(['2026-09-10T09:06:00+09:00', '2026-09-10T09:06:02+09:00',
                  '2026-09-10T09:06:15+09:00', '2026-09-10T09:06:17+09:00'])
    monkeypatch.setattr(adapter, 'build_clock', lambda market, now=None: actual_clock(market, now or next(times)))
    import tools.selection_shadow_runner as runner
    collect = runner.collect_snapshot
    monkeypatch.setattr(runner, 'collect_snapshot', lambda root, market, session_date: collect(
        root, market, session_date, datetime.fromisoformat('2026-09-10T09:05:59+09:00')))
    def quotes(market, tickers):
        assert market == 'KR' and tickers == ['005930']
        return {'005930': dict(price=100_000, price_at='2026-09-10T09:06:01+09:00',
                              requested_at='2026-09-10T09:06:00+09:00', received_at='2026-09-10T09:06:02+09:00',
                              session_date='2026-09-10', source='fixture', price_kind='LAST_PRICE_PAPER')}
    clock = actual_clock('KR', '2026-09-10T09:05:00+09:00')
    adapter.run_cycle(tmp_path, clock, quotes)
    monkeypatch.setattr(runner, 'collect_snapshot', lambda *args: pytest.fail('frozen snapshot recollected'))
    adapter.run_cycle(tmp_path, clock, quotes)
    report = read_report(tmp_path / 'data/shadow/selection_forward.db')
    assert len(report['positions']) == 3
    assert {p['entry_at'] for p in report['positions']} == {'2026-09-10T09:06:02+09:00'}


def test_late_start_records_missed_without_expensive_collection(tmp_path, monkeypatch):
    import runtime.selection_shadow_adapter as adapter
    import tools.selection_shadow_runner as runner
    from runtime.selection_shadow_book import read_report
    clock = build_clock('US', '2026-01-08T15:50:00-05:00')
    monkeypatch.setattr(adapter, 'build_clock', lambda *args: clock)
    monkeypatch.setattr(runner, 'collect_snapshot', lambda *args: pytest.fail('late collection'))
    adapter.run_cycle(tmp_path, clock, lambda market, tickers: {})
    report = read_report(tmp_path / 'data/shadow/selection_forward.db')
    assert report['markets'][0]['latest_status'] == 'ENTRY_WINDOW_MISSED'


def test_incomplete_retry_is_bounded(tmp_path, monkeypatch):
    import runtime.selection_shadow_adapter as adapter
    import tools.selection_shadow_runner as runner
    from runtime.selection_shadow_book import SelectionShadowBook
    clock = build_clock('KR', '2026-09-10T09:10:00+09:00')
    SelectionShadowBook(tmp_path / 'data/shadow/selection_forward.db').record_snapshot(dict(
        market='KR', session_date='2026-09-10', status='INPUT_INCOMPLETE', candidates=[],
        collected_at='2026-09-10T09:09:30+09:00', completed_at='2026-09-10T09:09:31+09:00'))
    monkeypatch.setattr(adapter, 'build_clock', lambda *args: clock)
    monkeypatch.setattr(runner, 'collect_snapshot', lambda *args: pytest.fail('retried too soon'))
    assert adapter.run_cycle(tmp_path, clock, lambda market, tickers: {})['errors'] == []


def test_worker_fairness_nonoverlap_and_interval(monkeypatch, tmp_path):
    import threading
    import types
    import runtime.selection_shadow_adapter as adapter
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    observed = []
    def cycle(root, clock, provider):
        observed.append(clock['market'])
        entered.set()
        release.wait(2)
        completed.set()
    monkeypatch.setattr(adapter, '_worker_lock', threading.Lock())
    monkeypatch.setattr(adapter, '_last_started', float('-inf'))
    monkeypatch.setattr(adapter, '_last_market', None)
    monkeypatch.setattr(adapter, 'ROOT', tmp_path)
    tick = [100.0]
    monkeypatch.setattr(adapter.time, 'monotonic', lambda: tick[0])
    monkeypatch.setattr(adapter, 'run_cycle', cycle)
    bot = types.SimpleNamespace(enabled_markets={'KR', 'US'})
    adapter.maybe_start(bot, 'KR')
    assert entered.wait(2)
    adapter.maybe_start(bot, 'US')
    assert observed == ['KR']
    release.set()
    assert completed.wait(2)
    # Synchronize with the worker's finally release, without starting any job.
    assert adapter._worker_lock.acquire(timeout=2)
    adapter._worker_lock.release()
    adapter.maybe_start(bot, 'US')
    assert observed == ['KR']
    tick[0] = 116
    entered.clear()
    adapter.maybe_start(bot, 'KR')
    adapter.maybe_start(bot, 'US')
    assert entered.wait(2)
    assert observed == ['KR', 'US']


def test_us_cache_only_no_network_and_missing_timestamp_never_fills(monkeypatch):
    import kis_api
    import runtime.selection_shadow_adapter as adapter
    monkeypatch.setattr(kis_api.requests, 'get', lambda *a, **k: pytest.fail('US network request'))
    monkeypatch.setattr(kis_api, '_FINNHUB_OBSERVED_QUOTES', {})
    provider = adapter.ExistingQuoteProvider()
    quotes = provider('US', ['AAA'])
    assert quotes['AAA']['status'] == 'NO_VERIFIED_QUOTE'
    assert provider.diagnostics['mode'] == 'CACHE_ONLY'


def test_naver_preserves_actual_stock_timestamp(monkeypatch):
    from tools import analysis_quotes
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {'datas': [{'closePrice': '10,000', 'localTradedAt': '2026-09-10T10:00:00+09:00',
                              'marketStatus': 'OPEN', 'integratedPriceInfo': {'closePrice': '999'}}]}
    monkeypatch.setattr(analysis_quotes._SESSION, 'get', lambda *a, **k: Response())
    quote = analysis_quotes.get_quote_kr('005930')
    assert quote['price'] == 10000
    assert quote['price_at'] == '2026-09-10T10:00:00+09:00'
    assert quote['requested_at'] <= quote['received_at']


def test_finnhub_producer_preserves_timestamp_in_bounded_cache(monkeypatch):
    import kis_api
    monkeypatch.setattr(kis_api, 'FINNHUB_KEY', 'fixture')
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return dict(c=100, pc=99, o=99, h=101, l=98, t=1789048800)
    monkeypatch.setattr(kis_api.requests, 'get', lambda *a, **k: Response())
    quote = kis_api._get_price_us_finnhub('AAA')
    cached = kis_api.get_observed_finnhub_quote('AAA')
    assert cached['price'] == quote['price'] == 100
    assert cached['price_at'] == '2026-09-10T14:00:00+00:00'
    assert cached['source'] == 'finnhub'
    assert cached['requested_at'] <= cached['received_at']


@pytest.mark.parametrize('field', ['price_at', 'requested_at', 'received_at'])
def test_null_timestamp_cannot_be_replaced_with_wall_clock(field, monkeypatch):
    import runtime.selection_shadow_adapter as adapter
    now = datetime.fromisoformat('2026-09-10T10:00:00+09:00')
    original = adapter.aware_now
    monkeypatch.setattr(adapter, 'aware_now', lambda value=None: now if value is None else original(value))
    raw = dict(price=100, source='fixture', price_at=now.isoformat(), requested_at=now.isoformat(), received_at=now.isoformat())
    raw[field] = None
    assert normalize_quote('KR', raw, now)['status'] == 'NO_VERIFIED_QUOTE'


def test_kr_quote_budget_rotates_without_polling_whole_portfolio(monkeypatch):
    from tools import analysis_quotes
    import runtime.selection_shadow_adapter as adapter
    requests = []
    monkeypatch.setattr(analysis_quotes, 'get_quote_kr', lambda ticker, timeout: requests.append(ticker))
    provider = adapter.ExistingQuoteProvider()
    provider('KR', ['1', '2', '3', '4', '5', '6'])
    assert requests == ['1', '2', '3', '4']
    provider('KR', ['1', '2', '3', '4', '5', '6'])
    assert requests[4:] == ['5', '6', '1', '2']


def test_cache_quote_requested_before_decision_does_not_fill(tmp_path, monkeypatch):
    import kis_api
    import runtime.selection_shadow_adapter as adapter
    from runtime.selection_shadow_book import SelectionShadowBook, read_report
    book = SelectionShadowBook(tmp_path / 'data/shadow/selection_forward.db')
    book.record_snapshot(dict(market='US', session_date='2026-09-10', status='READY',
                             collected_at='2026-09-10T09:35:00-04:00', completed_at='2026-09-10T09:35:00-04:00',
                             candidates=[dict(ticker='AAA', dvol=2e8)]))
    clock = build_clock('US', '2026-09-10T09:36:00-04:00')
    monkeypatch.setattr(adapter, 'build_clock', lambda *a: clock)
    monkeypatch.setattr(adapter, 'aware_now', lambda value=None: datetime.fromisoformat(value) if value else datetime.fromisoformat(clock['now']))
    monkeypatch.setattr(kis_api, '_FINNHUB_OBSERVED_QUOTES', {'AAA': dict(price=100, price_at='2026-09-10T09:35:59-04:00',
                        requested_at='2026-09-10T09:35:58-04:00', received_at='2026-09-10T09:36:00-04:00', source='finnhub')})
    adapter.run_cycle(tmp_path, clock, adapter.ExistingQuoteProvider())
    report = read_report(book.path)
    assert not report['positions']
    assert {i['status'] for i in report['intents']} == {'PENDING'}
    assert any(e['status'] == 'NO_VERIFIED_QUOTE' for e in report['errors'])


def test_closed_market_reports_quote_mode_without_collecting(tmp_path, monkeypatch):
    import runtime.selection_shadow_adapter as adapter
    import tools.selection_shadow_runner as runner
    monkeypatch.setattr(runner, 'collect_snapshot', lambda *a: pytest.fail('closed collection'))
    result = adapter.run_cycle(tmp_path, build_clock('US', '2026-09-12T12:00:00-04:00'), None)
    assert result['status'] == 'CLOSED'
    assert result['quote_source']['mode'] == 'NO_QUOTE_PROVIDER'


def test_winter_close_cycle_manages_existing_holdings_after_entry_cutoff(tmp_path, monkeypatch):
    from runtime.selection_shadow_book import SelectionShadowBook, read_report
    import runtime.selection_shadow_adapter as adapter
    import tools.selection_shadow_runner as runner
    book = SelectionShadowBook(tmp_path / 'data/shadow/selection_forward.db')
    entry = build_clock('US', '2026-01-07T09:36:00-05:00')
    book.record_snapshot(dict(market='US', session_date='2026-01-07', status='READY',
        collected_at=entry['now'], completed_at=entry['now'], candidates=[dict(ticker='AAA', dvol=2e8)]))
    book.decide(entry)
    def quote(clock, price):
        return {'AAA': dict(price=price, price_at=clock['now'], requested_at=clock['now'], received_at=clock['now'],
                            session_date=clock['session_date'], source='fixture', price_kind='LAST_PRICE_PAPER')}
    book.tick(entry, quote(entry, 100))
    assert len(read_report(book.path)['positions']) == 3
    late = build_clock('US', '2026-01-09T05:50:00+09:00')
    monkeypatch.setattr(adapter, 'build_clock', lambda *a: late)
    monkeypatch.setattr(runner, 'collect_snapshot', lambda *a: pytest.fail('late collection'))
    result = adapter.run_cycle(tmp_path, late, lambda market, tickers: quote(late, 113))
    assert len(result['exits']) == 3
    assert len(read_report(book.path)['closed']) == 3


def test_worker_failure_is_persisted_and_releases_single_worker(monkeypatch, tmp_path):
    import threading
    import types
    import runtime.selection_shadow_adapter as adapter
    from runtime.selection_shadow_book import read_report
    started = threading.Event()
    def broken(market):
        started.set()
        raise RuntimeError('secret URL must not be persisted')
    monkeypatch.setattr(adapter, 'ROOT', tmp_path)
    monkeypatch.setattr(adapter, '_worker_lock', threading.Lock())
    monkeypatch.setattr(adapter, '_last_started', float('-inf'))
    monkeypatch.setattr(adapter, '_last_market', None)
    monkeypatch.setattr(adapter, 'build_clock', broken)
    adapter.maybe_start(types.SimpleNamespace(enabled_markets={'US'}), 'US')
    assert started.wait(2)
    assert adapter._worker_lock.acquire(timeout=2)
    adapter._worker_lock.release()
    report = read_report(tmp_path / 'data/shadow/selection_forward.db')
    assert report['markets'][0]['latest_status'] == 'ERROR'
    assert 'secret URL' not in str(report)
def test_restart_expires_old_pending_before_quote_requests(tmp_path, monkeypatch):
    import runtime.selection_shadow_adapter as adapter
    import tools.selection_shadow_runner as runner
    from runtime.selection_shadow_book import SelectionShadowBook, read_report
    from tests.test_selection_shadow_book import snapshot, clock
    path = tmp_path / 'data/shadow/selection_forward.db'
    book = SelectionShadowBook(path)
    book.record_snapshot(snapshot())
    book.decide(clock())
    current = build_clock('KR', '2026-09-11T10:00:00+09:00')
    monkeypatch.setattr(adapter, 'build_clock', lambda *args: current)
    monkeypatch.setattr(runner, 'collect_snapshot', lambda *args: pytest.fail('late collection'))
    requests = []
    def provider(market, tickers):
        requests.extend(tickers)
        return {}
    adapter.run_cycle(tmp_path, current, provider)
    adapter.run_cycle(tmp_path, current, provider)
    report = read_report(path)
    assert {i['status'] for i in report['intents']} == {'ENTRY_WINDOW_MISSED'}
    assert not requests
    expirations = [e for e in report['errors'] if e['status'] == 'ENTRY_WINDOW_MISSED' and e['session_date'] == '2026-09-10']
    assert len(expirations) == 1 and expirations[0]['at'] == current['now']
    assert expirations[0]['details']['intent_count'] == 3


def test_preopen_diagnostics_do_not_reuse_previous_market(tmp_path, monkeypatch):
    import runtime.selection_shadow_adapter as adapter
    import tools.selection_shadow_runner as runner
    from tests.test_selection_shadow_book import snapshot
    from runtime.selection_shadow_book import SelectionShadowBook, read_report
    book = SelectionShadowBook(tmp_path / 'data/shadow/selection_forward.db')
    book.record_snapshot(snapshot(completed_at='2026-09-10T09:04:01+09:00'))
    current = build_clock('KR', '2026-09-11T08:45:00+09:00')
    monkeypatch.setattr(adapter, 'build_clock', lambda *args: current)
    monkeypatch.setattr(runner, 'collect_snapshot', lambda *args: dict(snapshot(status='EMPTY', candidates=[]), session_date='2026-09-11', collected_at=current['now'], completed_at=current['now']))
    class Provider:
        diagnostics = {'mode': 'CACHE_ONLY', 'requested_count': 4, 'last_available_price_at': 'OLD'}
        def __call__(self, *args):
            pytest.fail('provider called before open')
    result = adapter.run_cycle(tmp_path, current, Provider())
    assert result['quote_source']['mode'] == 'NAVER_BOUNDED'
    assert result['quote_source']['requested_count'] == 0
    assert result['quote_source']['last_available_price_at'] is None
    assert read_report(book.path)['markets'][0]['quote_source'] == result['quote_source']


@pytest.mark.parametrize('provider_error', [False, True])
def test_cycle_connections_close_on_success_and_provider_error(tmp_path, monkeypatch, provider_error):
    import sqlite3
    import runtime.selection_shadow_adapter as adapter
    from runtime.selection_shadow_book import SelectionShadowBook
    from tests.test_selection_shadow_book import snapshot
    book = SelectionShadowBook(tmp_path / 'data/shadow/selection_forward.db')
    book.record_snapshot(snapshot(status='EMPTY', candidates=[]))
    current = build_clock('KR', '2026-09-10T09:06:00+09:00')
    monkeypatch.setattr(adapter, 'build_clock', lambda *args: current)
    original = sqlite3.connect
    connections = []
    def connect(*args, **kwargs):
        db = original(*args, **kwargs)
        connections.append(db)
        return db
    monkeypatch.setattr(sqlite3, 'connect', connect)
    def provider(*args):
        if provider_error:
            raise RuntimeError('fixture')
        return {}
    if provider_error:
        with pytest.raises(RuntimeError, match='fixture'):
            adapter.run_cycle(tmp_path, current, provider)
    else:
        assert adapter.run_cycle(tmp_path, current, provider)['errors'] == []
    assert connections
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            db.execute('SELECT 1')


@pytest.mark.parametrize('market', ['KR', 'US'])
def test_provider_latest_timestamp_is_aware_max_and_rejections_keep_evidence(monkeypatch, market):
    import runtime.selection_shadow_adapter as adapter
    import kis_api
    import tools.analysis_quotes as analysis
    now = datetime.fromisoformat('2026-09-10T10:00:00+09:00')
    rows = {
        'A': dict(price=100, price_at='2026-09-10T10:00:00+09:00', requested_at=now.isoformat(), received_at=now.isoformat(), source='fixture', token='SECRET'),
        'B': dict(price=100, price_at='2026-09-10T01:00:01+00:00', requested_at=now.isoformat(), received_at=now.isoformat(), source='fixture', token='SECRET'),
        'C': dict(price=100, price_at='2026-09-10T09:59:00+09:00', requested_at=now.isoformat(), received_at=now.isoformat(), source='fixture', token='SECRET'),
    }
    monkeypatch.setattr(kis_api, 'get_observed_finnhub_quote', lambda ticker: rows[ticker])
    monkeypatch.setattr(analysis, 'get_quote_kr', lambda ticker, **kwargs: rows[ticker])
    provider = adapter.ExistingQuoteProvider()
    result = provider(market, rows)
    assert provider.diagnostics['last_available_price_at'] == rows['B']['price_at']
    rejected = normalize_quote(market, rows['B'], now)
    assert rejected['status'] == 'NO_VERIFIED_QUOTE'
    assert rejected['source'] == 'fixture' and rejected['price_at'] == rows['B']['price_at']
    assert 'token' not in rejected and 'price' not in rejected
