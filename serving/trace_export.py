"""Thin adapter: provisional serving outbox events -> the evaluation owner's trace-v1 envelope.

The contract is owned by the evaluation-gate branch: `contracts/trace-v1.schema.json` at
TRACE_V1_COMMIT, titled "PROPOSAL, not frozen". This module only maps fields; it never defines,
extends or loosens that schema. Compatibility is evidenced by running the owner's own checker
(`evalgate.trace.validate_trace` at the pinned commit: envelope, enums, sequence) on traces exported
through the HTTP API, in tests_serving/test_trace_contract.py; it is not asserted here.

Exported traces carry hashes and reason codes only: never prompt text, tool arguments, provider
keys or addresses (the contract's privacy rule).

Known contract gap (reported to the owner, not papered over): trace-v1 has no core outcome for a
definite provider no-effect failure. Such an attempt is exported as `serving.dispatch_failed_no_effect`;
the owner's uncommitted draft ordering rules would report the attempt as having no recorded outcome.

`prompt` means the exact text was delivered to the authenticated owner's client (recorded in the same
transaction that reads it out; consent is refused before it). That is the closest a server can attest to
the handoff's "actually displayed".
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any

TRACE_V1_COMMIT = "09565cd65c4908c19d218d0b062fbafc723edf99"
TRACE_V1_SCHEMA_BLOB = "bcd37d55b37bf408e560960602d7796ff000fe6c"  # git blob of contracts/trace-v1.schema.json there
MAPPING_VERSION = "backend-outbox-map-v1"

# Provisional reason codes -> the contract's enums. Anything unlisted maps to the contract's "other"
# (invalidated) and keeps its raw code in source.raw_title, so nothing is silently relabelled.
BLOCKED_REASONS = {"response_not_bound_to_active_prompt": "stale_consent", "ambiguous_response": "ambiguous_response"}
INVALIDATED_REASONS = {"authorization_expired": "expired", "credential_revoked": "revoked"}
RECONCILED_OUTCOMES = {"succeeded": "effect_confirmed", "failed_no_effect": "no_effect_confirmed"}
DISPATCH_TYPES = ("dispatch_started", "dispatch_succeeded", "dispatch_uncertain")


def to_trace_v1(event: dict[str, Any]) -> dict[str, Any]:
    """Map one provisional envelope (as stored in the outbox) to one trace-v1 event."""
    kind, payload = event["event_type"], event.get("payload") or {}
    raw_reason = payload.get("reason") or payload.get("code")
    title = str(payload.get("title", ""))
    mapped_kind, mapped = kind, {}
    if kind == "prompt":
        mapped = {"tool_name": payload["tool_name"], "prompt_sha256": sha256(payload["prompt"].encode("utf-8")).hexdigest()}
    elif kind == "authorized":
        mapped = {"classifier_version": payload["classifier_version"], "consent_binding_hash": payload["consent_binding_hash"],
                  "consent_binding_version": payload["consent_binding_version"]}
    elif kind == "invalidated":
        mapped = {"reason": INVALIDATED_REASONS.get(raw_reason, "other")}
    elif kind == "blocked":
        if raw_reason == "resource_on_hold":
            # The contract's reason means "an earlier outcome is unknown". A hold whose operation is
            # still in flight is a different fact, so it is exported as a namespaced event instead.
            if payload.get("hold_state") == "unknown":
                mapped = {"reason": "halted_after_uncertain"}
            else:
                mapped_kind, mapped = "serving.consent_blocked", {"reason": "resource_busy"}
        else:
            mapped = {"reason": BLOCKED_REASONS.get(raw_reason, "unmapped_legacy_reason")}
    elif kind in DISPATCH_TYPES:
        mapped = {"tool_name": payload["tool_name"]}
        if kind == "dispatch_uncertain":
            mapped["error_class"] = str(payload.get("code", "unknown"))[:96]
    elif kind == "serving.reconciled":
        mapped = {"outcome": RECONCILED_OUTCOMES[payload["resolved_state"]], "by": payload["method"]}
    elif kind == "serving.reconciliation_unavailable":
        mapped_kind, mapped = "serving.reconciled", {"outcome": "still_unknown", "by": payload["method"]}
    elif kind == "serving.dispatch_failed_no_effect":
        mapped = {"code": payload.get("code")}
    # rejected and the remaining serving.* events export an empty payload: their meaning is the type.
    source = {"component": "backend.service", "mapping_version": MAPPING_VERSION, "raw_type": kind}
    if title:
        source["raw_title"] = (f"{title} [{raw_reason}]" if raw_reason else title)[:128]
    result = {"schema_version": "trace-v1", "event_id": event["event_id"], "sequence": event["sequence"],
              "event_type": mapped_kind, "session_id": event["session_id"], "run_id": event["run_id"],
              "trace_id": event["trace_id"], "occurred_at": event["occurred_at"], "source": source,
              "evidence_class": "serving_observation", "payload": mapped}
    for key in ("operation_id", "payload_hash", "prompt_id"):
        if event.get(key) is not None:
            result[key] = event[key]
    return result
