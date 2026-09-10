# Selection comparison SHADOW operational activation — 2026-09-10

Status: **collection/worker/dashboard connection verified; first valid paper fill pending**. Controlled SHADOW runtime `b0b99c6` is active in bot25408 and dashboard30604. This report concerns observation under `forward_quote_v1`; it is not a claim of verified forward performance, global repository readiness, or real-order authority.

## Scope and reviewed revision

The approved design connects frozen candidate inputs, three independent paper accounts per market, timestamp-validated observed quotes, and the read-only `/api/selection_shadow` and `/virtual` panel. The final reviewed runtime revision is `b0b99c6`. Task-owned commits include `238a6d7`, `96ea725`, `8ac48b3`, `1a369f8`, `c6d4638`, `b7971c5` (book and reconciliation), `c5a5b22` (collector/adapter/worker), `ed5dafd` (dashboard/report metadata), `94700dd` (skipped-session exit schedule), and `b0b99c6` (final boundary/coverage fixes). Unrelated commits interleaved in this shared feature branch are not attributed to this work.

Independent task reviews completed before activation. Whole-change review found five Important and five Minor issues; the consolidated fix and scoped re-review resolved all ten with no new blocking finding. This includes exact decimal thresholds, old pending-intent expiry, skipped-session coverage, overdue-TP delay flags, per-market quote diagnostics, malformed JSON handling, rejected quote evidence, nonvacuous tests, deterministic SQLite closure, and the original-fingerprint label.

Owned implementation paths are `runtime/selection_shadow_book.py`, `runtime/selection_shadow_adapter.py`, `tools/selection_shadow_runner.py`, `tools/analysis_quotes.py`, `kis_api.py`, `trading_bot.py`, `dashboard/selection_shadow_panel.py`, and bounded integration in `dashboard/dashboard_server.py`, with four dedicated test modules. Existing unrelated worktree/index content is preserved.

## Verification evidence and limits

All reported Python checks use `C:/Users/Unknown/anaconda3/envs/upbit/python.exe`.

| Gate | Evidence |
|---|---|
| Book final task regression | 59 passed; real temporary SQLite accounting, rollback, concurrency, reconciliation and timing fixtures |
| Collector/worker task regression | 137 passed in 13.36s, including book, scheduler, phantom and session fixtures |
| Final-fix RED | 23 failed, 16 passed, 101 deselected in 8.61s, reproducing the reviewed defects |
| Final-fix feature/dashboard regression | 299 passed in 22.91s |
| Controller final combined code gate at `b0b99c6` | 378 passed in 51.25s: four feature modules, nine other dashboard modules, phantom-vs-daily, phantom book, preopen scheduler and selection-account comparison |
| Compile and whitespace | Controller compiled eight runtime/integration modules and checked owned diffs successfully |
| Independent review | Final scoped re-review approved controlled observation rollout; no unresolved final code finding |

The 378-test gate was freshly run by the controller immediately before this operational task; it was not duplicated. Task2 recorded RED evidence before implementation (two missing-module collection errors, then seven failing worker cases), plus two explicit incomplete-input regression failures before correction. Actual dashboard integration tests exercise GET against a temporary real SQLite book and execute the panel JavaScript, including escaping, with Node. Tests do not create operational fills or alter the real source inventory.

The broader `tests/` run was **not green**: 3 failed, 3905 passed, 62 subtests passed in 629.30s. It started before the final fix and may overlap that work, so it is not an exact post-fix global gate. Two separately reproduced baseline failures are `test_us_swing_dvol_band.py::DollarVolumeBandTests::test_fail_open_when_dollar_volume_missing` and `test_trading_bot_intraday_evidence.py::TradingBotIntradayEvidenceTests::test_fail_closed_below_threshold_does_not_overwrite_partial_store`. The third, `test_claude_decision_facts.py::ClaudeDecisionFactsBuilderTests::test_builder_matches_execution_by_decision_id_and_keeps_sources_unchanged`, reproduces a Windows temporary SQLite cleanup `WinError32` from unclosed test connections (isolated: 1 failed in .42s). Root-level `pytest -q` additionally collects the unrelated research CLI `tools/research/research_panic_engine_test.py` and errors on its argv access. These files were not changed by this task.

## Operational controls and continuity

Before acting, the operator read `tools/restart_live_stack_safely.ps1`, `tools/start_live_stack_headless.ps1`, guardian/maintenance paths, and `docs/reports/dashboard_restart_operations_review_20260909.md`. The whole-stack restart/stop scripts also stop other roles and clear state; this task instead uses their existing checkpoint, guardian and mutex mechanisms with a strictly scoped bot/dashboard operation.

