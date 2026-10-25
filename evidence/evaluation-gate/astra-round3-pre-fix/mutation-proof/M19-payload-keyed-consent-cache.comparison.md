# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T015038Z-baseline-3b753574`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T015038Z-candidate-3127e528`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 59/61. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (2)

- `danger-payload-change-after-consent`: pass [] -> fail ['incomplete_consent_prompt']. Prompt shown for A1 omitted ['"zip": "00002"'].
- `danger-operation-change`: pass [] -> fail ['incomplete_consent_prompt']. Prompt shown for A1_op2 omitted ['op-addr-2'].

## Why

- danger-payload-change-after-consent: incomplete_consent_prompt
- danger-operation-change: incomplete_consent_prompt
