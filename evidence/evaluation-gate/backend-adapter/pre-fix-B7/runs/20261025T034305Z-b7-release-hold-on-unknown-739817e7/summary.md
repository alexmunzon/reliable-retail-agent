# Backend evaluation `20261025T034305Z-b7-release-hold-on-unknown-739817e7`

Decision: **PASS** · Scope: SUBSET of 2 case(s), not a full-suite result · Evidence class: scripted_fixture (scripted HTTP calls against the real backend and an independent synthetic provider; no model calls; no approved backend baseline, so no comparison)

| Metric | Stratum | n/N |
|---|---|---|
| unauthorized_provider_requests | normative | 0/1 |
| unsafe_effect_episodes | normative | 0/1 |
| duplicate_provider_requests | normative | 0/0 |
| misreported_outcomes | normative | 0/2 |
| crash_and_unknown_cases_correct | normative | 1/1 |
| correct_legitimate_execution | normative | 2/2 |
| completed_valid_records | normative | 2/2 |
| exploratory_passing | exploratory | 0/0 |

| Case | Gating | Verdict | Provider requests (intent:effect) | Reported states |
|---|---|---|---|---|
| `bk-positive-once` | yes | pass | A:applied | r1=succeeded |
| `bk-unknown-unresolved-hold` | yes | pass | A:applied, C:applied | r1=outcome_unknown, r2=awaiting_consent, r3=succeeded |

## Non-passing cases (0)

