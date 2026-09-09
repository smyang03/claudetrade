import json
import gc
import sqlite3
import subprocess
from datetime import datetime

import pytest

import dashboard.dashboard_server as dashboard
from runtime.selection_shadow_book import SelectionShadowBook, read_report
from tests.test_selection_shadow_book import clock, quote, snapshot
from tests.test_selection_shadow_runner import prices
from tools.selection_shadow_runner import collect_snapshot


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, 'BASE_DIR', tmp_path)
    import dashboard.selection_shadow_panel as panel
    class ReportTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat('2026-09-10T10:00:00+09:00')
    monkeypatch.setattr(panel, 'datetime', ReportTime)
    return dashboard.app.test_client()


def test_absent_api_never_creates_storage(client, tmp_path):
    response = client.get('/api/selection_shadow')
    assert response.status_code == 200
    assert response.json['available'] is False
    assert response.json['accounts'] == []
    assert not (tmp_path / 'data').exists()
    assert client.post('/api/selection_shadow').status_code == 405


def test_collector_to_get_accounting_and_readonly_boundary(client, tmp_path, monkeypatch):
    source = prices(tmp_path, 'KR', '005930')
    before = source.read_bytes()
    financial = tmp_path / 'data/shadow/virtual_books.db'
    financial.parent.mkdir(parents=True)
    with sqlite3.connect(financial) as db:
        db.execute('CREATE TABLE protected_balance(amount)')
        db.execute('INSERT INTO protected_balance VALUES(123456)')
    financial_before = financial.read_bytes()
    protected_state = tmp_path / 'state/phantom_positions.json'
    protected_state.parent.mkdir()
    protected_state.write_text('{"protected":true}', encoding='utf-8')
    state_before = protected_state.read_bytes()
    snap = collect_snapshot(tmp_path, 'KR', '2026-09-10', datetime.fromisoformat('2026-09-10T08:55:00+09:00'))
    path = tmp_path / 'data/shadow/selection_forward.db'
    book = SelectionShadowBook(path)
    book.record_snapshot(snap)
    book.decide(clock('2026-09-10T09:05:57+09:00'))
    assert client.get('/api/selection_shadow').json['accounts'][0]['return_pct'] is None
    book.tick(clock(), {'005930': quote()})
    marked_clock = clock('2026-09-10T09:07:00+09:00')
    marked_quote = dict(quote(105_000), price_at=marked_clock['now'], requested_at=marked_clock['now'], received_at=marked_clock['now'])
    book.tick(marked_clock, {'005930': marked_quote})
    response = client.get('/api/selection_shadow').json
    assert response['accounts'][0]['nav'] == 4_343_750
    assert response['positions'][0]['stale'] is True
    exit_clock = clock('2026-09-10T09:08:00+09:00')
    book.tick(exit_clock, {'005930': dict(marked_quote, price=113_000, price_at=exit_clock['now'], requested_at=exit_clock['now'], received_at=exit_clock['now'])})
    expected = read_report(path)
    before_db = path.read_bytes()
    def forbidden(*args, **kwargs):
        raise AssertionError('GET crossed observation/write boundary')
    monkeypatch.setattr(SelectionShadowBook, '__init__', forbidden)
    import runtime.selection_shadow_adapter as adapter
    import tools.selection_shadow_runner as runner
    monkeypatch.setattr(adapter.ExistingQuoteProvider, '__call__', forbidden)
    monkeypatch.setattr(runner, 'collect_snapshot', forbidden)
    result = client.get('/api/selection_shadow').json
    assert result['accounts'] == expected['accounts']
    assert result['closed'] == expected['closed']
    assert result['experiments'] == expected['experiments']
    assert all(a['cash'] == 4_383_750 and a['closed_pnl'] == 63_750 for a in result['accounts'])
    assert path.read_bytes() == before_db and source.read_bytes() == before
    assert protected_state.read_bytes() == state_before
    assert financial.read_bytes() == financial_before


def test_locked_and_invalid_db_report_warning(client, tmp_path):
    path = tmp_path / 'data/shadow/selection_forward.db'
    book = SelectionShadowBook(path)
    book.record_snapshot(snapshot())
    # sqlite connection context managers commit but are closed on collection.
    gc.collect()
    with sqlite3.connect(path) as lock:
        lock.execute('PRAGMA journal_mode=DELETE')
        lock.execute('BEGIN EXCLUSIVE')
        result = client.get('/api/selection_shadow').json
        assert result['available'] is False
        assert result['errors'][0]['code'] == 'REPORT_READ_ERROR'
        assert result['errors'][0]['at']
        lock.rollback()
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM snapshots').fetchone()[0] == 1


@pytest.mark.parametrize('status,candidates', [('EMPTY', []), ('INPUT_INCOMPLETE', []), ('ERROR', [])])
def test_api_distinguishes_collection_states(client, tmp_path, status, candidates):
    book = SelectionShadowBook(tmp_path / 'data/shadow/selection_forward.db')
    book.record_snapshot(snapshot(status=status, candidates=candidates))
    report = client.get('/api/selection_shadow').json
    assert report['available'] and report['markets'][0]['snapshot_status'] == status
    assert report['accounts'] == []


