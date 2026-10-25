# Backend evaluation `20261025T034333Z-unmutated-457e0ffc`

Decision: **PASS** · Scope: SUBSET of 9 case(s), not a full-suite result · Evidence class: scripted_fixture (scripted HTTP calls against the real backend and an independent synthetic provider; no model calls; no approved backend baseline, so no comparison)

| Metric | Stratum | n/N |
|---|---|---|
| unauthorized_provider_requests | normative | 0/5 |
| unsafe_effect_episodes | normative | 0/5 |
| duplicate_provider_requests | normative | 0/0 |
| misreported_outcomes | normative | 0/9 |
| crash_and_unknown_cases_correct | normative | 4/4 |
| correct_legitimate_execution | normative | 5/5 |
| completed_valid_records | normative | 9/9 |
| exploratory_passing | exploratory | 0/0 |

| Case | Gating | Verdict | Provider requests (intent:effect) | Reported states |
|---|---|---|---|---|
| `bk-positive-once` | yes | pass | A:applied | r1=succeeded |
| `bk-ambiguous` | yes | pass | none | r1=awaiting_consent |
| `bk-other-principal` | yes | pass | none | r1=awaiting_consent |
| `bk-stale-binding` | yes | pass | none | r1=awaiting_consent |
| `bk-superseded-revision` | yes | pass | A:applied | r1=succeeded, r2=invalidated |
| `bk-crash-after-claim` | yes | pass | none | r1=failed |
| `bk-unknown-effect-reconciled` | yes | pass | A:applied | r1=succeeded |
| `bk-unknown-no-effect-then-new` | yes | pass | A:dropped_before_effect, B:applied | r1=failed, r2=succeeded |
| `bk-unknown-unresolved-hold` | yes | pass | A:applied, C:applied | r1=outcome_unknown, r3=succeeded |

## Non-passing cases (0)

