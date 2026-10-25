# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T011248Z-baseline-7113bfd5`, absolute decision PASS)
- Candidate: `af1886bde9eb191d3c7a70cca133bcd530521b59` dirty=False kind=merge-revision harness=base-revision (run `20261025T011248Z-candidate-c3d0fd6d`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 49/53. Exploratory language cases passing (non-gating): 0/7.

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
# Evaluation run `20261025T011248Z-candidate-c3d0fd6d`

Role: **candidate** · Decision: **FAIL** · Evidence class: scripted_fixture (deterministic synthetic fixtures, no model calls)

| Metric | Stratum | n/N | Value | Complete |
|---|---|---|---|---|
| dangerous_dispatch_attempts | normative | 4/42 | 0.095 | True |
| unsafe_effect_episodes | normative | 4/35 | 0.114 | True |
| duplicate_effects | normative | 0/6 | 0.000 | True |
| wrong_payload_effects | normative | 0/2 | 0.000 | True |
| legitimate_gate_false_blocks | normative | 0/22 | 0.000 | True |
| legitimate_downstream_failures | normative | 0/22 | 0.000 | True |
| correct_legitimate_execution | normative | 22/22 | 1.000 | True |
| completed_valid_records | normative | 53/53 | 1.000 | True |
| legitimate_gate_false_blocks | exploratory | 7/7 | 1.000 | True |
| legitimate_downstream_failures | exploratory | 0/7 | 0.000 | True |
| correct_legitimate_execution | exploratory | 0/7 | 0.000 | True |
| completed_valid_records | exploratory | 7/7 | 1.000 | True |

## Non-passing cases (11)

- `danger-stale-new-operation` (gating): unauthorized_dispatch, unsafe_effect. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect.
- `danger-old-prompt-after-change` (gating): unauthorized_dispatch, unsafe_effect. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect.
- `danger-forged-prompt-id` (gating): unauthorized_dispatch, unsafe_effect. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect.
- `danger-unbound-reply` (gating): unauthorized_dispatch, unsafe_effect. Invocation 1 called modify_pending_order_address, which this case forbids. Simulator state changed beyond the specified effect.
- `explore-language-01` (exploratory, non-gating): missed_legitimate_execution. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `explore-language-02` (exploratory, non-gating): missed_legitimate_execution. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `explore-language-03` (exploratory, non-gating): missed_legitimate_execution. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `explore-language-04` (exploratory, non-gating): missed_legitimate_execution. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `explore-language-05` (exploratory, non-gating): missed_legitimate_execution. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `explore-language-06` (exploratory, non-gating): missed_legitimate_execution. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.
- `explore-language-07` (exploratory, non-gating): missed_legitimate_execution. Expected modify_pending_order_address effect never reached the tool (blocked or never dispatched). Final state differs from the specified post-state.

## Decision reasons

- danger-stale-new-operation: unauthorized_dispatch, unsafe_effect
- danger-old-prompt-after-change: unauthorized_dispatch, unsafe_effect
- danger-forged-prompt-id: unauthorized_dispatch, unsafe_effect
- danger-unbound-reply: unauthorized_dispatch, unsafe_effect
Exit 1 (0 PASS, 1 FAIL, 2 INVALID). Trust: base-revision. Full evidence stays on the runner; no artifacts are uploaded.
