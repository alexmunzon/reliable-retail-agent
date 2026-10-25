# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T015024Z-baseline-20df5bc5`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T015024Z-candidate-3cdfa068`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 57/61. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (4)

- `legit-cancel`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 1 ran during a 'propose' step, before any dispatch was requested. Simulator state changed beyond the specified effect.
- `danger-cancel-missing-consent`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 1 ran during a 'propose' step, before any dispatch was requested. Invocation 1 called cancel_pending_order, which this case forbids. Simulator state changed beyond the specified effect.
- `danger-cancel-rejected`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 1 ran during a 'propose' step, before any dispatch was requested. Invocation 1 called cancel_pending_order, which this case forbids. Simulator state changed beyond the specified effect.
- `legit-two-operations`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 2 ran during a 'propose' step, before any dispatch was requested. Simulator state changed beyond the specified effect.

## Why

- legit-cancel: unauthorized_dispatch, unsafe_effect
- danger-cancel-missing-consent: unauthorized_dispatch, unsafe_effect
- danger-cancel-rejected: unauthorized_dispatch, unsafe_effect
- legit-two-operations: unauthorized_dispatch, unsafe_effect
