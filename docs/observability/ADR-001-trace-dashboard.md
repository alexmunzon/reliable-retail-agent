# ADR-001: Read-only trace dashboard over frozen trace-v1 plus a linked sidecar

**Status:** accepted for the synthetic build, 2026-10-25. Live serving integration is **held** (see [INTEGRATION_HOLD.md](INTEGRATION_HOLD.md)).

## Context

A reviewer must be able to open one request and see what was proposed, authorized, attempted and actually confirmed; what stayed unknown; how it was reconciled, retried or cancelled; where time went; and which cost numbers are known. The evidence contract `contracts/trace-v1.schema.json` is frozen and owned by the evaluation coordinator. It has no duration, token, price or charge fields. Its only time is the wall-clock `occurred_at`. Any new field would be trace-v2.

## Decision

1. **trace-v1 is consumed unchanged.** The frozen checker `evalgate.trace.validate_trace` runs on each stream exactly as delivered. Its verdict is kept and shown, and never upgraded.
2. **Timing and cost live in `obs-sidecar-v1`**, a separately versioned JSON Lines format owned here (`observability/sidecar.py`). It has closed field lists and four record kinds: `span`, `usage`, `billing_receipt` and `drop_report`. A sidecar references trace streams, events and operations. It never re-means a trace field. The trace and its sidecar are bound as a pair by SHA-256 digests in `cases.json`. A request's identity is *artifact + run_id + trace_id*, because the contract examples reuse every id across files.
3. **Business facts come only from trace events**, folded in sequence order. Span status, `serving.run_finished`, billing data and missing events never create or erase an effect. `authorized` is not execution. `dispatch_started` is a durably recorded attempt. Only `dispatch_succeeded`, or a matching `serving.reconciled effect_confirmed`, is a confirmed effect. The trace is still the system's own account of itself. Independent effect evidence stays with `evalgate`.
4. **Durations use one process incarnation's monotonic clock.** A span whose end was measured in another `clock_domain` has an unknown duration. A request with no measured span gets at most a labelled lower bound (its longest nested span), never a sum and never a wall-clock difference.
5. **Costs are three series that never merge:** measured usage, estimated charge (tokens × a dated, versioned price table) and confirmed charge (receipts only). Unknown stays `null`, never `$0`.
6. **Runtime is stdlib-only, offline and loopback-only.** It has no LLM, no agent graph, no telemetry vendor and no write routes. The viewer principal is fixed in server configuration.

## Alternatives rejected

- *Extend trace-v1 with timing/cost fields*: forbidden by the freeze, and it would silently re-mean the contract.
- *Sort and dedupe streams, then call them valid*: this hides INVALID evidence. We keep a labelled diagnostic view instead (Astra design finding 2).
- *Hosted telemetry (OpenTelemetry collector, vendor UI)*: no requirement needs one, and it adds a network boundary, credentials and cost.
- *Charts library / framework UI*: the views are tables and bars, and the DOM API plus `textContent` keeps every value escaped.

## Consequences

- A request can be **business-complete and telemetry-poor**. Damaged or missing sidecars never change status or operation outcomes. `tests/test_observability_security.py::TelemetryFailureIsolation` proves this on deleted, corrupted and truncated sidecars.
- One cross-stream check is added that trace-check cannot do. The same operation attempted in two runs of one owner is flagged `repeated_attempt_across_runs` (`contracts/HANDOFF.md` compatibility limit).
- `serving.run_cancelled` and `serving.process_restarted` are **fixture-defined namespaced events**, not trace-v1 core types. Cancellation phase is derived from event order (whether an attempt precedes the cancel), never from a payload claim. The real event names are an open question for the backend owner.

## Module inventory and justification

| Module | Why it exists |
|---|---|
| `observability/ingest.py` | Path allowlist, byte/line/depth limits, digest binding, the frozen checker, diagnostic view |
| `observability/sidecar.py` | The only definition of obs-sidecar-v1 |
| `observability/project.py` | Operation lifecycle, timing, redaction allowlist, headline status |
| `observability/costs.py` | Pricing table validation, dated selection, exact Decimal estimates, rollups |
| `observability/report.py` | Owner-scoped accessors, cross-run flag, aggregates, export |
| `observability/server.py`, `web/` | Read-only loopback UI |
| `scripts/observability_fixtures.py` | Deterministic fixture authoring (oracle is hand-written separately) |
| `scripts/observability_bench.py` | Declared-workload measurements |

No new dependencies. No shared file was edited.
