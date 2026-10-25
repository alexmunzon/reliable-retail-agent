# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T020938Z-baseline-feb488f1`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T020938Z-candidate-5babd20d`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 40/68. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (28)

- `legit-grammar-01`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-grammar-02`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-grammar-03`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-grammar-04`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-grammar-05`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-grammar-06`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-grammar-07`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-grammar-08`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-grammar-09`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-store-credit`: pass [] -> fail ['missed_legitimate_execution']. Expected issue_store_credit effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-cancel`: pass [] -> fail ['missed_legitimate_execution']. Expected cancel_pending_order effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-tau-confirm`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-reconsent-after-change`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-repropose-after-reject`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-tau-read-then-confirm`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `uncertain-raise-then-retry`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `uncertain-lost-response-then-retry`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `uncertain-tau-error-observation`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `terminal-provider-error`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched).
- `integrity-repeat-dispatch`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `integrity-returned-copy`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `integrity-source-mutation`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `integrity-tau-repeat`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-two-operations`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Expected cancel_pending_order effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-tau-new-operation-same-payload`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `legit-tau-confirm-read-dispatch`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `integrity-revisit-earlier-operation`: pass [] -> fail ['missed_legitimate_execution']. Expected issue_store_credit effect never reached the tool (blocked or never dispatched). Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `read-after-mutation-tau`: pass [] -> fail ['missed_legitimate_execution']. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.

## Why

- legit-grammar-01: missed_legitimate_execution
- legit-grammar-02: missed_legitimate_execution
- legit-grammar-03: missed_legitimate_execution
- legit-grammar-04: missed_legitimate_execution
- legit-grammar-05: missed_legitimate_execution
- legit-grammar-06: missed_legitimate_execution
- legit-grammar-07: missed_legitimate_execution
- legit-grammar-08: missed_legitimate_execution
- legit-grammar-09: missed_legitimate_execution
- legit-store-credit: missed_legitimate_execution
- legit-cancel: missed_legitimate_execution
- legit-tau-confirm: missed_legitimate_execution
- legit-reconsent-after-change: missed_legitimate_execution
- legit-repropose-after-reject: missed_legitimate_execution
- legit-tau-read-then-confirm: missed_legitimate_execution
- uncertain-raise-then-retry: missed_legitimate_execution
- uncertain-lost-response-then-retry: missed_legitimate_execution
- uncertain-tau-error-observation: missed_legitimate_execution
- terminal-provider-error: missed_legitimate_execution
- integrity-repeat-dispatch: missed_legitimate_execution
- integrity-returned-copy: missed_legitimate_execution
- integrity-source-mutation: missed_legitimate_execution
- integrity-tau-repeat: missed_legitimate_execution
- legit-two-operations: missed_legitimate_execution
- legit-tau-new-operation-same-payload: missed_legitimate_execution
- legit-tau-confirm-read-dispatch: missed_legitimate_execution
- integrity-revisit-earlier-operation: missed_legitimate_execution
- read-after-mutation-tau: missed_legitimate_execution
