# trace-v1 handoff packet (for the backend owner)

**Status:** contract frozen 2026-10-24. The evaluation side is built and tested. **The backend effect adapter is built** (`python3 -m evalgate backend`). It has been run locally against an uncommitted copy of the backend's working tree, not in hosted CI. One contract gap is open: a definite no-effect provider failure has no core outcome (see `docs/EVALUATION_GATE.md`, "Backend adapter").

## What's in the packet

| Item | Where |
|---|---|
| Event envelope (JSON Schema 2020-12) | `contracts/trace-v1.schema.json` |
| Evaluation records, manifests, comparisons | `contracts/evaluation-v1.schema.json` |
| Valid sample traces | `contracts/examples/serving-success.jsonl`, `serving-illustrative.jsonl` (unknown outcome, then reconciled), `offline-gate-trace.jsonl` (real mapped gate events) |
| Invalid sample traces, each with its expected problem | `contracts/examples/invalid/*.jsonl` and `invalid/expected.json` |
| Validation entry point (CLI) | `python3 -m evalgate trace-check TRACE.jsonl` |
| Validation entry point (Python) | `evalgate.trace.check_file(path)` returns `(problems, events)`; `evalgate.trace.validate_trace(events)` returns `problems` |
| Ordering rules (normative) | `evalgate.trace.check_semantics` and the "Ordering rules" section below |

Python 3.11+, standard library only. No install, network or key is needed.

## File format

UTF-8 JSON Lines: one JSON object per line, lines ending in LF (CRLF is accepted), written in `sequence` order. Duplicate keys anywhere in a line make the file INVALID. A file may hold several streams; a stream is one `(run_id, trace_id)` pair. Serving uses one trace per run, numbers its events `sequence` 1..N **without gaps**, and **ends with exactly one `serving.run_finished`** event, so a dropped line or a cut-off tail is detectable. Write `sequence` as a plain integer (`3`, never `3.0`); the checker is stricter than JSON Schema here.

## Event meanings

| `event_type` | Emit when | Required beyond the envelope (serving) |
|---|---|---|
| `prompt` | the exact confirmation was **displayed** to the authenticated customer (not merely generated) | `operation_id`, `prompt_id`, `payload_hash`, `occurred_at`; payload `prompt_sha256` of the exact shown text |
| `authorized` | the customer's reply to that prompt was accepted as approval | same `operation_id`, `prompt_id`, `payload_hash` as the prompt; payload `classifier_version` |
| `rejected` / `invalidated` / `blocked` | refusal, a payload or policy change, or a block | `operation_id` on serving `rejected`/`invalidated`; `payload_hash` (optional on `blocked`); a reason enum where the schema requires one. A block naming neither operation nor payload revokes **every** pending approval in the stream. |
| `dispatch_started` | the attempt is durably recorded, **before** the tool is called | `operation_id`, `payload_hash`, payload `tool_name` |
| `dispatch_succeeded` | the provider confirmed the effect | same keys as the start |
| `dispatch_uncertain` | the outcome is unknown (timeout, lost response; the ledger state `unknown`) | same keys; optional `error_class`. Never report unknown as success. |
| `serving.reconciled` | an unknown outcome was later resolved from evidence outside the agent (provider ledger or operator check) | `operation_id`, `payload_hash`; payload `outcome` ∈ `effect_confirmed`, `no_effect_confirmed`, `still_unknown`; optional `by` |
| `serving.run_accepted` / `serving.run_finished` | the run starts / the run's evidence is complete | `serving.run_finished` must be the last event of a serving stream |
| other `serving.*` / `transport.*` | anything else (reconnects, diagnostics) | not business events; a reconnect is never a mutation; payload is free-form but must hold no secrets |

`payload_hash` = `MutationIntent.fingerprint`: SHA-256 of strict canonical JSON `{"tool_name", "arguments"}` (sorted keys, no spaces, UTF-8, no NaN or Infinity, and `1` is not `1.0`). It is never consent by itself. `consent_binding_hash` is reserved for the milestone-2 consent envelope.

**No secrets or personal data:** traces carry hashes and identifiers only, never prompt text, tool arguments, emails, addresses, tokens or headers. Every core payload has a closed field list, so an extra field such as `prompt_text` is rejected (see `invalid/leaks-prompt-text.jsonl`).

## Ordering rules (normative, checked per stream)

Events are keyed by `operation_id`. Legacy offline events have none, so they're keyed by `payload_hash`.

