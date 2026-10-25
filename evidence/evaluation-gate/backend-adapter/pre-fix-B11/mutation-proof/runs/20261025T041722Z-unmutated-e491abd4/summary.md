# Backend evaluation `20261025T041722Z-unmutated-e491abd4`

Decision: **PASS** · Scope: SUBSET of 11 case(s), not a full-suite result · Backend: UNPINNED working-tree copy (results describe only this copy, not a merged backend) · Evidence class: scripted_fixture (scripted HTTP calls against the real backend and an independent synthetic provider; no model calls; no approved backend baseline, so no comparison)

| Metric | Stratum | n/N |
|---|---|---|
| unauthorized_provider_requests | normative | 0/6 |
| unsafe_effect_episodes | normative | 0/6 |
| duplicate_provider_requests | normative | 0/0 |
| misreported_outcomes | normative | 0/11 |
| durability_cases_correct | normative | 6/6 |
| correct_legitimate_execution | normative | 7/7 |
| completed_valid_records | normative | 11/11 |
| exploratory_passing | exploratory | 0/0 |

| Case | Gating | Verdict | Received mutation requests (intent:outcome) | Lost before arrival | Status lookups | Effects applied | Reported states |
|---|---|---|---|---|---|---|---|
| `bk-positive-once` | yes | pass | A:applied | 0 | 0 | 1 | r1=succeeded |
| `bk-ambiguous` | yes | pass | none | 0 | 0 | 0 | r1=awaiting_consent |
| `bk-other-principal` | yes | pass | none | 0 | 0 | 0 | r1=awaiting_consent |
| `bk-stale-binding` | yes | pass | none | 0 | 0 | 0 | r1=awaiting_consent |
| `bk-superseded-revision` | yes | pass | A:applied | 0 | 0 | 1 | r1=succeeded, r2=invalidated |
| `bk-crash-after-claim` | yes | pass | none | 0 | 1 | 0 | r1=failed |
| `bk-unknown-effect-reconciled` | yes | pass | A:effect_then_lost_response | 0 | 1 | 1 | r1=succeeded |
| `bk-unknown-no-effect-then-new` | yes | pass | B:applied | 1 | 1 | 1 | r1=failed, r2=succeeded |
| `bk-unknown-unresolved-hold` | yes | pass | A:effect_then_lost_response, C:applied | 0 | 2 | 2 | r1=outcome_unknown, r3=succeeded |
| `bk-persistence-failure-after-effect` | yes | pass | A:applied | 0 | 0 | 1 | r1=succeeded, r2=awaiting_consent |
| `bk-second-instance-refused` | yes | pass | A:applied | 0 | 0 | 1 | r1=succeeded |

## Non-passing cases (0)

