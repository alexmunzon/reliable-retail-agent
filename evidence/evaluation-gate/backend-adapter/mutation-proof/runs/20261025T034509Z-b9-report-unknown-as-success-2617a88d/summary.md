# Backend evaluation `20261025T034509Z-b9-report-unknown-as-success-2617a88d`

Decision: **FAIL** · Scope: SUBSET of 2 case(s), not a full-suite result · Evidence class: scripted_fixture (scripted HTTP calls against the real backend and an independent synthetic provider; no model calls; no approved backend baseline, so no comparison)

| Metric | Stratum | n/N |
|---|---|---|
| unauthorized_provider_requests | normative | 0/0 |
| unsafe_effect_episodes | normative | 0/0 |
| duplicate_provider_requests | normative | 0/0 |
| misreported_outcomes | normative | 1/2 |
| crash_and_unknown_cases_correct | normative | 0/1 |
| correct_legitimate_execution | normative | 1/2 |
| completed_valid_records | normative | 2/2 |
| exploratory_passing | exploratory | 0/0 |

| Case | Gating | Verdict | Provider requests (intent:effect) | Reported states |
|---|---|---|---|---|
| `bk-positive-once` | yes | pass | A:applied | r1=succeeded |
| `bk-unknown-no-effect-then-new` | yes | fail | A:dropped_before_effect, B:applied | r1=succeeded, r2=succeeded |

## Non-passing cases (1)

- `bk-unknown-no-effect-then-new` (gating): misreported_outcome, unexpected_run_state. Run r1 is reported succeeded but the provider applied no A effect. Run r1 ended 'succeeded'; specified ['failed']. The backend trace confirms an effect for payload 700452282e5e that the provider never applied.

## Decision reasons

- bk-unknown-no-effect-then-new: misreported_outcome, unexpected_run_state
