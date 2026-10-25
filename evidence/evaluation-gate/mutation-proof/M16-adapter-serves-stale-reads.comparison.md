# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T023033Z-baseline-0fdcb940`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T023034Z-candidate-dbd8fa3f`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 67/68. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (1)

- `read-after-mutation-tau`: pass [] -> fail ['missed_legitimate_execution']. A scheduled read did not reach the provider or did not return the current record.

## Why

- read-after-mutation-tau: missed_legitimate_execution
