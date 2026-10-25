# Data dictionary: obs-sidecar-v1, obs-pricing-v1 and the projected request

trace-v1 field meanings are defined only in `contracts/trace-v1.schema.json` and `contracts/HANDOFF.md`. Nothing here changes them.

## obs-sidecar-v1 (JSON Lines, closed fields; any extra field rejects the whole record)

Common to every record: `sidecar_version` = `obs-sidecar-v1`, `record_id`, `kind`, `run_id`, `trace_id` (all opaque trace-v1-style ids) and `emitter {component, emitter_version}`. A record binds only to a stream inside its own digest-bound artifact pair.

### kind = span

| Field | Meaning |
|---|---|
| `span_id`, `parent_span_id` | Unique within the request. The parent must exist in the same request, and the graph must be acyclic, or the span is rejected |
| `span_class` | `request` (completion), `command_response` (accepted-command response), `first_event_delivery` (first SSE event delivered, **not** time-to-first-token), `queue_wait`, `model_call`, `user_wait` (customer think time), `authorization`, `local_persistence` (claim/outbox write), `dispatch_provider`, `reconciliation_lookup`, `sse_delivery` |
| `clock_domain` | A globally unique process incarnation, `name:incarnation` (e.g. `proc-a:boot-2`). Monotonic values are only comparable within one domain |
| `start_mono_ns`, `end_mono_ns` | Non-negative integers from that domain's monotonic clock, or `null` (missing endpoint means unknown) |
| `end_clock_domain` | Present when the end was measured in another incarnation; the duration is then **unknown** |
| `status` | `ok` / `error` / `unknown` for the span's own work. **Never** a business outcome |
| `operation_id`, `linked_event_ids` | Must exist in the trace stream, else the span is rejected |
| `attempt_no` | Allowed **only** on `reconciliation_lookup`. Lookups are not mutation retries |
| `error_code` | `[a-z][a-z0-9_]{0,63}`; free text is impossible |
| `route_class`, `process_request_index` | Request span only. `route_class` is a bounded label (never a user id). The index orders warmup per process incarnation |

### kind = usage (one provider call's **final** totals; `usage_semantics` must be `final_per_call`)

Fields: `span_id`, `provider`, exact `model_id`, `provider_request_id` (or `null`), `measurement` (`measured` from the provider's usage field, or `estimated` from a local count, kept in a separate series), `observed_at` (UTC, selects the price table; `null` means pricing is unknown) and `source`.

`tokens` holds **disjoint** categories, each an integer or `null` (= not supplied, never 0):

| Category | Includes | Excludes |
|---|---|---|
| `input` | uncached prompt tokens | cache reads, cache writes |
| `cache_read` | tokens served from cache | |
| `cache_write` | tokens written to cache | |
| `output` | visible completion tokens | reasoning |
| `reasoning` | reasoning tokens billed as output | |

Identity is `(provider, provider_request_id)`, or the `record_id` when there is no request id. A re-sent identical record counts once (`duplicate_usage_identities`). Conflicting content under one identity excludes both records.

### kind = billing_receipt

`usage_record_id`, `amount` (decimal string, at most 12 dp), `currency` (ISO 4217), `receipt_id`, `source`. Identity is `(source, receipt_id)`. This is the **only** source of a confirmed charge.

### kind = drop_report

`dropped_records` and `reason` ∈ `queue_full`, `oversize`, `emitter_error`. These are the emitter's backpressure counts, and they show as telemetry incompleteness.

## obs-pricing-v1 (one JSON file per dated table)

`table_id`, `effective_from` (UTC date), `currency`, `unit` = `per_1m_tokens`, `synthetic` (bool), `source`, `assumptions[]`, and `models {model_id: {provider, <category>: decimal string}}`. The table used is the latest one whose `effective_from` ≤ the usage's UTC date. Two different entries in force on one date make pricing `ambiguous_pricing`. No currency conversion is ever done. Fixture tables are **SYNTHETIC** and are not real prices.

`estimate = Σ tokens[c] × price[c] / 1,000,000`, in exact Decimal, with no rounding until display. It is unknown (`null` plus a reason) for any of: `no_usage_time`, `model_not_priced`, `token_category_not_supplied`, `category_not_priced` (with non-zero tokens) or `ambiguous_pricing`. A price of `"0"` is a legitimate zero.

## Projected request (API `/api/requests/<case>/<run>`, export)

| Field | Meaning |
|---|---|
| `status` | Headline label only. Precedence: `incomplete` (gap, missing run_finished, attempt without outcome, unattributable line) > `invalid_evidence` (checker INVALID) > `unknown_outcome` (any unresolved unknown) > `cancelled` > `failed` (reconciled `no_effect_confirmed`, or a handler error on the request span) > `completed` |
| `evidence.contract_verdict`, `contract_codes` | The frozen checker's verdict on the artifact as delivered, as fixed codes (no echoed input) |
| `evidence.diagnostic_view` | True when identical repeated lines were collapsed or lines reordered for display. The verdict still stands |
| `operations.<id>` | `prompts`, `distinct_payloads`, `invalidations`, `authorized` (not execution), `mutation_attempts` (`dispatch_started` count), `attempts_across_runs`, `outcome` ∈ `effect_confirmed` / `no_effect_confirmed` / `unknown` / `outcome_missing` / `not_attempted`, `confirmed_by`, `history_has_unknown`, `reconciliations[]`, `cancellation` ∈ `before_claim` / `after_claim` |
| `timing` | `request_duration_ns` (measured) or `duration_kind` = `lower_bound` / `unknown`, `command_response_ns`, `first_event_ns`, `by_class`, `children_overlap`, `clock_domains`, per-domain `lanes` (raw ns as strings), rejection codes, `telemetry_dropped` |
| `costs` | `usage[]`, `estimated_charge {total or null, known_subtotal, unknown_usage_records, reason, pricing_tables}`, `estimated_charge_from_estimated_tokens`, `confirmed_charge`, `span_rollup` (direct plus descendants), `uncertain_outcome` |

## Aggregates

Groups never mix `data_origin`, `evidence_class` or `route_class`. Each shows the request count, every status count, the window rule (request start wall time in `[from, to)`; undated requests are outside every window and are counted), eligible latency samples, exclusions with reasons (`incomplete_evidence`, `invalid_evidence`, `duration_lower_bound_only`, `duration_unknown`, `warmup`), and warmup K per process. Percentiles use **nearest rank**: the ⌈p·n/100⌉-th smallest value, n = 0 gives unknown, and failed requests are included. There is a small-sample warning under n = 100. Costs show complete-request estimates per currency, how many requests have an unknown estimate plus their known subtotal, and confirmed charges.