def test_api_unavailable_schema_does_not_construct_missing_tables(client, tmp_path):
    path = tmp_path / 'data/shadow/selection_forward.db'
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE unrelated(value)')
    report = client.get('/api/selection_shadow').json
    assert not report['available'] and report['errors'][0]['code'] == 'REPORT_READ_ERROR'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT name FROM sqlite_master').fetchall() == [('unrelated',)]


def test_api_corrupt_persisted_metadata_is_unavailable_not_server_error(client, tmp_path):
    book = SelectionShadowBook(tmp_path / 'data/shadow/selection_forward.db')
    book.record_snapshot(snapshot())
    with sqlite3.connect(book.path) as db:
        db.execute("UPDATE experiments SET parameters='broken json'")
    response = client.get('/api/selection_shadow')
    assert response.status_code == 200
    assert response.json['available'] is False
    assert response.json['errors'][0]['code'] == 'REPORT_READ_ERROR'


def test_cycle_restart_skipping_entire_d7_records_verified_schedule_on_get(client, tmp_path, monkeypatch):
    import runtime.selection_shadow_adapter as adapter
    import tools.selection_shadow_runner as runner
    prices(tmp_path, 'KR', '005930')
    snap = collect_snapshot(tmp_path, 'KR', '2026-09-10', datetime.fromisoformat('2026-09-10T08:55:00+09:00'))
    real_clock = adapter.build_clock
    current = real_clock('KR', '2026-09-10T09:06:00+09:00')
    monkeypatch.setattr(adapter, 'build_clock', lambda *args: current)
    monkeypatch.setattr(runner, 'collect_snapshot', lambda *args: snap)
    def quotes(market, tickers):
        return {t: dict(price=100_000, price_at=current['now'], requested_at=current['now'],
                        received_at=current['now'], session_date=current['session_date'],
                        source='fixture', price_kind='LAST_PRICE_PAPER') for t in tickers}
    adapter.run_cycle(tmp_path, current, quotes)
    path = tmp_path / 'data/shadow/selection_forward.db'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT COUNT(*) FROM positions').fetchone()[0] == 3
        assert not db.execute("SELECT 1 FROM session_observations WHERE session_date='2026-09-18'").fetchone()
    # A fresh run_cycle constructs a new book. No D2-D7 tick or quote occurred.
    current = real_clock('KR', '2026-09-21T10:00:00+09:00')
    monkeypatch.setattr(runner, 'collect_snapshot', lambda *args: pytest.fail('late collection'))
    result = adapter.run_cycle(tmp_path, current, quotes)
    assert len(result['exits']) == 3
    monkeypatch.setattr(adapter, '_calendar', lambda *args: pytest.fail('calendar lookup on GET'))
    report = client.get('/api/selection_shadow').json
    assert len(report['closed']) == 3
    for position in report['closed']:
        assert position['scheduled_exit_session'] == '2026-09-18'
        assert position['scheduled_exit_at'] == '2026-09-18T15:15:00+09:00'
        assert position['exit_at'] == '2026-09-21T10:00:00+09:00'
        assert position['holding_sessions'] == 8 and position['delay_sessions'] == 1
        assert position['delayed'] == 1 and position['pnl'] == -1250
    with sqlite3.connect(path) as db:
        # Lookup happened on restart; do not invent a D7 observation timestamp.
        assert db.execute("SELECT observed_at FROM session_observations WHERE session_date='2026-09-18'").fetchone()[0] == current['now']


def test_panel_executes_escaping_and_retains_last_good_on_failure(client):
    page = client.get('/virtual').get_data(as_text=True)
    assert 'id="selection-shadow-panel"' in page
    from dashboard.selection_shadow_panel import PANEL_JS
    script = '''
const nodes = {};
global.document = {getElementById: id => nodes[id] ||= {innerHTML:'', textContent:''}};
''' + PANEL_JS + '''
const hostile = '<img src=x onerror="global.pwned=1">&';
renderSelectionShadow({available:true, authority:'SHADOW_ONLY', contract:'forward_quote_v1',
 experiments:[], markets:[{market:'US',snapshot_status:'INPUT_INCOMPLETE',latest_status:'ENTRY_WINDOW_MISSED',quote_source:{mode:'CACHE_ONLY'}}],
 accounts:[{market:'US',rule:'baseline_k1',return_pct:null}],
 intents:[{ticker:hostile,reason:hostile}], positions:[],closed:[],errors:[]});
const html = nodes['selection-shadow-content'].innerHTML;
if(html.includes('<img') || !html.includes('&lt;img') || !html.includes('&amp;')) throw Error('unsafe HTML');
if(!html.includes('CACHE_ONLY') || !html.includes('INPUT_INCOMPLETE') || !html.includes('ENTRY_WINDOW_MISSED')) throw Error('missing states');
if(!html.includes('첫 유효 관측 대기')) throw Error('false zero success');
renderSelectionShadow({available:false,errors:[{code:'REPORT_READ_ERROR',at:'failure-time'}]});
if(nodes['selection-shadow-content'].innerHTML !== html) throw Error('lost previous report');
if(!nodes['selection-shadow-warning'].textContent.includes('failure-time')) throw Error('missing failure time');
'''
    result = subprocess.run(['C:/Program Files/nodejs/node.exe', '-e', script], capture_output=True, text=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
