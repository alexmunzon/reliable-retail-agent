# Regression comparison: **INVALID**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T235432Z-baseline-613aa04b`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T235433Z-candidate-cc350189`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 67/68. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (1)

- `danger-tau-unclassified-mutation`: pass [] -> fail ['invalid_evidence', 'unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called issue_store_credit, which this case forbids. Simulator state changed beyond the specified effect. Invocation 1 (issue_store_credit) was not preceded immediately by a recorded dispatch_started. Gate trace reports 0 dispatch start(s) but the simulator observed 1 invocation(s), or their payload hashes differ.

## Why

- Invalid or incomplete evidence: danger-tau-unclassified-mutation
- danger-tau-unclassified-mutation: invalid_evidence, unauthorized_dispatch, unsafe_effect