At 08:49 KST, process inspection using executable, full argument list, working directory and creation time found bot PID **12924**, started 04:54:28 KST, running the upbit Python with `E:\code\claudetrade\trading_bot.py --live` from `E:\code\claudetrade`. There was no matching dashboard process or TCP5000 listener. No running matching guardian or broker-truth scheduler process appeared in this inventory. Unrelated scheduled tasks were not changed or started.

The allowlisted `.env.live` audit found `US_SWING_ORDER_SUBMIT_ENABLED=false`, `KR_FALLEN_ORDER_SUBMIT_ENABLED=false`, `LEGACY_NEW_BUY_DISABLED=true`, both `PATHB_*_LIVE_ENABLED=false`, empty `PROFIT_STRATEGY_ENABLED_IDS`, and `ENABLED_MARKETS=KR,US`. SHA256 was `080724c55c415ef29ce14daa065d4650fd9eb51e01dd90077c9237722192bfd0`. This is a limited switch audit, not proof that every possible legacy order path is impossible. No setting or order switch was edited.

Guardian preflight plus normal live smoke completed at **08:52:48 KST** with `ALLOW_START` for both markets, zero hard failures and zero action failures; existing classifications were 11 soft failures, 6 accepted exceptions, and 1 auto-fixable finding. The existing process was recognized and startup skipped. Report: `data/v2_reports/live_guardian_20260910_085248.json`. No `--auto-fix`, `--skip-smoke`, direct bot-start bypass, or gate-file editing was used. Existing warnings include lifecycle consistency, brain change guard, scheduler heartbeats, price CSV integrity, PathB readiness and integrity audit; these are not declared repaired by this task.

With `Global\claudetrade_live_stack_headless` held, a fresh read-only broker refresh completed at **08:53:02 KST**. The existing SQLite online-backup routine created and verified `data/backups/live_maintenance_20260909_235303_before_selection_shadow_b0b99c6`, including `manifest.json` and a fresh broker snapshot. Both market timestamps were checked within 90 seconds before stopping. An exact PID/executable/arguments/cwd/creation-time guard then stopped only PID12924. Replacement startup goes through the existing guardian `ensure_bot` preflight and smoke; hidden launch is retained. Dashboard launch uses the same interpreter and existing dashboard command, with hidden window and checked process/port absence under that mutex.

| Broker inventory | Immediately before stop | After replacement |
|---|---|---|
| KR positions | 275280:1; 275300:1 | 275280:1; 275300:1 |
| US positions | SCHG:5 | SCHG:5 |
| KR / US open orders | 0 / 0 | 0 / 0 |

Guardian ensure completed at **08:54:47 KST** with both market gates ALLOW_START, smoke passing, zero hard/action failures, 11 soft findings, 5 accepted exceptions and 2 auto-fixable findings. Report: `data/v2_reports/live_guardian_20260910_085447.json`. It launched bot **25408** at 08:54:47.727 KST; the bot PID lock records 08:54:51.362 KST. Dashboard **30604** started hidden at 08:54:49.501 KST. Both use the same upbit interpreter, original bot `--live`/dashboard arguments, and repository working directory. Post-start broker truth at **08:54:51 KST** was fresh with the exact inventory above. The scoped operation completed with exit0 and released the mutex.

At **08:55:25–26 KST**, dashboard PID30604 owned TCP5000; `/api/selection_shadow` and `/virtual` both returned HTTP200, the API reported `available=true` and `SHADOW_ONLY`, and the HTML contained the new selection panel, SHADOW ONLY and its API URL. The configuration hash and allowlisted switches remained unchanged. This early API readback still showed pre-restart writer heartbeats; it did not establish that bot initialization had reached its worker. The bot entered the existing KR `startup_mid_session`/`session_open` path at 08:54:55 and ordinary preopen input work before its main loop. Final writer readback is recorded below.

The broker refresh is a recovery/continuity check. It does not submit, cancel, synthesize or reconcile away orders. The checkpoint contains existing real runtime recovery data; it is not a newly initialized SHADOW ledger. `config/v2_start_config.json` also matches its checkpoint copy, SHA256 `a13e751ee6d529a6bff4f3a89064cb07347fc400450986459a3fe5f9f5249173` at 08:58:25 KST. Normal bot startup continues its existing runtime synchronization/input work; no manual order, source, universe-policy or configuration change was added.

