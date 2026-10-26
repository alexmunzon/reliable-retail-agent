# Trace-v3: effects observed after stale restore

This additive version implements the separately named restore observation anticipated by the
frozen trace-v2 handoff. Trace-v1/v2 schemas, meanings and ordering checks are unchanged. Their
legacy missing-history export still fails honestly; the exposed backend-v1 exploratory case and
its original receipts have not been edited or relabeled.

`GET /api/v1/runs/{run_id}/trace?contract=trace-v3` explicitly requests v3. The compatibility default
remains v1. The authenticated live dashboard requests v3. Ordinary v3 events follow v2 rules.

## New observation and its limits

`serving.restore_observed` means the backend received a typed provider status response reporting
an effect for this operation after restoring a backup that lacks its local dispatch record.
It is not a dispatch, authorization, reconstructed approval, or independent execution attestation.
No consumed timestamp or attempt count is synthesized. A retained approval may still appear in a
backup taken after consent; an approval missing from an earlier backup remains missing.

The envelope binds run, session, trace, operation and payload hash, and records real observation
time. Its closed payload contains:

| Field | Meaning |
|---|---|
| `restore_id` | Unique identifier generated for this restore, retained through repeated verification |
| `snapshot_sha256` | SHA-256 of the consistent staged SQLite backup, before restore metadata/current credentials are carried into it; includes committed source WAL content |
| `provider_key_sha256` | Hash of the operation's provider idempotency key; the raw key is not exported |
| `provider_receipt_sha256` | Hash of canonical `{effect_id, revision}` received from the provider |
| `effect_id`, `provider_revision` | Positive typed provider receipt values |
| `outcome` | Always `effect_confirmed`: an observed provider claim, not proof of local consent |
| `by` | Always `restore_provider_status_lookup`; absent-key lookups fence keys |
| `history` | Always `missing_after_restore`; never complete reconstructed history |

The trace checker validates fields, hashes, retained proposal binding and ordering. A duplicate or
contradictory observation is invalid. A recovered operation may not dispatch again. A restore
observation consumes any retained authorization for trace-ordering purposes and grants none.
`trace-check` reports structural CONSISTENT/INVALID, never PASS, and discloses incomplete audit
history. Hashes establish identity/consistency, not trusted execution or cryptographic attestation.
A provider status receipt alone does not attest original tenant/payload/consent; the independent
comparison below checks the separately held effect ledger.

The runtime persists the observation atomically with settlement. Unknown/malformed/unsupported
status leaves global restore verification incomplete and writes disabled. Partial verification
can resume without duplicating settled observations. Repeated verification is idempotent.
Operations absent entirely from a backup cannot be reconstructed. No distributed exactly-once,
complete history recovery, model evaluation or production reliability claim is added.

## Independent comparison and provenance

`evalgate.recovery.check_recovery` uses `restore-evaluation-v1` to compare a v3 trace with a caller-
supplied independent synthetic provider SQLite effect ledger and a separately retained fixture.
It checks run/session/trace/operation identity, declared payload, tenant/order/address, provider
request digest, effect ID/revision, restore generation/fingerprint and counts of retained local
history. Missing, malformed or contradictory input returns INVALID. A PASS applies only to this
synthetic comparison and explicitly reports `local_authorization_attested: false`.

HTTP tests preserve the provider's original effect transaction and separately count incoming
mutation requests. They independently hash a separate consistent SQLite snapshot before restore.
Forged provenance, wrong identity/payload/ledger, fabricated approval, duplicate observation and
redispatch cases are exposed regressions. They are not untouched holdout or a real-world failure
rate. The old backend-v1 case continues to exercise its original frozen v2 expectations.

## Interrupted clients and presentation

SSE transport cursors gain a restore-generation suffix after restore. Business event IDs and
legacy trace exports remain unchanged. Any cursor from an earlier generation receives explicit
HTTP410 `replay_expired`, including a sequence number reused in restored history. The client
fetches a fresh owned snapshot, clears old displayed events and resumes only the current history.
A generation mismatch never authorizes an action. Ordinary same-generation replay retains its
existing ordering and retention behavior.

The dashboard distinguishes an effect observed after restore from recorded mutation attempts.
Its evidence remains incomplete, with zero recorded attempts interpreted as missing history,
not proof of no effect. Optional timing and billing remain unknown where measurements are absent.
