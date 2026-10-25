# Integrated retail verification, 2026-10-25

Runtime commit: `87970608892a0d130a18f9c6bc31bdfcf0e9e471`.

- [Machine-readable verification](verification.json), with exact commands and log digests.
- [Pinned backend summary](20261025T072223Z-backend-babb43f7/summary.md) and unchanged raw observations/traces/manifest. `python3 -m evalgate backend-validate evidence/integration-2026-10-25/20261025T072223Z-backend-babb43f7` verifies the evidence.
- [Offline tests](offline.log), [serving/crash tests](serving.log), [native-author checks](native.log), [fresh-environment HTTP checks](cleanroom.log).
- [Core/evidence mutants](evaluation-mutants.log), [backend mutants](backend-mutants.log), [headless UI](headless-ui.json).
- [Performance and limits](PERFORMANCE.md), [derived comparison](performance-comparison.json), [fixture dashboard benchmark](dashboard-bench.json).

The logs that contained local output paths are explicitly path-redacted copies; original digests are in verification.json. Full mutant observations and raw performance samples remain in the local recovery/evidence folder and are bound by digests. Original failures were retained there, including the initial sandbox-limited invocation, the transient idempotency failure, and benchmark harness corrections. These are exposed, code-informed synthetic regressions, not an untouched holdout. Fixture prices/receipts are synthetic; serving makes no model calls.

Final read-only source review found no remaining concrete high-signal issue in the frozen runtime. Review roles shared context and filesystem; that is not independent real-world validation. Restore-before-approval still lacks local history and remains exploratory/invalid evidence. See [current scope](../../docs/INTEGRATION.md) and [draft PR #2](https://github.com/alexmunzon/reliable-retail-agent/pull/2) for remote checks; local PASS does not establish required-check enforcement.