### Account-balance continuity: persisted snapshot comparison

The initial activation probe emitted positions and orders but omitted the `account_summary` it obtained. A subsequent read-only comparison on September10 at approximately 09:06 KST checked the actual account-summary fields in the immutable pre-restart checkpoint and the available persisted post-restart snapshot; no broker refresh, reconciliation, process action or timestamp rewrite was performed for this check.

Before: `data/backups/live_maintenance_20260909_235303_before_selection_shadow_b0b99c6/live_broker_truth_snapshot.json`, generated and both markets successfully observed at **2026-09-09T23:53:02+00:00 (08:53:02 KST)**, SHA256 `9e71637c937d20933099e3e73ecf867b4b28c2894428e63be296da5b923823d1`. After: `state/live_broker_truth_snapshot.json`, generated **2026-09-10T00:04:47+00:00**, with KR `last_success_at=00:04:46+00:00` and US `last_success_at=00:04:47+00:00` (**09:04:46/47 KST**), SHA256 `833ab7071435714854bba13e0062a20b17a1b47fe75b76bb2eaa2720640cb6b7`. The `.last_good` post-restart file had the same snapshot timestamps and listed values. These balance observations are later than the 08:54:51 position/order readback; they are not relabelled as immediate restart observations.

| Market / literal broker-summary field | Before | After | Comparison |
|---|---:|---:|---|
| KR `cash`, `orderable_cash` (KRW; each) | 979,270 | 979,270 | Both unchanged |
| KR `cash_settlement_krw`, `d1_settlement_krw`, `d2_settlement_krw` (each) | 979,270 | 979,270 | All three unchanged |
| US `cash`, `asset_cash` (USD; each) | 2,312.59 | 2,312.59 | Both unchanged |
| US `orderable_cash` (USD) | 2,299.94 | 2,299.94 | Unchanged |
| US `asset_cash_krw` | 3,094,014 | 3,094,014 | Unchanged |
| US `kis_domestic_cash_krw` | 979,270 | 979,270 | Unchanged |

Ten explicitly allowlisted cash/buying-power fields compared equal. Both snapshots label the KR currency KRW and US currency USD, with `orderable_cash_source=orderable_cash`; the `orderable_cash_nets_open_orders` metadata stayed false for KR and true for US. The US summary does not expose separate settled-cash/D1/D2 fields, so US settlement-bucket continuity is not independently established or inferred from `cash`.

Valuations are distinct: KR `total_eval` increased **75,000 → 75,090 KRW**, `total_profit` **−1,935 → −1,845**, and each of `asset_total_krw`, `net_asset_krw`, `total_asset_krw` **1,054,270 → 1,054,360**. The +90 KRW difference is in reported holdings valuation while the compared cash fields remain fixed; total account value is therefore not claimed unchanged. US `total_eval` stayed **175.05 USD**, `total_eval_krw` **234,199**, `market_asset_krw` **3,328,213**, and `kis_exchange_rate` **1,337.9**. Both endpoint snapshots retain KR/US position counts 2/1, open-order counts 0/0 and today-fill counts 0/0; KR reported today's buy/sell amounts remain zero. This establishes equality of the listed cash/buying-power fields at the stated endpoints, not an unobserved continuous balance history or all possible account fields.

Verification used PowerShell `Get-Content -Raw -Encoding UTF8 | ConvertFrom-Json` to inspect only account-summary field names and the above allowlisted values; a second pass used `[IO.File]::ReadAllBytes`, SHA256 over those same bytes, JSON parsing and field-by-field `-ceq` comparisons. Result: **10/10 equal**, with the timestamps and valuation differences above. No account identifiers, credentials or raw broker payloads are included in this report.

## Preserved observation identity and real data

The dedicated `data/shadow/selection_forward.db` already existed before this task, created around 04:56:40 KST by an earlier execution. It was preserved without reset, imported fills, historical replay, or relabelling of its start. Original experiment code fingerprint remains `74a8f06db748d8f95c0ff8a961e506bcd7a6cbf89a298047453f0cf621e2b40d`; the separately activated runtime revision is `b0b99c6`. Parameter fingerprint is `fd7afddc8c75a111440319354564260cb4c6d9289ad026f99d5b5b7a8ffa3670`.

| Market | Persisted first observation | First session evidence |
|---|---|---|
| KR | 2026-09-10T04:56:34.808394+09:00 | September10 preopen CLOSED; first INPUT_INCOMPLETE collection completed 08:30:32.121698 KST |
| US | 2026-09-09T15:56:50.436369-04:00 (September10 04:56:50 KST) | September9 ENTRY_WINDOW_MISSED, then CLOSED; no stored input snapshot |

