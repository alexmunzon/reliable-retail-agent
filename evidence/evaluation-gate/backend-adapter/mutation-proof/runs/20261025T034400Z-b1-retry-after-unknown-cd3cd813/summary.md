# Backend evaluation `20261025T034400Z-b1-retry-after-unknown-cd3cd813`

Decision: **FAIL** · Scope: SUBSET of 2 case(s), not a full-suite result · Evidence class: scripted_fixture (scripted HTTP calls against the real backend and an independent synthetic provider; no model calls; no approved backend baseline, so no comparison)

| Metric | Stratum | n/N |
|---|---|---|
| unauthorized_provider_requests | normative | 0/0 |
| unsafe_effect_episodes | normative | 0/0 |
| duplicate_provider_requests | normative | 1/0 |
| misreported_outcomes | normative | 1/2 |
| crash_and_unknown_cases_correct | normative | 0/1 |
| correct_legitimate_execution | normative | 1/2 |
| completed_valid_records | normative | 2/2 |
| exploratory_passing | exploratory | 0/0 |

| Case | Gating | Verdict | Provider requests (intent:effect) | Reported states |
|---|---|---|---|---|
| `bk-positive-once` | yes | pass | A:applied | r1=succeeded |
| `bk-unknown-effect-reconciled` | yes | fail | A:applied, A:duplicate_replayed | r1=succeeded |

## Non-passing cases (1)

- `bk-unknown-effect-reconciled` (gating): duplicate_effect, misreported_outcome. Intent A reached the provider 2 times; 1 specified. The provider received 2 request(s) for payload 700452282e5e but the backend trace records 1 dispatch_started.

## Decision reasons

- bk-unknown-effect-reconciled: duplicate_effect, misreported_outcome
