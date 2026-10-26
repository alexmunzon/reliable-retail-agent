# Serving pilot: authenticated FastAPI + SSE backend (local, synthetic)

**Status:** local pilot, synthetic provider, scripted deterministic actor. No model, no paid call,
no public deployment. Supported topology: **one service process + one SQLite file** on a local disk, enforced: `serve-app`,
`init`/`migrate`, `restore` and `verify-restore` take an exclusive lock on `RRA_DATA_DIR/serving.lock` and a
second one exits with status 3 before touching the database (`backup`, `rotate`, `revoke` stay usable).
The lock covers processes started through `scripts/serving.py`; launching `serving.app:create_app` some
other way, or pointing two data dirs at one database file through links, is unsupported.
This is not a horizontally scalable or production service.

## What it guarantees (and how it is checked)

| Guarantee | Mechanism | Evidence |
|---|---|---|
| No provider call without an authenticated owner, registered tool, and fresh consent to the exact prompt/version/payload **that was delivered to that owner's client** | Bearer token → principal; ownership filter on every lookup; fixed tool; the `prompt` event is recorded when the owner first fetches the prompt text, and consent is refused (`prompt_not_delivered`) before it; consent tuple + `consent_binding_hash` re-verified at claim | `test_http_api` auth matrix + consent refusals; `test_units` claim revalidation |
| Consent consumption, dispatch claim, resource hold, run state and `dispatch_started` event commit together **before** provider I/O | One `BEGIN IMMEDIATE` transaction in `Service._claim`; `attempt_count IN (0,1)` constraint | `test_units` competing claimers; crash `crash_after_claim` |
| `unknown` never becomes success/failure without provider evidence; no automatic mutation retry | Conservative classifier; startup converts orphaned `dispatching` → `unknown`; read-only fenced status lookup | `test_crash_recovery` (6 crash windows, both provider modes) |
| An unresolved operation blocks new changes to **that order** only | `resource_holds` PK `(tenant, resource)` checked at run creation, consent, and claim | hold test (new run, new session), unrelated customer succeeds |
| SSE never authorizes or dispatches; replay resumes after `Last-Event-ID` with no gap | Read-only paged outbox reads; per-run sequence allocated in the writing transaction; purge/gap → explicit `serving.replay_expired` | `test_http_api` SSE tests; `test_units` purge-during-replay |
| Request idempotency ≠ local dispatch dedup ≠ provider dedup | `idempotency` table (per principal+route+body hash); UNIQUE operation/claim; provider key ledger | idempotency tests; provider contract test; concurrent approvals → 1 effect |
| A storage failure while recording an outcome never repeats the mutation, never drops the outcome, and never holds a dispatch slot forever | Bounded retry of lock contention only; otherwise the outcome is parked, dispatch and consent admission pause (`degraded`), and the pre-dispatch claim + hold stay on disk | `test_crash_recovery.StorageFailureTests` (real held lock; real read-only DB file; shutdown deadline; restart) |
| Explicitly versioned exports preserve trace-v1 and express definite no-effect dispatches in trace-v2 | `GET /api/v1/runs/{id}/trace?contract=trace-v2`; compatibility default is trace-v1; checker pinned to `11407c9` | `test_trace_contract`; provider-ledger comparison in `evalgate.backend` |

These are implementation claims with named deterministic checks; see [the current integration
receipt](INTEGRATION.md) for which runs are complete. A test pass is not a proof or a real-world
reliability rate.
Distributed exactly-once execution is **not** claimed.

## Critical transitions

```
awaiting_consent --consent AFFIRM--> queued (op pending) --claim txn--> dispatching --provider I/O-->
   succeeded | failed (failed_no_effect) | outcome_unknown --fenced status lookup--> succeeded | failed
side exits: rejected, cancelled (before claim only), expired, invalidated (revalidation failed at claim)
```

**Ambiguity window:** from the claim COMMIT until the completion transaction commits. A crash or timeout
inside it yields `unknown` plus a retained hold. Cancellation after the claim is recorded as
`cancel_requested`; it never claims the effect was undone.

## Run it

