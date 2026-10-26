# Release readiness — 2026-10-25

The user authorized merge and deployment when ready, limited to the existing portfolio/demo/test
scope. The engineering baseline and all failed receipts remain preserved. This authorization does
not establish production suitability or authorize new infrastructure, spending, accounts, credentials,
security changes, real customer effects, or source-branch deletion.

## Merge acceptance

- Dependency PR #1 (`86caf2e`) merged with a normal merge commit `484e4e10a02db1ca849c19f4f8e88f40cd7f77ba`.
- [PR #2](https://github.com/alexmunzon/reliable-retail-agent/pull/2) merged normally as `3856c89241bf9471358628052db1b3c0b7c528d9`; all three post-merge workflows passed. The remaining-work branch `chatgpt/retail-restore-evidence` uses that merged baseline; no unmerged stack dependency remains.
- Before merge, require no conflicts or unresolved review requests, and passing `test`, `evaluation-gate` and `backend-evaluation` checks for the release candidate. No review submissions or threads existed at preflight; internal review shares model/context and is not an external audit.
- Main's existing rules require `evaluation-gate` and `test`. Backend evaluation is additionally checked for this release; repository settings are unchanged.
- Recheck all three workflows at the resulting main merge commit. A pre-merge receipt does not establish post-merge execution.
- Source branches and prior commits remain preserved; automatic branch deletion is disabled.

The current immutable runtime pin is `503e95445fa09849fbfe22a589b23df295618fd3`. The prior pin
`0dd21e0` and failed receipts remain historical. Trace-v1/v2 and backend-v1 cases/labels/policy are
unchanged; trace-v3 is separately versioned. See [completion](RESTORE_COMPLETION.md) for acceptance.

## Deployment discovery and decision

No established remote Retail deployment target was found. The repository has only test workflows,
no cloud deployment manifest or project linkage. GitHub deployment history is empty and Pages is
not configured. Accessible Vercel project listings and repository/name searches contained no Retail
project. After the user completed official team SSO, the previously denied read was retried once
and succeeded: all eight authorized-team projects were listed, none matched Retail, and repository
and name filters were empty. The final exact-repository lookup was also empty. No suitable other
connected hosting target is established. Historical storage warnings do not prove current capacity,
and no current no-cost capacity or billing allowance has been established.

The supported targets remain local synthetic demos: the original loopback app, and a single-process
serving pilot with SQLite on local disk. Proxy, TLS and multi-worker operation are untested. A static
host cannot run this backend. Do not invent a cloud destination or reinterpret a local smoke test
as a hosted deployment.

**Needed decision:** name an existing no-cost VM or persistent container host, its persistent local
disk allocation, and intended private/public access. It must run one Python3.11 service process plus
the synthetic provider, support long-lived SSE, SQLite WAL/locking and durable backups, and offer a
confirmed allowance that does not require spending. A private loopback-only demo reached through an
already-authorized access path is the closest fit. An existing Vercel static/serverless frontend alone
cannot satisfy this local-disk backend contract; do not choose it merely to claim hosted.

Public access would additionally require an approved hostname/TLS/access design and explicit changes
to the current loopback Host/Origin guards, followed by end-to-end verification. Do not rewrite proxy
headers to evade those guards. If no existing suitable host is available, keep the supported local
demo/video, or obtain separate approval for new infrastructure and its costs/exposure. Deployment
remains NOT_RUN: no public service URL, deployment artifact or hosted health result is claimed.

## Verification and rollback path

For a future identified target, deploy an exact tested commit in synthetic safe mode; verify artifact
identity, readiness, unauthenticated rejection, fresh exact consent, one provider-ledger effect,
read-only dashboard/trace output, and unchanged real-world systems. Preserve the prior artifact/data
snapshot and use the host's supported rollback. Do not discard ambiguous claims or holds by restoring
stale data; follow [the restore limitations](SERVING.md#backup-and-restore).

If a repository regression is found after merge, use a normal revert PR against the recorded merge
commit, rerun checks and preserve history. Do not reset/force-push or delete source branches. No service was deployed during this release work; deployment remains NOT_RUN. No customer-system
effect is authorized.
