# Remaining-work completion evidence

Runtime: `503e95445fa09849fbfe22a589b23df295618fd3`; preceding merged baseline: `3856c89`.
The acceptance criteria were saved before implementation in [ACCEPTANCE.md](ACCEPTANCE.md).
[Completion report](../../docs/RESTORE_COMPLETION.md) explains the result and limits.

These are exposed synthetic regressions. No untouched holdout, model quality, recovered consent,
production reliability or hosted deployment is claimed. Runtime observation and independent provider
ledger are distinct; the browser's recovery comparison explicitly does not attest original consent.

- First exposure: explicit v3 HTTP422, accepted old-generation cursor, and misleading dashboard outcome.
- Client ordering baseline: 13/16 checks failed; the final actual-JavaScript suite passes16/16.
- Full serving first run: one genuine same-second backup-name collision among 106 tests.
- Fixed-clock reproduction fails before the unique filename; all 8 restore tests pass afterward.
- Final clean checkout: 363 offline, 8 native, 107 serving tests pass; 19 recovery corruptions rejected.
- Browser: 35 checks plus 12 Python ledger/provenance checks pass; existing dashboard 30 checks pass.
- Original backend-v1 exploratory v2 restore case still fails. New v3 evidence does not rewrite it.
- The first two browser receipts preserve verifier defects: an unclosed SQLite connection, then
  an invalid rowid assumption. They are not described as production failures.
- The expanded restore first run likewise preserves test-harness defects, corrected before final checks.
- Scoped review found legacy-version disclosure, transplanted stream acceptance and malformed-container
  exceptions; these were fixed. Review shared model/context and is not an external audit.

Public log copies redact local checkout/home paths only; original/published hashes are in
[publication-manifest.json](publication-manifest.json). Original local receipts are preserved.
The backend run directory is byte-for-byte copied and can be checked with `evalgate backend-validate`.
The historical v1/v2 schemas, backend suite/policy and original provider effect transaction are unchanged.

Core gate: 68/68 normative, 0 changed outcomes; comparison is public. Original raw core run files
contain local paths and remain preserved locally without rewriting their integrity manifests. Their
hashes are retained in publication-manifest.json. Backend receipts remain complete and unaltered.

Final mutation controls: 11/11 backend mutants killed for named reasons with 11-case passing control;
27 core code plus 6 evidence mutants rejected with passing control. Full receipt/patch files are included.
