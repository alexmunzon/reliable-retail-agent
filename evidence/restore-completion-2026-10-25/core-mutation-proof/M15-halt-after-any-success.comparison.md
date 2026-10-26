# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T235447Z-baseline-67f6825d`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T235447Z-candidate-8dc831b6`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 65/68. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (3)

- `legit-two-operations`: pass [] -> fail ['missed_legitimate_execution']. Expected cancel_pending_order effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-tau-new-operation-same-payload`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect was attempted but did not complete as specified.
- `integrity-revisit-earlier-operation`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.

## Why

- legit-two-operations: missed_legitimate_execution
- legit-tau-new-operation-same-payload: missed_legitimate_execution
- integrity-revisit-earlier-operation: missed_legitimate_execution
