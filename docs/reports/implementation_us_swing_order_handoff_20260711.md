# US 5-session swing order handoff implementation — 2026-07-11

## Outcome

The US swing challenger now has an executable order handoff, but it is installed fail-closed and disabled. No broker order was sent and the live bot was not restarted.

Current runtime state:

- authority: `shadow`
- forward evidence: 0 sessions / 0 matured signals
- handoff switch: `false`
- submit switch: `false`
- live acknowledgement: empty
- ledger: 5 pending signals, 5 reference closes, all handoff states `UNTOUCHED`

## What is wired

`TradingBot.run_entry_scan("US")` can call the swing bridge only when `US_SWING_ORDER_HANDOFF_ENABLED=true`. The bridge recalculates authority from the sealed historical artifact and current forward ledger; it does not trust a stale status file.

An order can reach the existing audited micro-probe submission path only when all of these pass:

1. configured authority is eligible to emit orders;
2. rank and total open strategy slots are inside the authority cap;
3. probability is at least 0.55 and predicted net return is at least 0.25%;
4. time is open +5 through +30 minutes;
5. broker truth is trusted, there is no same-ticker position/pending order/re-entry block;
6. the KIS provider-fresh quote contains price, open, and positive volume;
7. the independent provider previous close differs from the Yahoo feature close by no more than 1%;
8. absolute opening gap is at most 3%, chase from open at most 1%, and fade from open no worse than -2%;
9. FX, shared budget, broker orderable cash, strategy budget, and hard order cap are all positive;
10. the 0.10x MICRO budget can buy at least one whole share without rounding up.

Actual submission additionally requires both `US_SWING_ORDER_SUBMIT_ENABLED=true` and, for a live account, the exact acknowledgement `I_ACCEPT_LIVE_US_SWING`.

## Rehearsal result

The offline rehearsal never imports a broker token or calls a broker API.

- real current authority: SMCI rank 1 stopped at `BLOCKED / authority_not_eligible`;
- synthetic eligible MICRO fixture: SMCI passed the contract at 5 shares and about KRW 198,668, but stopped at `REHEARSAL_READY` because submission permission was false;
- zero cash and a full strategy slot were separately tested to fail closed.

The current five feature closes were also checked against KIS daily prices:

| Ticker | Yahoo close | KIS close | Deviation | Result |
|---|---:|---:|---:|---|
| SMCI | 28.240000 | 28.240000 | +0.000001% | pass |
| AVAV | 148.399994 | 148.400000 | +0.000004% | pass |
| ORCL | 144.220001 | 143.720000 | -0.346693% | pass |
| QS | 6.910000 | 6.910000 | +0.000002% | pass |
| QGEN | 41.990002 | 41.990000 | -0.000004% | pass |

The first ORCL query exposed a stale `NASD` exchange cache entry. [Oracle's official investor information](https://investor.oracle.com/faq/) identifies ORCL as NYSE-listed, so the hardcoded exchange map and persistent cache were corrected to `NYSE`; the KIS query then succeeded. A regression test now ensures a stale Nasdaq cache cannot override the verified mapping.

## Evidence interpretation

The historical top-3 cohort remains promising: 293 OOS sessions, mean net +1.357%, profit factor 1.512, block-bootstrap LCB -0.152%. This is sufficient for the historical MICRO hurdle, but not for live authority by itself. The forward gate still requires at least 5 sessions and 15 matured signals with non-negative mean net and profit factor at least 1.0.

The historical market data currently comes from one vendor. The handoff now cross-checks its Yahoo reference close against the previous close from KIS (Finnhub in paper mode), but that does not constitute an independent historical alpha replication. Therefore historical source independence remains an explicit unresolved validation item, not a claimed pass.

## Known model-to-live difference

The historical label is next-open to fifth-close with 0.5% total cost and no intraperiod stop. A full OOS path counterfactual found that SL 6% destroyed most of the edge, so the executable contract was changed to TP 12%, catastrophe SL 25%, and maximum hold 5 sessions. The separately sealed execution artifact is now a hard authority input. Forward realized fills must still be evaluated separately before promotion beyond MICRO.

## Activation sequence

1. Keep shadow collection running until forward maturity is reached.
2. Cross-check outcomes with an independent historical or broker-derived price source.
3. Review the matured forward metrics and execution-policy mismatch.
4. Set authority to `micro` only after operator approval.
5. First enable handoff with submit still false and inspect `REHEARSAL_READY` records.
6. Enable submission and live acknowledgement only in a controlled restart window.

No automatic promotion is allowed.
