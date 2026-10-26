"""Versioned, exposed restore-evidence checks; never an authorization attestation.

Only tests/offline callers supply the independent provider ledger. Runtime traces merely
record provider observations; the backend never reads this oracle.
"""
from hashlib import sha256
import json
import re

VERSION = "restore-evaluation-v1"
FIELDS = {"outcome", "by", "history", "restore_id", "snapshot_sha256", "provider_key_sha256",
          "provider_receipt_sha256", "effect_id", "provider_revision"}


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                             allow_nan=False).encode()).hexdigest()


def validate_observation(event):
    p = event.get("payload")
    if not isinstance(p, dict) or set(p) != FIELDS:
        return ["restore observation requires exactly its versioned evidence fields"]
    problems = []
    if (event.get("evidence_class") != "serving_observation"
            or not isinstance(event.get("source"), dict) or event["source"].get("component") != "backend.service"
            or not {"operation_id", "payload_hash", "occurred_at"} <= set(event)):
        problems.append("restore observation requires serving identity, payload binding and observation time")
    if p["outcome"] != "effect_confirmed" or p["by"] != "restore_provider_status_lookup" or p["history"] != "missing_after_restore":
        problems.append("restore observation must disclose external effect and missing local history")
    for field, size in (("restore_id", 32), ("snapshot_sha256", 64), ("provider_key_sha256", 64),
                        ("provider_receipt_sha256", 64)):
        if not isinstance(p[field], str) or re.fullmatch("[0-9a-f]{" + str(size) + "}", p[field]) is None:
            problems.append("invalid restore observation " + field)
    for field in ("effect_id", "provider_revision"):
        if type(p[field]) is not int or p[field] < 1:
            problems.append("invalid restore observation " + field)
    if (all(type(p[f]) is int and p[f] > 0 for f in ("effect_id", "provider_revision"))
            and p["provider_receipt_sha256"] != digest({"effect_id": p["effect_id"], "revision": p["provider_revision"]})):
        problems.append("restore provider receipt digest mismatch")
    operation = event.get("operation_id")
    if not isinstance(operation, str) or p["provider_key_sha256"] != sha256(("rra-" + operation).encode()).hexdigest():
        problems.append("restore provider key does not bind this operation")
    return problems


def check_recovery(events, provider_effects, expected):
    """Compare with independently read SQLite effects and a pre-verification fixture.

    expected declares tenant/action/revision and retained restore identity independently
    of the emitted observation. Passing establishes this synthetic recovery comparison,
    not missing original consent or dispatch history.
    """
    from .trace import validate_trace
    if not isinstance(events, list) or not isinstance(provider_effects, list) or not isinstance(expected, dict):
        return {"schema_version": VERSION, "verdict": "INVALID", "problems": ["malformed recovery input containers"],
                "local_authorization_attested": False, "evidence_provenance": "exposed_regression"}
    problems = list(validate_trace(events))
    observations = [e for e in events if isinstance(e, dict) and e.get("event_type") == "serving.restore_observed"]
    if len(observations) != 1:
        problems.append("expected exactly one restore observation")
    elif not problems:
        e, p = observations[0], observations[0]["payload"]
        try:
            for field in ("run_id", "session_id", "trace_id"):
                if any(item.get(field) != expected[field] for item in events):
                    problems.append("recovery stream identity differs from fixture: " + field)
            if e["schema_version"] != "trace-v3":
                problems.append("recovery comparison requires trace-v3")
            if e["operation_id"] != expected["operation_id"] or e["payload_hash"] != digest(
                    {"tool_name": expected["tool_name"], "arguments": expected["arguments"]}):
                problems.append("restore operation/payload differs from declared fixture")
            for field in ("restore_id", "snapshot_sha256"):
                if p[field] != expected[field]:
                    problems.append("restore provenance differs from retained " + field)
            for kind, field in (("authorized", "retained_authorizations"), ("dispatch_started", "retained_dispatches")):
                if sum(e.get("event_type") == kind for e in events) != expected[field]:
                    problems.append("retained local history differs from fixture: " + kind)
            key = "rra-" + expected["operation_id"]
            effects = [r for r in provider_effects if r["idempotency_key"] == key]
            if len(effects) != 1:
                problems.append("provider ledger must contain exactly one matching effect")
            else:
                row = effects[0]
                request = {"idempotency_key": key, "tenant": expected["tenant"],
                           "tool_name": expected["tool_name"], "arguments": expected["arguments"],
                           "expected_revision": expected["expected_revision"]}
                if (row["tenant"] != expected["tenant"] or row["order_id"] != expected["arguments"]["order_id"]
                        or json.loads(row["address"]) != expected["arguments"]["address"]
                        or row["request_hash"] != digest(request)
                        or row["revision_after"] != expected["expected_revision"] + 1
                        or row["effect_id"] != p["effect_id"] or row["revision_after"] != p["provider_revision"]):
                    problems.append("restore observation disagrees with independent provider ledger")
        except (KeyError, TypeError, ValueError):
            problems.append("malformed recovery fixture or provider ledger")
    return {"schema_version": VERSION, "verdict": "INVALID" if problems else "PASS", "problems": problems,
            "scope": "synthetic recovery observation versus independent provider ledger",
            "local_authorization_attested": False, "local_dispatch_history": "missing_after_restore",
            "evidence_provenance": "exposed_regression"}
