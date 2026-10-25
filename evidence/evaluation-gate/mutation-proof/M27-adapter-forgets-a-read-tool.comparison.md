# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T021008Z-baseline-fd3cc01a`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T021008Z-candidate-a96672d9`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 67/68. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (1)

- `read-user-details-tau`: pass [] -> fail ['interface_error', 'missed_legitimate_execution']. A scheduled read did not reach the provider or did not return the current record. Unexpected exceptions from the SUT: step 0 ValueError

## Why

- read-user-details-tau: interface_error, missed_legitimate_execution