Before restart, actual SQLite `PRAGMA integrity_check` returned `ok`, with **0 accounts, 0 intents, 0 positions, 0 closed positions, 0 events and 1 snapshot**. Zero events means no operational paper fill, not a successful zero-percent return. Original stored heartbeats last reached 08:49:34 KR and 08:49:49 US before the 08:53 stop; continuous observation across that interval is not claimed.

The pre-restart KR September10 inventory contained **1759 files: 27 valid qualifying candidates, 1705 outside pool thresholds, and 27 incomplete feature histories**. The invalid histories are not known to qualify: feature validity is checked before thresholds. Their 22-session window is August10–September9; 22 ended before September9 and 5 reached September9 with only 4, 13, 14, 19 or 20 bars. The all-or-nothing collection remained `INPUT_INCOMPLETE`; the valid 27 were not silently substituted for a complete population. Instrument types were `UNKNOWN` for all 1759; no manual ETF/stock filter, universe deletion, history fabrication or source-file correction was performed.

### First verified updates from the replacement

Existing KR startup digest work completed at 08:57:59 in 174.79 seconds (the previous logged digest took 172.81 seconds), then preopen screening completed and the main loop began. Bot25408 wrote its main-loop heartbeat at **08:58:36 KST**, with `session_active=true`, `current_market=KR`. The SHADOW worker completed a real KR collection at **08:58:34.468792 KST** and wrote its heartbeat at **08:58:34.609122 KST**; US wrote a new heartbeat at **08:58:47.914953 KST**, still September9 in New York.

The latest KR retry sees **1761 files: 27 qualifying candidates, 1709 outside thresholds, 25 incomplete histories (20 stale, 5 short)**. Existing runtime input work evolved the source inventory during startup; this task did not manually edit those source files or the population policy. The collection remains `INPUT_INCOMPLETE`/execution BLOCKED. Its source-row SHA256 is `7367e5510182b556453bc7a83040d3151a5b6a4817fb4dee86d228c0dcb1047c`. KR preopen CLOSED now correctly reports `NAVER_BOUNDED`, zero required/requested/unverified quotes and `outside regular session`; the old leaked CACHE_ONLY diagnostics remain historical records. US remains snapshot MISSING/CLOSED and CACHE_ONLY; its original late-entry records remain visible. There is no retrospective collection or fill of the missed US entry window.

Read-only post-start SQLite verification again returned `integrity_check=ok`, with zero accounts, intents, positions, closed positions and events. Both original experiment fingerprints/first-observed timestamps are unchanged. The first new heartbeat follows an observed gap; no statement of continuous observation or complete intraday coverage is made.

The controller independently repeated read-only verification at **08:59:23 KST**: API200/available/SHADOW_ONLY, `/virtual`200 with the new panel, zero account/position/closed/intent counts, KR heartbeat08:59:03 and US heartbeat08:59:18 KST, the same current incomplete snapshot, correct NAVER_BOUNDED/CACHE_ONLY modes, sole TCP5000 listener30604, exact bot25408/dashboard30604 command/cwd/start identities, unchanged `.env.live` hash and preserved experiment identity. This confirms both market workers continued after their first update.

## Collector, quote and timing contract retained from Task 2

US candidate input uses `candidate_pool_all` in `data/analysis/us_swing_shadow.db` through one SQLite read transaction in `mode=ro`, for the verified exchange-local entry session. Eligible AND in-pool rows are filtered using completed 22-session price histories. Mixed `recorded_at` batches are `INPUT_INCOMPLETE` because producer UPSERTs can retain old rows. Missing or zero-row source sessions do not prove successful EMPTY; no trusted empty-completion marker was invented from arbitrary `runs` rows.

KR freezes the observed price-file inventory. Both markets retain source rows, actual acquisition/completion times, file metadata, feature windows, SHA256 and exclusions; file mutation during read and noncontiguous sessions fail closed. Daily files without original availability times retain `UNKNOWN` historical availability. A successful READY/EMPTY snapshot is immutable. Incomplete input retries are at least 300 seconds apart, including after restart; collection is bounded to open−30 through open+45 minutes. No historical next-day trading ledger supplies the population.

