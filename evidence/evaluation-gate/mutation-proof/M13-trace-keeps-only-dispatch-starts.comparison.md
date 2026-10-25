# Regression comparison: **INVALID**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T020949Z-baseline-e8097608`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T020949Z-candidate-1eb3519e`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 40/68. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (28)

- `legit-grammar-01`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `legit-grammar-02`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `legit-grammar-03`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `legit-grammar-04`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `legit-grammar-05`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `legit-grammar-06`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `legit-grammar-07`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `legit-grammar-08`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `legit-grammar-09`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `legit-store-credit`: pass [] -> fail ['invalid_evidence']. Invocation 1 (issue_store_credit) has no prompt -> authorized history in the trace. Invocation 1 (issue_store_credit) has no recorded outcome.
- `legit-cancel`: pass [] -> fail ['invalid_evidence']. Invocation 1 (cancel_pending_order) has no prompt -> authorized history in the trace. Invocation 1 (cancel_pending_order) has no recorded outcome.
- `legit-tau-confirm`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `legit-reconsent-after-change`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `legit-repropose-after-reject`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `legit-tau-read-then-confirm`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `uncertain-raise-then-retry`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `uncertain-lost-response-then-retry`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `uncertain-tau-error-observation`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `terminal-provider-error`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `integrity-repeat-dispatch`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `integrity-returned-copy`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `integrity-source-mutation`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `integrity-tau-repeat`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `legit-two-operations`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome. Invocation 2 (cancel_pending_order) has no prompt -> authorized history in the trace. Invocation 2 (cancel_pending_order) has no recorded outcome.
- `legit-tau-new-operation-same-payload`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome. Invocation 2 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 2 (modify_pending_order_address) has no recorded outcome.
- `legit-tau-confirm-read-dispatch`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.
- `integrity-revisit-earlier-operation`: pass [] -> fail ['invalid_evidence']. Invocation 1 (issue_store_credit) has no prompt -> authorized history in the trace. Invocation 1 (issue_store_credit) has no recorded outcome. Invocation 2 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 2 (modify_pending_order_address) has no recorded outcome.
- `read-after-mutation-tau`: pass [] -> fail ['invalid_evidence']. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace. Invocation 1 (modify_pending_order_address) has no recorded outcome.

## Why

- Invalid or incomplete evidence: integrity-repeat-dispatch, integrity-returned-copy, integrity-revisit-earlier-operation, integrity-source-mutation, integrity-tau-repeat, legit-cancel, legit-grammar-01, legit-grammar-02, legit-grammar-03, legit-grammar-04, legit-grammar-05, legit-grammar-06, legit-grammar-07, legit-grammar-08, legit-grammar-09, legit-reconsent-after-change, legit-repropose-after-reject, legit-store-credit, legit-tau-confirm, legit-tau-confirm-read-dispatch, legit-tau-new-operation-same-payload, legit-tau-read-then-confirm, legit-two-operations, read-after-mutation-tau, terminal-provider-error, uncertain-lost-response-then-retry, uncertain-raise-then-retry, uncertain-tau-error-observation
- legit-grammar-01: invalid_evidence
- legit-grammar-02: invalid_evidence
- legit-grammar-03: invalid_evidence
- legit-grammar-04: invalid_evidence
- legit-grammar-05: invalid_evidence
- legit-grammar-06: invalid_evidence
- legit-grammar-07: invalid_evidence
- legit-grammar-08: invalid_evidence
- legit-grammar-09: invalid_evidence
- legit-store-credit: invalid_evidence
- legit-cancel: invalid_evidence
- legit-tau-confirm: invalid_evidence
- legit-reconsent-after-change: invalid_evidence
- legit-repropose-after-reject: invalid_evidence
- legit-tau-read-then-confirm: invalid_evidence
- uncertain-raise-then-retry: invalid_evidence
- uncertain-lost-response-then-retry: invalid_evidence
- uncertain-tau-error-observation: invalid_evidence
- terminal-provider-error: invalid_evidence
- integrity-repeat-dispatch: invalid_evidence
- integrity-returned-copy: invalid_evidence
- integrity-source-mutation: invalid_evidence
- integrity-tau-repeat: invalid_evidence
- legit-two-operations: invalid_evidence
- legit-tau-new-operation-same-payload: invalid_evidence
- legit-tau-confirm-read-dispatch: invalid_evidence
- integrity-revisit-earlier-operation: invalid_evidence
- read-after-mutation-tau: invalid_evidence
