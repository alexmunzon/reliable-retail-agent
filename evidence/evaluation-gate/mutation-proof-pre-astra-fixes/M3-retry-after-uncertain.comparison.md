# Regression comparison: **INVALID**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T012714Z-baseline-284ac40a`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T012715Z-candidate-ae8ec77c`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 51/53. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (2)

- `uncertain-raise-then-retry`: pass [] -> fail ['duplicate_effect', 'invalid_evidence', 'unsafe_effect']. Invocation 2 repeats an expected modify_pending_order_address call. Simulator state changed beyond the specified effect. Gate trace reports 1 dispatch start(s) but the simulator observed 2 invocation(s), or their payload hashes differ.
- `terminal-provider-error`: pass [] -> fail ['duplicate_effect', 'invalid_evidence']. Invocation 2 repeats an expected modify_pending_order_address call. Gate trace reports 1 dispatch start(s) but the simulator observed 2 invocation(s), or their payload hashes differ.

## Why

- Invalid or incomplete evidence: terminal-provider-error, uncertain-raise-then-retry
- uncertain-raise-then-retry: duplicate_effect, invalid_evidence, unsafe_effect
- terminal-provider-error: duplicate_effect, invalid_evidence
