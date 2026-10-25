# Evaluation gate (milestone 1)

**Status: `CI_EXECUTED`** on draft PR [#1](https://github.com/alexmunzon/reliable-retail-agent/pull/1), head `311b2dd`: hosted [`evaluation-gate`](https://github.com/alexmunzon/reliable-retail-agent/actions/runs/40155951054) and [`test`](https://github.com/alexmunzon/reliable-retail-agent/actions/runs/40155950952) both passed. Not `MERGE_ENFORCED` (no required-check rule has been configured). All results below are deterministic scripted fixtures with no model calls.

## The claim, and its limits

Given trusted orchestration, registered simulated mutation tools, and the declared consent policy, **regressions in exact-action authorization and in successful legitimate dispatch are detected automatically before merge.**

It does **not** decide business-policy correctness, authenticate callers, stop a privileged maintainer from removing controls, provide durable or crash-safe recovery, or measure model behavior. The suite was written by the same builder who read the gate code. It is a **builder-authored regression suite**, not an independent held-out benchmark, and independent held-out validation remains **unestablished**.

## Commands

```bash
python3 -m evalgate check        # the gate: approved baseline 8e70c0f vs this tree. Exit 0 PASS, 1 FAIL, 2 INVALID
python3 -m evalgate run          # evaluate one tree (--sut PATH, --role baseline|candidate)
python3 -m evalgate compare BASELINE_RUN CANDIDATE_RUN
python3 -m evalgate validate RUN_DIR   # re-score raw evidence; never trusts stored verdicts
python3 -m evalgate trace-check TRACE.jsonl  # check a service-generated trace-v1 file: CONSISTENT or INVALID, never PASS
python3 -m evalgate coverage     # invariant coverage matrix
python3 scripts/mutation_proof.py      # plant curated regressions in throwaway copies and run the real gate
python3 scripts/ci.py            # all offline unit tests, including the harness's own negative controls
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
- **Hosted (`CI_EXECUTED`):** on draft PR #1, the first run (head `f81a207`) passed `evaluation-gate` but failed `test`: its shallow checkout lacked the approved baseline commit, which the unit tests export by SHA. `offline-tests.yml` now fetches full history (`311b2dd`), and both checks passed on that head. No test or assertion was changed.
- **To reach `MERGE_ENFORCED`:** Alex adds a branch ruleset or protection on `main` requiring the status checks `evaluation-gate` and `test`. Optionally add CODEOWNERS for `evalgate/`, `contracts/` and `.github/`. I have not changed any repository settings.

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

- **Start here:** [`contracts/HANDOFF.md`](../contracts/HANDOFF.md). It has the schema and version, valid and invalid sample traces, the validation entry point, field mapping, compatibility limits, and what the backend must do. Integration is **not** built or tested.
- **Contracts:** `contracts/trace-v1.schema.json` (event envelope, frozen 2026-10-24, with `serving.reconciled` defined) and `contracts/evaluation-v1.schema.json` (case records, metrics, manifests, comparisons). Examples: `contracts/examples/offline-gate-trace.jsonl` (real mapped gate events) and `serving-illustrative.jsonl` (hand-written serving example, not observed). Tests fail if the schema enums drift from the code that emits them.
- **`payload_hash`** = existing `MutationIntent.fingerprint`, unchanged: SHA-256 of strict canonical JSON `{tool_name, arguments}`. It is never consent by itself. `operation_id` and `prompt_id` stay separate fields.
- **`consent_binding_hash`** is reserved on `authorized` payloads for a separately versioned envelope covering tenant, actor, session, operation, prompt, payload_hash, resource revision, schema/policy version and freshness.
- **Semantics to keep:** the eight core event types are unchanged; the ledger's `unknown` maps to `dispatch_uncertain` (never success); serving events are namespaced `serving.*`; serving events require `occurred_at`, plus `operation_id`/`prompt_id` on prompt, authorization and dispatch events. Offline events never invent timestamps.
- **Discovered subtlety:** a legacy gate `prompt` event means *generated*. `dispatch()` of an unconfirmed intent generates a prompt the caller may never show. For serving, emit `prompt` only for a prompt actually displayed to the authenticated customer.
- **Recommended additive gate change (not made here):** include `operation_id`, `prompt_id` and a `reason` code in gate events, so mapping does not depend on event titles (`rra-gate-legacy-map-v1`). This needs differential tests against 8e70c0f.
- **Prompt contract:** the offline oracle pins the gate's exact confirmation text (`scoring.PROMPT_TEMPLATE`): operation, tool and one strict JSON argument object. A serving UI that renders consent differently needs its own reviewed oracle, not a loosened one.
- **Integration path:** add a `backend` adapter to `evalgate/driver.py` that drives the service through its API, with the same simulator, labels and scorer, plus new durability/concurrency families. Do not make `check` depend on FastAPI, a database or a browser.

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
