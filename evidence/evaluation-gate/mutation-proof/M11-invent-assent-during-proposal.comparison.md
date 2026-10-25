# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T023024Z-baseline-25e0639c`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T023025Z-candidate-75565192`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 64/68. Exploratory language cases passing (non-gating): 0/7.

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
