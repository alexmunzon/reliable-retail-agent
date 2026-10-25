"""obs-sidecar-v1: strict, closed-field validation of timing, usage, receipt and drop records.

A sidecar never re-means a trace-v1 field. It references a trace stream by (run_id, trace_id) inside one
digest-bound artifact pair (see ingest.py) and may reference that stream's event_ids and operation_ids.
Every problem is a fixed code; input values are never echoed.
"""
from __future__ import annotations

import re

from . import SIDECAR_VERSION

ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
CODE = re.compile(r"[a-z][a-z0-9_]{0,63}")
LABEL = re.compile(r"[a-z][a-z0-9_.-]{0,63}")
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,95}")
# A globally unique process incarnation, e.g. "proc-a:boot-1": host-or-process name, then the boot/incarnation.
CLOCK = re.compile(r"[a-z0-9][a-z0-9._-]{0,47}:[a-z0-9][a-z0-9._-]{0,47}")
TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z")
DECIMAL = re.compile(r"(0|[1-9][0-9]{0,11})(\.[0-9]{1,12})?")
CURRENCY = re.compile(r"[A-Z]{3}")

SPAN_CLASSES = ("request", "command_response", "first_event_delivery", "queue_wait", "model_call", "user_wait",
                "authorization", "local_persistence", "dispatch_provider", "reconciliation_lookup", "sse_delivery")
SPAN_STATUS = ("ok", "error", "unknown")
TOKEN_CATEGORIES = ("input", "output", "cache_read", "cache_write", "reasoning")
DROP_REASONS = ("queue_full", "oversize", "emitter_error")
MAX_LINKS = 32

COMMON = {"sidecar_version", "record_id", "kind", "run_id", "trace_id", "emitter"}
FIELDS = {
    "span": {"required": {"span_id", "parent_span_id", "span_class", "clock_domain", "start_mono_ns", "end_mono_ns",
                          "status", "linked_event_ids"},
             "optional": {"end_clock_domain", "operation_id", "attempt_no", "error_code", "route_class",
                          "process_request_index"}},
    "usage": {"required": {"span_id", "provider", "model_id", "provider_request_id", "measurement", "usage_semantics",
                           "tokens", "observed_at", "source"}, "optional": set()},
    "billing_receipt": {"required": {"usage_record_id", "amount", "currency", "receipt_id", "source"}, "optional": set()},
    "drop_report": {"required": {"dropped_records", "reason"}, "optional": set()},
}


def _int(value, minimum=0) -> bool:
    return type(value) is int and value >= minimum


def validate_record(record) -> list[str]:
    """Return problem codes for one decoded sidecar line (empty list = accepted)."""
    if not isinstance(record, dict):
        return ["sidecar.not_object"]
    kind = record.get("kind")
    if kind not in FIELDS:
        return ["sidecar.unknown_kind"]
    allowed = COMMON | FIELDS[kind]["required"] | FIELDS[kind]["optional"]
    problems = []
    if set(record) - allowed:
        problems.append("sidecar.disallowed_field")  # e.g. a copied prompt: the whole record is rejected
    if (COMMON | FIELDS[kind]["required"]) - set(record):
        return problems + ["sidecar.missing_field"]
    if record["sidecar_version"] != SIDECAR_VERSION:
        problems.append("sidecar.unknown_version")
    for key in ("record_id", "run_id", "trace_id"):
        if not (isinstance(record[key], str) and ID.fullmatch(record[key])):
            problems.append(f"sidecar.invalid_{key}")
    emitter = record["emitter"]
    if not (isinstance(emitter, dict) and set(emitter) == {"component", "emitter_version"}
            and all(isinstance(emitter[k], str) and LABEL.fullmatch(emitter[k]) for k in emitter)):
        problems.append("sidecar.invalid_emitter")
    return problems + globals()[f"_check_{kind}"](record)