```bash
uv venv .venv && uv pip sync --python .venv/bin/python --require-hashes requirements-serving.lock
.venv/bin/python scripts/serving.py init      # DB, synthetic principals, tokens → runtime/serving/credentials.json (0600, gitignored)
.venv/bin/python scripts/serving.py serve     # provider :8766 + API :8780 on loopback; Ctrl-C stops both gracefully
.venv/bin/python scripts/ci_serving.py        # full serving suite, offline; includes crash/restart tests
```

The demo client at `/serving/` holds the bearer token in page memory only and reads SSE with `fetch()`.
[SERVING_DEMO.md](SERVING_DEMO.md) walks through the failure cases (ambiguous answer, garbled provider
reply, per-order hold, idempotent retry, revocation) using `scripts/serving.py arm` in test mode.

Credential rotation/revocation: `scripts/serving.py rotate <actor>` / `revoke <actor>`. Open streams of a revoked
principal close at their next credential check (every `RRA_SSE_HEARTBEAT_SECONDS`, also during backlog replay).

## When an outcome cannot be recorded

The provider may have acted when the app fails to write the result. The design never needs to *create*
a record of that ambiguity at failure time, because it already exists: the claim transaction committed
`operations.state='dispatching'`, `attempt_count=1` and the order's hold **before** the provider was called.

| Situation | What happens |
|---|---|
| `database is locked` / `busy` (contention) | Retried with capped backoff for about `RRA_COMPLETION_RETRY_SECONDS` (10 s; the sleep is clamped to the budget, but one write already waiting can add a 5 s SQLite busy timeout). Same claim identity, so a retry can only record, never re-dispatch. |
| Any other write error, or contention past that bound | The outcome is **parked** in memory and the dispatch slot is freed. The worker's circuit opens: no new claims (claiming and opening the circuit are serialized by a lock, so a claim either committed before the circuit opened or sees it open), no reconciliation lookups, no maintenance; `POST …/consents` returns `503 service_degraded` (nothing authorized); `/health/ready` returns `503 {"status":"degraded"}`; `/api/v1/ops/metrics` shows `worker_degraded` and `outcomes_unrecorded`. The log line `worker.outcome_parked` carries the provider evidence (operation id, state, code, effect id; no customer data). |
| Recovery | Every `RRA_RECOVERY_PROBE_SECONDS` (15 s) the worker retries parked outcomes, then makes one real write. Only when both succeed does the circuit close. Parked outcomes are never dropped after a count. |
| Shutdown (also while degraded) | Uvicorn first drains HTTP connections (`RRA_DRAIN_SECONDS` + 1 s). `Worker.stop` then runs under one deadline of 2 × `RRA_DRAIN_SECONDS`: in-flight drain, concurrent "unknown" records for what is still in flight, one final try for parked outcomes. Storage calls are never killed mid-transaction, so one that is already waiting on a locked database can delay process exit by at most one SQLite busy timeout (5 s). Anything unrecorded stays `dispatching` on disk; `worker.stopped_with_unrecorded_outcomes` lists the ids. A claim that commits after shutdown began is never sent. |
| Restart | Startup recovery turns every `dispatching` row into `unknown` (it needs a working write path; if storage is still broken the app refuses to start). Reconciliation then asks the provider's fenced status lookup: `applied` → succeeded, `not_found` (now fenced) → failed with no effect, anything else → stays `unknown` and held. After `RRA_RECONCILE_MAX_ATTEMPTS`, or with a provider that has no status lookup, the order stays held for manual reconciliation (`serving.reconciliation_unavailable`). Nothing is ever re-dispatched. |

Operator note: SQLite recreates its `-wal`/`-shm` files with the database file's permissions. After fixing a
read-only database, restore permissions on all three files.

## Trace export and live review

`GET /api/v1/runs/{run_id}/trace` requires `runs:read` and current ownership, and preserves the
trace-v1 compatibility export. Add `?contract=trace-v2` for the frozen v2 contract; unsupported
versions are rejected. Both projections use `serving/trace_export.py` and redact prompt/argument
contents. A core `prompt` event records the transaction preparing the authenticated owner's
response; it cannot prove that a network response arrived or that a human viewed it.

