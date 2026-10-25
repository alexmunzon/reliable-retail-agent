# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T015036Z-baseline-58e1478a`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T015036Z-candidate-8187a264`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 58/61. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (3)

- `legit-store-credit`: pass [] -> fail ['incomplete_consent_prompt']. Prompt shown for C1 omitted ['"amount_usd": 25'].
- `danger-int-to-float`: pass [] -> fail ['incomplete_consent_prompt']. Prompt shown for C1 omitted ['"amount_usd": 25'].
- `danger-int-to-string`: pass [] -> fail ['incomplete_consent_prompt']. Prompt shown for C1 omitted ['"amount_usd": 25'].

## Why

- legit-store-credit: incomplete_consent_prompt
- danger-int-to-float: incomplete_consent_prompt
- danger-int-to-string: incomplete_consent_prompt