def _check_span(r: dict) -> list[str]:
    p = []
    if not (isinstance(r["span_id"], str) and ID.fullmatch(r["span_id"])):
        p.append("span.invalid_span_id")
    if r["parent_span_id"] is not None and not (isinstance(r["parent_span_id"], str) and ID.fullmatch(r["parent_span_id"])):
        p.append("span.invalid_parent")
    if r["span_class"] not in SPAN_CLASSES:
        p.append("span.unknown_class")
    if r["status"] not in SPAN_STATUS:
        p.append("span.unknown_status")
    for key in ("clock_domain", "end_clock_domain"):
        if key in r and not (isinstance(r[key], str) and CLOCK.fullmatch(r[key])):
            p.append("span.invalid_clock_domain")
    start, end = r["start_mono_ns"], r["end_mono_ns"]
    for value in (start, end):
        if value is not None and not _int(value):
            p.append("span.invalid_mono_ns")
    if _int(start) and _int(end) and r.get("end_clock_domain", r["clock_domain"]) == r["clock_domain"] and end < start:
        p.append("span.end_before_start")
    links = r["linked_event_ids"]
    if not (isinstance(links, list) and len(links) <= MAX_LINKS and all(isinstance(x, str) and ID.fullmatch(x) for x in links)):
        p.append("span.invalid_links")
    if "operation_id" in r and not (isinstance(r["operation_id"], str) and ID.fullmatch(r["operation_id"])):
        p.append("span.invalid_operation_id")
    if "attempt_no" in r and not (r["span_class"] == "reconciliation_lookup" and _int(r["attempt_no"], 1)):
        p.append("span.attempt_no_outside_reconciliation")  # never a mutation-attempt counter
    if "error_code" in r and not (isinstance(r["error_code"], str) and CODE.fullmatch(r["error_code"])):
        p.append("span.invalid_error_code")
    if ("route_class" in r or "process_request_index" in r) and r["span_class"] != "request":
        p.append("span.request_field_on_child")
    if "route_class" in r and not (isinstance(r["route_class"], str) and CODE.fullmatch(r["route_class"])):
        p.append("span.invalid_route_class")
    if "process_request_index" in r and not _int(r["process_request_index"], 1):
        p.append("span.invalid_process_request_index")
    return p


def _check_usage(r: dict) -> list[str]:
    p = []
    if not (isinstance(r["span_id"], str) and ID.fullmatch(r["span_id"])):
        p.append("usage.invalid_span_id")
    if not (isinstance(r["provider"], str) and LABEL.fullmatch(r["provider"])):
        p.append("usage.invalid_provider")
    if not (isinstance(r["model_id"], str) and MODEL.fullmatch(r["model_id"])):
        p.append("usage.invalid_model_id")
    if r["provider_request_id"] is not None and not (isinstance(r["provider_request_id"], str) and ID.fullmatch(r["provider_request_id"])):
        p.append("usage.invalid_provider_request_id")
    if r["measurement"] not in ("measured", "estimated"):
        p.append("usage.invalid_measurement")
    if r["usage_semantics"] != "final_per_call":
        p.append("usage.unsupported_semantics")  # deltas or cumulative snapshots are not summable here
    tokens = r["tokens"]
    if not (isinstance(tokens, dict) and set(tokens) == set(TOKEN_CATEGORIES)
            and all(v is None or _int(v) for v in tokens.values())):
        p.append("usage.invalid_tokens")
    if r["observed_at"] is not None and not (isinstance(r["observed_at"], str) and TIME.fullmatch(r["observed_at"])):
        p.append("usage.invalid_observed_at")
    if not (isinstance(r["source"], str) and LABEL.fullmatch(r["source"])):
        p.append("usage.invalid_source")
    return p


def _check_billing_receipt(r: dict) -> list[str]:
    p = []
    if not (isinstance(r["usage_record_id"], str) and ID.fullmatch(r["usage_record_id"])):
        p.append("receipt.invalid_usage_ref")
    if not (isinstance(r["amount"], str) and DECIMAL.fullmatch(r["amount"])):
        p.append("receipt.invalid_amount")
    if not (isinstance(r["currency"], str) and CURRENCY.fullmatch(r["currency"])):
        p.append("receipt.invalid_currency")
    if not (isinstance(r["receipt_id"], str) and ID.fullmatch(r["receipt_id"])):
        p.append("receipt.invalid_receipt_id")
    if not (isinstance(r["source"], str) and LABEL.fullmatch(r["source"])):
        p.append("receipt.invalid_source")
    return p


def _check_drop_report(r: dict) -> list[str]:
    p = []
    if not _int(r["dropped_records"]):
        p.append("drop.invalid_count")
    if r["reason"] not in DROP_REASONS:
        p.append("drop.unknown_reason")
    return p
