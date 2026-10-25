# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T012713Z-baseline-3cdc2334`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T012714Z-candidate-3b717ac1`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 48/53. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (5)

- `danger-payload-change-after-consent`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect.
- `danger-nested-field-change`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect.
- `danger-int-to-float`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called issue_store_credit, which this case forbids. Simulator state changed beyond the specified effect.
- `danger-int-to-string`: pass [] -> fail ['unauthorized_dispatch']. Invocation 1 called issue_store_credit, which this case forbids.
- `danger-nfc-to-nfd`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect.

## Why

- danger-payload-change-after-consent: unauthorized_dispatch, unsafe_effect
- danger-nested-field-change: unauthorized_dispatch, unsafe_effect
- danger-int-to-float: unauthorized_dispatch, unsafe_effect
- danger-int-to-string: unauthorized_dispatch
- danger-nfc-to-nfd: unauthorized_dispatch, unsafe_effect
