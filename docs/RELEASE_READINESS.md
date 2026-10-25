# Release readiness — 2026-10-25

The user authorized merge and deployment when ready, limited to the existing portfolio/demo/test
scope. The engineering baseline and all failed receipts remain preserved. This authorization does
not establish production suitability or authorize new infrastructure, spending, accounts, credentials,
security changes, real customer effects, or source-branch deletion.

## Merge acceptance

- Dependency PR #1 (`86caf2e`) merged with a normal merge commit `484e4e10a02db1ca849c19f4f8e88f40cd7f77ba`.
- [PR #2](https://github.com/alexmunzon/reliable-retail-agent/pull/2) now targets `main`; inspect its exact current head and merge result there.
- Before merge, require no conflicts or unresolved review requests, and passing `test`, `evaluation-gate` and `backend-evaluation` checks for the release candidate. No review submissions or threads existed at preflight; internal review shares model/context and is not an external audit.
- Main's existing rules require `evaluation-gate` and `test`. Backend evaluation is additionally checked for this release; repository settings are unchanged.
- Recheck all three workflows at the resulting main merge commit. A pre-merge receipt does not establish post-merge execution.
- Source branches and prior commits remain preserved; automatic branch deletion is disabled.

The immutable runtime pin remains `0dd21e0676c1ef306069edfffa6a1ec399fc994d`. Release documentation
changes do not modify runtime, frozen cases, labels, contracts, or earlier evidence. See
[post-build reflection](POST_BUILD_REFLECTION.md) for measured acceptance and limitations.

## Deployment discovery and decision

No established remote Retail deployment target was found. The repository has only test workflows,
no cloud deployment manifest or project linkage. GitHub deployment history is empty and Pages is
not configured. Accessible Vercel project listings and repository/name searches contained no Retail
project. A further team-scoped listing was denied for missing authorization and was not retried;
this limits exhaustive discovery. No access expansion was performed. Historical storage warnings
do not prove current capacity, and no new capacity or billing allowance has been established.

The supported targets remain local synthetic demos: the original loopback app, and a single-process
serving pilot with SQLite on local disk. Proxy, TLS and multi-worker operation are untested. A static
host cannot run this backend. Do not invent a cloud destination or reinterpret a local smoke test
as a hosted deployment.

**Needed decision:** identify an existing authorized Retail demo/test host or project. If none exists,
new hosting and its topology/cost/exposure require separate authorization. Deployment remains NOT_RUN,
with no public service URL, deployment artifact, hosted health check or deployment rollback yet.

## Verification and rollback path

For a future identified target, deploy an exact tested commit in synthetic safe mode; verify artifact
identity, readiness, unauthenticated rejection, fresh exact consent, one provider-ledger effect,
read-only dashboard/trace output, and unchanged real-world systems. Preserve the prior artifact/data
snapshot and use the host's supported rollback. Do not discard ambiguous claims or holds by restoring
stale data; follow [the restore limitations](SERVING.md#backup-and-restore).

If a repository regression is found after merge, use a normal revert PR against the recorded merge
commit, rerun checks and preserve history. Do not reset/force-push or delete source branches. No service was deployed during this release work; deployment remains NOT_RUN. No customer-system
effect is authorized.
