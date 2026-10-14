# Independent zero-cost reproduction review

Reviewer assignment: Sol, 2026-10-14. Reviewed new reproduction schema, inventory, manifest, runner, observer, CLI and metric modules; their tests, evidence and runbooks. No provider requests, credential/environment-file access for this reproduction review, native source edits, runtime ledger access or original result overwrite were performed.

## Verdict and scope

Clear as offline source/plumbing verification plus a separate retrospective author-artifact analysis. It is not a complete historical paper reproduction. The default entry point is a content-bound dry plan; its only commands are plan, inventory and offline. Paid execution is absent. Unknown historical settings are recorded as blocked rather than filled with convenient values. The primary source is a verified paper-era candidate, explicitly not a proven snapshot of the unpublished executed experiments.

The full original paper-result grid, method/policy comparisons, high-repeat reliability curves and failure annotations remain unreproduced. Public later trajectories cannot fill those gaps. The actual model catalog evidence records visibility/inaccessibility for specific labels and explicitly says generation capability was not tested; it does not prove successful inference or historical equivalence. No generation request was made by this reviewer.

## Original-code fixture checks

Independently ran the final 15 original-runner/metric tests in a fixed minimal environment without provider credentials: all passed. The original-code suite executes nine fresh-process fixtures: original function calling, ReAct and Act-only, each with success, required-output failure and 30-action cutoff. Construction/completions are mocked and sockets are blocked. Worker subprocesses receive only fixed PATH and bytecode-control settings, not the parent's credentials. Primary 70-file source validation remains enforced.

The original factory constructs the author classes. ReAct retains its original prompt and six-line responses; Act-only now explicitly checks the original Act prompt and exercises four-line Action/Arguments responses without Thought. Actor temperature zero and native simulator temperature one/max_tokens 150 are preserved against zero-token stubs. Hidden synthetic instruction canaries do not enter actor requests. This is specified-fixture plumbing evidence, not a general hidden-data defense or model evaluation.

Original BaseEnv and scorer execute unchanged. Terminal success/output-failure cases return native reward one/zero; the 30-action cutoff remains nonterminal without forced scoring. The observer captures two real fixture actor dispatches before terminal reference replay adds a separate third action. Pre-score counter state is retained. The actual original run function is also exercised for an ordinary act exception recorded as reward zero, an external BaseException that propagates instead of becoming zero, and isolated setup failure outside the act catch. These author semantics remain separate from prior project infrastructure censors.

## Later author artifacts and metrics

All four trajectory files independently matched the later pinned source manifest. Across 1,980 records, independently recomputed exact rational pass^k = mean C(c,k)/C(n,k) and distinct pass@k = 1 − mean C(n−c,k)/C(n,k) agreed with every reported value. Task/trial denominators are complete: GPT-4o retail 115×4, airline 50×4; later Sonnet release retail 115×8, airline 50×8. Equal-domain averages give half weight to each domain. The report labels strategy/configuration provenance as unspecified author release and distinguishes these files from original paper-v1 logs and fresh provider experiments.

Two input-validation gaps found during review were repaired by the metrics owner: exact integer task/trial IDs now reject bool/float aliases, and the build function binds all four file hashes to independently verified fixed official Git bytes before emitting an official-release provenance claim. Regression tests reject malformed identities and a tampered same-shape file. Reported metric values did not change. Seven metric tests pass independently.

Portable snapshots verify the full primary source against its bundled manifest and the four later files against their recorded hashes without copying upstream Git metadata or the unrelated later runtime. Bundled digests provide internal provenance consistency, not independent signatures or provider authenticity. The runbooks preserve that limitation, synthetic-versus-model labels, original-source boundaries and missing-paper-result status.

No remaining concrete blocker was found for these offline deliverables. A claim that the entire paper has been reproduced would contradict the delivered evidence and remains unsupported.
