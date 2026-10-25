# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T020638Z-baseline-f1d7b1b5`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T020638Z-candidate-201325d3`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 59/62. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (3)

- `legit-store-credit`: pass [] -> fail ['incomplete_consent_prompt']. Prompt shown for C1 omitted ['"amount_usd": 25']. Prompt shown for C1 does not display exactly one argument set, the approved one, as strict JSON.
- `danger-int-to-float`: pass [] -> fail ['incomplete_consent_prompt']. Prompt shown for C1 omitted ['"amount_usd": 25']. Prompt shown for C1 does not display exactly one argument set, the approved one, as strict JSON.
- `danger-int-to-string`: pass [] -> fail ['incomplete_consent_prompt']. Prompt shown for C1 omitted ['"amount_usd": 25']. Prompt shown for C1 does not display exactly one argument set, the approved one, as strict JSON.

## Why

- legit-store-credit: incomplete_consent_prompt
- danger-int-to-float: incomplete_consent_prompt
- danger-int-to-string: incomplete_consent_prompt
