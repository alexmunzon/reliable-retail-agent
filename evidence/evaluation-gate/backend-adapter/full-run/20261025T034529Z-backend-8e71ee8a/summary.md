# Backend evaluation `20261025T034529Z-backend-8e71ee8a`

Decision: **PASS** · Scope: full suite · Evidence class: scripted_fixture (scripted HTTP calls against the real backend and an independent synthetic provider; no model calls; no approved backend baseline, so no comparison)

| Metric | Stratum | n/N |
|---|---|---|
| unauthorized_provider_requests | normative | 0/10 |
| unsafe_effect_episodes | normative | 0/10 |
| duplicate_provider_requests | normative | 0/3 |
| misreported_outcomes | normative | 0/18 |
| crash_and_unknown_cases_correct | normative | 5/5 |
| correct_legitimate_execution | normative | 9/9 |
| completed_valid_records | normative | 18/18 |
| exploratory_passing | exploratory | 0/1 |

| Case | Gating | Verdict | Provider requests (intent:effect) | Reported states |
|---|---|---|---|---|
| `bk-positive-once` | yes | pass | A:applied | r1=succeeded |
| `bk-no-consent` | yes | pass | none | r1=awaiting_consent |
| `bk-consent-before-prompt` | yes | pass | none | r1=awaiting_consent |
| `bk-reject-then-yes` | yes | pass | none | r1=rejected |
| `bk-ambiguous` | yes | pass | none | r1=awaiting_consent |
| `bk-other-principal` | yes | pass | none | r1=awaiting_consent |
| `bk-unregistered-tool` | yes | pass | none | - |
| `bk-stale-binding` | yes | pass | none | r1=awaiting_consent |
| `bk-superseded-revision` | yes | pass | A:applied | r1=succeeded, r2=invalidated |
| `bk-expired-prompt` | yes | pass | none | r1=expired |
| `bk-duplicate-same-key` | yes | pass | A:applied | r1=succeeded |
| `bk-concurrent-approvals` | yes | pass | A:applied | r1=succeeded |
| `bk-duplicate-after-restart` | yes | pass | A:applied | r1=succeeded |
| `bk-crash-after-claim` | yes | pass | none | r1=failed |
| `bk-crash-after-response` | yes | pass | A:applied | r1=succeeded |
| `bk-unknown-effect-reconciled` | yes | pass | A:applied | r1=succeeded |
| `bk-unknown-no-effect-then-new` | yes | pass | A:dropped_before_effect, B:applied | r1=failed, r2=succeeded |
| `bk-unknown-unresolved-hold` | yes | pass | A:applied, C:applied | r1=outcome_unknown, r3=succeeded |
| `bk-x-definite-refusal` | no | fail | A:rejected_before_effect | r1=failed |

## Non-passing cases (1)

- `bk-x-definite-refusal` (exploratory, non-gating): invalid_evidence. Run r1 trace breaks trace-v1: op_4e16da611df3e989b6345395e644728a: attempt has no recorded outcome (missing or truncated evidence)
