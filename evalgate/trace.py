"""Map raw rra.gate events into contracts/trace-v1 and validate envelope and semantics.

The JSON Schema file is the envelope contract; this module is a dependency-free
check of it plus the ordering rules the schema cannot express (check_semantics).
The same checks apply to offline traces and to service-generated traces
(`python3 -m evalgate trace-check`). tests/test_evalgate.py keeps the enums in sync.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

from .scoring import payload_hash

MAPPING_VERSION = "rra-gate-legacy-map-v1"
CORE_EVENT_TYPES = ("prompt", "invalidated", "authorized", "blocked", "rejected",
                    "dispatch_started", "dispatch_succeeded", "dispatch_uncertain")
# trace-v2 (contracts/trace-v2.schema.json) is trace-v1 plus one core outcome: the provider definitively refused
# the attempt and nothing happened. trace-v1 events are checked exactly as before; a stream uses one version.
SCHEMA_VERSIONS = ("trace-v1", "trace-v2")
V2_CORE_EVENT_TYPES = CORE_EVENT_TYPES + ("dispatch_rejected",)
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
NEEDS_HASH = {"prompt", "invalidated", "authorized", "rejected", "dispatch_started", "dispatch_succeeded", "dispatch_uncertain",
              "dispatch_rejected"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SHA = re.compile(r"[0-9a-f]{64}")
_NAMESPACED = re.compile(r"(serving|eval|transport)\.[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*")
_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z")
_VERSION = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_TOOL = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")
SOURCE_COMPONENTS = ("rra.gate", "adapters.tau_dispatch", "eval.simulator", "eval.harness", "backend.service")
SOURCE_KEYS = {"component", "mapping_version", "raw_type", "raw_sequence", "raw_title"}
RECONCILED_OUTCOMES = ("effect_confirmed", "no_effect_confirmed", "still_unknown")
OUTCOME_TYPES = ("dispatch_succeeded", "dispatch_uncertain", "dispatch_rejected")
INVALIDATED_REASONS = ("payload_or_operation_changed", "policy_version_changed", "expired", "revoked", "rejected", "other")
# Allowed payload fields per core event type (mirrors the schema's $defs).
PAYLOAD_KEYS = {"prompt": {"tool_name", "prompt_sha256"}, "invalidated": {"reason"}, "rejected": set(),
                "authorized": {"classifier_version", "consent_binding_hash", "consent_binding_version"}, "blocked": {"reason"},
                "dispatch_started": {"tool_name", "error_class"}, "dispatch_succeeded": {"tool_name", "error_class"},
                "dispatch_uncertain": {"tool_name", "error_class"}, "dispatch_rejected": {"tool_name", "code", "effect"}}


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
    if event["schema_version"] not in SCHEMA_VERSIONS:
        problems.append("unknown schema_version")
    core = V2_CORE_EVENT_TYPES if event["schema_version"] == "trace-v2" else CORE_EVENT_TYPES
    for key in ("event_id", "session_id", "run_id", "trace_id", "operation_id", "prompt_id"):
        if key in event and not (isinstance(event[key], str) and _ID.fullmatch(event[key])):
            problems.append(f"invalid {key}")
    if type(event["sequence"]) is not int or event["sequence"] < 1:
        problems.append("invalid sequence")
    kind = event["event_type"]
    if kind not in core and not (isinstance(kind, str) and _NAMESPACED.fullmatch(kind)):
        problems.append(f"unknown event_type {kind!r}")
        kind = ""
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
    if serving and kind in ("rejected", "invalidated") and "operation_id" not in event:
        problems.append(f"serving {kind} events require operation_id")
    if serving and kind == "prompt" and "prompt_sha256" not in (event["payload"] if isinstance(event["payload"], dict) else {}):
        problems.append("serving prompt events require payload.prompt_sha256 (hash of the exact text shown)")
    if serving and kind.startswith("dispatch_") and kind in core and (
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
    if "tool_name" in payload and kind in core and not (isinstance(payload["tool_name"], str) and _TOOL.fullmatch(payload["tool_name"])):
        problems.append("invalid payload.tool_name")
    for key in ("prompt_sha256", "consent_binding_hash"):
        if kind in core and key in payload and not (isinstance(payload[key], str) and _SHA.fullmatch(payload[key])):
            problems.append(f"invalid payload.{key}")
    if kind == "blocked" and payload.get("reason") not in BLOCKED_REASONS:
        problems.append("blocked event requires a known reason")
    if kind == "invalidated" and payload.get("reason") not in INVALIDATED_REASONS:
        problems.append("invalidated event requires a known reason")
    if kind == "dispatch_rejected" and (payload.get("effect") != "none" or not isinstance(payload.get("code"), str)
                                        or not 1 <= len(payload["code"]) <= 96):
        problems.append('dispatch_rejected requires payload.effect "none" and a payload.code of 1-96 characters')
    if kind == "authorized" and not isinstance(payload.get("classifier_version"), str):
        problems.append("authorized event requires a string classifier_version")
    if kind == "serving.reconciled":
        if not {"operation_id", "payload_hash"} <= set(event):
            problems.append("serving.reconciled requires operation_id and payload_hash")
        if set(payload) - {"outcome", "by"} or payload.get("outcome") not in RECONCILED_OUTCOMES or not _short_string(payload, "by", 64):
            problems.append(f"serving.reconciled requires payload.outcome in {list(RECONCILED_OUTCOMES)} and at most a short payload.by")
    for key, limit in (("classifier_version", 96), ("consent_binding_version", 64), ("error_class", 96)):
        if kind in core and not _short_string(payload, key, limit):
            problems.append(f"invalid payload.{key}")
    return problems


def _short_string(container: dict, key: str, limit: int) -> bool:
    """An optional field, when present, must be a string of at most `limit` characters."""
    return key not in container or (isinstance(container[key], str) and len(container[key]) <= limit)


def check_semantics(events: list[dict]) -> list[str]:
    """Ordering rules per (run_id, trace_id) stream, keyed by operation_id (or payload_hash for legacy events):
    an attempt needs a fresh authorization of the same payload answering the operation's current prompt; a refusal,
    invalidation or block revokes approval (by operation, else by payload, else all); an operation has one attempt
    and one tool; every attempt has exactly one outcome; no attempt starts while an earlier outcome is unknown and
    unreconciled; reconciliation only resolves an unknown outcome of the same payload. Serving streams number their
    events 1..N without gaps and end with serving.run_finished. Call with envelope-valid events only."""
    problems, streams = [], {}
    for event in events:
        streams.setdefault((event["run_id"], event["trace_id"]), []).append(event)
    for stream in streams.values():
        current, consent, open_attempts, attempted, unknown, tools = {}, {}, {}, set(), {}, {}
        for event in stream:
            kind, operation, hashed = event["event_type"], event.get("operation_id"), event.get("payload_hash")
            key, where, prompt_id = operation or hashed, event["event_id"], event.get("prompt_id")
            tool = event["payload"].get("tool_name") if kind in V2_CORE_EVENT_TYPES else None
            if tool and key:
                if tools.setdefault(key, tool) != tool:
                    problems.append(f"{where}: tool {tool} differs from {tools[key]} for the same operation")
            if kind == "prompt":
                current[key], _ = (prompt_id, hashed), consent.pop(key, None)
            elif kind in ("invalidated", "rejected", "blocked"):
                if operation:
                    revoked = [operation]
                elif hashed:
                    revoked = [k for k, h in consent.items() if h == hashed] + [k for k, (_, h) in current.items() if h == hashed]
                else:
                    revoked = list(consent)  # a block naming neither operation nor payload revokes every approval
                for k in revoked:
                    consent.pop(k, None)
                    if kind != "blocked":  # a refused or invalidated prompt needs a fresh one; a block keeps it answerable
                        current.pop(k, None)
            elif kind == "authorized":
                if current.get(key) != (prompt_id, hashed):
                    problems.append(f"{where}: authorization does not answer the operation's current prompt for the same payload")
                consent[key] = hashed
            elif kind == "dispatch_started":
                if any(unknown.values()):
                    problems.append(f"{where}: attempt started while an earlier outcome is unknown and unreconciled")
                if operation and operation in attempted:
                    problems.append(f"{where}: second attempt for operation {operation}")
                if consent.get(key) != hashed:
                    problems.append(f"{where}: attempt without a fresh authorization of the same payload")
                if key in open_attempts:
                    problems.append(f"{where}: attempt started before the previous attempt's outcome was recorded")
                consent.pop(key, None)
                current.pop(key, None)
                open_attempts[key] = hashed
                if operation:
                    attempted.add(operation)
            elif kind in OUTCOME_TYPES:
                if open_attempts.pop(key, None) != hashed:
                    problems.append(f"{where}: outcome without a matching started attempt")
                if kind == "dispatch_uncertain":
                    unknown[key] = hashed
            elif kind == "serving.reconciled":
                if not unknown.get(key) or unknown[key] != hashed:
                    problems.append(f"{where}: reconciliation of an outcome that was not unknown for this operation and payload")
                elif event["payload"].get("outcome") != "still_unknown":
                    unknown[key] = None
        problems += [f"{key}: attempt has no recorded outcome (missing or truncated evidence)" for key in open_attempts]
        if len({e["schema_version"] for e in stream}) > 1:
            problems.append(f"{stream[0]['trace_id']}: one stream mixes trace contract versions")
        if any(e["evidence_class"] == "serving_observation" for e in stream):
            if [e["sequence"] for e in stream] != list(range(1, len(stream) + 1)):
                problems.append(f"{stream[0]['trace_id']}: sequence is not 1..N without gaps (dropped or reordered events)")
            finished = [i for i, e in enumerate(stream) if e["event_type"] == "serving.run_finished"]
            if finished != [len(stream) - 1]:
                problems.append(f"{stream[0]['trace_id']}: stream must end with exactly one serving.run_finished (truncated evidence?)")
    return problems


def _unique_object(pairs):
    if len({key for key, _ in pairs}) != len(pairs):
        raise ValueError("duplicate key")
    return dict(pairs)


def _reject_constant(constant):
    raise ValueError(f"non-finite number {constant}")


def check_file(path: Path) -> tuple[list[str], list]:
    """Service-trace entry point: a JSON Lines file of trace-v1 events (LF or CRLF line ends).
    Never raises and never returns a PASS verdict; any problem means INVALID."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        return [f"trace file unreadable: {error}"], []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    events, problems = [], []
    for number, line in enumerate(lines, 1):
        try:
            events.append(json.loads(line.removesuffix("\r"), object_pairs_hook=_unique_object, parse_constant=_reject_constant))
        except (ValueError, RecursionError):
            problems.append(f"line {number} is not one complete JSON value without duplicate keys (truncated or corrupted)")
    return problems + validate_trace(events), events


def validate_trace(events: list) -> list[str]:
    """Envelope and ordering checks for any list of JSON values; never raises. Empty input is a problem."""
    if not events:
        return ["no events: missing evidence is never consistent"]
    problems, seen, last, valid = [], set(), {}, []
    for index, event in enumerate(events, 1):
        own = validate_event(event)
        problems += [f"event {index} ({event.get('event_id') if isinstance(event, dict) and isinstance(event.get('event_id'), str) else '?'}): {p}"
                     for p in own]
        if own:
            continue
        valid.append(event)
        if event["event_id"] in seen:
            problems.append(f"duplicate event_id {event['event_id']}")
        seen.add(event["event_id"])
        key = (event["run_id"], event["trace_id"])
        if event["sequence"] <= last.get(key, 0):
            problems.append(f"{event['event_id']}: sequence is not strictly increasing")
        last[key] = event["sequence"]
    return problems + (check_semantics(valid) if not problems else [])
