"""Versioned projection of the durable outbox into frozen trace-v1/trace-v2.

Trace-v1 retains its original meanings. Trace-v2 adds a definite no-effect refusal.
The immutable contract/checker revision below is validated by HTTP effect-ledger tests.
Exports contain only hashes, identifiers and bounded reason codes. A prompt event attests
that the server prepared an authenticated response, not that a human viewed it.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any

TRACE_V1_COMMIT = "11407c97c334d622df01f3de25792c5a045b34ea"
TRACE_V1_SCHEMA_BLOB = "23b620cb151711fbb94c6233ca79b1629ce77ea2"  # git blob of contracts/trace-v1.schema.json there
MAPPING_VERSION = "backend-outbox-map-v1"

# Provisional reason codes -> the contract's enums. Anything unlisted maps to the contract's "other"
# (invalidated) and keeps its raw code in source.raw_title, so nothing is silently relabelled.
BLOCKED_REASONS = {"response_not_bound_to_active_prompt": "stale_consent", "ambiguous_response": "ambiguous_response"}
INVALIDATED_REASONS = {"authorization_expired": "expired", "credential_revoked": "revoked"}
RECONCILED_OUTCOMES = {"succeeded": "effect_confirmed", "failed_no_effect": "no_effect_confirmed"}
DISPATCH_TYPES = ("dispatch_started", "dispatch_succeeded", "dispatch_uncertain")


def to_trace(event: dict[str, Any], contract: str = "trace-v1") -> dict[str, Any]:
    """Map an outbox envelope; unsupported contracts fail before producing evidence."""
    if contract not in ("trace-v1", "trace-v2"):
        raise ValueError("unsupported trace contract")
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
        if contract == "trace-v2":
            mapped_kind = "dispatch_rejected"
            mapped = {"tool_name": "change_order_address", "code": str(payload["code"])[:96], "effect": "none"}
        else:
            mapped = {"code": payload.get("code")}
    # rejected and the remaining serving.* events export an empty payload: their meaning is the type.
    source = {"component": "backend.service", "mapping_version": MAPPING_VERSION if contract == "trace-v1" else "backend-outbox-map-v2", "raw_type": kind}
    if title:
        source["raw_title"] = (f"{title} [{raw_reason}]" if raw_reason else title)[:128]
    result = {"schema_version": contract, "event_id": event["event_id"], "sequence": event["sequence"],
              "event_type": mapped_kind, "session_id": event["session_id"], "run_id": event["run_id"],
              "trace_id": event["trace_id"], "occurred_at": event["occurred_at"], "source": source,
              "evidence_class": "serving_observation", "payload": mapped}
    for key in ("operation_id", "payload_hash", "prompt_id"):
        if event.get(key) is not None:
            result[key] = event[key]
    return result


def to_trace_v1(event: dict[str, Any]) -> dict[str, Any]:
    """Compatibility entry point retaining the original v1 projection."""
    return to_trace(event, "trace-v1")
