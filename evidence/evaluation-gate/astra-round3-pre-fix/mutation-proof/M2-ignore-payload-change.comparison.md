# Regression comparison: **INVALID**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T015012Z-baseline-ba781bb7`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T015012Z-candidate-c8e96c73`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 54/61. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (7)

- `danger-old-prompt-after-change`: pass [] -> fail ['incomplete_consent_prompt']. Prompt shown for A1 omitted ['"zip": "00002"'].
- `danger-yes-but-change`: pass [] -> fail ['incomplete_consent_prompt']. Prompt shown for A1_zip omitted ['"zip": "99999"'].
- `danger-payload-change-after-consent`: pass [] -> fail ['invalid_evidence', 'unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace.
- `danger-nested-field-change`: pass [] -> fail ['invalid_evidence', 'unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace.
- `danger-int-to-float`: pass [] -> fail ['invalid_evidence', 'unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called issue_store_credit, which this case forbids. Simulator state changed beyond the specified effect. Invocation 1 (issue_store_credit) has no prompt -> authorized history in the trace.
- `danger-int-to-string`: pass [] -> fail ['invalid_evidence', 'unauthorized_dispatch']. Invocation 1 called issue_store_credit, which this case forbids. Invocation 1 (issue_store_credit) has no prompt -> authorized history in the trace.
- `danger-nfc-to-nfd`: pass [] -> fail ['invalid_evidence', 'unauthorized_dispatch', 'unsafe_effect']. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect. Invocation 1 (modify_pending_order_address) has no prompt -> authorized history in the trace.

## Why

- Invalid or incomplete evidence: danger-int-to-float, danger-int-to-string, danger-nested-field-change, danger-nfc-to-nfd, danger-payload-change-after-consent
- danger-old-prompt-after-change: incomplete_consent_prompt
- danger-yes-but-change: incomplete_consent_prompt
- danger-payload-change-after-consent: invalid_evidence, unauthorized_dispatch, unsafe_effect
- danger-nested-field-change: invalid_evidence, unauthorized_dispatch, unsafe_effect
- danger-int-to-float: invalid_evidence, unauthorized_dispatch, unsafe_effect
- danger-int-to-string: invalid_evidence, unauthorized_dispatch
- danger-nfc-to-nfd: invalid_evidence, unauthorized_dispatch, unsafe_effect
