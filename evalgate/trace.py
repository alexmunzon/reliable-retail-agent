"""Map raw rra.gate events into contracts/trace-v1 and validate the envelope.

The JSON Schema file is the contract; this module is a dependency-free check of
the subset evaluation emits. tests/test_evalgate.py keeps the enums in sync.
"""
from __future__ import annotations

import re

from .scoring import payload_hash

MAPPING_VERSION = "rra-gate-legacy-map-v1"
CORE_EVENT_TYPES = ("prompt", "invalidated", "authorized", "blocked", "rejected",
                    "dispatch_started", "dispatch_succeeded", "dispatch_uncertain")
BLOCKED_REASONS = ("unknown_tool", "stale_consent", "ambiguous_response", "unconfirmed_dispatch", "repeat_dispatch",
                   "halted_after_uncertain", "wrong_source", "unclassified_tool", "unmapped_legacy_reason")
EVIDENCE_CLASSES = ("scripted_fixture", "synthetic_simulator", "serving_observation",
                    "retrospective_author_trajectory", "prospective_model")
# Legacy gate events encode blocked reasons only in their title (8e70c0f).
LEGACY_BLOCKED_TITLES = {
    "Unknown tool blocked": "unknown_tool", "Stale consent blocked": "stale_consent",
    "Ambiguous response blocked": "ambiguous_response", "Unconfirmed dispatch blocked": "unconfirmed_dispatch",
    "Repeat dispatch blocked": "repeat_dispatch", "Uncertain outcome stops further mutation": "halted_after_uncertain",
}
ENVELOPE_KEYS = {"schema_version", "event_id", "sequence", "event_type", "session_id", "run_id", "trace_id", "operation_id",
                 "payload_hash", "prompt_id", "occurred_at", "source", "evidence_class", "payload"}
REQUIRED_KEYS = {"schema_version", "event_id", "sequence", "event_type", "session_id", "run_id", "trace_id",
                 "source", "evidence_class", "payload"}
