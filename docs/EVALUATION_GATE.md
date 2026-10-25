# Evaluation gate (milestone 1)

Current post-build follow-up: [POST_BUILD_REFLECTION.md](POST_BUILD_REFLECTION.md). Earlier receipts and claims below retain their original version and runtime; harness-v3 and the new pin are documented in the follow-up.

**Status: `MERGE_ENFORCED`.** On PR [#1](https://github.com/alexmunzon/reliable-retail-agent/pull/1), hosted [`evaluation-gate`](https://github.com/alexmunzon/reliable-retail-agent/actions/runs/40155951054) and [`test`](https://github.com/alexmunzon/reliable-retail-agent/actions/runs/40155950952) passed on head `311b2dd` (`CI_EXECUTED`). Since 2026-10-24, the repository ruleset "Require evaluation-gate and test on main" requires both checks before anything merges into `main`, with no bypass. All results below are deterministic scripted fixtures with no model calls.

## The claim, and its limits

Given trusted orchestration, registered simulated mutation tools, and the declared consent policy, **regressions in exact-action authorization and in successful legitimate dispatch are detected automatically before merge.**

It does **not** decide business-policy correctness, authenticate callers, stop a privileged maintainer from removing controls, provide durable or crash-safe recovery, or measure model behavior. The suite was written by the same builder who read the gate code. It is a **builder-authored regression suite**, not an independent held-out benchmark, and independent held-out validation remains **unestablished**.

## Commands

```bash
python3 -m evalgate check        # the gate: approved baseline 8e70c0f vs this tree. Exit 0 PASS, 1 FAIL, 2 INVALID
python3 -m evalgate run          # evaluate one tree (--sut PATH, --role baseline|candidate)
python3 -m evalgate compare BASELINE_RUN CANDIDATE_RUN
python3 -m evalgate validate RUN_DIR   # re-score raw evidence; never trusts stored verdicts
python3 -m evalgate trace-check TRACE.jsonl  # check a service-generated trace-v1/v2 file: CONSISTENT or INVALID, never PASS
python3 -m evalgate coverage     # invariant coverage matrix
python3 scripts/mutation_proof.py      # plant curated regressions in throwaway copies and run the real gate
python3 scripts/ci.py            # all offline unit tests, including the harness's own negative controls
python3 -m evalgate backend --backend PATH [--backend-python PY]  # opt-in: the durable backend's real effects (exit 3 NOT_RUN if absent)
python3 -m evalgate backend --pinned --backend-git REPO --backend-python PY  # exactly the commit in evalgate/backend-pin.json (NOT_RUN if unset)
python3 -m evalgate backend-validate RUN_DIR                      # re-score a backend run from its raw observations
python3 scripts/backend_mutation_proof.py --backend PATH          # plant real bugs in throwaway backend copies
```

No API key, package install or network is needed. Evidence goes to `runtime/evalgate/` (gitignored). Each run gets a new directory created exclusively, and no file is ever overwritten.

## Current result (`dev-v1` r5, policy `evalgate-policy-v1`)

| Metric (normative, gating) | n/N |
|---|---|
| Unauthorized dispatch attempts / declared forbidden opportunities | **0/47** |
| Unsafe-effect episodes / dangerous episodes | **0/40** |
| Duplicate effects (repeat stratum, 7 cases) | **0** |
| Wrong-payload effects (payload stratum, 2 cases) | **0** |
| Legitimate actions blocked by the gate / legitimate opportunities | **0/30** |
| Correct legitimate execution (exact effect, nothing extra) | **27/27** |
| Complete, valid records / scheduled | **68/68** |
| *Exploratory plain-language approvals (non-gating)* | *0/7 execute* |

The approved baseline (8e70c0f) and the current candidate are identical on all 75 cases (0 changed outcomes). The exploratory row is a real usefulness gap: "yes please", "Yes, I confirm.", "sure", "ok, go ahead", "yeah, do it" and similar are all treated as ambiguous. That is safe but unhelpful. Widening the grammar is a separate, reviewed classifier change.

## Decision rules (fail-closed)

The pipeline is finite: freeze inputs (policy, suite hashes, harness files, SUT commit), run every scheduled case once in a fresh process, score effects from the independent simulator, validate evidence, compare with the approved baseline, then end with exactly one of three results.

| Condition | Result |
|---|---|
| Every gating case passes, all evidence complete and consistent, baseline valid and identical in identity | **PASS** (exit 0) |
| Any gating case has an unauthorized attempt, unsafe, duplicate or wrong-payload effect, incomplete or misleading prompt, missed legitimate action, or unexpected exception; or an exploratory case passing on the baseline now fails | **FAIL** (exit 1) |
| A case result is missing or crashed; the driver timed out or exited; any file is unreadable, truncated, non-strict JSON or fails its manifest hash; records, metrics or decision differ from a re-score of raw evidence; the trace breaks trace-v1 or its ordering rules; suite, policy, harness or commit identity mismatch; the baseline is unapproved or itself INVALID | **INVALID** (exit 2) |
| `trace-check` on a service trace | **CONSISTENT** (exit 0) or **INVALID** (exit 2), never PASS |

Missing evidence never produces PASS. A timeout is bounded by `driver_timeout_seconds` (60 s) and produces an explicit record for every scheduled case.

**Repair loops are separate from evaluation.** A FAIL or INVALID result is evidence, kept in its own new directory and never overwritten. It doesn't permit changing a case, a label, a threshold or the oracle to get green, and there's no "retry until green" path: `check` runs each case exactly once. Oracle or suite changes are reviewed changes: they need a recorded revision, refreshed policy hashes, and before-fix evidence showing the old version missed something (see the Astra rounds below). Mutation-proof mutants run on throwaway copies, never on the working tree. There is no hidden challenge set, so nothing was tuned against one.

## Threat model and trust boundaries

| Boundary | Rule |
|---|---|
| User consent vs actor/tool text | Only a reply on the user channel, bound to the prompt identity that was actually shown, can authorize. Actor-authored, tool-observation, quoted, injected and unrelated assent are adversarial cases. |
| Public case input vs hidden labels | `cases.json` (actor-visible) and `labels.json` (scorer-only) are separate files. The driver process receives only whitelisted actor fields, and `tests/test_evalgate.py` inspects the captured request for leakage. |
| Actor dispatch vs scoring | The gate and adapter run in a fresh `python -I` process that sees only the code under test. The scorer reads the **independent simulator's** invocation log and before/after state, and never asks the gate whether it behaved. |
| Candidate output vs evidence | Validation re-scores raw evidence and checks manifest hashes, suite and policy hashes, module provenance and the trace-to-effect agreement before any verdict counts. |
| Trusted config vs untrusted PR | In CI, a PR is judged by the **base revision's** harness, policy and suite (`harness_trust: base-revision`). Changed gate-controlling files are listed in the job summary. |

Limits stated plainly: the simulator shares a process with the candidate, so a *malicious* PR could tamper with it in memory. This detects regressions; it is not a sandbox against hostile contributors (`scripts/ci.py`'s socket block is test isolation, not security). An ordinary workflow file in a PR branch is not tamper-proof against an authorized maintainer. Concurrency is out of scope: the in-memory gate is tested single-threaded, and concurrent direct callers are milestone 2's problem.

## Invariants and coverage

1. Missing, stale, wrong-prompt, rejected, ambiguous, conditional, injected, model-authored or unrelated assent cannot authorize.
2. Consent covers the complete exact arguments, operation and current prompt, and the prompt the customer approves must show every argument. Changes need fresh consent. Mutating copies cannot alter an intent.
3. Unknown or unclassified mutation tools fail closed, and reads stay usable.
4. An attempt is recorded before the tool runs. A repeat never invokes twice. An uncertain outcome halts, even under a new operation ID.
5. A valid, consented action on a healthy provider produces exactly its effect once. Blocking everything fails.
6. Fixed inputs give identical verdicts, records and traces across runs. A hung or crashed driver ends as INVALID with a record for every case. *(`ReproducibilityTests`, `TerminationTests`.)* Friendly statuses, dropped trace events, forged or truncated or replayed result files, duplicate, missing or renamed records, non-finite numbers, unknown versions and mismatched suites or baselines, trace-contract violations and relabelled commits cannot produce green. *(Covered by `tests/test_evalgate.py` and evidence mutants E1–E6.)*
7. Hidden labels never reach the actor process. Native reward and scoring code is untouched. *(Label isolation is covered by the leakage test. "Native code unchanged" is **not** an automated check: it was verified by `git status` at delivery, and the PR job lists any change under `vendor/`, `native/` or `reproduction/` for explicit review. An earlier draft overstated this; the gpt-6-astra review caught it.)*

`python3 -m evalgate coverage` prints the live matrix. Today: invariant 1 has 11 positive and 24 adversarial controls; 2 has 4 and 9; 3 has 11 and 3; 4 has 6 and 7; 5 has 24 and 3.

| Invariant | Mutant that must be killed |
|---|---|
| 1 | M1 accept unbound/stale prompt; M7 accept ambiguous replies; M11 invent assent during proposal; M17 ignore termination |
| 2 | M2 ignore payload changes; M9 prompt hides a field; M10 adapter truncates prompt; M12 prompt lists bare values without names or operation; M6 tool gets a different payload |
| 3 | M8 adapter lets unclassified tools through; M16 adapter serves stale reads |
| 4 | M3 retry after uncertain; M4 remove one-attempt guard; M13 trace drops lifecycle events; M14 attempt recorded after the tool |
| 5 | M5 block everything; M15 halt after any success |
| 2 (added in Astra rounds 2–4) | M18 prompt shows 250 for 25; M19 consent cached by payload for a new operation; M20 prompt shows 25,000; M21 prompt shows right values as "Old" and a wrong "New"; M22 prompt shows 25 000 (space-grouped) |
| 2 (added round 5) | M25 a summary line above the exact arguments states a wrong amount; M26 the prompt JSON repeats a key with a wrong value |
| 3, 5 (added rounds 4–5) | M23 adapter drops the customer's approval on any read; M27 adapter forgets a declared read tool |
| 4 (added round 5) | M24 one-attempt ledger remembers only the latest operation |
| 6 | E1 forged pass records; E2 dropped failing cases; E3 renamed case; E4 unapproved baseline; E5 trace-invalid candidate with honestly regenerated files; E6 current code labelled as the approved commit |

**Mutation proof:** all 27 code mutants and all 6 evidence mutants are killed for their named semantic reason, the unmutated tree passes the same command, and the working tree is verified unchanged. See `evidence/evaluation-gate/mutation-proof/receipts.json`, with one patch and one comparison report per mutant. An earlier proof run where two of *my predictions* named the wrong failure class is kept as `receipts-run1-prediction-errors.json`. Pre-fix proof runs are kept too: `mutation-proof-pre-astra-fixes/` (before round 1's fixes) and `astra-round2-pre-fix/` through `astra-round5-pre-fix/` (each new mutant green, or its named case passing, on the harness before that round's fix, plus the unit checks that failed before the fix).

## How it is built

`evalgate/` uses only the standard library and adds no dependencies:

- `simulator.py`: independent synthetic retail store. It records each attempt before any effect, and its provider modes are healthy, effect-then-timeout, effect-then-lost-response, and error-before-effect. It answers all six declared tau retail read tools from a small read-only synthetic catalog.
- `driver.py`: runs in the SUT process, executes actor-visible scripts through the **real public API**: `ConfirmationGate.propose/respond/dispatch` and `TauDispatchAdapter.request_confirmation/dispatch`. It verifies that every imported SUT module comes from the SUT tree.
- `scoring.py`: the oracle compares exact invocations (type-strict canonical JSON) and post-state against hand-written labels, binding at most one expected call to each dispatch step. It checks that every shown prompt is the declared confirmation text with exactly its operation, tool and one strict JSON argument object (duplicate keys rejected), that each call had its own fresh prompt → approval → attempt record, that the trace snapshot at tool entry is a prefix of the final trace, and that provider reads match the scheduled reads. Then it computes metrics and the policy decision.
- `runner.py` / `compare.py` / `__main__.py`: evidence directories and manifests, validation, comparison, CLI.
- `trace.py`: maps legacy gate events into `contracts/trace-v1` and validates them.
- `suites/dev-v1/`: 75 cases (68 normative, 7 exploratory) in 25 families. `policy-v1.json` freezes the suite hashes and the approved baseline. `prospective-plan-v1.json` holds the disabled real-model plan.

**Bootstrap:** 8e70c0f has no harness, so `check` exports that exact commit with `git archive` and runs it through *this* harness in an isolated process. That works because the public gate/adapter API is unchanged. The baseline is re-run every time rather than stored as a golden file, so there is nothing to auto-update or bless.

**Suite revision r2 (before acceptance):** the first run failed two tau cases because they used `cancel_pending_order`, a tool outside the adapter's audited inventory. The adapter correctly refused it. The cases were fixed to use a classified tool, and the failing r1 run is preserved in `evidence/evaluation-gate/initial-run-suite-r1/`. It was produced by an earlier harness build, so the current `validate` command will reject it. That's expected, and the run is kept as history only. Before publishing to the public repository, local machine paths in preserved evidence were replaced with `<local-checkout>`, `<home>` and `<tmp>` placeholders, so that run's recorded file hashes no longer match its two redacted files.

## CI and merge enforcement

- `.github/workflows/evaluation-gate.yml`, job **`evaluation-gate`**: every PR (no path filters) plus pushes to main. Read-only token, no secrets, `persist-credentials: false`, pinned checkout SHA, 10-minute limit, no artifacts or caches. On a PR it checks out the merge revision with full history, uses the base SHA's harness when present (otherwise warns "bootstrap"), runs `check`, and writes the comparison and summary to the job summary.
- `offline-tests.yml`: the `**.md` path filters are removed, so a required check cannot stay pending on Markdown-only PRs. Cost: Markdown-only PRs now use about 1 runner-minute each.
- **Locally simulated, not hosted:** the workflow's own shell steps were executed against a throwaway clone. Results: bootstrap PR → PASS with warning; a PR planting M1, judged by the base harness → FAIL naming the forged/unbound/stale cases; the repair → PASS. See `evidence/evaluation-gate/ci-simulation/`.
- **Hosted (`CI_EXECUTED`):** on PR #1, the first run (head `f81a207`) passed `evaluation-gate` but failed `test`: its shallow checkout lacked the approved baseline commit, which the unit tests export by SHA. `offline-tests.yml` now fetches full history (`311b2dd`), and both checks passed on that head. No test or assertion was changed.
- **`MERGE_ENFORCED`:** at Alex's request, repository ruleset 25808504 ("Require evaluation-gate and test on main") applies to the default branch with no bypass actors. It requires the check runs `evaluation-gate` and `test` from GitHub Actions (integration 15368), the exact job names in `evaluation-gate.yml` and `offline-tests.yml`. Both workflows run on every pull request with no path or branch filters. "Branch must be up to date" is off. Not done: CODEOWNERS for `evalgate/`, `contracts/` and `.github/` (optional). A repository admin can still edit or disable the ruleset, so it is a guard rail against mistakes, not against an admin.

## Runbook

**Updating the approved baseline** (only after a reviewed improvement is merged): open a PR that changes only `approved_baseline_sha` in `evalgate/policy-v1.json` to the merged commit, and attach the `check` output showing the new baseline passes absolutely. The job summary lists the policy change for the reviewer.

**Changing the suite or policy:** edit `cases.json`/`labels.json`, add a `provenance.revisions` entry explaining why, and update the hashes in the policy, which `load_suite` enforces. Never relabel a failure, remove a hard case or lower a requirement to get green. If a specification was wrong, record the revision and keep the earlier evidence.

**When the gate is red:**
- **FAIL** means behavior. Open the candidate `summary.md`. Each failing case lists its failure class and the observed vs specified effect. Reproduce with `python3 -m evalgate run`, then read the case script in `cases.json` and its label in `labels.json`. The raw trace and simulator log are in `driver-output.json` and `traces.jsonl`.
- **INVALID** means evidence. The reasons name the problem: missing or crashed case, trace/effect contradiction, hash or suite mismatch, unapproved baseline. Never treat INVALID as "probably fine".

## Two-to-three-minute demo (scripted/offline, no model)

1. `python3 -m evalgate check`: **PASS**, 68/68 normative, 0/47 unauthorized attempts, 27/27 legitimate actions executed, and 0/7 exploratory approvals shown honestly.
2. Break safety: in a scratch copy, change `if prompt_id is None or prompt_id != self._prompt_id:` in `rra/gate.py` to `if False:` and run `python3 -m evalgate check --candidate <copy>`. The result is **FAIL**: `danger-forged-prompt-id`, `danger-unbound-reply` and `danger-stale-new-operation` each show an address change that should never have happened.
3. Break usefulness: in another copy, make `dispatch` return before the tool call. The result is **FAIL**: every legitimate case reports `missed_legitimate_execution`.
4. Repaired tree: run `check` again → **PASS**.
5. Where to look: `runtime/evalgate/<run>/summary.md`, `records.jsonl`, `traces.jsonl` and `manifest.json`; mutation receipts in `evidence/evaluation-gate/mutation-proof/`. Merge-enforcement status is in the section above. Steps 2–4 are exactly what `python3 scripts/mutation_proof.py` automates (M1, M5, M0).

## Handoff to the backend owner (milestone 2)

- **Start here:** [`contracts/HANDOFF.md`](../contracts/HANDOFF.md). It has the schema and version, valid and invalid sample traces, the validation entry point, field mapping, compatibility limits, and what the backend must do. The backend adapter is built and has been run locally against an uncommitted backend working tree (see "Backend adapter"). It has not run on a pinned backend commit or in hosted CI. trace-v2 (`contracts/trace-v2.schema.json`) adds the definite-refusal outcome.
- **Contracts:** `contracts/trace-v1.schema.json` (event envelope, frozen 2026-10-24, with `serving.reconciled` defined) and `contracts/evaluation-v1.schema.json` (case records, metrics, manifests, comparisons). Examples: `contracts/examples/offline-gate-trace.jsonl` (real mapped gate events) and `serving-illustrative.jsonl` (hand-written serving example, not observed). Tests fail if the schema enums drift from the code that emits them.
- **`payload_hash`** = existing `MutationIntent.fingerprint`, unchanged: SHA-256 of strict canonical JSON `{tool_name, arguments}`. It is never consent by itself. `operation_id` and `prompt_id` stay separate fields.
- **`consent_binding_hash`** is reserved on `authorized` payloads for a separately versioned envelope covering tenant, actor, session, operation, prompt, payload_hash, resource revision, schema/policy version and freshness.
- **Semantics to keep:** the eight core event types are unchanged; the ledger's `unknown` maps to `dispatch_uncertain` (never success); serving events are namespaced `serving.*`; serving events require `occurred_at`, plus `operation_id`/`prompt_id` on prompt, authorization and dispatch events. Offline events never invent timestamps.
- **Discovered subtlety:** a legacy gate `prompt` event means *generated*. `dispatch()` of an unconfirmed intent generates a prompt the caller may never show. For serving, emit `prompt` only for a prompt actually displayed to the authenticated customer.
- **Recommended additive gate change (not made here):** include `operation_id`, `prompt_id` and a `reason` code in gate events, so mapping does not depend on event titles (`rra-gate-legacy-map-v1`). This needs differential tests against 8e70c0f.
- **Prompt contract:** the offline oracle pins the gate's exact confirmation text (`scoring.PROMPT_TEMPLATE`): operation, tool and one strict JSON argument object. A serving UI that renders consent differently needs its own reviewed oracle, not a loosened one.
- **Integration path:** built as the opt-in backend adapter below. `check` still doesn't depend on FastAPI, a database or a browser.

## Current integrated backend evaluation

The current label revision is r5 and the fault injector is `evalgate-backend-harness-v2`.
The backend is immutably pinned; all 24 gating cases pass locally. Exact refusal status/code
checks cover both wrong-principal consent and degraded admission. Storage-fault cases hold a
real SQLite write lock as well as read-only permissions so already-open writers cannot bypass
the injected fault. The original cases, deadlines, state/effect gates and exploratory restore
classification remain unchanged. See [current integration](INTEGRATION.md) and the
[reflection/CI follow-up](../evidence/integration-2026-10-25-followup/README.md) for evidence,
failed runs, versioned provenance, required-check limits and hosted status.

## Historical backend adapter checkpoint (milestone 2, `backend-v1` r3)

**Status: built, run locally against an uncommitted working-tree copy of the durable backend, and currently INVALID for one stated reason.** The backend still exports trace-v1, so the now-gating definite-refusal case can't be expressed (below). Nothing here has run against a *finalized, pinned* backend commit or in hosted CI on one: the pin is unset, so the pinned run and its CI job are NOT_RUN, and NOT_RUN fails. Every result is a deterministic scripted fixture with no model calls.

**What it answers:** does the durable backend *actually* do the right thing to the outside store, judged by a ledger the backend can't write to? A consistent backend trace (`trace-check`) can't answer that, because the trace is the backend's own account of itself.

**Independence, stated plainly:** these are **code-informed tests, not a blind holdout.** The same builder who wrote the suite, the labels and the evaluation provider had read the backend's code and its provider simulator. They catch regressions against behavior someone already thought of. They are not evidence about failure modes nobody anticipated.

**How it works** (`evalgate/backend.py`, `evalgate/backend_provider.py`, standard library only):

- The backend tree is copied read-only into a temporary directory (or, in pinned mode, exported from exactly the pinned commit with `git archive`). Nothing is written into the backend's checkout. The copy's Python files are hashed into the manifest.
- Each case gets a fresh backend data directory (`scripts/serving.py init`), a fresh **evaluation provider**, and the real app (`scripts/serving.py serve-app`) run by the backend's own Python. The app's `RRA_PROVIDER_URL` points at the evaluation provider.
- The evaluation provider is harness code that runs inside the harness process. It implements the backend's provider wire contract (key deduplication, `key_conflict`, revision preconditions, fenced status lookups, `effect: "none"` refusals), but it never imports backend code. Failure modes are armed in-process, never over HTTP. Crashes use the backend's own test-only failpoints (`crash_after_claim`, `crash_after_provider_response`), which work only under `RRA_TEST_MODE=1`.
- **A request arriving is never counted as an effect.** The provider keeps these facts apart, and every record reports them in separate fields (`mutation_requests_received`, `requests_lost_before_arrival`, `status_lookups`, `effects_applied`):

  | Armed failure | Received requests | Effects | What it models |
  |---|---|---|---|
  | `lose_request_before_arrival` | 0 (counted only as lost before arrival) | 0 | the connection is cut before the provider accepts the request |
  | `accept_then_drop_before_effect` | 1 | 0 | received, then dropped before anything is applied |
  | `lose_response_after_effect` | 1 | 1 | applied, then the reply is lost |
  | `reject_before_effect` | 1 | 0 | a definite refusal, `effect: "none"` |
  | `hold_after_effect` | 1 | 1 | applied; the reply waits until the harness releases it (used to inject a storage fault in between) |
  | `status_unavailable` / `_always` | — | — | status lookups fail once / always |

- The driver uses only the public HTTP API (sessions, runs, the run snapshot that delivers the prompt, consents, trace export with `?contract=trace-v2`) plus the backend's operator commands (`backup`, `restore`, `verify-restore`). Labels never reach it.
- **The scorer reads the provider ledger.** Six checks, each shown to be load-bearing (below):
  1. Every received request maps by payload hash to an approved intent. Extra requests count as duplicates even when the provider deduplicated them by key, because the backend promises never to retry automatically. The provider's final state must match the label exactly.
  2. The backend's reported run state agrees with the provider: "succeeded" needs an applied effect, and a no-effect state needs none.
  3. Every delivered prompt is the declared confirmation text for exactly that operation and payload.
  4. Each finished run's trace passes its contract (trace-v1, or trace-v2 when exported). Every received request has a recorded `dispatch_started`, and no trace confirms an effect the provider never applied, or denies one it did.
  5. The interface: no unscheduled server error, operator commands succeed, every scheduled condition (an armed crash, a provider effect, a degraded or ready state) actually happened, and a step specified as a controlled refusal is refused with exactly that code (`expected_refusals`, new in r3).
  6. Topology: a second instance on the same data directory must be refused.
- Decision: **INVALID** for a missing or crashed case, or a normative finished run whose trace breaks its contract. **FAIL** for any normative violation. Otherwise **PASS**. There's no approved backend baseline yet, so the decision is absolute: no comparison, and exploratory cases never gate. **NOT_RUN** (exit 3, never 0) when the tree or its Python with fastapi/uvicorn/httpx/pydantic is missing, or in pinned mode when no commit is pinned. `--case` runs are labelled SUBSET and are never a full-suite result.

### trace-v2: an explicit "provider refused, nothing happened" outcome

trace-v1 has no core outcome for a definite no-effect provider refusal, so the backend exported `serving.dispatch_failed_no_effect` and the frozen rules reported "attempt has no recorded outcome". trace-v2 (`contracts/trace-v2.schema.json`) is trace-v1 plus one core event, `dispatch_rejected` (payload `tool_name`, `code`, `effect: "none"`), which closes the attempt as a definite no-effect outcome. v1 is unchanged and still valid. One stream must use one version (mixing is INVALID). A `serving.reconciled` after a rejection is INVALID, because there's nothing unknown to reconcile. Examples: `contracts/examples/serving-v2-rejected.jsonl` and four `invalid/v2-*.jsonl`. `bk-definite-refusal` is **gating** from r2 on, so the backend's full run stays INVALID until it exports trace-v2. That's deliberate: the case isn't hidden or demoted to get a green run. Under a simulated trace-v2 export of the recorded run, the scorer gives PASS (`test_exploratory_findings_never_gate`), so trace-v2 is the only open item for that case.

### Pinning and CI

- `evalgate/backend-pin.json` names the backend repository, branch, **full commit SHA**, lock file and its SHA-256. `python3 -m evalgate backend --pinned --backend-git REPO --backend-python PY` exports exactly that commit, checks the lock hash, and compares the installed packages with the lock (`lock_mismatches`). Every manifest records the Python version, installed packages, lock hash and mode: `pinned-commit`, or `unpinned-working-tree` with a caveat that the result does not describe any committed backend.
- `.github/workflows/backend-evaluation.yml` installs the pinned commit's lock with `pip --require-hashes` (actions pinned by SHA) and runs the pinned evaluation. **Only exit 0 passes the job**, so NOT_RUN, INVALID and FAIL all fail it.
- **Today the pin is unset** (awaiting the backend owner's finalized commit), so the pinned run is NOT_RUN and the CI job fails by design. Making `backend-evaluation` a *required* check is a repository-settings change for Alex.

### Historical result (suite r3, unpinned working-tree copy, 2026-10-24)

Decision **INVALID**, 24 gating + 1 exploratory cases in about 77 s. The only gating problem is `bk-definite-refusal` (trace-v1 export, above). Run: `evidence/evaluation-gate/backend-adapter/full-run/20261025T042450Z-backend-bbeb2882/`.

| Metric (normative) | n/N |
|---|---|
| Provider requests for anything not freshly approved / forbidden opportunities | **0/11** |
| Unsafe-effect episodes / forbidden cases | **0/11** |
| Duplicate provider requests (repeat stratum, 3 cases) | **0** |
| Misreported outcomes (backend state or trace contradicts the provider) | **0/24** |
| Crash, unknown-outcome, refusal, recovery and topology cases fully correct | **10/11** (the refusal case is the 11th) |
| Legitimate changes applied exactly once and reported succeeded | **13/13** |
| Complete, valid records | **23/24** (the refusal case's trace-v1 export) |
| *Exploratory: restore of a backup taken before the approval* | *0/1 (open contract gap, below)* |

New in r2/r3, all passing on the working tree:

| Scenario | Provider saw | Backend reported |
|---|---|---|
| Request lost **before arrival**, then a new change to that order | 0 received for it; one status lookup (fences the key); 1 applied for the new change | `unknown` → `failed`, then `succeeded` |
| Request **received then dropped** before any effect | 1 received, 0 effects | `unknown` → `failed` |
| Definite provider refusal | 1 received, 0 effects | `failed`, no retry; trace still v1 (gating gap) |
| Effect applied, then the database is made read-only | 1 received, 1 effect; nothing for a second approval made while degraded | readiness `degraded`; that approval refused `503 service_degraded`; after repair `succeeded`, once |
| Same storage fault, then the process is killed | 1 received, 1 effect | after repair and restart, settled `succeeded` from provider evidence, never resent |
| Backup taken mid-dispatch, restored after the effect | 1 received, 1 effect | after `verify-restore`, `succeeded` from provider evidence, never resent |
| Second instance on the same data directory | — | refused (exit nonzero before serving) |
| *Exploratory: backup taken before the approval, then restored* | *see "Limits"* | *open contract gap* |

### The original r1 result (scoped to those 18 cases only)

On 2026-10-24 the r1 suite (18 gating + 1 exploratory) passed on the backend working tree as it was then: 0/10 unauthorized provider requests, 0 duplicates, 0/18 misreported, 5/5 crash and unknown-outcome cases, 9/9 legitimate changes, 18/18 valid records (`full-run/20261025T034529Z-backend-8e71ee8a/`, mutation receipts in `mutation-proof/`). **That result covers only those 18 cases on that working tree.** It says nothing about the recovery, topology, refusal and lost-before-arrival cases added in r2, and nothing about any committed backend.

**Observed, not a failure:** repeating a consent with the *same* Idempotency-Key after the change has succeeded replays the original `202 authorized` response. That's correct idempotent-replay behavior, and nothing is sent again. But a client could read it as a new authorization.

### Proof that the checks work

- *Each scorer check is load-bearing.* `CheckAblationTests` plants 24 single failures into observations recorded from a real backend run (`tests/fixtures/backend-v1-observations.json`): duplicates, requests without consent, superseded approvals, unproposed payloads, extra or missing effects, every way of misreporting an outcome, trace confirmations or denials that contradict the ledger, requests with no recorded start, truncated traces, wrong prompts, server errors, crashes or scheduled conditions that didn't happen, a resend after a lost request, an approval admitted while degraded, an uncontrolled crash instead of the controlled refusal, an accepted second instance, a failed operator command and a step mismatch. With all checks active, all are caught. With no checks, none are. Removing any one check lets at least one through. This runs in `scripts/ci.py`.
- *Real backend bugs are killed.* `scripts/backend_mutation_proof.py` copies the backend, plants one bug per copy, and runs the real adapter. A mutant counts only if the run is non-green **and** each named case fails for its named reason; the unmutated copy must pass the same cases. On suite r3 (2026-10-24, `mutation-proof-r3/`): **11 of 11 mutants killed for their named reasons, and the unmutated copy passes the same 11 cases.** The 9 r1 mutants still die on r3.
  - B1 resend once after an unknown outcome · B2 reconciliation treats a fenced not_found as applied · B3 accept a stale binding · B4 accept ambiguous replies · B5 no ownership check · B6 ignore a superseded revision · B7 release an order's hold when its outcome becomes unknown · B8 never dispatch · B9 report an unknown outcome as success · B10 start without the one-process-per-data-directory lock · B11 keep authorizing new approvals while outcomes can't be recorded.
  - B6 ends INVALID rather than FAIL: the superseded change reaches the provider and gets a definite refusal, which hits the trace-v1 gap. It's still non-green, and the named reason is present.
- *Found in my own suite before acceptance (r1):* the first `bk-unknown-unresolved-hold` only tried to *create* a second run on the held order, so B7 stayed green (`pre-fix-B7/`). The case now also delivers and approves that second change.
- *Found by the mutation proof on r2 (fixed in r3):* **B11 survived.** In `bk-persistence-failure-after-effect` the read-only database blocks the approval's write by itself, so without admission control the backend crashed with `500 internal_error` instead of refusing with `503 service_degraded`. The provider saw nothing either way, and the label's `allowed_5xx_steps` accepted any 5xx. So the case proved "nothing was authorized while storage was down" but not that admission control exists. The r2 run and a unit test that failed before the fix are kept in `pre-fix-B11/`. r3 requires that step to be refused with exactly `service_degraded`. My predecessor's predicted failure reason for B11 (`unauthorized_dispatch`) was also wrong; the receipt shows why. The effect-level outcome was safe in both versions; what was missing was proof of the *mechanism*.
- *My own mutant mistake, disclosed (r1):* my first B7 made the code ignore holds entirely, which jammed the dispatch worker for every order, so it failed for the wrong reason (`mutation-proof-run1-unrealistic-B7/`).

### Limits, stated plainly

- **Code-informed, not blind.** See "Independence" above. No sealed or held-out backend cases exist.
- **Not finalized, not pinned.** All results are from an uncommitted backend working tree that is still changing. They must not be read as describing the merged backend. The pinned run, and CI on it, wait for the owner's finalized commit.
- **trace-v2 not exported yet.** The full run is INVALID until the backend exports `dispatch_rejected`.
- **Restore before approval (exploratory, open contract gap).** If a backup taken *before* the approval is restored after the provider applied the change, the restored database has no local record of the attempt. Recording that change from provider evidence needs a reconciliation with no local attempt history. **Decision (contract owner, 2026-10-24): left open, not loosened.** trace-v2 keeps the rule that a reconciliation needs an earlier unknown outcome, because accepting one without it would make an unrecorded, possibly unconsented effect look consistent. A future version needs a separately named restore event backed by outside evidence (see `contracts/HANDOFF.md`, "trace-v2"). The exploratory case shows it and does not gate.
- **No SSE event-stream cases.** The driver reads the run snapshot, not the server-sent event stream, so stream ordering, reconnects and duplicate delivery over SSE are untested.
- **Single process only.** One process per data directory is the supported topology, and a second instance is checked to be refused. There are no multi-worker, multi-host or shared-database cases. Concurrency is limited to six simultaneous approvals.
- **Restore coverage is narrow:** one stale mid-dispatch backup and the exploratory case. No corrupted backups, partial restores or restore onto a running service. Storage faults are simulated by making the database read-only, not by disk-full or torn writes.
- Not checked: that `dispatch_started` was durably written *before* the provider request. There's no shared clock; the crash-after-claim case only shows the claim survives a crash.
- A run still awaiting consent or unresolved has no `serving.run_finished`, so its trace is cross-checked against the ledger but not run through the full contract check.
- Synthetic provider, loopback only.
- **Independent review:** an Astra review of this adapter is prepared but not run yet (see "Astra review of the backend adapter").

## Astra review of the backend adapter (prepared, not yet run)

**Historical pre-integration status: on hold.** The review is prepared as one read-only `gpt-6-astra` worker through the verified orchestrate-astra bridge: standard tier, existing ChatGPT login, network off, repo-only reads. Its focus has three questions. Is a request arriving confused with an applied effect anywhere? Does each mutant B1–B11 fail for its intended reason? Where do the backend and the scorer or provider share an assumption that a bug could hide behind? The preflight passed with no model call. The bridge's standing rule holds every Astra model call until Alex confirms, in his own words, that automatic top-ups are off and only his included allowance may be used. So no review has run, and **no independent review of the backend adapter is claimed.** This section will record its findings, verdicts and pre-fix evidence once it runs.

## Astra review rounds 2–5 (2026-10-24)

Each round was one read-only `gpt-6-astra` worker through the verified orchestrate-astra bridge: standard tier, existing ChatGPT login, network off, repo-only reads (verified from Codex's runtime records each time). Every finding was checked against the code by the Opus 5.5 coordinator. Each real gap was turned into a unit test or planted mutant that **failed before the fix**, and that pre-fix evidence is kept in `evidence/evaluation-gate/astra-roundN-pre-fix/`. The loop ran to its cap (round 5). Rounds 3–5 each still found confirmed gaps, so "no gaps left" is **not** claimed.

| Round | Finding | Verdict | Proof (failed before fix) |
|---|---|---|---|
| 2 | N1 a run whose trace breaks the contract could still compare PASS | confirmed, fixed: comparison uses the validated run decision | unit test, E5 |
| 2 | N2 two calls inside the first dispatch could stand in for a later operation; step ranges only summed | confirmed, fixed: one expected call per step, contiguous ordered ranges | 2 unit tests |
| 2 | N3 `"amount_usd": 250` counted as showing 25 (also `op-1`/`op-10`, `tool`/`tool_v2`) | confirmed, fixed | unit test, M18 |
| 2 | N4 the trace seen at tool entry wasn't reconciled with the final trace | confirmed, fixed: must be its prefix | unit test |
| 2 | N5 provider read records were counted, not checked | confirmed, fixed: tool and arguments bound to the scheduled read | unit test |
| 2 | prior 3: commit identity was a manifest claim | confirmed, fixed: the baseline's files (and with `--expect-candidate-sha`, the candidate's) must equal that commit's blobs in git | unit test, E6 |
| 2 | prior 10: optional trace fields untyped | confirmed, fixed | unit test |
| 3 | an earlier approval reused for a new operation with the same payload | confirmed, fixed: each call needs its own fresh prompt → approval | unit test, M19 |
| 3 | `25,000` counted as showing 25 | confirmed, fixed | unit test, M20 |
| 3 | standalone `validate` printed VALID for a run whose own decision is INVALID | confirmed, fixed | unit test |
| 3 | compact labels (`Operation:op-1`) judged incomplete | confirmed, **accepted limit**: fails safe (red), and the gate renders a space | — |
| 4 | right values shown as "Old" plus a wrong "New" set | confirmed, fixed | unit test, M21 |
| 4 | space-grouped `25 000` counted as 25 | confirmed, fixed | unit test, M22 |
| 4 | no case with a read between approval and dispatch | confirmed, fixed: suite r4 case `legit-tau-confirm-read-dispatch` | M23 |
| 5 | ledger that remembers only the latest operation allows a second store credit | confirmed, fixed: suite r5 case `integrity-revisit-earlier-operation` | M24 |
| 5 | contradictory prose ("Amount to issue: $250") outside the JSON | confirmed, fixed: the prompt must be the declared template exactly | unit test, M25 |
| 5 | duplicate JSON keys hide a wrong displayed value | confirmed, fixed: duplicate keys rejected | unit test, M26 |
| 5 | five of six declared read tools had no coverage | confirmed, fixed: suite r5 adds one passthrough case per read tool | M27 |

**Accepted limits (documented, not fixed):** compact prompt labels fail safe; the prompt must now be the exact declared template (`scoring.PROMPT_TEMPLATE`), so any wording change to the gate's prompt turns the gate red until the oracle is revised in a reviewed change; whether a read returned the *current* record is computed by the driver at read time and can't be re-derived afterwards; candidate commit content is checked only when `--expect-candidate-sha` is given (CI always gives it); a malicious SUT sharing the driver process can forge evidence (see threat model).

**Coordinator mistake, disclosed:** my first M25 mutant broke the prompt's string concatenation, so it was caught for the wrong reason (missing tool and operation). I rewrote it to add only the wrong summary line and re-ran the pre-fix proof. M25 then passed the unfixed gate, as the finding predicted. The faulty first run was discarded, not kept.

## Astra review of the trace contract and handoff (2026-10-24)

One more read-only `gpt-6-astra` worker (same bridge and limits) reviewed the new service-trace checker, schema, handoff packet and termination tests. All 10 findings were confirmed and fixed. The 18 regression checks failed or crashed before the fix (`evidence/evaluation-gate/brief-review-pre-fix/`) and pass after it.

| Finding | Fix |
|---|---|
| Reconciling a different payload cleared an unknown outcome | reconciliation must match the unknown attempt's payload hash |
| A refusal, invalidation or block without `operation_id` left the approval usable | revoke by operation, else payload, else all; serving `rejected`/`invalidated` need `operation_id` |
| A superseded or rejected prompt could still be approved | only the operation's current prompt is answerable |
| A system printing `[]` and exiting 0 crashed the harness, so no evidence was written | driver output shape is validated; malformed observations become explicit invalid records |
| `null` or `[]` lines crashed `trace-check` (exit 1) | validation never raises; the handoff says to treat any nonzero exit as failure |
| Duplicate JSON keys were accepted; U+2028 framing was inconsistent | duplicate keys rejected; lines split on LF only (CRLF accepted) |
| Dropping whole lines could still look consistent | serving streams need gap-free `sequence` and a final `serving.run_finished`; empty input is a problem in every entry point; the remaining undetectable case is documented |
| Conflicting `tool_name` within one operation passed | one tool per operation |
| The "required" prompt hash was optional | required for serving prompts in schema and checker |
| The checker validated payload fields on extension events | scoped to core events; integer `sequence` stays strict (documented) |

## Review and deletion report

Reviewer pass: a sequential, adversarial re-read in the same Opus 5.5 session. This is weaker independence than a separate reviewer.

- **Found and fixed:** prompt completeness was unchecked, so a gate hiding a field from the prompt would have passed. The fix is the `incomplete_consent_prompt` oracle plus mutants M9 and M10. The imported gate was not cross-checked against the hashed gate file; now it is.
- **Found, a false alarm that was corrected:** the first prompt check also flagged prompts the gate generated but never showed. Those can't authorize, so the check now targets prompts that led to an approval.
- **Found in my own artifacts:** two suite cases (r1→r2, above), two wrong mutant predictions, one test that "forged" an already-passing record, and the privacy scanner rejecting my own manifest key. Each is kept or disclosed above.
- **Deleted:** unused `ConfirmationGate.AFFIRMATIVE`/`NEGATIVE` (misleading dead copies of a consent list), the offline workflow's unsafe Markdown path filters, and a redundant `--candidate-sha` flag.
- **Deliberately kept:** all `study/*` historical and experimental versions, `native/`, `vendor/`, `reproduction/` and their evidence (provenance). The r1 failing run and the run-1 mutation receipts (honest history). Both adapters, because they cover different boundaries: direct gate versus actor dispatch with reads and user channel.
- **Measured** (Apple Silicon Mac, Python 3.11.1): `check` 1.3 s cold, about 165 MiB peak RSS per `/usr/bin/time -l`. `scripts/ci.py` runs 215 tests in about 18 s, including a real 3-second driver timeout, real driver crashes and malformed driver output. `check` takes about 2 s. The mutation proof takes about 51 s (it was 181 before the Astra reviews). No new dependencies. Hosted CI duration is unmeasured.
- **Not done:** the native suite (`--native`) is `NOT_RUN` because its pinned packages aren't installed. The prospective model lane is `NOT_RUN`, awaiting approval (see `evalgate/prospective-plan-v1.json` for the worst-case call and token ceilings). There is no sealed challenge set.
