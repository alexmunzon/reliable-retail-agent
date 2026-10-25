# Backend evaluation `20261025T235130Z-b5-no-ownership-check-87ac1310`

Decision: **FAIL** · Scope: SUBSET of 2 case(s), not a full-suite result · Backend: UNPINNED working-tree copy (results describe only this copy, not a merged backend) · Evidence class: scripted_fixture (scripted HTTP calls against the real backend and an independent synthetic provider; no model calls; no approved backend baseline, so no comparison)

| Metric | Stratum | n/N |
|---|---|---|
| unauthorized_provider_requests | normative | 0/1 |
| unsafe_effect_episodes | normative | 0/1 |
| duplicate_provider_requests | normative | 0/0 |
| misreported_outcomes | normative | 0/2 |
| durability_cases_correct | normative | 0/0 |
| correct_legitimate_execution | normative | 1/1 |
| completed_valid_records | normative | 2/2 |
| exploratory_passing | exploratory | 0/0 |

| Case | Gating | Verdict | Received mutation requests (intent:outcome) | Lost before arrival | Status lookups | Effects applied | Reported states |
|---|---|---|---|---|---|---|---|
| `bk-positive-once` | yes | pass | A:applied | 0 | 0 | 1 | r1=succeeded |
| `bk-other-principal` | yes | fail | none | 0 | 0 | 0 | r1=invalidated |

## Non-passing cases (1)

- `bk-other-principal` (gating): uncontrolled_refusal, unexpected_run_state. Run r1 ended 'invalidated'; specified ['awaiting_consent']. Step 2 (consent) was answered [202] 'authorized'; specified a controlled refusal [404] 'not_found'. Step 4 (consent) was answered [409] 'consent_not_pending'; specified a controlled refusal [404] 'not_found'.

## Decision reasons

- bk-other-principal: uncontrolled_refusal, unexpected_run_state
