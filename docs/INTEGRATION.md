# Retail integration: claims, evidence and remaining limits

Current post-build follow-up: [POST_BUILD_REFLECTION.md](POST_BUILD_REFLECTION.md). Earlier receipts and claims below retain their original version and runtime; harness-v3 and the new pin are documented in the follow-up.

**Local verification complete: 2026-10-25.** The backend is pinned to
`87970608892a0d130a18f9c6bc31bdfcf0e9e471`, with its dependency-lock digest. All three tracks
are integrated on `claude/backend-adapter-6b82f6`; original source branches remain intact.
[Initial integration evidence](../evidence/integration-2026-10-25/README.md) and [final reflection/CI follow-up](../evidence/integration-2026-10-25-followup/README.md) records exact commands, counts and limitations.
[Draft PR #2](https://github.com/alexmunzon/reliable-retail-agent/pull/2) carries the latest remote-head
CI status. GitHub merges, deployment and repository security/settings changes remain prohibited.

## What changed

The preserved dashboard history and staged backend implementation were integrated without rewriting
or deleting the source branches. Scoped review found and corrected stale-credential authorization,
revocation/rotation revival, fabricated restore-history fields, optional log failures, misleading
sidecar/cost completeness, and trace no-effect evidence checking. The new adapter exposes explicit
trace-v1/v2 exports and a GET-only authenticated local dashboard. It reuses existing projection and
cost logic rather than adding another analytics service or schema.

| Claim | Implementation/check | Limitation |
|---|---|---|
| Current identity and exact action are checked before dispatch | Transactional authorization and credential-bound consent in `serving/service.py`; auth/HTTP/durability regression tests | One local service process; no production identity-provider integration |
| A crash does not authorize another uncertain mutation | Durable claim/outbox + resource hold; real subprocess/provider-ledger tests in `tests_serving/test_crash_recovery.py` | No atomic transaction across app and provider; unsupported status lookup stays unknown |
| Definite no-effect dispatch has an explicit contract | Frozen trace-v2 `dispatch_rejected`; compatibility trace-v1 preserved; HTTP trace tests | v1 cannot express that core terminal outcome; restored missing history remains incomplete |
| An observation cannot approve or dispatch | GET-only `serving/observability_api.py`; auth/ownership checked in `LiveDataset` read transaction; independent effect-count tests | Latest 100 owned runs for list/aggregates; 2,000 events/run and 4 MiB/snapshot; gaps are reported |
| Provider duration is measured without cross-clock subtraction | Process-incarnation monotonic spans; `tests_serving/test_observability_live.py` | Provider only; max 512 process-local spans, one-hour visibility, no persistence across restart |
| Missing/conflicting cost evidence cannot become a complete zero total | `observability/costs.py` and regression tests; distinct usage, estimates, receipt subtotals and completeness | Fixture prices/receipts are synthetic; live adapter makes no model calls and has no billing receipts |
| Runtime and evaluation remain bounded and inspectable | Existing module boundaries, query limits, owner-created index, finite worker/replay/retry bounds | Measurements describe only the declared local workload; no production capacity claim |

## Verification ledger

All listed evaluations are scripted/synthetic. Existing historical/native artifacts remain a
different evidence class. Initial failures are retained locally; exposed cases are regression tests,
not an untouched holdout. Internal reviews share model/context/filesystem access and are role-separated
reviews, not independent real-world collection.

| Command/lane | Result on the integrated source | Evidence/limits |
|---|---|---|
| `python3 -B scripts/ci.py` | 342 passed; 20.700 s | Includes fixture dashboard oracles and eight planted dashboard mutants |
| `python3 -B scripts/ci_serving.py` | 91 passed; 97.962 s | HTTP/SSE, process crashes, storage failures, restore, auth, trace-v1/v2 and live dashboard |
| `scripts/ci.py --native` in existing exact-version environment | 338 core + 8 original-author checks passed | Native checks 3.717 s; synthetic completions, no model calls |
| `python3 -m evalgate check` | 68/68 normative; 0 changed outcomes | 0/7 exploratory language cases; existing approved baseline unchanged |
| `evalgate backend --pinned` from fresh environment | 24/24 gating cases passed under r5/harness-v2 | Exact commit and lock verified; one exploratory restore-before-approval failure retained |
| Core/evidence mutation proof | 33/33 killed; unmutated control passed | Named semantic failure checks, including evidence tampering |
| Backend mutation proof | 11/11 killed under r4; affected B11 reverified under r5/v2 | Independent ledger; exact B5/B11 refusal status/code |
| Headless UI | 28 checks passed | 101→100 sample disclosure, GET-only, empty browser storage, unchanged business state; no external requests |
| Fresh archive + fresh offline hash-locked venv | 22 HTTP/dashboard tests passed; 18.282 s | 15 packages installed from existing cache; no downloads |
| Predeclared serving workload | Both sources passed 54 diagnostic budget checks | 360/360 measured operations/effects per source; short accelerated local bursts |
| Fixture dashboard benchmark | 2,000 requests: 1,072.264 ms load best-of-3; 126.75 MiB peak traced heap | Query best-of-5, not percentiles; full receipt retained |

[Machine receipt](../evidence/integration-2026-10-25/verification.json) links logs and hashes;
[performance report](../evidence/integration-2026-10-25/PERFORMANCE.md) gives samples, warmup,
resource bounds, before/after results and limitations. The initial failures remain preserved.
The B5 refusal gap was exposed during review and fixed with a declared label-only r4 update;
no cases or state/effect gates were removed, and the original baseline was not automatically updated. Final reflection also found B11 accepted the right refusal code with the wrong HTTP status; r5 strengthens that label. Hosted CI exposed an ineffective chmod-only storage-fault phase. Harness-v2 holds a real SQLite write lock and guarantees cleanup; no readiness deadline or safety threshold was relaxed. The failed CI receipt and all earlier local results remain preserved.

Delivery states remain distinct. Local verification is complete. Read the exact PR-head checks
before claiming `CI_EXECUTED`. The existing main ruleset requires `evaluation-gate` and `test`;
`backend-evaluation` is not required. The current stacked PR base `claude/evaluation-gate` has no
applicable rules. Therefore backend merge enforcement and enforcement on that stacked base are
**not established**. Changing those settings requires separate authority; no settings were changed.

## Reflection and scope

The auth defects required checking current authorization inside the same transaction that uses it;
checking only at HTTP entry was insufficient. Credential epochs prevent a revoked approval from
reviving after rotation. An optional logger must not crash an otherwise durable operation. A restore
may recover provider truth while still losing the local historical evidence; the code now preserves
that distinction rather than manufacturing an attempt/consumption record.

The trace and dashboard fixes challenge friendly status/cost labels against actual evidence. A
trace-v2 no-effect claim must disagree with an applied provider effect and fail evaluation. Sidecar
failure must preserve the authoritative trace while marking unavailable measurements. Conflicting
usage/receipts and mixed currencies cannot be hidden in one reassuring total. The bounded live sample
must stay visible beside filtered denominators.

The integration reuses the frozen contracts, existing projection/scorer and the current consent
classifier. Frozen historical schema/classifier copies are retained for provenance, not treated as
dead code. No graph framework, new queue/broker/database, model call or telemetry vendor was added.
The live adapter resides in `serving/` so fixture observability stays free of mutation-capable imports.
The added owner-created index addresses the actual bounded list query; broader speculative indexing
is unnecessary. Optional span eviction is visible through operator metrics; customer pages do not
expose cross-principal counts. The SSE test helper now uses a total read deadline instead of waiting
indefinitely for heartbeat silence; this reduces test delay without changing runtime behavior or
removing assertions. The final source review found no remaining concrete high-signal issue. Provider spans cost a measured p95 2,375 ns per recorder call in the declared microbenchmark; this is not a whole-service A/B. Runtime dependencies were unchanged. Keep the 100-run sample and 512-span bounds rather than adding a telemetry service. The 21-run live list query measured p95 9.31 ms; no large-history or sustained-capacity claim follows.

Deliberately unsupported or unrun: distributed exactly-once execution, multi-worker deployment,
TLS/proxy/public hosting, real customers, actual bills, real-model improvement, complete recovery of
history missing from a stale backup, and untouched held-out validation of the exposed regression set.
The prospective model lane remains disabled pending its separate resource/budget authorization.
