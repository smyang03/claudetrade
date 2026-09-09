"""Read-only candidate collection for the independent forward paper book."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sqlite3
import sys
from pathlib import Path

if __package__ in {None, ''}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime.selection_shadow_adapter import aware_now, build_clock


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def collect_snapshot(root, market: str, session_date: str, now=None) -> dict:
    started = aware_now(now)
    clock = build_clock(market, started)
    if clock['session_date'] != session_date or not clock['is_session']:
        raise ValueError('collection requires current verified session')
    sessions = [d for d in clock['session_dates'] if d < session_date][-22:]
    root = Path(root)
    snap = dict(market=market, session_date=session_date, signal_date=sessions[-1] if sessions else None,
                collected_at=started.isoformat(), candidates=[], source_rows=[], excluded=[],
                provenance={'calendar': clock['calendar'], 'feature_sessions': sessions, 'inventory': [],
                            'historical_availability': 'UNKNOWN', 'source_completion': 'UNKNOWN'})
    incomplete = False
    try:
        if len(sessions) != 22:
            raise ValueError('insufficient verified history')
        price_dir = root / 'data/price' / market.lower()
        if market == 'US':
            source = root / 'data/analysis/us_swing_shadow.db'
            with sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True) as db:
                db.row_factory = sqlite3.Row
                db.execute('BEGIN')
                records = [dict(row) for row in db.execute(
                    'SELECT * FROM candidate_pool_all WHERE session_date=? ORDER BY ticker', (session_date,))]
            snap['provenance']['source'] = str(source.relative_to(root))
            snap['provenance']['source_completion'] = 'ATOMIC_NONEMPTY_INVENTORY' if records else 'UNKNOWN'
            incomplete = not records
            if len({row.get('recorded_at') for row in records}) > 1:
                snap['provenance']['source_completion'] = 'MIXED_SOURCE_BATCHES'
                incomplete = True
        else:
            records = [{'ticker': p.stem[3:], 'path': str(p.relative_to(root)), 'instrument_type': 'UNKNOWN'}
                       for p in sorted(price_dir.glob('kr_*.csv'))]
            incomplete = not records
            snap['provenance']['source_completion'] = 'FILE_INVENTORY' if records else 'UNKNOWN'
        for raw in records:
            ticker = str(raw.get('ticker', ''))
            item = {'ticker': ticker, 'raw': raw, 'raw_sha256': _digest(raw)}
            snap['source_rows'].append(item)
            path = price_dir / f'{market.lower()}_{ticker}.csv'
            inventory = dict(ticker=ticker, path=str(path.relative_to(root)), instrument_type='UNKNOWN')
            snap['provenance']['inventory'].append(inventory)
            reason = None
            if market == 'US':
                try:
                    if not raw.get('recorded_at'):
                        raise ValueError('unknown source timestamp')
                    if aware_now(raw['recorded_at']) > started:
                        reason = 'FUTURE_SOURCE_TIMESTAMP'
                        incomplete = True
                except (KeyError, ValueError, TypeError):
                    reason = 'UNKNOWN_SOURCE_TIMESTAMP'
                    incomplete = True
                if reason is None and not (raw.get('eligible') == 1 and raw.get('in_pool') == 1):
                    reason = 'NOT_ELIGIBLE_IN_POOL'
            if reason is None:
                try:
                    before = path.stat()
                    with path.open(encoding='utf-8-sig', newline='') as fh:
                        rows = list(csv.DictReader(fh))
                    after = path.stat()
                    inventory.update(size=before.st_size, mtime_ns=before.st_mtime_ns)
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError('SOURCE_CHANGED_DURING_READ')
                    window = [r for r in rows if sessions[0] <= r.get('date', '') <= sessions[-1]]
                    window.sort(key=lambda r: r['date'])
                    item['feature_window'] = window
                    item['feature_window_sha256'] = _digest(window)
                    if [r['date'] for r in window] != sessions:
                        raise ValueError('NONCONTIGUOUS_FEATURE_WINDOW')
                    closes = [float(r['close']) for r in window]
                    volume = float(window[-1]['volume'])
                    if not all(math.isfinite(c) and c > 0 for c in closes) or not math.isfinite(volume) or volume < 0:
                        raise ValueError('INVALID_FEATURE_VALUE')
                    returns = [100 * (b / a - 1) for a, b in zip(closes, closes[1:])]
                    features = {'max21': max(returns), 'chg_pct': returns[-1], 'dvol': closes[-1] * volume}
                    if not all(math.isfinite(value) for value in features.values()):
                        raise ValueError('INVALID_DERIVED_FEATURE_VALUE')
                    item['features'] = features
                    qualifies = (features['chg_pct'] <= -3 and features['dvol'] >= 2e9) if market == 'KR' else (
                        1e8 <= features['dvol'] < 5e8 and features['max21'] >= 8)
                    if qualifies:
                        snap['candidates'].append(dict(ticker=ticker, dvol=features['dvol'], features=features))
                    else:
                        reason = 'OUTSIDE_POOL_THRESHOLDS'
                except (OSError, ValueError, KeyError, TypeError, csv.Error) as exc:
                    reason = str(exc) if isinstance(exc, ValueError) else 'MISSING_OR_INVALID_PRICE_FILE'
                    incomplete = True
            if reason:
                snap['excluded'].append(dict(ticker=ticker, reason=reason))
        snap['status'] = 'INPUT_INCOMPLETE' if incomplete else ('READY' if snap['candidates'] else 'EMPTY')
    except (OSError, sqlite3.Error, ValueError, TypeError, csv.Error) as exc:
        snap['status'] = 'INPUT_INCOMPLETE' if isinstance(exc, (OSError, sqlite3.OperationalError)) else 'ERROR'
        snap['provenance']['error'] = type(exc).__name__
    snap['provenance']['source_rows_sha256'] = _digest(snap['source_rows'])
    snap['completed_at'] = aware_now(now).isoformat()
    return snap


def main(argv=None):
    import argparse
    from runtime.selection_shadow_adapter import run_cycle
    from runtime.selection_shadow_book import SelectionShadowBook, read_report
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status')
    for command in ('collect', 'run-once'):
        sub.add_parser(command).add_argument('--market', choices=['KR', 'US'], required=True)
    args = parser.parse_args(argv)
    path = args.root / 'data/shadow/selection_forward.db'
    try:
        if args.command == 'status':
            output = read_report(path, aware_now().isoformat())
        else:
            clock = build_clock(args.market)
            if args.command == 'collect':
                snapshot = collect_snapshot(args.root, args.market, clock['session_date'])
                output = SelectionShadowBook(path).record_snapshot(snapshot)
            else:
                output = run_cycle(args.root, clock, None)
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'ERROR', 'error_type': type(exc).__name__,
                          'quote_source': {'mode': 'NO_QUOTE_PROVIDER'}}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
