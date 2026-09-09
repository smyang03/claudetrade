# Task 2 implementation report

Status: DONE_WITH_CONCERNS (intentional source availability limits described below). Base HEAD b7971c5.

## Implemented interfaces

- `tools.selection_shadow_runner.collect_snapshot(root, market: str, session_date: str, now=None) -> dict`
- `tools.selection_shadow_runner.main(argv=None) -> int`: `--root ROOT status`, `--root ROOT collect --market KR|US`, `--root ROOT run-once --market KR|US`.
- `runtime.selection_shadow_adapter.build_clock(market: str, now=None) -> dict`
- `runtime.selection_shadow_adapter.run_cycle(root, clock: dict, quote_provider) -> dict`
- `runtime.selection_shadow_adapter.maybe_start(bot, market: str) -> None`
- `runtime.selection_shadow_adapter.ExistingQuoteProvider`: plain `(market, tickers) -> quote dict` callable, without order methods.
- `runtime.selection_shadow_adapter.normalize_quote(market, raw, now=None)` preserves original request/receipt/observed timestamps or returns explicit `NO_VERIFIED_QUOTE`.
- `kis_api.get_observed_finnhub_quote(ticker)` reads a bounded 256-entry process-local copy cache and never requests a quote.

## Collection and input validity

US reads `data/analysis/us_swing_shadow.db` via SQLite `mode=ro` and one explicit read transaction, selecting `candidate_pool_all` for the current verified exchange-local entry session. Raw records, eligibility exclusions and provider `recorded_at` are preserved. The existing `_record_candidate_pool_all` producer uses one executemany and commit. Since its UPSERT does not delete old rows, mixed timestamp batches fail closed as `INPUT_INCOMPLETE` with `MIXED_SOURCE_BATCHES`. A nonempty consistent batch proves inventory availability; missing source/session or zero rows do not prove successful EMPTY. No completion-marker semantics were invented from `runs` rows.

KR freezes the observed `data/price/kr/kr_*.csv` inventory; it does not consult a future trading ledger. All instrument types are explicitly `UNKNOWN`, and no ETF/stock exclusion is inferred. Both markets use the last 22 verified completed sessions, requiring the CSV dates to match exactly; duplicate/missing sessions fail closed rather than creating multi-session returns. The entire raw selected window and SHA256, raw source row and SHA256, inventory size/mtime, calendar sessions, source provenance and actual acquisition/completion timestamps are retained. File mutation during read is rejected. No original availability timestamp is fabricated for daily files; historical availability is marked UNKNOWN.

Features are recomputed solely from those completed bars: prior close * prior volume in original currency units; MAX21 is the maximum of exactly 21 adjacent close returns. KR requires return <= -3% and dvol >= 2e9; US requires eligible AND in_pool, 1e8 <= dvol < 5e8 and MAX21 >= 8. No next-session completed bar, price floor, IBS or news filter was added. Missing/invalid features or nonfinite derived values block the entire snapshot. Raw exclusions remain visible, even when other candidates qualify. READY/EMPTY become immutable in the book. The controller explicitly authorized the narrow addition of `INPUT_INCOMPLETE` and `ERROR` snapshot statuses, including their blocked classification in read_report; no accounting rule was changed.

Failed input collection is retried no more frequently than every 300 seconds using persisted snapshot completion, including across worker restarts. Frozen inputs are never recollected by the cycle. Heavy collection is restricted to open-30min through open+45min. Late startup records ENTRY_WINDOW_MISSED without rereading the universe, while still managing held positions.

## Clock, quote and worker behavior

The adapter directly calls installed XKRX/XNYS exchange calendars, validates real sessions and applies existing known KR holiday overrides. No scheduler weekday fallback is used. Clocks are serialized in exchange-local time so US sessions remain correct across KST midnight: summer open22:30/close05:00KST, winter open23:30/close06:00KST. Early closes are taken from XNYS. Calendar failure propagates into a persisted sanitized worker ERROR. Verified session_dates contain calendar history through the current local date, excluding known overrides.

The cycle persists the snapshot, refreshes the clock after collection, decides, asks the book for pending/held tickers only, then obtains quotes and refreshes the clock again before ticking. Session transitions and closed windows cannot produce fills. No broker object enters the book. CLI run-once deliberately supplies no provider and reports NO_QUOTE_PROVIDER; no fake timestamp/fill fallback is present. CLI status remains read-only and does not create an absent DB.

KR uses existing Naver stock polling and its shared .25-second process throttle. A small lock protects throttle reservation among threads. Existing return values are unchanged; additive metadata preserves stock-level `localTradedAt`, original request/receipt time, source and price kind. It does not read integrated/NXT price objects. The worker issues at most four KR requests per cycle, each with a two-second requests timeout, and rotates across the required list. Exhausted batch slots return NO_VERIFIED_QUOTE. Requests timeout is a requests connect/read timeout, not a hard wall-clock deadline for the whole cycle.

US is explicitly CACHE_ONLY, as directed by the controller after confirming no existing shared Finnhub limiter. The existing Finnhub producer now preserves original `t`, request and receipt times and populates the bounded cache. The worker does not call `_get_price_us_finnhub`, `get_current_price`, KIS live-key paths or any US network API. Cache reads do not renew timestamps. Both request and observed price must satisfy the existing post-decision rule for entry; an earlier cached quote cannot fill. Age60, positive finite price and regular exchange-session validation remain strict. Missing/null/naive/future/old timestamps remain unverified.

`QUOTE_SOURCE` status details expose `mode`, `required_count`, `requested_count`, `last_available_price_at`, `unverified_count`, and `missing_reason`. CLOSED and ENTRY_WINDOW_MISSED diagnostics expose the relevant source mode as well. The dashboard can use these with persisted snapshot provenance; no secrets or request URLs are included in worker errors.

