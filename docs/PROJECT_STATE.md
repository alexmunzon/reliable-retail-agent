# Retail integration state — 2026-10-25

One ChatGPT/Astra project session now owns the evaluation adapter, durable serving backend and
trace latency/cost dashboard. The branch remains `claude/backend-adapter-6b82f6`. Earlier Claude
writers were verified quiescent before integration; they remain retired. Original conversation
history was not migrated. The migration handoff, complete original prompts and preserved local
checkpoints supply continuity.

## Preservation and authorization history

The original local dashboard merge was denied under the earlier no-merge rule and did not execute.
The user subsequently explicitly approved that exact local integration while preserving the
source branches and without a GitHub merge. A fresh snapshot verified that the source bytes still
matched the migration checkpoint. The normal Git merge then succeeded; no alternate operation
was used to evade the denial.

Preserved source identities:

- Evaluation: `86caf2e18f54a574ac7be826e2625a169e016bfa`.
- Dashboard: `a43026103f5bfc8dafe3bfe3f8415e64afe928e6`.
- Backend base: `8e70c0f9c674399fcdbd7892e33a8432bc1c4e96`, plus the preserved staged implementation.
- Adapter before integration: `11407c97c334d622df01f3de25792c5a045b34ea`.

The dashboard's Git history was merged and the preserved staged backend source was committed as
`c0afb816d68fe2b3367c0457ffc271d163a0f793`. Subsequent isolated review fixes were merged from
`af6a9d7e0f9b51e44f280f2ba89625ed93522d91` and `3d043f5`. Original branches/worktrees, uncommitted
drafts, source archives and earlier failure evidence remain intact. The local recovery inventory
contains their paths and hashes; private coordination records are not public product evidence.

## Current implementation and verification

See [integration and claims](INTEGRATION.md) for the implementation boundaries and current receipts.
The historical dashboard hold is retained in [INTEGRATION_HOLD.md](observability/INTEGRATION_HOLD.md).
It describes the pre-integration checkpoint, not the current adapter.

The three-track integration is locally verified. Runtime source and the immutable backend pin are
`87970608892a0d130a18f9c6bc31bdfcf0e9e471`. Final results: 338 offline tests, 91 serving/crash tests,
8 native-author checks, 24/24 pinned backend gating cases, 33 core/evidence mutants, 11 backend
mutants, 28 headless UI checks and 22 fresh-environment HTTP checks passed. Paired synthetic
performance runs met the declared diagnostic budgets. See the linked evidence for scope and hashes.

The authorized feature branch and existing draft PR #2 are the publication target. Check that PR's
exact head and checks for current hosted status. Main requires the existing `evaluation-gate` and
`test` checks, but backend evaluation is not required; the stacked base has no applicable rules.
No repository settings, remote merge or deployment is authorized by this checkpoint.

Remaining scope boundaries:

- Restore-before-approval can recover provider evidence without reconstructing missing local
  consent/attempt history. It stays exploratory; traces must disclose missing evidence.
- The live dashboard measures provider-call spans only. Spans are process-local, bounded and
  expire; list/aggregate views cover the newest 100 owned runs, with explicit sample disclosure.
- No real-model evaluation, real billing, production traffic, public deployment or remote merge.
- Exposed challenges and new defect reproductions are regression evidence, not untouched holdout.
- Required-check enforcement must be inspected for the relevant target; a workflow file or local
  pass alone does not establish merge blocking.

Tests and secrets/unrelated-data scans must precede each authorized feature-branch push/draft PR update. Preserve all holds
above and all unrelated work. Continue through remaining verification or report a concrete blocker;
do not stop merely because internal reviewers were launched.
