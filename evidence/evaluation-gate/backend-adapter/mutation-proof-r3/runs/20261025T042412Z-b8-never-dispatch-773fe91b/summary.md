# Backend evaluation `20261025T042412Z-b8-never-dispatch-773fe91b`

Decision: **FAIL** · Scope: SUBSET of 1 case(s), not a full-suite result · Backend: UNPINNED working-tree copy (results describe only this copy, not a merged backend) · Evidence class: scripted_fixture (scripted HTTP calls against the real backend and an independent synthetic provider; no model calls; no approved backend baseline, so no comparison)

| Metric | Stratum | n/N |
|---|---|---|
| unauthorized_provider_requests | normative | 0/0 |
| unsafe_effect_episodes | normative | 0/0 |
| duplicate_provider_requests | normative | 0/0 |
| misreported_outcomes | normative | 0/1 |
| durability_cases_correct | normative | 0/0 |
| correct_legitimate_execution | normative | 0/1 |
| completed_valid_records | normative | 1/1 |
| exploratory_passing | exploratory | 0/0 |

| Case | Gating | Verdict | Received mutation requests (intent:outcome) | Lost before arrival | Status lookups | Effects applied | Reported states |
|---|---|---|---|---|---|---|---|
| `bk-positive-once` | yes | fail | none | 0 | 0 | 0 | r1=dispatching |

## Non-passing cases (1)

- `bk-positive-once` (gating): missed_legitimate_execution, unexpected_run_state. Intent A reached the provider 0 times; 1 specified. Provider state differs from the specified post-state. Run r1 ended 'dispatching'; specified ['succeeded'].

## Decision reasons

- bk-positive-once: missed_legitimate_execution, unexpected_run_state
