# Reliable retail agent: shared coordinator instructions (2026-10-25)

This filename is retained for compatibility. The active development host is ChatGPT/Astra,
with one user-facing session owning evaluation, durable serving and observability. Earlier
Claude sessions remain historical records; their conversation histories were not migrated.
Continue from the preserved source and handoffs, never from an invented reconstruction.

The integration branch is `claude/backend-adapter-6b82f6`; its legacy name does not designate
an active Claude writer. Previous writers remain retired. Explicit user approval superseded
the prior local-integration denial: the dashboard history and preserved backend source have
been integrated locally. Original source branches/worktrees remain intact. Consult
[project state](docs/PROJECT_STATE.md) for verification status; a merge is not a test receipt.
Read `contracts/HANDOFF.md`, the trace-v2 decision records, `docs/SERVING.md`, and the dated
hold history in `docs/observability/INTEGRATION_HOLD.md`.

## Product graph and invariants
The runtime is deterministic consent and durable execution, not an LLM-agent graph.
`request -> authenticated principal/ownership -> proposal -> exact delivered prompt -> fresh consent -> queued -> durable claim transaction -> provider attempt -> succeeded | definite-no-effect failure | unknown`.
Reject/expire/cancel before claim are distinct exits. After claim, cancellation is only a request; do not claim the effect was undone. `unknown -> bounded read-only reconciliation -> confirmed effect | confirmed no effect | unknown with resource hold`. Never automatically redispatch an uncertain mutation. Request idempotency, local claim deduplication and provider deduplication are distinct guarantees. SSE replay and telemetry never grant authorization.
Approval binds actor, tenant, credential epoch, session, operation, resource revision, exact payload/hash, prompt/version and policy/classifier versions. Revalidate inside the claim transaction before provider I/O. Backups do not restore revoked credentials. Restores stay read-only until safely verified; missing pre-snapshot history must not be invented.
Completion-record retries are bounded and never repeat dispatch. Preserve configured retry budgets and shutdown deadlines. An unrecordable outcome opens the degraded circuit and retains the durable claim/hold. Reconciliation exhaustion stays held, not success or zero cost.
Evaluation graph: frozen cases + independent effects oracle -> actor -> trace validation -> scoring -> baseline/mutants -> evidence integrity -> explicit gate result. Missing pin/evidence is NOT_RUN/INVALID, never PASS.
Observability graph: immutable trace + versioned sidecar -> validate/limit/redact/deduplicate -> projection -> measured durations/unknowns -> measured usage, estimates and confirmed receipts separately -> read-only report. No subtraction across clock domains; no fabricated costs; attempts are not effects.

## Contracts and integration

The project coordinator is the sole shared-contract and integration owner. Preserve trace-v1
and trace-v2 as explicitly versioned frozen contracts. Serving defaults to the v1 compatibility
export; consumers needing a definite no-effect dispatch outcome explicitly request trace-v2.
Never reinterpret v1 or turn incomplete restored history into a valid trace by inventing events.
Explicit trace-v3 restore observations follow [RESTORE_EVIDENCE.md](contracts/RESTORE_EVIDENCE.md):
provider-observed effects and missing local history stay distinct; legacy exports and cases stay frozen.
Restore-before-approval history remains an exploratory limitation. Freeze and test the exact
backend revision before pinning it; a mutable path is not a pinned CI result.

The authenticated dashboard adapter is a read-only projection of the durable outbox plus
optional process-local provider spans. The sidecar never replaces the business audit ledger.
Missing, expired, evicted or conflicting measurements remain incomplete/unknown. Do not infer
model tokens, billing or cross-process durations. Fixture exports and live synthetic provider
observations must retain distinct provenance and scopes.

## Verification and honest status

Run `scripts/ci.py`, `scripts/ci_serving.py`, trace-contract tests, the backend evaluation with
a verified immutable pin, and the applicable curated mutation proofs. Run only local synthetic
or offline lanes. Validate current credentials and scopes, idempotency, crash windows, restore,
unknown outcomes, actor/tenant isolation and optional-telemetry failure isolation. A passing
component suite does not establish an integrated result. Preserve failures; never weaken a gate
or promote exposed regression cases back to untouched holdout status. Record exact commands,
revision/dirty state, test counts, skips, artifacts and limitations. Distinguish local verification,
hosted CI execution and required-check enforcement.

## Development orchestration (not product runtime)

Use one user-facing ChatGPT project session with scoped internal agents, graphs and bounded
loops. The requested model configuration is `gpt-6-astra`, Ultra reasoning, Standard speed;
verify settings where exposed and do not claim unavailable speed controls were applied. Model
and host instructions from an older handoff are historical when superseded by the user.
Assign each internal worker a concrete question, bounded scope, stop condition and file ownership.
Use separate worktrees for parallel writes. Reviewers may share model/context/filesystem access;
disclose that limitation rather than claiming sealed or independently collected validation.

Preserve existing included-allowance usage only: no API billing, purchased credits, top-ups,
paid fallback or credential/security/network expansion. Stop on a genuine resource or authority
blocker. Use normal permission mechanisms; do not bypass a denial. A login or zero credit balance
does not establish an included-only spending cap. Do not reactivate old sessions or create peer
coordinator relay chains.

Development loop: reproduce -> minimal fix -> targeted regression -> relevant full suite ->
independent review -> reconcile evidence -> reflection. Reassess a review scope after two rounds;
fix in-scope unsupported claims, unnecessary code, performance and failure-handling gaps before
completion. Do not add product agents/frameworks merely to imitate the development graph.

No unattended foreground browser/UI automation; prefer existing offline/headless verification.
Feature-branch pushes to verified existing user remotes and PR updates are authorized after
tests and secrets/unrelated-data scans. The 2026-10-25 release authorization also permits normal
GitHub merges after exact-head checks, stack and review verification, preserving source branches
and existing protections. Deployment is limited to an identified, already-established authorized
portfolio/demo/test target with synthetic data and safe modes; no such remote Retail target is
currently verified. See [release readiness](docs/RELEASE_READINESS.md). Visibility/settings changes,
branch/worktree deletion, destructive history rewriting, real customer data and external business
mutations remain prohibited. Local test credential generation/rotation exercises the synthetic
pilot only; it does not authorize changes to external accounts.

The deferred [post-build reflection](docs/POST_BUILD_REFLECTION.md) records the current bounded improvement pass. Provider parsing failures preserve durable uncertainty; saved backend receipts use explicitly versioned validators. Keep historical receipts with their original source/pin.

## Session start and stop

Read these instructions, the project state, relevant handoffs and Git status. Verify the target
branch/tree, quiescence of previous writers and preservation snapshot before writing. Never reset,
clean, overwrite or remove another worktree's changes. Keep evidence append-only. Before stopping,
record implemented/tested/planned/NOT_RUN/blocked work, exact commands/results, current commit and
dirty state, remaining blockers and next actions. Preserve original prompts, histories, independent
effect oracles and first-exposure results locally without publishing private coordination details.
