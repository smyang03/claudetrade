# Selection Shadow Forward Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect frozen candidate collection, independent paper accounts, bot quotes and a read-only dashboard without order authority.

**Architecture:** A dedicated SQLite book owns all paper state and transactions. Input and quote adapters pass plain data to it; the existing bot only launches a throttled worker. The dashboard reads this book without creating or mutating it.

**Tech Stack:** Python 3.11, SQLite WAL, pytest, existing exchange_calendars and Flask, existing quote adapters.

**Spec:** `docs/superpowers/specs/2026-09-10-selection-shadow-forward-design.md`

## Global Constraints

- 실주문 스위치, 계좌 잔고, 기존 포지션 및 기존 유령 원장은 변경하지 않는다.
- Each KR/US rule has independent capital 4,320,000 KRW and daily maximum budget 540,000 KRW; random master seed 20260910; rules baseline_k1/random_k1/random_k3; slots 7/7/21.
- US FX is fixed 1390; round-trip original-notional costs KR .25%, US .50%, half paid at entry, half paid/reserved at exit/MTM.
- Entry window is regular open +5 through +45 minutes. No retro fills. Verified quotes require finite positive prices, matching session, known price timestamp with age <=60 seconds, and request after decision.
- `forward_quote_v1`: fresh observed TP12; SL-25 and US prior-session peak BE4 in close-15min window; D7 inclusive; delayed exits marked explicitly.
- Storage: `data/shadow/selection_forward.db`; no existing financial ledger writes. No order API calls. No real-source mutations in tests.
- Unknown timing, missing history and failed collection remain visible and fail closed. No weekday-only fallback masquerading as a verified exchange calendar.
- Existing changes belong to the user. Stage/commit only task-owned paths; do not push or bulk-add data artifacts.
- Commands use `C:/Users/Unknown/anaconda3/envs/upbit/python.exe` and PowerShell. Use apply_patch for edits. Tests use temporary directories.

## Shared data contracts

Timestamps are timezone-aware ISO strings. Public adapters may accept datetime then serialize before book calls. All functions reject naive or future timestamps that affect decisions.

```python
snapshot = {
    'market': 'KR', 'session_date': '2026-09-10', 'signal_date': '2026-09-09',
    'collected_at': '2026-09-10T09:04:00+09:00',
    'completed_at': '2026-09-10T09:04:01+09:00', 'status': 'READY',
    'candidates': [{'ticker': '005930', 'dvol': 3000000000.0, 'features': {}}],
    'source_rows': [], 'excluded': [], 'provenance': {},
}
clock = {
    'market': 'KR', 'session_date': '2026-09-10',
    'now': '2026-09-10T09:06:00+09:00',
    'open_at': '2026-09-10T09:00:00+09:00',
    'close_at': '2026-09-10T15:30:00+09:00',
    'session_dates': ['2026-09-09', '2026-09-10'],
}
quotes = {'005930': {
    'price': 100000.0, 'price_at': '2026-09-10T09:05:59+09:00',
    'requested_at': '2026-09-10T09:05:58+09:00',
    'received_at': '2026-09-10T09:06:00+09:00',
    'session_date': '2026-09-10', 'source': 'fixture', 'price_kind': 'LAST_PRICE_PAPER',
}}
```

Book report envelope is `available, authority, contract, accounts, markets, positions, closed, intents, errors, last_updated`. Each account includes market/rule/capital/cash/nav/return_pct/mdd_pct/exposure_pct/closed_count/open_count/mature_count/stale_count/closed_pnl/open_pnl. `return_pct` is null before first actual paper fill. Positions/intents include timing and source fields, not secrets or full raw source rows.

### Task 1: Transactional independent paper book

**Files:** Create `runtime/selection_shadow_book.py`, optionally `runtime/selection_shadow_store.py` for schema/readonly reporting to keep responsibilities small; test `tests/test_selection_shadow_book.py`.

