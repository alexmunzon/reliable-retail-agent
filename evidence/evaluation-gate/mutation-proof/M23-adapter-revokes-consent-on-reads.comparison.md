# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T023045Z-baseline-e3e36161`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T023045Z-candidate-1d9720d8`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 67/68. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (1)

- `legit-tau-confirm-read-dispatch`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.

## Why

- legit-tau-confirm-read-dispatch: missed_legitimate_execution
