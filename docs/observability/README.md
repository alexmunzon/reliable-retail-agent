# Trace-based latency and cost review: runbook

A read-only dashboard over versioned traces and obs-sidecar-v1 timing/cost records. It has two
distinct sources: frozen **synthetic fixtures**, and the authenticated local **live synthetic
provider** adapter. The fixture CLI remains standard-library only; the serving adapter uses the
serving lock and bearer authentication. Current evidence is in [the integration receipt](../INTEGRATION.md).
The [original hold](INTEGRATION_HOLD.md) remains dated history. Design: [ADR-001](ADR-001-trace-dashboard.md).
Fields: [data dictionary](DATA_DICTIONARY.md). Review trail: [REVIEW_LOG](REVIEW_LOG.md).

The fixture commands below require Python 3.11+ and the standard library. They need no provider
key or model call; the local UI binds loopback. The separate serving mode uses locally generated
synthetic bearer credentials and the pinned serving dependencies.

## Commands

```bash
python3 -m observability serve                 # http://127.0.0.1:8770, viewer tenant-a
python3 -m observability serve --principal tenant-b --port 8771
python3 -m observability report                # aggregate report in the terminal
python3 -m observability report --json /tmp/obs-report.json
python3 -m observability export healthy/run-healthy --out /tmp/healthy.redacted.json
python3 scripts/observability_fixtures.py --check   # fixtures reproduce byte-for-byte
python3 -m unittest tests.test_observability tests.test_observability_security
python3 scripts/ci.py                          # whole offline suite (includes the tests above)
python3 scripts/observability_bench.py         # declared-workload measurements
```

## Live local serving mode

Follow [SERVING.md](../SERVING.md) to initialize and run the synthetic provider and app, then open
`http://127.0.0.1:8780/observability/`. Paste a locally generated customer bearer token. It is kept
only in page memory, never browser storage or a query string. The viewer cannot approve, cancel or
dispatch an operation. Each data request rechecks the credential, scope, actor/tenant and resource
ownership; fixture `--principal` selection is not used as authentication.

The banner distinguishes `live_synthetic_provider` from fixture data. List/aggregate filters and
denominators cover the newest 100 owned runs; older history is excluded from that sample. Limits
are 2,000 events per run and 4 MiB of event input per snapshot. Selected detail/export remains
independently addressable. Retained trace gaps or truncation are shown as invalid/incomplete data.

Only provider-call duration is currently captured, with monotonic timestamps and a process
incarnation ID. The bounded optional recorder holds up to 512 spans; records expire after restart,
eviction or one hour. Operator-only metrics expose retained/capacity/evicted span counts without
leaking other customers' aggregate activity through the customer dashboard.
Request/queue/SSE/reconciliation durations are not inferred from wall clocks.
No model is invoked, so model usage is not applicable; no live price table or billing receipts are
invented. Optional capture failure cannot authorize or repeat an effect. The transactional outbox
is still required business evidence.

The cost panel keeps measured token records, price-based estimates and receipt subtotals separate.
Conflicting usage/receipts are quarantined; partial evidence makes totals unknown. A known receipt
subtotal is not a complete bill. Currency totals are never combined without an authorized conversion.

## Reading one healthy and one uncertain fixture request

**`healthy/run-healthy`** (status *Completed*, checker CONSISTENT)
- *Proposed → authorized → attempted → confirmed:* prompt at seq 2, approval at seq 3 ("authorized, not executed"), attempt durably recorded at seq 4, provider confirmed at seq 5. That gives 1 mutation attempt, and the effect is confirmed by `dispatch_succeeded`.
- *Time:* the request span runs 0 → 1450 ms in `proc-a:boot-1`, so completion is **1450 ms**. The accepted-command response took 12 ms. The first event was delivered at 655 ms. The direct children sum to 2085 ms because `first_event_delivery` overlaps the others, so the sum is not elapsed time.
- *Cost:* measured tokens are input 1200, output 180, cache_read 800, cache_write 0, reasoning 64. Under the SYNTHETIC table `synthetic-2026-01-01`: 1200×3.00 + 180×15.00 + 800×0.30 + 0 + 64×15.00 = 3600 + 2700 + 240 + 0 + 960 = 7500 per million, so the **estimate is USD 0.0075**. The synthetic receipt says **confirmed USD 0.0074**. The two are shown separately and never reconciled into one number.

**`process-restart/run-restart`** (status *Completed*, history *was unknown*)
- The attempt at seq 4 was followed by `dispatch_uncertain` (`worker_lost`) at seq 5. The process restarted at seq 6. At seq 7, `serving.reconciled effect_confirmed by provider_ledger` resolved the same operation and payload hash. One mutation attempt was made, and the one reconciliation lookup is not a retry.
- *Time:* the request span started in `proc-a:boot-1` but ended in `proc-a:boot-2`. The clocks are never subtracted, so completion is **unknown**. The only claim is **≥ 280 ms**, the longest nested span (the boot-2 lookup).
- *Cost:* there are no usage records, so the estimate is unknown ("no usage recorded"), never $0.

**`lost-response/run-lost-response`** stays *Unknown outcome*. The independent simulator (`fixtures/v1/effects.json`) applied the effect, but the trace never resolved it. The dashboard shows Unknown and keeps the USD 0.0075 estimate marked "outcome unknown". It never shows failed or $0.

## Load aggregate, reproducible by hand

`load/run-load-01..24` have durations 110, 120, … 340 ms. The three slowest (22–24) failed and **are included**. Nearest rank over n = 24: p50 = 12th value = **220 ms**, p95 = ⌈0.95 × 24⌉ = 23rd value = **330 ms**. If failures were hidden, p95 would be 300 ms (a planted mutant checks this). The estimate is Σ(1000+i)×3/1e6 + 24×200×15/1e6 = 0.0729 + 0.0720 = **USD 0.1449** (synthetic prices). With n = 24, p95 is one observed value, not a stable tail.

## Original fixture checkpoint (historical)

| Evidence | Establishes | Does not establish |
|---|---|---|
| 47 feature tests + 215 existing tests (262 total), `scripts/ci.py` locally (no network, no inherited credentials) | The specified projection, limits, redaction and isolation behave as written on the frozen fixtures | Hosted CI execution (not run), production reliability |
| Hand oracle + 8 planted mutants | The named failure modes are detected by meaningful assertions | That no other bug exists |
| `evalgate check` PASS (68/68 normative, 0 changed outcomes) | The existing regression gate is unaffected | Anything about live serving |
| Bench receipt | Local timings for 54 and 2,000 synthetic requests | Production latency or capacity |
| Browser walkthrough (built-in browser, 2026-10-25) | List, filters, detail, keyboard, narrow screen, cross-tenant 404, displayed values equal the oracle | Accessibility audit with assistive tech |

The table records the original fixture-only checkpoint; it is not certification of the later
integration. [Current integration evidence](../INTEGRATION.md) records reruns separately. No real
customer data, paid provider/model call, purchase, actual billed spend or deployment is claimed.
Live adapter data comes from the local synthetic provider, not production traffic.