**Interfaces:**

```python
class SelectionShadowBook:
    def __init__(self, path): ...  # constructor creates only this dedicated DB
    def record_snapshot(self, snapshot: dict) -> dict: ...
    def decide(self, clock: dict) -> list[dict]: ...
    def required_tickers(self, market: str) -> list[str]: ...
    def tick(self, clock: dict, quotes: dict) -> dict: ...
    def record_status(self, market: str, session_date: str, status: str,
                      now: str, details: dict) -> None: ...

def read_report(path, now: str | None = None) -> dict: ...
```

- [x] Write failing behavioral tests using real temporary SQLite. A concrete money fixture: capital4320000, budget540000, KR price100000 -> each one-name rule buys5, cash3819375; close100000 MTM NAV4318750; fees1250 total. All three rules selecting one ticker have separate positions and cash, not one shared entry.
- [x] Run focused tests and record expected red evidence. Cover duplicate ticks, future/naive timestamps, bad/stale quote, entry-window cutoff, immutable first successful snapshot, failed snapshot retry, no data/empty distinction, deterministic selection independent of input order, expensive picks no substitution, same-day close proceeds not reused.
- [x] Implement schema/transactions. Use UNIQUE(market,session,rule,ticker) for intents and unique fill/close events. Snapshot READY/EMPTY locks a session; failures may be recorded/retried. Decide transaction freezes chosen tickers and per-slot allocation once. Fill transaction verifies intent status, quote timing/clock, and available cash; receipt/write/update/ledger must rollback together. No I/O while holding write lock.

```python
entry_cost = qty * quote_price * fx
cash_after = cash_before - entry_cost * (1 + fee_pct / 200)
marked_net = qty * latest_price * fx - entry_cost * fee_pct / 200
# At exit: add gross proceeds less original-notional half fee.
exit_receipt = qty * exit_price * fx - entry_cost * fee_pct / 200
```

- [x] Persist per-session peak so a same-day +4% cannot activate BE. Count holding days against supplied verified `session_dates`; quote gaps must not increment bar counters. Overdue D7 exits use next fresh regular-session quote with delay metadata. SL/BE pending state follows spec; no retrospective highs.
- [x] Test transaction rollback via a real SQLite trigger that aborts OPEN insertion; assert cash/position/event state unchanged. Test concurrent duplicate intent fills and report read-only behavior when DB absent. Reconciliation must block new entries on inconsistency and expose an error without losing exits/diagnostics.
- [x] Run `python -m pytest tests/test_selection_shadow_book.py -q`, self-review, commit only owned files. Write task report with public API/report schema and RED/GREEN output.

### Task 2: Frozen collection, verified clock/quotes and bot worker

**Files:** Create `tools/selection_shadow_runner.py`, `runtime/selection_shadow_adapter.py`, `tests/test_selection_shadow_runner.py`, `tests/test_selection_shadow_adapter.py`; modify only bounded hook locations in `trading_bot.py` and if needed existing quote-return metadata producers without changing their callers' behavior. Narrow interface correction permitted in `runtime/selection_shadow_book.py` and its test: accept retryable `INPUT_INCOMPLETE`/`ERROR` snapshot statuses directly, preserving existing READY/EMPTY immutability and accounting behavior.

**Consumes:** Task1 `SelectionShadowBook` methods and shared snapshot/clock/quote contracts.
**Produces:**

```python
def collect_snapshot(root, market: str, session_date: str, now=None) -> dict: ...
def build_clock(market: str, now=None) -> dict: ...  # raise on unverifiable calendar
def run_cycle(root, clock: dict, quote_provider) -> dict: ...
def maybe_start(bot, market: str) -> None: ...  # nonblocking, <=once/15s, one worker
```

