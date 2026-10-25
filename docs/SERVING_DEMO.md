# Serving pilot demo: a failure-centered walkthrough

The happy path takes ten seconds and proves little. This walkthrough spends its time on the
moments where a naive backend would double-charge, double-ship, or act on stale consent, and
shows what the pilot does instead. Everything is local and synthetic: no model, no paid call.

## Setup (test mode, so the synthetic provider accepts injected failures)

```bash
export RRA_DATA_DIR="$PWD/runtime/serving-demo/app" RRA_TEST_MODE=1 RRA_RECONCILE_BACKOFF_SECONDS=5
.venv/bin/python scripts/serving.py init
.venv/bin/python scripts/serving.py serve
```

Open <http://127.0.0.1:8780/serving/> and paste `alice`'s token from
`runtime/serving-demo/app/credentials.json`. The page keeps it only in memory (not in browser storage,
cookies or the URL), so reloading the page forgets it. The browser's built-in `EventSource` cannot
send an `Authorization` header, so the page reads the event stream with `fetch()` instead.

Provider failures are queued from a second terminal with the same three environment variables:
`.venv/bin/python scripts/serving.py arm ACTION...`. `arm` refuses unless `RRA_TEST_MODE=1` is set, and
the provider refuses unless it was started in test mode. `serve` starts the app only after its own
provider is listening, so it never silently talks to some other provider left on that port.

The longer reconciliation backoff (5 s, doubling) is only there to give you time to look at the
intermediate states in steps 2 and 3; with the default (1 s) they pass in a few seconds.

## 1. An unclear answer is not consent

Create a run for `SYN-ORDER-1042`. The page shows the gate's exact prompt, its `prompt_id`,
version and `payload_hash`. Answer `maybe later?`.

- Expected: `HTTP 200 ambiguous`; event `blocked · Ambiguous response blocked · AMBIGUOUS`; run
  still `awaiting_consent`; nothing reaches the provider.

## 2. The provider applies the change but its reply is garbled

```bash
.venv/bin/python scripts/serving.py arm malformed_after_commit status_unavailable status_unavailable
```

Answer `yes`, then press **Drop connection** at once.

- `authorized` → `dispatch_started` → `dispatch_uncertain`. The app never guesses: the run is
  `outcome_unknown`, the snapshot shows `resource_hold.held: true`, and the mutation is not retried
  (only read-only status lookups are).
- The worker then asks the provider for the status of that exact idempotency key. The first two
  lookups fail (`status_unavailable`), so it waits with backoff and tries again.
- Press **Resume from Last-Event-ID**. The stream continues after the last event the page saw, with
  no gap and no duplicate, ending at `serving.reconciled · Outcome established from provider evidence`
  and run `succeeded`, with `operation.reconciliation.attempts: 3` in the snapshot (about 15 s with
  the backoff above; reconciliation keeps running while the page is disconnected).
- Ground truth: the provider's own ledger (`runtime/serving-demo/provider/provider.db`, table
  `effects`) holds exactly **one** row whose `idempotency_key` is `rra-` + that `operation_id`.

## 3. An unresolved change blocks only that order

Switch to `carol` (order `SYN-ORDER-1043`), arm a longer status outage, and approve:

```bash
.venv/bin/python scripts/serving.py arm malformed_after_commit status_unavailable status_unavailable status_unavailable status_unavailable
```

Have the second address typed in beforehand. While the run is `outcome_unknown` (about 75 s with
the backoff above), submit it for the same order.

- Expected: `HTTP 409 resource_on_hold`. The page stays on the first run (it switches only after
  the server accepts a new one). Other orders and customers are unaffected. Once reconciliation
  settles the first change, the hold is released.

## 4. Retrying a command does not act twice

Press **Retry last command** after a command the server accepted. It resends the same body with the
same `Idempotency-Key`, and the server returns the stored answer instead of acting again. The
"Create session and run" request is retried as a whole with both of its original keys.

Two limits, both by design. A command the server *refused before recording it* (`409
resource_on_hold`, `429`, `503`) was never executed, so retrying it later may execute it for the first
time: the key guarantees at most one execution, not a replay of the refusal. And stored answers are
kept for `RRA_IDEMPOTENCY_TTL_SECONDS` (24 h by default); after that the same key is new again. The page
only offers Retry for the run on screen, and allows one command in flight at a time.

## 5. A revoked credential closes the live stream

With a stream open as `bob`, run `.venv/bin/python scripts/serving.py revoke bob`.

- Expected: the open stream ends at its next credential check, which is due at most one heartbeat
  (`RRA_SSE_HEARTBEAT_SECONDS`, 15 s by default) after the previous one, plus query time. The page's
  following snapshot request gets `401` and the page drops the token ("Token rejected").
  `.venv/bin/python scripts/serving.py rotate bob` issues a new one.

## What this demo does not show

Crash windows inside the app process (after claim, after the provider answered, during restore) are
exercised by `tests_serving/test_crash_recovery.py`, not by this page. Results here are observations
of a deterministic local pilot, not production reliability rates.
