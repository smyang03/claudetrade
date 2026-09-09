import csv
import sqlite3
from datetime import datetime

import exchange_calendars as xc
import pytest

from tools.selection_shadow_runner import collect_snapshot


@pytest.mark.parametrize('market,previous,last,volume,expected', [
    ('KR', '100', '97', '30000000', 'READY'),
    ('KR', '100', '97.000000001', '30000000', 'EMPTY'),
    ('KR', '100', '96.999999999', '30000000', 'READY'),
    ('KR', '125', '100', '20000000', 'READY'),
    ('KR', '125', '100', '19999999.999999', 'EMPTY'),
    ('US', '1.75', '1.89', '150000000', 'READY'),
    ('US', '1.75', '1.889999999', '150000000', 'EMPTY'),
    ('US', '1.75', '1.890000001', '150000000', 'READY'),
    ('US', '1', '2', '50000000', 'READY'),
    ('US', '1', '2', '49999999.999999', 'EMPTY'),
    ('US', '1', '2', '250000000', 'EMPTY'),
    ('US', '1', '2', '249999999.999999', 'READY'),
])
def test_exact_collector_boundaries(tmp_path, market, previous, last, volume, expected):
    if market == 'US':
        source(tmp_path, [('2026-09-10', 'ABC', 1, 1, '2026-09-10T12:00:00+00:00')])
    path = prices(tmp_path, market, 'ABC')
    with path.open(newline='') as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        row['close'] = previous
    rows[-1].update(close=last, volume=volume)
    with path.open('w', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    now = '2026-09-10T09:34:00-04:00' if market == 'US' else '2026-09-10T08:55:00+09:00'
    assert collect_snapshot(tmp_path, market, '2026-09-10', now)['status'] == expected


@pytest.mark.parametrize('broken_schema', [False, True])
def test_collector_source_connections_close_on_success_and_error(tmp_path, monkeypatch, broken_schema):
    source(tmp_path, [('2026-09-10', 'ABC', 1, 1, '2026-09-10T12:00:00+00:00')])
    prices(tmp_path, 'US', 'ABC')
    if broken_schema:
        from contextlib import closing
        with closing(sqlite3.connect(tmp_path / 'data/analysis/us_swing_shadow.db')) as db, db:
            db.execute('DROP TABLE candidate_pool_all')
    original = sqlite3.connect
    connections = []
    def connect(*args, **kwargs):
        db = original(*args, **kwargs)
        connections.append(db)
        return db
    monkeypatch.setattr(sqlite3, 'connect', connect)
    result = collect_snapshot(tmp_path, 'US', '2026-09-10', '2026-09-10T09:34:00-04:00')
    assert result['status'] == ('INPUT_INCOMPLETE' if broken_schema else 'READY')
    assert connections
    for db in connections:
        with pytest.raises(sqlite3.ProgrammingError, match='closed'):
            db.execute('SELECT 1')


def prices(root, market, ticker, missing=False):
    cal = xc.get_calendar('XNYS' if market == 'US' else 'XKRX')
    dates = [str(d.date()) for d in cal.sessions_in_range('2026-07-20', '2026-09-09')][-22:]
    path = root / 'data' / 'price' / market.lower() / f'{market.lower()}_{ticker}.csv'
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='') as fh:
        writer = csv.writer(fh)
        writer.writerow(['date', 'open', 'high', 'low', 'close', 'volume'])
        for i, day in enumerate(dates):
            if missing and i == 10:
                continue
            close = (110 if i == 8 else 100) if market == 'US' else (95 if i == 21 else 100)
            writer.writerow([day, close, close, close, close, 30_000_000 if market == 'KR' else 2_000_000])
    return path


def source(root, rows):
    path = root / 'data/analysis/us_swing_shadow.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE candidate_pool_all(session_date,ticker,eligible,in_pool,recorded_at)')
        db.executemany('INSERT INTO candidate_pool_all VALUES(?,?,?,?,?)', rows)


def test_kr_preentry_inventory_needs_no_next_day_bar(tmp_path):
    prices(tmp_path, 'KR', '005930')
    snap = collect_snapshot(tmp_path, 'KR', '2026-09-10', datetime.fromisoformat('2026-09-10T08:55:00+09:00'))
    assert snap['status'] == 'READY'
    assert snap['candidates'][0]['ticker'] == '005930'
    assert snap['candidates'][0]['dvol'] == 2_850_000_000
    assert len(snap['source_rows'][0]['feature_window']) == 22
    assert snap['provenance']['inventory'][0]['instrument_type'] == 'UNKNOWN'


