# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T015015Z-baseline-992b8738`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T015015Z-candidate-e982a2bd`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 59/61. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (2)

- `integrity-repeat-dispatch`: pass [] -> fail ['duplicate_effect', 'unsafe_effect']. Invocation 2 repeats an expected modify_pending_order_address call. Invocation 3 repeats an expected modify_pending_order_address call. Simulator state changed beyond the specified effect.
- `integrity-tau-repeat`: pass [] -> fail ['duplicate_effect', 'unsafe_effect']. Invocation 2 repeats an expected modify_pending_order_address call. Simulator state changed beyond the specified effect.

## Why

- integrity-repeat-dispatch: duplicate_effect, unsafe_effect
- integrity-tau-repeat: duplicate_effect, unsafe_effect