The hook is a bounded seven-line insertion at the beginning of the existing main loop, before schedule.run_pending, independently iterating enabled KR/US markets. It does not depend on legacy `session_active`, `current_market`, phantom housekeeping, or fixed05:00KST close. One process-wide nonblocking lock permits one worker, with no queue and >=15 seconds between starts. Alternation gives both enabled markets a turn (normally ~30 seconds per market); a closed KR market cannot monopolize US winter-close observations. Worker failure logs/persists only exception class and releases the lock. No scheduler job, persistent new process, restart, order switch, source DB write or operational forward DB write was performed during implementation.

## Tests and TDD evidence

All new fixtures use temporary directories/SQLite/CSV and fake network responses; no real orders or source mutations. Production-changing requirements were tested RED before their implementation.

Initial RED command:

`C:/Users/Unknown/anaconda3/envs/upbit/python.exe -m pytest tests/test_selection_shadow_runner.py tests/test_selection_shadow_adapter.py -q`

Output: 2 collection errors in 1.81s, expected ModuleNotFoundError for the not-yet-created collector/adapter. First GREEN: `10 passed in 3.94s`.

Second RED command:

`C:/Users/Unknown/anaconda3/envs/upbit/python.exe -m pytest tests/test_selection_shadow_adapter.py -q`

Output: `7 failed, 5 passed in 4.47s`, expected missing run_cycle, worker, US cache and metadata producer fields. GREEN after implementing these: `17 passed in 4.49s` across both new test files.

Third RED exposed missing CLI, missing closed source-mode reporting and null source timestamp handling (`4 failed, 21 passed in 4.79s`; one of these failures was a test using report error `code` instead of the actual `status` field, corrected without production change). A separately controlled null-timestamp test then demonstrated the actual fallback bug:

`C:/Users/Unknown/anaconda3/envs/upbit/python.exe -m pytest tests/test_selection_shadow_adapter.py -q -k null_timestamp`

Output: `3 failed, 15 deselected in 3.75s` because null timestamps were replaced by current wall time. Explicit missing checks fixed it. GREEN: `25 passed in 4.47s`.

Self-review RED for stale mixed US source batches and derived dollar-volume overflow:

`C:/Users/Unknown/anaconda3/envs/upbit/python.exe -m pytest tests/test_selection_shadow_runner.py -q -k 'mixed_us or nonfinite_derived'`

Output: `2 failed, 7 deselected in 4.04s` (incorrect READY instead of INPUT_INCOMPLETE). Both now fail closed.

Final combined regression command:

`C:/Users/Unknown/anaconda3/envs/upbit/python.exe -m pytest tests/test_selection_shadow_runner.py tests/test_selection_shadow_adapter.py tests/test_selection_shadow_book.py tests/test_preopen_scheduler.py tests/test_phantom_book.py tests/test_session_cache_reset.py tests/test_session_evidence_quality.py -q`

Final output: `137 passed in 13.36s`, exit0. This includes29 new tests/parameter cases,59 existing book tests, and related scheduler/phantom/session tests. The new tests include late winter-close management of actual temporary-book holdings, worker exception persistence/release, quote rotation, stale pre-decision US cache, and non-overlap/fairness. The entire repository suite was not run because unrelated modules can have operational side effects; this is the bounded relevant regression suite.

Compile command:

`C:/Users/Unknown/anaconda3/envs/upbit/python.exe -m py_compile runtime/selection_shadow_adapter.py runtime/selection_shadow_book.py tools/selection_shadow_runner.py tools/analysis_quotes.py kis_api.py trading_bot.py tests/test_selection_shadow_runner.py tests/test_selection_shadow_adapter.py`

Passed exit0, no output. `git diff --check` on changed tracked paths passed with only existing CRLF-to-LF notices for kis_api.py/trading_bot.py. Direct script `tools/selection_shadow_runner.py --help` passed and lists status/collect/run-once.

## Files and self-review

Created: runtime/selection_shadow_adapter.py; tools/selection_shadow_runner.py; tests/test_selection_shadow_adapter.py; tests/test_selection_shadow_runner.py; this report.

Modified: runtime/selection_shadow_book.py (status allowlist/read-model blocked classification only); tools/analysis_quotes.py (timestamp metadata/shared throttle reservation); kis_api.py (Finnhub metadata/bounded observation cache only); trading_bot.py (independent main-loop hook only).

Self-review read the complete new modules and focused diffs in large existing files. It caught null timestamp substitution, derived overflow and stale source-batch risks, all addressed with regression tests. No unrelated dirty files were staged, reset or rewritten. The ignored .superpowers report is force-added individually. No push or operational restart was performed.

## Explicit limitations / concerns

- US may remain at zero fills if the bot does not naturally request selected symbols after the decision. CACHE_ONLY is visible; this is not active US REST collection.
- KR four-request rotation means larger held portfolios can exceed60seconds between observations. Those observations remain stale rather than relaxing validity or expanding provider budgets. A worker blocked on file I/O or network can delay the other market, but cannot overlap or grow a queue.
- All KR instrument types are UNKNOWN, and any incomplete constituent history blocks the full collection; this is intentionally conservative and may initially leave KR INPUT_INCOMPLETE.
- No trusted source completion marker currently exists for a zero-row US session; zero rows remain INPUT_INCOMPLETE. Existing source batches with multiple recorded_at values also remain incomplete.
- Calendar correctness depends on installed exchange_calendars plus existing KR overrides. Missing/unverifiable dates fail closed; no claim of future-calendar infallibility is made.
- The UI integration and operational activation are subsequent tasks. This task has not started an operational forward account or restarted the running bot.