- [x] Write failing fixtures for US eligible/in_pool source and KR pre-entry price-file universe; preserve all raw candidate records and exclusion reasons. A missing intermediate feature bar must produce INPUT_INCOMPLETE, not a multi-day MAX jump. A missing US source session is not EMPTY unless a completion marker proves successful zero candidates. Last 22 sessions must match verified calendar; KR pool <=-3%, dvol>=2e9; US 1e8<=dvol<5e8 and MAX21>=8. Do not require next day's completed bar to discover today's candidate.
- [x] Implement collector using current prior-completed inputs and immutable snapshots. Persist raw selected feature windows/source identifiers; record file inventory and unknown instrument types. Read source DB via `mode=ro`; no untracked research tool dependency necessary. Use installed calendar API, no new packages or web data vendors.
- [x] Tests for clock: US summer/winter and early-close session, KR known holiday, calendar failure. Existing `preopen.scheduler` has fallback behavior; adapter must validate a real calendar session instead of accepting weekday fallback.
- [x] Implement cycle with a fresh clock after collection and fresh request after `decide`. `required_tickers` combines selected pending and held tickers only. Never pass a broker object into the book. Clock outside regular session records CLOSED; late startup records ENTRY_WINDOW_MISSED and can still manage old holdings. Collection work is not repeated every tick once frozen.

```python
book.record_snapshot(snapshot)
book.decide(clock)
tickers = book.required_tickers(clock['market'])
quotes = quote_provider(clock['market'], tickers)
book.tick(fresh_clock, quotes)
```

- [x] Inspect existing quote payloads and use only timestamps supplied for actual observed market prices. Float-only or response-time-only data must return explicit NO_VERIFIED_QUOTE, not invented exchange times. Preserve request/received timestamps and price kind. Reuse shared rate limits and cached quote context without adding unlimited HTTP requests. Do not weaken the age60 rule to make test/production fills appear.
- [x] Add a separate nonblocking hook in the existing main loop of `trading_bot.py` for enabled KR/US markets; use the worker's verified calendar, not legacy `session_active/current_market` gates. Housekeeping runs every5minutes and legacy US fixed05:00KST close misses the winter close window (XNYS06:00KST), so the hook must not depend only on the phantom-housekeeping location. One worker per bot/process; interval15sec; catch and persist/log failures. Do not change legacy live schedules, add a permanently running process, or add a scheduler job. Expose CLI `collect --market KR|US`, `status`, and `run-once --market KR|US` with explicit lack of quote provider reporting instead of fake fills.
- [x] Run focused new tests, related session/phantom tests, and compile changed files. Commit owned files and document the actual provider timing availability, hook trigger, and adapter behavior in the report. No restart yet.

### Task 3: Read-only dashboard and integration tests

**Files:** Create `dashboard/selection_shadow_panel.py` (HTML/JS fragment and API reader helper), `tests/test_dashboard_selection_shadow.py`; modify bounded imports/route and `/virtual` fragment in `dashboard/dashboard_server.py`. Fill the verified report-contract gaps in `runtime/selection_shadow_book.py` and `tests/test_selection_shadow_book.py` as described below, without changing selection/fill/exit rules. A bounded writer/clock metadata extension in `runtime/selection_shadow_adapter.py` and its test may supply verified historical D7 bounds for a fully skipped session; do not infer calendar times inside the financial engine or dashboard GET.

**Consumes:** Task1 `read_report(path, now)`; actual Task2 status fields. No broker, collector or book constructor in request handlers.
**Produces:** `/api/selection_shadow` GET and panel in `/virtual`, refreshed using existing page timer.

- [x] Close the cross-task report gaps required by the approved spec: persist experiment version/parameter and code fingerprints plus actual first-observed times; expose safe collection counts/provenance summaries; expose verified holding-session counts and scheduled/delayed exit metadata. Use real stored timestamps and verified calendar counts, never quote-day counts or invented times. Keep financial rules unchanged, perform any fingerprint file I/O outside write transactions, and keep report queries in one readonly transaction. Add real-SQLite tests for persistence across restart and API visibility. Never create/update this metadata on dashboard GET.