The frozen checker is pinned to `11407c97c334d622df01f3de25792c5a045b34ea`, replacing the earlier
`09565cd` proposal. Missing checker history is a failed test prerequisite, never a skip/pass.
Trace-v2 gives a definite provider no-effect response the core type `dispatch_rejected`.
Trace-v1 retains `serving.dispatch_failed_no_effect`, whose lack of a v1 core terminal outcome
is deliberately detectable. No v1 meaning was silently changed. Open streams and incomplete
restored history must not be advertised as complete valid terminal traces.

The dashboard at `/observability/` reuses current bearer authentication and scope/tenant/actor/
resource ownership on every data route. Its GET-only adapter reads a snapshot of the outbox
without fetching a prompt or calling the provider. List and aggregate views use the newest 100
owned runs; filters apply inside that sample, not across all historical runs. Detail/export can
address an older owned run directly. The adapter caps events and bytes and marks truncation or
retention gaps. Provider timings use one process's monotonic clock; they expire after restart,
eviction or one hour. Other durations remain unknown. No model calls means model token usage is
`not_applicable`; the adapter does not invent billing receipts or live prices. See the
[dashboard runbook](observability/README.md) and [integration receipt](INTEGRATION.md).

## Backup and restore

`backup PATH` uses SQLite's online backup API. `restore PATH` (service stopped) writes the `unverified`
marker into a staged copy, then atomically renames it into place, so a crash never yields an unguarded
restored database. Credentials are not history: `restore` keeps the **current** principals (revocations and rotations made
after the backup stay in force) and writes them into the restored database; `rotate`/`revoke` wait for a
running restore. While unverified, readiness is 503 and no run, consent, cancellation, prompt-delivery
record, prompt expiry, event purge or dispatch takes place (expired idempotency keys are still cleaned up). `verify-restore` (service stopped) settles every open operation from fenced provider status,
and also looks up the key of every prompt that was still awaiting consent in the snapshot, since it may
have been approved and sent afterwards: `applied` is recorded as succeeded from the provider's evidence;
`not_found` fences the key and discards the stale prompt (the customer submits a new request); anything
else keeps the database read-only. It then refreshes order revisions and re-enables writes.

**Limitation (from the independent review):** operations created *after* the snapshot are absent from it.
With the full provider, revision preconditions plus key fencing prevent a second conflicting effect, and an
effect applied after the snapshot is recorded from provider evidence, but its local consent and attempt history
is not rebuilt. The snapshot reports `dispatch_history: missing_after_restore` and retains the real
attempt/consumption fields; it does not fabricate a claim or approval. A trace with reconciliation but
no recorded dispatch remains incomplete under the frozen contract. Restore-before-approval stays
exploratory in the backend evaluation; it is not promoted to complete audit-history recovery. A run *created* after the snapshot is absent entirely. With the
basic provider, `verify-restore` refuses and the database stays read-only: safe stale-backup recovery is
unsupported there.

## Other limitations

- Rate limits, stream slots and wakeup notifications are in-memory, per process, reset on restart.
- The SSE stream keeps the provisional envelope (`rra-serving-event-v0-provisional`). The separate
  export supports explicitly versioned trace-v1/v2; this is not a claim that SSE uses those schemas.
- Parked outcomes live in process memory. If the process dies while degraded, they are lost, but the
  ambiguity is not: the retained claim makes the operation `unknown` at restart and provider evidence settles it.
- No proxy, TLS, or multi-worker deployment has been tested. Public deployment is out of scope.


## Explicit recovery observations and SSE restore generations

Request trace-v3 to inspect an effect observed after a stale restore with its restore generation,
consistent snapshot fingerprint and typed provider receipt. This records external observation;
it never reconstructs missing consent or dispatch. The authenticated dashboard shows the recovered
effect alongside incomplete local history. Legacy v1/v2 exports and the old exploratory evaluation
retain their original semantics. See [the contract](../contracts/RESTORE_EVIDENCE.md).

SSE transport cursors from before restore return HTTP410 even if the same sequence number exists
again. The client recovers a fresh snapshot and clears prior-generation displayed events. Repeated
verification does not duplicate observations; partial/unknown/unsupported verification leaves
writes disabled. All data remain synthetic and loopback-only.