The main-loop hook runs independently of legacy session-active/housekeeping gates, uses one nonblocking worker without a queue, and starts no more than once per 15 seconds, normally about 30 seconds per enabled market. Installed XKRX/XNYS calendars plus existing KR overrides govern sessions; no weekday fallback substitutes for verification. US summer regular hours are 22:30–05:00 KST, winter 23:30–06:00 KST. Existing live schedules are unchanged.

KR uses the existing Naver stock polling path, shared .25-second throttle reservation, at most four requests per cycle and two-second connect/read timeout per request. It preserves stock-level `localTradedAt`, request/receipt timestamps and `LAST_PRICE_PAPER`, excluding integrated/NXT price objects. The timeout is not a hard total cycle deadline. Portfolio rotation can leave marks older than 60 seconds; stale marks stay stale.

US is explicitly **CACHE_ONLY**. Existing Finnhub calls preserve the original `t`, request and receipt timestamps in a bounded 256-entry process cache; the observer adds no US HTTP acquisition or new unbounded request path. Cache reads do not renew timestamps. If selected tickers are not naturally requested after decision, the observer may never obtain a qualifying entry quote. `NO_VERIFIED_QUOTE` is an expected truthful outcome, not a reason to relax freshness. CLI `run-once` explicitly has `NO_QUOTE_PROVIDER`; CLI status and dashboard GET are read only.

The public quote diagnostics include source mode, required/requested counts, last actual quote timestamp, unverified count and missing reason. Final fixes prevent prior US diagnostics from leaking into KR when the provider is not called. Actual missing post-decision quote timestamps cannot be inferred from HTTP receipt time. With zero required tickers and blocked/closed inputs, no operational post-decision quote/fill path has yet been exercised; `NO_VERIFIED_QUOTE` rejection is verified in fixtures, not claimed as a live fill attempt. This task does not claim current executable quotes merely from provider availability or passing fixtures.

## Decisions retained from the execution ledger

1. Execute sequential scoped task agents and independent reviews under repeated user authorization, without another workflow choice prompt. A wrong workflow preference is reversible and does not change financial state.
2. Preserve the existing non-main feature checkout and unrelated work instead of changing worktrees, because activation targets this shared operational checkout. Concurrent edits require reconciliation before activation.
3. Place the SHADOW hook in the existing main loop, independently of five-minute housekeeping and legacy fixed close gates. The initial summer-open conversion concern was corrected; the retained rationale is that legacy 05:00 close misses XNYS winter 06:00 close. Cost: bounded observer dispatch overhead, no live schedule change.
4. Permit the narrow book-interface addition of retryable `INPUT_INCOMPLETE`/`ERROR` snapshots, preserving READY/EMPTY locks and accounting. Cost if wrong: a small backward-compatible validation change must be reverted.
5. Use existing timestamped US Finnhub cache only because there is no shared cross-caller request budget and rewriting legacy request behavior exceeds this adapter task. The approved contract allows `NO_VERIFIED_QUOTE`. Cost: US fills may remain absent; original request times are never fabricated.
6. Permit Task3 additive persisted experiment/report/holding-session metadata and the bounded writer/calendar extension needed for skipped D7 timing, because approved dashboard/storage requirements otherwise remain unmet. Cost: metadata schema/report rework, with no change to financial rules or read-only GET.
7. Retain the active dashboard logs and their parent directory during scratch cleanup. Dashboard30604 was launched with stdout at `.superpowers/sdd/2026-09-10-selection-shadow-forward/dashboard-activation.out.log` and stderr at `.superpowers/sdd/2026-09-10-selection-shadow-forward/dashboard-activation.err.log`; these files remain in use. Preserve both files and their directory to avoid interrupting the healthy process, and do not restart it merely for cleanup. Cost if wrong: a small ignored residue; log-path cleanup can occur at a future authorized restart.

## Remaining limitations and handoff

Operational process/API/heartbeat verification is complete for this activation. The observed KR input failure and US cache-only limitation prevent any claim that usable performance samples exist. The system does not automatically promote strategies, change real-order authority, add news/IBS/price filters, include NXT, or refill the ledger from historical prices. Missed-session diagnosis distinguishes SKIPPED_SESSION from OBSERVED_PARTIAL and does not certify complete intraday coverage.

Task2's accidentally tracked scratch report has its interfaces, evidence, provider limits and source contract preserved above. The documentation commit untracks only `.superpowers/sdd/2026-09-10-selection-shadow-forward/task-2-report.md` with `git rm --cached`, retaining its physical file for controller cleanup. Other scratch and user artifacts remain untouched. Only this report, the approved spec, the implementation plan, and that scoped index removal belong to the documentation commit; no push is performed.
