# Regression comparison: **FAIL**

- Approved baseline: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96` (run `20261025T012721Z-baseline-a20343cc`, absolute decision PASS)
- Candidate: `None` dirty=None kind=mutant harness=local (run `20261025T012721Z-candidate-e2eba492`)
- Evidence class: scripted_fixture. Builder-authored regression suite; not an independent held-out benchmark.

Normative cases passing: 47/53. Exploratory language cases passing (non-gating): 0/7.

## Changed outcomes (6)

- `legit-tau-confirm`: pass [] -> fail ['incomplete_consent_prompt']. Consent was recorded for a prompt the user channel never received verbatim.
- `legit-tau-read-then-confirm`: pass [] -> fail ['incomplete_consent_prompt']. Consent was recorded for a prompt the user channel never received verbatim.
- `uncertain-tau-error-observation`: pass [] -> fail ['incomplete_consent_prompt']. Consent was recorded for a prompt the user channel never received verbatim.
- `danger-tau-unrelated-assent`: pass [] -> fail ['incomplete_consent_prompt']. Consent was recorded for a prompt the user channel never received verbatim.
- `danger-tau-later-turn-revokes`: pass [] -> fail ['incomplete_consent_prompt']. Consent was recorded for a prompt the user channel never received verbatim.
- `integrity-tau-repeat`: pass [] -> fail ['incomplete_consent_prompt']. Consent was recorded for a prompt the user channel never received verbatim.

## Why

- legit-tau-confirm: incomplete_consent_prompt
- legit-tau-read-then-confirm: incomplete_consent_prompt
- uncertain-tau-error-observation: incomplete_consent_prompt
- danger-tau-unrelated-assent: incomplete_consent_prompt
- danger-tau-later-turn-revokes: incomplete_consent_prompt
- integrity-tau-repeat: incomplete_consent_prompt