1. An attempt (`dispatch_started`) needs a fresh `authorized` for the **same payload_hash**, after any earlier attempt, prompt, refusal, invalidation or block for that operation.
2. The `authorized` event must answer the operation's **current** prompt: same `prompt_id` and payload. A newer prompt replaces the old one. A refusal, an invalidation or an attempt retires it. A block revokes the approval but leaves the prompt answerable, as the gate does after an ambiguous reply.
3. Refusals, invalidations and blocks revoke by `operation_id`. Without one, they revoke by `payload_hash`. With neither, they revoke every pending approval.
4. An operation gets **one** attempt and **one** tool: every `tool_name` reported for it must agree. A retry must reuse the same `operation_id`, which is then rejected; it must never mint a new one.
5. Every attempt has exactly one outcome (`dispatch_succeeded` or `dispatch_uncertain`). An attempt without an outcome means the evidence is missing or truncated.
6. No new attempt may start while an earlier outcome is unknown and unreconciled.
7. `serving.reconciled` may only resolve an outcome that was `dispatch_uncertain` for the **same operation and payload_hash**. `still_unknown` leaves it unresolved.
8. Serving streams: `sequence` 1..N without gaps, with exactly one `serving.run_finished`, and it comes last.

## What a result means

- `trace-check` prints **CONSISTENT** (exit 0) or **INVALID** (exit 2). It **never reports PASS**: a trace is the system's own account of itself. **Treat every nonzero exit as a failure**, not just 2.
- Detected as INVALID: an empty, unreadable or non-UTF-8 file; a line that is cut off, isn't an object, or has duplicate keys; a dropped middle line or a missing tail (serving streams); an attempt without an outcome; and any contradiction with the rules above. The Python entry points never raise on malformed input.
- **Not detectable from a trace alone:** a stream that was faithfully written but stopped early, *and* still ends with `serving.run_finished`, looks complete. So does a backend that leaves out events it should have written. That is why the trace is never a PASS.
- A PASS or FAIL about **effects** comes only from independent effect evidence: `python3 -m evalgate check` (the offline gate, where the scorer reads a simulator the system under test cannot speak for) and `python3 -m evalgate backend` (the durable backend, where the scorer reads the evaluation provider's ledger).

## What the backend must do to be evaluated

1. Emit trace-v1 JSON Lines exactly as above, including `serving.run_accepted` first and `serving.run_finished` last in every run. Run `python3 -m evalgate trace-check` on real service output in the backend's own CI, and treat any nonzero exit as a failure.
2. Map ledger states one-to-one: `started` → `dispatch_started` (written before the call), `succeeded` → `dispatch_succeeded`, `unknown` → `dispatch_uncertain`, operator or provider resolution → `serving.reconciled`.
3. Emit `prompt` only for a confirmation the customer actually saw, with `payload.prompt_sha256` set to the hash of the exact shown text (required).
4. For PASS/FAIL on effects, run the opt-in backend adapter: `python3 -m evalgate backend --backend PATH [--backend-python PY]`. It drives the service's real HTTP API through the `backend-v1` suite and points the service's provider URL at an independent evaluation provider (`evalgate/backend_provider.py`), whose ledger the scorer reads. It was built in a separate module, not in `evalgate/driver.py`, so `check` stays standard-library only. It relies on these backend entry points, which the backend owner agreed to keep stable: `scripts/serving.py init` / `serve-app`, the `RRA_*` settings including the test-only failpoints, `credentials.json` and `provider.key` in `RRA_DATA_DIR`, the seeded principals and orders, the v1 HTTP routes, and the provider wire contract. If the backend is absent, the result is NOT_RUN (exit 3), never PASS. Results and limits are in `docs/EVALUATION_GATE.md`, section "Backend adapter".
5. Keep the trace-v1 contract frozen: any added, removed or re-meant field or enum value is trace-v2. Re-run `python3 scripts/ci.py`, which fails if the schema enums drift from the checker.

## Compatibility limits

- Ordering is checked **within one stream**. A duplicate attempt split across two runs or two trace files (the same `operation_id` again after a crash and restart) isn't detected by `trace-check`. A durable backend must keep an operation's whole lifetime in one stream, or this needs a cross-stream check in trace-v2.
- Legacy offline gate events (`rra-gate-legacy-map-v1`) have no `operation_id`, `prompt_id` or `occurred_at`, and encode blocked reasons in titles. Serving events must not copy that.
- `trace-check` validates structure and order, not business correctness, authentication, or whether a hash matches real arguments it never sees.
- Concurrency (two writers in one stream) is out of scope. `sequence` must be assigned by one writer per stream.
