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
| Exported traces are accepted by the evaluation owner's committed trace-v1 checker and agree with the provider ledger | `GET /api/v1/runs/{id}/trace` via `serving/trace_export.py` (pinned to 09565cd) | `test_trace_contract` (10 HTTP scenarios: success, duplicates, stale/ambiguous/rejected, cancel, unknown + hold, crash, definite failure, restore mid-dispatch, expiry, exhausted reconciliation) |

These are **observed results of a deterministic suite**, not proofs and not real-world reliability rates.
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
.venv/bin/python scripts/ci_serving.py        # full serving suite, offline (~12 min)
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

## Trace export (trace-v1)

`GET /api/v1/runs/{run_id}/trace` (scope `runs:read`, owner only) returns the run as trace-v1 JSON Lines,
mapped by `serving/trace_export.py` from the provisional outbox envelope (which the SSE stream still uses).
It exports hashes and reason codes only. A core `prompt` event means the exact text was delivered to the
authenticated owner's client (recorded in the same transaction that puts it into a committed response
to the owner; the server cannot know whether that response arrived or was displayed). Creating a run only generates the prompt. Every terminal run ends with exactly one `serving.run_finished`,
written in the same transaction as the final state, so a cut-off trace is detectable.

**Status, honestly:** the contract belongs to the evaluation owner. CI pins the *committed* handoff
(evaluation-gate commit `09565cd`, schema titled "PROPOSAL, not frozen") and runs that commit's own
`validate_trace` (envelope, enums, sequence). Ordering semantics are not in that commit. A non-gating
preview against the owner's *uncommitted* frozen draft accepted success, rejection and unknown→reconciled
traces. One known gap remains: trace-v1 has no core outcome for a **definite provider no-effect failure**;
it is exported as `serving.dispatch_failed_no_effect`, which the draft's ordering rules report as an
attempt without an outcome. That needs the owner's decision; the backend will not relabel it as uncertain.
Without the pinned commit in the clone, the contract tests skip and compatibility is **not** claimed.

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
is not rebuilt (the trace shows `serving.reconciled` with no `dispatch_started`; how trace-v1 should express
that is the evaluation owner's decision). A run *created* after the snapshot is absent entirely. With the
basic provider, `verify-restore` refuses and the database stays read-only: safe stale-backup recovery is
unsupported there.

## Other limitations

- Rate limits, stream slots and wakeup notifications are in-memory, per process, reset on restart.
- The SSE stream keeps the provisional envelope (`rra-serving-event-v0-provisional`); trace-v1 is the export
  format. Re-pin `TRACE_V1_COMMIT` when the owner commits the frozen contract.
- Parked outcomes live in process memory. If the process dies while degraded, they are lost, but the
  ambiguity is not: the retained claim makes the operation `unknown` at restart and provider evidence settles it.
- No proxy, TLS, or multi-worker deployment has been tested. Public deployment is out of scope.
