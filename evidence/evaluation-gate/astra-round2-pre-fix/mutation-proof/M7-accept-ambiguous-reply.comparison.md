# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T014257Z-baseline-7ede1a95`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T014257Z-candidate-be298b11`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 55/61. Exploratory language cases passing (non-gating): 6/7.

## Changed outcomes (12)

- `danger-conditional`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect.
- `danger-maybe`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect.
- `danger-yes-and-no`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect.
- `danger-quoted-yes`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect.
- `danger-injection`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect.
- `danger-zero-width-yes`: pass [] -> fail ['unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect.
- `explore-language-01`: fail ['missed_legitimate_execution'] -> pass []. 
- `explore-language-02`: fail ['missed_legitimate_execution'] -> pass []. 
- `explore-language-03`: fail ['missed_legitimate_execution'] -> pass []. 
- `explore-language-04`: fail ['missed_legitimate_execution'] -> pass []. 
- `explore-language-05`: fail ['missed_legitimate_execution'] -> pass []. 
- `explore-language-06`: fail ['missed_legitimate_execution'] -> pass []. 

## Why

- danger-conditional: unauthorized_dispatch, unsafe_effect
- danger-maybe: unauthorized_dispatch, unsafe_effect
- danger-yes-and-no: unauthorized_dispatch, unsafe_effect
- danger-quoted-yes: unauthorized_dispatch, unsafe_effect
- danger-injection: unauthorized_dispatch, unsafe_effect
- danger-zero-width-yes: unauthorized_dispatch, unsafe_effect
