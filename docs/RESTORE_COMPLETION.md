# Restore and remaining-flow completion

Decision: **improved** within the synthetic local portfolio scope. Hosting remains **blocked** on
an existing authorized no-cost persistent host/access choice; deployment is NOT_RUN.

The preserved merged baseline is `3856c89`; reviewed runtime is `503e95445fa09849fbfe22a589b23df295618fd3`.
[Acceptance and receipts](../evidence/restore-completion-2026-10-25/README.md) were retained before and
after the changes. Exact remote publication state belongs to the PR and Actions checks.

## Consequential findings and outcomes

| Priority / trigger | Baseline | Result and acceptance |
|---|---|---|
| High: backup predates approval/effect | Provider ledger has one effect but local trace has no dispatch history; original exploratory v2 case fails | Explicit trace-v3 provider observation binds restore generation, snapshot fingerprint, operation/payload and typed receipt; independent ledger comparison passes while `local_authorization_attested` stays false |
| High: browser keeps a cursor across restore | Reused event sequence could be accepted as continuous history | Earlier-generation cursor receives 410; UI refreshes an owned snapshot, clears old rows and resumes current generation |
| High: credential/command/refresh responses arrive out of order | 13 of 16 actual-JavaScript ordering checks fail |16/16 pass; old identity cannot repopulate sensitive view, chain commands or unlock a newer request; retries retain the exact original key/body |
| Medium: rapid repeated operator restore | Two backups in the same second collide and restore fails | Unique generation in preserved-backup filename; fixed-clock regression verifies both intact backups and zero provider mutations |
| Medium: legacy namespaced event or forged recovery evidence | Review exposed legacy data disclosure, transplant acceptance and malformed-container exceptions | Legacy payload remains opaque; identity binding and clean INVALID responses;19 recovery corruptions rejected |

Missing original approval, attempt and consumption records remain missing. No synthetic dispatch
or authorization is added. Unknown/malformed/basic-provider restore remains read-only; partial
verification resumes without duplicate observation or provider effect. The separately named
[trace-v3 contract](../contracts/RESTORE_EVIDENCE.md) does not alter v1/v2 or the frozen original case.
The freshly pinned backend run again records the old exploratory failure, not a relabeled pass.

## Verification scope

- Clean runtime checkout: 363 offline tests (37.090s), 8 native-author checks (7.303s), 107 serving tests
  (144.727s) passed. The preserved untracked historical copies were not deleted to make tests green.
- Full frozen backend-v1 run: 24/24 valid gating records; original exploratory v2 case remains FAIL.
- All 11 backend mutants killed for their named reasons; 11-case unmutated control passes. Final
  core gate: 68/68 normative, zero changed outcomes; exploratory language remains 0/7.
- All 27 core code mutants and 6 evidence mutants rejected with a passing unmutated control;
  the mutation runner verified its working tree stayed unchanged.
- Eight dedicated restore HTTP regressions pass, including partial/malformed/basic-provider status,
  repeated verification/restore, independent SQLite snapshot hash/effect ledger and 19 corruptions.
- Actual serving-client JavaScript: 16/16; existing dashboard races: 11/11. Browser flow: 35 checks plus
  12 ledger/provenance checks; existing dashboard browser: 30 checks. One synthetic effect across
  approval, repeated requests, interruptions, restore and read-only dashboard/download.
- The full browser verifies no external page requests, persistent tokens or JavaScript errors.
  Measured/model-inapplicable usage, estimated costs and unknown confirmed charges remain distinct.
- Original core suite/policy, backend cases/labels, frozen trace-v1/v2 and provider effect transaction
  are unchanged. Current core-gate and mutant receipts are included with final verification.

Commands use existing installed dependencies only: `scripts/ci.py --native`, `scripts/ci_serving.py`,
`node scripts/verify_serving_races.js`, `node scripts/verify_dashboard_races.js`, the existing
`verify_dashboard_ui.py`, and new `verify_serving_ui.py` with explicit installed Playwright/browser
paths. Backend uses `python -m evalgate backend --pinned --backend-git REPO --backend-python PY`;
mutation proof uses disposable copies via `scripts/backend_mutation_proof.py`.

The first full serving failure, fixed-clock failure, first-exposure failures, browser verifier
mistakes and review findings are preserved. Pure structural consistency and local synthetic success
are not independent execution attestations. Review used a scoped peer sharing model/context/filesystem;
it is not an external audit, blind holdout, or exhaustive proof.

## Final reflection and stop boundary

The useful additions address false continuity, misleading missing-history presentation and real
request-ordering failures. No new runtime agents, frameworks or dependencies were added. The new
static schema keeps old contracts frozen; the offline recovery comparator never runs as a provider
oracle inside production code. Snapshot hashing is a linear read performed only during operator
restore; generation checks add bounded metadata lookup per stream. No new dispatch retries or
unbounded loops were introduced. Test runtimes are receipts, not performance improvement claims.

The frontend keeps per-credential and per-request generation guards because cancellation alone
cannot stop already-completed responses. The browser verifier is larger than a narrow unit test
because it measures the actual browser→API→independent effect ledger→restore→dashboard outcome.
Further architecture or speculative features are outside this bounded completion scope.

Lost history cannot be reconstructed from a status receipt. Operations absent from a backup remain
absent. Broader natural-language/model quality, real customer behavior, distributed exactly-once,
public proxy/TLS deployment and production reliability remain unverified. Timings are bounded
process-local provider observations; absent billing receipts remain unknown. The original real-model
hold and evidence provenance remain intact. See [hosting decision](RELEASE_READINESS.md#deployment-discovery-and-decision).
