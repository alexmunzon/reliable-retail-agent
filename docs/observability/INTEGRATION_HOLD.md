# Integration hold report: live serving is NOT INTEGRATED

**Date:** 2026-10-25. **Branch:** `claude/trace-dashboard` at base `86caf2e`.

## Why integration is held

The spec allows integration only with a verified owner version, commit or test receipt **and** an explicit scoped integration path. Neither exists:

- The backend owner's tree (`retail-durable-backend`, branch `claude/durable-backend`) is still at `8e70c0f`. Its `serving/`, `provider_sim/`, `tests_serving/`, `scripts/serving.py` and `web/serving/` exist only as **uncommitted** files (read with `git status` only, 2026-10-25). No serving code was read, copied or depended on.
- The active backend-adapter worktree (`.claude/worktrees/backend-adapter-6b82f6`) was not opened or touched.
- The evaluation owner's `evalgate/` and `contracts/` were used read-only, unchanged.

The dashboard therefore reads **only** the frozen synthetic fixture set (`data_origin: synthetic_fixture`). `Dataset` refuses any other origin.

## What a verified handoff would need to provide

1. A commit or tag, plus a test receipt, for serving's trace exporter. Its output must pass `python3 -m evalgate trace-check` and keep `serving.run_accepted` first and `serving.run_finished` last.
2. An exporter that writes **obs-sidecar-v1** records next to each trace artifact (see [DATA_DICTIONARY.md](DATA_DICTIONARY.md)) and a `cases.json`-style manifest with SHA-256 digests. The ingestion side then needs no new code. It is the adapter.
3. Decisions the owner must make. These are questions, not assumptions:
   - the real event names for cancellation and restart (fixtures use `serving.run_cancelled`, `serving.process_restarted`);
   - which process-incarnation id serves as `clock_domain`;
   - whether the provider returns a request id for usage identity;
   - where the principal/tenant ownership table lives.
4. An authenticated route plan. Every trace, export or replay route must reuse serving's existing principal/tenant authorization. The fixture `access.json` and the server's `--principal` are a stand-in, not authentication. A run id is never authorization.

## Proposed thin boundary (review artifact, NOT applied, NOT checked against uncommitted serving code)

The emit point is wherever serving already writes the trace-v1 line for an outbox transition. In the same, existing durable write path, it appends the matching sidecar span to a separate, optional file:

```text
# conceptual, for the backend owner's review only
on outbox transition (claim / dispatch start / outcome / reconcile):
    write trace-v1 event                      # existing, authoritative, unchanged
    try:
        sidecar.append(span_record(...))      # optional metrics; bounded queue; drop_report on overflow
    except Exception:
        count_drop("emitter_error")           # never raise into the dispatch worker
```

Invariants the owner should keep:

- The transactional outbox and the trace stay authoritative. Sidecar loss or failure never blocks, retries, duplicates or alters a dispatch. A missing **audit ledger** is not optional; missing **metrics** are.
- The sidecar holds monotonic ns from the emitting process and its incarnation id. It never holds prompt text, arguments, addresses or free-form error text.
- Exported files are immutable once their digest is written.

## Tests that cover the adapter contract today (fixture-backed)

- `OracleAgreement.test_contract_examples_are_artifact_scoped_requests` loads the real contract examples through the same loader a serving export would use.
- `TelemetryFailureIsolation` covers deleted, corrupted and truncated sidecars, and shows they change no business outcome.
- `ManifestAndPaths` covers digest binding, traversal, symlinks and the origin allowlist.

**Claim boundary:** no live serving traffic, hosted CI run of these tests, real billed spend or deployment is claimed.