- [x] Write failing API tests with a temporary absent DB and a populated real fixture DB; absence must not create a file. A captured quote/entry/close must show same financial values as `read_report`; unavailable/stale/error states remain distinct from valid zero trades. Test text escaping of ticker/reason content.
- [x] Implement panel showing market/version/start/latest collector+mark+heartbeat; each independent account; pending picks/filled positions/recent closes; source/date/qty/cost/holding-days; missing timestamps and late entry warnings. Use `textContent` or an escape helper for dynamic strings. Empty/unavailable return null performance, not green0% success. Keep pre-existing panels unchanged.

```python
@app.get('/api/selection_shadow')
def api_selection_shadow():
    return jsonify(read_report(BASE_DIR / 'data/shadow/selection_forward.db'))
```

- [x] End-to-end temporary test: collect fixture snapshot -> record -> decide -> verified quote fill -> mark -> close -> GET report. Assert no existing financial DB/state modified and no quote/order call on GET. Test a locked/unavailable DB returns status warning rather than server exception or implicit writable creation.
- [x] Run focused tests, existing dashboard regression tests and compile; commit only owned files, report exact UI/API contract and evidence.

### Task 4: Whole-chain verification and authorized operational activation

**Files:** Create `docs/reports/selection_shadow_forward_activation_20260910.md`; update approved spec status and plan checkboxes only after evidence. No broad settings edits.

**Interfaces:** Consumes all preceding code and current process/task inventory. Produces operational report, dedicated DB initial status, and healthy existing bot/dashboard instances with the new code loaded.

- [x] Run all new tests plus prior selection_account_compare, phantom/session/dashboard related tests, compile and diff checks. Independent whole-change review must precede activation.
- [x] Read `tools/restart_live_stack_safely.ps1` and prior restart report. Identify exact target PIDs and command lines; verify order switches/config hashes before restart. Do not kill unrelated Python processes. Preserve unrelated worktree/index changes and existing running data.
- [x] Use the existing safe restart mechanism only after verifying its scope; launch hidden. User explicitly authorized necessary bot/dashboard restart as part of approved design. Capture process identities after restart and read API/heartbeat.
- [x] If outside entry window, run/observe real collection without fabricating a fill; ensure ENTRY_WINDOW_MISSED/CLOSED is visible. If current quote metadata cannot meet freshness contract, expose NO_VERIFIED_QUOTE and report the limitation. Do not call completion of verified trading evidence merely because a worker exists.
- [x] Save report with test counts, files/commits, activated processes, first real session statuses, unresolved data limitations, no real-order/config changes, and no automatic strategy promotion. Stage only document/task files; no pushing unless user asks.

## Execution choice and review

Subagent-driven execution is recommended; inline execution is the alternative. The user has repeatedly authorized execution; proceed with task-scoped subagents and independent reviews without another approval pause. Use sequential implementers to avoid shared-file conflicts.
Task reviews gate the next task. Record progress/decisions in this plan's SDD ledger; keep source/quote limitations explicit. End with operational evidence, not only passing unit tests.

## Completion evidence — 2026-09-10

Tasks 1–3 and final independent re-review are complete at runtime revision `b0b99c6`; the controller's final relevant regression gate is 378 passed in 51.25s, with eight runtime/integration modules compiled. Task4 activated bot25408 and dashboard30604 through the existing mutex/checkpoint/guardian safeguards and verified fresh bot/worker heartbeat, API/page HTTP200, unchanged configuration and broker position/open-order continuity. The dedicated ledger and original first-observation identity were preserved. These checkmarks describe implemented/verified connection, not profitable forward performance or a globally green repository: zero operational fills, KR INPUT_INCOMPLETE and US CACHE_ONLY remain; broader tests have three documented failures. See [activation report](../../reports/selection_shadow_forward_activation_20260910.md) for evidence, decisions and limitations.
