# Final-reflection and hosted-CI follow-up

The serving runtime remains pinned to `87970608892a0d130a18f9c6bc31bdfcf0e9e471`. This follow-up changes evaluation only.

[Verification receipt](verification.json) · [full pinned result](20261025T074658Z-backend-c6b27bb4/summary.md) · [342 offline tests](offline-final.txt) · [B11 first exposure](b11-first-exposure.txt) · [B11 mutant](b11-mutant.txt).

## Exact degraded refusal

The final claim audit planted HTTP202 and HTTP500 with the otherwise correct `service_degraded` code. Both incorrectly passed under r4. The recorded regression failed before the label fix. The reviewed r5 label requires exactly HTTP503 plus that code; cases, deadlines, state/effect expectations and the runtime were unchanged. Ablating `check_interface` allows these planted violations through again. These exposed cases remain regression evidence.

## Hosted fault-injection failure

[Run 40173068583, job 121107880423](https://github.com/alexmunzon/reliable-retail-agent/actions/runs/40173068583/job/121107880423) on `abc6760` passed all 91 serving tests, then returned exit 2 INVALID: the persistence-failure-then-crash case never reached degraded at step 7, leaving 23/24 valid gating records. Its provider applied one effect and the run finished succeeded; this does not demonstrate that the intended storage-failure phase occurred. No unsafe duplicate effect was reported. The full failed job log is retained locally and its digest is recorded. Raw backend observation files were not uploaded by that workflow and are unavailable from the completed runner.

The old injector changed only file permissions. Those permissions cannot reliably revoke pre-existing writable descriptors; this is a plausible explanation, not a proven reconstruction of the Linux race. Harness v2 additionally acquires a real SQLite `BEGIN IMMEDIATE` write lock before releasing the provider reply, retains it across the app crash, and releases it only on repair/cleanup. It exercises exhausted contention as well as retaining read-only permissions. The separate permanent-read-only serving tests remain intact. The existing 15-second readiness deadline and 1-second completion retry budget were not raised or disabled.

A real SQLite regression verifies that already-open and fresh writers cannot INSERT, readers still work, and explicit repair restores writing. Read-only review found a cleanup edge; outer finally blocks now release the blocker even on chmod/process cleanup errors, which remain errors. Two fault tests cover those paths.

The final local full pinned run passes 24/24 gating cases; the original exploratory stale-restore history failure remains visible. B11 remains killed for its named refusal violation, and its 11-case unmutated control passes. The earlier full 11-mutant result is preserved with its original r4/harness-v1 provenance; unaffected mutants were not needlessly rerun. Source review confirmed the scoped fixes without claiming an independent held-out study.

```bash
python3 -m evalgate backend-validate evidence/integration-2026-10-25-followup/20261025T074658Z-backend-c6b27bb4
```

Earlier r4 raw evidence remains unchanged in the [original receipt](../integration-2026-10-25/README.md); validate it using its original `8797060` harness/policy, not the stricter current version. Exact latest hosted status is on [draft PR #2](https://github.com/alexmunzon/reliable-retail-agent/pull/2). Local verification does not establish merge enforcement: backend evaluation is still not a required check, and changing repository rules is outside authorization.
