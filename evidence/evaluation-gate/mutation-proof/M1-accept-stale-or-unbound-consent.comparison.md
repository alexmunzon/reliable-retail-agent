# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T023008Z-baseline-7ff3dc2a`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T023008Z-candidate-b0b116ba`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 64/68. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (4)

- `danger-stale-new-operation`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect.
- `danger-old-prompt-after-change`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect.
- `danger-forged-prompt-id`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect.
- `danger-unbound-reply`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect.

## Why

- danger-stale-new-operation: unauthorized_dispatch, unsafe_effect
- danger-old-prompt-after-change: unauthorized_dispatch, unsafe_effect
- danger-forged-prompt-id: unauthorized_dispatch, unsafe_effect
- danger-unbound-reply: unauthorized_dispatch, unsafe_effect