def test_us_raw_exclusions_and_prior_features(tmp_path):
    source(tmp_path, [('2026-09-10', 'AAA', 1, 1, '2026-09-10T12:00:00+00:00'),
                      ('2026-09-10', 'BBB', 0, 1, '2026-09-10T12:00:00+00:00')])
    prices(tmp_path, 'US', 'AAA')
    snap = collect_snapshot(tmp_path, 'US', '2026-09-10', datetime.fromisoformat('2026-09-10T09:04:00-04:00'))
    assert snap['status'] == 'READY'
    assert [c['ticker'] for c in snap['candidates']] == ['AAA']
    assert len(snap['source_rows']) == 2
    assert snap['excluded'][0]['reason'] == 'NOT_ELIGIBLE_IN_POOL'


def test_missing_intermediate_bar_blocks_snapshot(tmp_path):
    prices(tmp_path, 'KR', '005930', missing=True)
    snap = collect_snapshot(tmp_path, 'KR', '2026-09-10', datetime.fromisoformat('2026-09-10T08:55:00+09:00'))
    assert snap['status'] == 'INPUT_INCOMPLETE'
    assert snap['excluded'][0]['reason'] == 'NONCONTIGUOUS_FEATURE_WINDOW'


def test_missing_us_source_is_not_empty(tmp_path):
    source(tmp_path, [])
    snap = collect_snapshot(tmp_path, 'US', '2026-09-10', datetime.fromisoformat('2026-09-10T09:04:00-04:00'))
    assert snap['status'] == 'INPUT_INCOMPLETE'
    assert not snap['candidates']


def test_snapshot_failure_statuses_retry_then_freeze(tmp_path):
    from runtime.selection_shadow_book import SelectionShadowBook
    book = SelectionShadowBook(tmp_path / 'book.db')
    snap = dict(market='KR', session_date='2026-09-10', collected_at='2026-09-10T08:55:00+09:00', completed_at='2026-09-10T08:55:00+09:00', candidates=[])
    for status in ['INPUT_INCOMPLETE', 'ERROR', 'EMPTY']:
        assert book.record_snapshot(dict(snap, status=status))['status'] == status
    assert book.record_snapshot(dict(snap, status='ERROR'))['status'] == 'EMPTY'


def test_cli_status_is_readonly_and_run_once_has_no_fake_quote_provider(tmp_path, capsys, monkeypatch):
    import tools.selection_shadow_runner as runner
    import runtime.selection_shadow_adapter as adapter
    assert runner.main(['--root', str(tmp_path), 'status']) == 0
    assert 'false' in capsys.readouterr().out
    assert not (tmp_path / 'data').exists()
    clock = adapter.build_clock('KR', '2026-09-12T12:00:00+09:00')
    monkeypatch.setattr(runner, 'build_clock', lambda *a: clock)
    assert runner.main(['--root', str(tmp_path), 'run-once', '--market', 'KR']) == 0
    assert 'NO_QUOTE_PROVIDER' in capsys.readouterr().out


def test_us_future_and_null_source_timing_fail_closed(tmp_path):
    source(tmp_path, [('2026-09-10', 'AAA', 1, 1, None),
                      ('2026-09-10', 'BBB', 1, 1, '2026-09-11T12:00:00+00:00')])
    prices(tmp_path, 'US', 'AAA')
    snap = collect_snapshot(tmp_path, 'US', '2026-09-10', datetime.fromisoformat('2026-09-10T09:04:00-04:00'))
    assert snap['status'] == 'INPUT_INCOMPLETE'
    assert {e['reason'] for e in snap['excluded']} == {'UNKNOWN_SOURCE_TIMESTAMP', 'FUTURE_SOURCE_TIMESTAMP'}


def test_mixed_us_source_batches_cannot_freeze_stale_members(tmp_path):
    source(tmp_path, [('2026-09-10', 'AAA', 1, 1, '2026-09-10T12:00:00+00:00'),
                      ('2026-09-10', 'BBB', 0, 0, '2026-09-10T12:01:00+00:00')])
    prices(tmp_path, 'US', 'AAA')
    snap = collect_snapshot(tmp_path, 'US', '2026-09-10', datetime.fromisoformat('2026-09-10T09:04:00-04:00'))
    assert snap['status'] == 'INPUT_INCOMPLETE'
    assert snap['provenance']['source_completion'] == 'MIXED_SOURCE_BATCHES'
    assert len(snap['source_rows']) == 2


def test_nonfinite_derived_dollar_volume_blocks_collection(tmp_path):
    path = prices(tmp_path, 'KR', '005930')
    with path.open(newline='') as fh:
        rows = list(csv.DictReader(fh))
    rows[-1]['volume'] = '1e308'
    with path.open('w', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    snap = collect_snapshot(tmp_path, 'KR', '2026-09-10', datetime.fromisoformat('2026-09-10T08:55:00+09:00'))
    assert snap['status'] == 'INPUT_INCOMPLETE'
    assert not snap['candidates']
