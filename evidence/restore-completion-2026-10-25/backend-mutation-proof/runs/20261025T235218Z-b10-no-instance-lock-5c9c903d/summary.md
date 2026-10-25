# Backend evaluation `20261025T235218Z-b10-no-instance-lock-5c9c903d`

Decision: **FAIL** · Scope: SUBSET of 2 case(s), not a full-suite result · Backend: UNPINNED working-tree copy (results describe only this copy, not a merged backend) · Evidence class: scripted_fixture (scripted HTTP calls against the real backend and an independent synthetic provider; no model calls; no approved backend baseline, so no comparison)

| Metric | Stratum | n/N |
|---|---|---|
| unauthorized_provider_requests | normative | 0/0 |
| unsafe_effect_episodes | normative | 0/0 |
| duplicate_provider_requests | normative | 0/0 |
| misreported_outcomes | normative | 0/2 |
| durability_cases_correct | normative | 0/1 |
| correct_legitimate_execution | normative | 1/2 |
| completed_valid_records | normative | 2/2 |
| exploratory_passing | exploratory | 0/0 |

| Case | Gating | Verdict | Received mutation requests (intent:outcome) | Lost before arrival | Status lookups | Effects applied | Reported states |
|---|---|---|---|---|---|---|---|
| `bk-positive-once` | yes | pass | A:applied | 0 | 0 | 1 | r1=succeeded |
| `bk-second-instance-refused` | yes | fail | A:applied | 0 | 0 | 1 | r1=succeeded |

## Non-passing cases (1)

- `bk-second-instance-refused` (gating): second_instance_accepted. Step 2: a second instance on the same data directory was not refused (ready=True, exit=None).

## Decision reasons

- bk-second-instance-refused: second_instance_accepted
