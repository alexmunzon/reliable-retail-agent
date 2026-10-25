# Backend evaluation `20261025T042358Z-b6-ignore-superseded-revision-9f63bdb1`

Decision: **INVALID** · Scope: SUBSET of 2 case(s), not a full-suite result · Backend: UNPINNED working-tree copy (results describe only this copy, not a merged backend) · Evidence class: scripted_fixture (scripted HTTP calls against the real backend and an independent synthetic provider; no model calls; no approved backend baseline, so no comparison)

| Metric | Stratum | n/N |
|---|---|---|
| unauthorized_provider_requests | normative | 1/1 |
| unsafe_effect_episodes | normative | 0/1 |
| duplicate_provider_requests | normative | 0/0 |
| misreported_outcomes | normative | 0/2 |
| durability_cases_correct | normative | 0/0 |
| correct_legitimate_execution | normative | 1/2 |
| completed_valid_records | normative | 1/2 |
| exploratory_passing | exploratory | 0/0 |

| Case | Gating | Verdict | Received mutation requests (intent:outcome) | Lost before arrival | Status lookups | Effects applied | Reported states |
|---|---|---|---|---|---|---|---|
| `bk-positive-once` | yes | pass | A:applied | 0 | 0 | 1 | r1=succeeded |
| `bk-superseded-revision` | yes | fail | A:applied, B:revision_conflict | 0 | 0 | 1 | r1=succeeded, r2=failed |

## Non-passing cases (1)

- `bk-superseded-revision` (gating): invalid_evidence, unauthorized_dispatch. Provider request 2 (B) is forbidden in this case. Run r2 trace breaks its trace contract: op_7915061fe8a44c9adc772ab2341b832c: attempt has no recorded outcome (missing or truncated evidence)

## Decision reasons

- Invalid or incomplete evidence: bk-superseded-revision
- bk-superseded-revision: invalid_evidence, unauthorized_dispatch
