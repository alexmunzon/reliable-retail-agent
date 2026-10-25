# Local Retail performance evidence — 2026-10-25

Both frozen versions met the declared diagnostic budgets. Each completed **360/360 measured pilot/mixed operations with exactly 360 independent provider effects and zero operation errors**, plus **12/12** while two sockets stopped reading. Each produced 390 total effects including 18 declared warmups. This is synthetic local evidence, not a production reliability or capacity claim.

Baseline: `c0afb816d68fe2b3367c0457ffc271d163a0f793`. Candidate: `87970608892a0d130a18f9c6bc31bdfcf0e9e471`. Both were exported from Git into isolated folders; source hashes remained unchanged. The final runs used identical harness and plan hashes.

| Workload (120 observations per version) | Baseline completion p50 / p95 ms | Candidate completion p50 / p95 ms | Candidate completed ops/s across 3 repetitions |
|---|---:|---:|---:|
| One customer | 22.58 / 24.88 | 22.93 / 29.12 | 28.78–31.00 |
| Two customers | 21.62 / 27.15 | 22.64 / 34.43 | 48.34–63.43 |
| Two customers + live/replay streams | 22.89 / 28.56 | 23.78 / 33.31 | 33.05–38.92 |

Candidate mixed p95: consent response **7.57 ms**, consent to client-observed dispatch **13.90 ms**, stream-open to first existing committed event **12.15 ms**, reconnect catch-up **14.99 ms**, and health response **4.63 ms**. Accepted-consent and completed-operation throughputs are separate fields in the raw results; they agree here because every accepted operation completed. Completion includes the client's 10 ms state-poll interval.

Admission evidence: four streams for one principal returned 200; the fifth returned 429. Eight active unapproved runs were admitted; the ninth returned `429 active_run_limit`. With default burst20/rate60, one setup request plus 19 measured replays were admitted and 21 measured requests were rate-limited; these observations caused no effects. Two non-reading sockets did not prevent 12 unrelated operations. The latter is low-volume occupancy evidence, not proof of kernel-buffer saturation or the slow-send timeout.

| Resource | Baseline | Candidate |
|---|---:|---:|
| Service peak sampled RSS (MiB) | 58.88 | 61.02 |
| App ready p50 / p95 (ms; 12 starts) | 322.46 / 417.29 | 333.05 / 577.64 |
| Sampled app CPU across phases (s) | 6.80 | 7.81 |
| Largest app DB + WAL + SHM growth per phase (bytes) | 4685640 | 4677616 |
| Peak app DB + WAL + SHM size (bytes) | 4829000 | 4825072 |

Host: Apple M1, 8 CPUs, 16 GiB RAM, macOS; exact OS/Python/package/volume metadata is in each result. SQLite settings are recorded per phase. One ASGI process and one separate provider process share this host with the clients. No provider delay/failure was injected into normal lanes. Baseline and candidate use the same installed serving dependency set. Startup excludes database initialization, which is separately reported. CPU/RSS sampling at 100 ms may miss peaks. Shared-host background work prevents attributing small differences to one code change.

Live dashboard query diagnostics (candidate only, 21 owned runs, 20 observations after one warmup per route):

- `requests?page_size=20`: p50 6.84 ms, p95 9.31 ms, errors 0/20. Browser rendering excluded.
- `aggregates`: p50 6.35 ms, p95 7.21 ms, errors 0/20. Browser rendering excluded.

Optional `SpanRecorder` call microbenchmark (20,000 calls each):

- off: p50 42 ns, p95 84 ns, caught failures 0.
- on: p50 2250 ns, p95 2375 ns, caught failures 0.
- failure: p50 542 ns, p95 584 ns, caught failures 20000.

The enabled recorder retained 512/512 records. This supports the bounded recorder choice; it is not a whole-service off/on/failure comparison or an effect-isolation test. The original baseline has neither the live dashboard nor the recorder, so those comparisons are explicitly unavailable.

Predeclared budgets: command p95 ≤500 ms, consent-to-completion ≤1,000 ms, first replay event/catch-up ≤500 ms, health ≤250 ms, service RSS ≤256 MiB, ready ≤5,000 ms, zero legitimate-operation errors, exactly one effect per completion. `performance-comparison.json` lists every evaluated budget and SHA-256 digests of the locally retained original evidence. The short accelerated workload overrides command rates to 6,000/minute and burst1,000; default product rates are only exercised in the separate refusal probe. This does not advertise sustained default-profile throughput.

All percentiles use nearest rank. Three short repeated bursts are correlated observations; p95 is descriptive and cannot establish a stable tail. Raw warmup observations are retained locally and excluded. No direct event-loop lag, queue saturation, global64-stream saturation, long-duration soak, large-history dashboard query/render, whole-service instrumentation A/B, dependency installation or test-runtime comparison was performed here. Existing correctness/clean-room/UI suites remain separate evidence.

First exposures remain intact: two extraction attempts failed before services started because Python lacked `tarfile.extractall(filter=...)`; the empty launch directories remain. The normal sandbox then denied loopback binding (`results-baseline-bind-probe`). Scoped local execution was approved. `results-baseline-run1` completed its operation workload but its admission probe used an invalid short key and received eight 400s; this is a harness defect, not a service limit failure. Before the final pair, keys were fixed, partial observations were preserved, health warmups/errors corrected, and first-event semantics clarified after a read-only review. Numeric budgets were unchanged. The original plan is `benchmarks/serving-plan-first-baseline.json`.

Reproducible command (run with the already-installed pinned serving Python):

```bash
PYTHONDONTWRITEBYTECODE=1 <serving-python> -B scripts/serving_bench.py \
  --source <frozen-git-export> --plan benchmarks/serving-plan.json \
  --output <new-output-directory> --label <exact-source-revision>
```

Public artifacts: [plan](../../benchmarks/serving-plan.json), [harness](../../scripts/serving_bench.py), and [derived comparison and budgets](performance-comparison.json). The derived comparison removes only local source-directory paths and binds the retained original raw results, full comparison and source receipts by SHA-256. Measurement values, budgets, commit identities and archive hashes are unchanged. The harness writes new evidence exclusively, leaves source unchanged and uses synthetic provider state in temporary directories. No provider credentials are inherited.
