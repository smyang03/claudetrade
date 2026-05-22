# Active Work

Updated: 2026-05-22

This file only separates what should be developed now from deferred backlog. The concrete immediate requirements are in [NOW_CODE_REQUIREMENTS_20260522.md](NOW_CODE_REQUIREMENTS_20260522.md), and the code-level before/after risk review is in [NOW_RECHECK_RISK_ANALYSIS_20260522.md](../plans/NOW_RECHECK_RISK_ANALYSIS_20260522.md).

## Develop Now

| Priority | Work | Why Now | Rule |
| --- | --- | --- | --- |
| NOW-0 | Commit hygiene guardrail | Current working tree contains `state/brain.json`, generated state JSON, DB sidecars, temp browser profiles, screenshots, and runtime data. | Stage only intentional source/docs/config/test paths. Do not include policy memory or runtime artifacts without explicit approval. |
| NOW-1 | Sub-screener effective trigger visibility | Global `SUB_SCREENER_TRIGGER_ENABLED=false` is misleading because scoped KR/US trigger flags are `true` and override it in code. | Add read-only visibility first. Do not change trigger config until operator confirms intended KR/US policy. |
| NOW-2 | Broker-truth zero-holding fixture tests | Plan A and PathB can remove/close local positions after fresh broker zero-holding truth. This is safety-critical and depends on real KIS row shapes. | Add KR/US broker payload fixture tests before changing reconcile logic. |

## Not Now

These are important, but not the immediate development scope.

| Item | Why Deferred |
| --- | --- |
| PathB entry broker-truth gate ops visibility | Gate code exists. Visibility is useful but less urgent than sub-screener effective trigger and destructive reconcile fixture coverage. |
| Profit review timeout fallback visibility | Timeout fallback is already marked `advisor_unavailable` and `learning_excluded`; dashboard/ops display can follow after NOW items. |
| US KIS ranking screener | Candidate source change can affect buy quality. Implement only after shadow comparison metrics are defined and reviewed. |
| V2 canonical truth runbook/freshness | Important for analysis and learning truth, but not the first safety blocker. |
| Counterfactual outcome schedule | Important for policy review quality, but not urgent live safety work. |
| Prompt pool/evidence alignment validation | Requires next-session data review rather than immediate code change. |
| Market index expansion, CandidateTierBook, KRX/BigKinds, new strategy gates | Shadow/longer-term experiments only. |

## Completed

| Item | Code Judgment | Remaining Rule |
| --- | --- | --- |
| KR confirmation `minute_complete` data-quality fix | Completed by `6f8fdc1`. KR confirmation accepts `minute_complete`. | Treat as a bug fix, not policy relaxation. |
| KR `fade_recovered_shadow` | Completed by `6f8fdc1`. KR-only shadow evidence is emitted. | Keep observation only. No live `PROBE_READY`, US fade relaxation, or PathB wait exception without separate approval. |

## Policy Questions

Before changing sub-screener config, the operator must confirm one of:

1. KR/US both shadow: set market-scoped trigger flags false or remove scoped overrides.
2. KR/US both live trigger: keep scoped true and document override as approved.
3. Split policy: keep only the approved market scoped true.

Until then, implementation is limited to read-only visibility and tests.
