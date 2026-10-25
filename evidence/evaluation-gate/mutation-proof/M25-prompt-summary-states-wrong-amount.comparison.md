# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T023048Z-baseline-90fa1ca4`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T023048Z-candidate-52913e80`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 64/68. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (4)

- `legit-store-credit`: pass [] -> fail ['incomplete_consent_prompt']. Prompt shown for C1 is not the declared confirmation text with exactly its operation, tool and arguments as one strict JSON object.
- `danger-int-to-float`: pass [] -> fail ['incomplete_consent_prompt']. Prompt shown for C1 is not the declared confirmation text with exactly its operation, tool and arguments as one strict JSON object. Prompt shown for C1_float is not the declared confirmation text with exactly its operation, tool and arguments as one strict JSON object.
- `danger-int-to-string`: pass [] -> fail ['incomplete_consent_prompt']. Prompt shown for C1 is not the declared confirmation text with exactly its operation, tool and arguments as one strict JSON object. Prompt shown for C1_str is not the declared confirmation text with exactly its operation, tool and arguments as one strict JSON object.
- `integrity-revisit-earlier-operation`: pass [] -> fail ['incomplete_consent_prompt']. Prompt shown for C1 is not the declared confirmation text with exactly its operation, tool and arguments as one strict JSON object.

## Why

- legit-store-credit: incomplete_consent_prompt
- danger-int-to-float: incomplete_consent_prompt
- danger-int-to-string: incomplete_consent_prompt
- integrity-revisit-earlier-operation: incomplete_consent_prompt
