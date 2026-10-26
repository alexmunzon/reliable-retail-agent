# Backend evaluation `20261025T235224Z-b11-admit-while-degraded-421c338d`

Decision: **FAIL** · Scope: SUBSET of 2 case(s), not a full-suite result · Backend: UNPINNED working-tree copy (results describe only this copy, not a merged backend) · Evidence class: scripted_fixture (scripted HTTP calls against the real backend and an independent synthetic provider; no model calls; no approved backend baseline, so no comparison)

| Metric | Stratum | n/N |
|---|---|---|
| unauthorized_provider_requests | normative | 0/1 |
| unsafe_effect_episodes | normative | 0/1 |
| duplicate_provider_requests | normative | 0/0 |
| misreported_outcomes | normative | 0/2 |
| durability_cases_correct | normative | 0/1 |
| correct_legitimate_execution | normative | 1/2 |
| completed_valid_records | normative | 2/2 |
| exploratory_passing | exploratory | 0/0 |

| Case | Gating | Verdict | Received mutation requests (intent:outcome) | Lost before arrival | Status lookups | Effects applied | Reported states |
|---|---|---|---|---|---|---|---|
| `bk-positive-once` | yes | pass | A:applied | 0 | 0 | 1 | r1=succeeded |
| `bk-persistence-failure-after-effect` | yes | fail | A:applied | 0 | 0 | 1 | r1=succeeded, r2=awaiting_consent |

## Non-passing cases (1)

- `bk-persistence-failure-after-effect` (gating): uncontrolled_refusal. Step 10 (consent) was answered [500] 'internal_error'; specified a controlled refusal [503] 'service_degraded'.

## Decision reasons

- bk-persistence-failure-after-effect: uncontrolled_refusal
