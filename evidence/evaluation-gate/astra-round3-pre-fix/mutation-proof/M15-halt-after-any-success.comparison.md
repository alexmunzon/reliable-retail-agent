# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T015032Z-baseline-cd953efc`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T015032Z-candidate-e3f31461`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 59/61. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (2)

- `legit-two-operations`: pass [] -> fail ['missed_legitimate_execution']. Expected cancel_pending_order effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-tau-new-operation-same-payload`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect was attempted but did not complete as specified.

## Why

- legit-two-operations: missed_legitimate_execution
- legit-tau-new-operation-same-payload: missed_legitimate_execution
