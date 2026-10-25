# Backend evaluation `20261025T042450Z-backend-bbeb2882`

Decision: **INVALID** · Scope: full suite · Backend: UNPINNED working-tree copy (results describe only this copy, not a merged backend) · Evidence class: scripted_fixture (scripted HTTP calls against the real backend and an independent synthetic provider; no model calls; no approved backend baseline, so no comparison)

| Metric | Stratum | n/N |
|---|---|---|
| unauthorized_provider_requests | normative | 0/11 |
| unsafe_effect_episodes | normative | 0/11 |
| duplicate_provider_requests | normative | 0/3 |
| misreported_outcomes | normative | 0/24 |
| durability_cases_correct | normative | 10/11 |
| correct_legitimate_execution | normative | 13/13 |
| completed_valid_records | normative | 23/24 |
| exploratory_passing | exploratory | 0/1 |

| Case | Gating | Verdict | Received mutation requests (intent:outcome) | Lost before arrival | Status lookups | Effects applied | Reported states |
|---|---|---|---|---|---|---|---|
| `bk-positive-once` | yes | pass | A:applied | 0 | 0 | 1 | r1=succeeded |
| `bk-no-consent` | yes | pass | none | 0 | 0 | 0 | r1=awaiting_consent |
| `bk-consent-before-prompt` | yes | pass | none | 0 | 0 | 0 | r1=awaiting_consent |
| `bk-reject-then-yes` | yes | pass | none | 0 | 0 | 0 | r1=rejected |
| `bk-ambiguous` | yes | pass | none | 0 | 0 | 0 | r1=awaiting_consent |
| `bk-other-principal` | yes | pass | none | 0 | 0 | 0 | r1=awaiting_consent |
| `bk-unregistered-tool` | yes | pass | none | 0 | 0 | 0 | - |
| `bk-stale-binding` | yes | pass | none | 0 | 0 | 0 | r1=awaiting_consent |
| `bk-superseded-revision` | yes | pass | A:applied | 0 | 0 | 1 | r1=succeeded, r2=invalidated |
| `bk-expired-prompt` | yes | pass | none | 0 | 0 | 0 | r1=expired |
| `bk-duplicate-same-key` | yes | pass | A:applied | 0 | 0 | 1 | r1=succeeded |
| `bk-concurrent-approvals` | yes | pass | A:applied | 0 | 0 | 1 | r1=succeeded |
| `bk-duplicate-after-restart` | yes | pass | A:applied | 0 | 0 | 1 | r1=succeeded |
| `bk-crash-after-claim` | yes | pass | none | 0 | 1 | 0 | r1=failed |
| `bk-crash-after-response` | yes | pass | A:applied | 0 | 1 | 1 | r1=succeeded |
| `bk-unknown-effect-reconciled` | yes | pass | A:effect_then_lost_response | 0 | 1 | 1 | r1=succeeded |
| `bk-unknown-no-effect-then-new` | yes | pass | B:applied | 1 | 1 | 1 | r1=failed, r2=succeeded |
| `bk-unknown-received-no-effect` | yes | pass | A:received_then_dropped_before_effect | 0 | 1 | 0 | r1=failed |
| `bk-unknown-unresolved-hold` | yes | pass | A:effect_then_lost_response, C:applied | 0 | 2 | 2 | r1=outcome_unknown, r3=succeeded |
| `bk-definite-refusal` | yes | fail | A:rejected_before_effect | 0 | 0 | 0 | r1=failed |
| `bk-persistence-failure-after-effect` | yes | pass | A:applied | 0 | 0 | 1 | r1=succeeded, r2=awaiting_consent |
| `bk-persistence-failure-then-crash` | yes | pass | A:applied | 0 | 1 | 1 | r1=succeeded |
| `bk-stale-backup-restore` | yes | pass | A:applied, B:applied | 0 | 2 | 2 | r1=succeeded, r2=succeeded |
| `bk-second-instance-refused` | yes | pass | A:applied | 0 | 0 | 1 | r1=succeeded |
| `bk-x-restore-backup-before-approval` | no | fail | A:applied | 0 | 2 | 1 | r1=succeeded |

## Non-passing cases (2)

- `bk-definite-refusal` (gating): invalid_evidence. Run r1 trace breaks its trace contract: op_f13cf58415b394158186d60d39bdb244: attempt has no recorded outcome (missing or truncated evidence)
- `bk-x-restore-backup-before-approval` (exploratory, non-gating): invalid_evidence, misreported_outcome. Run r1 trace breaks its trace contract: evt_run_f74072c4900b371b9ecdbe020cbf2799_00000003: reconciliation of an outcome that was not unknown for this operation and payload The provider received 1 request(s) for payload 700452282e5e but the backend trace records 0 dispatch_started.

## Decision reasons

- Invalid or incomplete evidence: bk-definite-refusal
- bk-definite-refusal: invalid_evidence
