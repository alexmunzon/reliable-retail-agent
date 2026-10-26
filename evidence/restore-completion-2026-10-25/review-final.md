# Bounded restore-evidence final review

UTC: 2026-10-25T23:42:58.799625+00:00
Branch: `chatgpt/retail-restore-evidence`; base HEAD: `3856c89241bf9471358628052db1b3c0b7c528d9`. Review is of the current uncommitted working tree, identified below, not a claim that these changes are committed or released.

## Decision

The three previously confirmed actionable findings are fixed in the reviewed source and focused pure checks. No additional blocking finding emerged in this bounded final pass. Full HTTP, crash, browser, mutant and hosted release verification remains the owner's responsibility; this receipt does not replace it.

## Original findings and confirmation

These are transcriptions of the reviewer's earlier tool-observed probes against the implementation before the fixes, not new executions against that earlier tree:

1. P1 legacy namespaced projection: a trace-v2 `serving.restore_observed` event with sentinel strings in `snapshot_sha256` and `effect_id` passed `validate_trace` and `redact_event` exported those strings. A dictionary-valued `by` caused `TypeError: expected string or bytes-like object, got 'dict'`.
2. P2 stream transplant: `check_recovery` returned PASS when all run/session/trace identifiers were changed to WRONG values while the expected fixture retained correct identities.
3. P2 malformed container: `check_recovery(None, effects, expected)` raised `TypeError: 'NoneType' object is not iterable` rather than returning INVALID.

The old exploratory stale-restore failure is separate historical evidence and must remain unchanged. Its original missing consent/dispatch history is not repaired by these new observations.

## Fixes inspected and checks actually run

- `observability/project.py` now treats the new restore event as opaque namespaced data outside trace-v3. Its label, exported payload and tool projection are gated by version. Frozen v1/v2 validation is unchanged.
- `evalgate/recovery.py` checks list/dict container shape before iteration, and binds every event's run/session/trace identity to the retained fixture.
- `tests_serving/test_restore_evidence.py` captures original stream identity before restore and adds transplanted-stream, fabricated-authorization and later-dispatch regressions.
- The snapshot expectation now comes from an independently created SQLite backup and SHA-256 before restore; it is asserted equal to implementation metadata. This addresses the earlier circular expected-hash test design. The reviewer inspected this code but did not execute its HTTP test.

Command executed: `python3 -B -m unittest tests.test_restore_observability -v`
Result: **2 tests passed in 0.001s**, exit 0. The legacy test covers both trace-v1 and trace-v2 with string/dictionary/integer `by` payloads and sentinel private strings; the other test covers malformed containers and empty evidence.

Additional in-memory probes (exit 0):

- Valid recovery observation with matching independent fixture/ledger: PASS.
- Run ID transplant: INVALID.
- Session ID transplant: INVALID.
- Trace ID transplant: INVALID.
- Null event container: INVALID without exception.

No files in the repository were edited by this reviewer. No network, credentials, provider processes, customer data, deployments or remote mutations were used. The only file write is this requested local review receipt.

## Preserved interpretation and limits

A runtime recovery event is the backend's account of a provider observation. Its checksum establishes consistency, not cryptographic attestation of the provider or original consent. The independent provider SQLite ledger remains the evaluation oracle. Missing attempt/consumption records stay missing. A v3 observation does not authorize later dispatch; legacy v1/v2 exports retain their failure boundary.

Review shares the parent model, context and filesystem. It is neither a sealed holdout nor an external audit. New tests/probes are exposed regression evidence. This pass checked the requested fixes and consequences only; it makes no new performance, hosting, production reliability or complete audit-history claim.

## Reviewed source hashes

- `evalgate/recovery.py`: `8440175acef3835fbd6325fc00f4a04a8d1e5de59f81a9662b1cc66e19dd8078`
- `evalgate/trace.py`: `0712ae496de02c85ef4d60a98aa3cd171ce6568c10410ee01111c0cf2519ebb8`
- `observability/project.py`: `af746e725f7bdb3f4f92eb5aec1ffb357d98aa338dfd06c1e762b62c98fd71bc`
- `tests/test_restore_observability.py`: `40c3723fdc12bed4fb83563a3d3b59bb881eaecd651ceee898f9adde0ee54805`
- `tests_serving/test_restore_evidence.py`: `ce2f7d37b81ee818815262abca838178e1b2daed6771d8667b8a1397a6870fa6`
- `scripts/serving.py`: `230f34e83df5a02ef11363c2c58cb3e4b359e449415e675d0ad1f623d8133111`
- `serving/service.py`: `48edc2960b5e535811c22ba65cb68ca027816b69f68101856d010fe4d382eed3`
- `serving/streaming.py`: `0e3168072b6a88f7dc1134f45c7ef3c56d7778cdec5bc1572751cbe784274a0d`
- `serving/trace_export.py`: `a4cd88dd99ff9890e5154f431b7ad7d76ddbdde852d1428ea9c4a640560f593d`
- `contracts/trace-v3.schema.json`: `6e8c5802bb58108d4e99acb077e8d2fdff91e790f1d3789a589587def69f1dc1`
