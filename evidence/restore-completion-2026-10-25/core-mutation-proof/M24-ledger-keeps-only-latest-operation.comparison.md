# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T235504Z-baseline-6f673f83`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T235504Z-candidate-5200104a`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 67/68. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (1)

- `integrity-revisit-earlier-operation`: pass [] -> fail ['duplicate_effect', 'unsafe_effect']. Invocation 3 repeats an expected issue_store_credit call. Simulator state changed beyond the specified effect.

## Why

- integrity-revisit-earlier-operation: duplicate_effect, unsafe_effect
