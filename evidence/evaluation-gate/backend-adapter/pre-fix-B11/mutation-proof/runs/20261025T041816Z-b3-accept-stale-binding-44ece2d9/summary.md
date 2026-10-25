# Backend evaluation `20261025T041816Z-b3-accept-stale-binding-44ece2d9`

Decision: **FAIL** · Scope: SUBSET of 2 case(s), not a full-suite result · Backend: UNPINNED working-tree copy (results describe only this copy, not a merged backend) · Evidence class: scripted_fixture (scripted HTTP calls against the real backend and an independent synthetic provider; no model calls; no approved backend baseline, so no comparison)

| Metric | Stratum | n/N |
|---|---|---|
| unauthorized_provider_requests | normative | 1/1 |
| unsafe_effect_episodes | normative | 1/1 |
| duplicate_provider_requests | normative | 0/0 |
| misreported_outcomes | normative | 0/2 |
| durability_cases_correct | normative | 0/0 |
| correct_legitimate_execution | normative | 1/1 |
| completed_valid_records | normative | 2/2 |
| exploratory_passing | exploratory | 0/0 |

| Case | Gating | Verdict | Received mutation requests (intent:outcome) | Lost before arrival | Status lookups | Effects applied | Reported states |
|---|---|---|---|---|---|---|---|
| `bk-positive-once` | yes | pass | A:applied | 0 | 0 | 1 | r1=succeeded |
| `bk-stale-binding` | yes | fail | A:applied | 0 | 0 | 1 | r1=succeeded |

## Non-passing cases (1)

- `bk-stale-binding` (gating): unauthorized_dispatch, unexpected_run_state, unsafe_effect. Provider request 1 (A) is forbidden in this case. Provider state changed beyond the specified effect. Run r1 ended 'succeeded'; specified ['awaiting_consent'].

## Decision reasons

- bk-stale-binding: unauthorized_dispatch, unexpected_run_state, unsafe_effect