NEEDS_HASH = {"prompt", "invalidated", "authorized", "rejected", "dispatch_started", "dispatch_succeeded", "dispatch_uncertain"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SHA = re.compile(r"[0-9a-f]{64}")
_NAMESPACED = re.compile(r"(serving|eval|transport)\.[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z")
_VERSION = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_TOOL = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
SOURCE_COMPONENTS = ("rra.gate", "adapters.tau_dispatch", "eval.simulator", "eval.harness", "backend.service")
SOURCE_KEYS = {"component", "mapping_version", "raw_type", "raw_sequence", "raw_title"}
INVALIDATED_REASONS = ("payload_or_operation_changed", "policy_version_changed", "expired", "revoked", "rejected", "other")
# Allowed payload fields per core event type (mirrors the schema's $defs).
PAYLOAD_KEYS = {"prompt": {"tool_name", "prompt_sha256"}, "invalidated": {"reason"}, "rejected": set(),
                "authorized": {"classifier_version", "consent_binding_hash", "consent_binding_version"}, "blocked": {"reason"},
                "dispatch_started": {"tool_name", "error_class"}, "dispatch_succeeded": {"tool_name", "error_class"},
                "dispatch_uncertain": {"tool_name", "error_class"}}


def _payload(event: dict, classifier_version: str) -> dict:
    kind = event["type"]
    if kind == "blocked":
        return {"reason": LEGACY_BLOCKED_TITLES.get(event.get("title"), "unmapped_legacy_reason")}
    if kind == "invalidated":
        return {"reason": "payload_or_operation_changed"}
    if kind == "authorized":
        return {"classifier_version": classifier_version}
    return {}


def map_case(run_id: str, case_id: str, observation: dict, classifier_version: str) -> list[dict]:
    """Gate events keep their order; simulator invocations follow as eval.* events."""
    events, base = [], {"schema_version": "trace-v1", "session_id": case_id, "run_id": run_id, "trace_id": case_id,
                        "evidence_class": "scripted_fixture"}
    for raw in observation.get("raw", {}).get("gate_events", []):
        event = {**base, "event_id": f"{run_id}:{case_id}:g{raw['seq']}", "sequence": len(events) + 1,
                 "event_type": raw["type"] if raw["type"] in CORE_EVENT_TYPES else f"eval.legacy_{raw['type']}",
                 "source": {"component": "rra.gate", "mapping_version": MAPPING_VERSION, "raw_type": raw["type"],
                            "raw_sequence": raw["seq"], "raw_title": str(raw.get("title", ""))[:128]},
                 "payload": _payload(raw, classifier_version)}
        if raw.get("fingerprint"):
            event["payload_hash"] = raw["fingerprint"]
        events.append(event)
    for index, item in enumerate(observation.get("invocations", []), 1):
        events.append({**base, "event_id": f"{run_id}:{case_id}:s{index}", "sequence": len(events) + 1,
                       "event_type": "eval.tool_invoked", "payload_hash": payload_hash(item["tool"], item["arguments"]),
                       "source": {"component": "eval.simulator", "mapping_version": MAPPING_VERSION},
                       "payload": {"tool_name": item["tool"], "effect_applied": item["effect_applied"], "outcome": item["outcome"]}})
    return events


def validate_event(event: dict) -> list[str]:
    problems = []
    if not isinstance(event, dict):
        return ["event is not an object"]
    if extra := set(event) - ENVELOPE_KEYS:
        problems.append(f"disallowed fields {sorted(extra)}")
    if missing := REQUIRED_KEYS - set(event):
        problems.append(f"missing fields {sorted(missing)}")
        return problems
    if event["schema_version"] != "trace-v1":
        problems.append("unknown schema_version")
    for key in ("event_id", "session_id", "run_id", "trace_id", "operation_id", "prompt_id"):
        if key in event and not (isinstance(event[key], str) and _ID.fullmatch(event[key])):
            problems.append(f"invalid {key}")
    if type(event["sequence"]) is not int or event["sequence"] < 1:
        problems.append("invalid sequence")
    kind = event["event_type"]
    if kind not in CORE_EVENT_TYPES and not (isinstance(kind, str) and _NAMESPACED.fullmatch(kind)):
        problems.append(f"unknown event_type {kind!r}")
    if "payload_hash" in event and not (isinstance(event["payload_hash"], str) and _SHA.fullmatch(event["payload_hash"])):
        problems.append("invalid payload_hash")
    if kind in NEEDS_HASH and "payload_hash" not in event:
        problems.append(f"{kind} requires payload_hash")
    if event["evidence_class"] not in EVIDENCE_CLASSES:
        problems.append("unknown evidence_class")
    if "occurred_at" in event and not (isinstance(event["occurred_at"], str) and _TIME.fullmatch(event["occurred_at"])):
        problems.append("occurred_at must be a UTC timestamp ending in Z")
    serving = event["evidence_class"] == "serving_observation"
    if serving and "occurred_at" not in event:
        problems.append("serving events require occurred_at")
    if serving and kind in ("prompt", "authorized") and not {"operation_id", "prompt_id"} <= set(event):
        problems.append(f"serving {kind} events require operation_id and prompt_id")
    if serving and kind.startswith("dispatch_") and kind in CORE_EVENT_TYPES and (
            "operation_id" not in event or "tool_name" not in (event["payload"] if isinstance(event["payload"], dict) else {})):
        problems.append(f"serving {kind} events require operation_id and payload.tool_name")
    source = event["source"]
    if (not isinstance(source, dict) or not {"component", "mapping_version"} <= set(source) or set(source) - SOURCE_KEYS
            or source.get("component") not in SOURCE_COMPONENTS
            or not (isinstance(source.get("mapping_version"), str) and _VERSION.fullmatch(source["mapping_version"]))
            or not _short_string(source, "raw_type", 64) or not _short_string(source, "raw_title", 128)
            or ("raw_sequence" in source and not (type(source["raw_sequence"]) is int and source["raw_sequence"] >= 1))):
        problems.append("invalid source")
    payload = event["payload"]
    if not isinstance(payload, dict):
        problems.append("payload is not an object")
        return problems
    if kind in PAYLOAD_KEYS and set(payload) - PAYLOAD_KEYS[kind]:
        problems.append(f"{kind} payload has disallowed fields {sorted(set(payload) - PAYLOAD_KEYS[kind])}")
    if "tool_name" in payload and kind in CORE_EVENT_TYPES and not (isinstance(payload["tool_name"], str) and _TOOL.fullmatch(payload["tool_name"])):
        problems.append("invalid payload.tool_name")
    for key in ("prompt_sha256", "consent_binding_hash"):
        if key in payload and not (isinstance(payload[key], str) and _SHA.fullmatch(payload[key])):
            problems.append(f"invalid payload.{key}")
    if kind == "blocked" and payload.get("reason") not in BLOCKED_REASONS:
        problems.append("blocked event requires a known reason")
    if kind == "invalidated" and payload.get("reason") not in INVALIDATED_REASONS:
        problems.append("invalidated event requires a known reason")
    if kind == "authorized" and not isinstance(payload.get("classifier_version"), str):
        problems.append("authorized event requires a string classifier_version")
    for key, limit in (("classifier_version", 96), ("consent_binding_version", 64), ("error_class", 96)):
        if kind in CORE_EVENT_TYPES and not _short_string(payload, key, limit):
            problems.append(f"invalid payload.{key}")
    return problems


def _short_string(container: dict, key: str, limit: int) -> bool:
    """An optional field, when present, must be a string of at most `limit` characters."""
    return key not in container or (isinstance(container[key], str) and len(container[key]) <= limit)


def validate_trace(events: list[dict]) -> list[str]:
    problems, seen, last = [], set(), {}
    for event in events:
        problems += [f"{event.get('event_id')}: {p}" for p in validate_event(event)]
        if event.get("event_id") in seen:
            problems.append(f"duplicate event_id {event.get('event_id')}")
        seen.add(event.get("event_id"))
        run, sequence = event.get("run_id"), event.get("sequence")
        key = (run, event.get("trace_id"))
        if isinstance(sequence, int) and sequence <= last.get(key, 0):
            problems.append(f"{event.get('event_id')}: sequence is not strictly increasing")
        last[key] = sequence if isinstance(sequence, int) else last.get(key, 0)
    return problems
