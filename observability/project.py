"""Project one ingested stream into a reviewable request: operations, timing, costs, status. Pure functions.

Business facts come only from trace events, folded in sequence order; span status never changes them.
`authorized` is not execution, `dispatch_started` is a durably recorded attempt (not proof the provider was
entered), and only `dispatch_succeeded` or a matching `serving.reconciled effect_confirmed` is a confirmed
effect. The trace remains the system's own account; independent effect evidence lives in evalgate.
"""
from __future__ import annotations

import re

from .costs import request_costs
from .ingest import Limits, Stream
from .sidecar import SPAN_CLASSES

SAFE_ERROR = re.compile(r"[a-z][a-z0-9_]{0,63}")
SAFE_BY = re.compile(r"[a-z][a-z0-9_]{0,31}")
# Namespaced (serving.*/transport.*) payloads are free-form in trace-v1; nothing in them is exported.
# Core payloads export exactly the contract's closed hash/enum fields (trace-v1 $defs).
EXPORTED_PAYLOAD = {"prompt": ("tool_name", "prompt_sha256"), "dispatch_started": ("tool_name",),
                    "dispatch_succeeded": ("tool_name",), "dispatch_uncertain": ("tool_name", "error_class"),
                    "blocked": ("reason",), "invalidated": ("reason",),
                    "authorized": ("classifier_version", "consent_binding_hash", "consent_binding_version"),
                    "serving.reconciled": ("outcome", "by")}
LABELS = {"prompt": "Confirmation shown (proposed)", "authorized": "Customer approval accepted (authorized, not executed)",
          "rejected": "Customer refused", "invalidated": "Approval invalidated", "blocked": "Blocked by gate",
          "dispatch_started": "Attempt durably recorded", "dispatch_succeeded": "Provider confirmed effect",
          "dispatch_uncertain": "Outcome unknown", "serving.reconciled": "Reconciliation result",
          "serving.run_accepted": "Request accepted", "serving.run_finished": "Evidence stream complete",
          "serving.run_cancelled": "Cancellation requested", "serving.process_restarted": "Process restarted"}


def redact_event(event: dict) -> tuple[dict, int]:
    """Allowlisted view of one event, plus how many payload fields were withheld."""
    kind, payload = event["event_type"], event["payload"]
    out = {"sequence": event["sequence"], "event_id": event["event_id"], "event_type": kind,
           "label": LABELS.get(kind, "Other namespaced event (not a business event)" if "." in kind else kind),
           "occurred_at": event.get("occurred_at"), "operation_id": event.get("operation_id"),
           "prompt_id": event.get("prompt_id"), "payload_hash": event.get("payload_hash")}
    withheld = 0
    allowed = EXPORTED_PAYLOAD.get(kind, ())
    for key, value in payload.items():
        if key not in allowed:
            withheld += 1
            continue
        if key == "error_class" and not SAFE_ERROR.fullmatch(value):
            out["error_class"], out["error_class_redacted"] = "redacted", True
        elif key == "by" and not SAFE_BY.fullmatch(value):
            out["by"] = "redacted"
        else:
            out[key] = value
    return out, withheld


def operations(events: list[dict]) -> dict[str, dict]:
    ops: dict[str, dict] = {}
    cancel_at = next((e["sequence"] for e in events if e["event_type"] == "serving.run_cancelled"), None)
    for event in events:
        key = event.get("operation_id") or event.get("payload_hash")
        if not key or event["event_type"] in ("serving.run_accepted", "serving.run_finished"):
            continue
        op = ops.setdefault(key, {"operation_id": key, "tool_name": None, "prompts": 0, "payload_hashes": [],
                                  "invalidations": 0, "authorized": False, "mutation_attempts": 0, "terminal": None,
                                  "outcome": "not_attempted", "confirmed_by": None, "history_has_unknown": False,
                                  "reconciliations": [], "cancellation": None, "attempt_sequences": []})
        kind = event["event_type"]
        tool = event["payload"].get("tool_name") if kind in EXPORTED_PAYLOAD else None
        op["tool_name"] = op["tool_name"] or tool
        if event.get("payload_hash") and event["payload_hash"] not in op["payload_hashes"]:
            op["payload_hashes"].append(event["payload_hash"])
        if kind == "prompt":
            op["prompts"] += 1
        elif kind == "invalidated":
            op["invalidations"] += 1
            op["terminal"] = "invalidated"
        elif kind == "rejected":
            op["terminal"] = "rejected"
        elif kind == "blocked":
            op["terminal"] = f"blocked:{event['payload'].get('reason')}"
        elif kind == "authorized":
            op["authorized"] = True
        elif kind == "dispatch_started":
            op["mutation_attempts"] += 1
            op["attempt_sequences"].append(event["sequence"])
            op["outcome"], op["confirmed_by"], op["terminal"] = "outcome_missing", None, None
        elif kind == "dispatch_succeeded":
            op["outcome"], op["confirmed_by"] = "effect_confirmed", "dispatch_succeeded"
        elif kind == "dispatch_uncertain":
            op["outcome"], op["history_has_unknown"] = "unknown", True
        elif kind == "serving.reconciled":
            result = event["payload"].get("outcome")
            op["reconciliations"].append(result)
            if op["outcome"] == "unknown" and result == "effect_confirmed":
                op["outcome"], op["confirmed_by"] = "effect_confirmed", "reconciled"
            elif op["outcome"] == "unknown" and result == "no_effect_confirmed":
                op["outcome"], op["confirmed_by"] = "no_effect_confirmed", "reconciled"
            # still_unknown leaves the outcome unknown; a later matching resolution may still decide it.
    if cancel_at is not None:
        for op in ops.values():
            claimed = any(s < cancel_at for s in op["attempt_sequences"])
            op["cancellation"] = "after_claim" if claimed else "before_claim"
            if any(s > cancel_at for s in op["attempt_sequences"]):
                op["attempt_after_cancellation"] = True
    for op in ops.values():
        op.pop("attempt_sequences")
        op["distinct_payloads"] = len(op["payload_hashes"])
    return ops


def _duration(span: dict) -> int | None:
    start, end = span["start_mono_ns"], span["end_mono_ns"]
    if start is None or end is None or span.get("end_clock_domain", span["clock_domain"]) != span["clock_domain"]:
        return None  # never subtract across process incarnations or from a missing endpoint
    return end - start


def timing(stream: Stream, spans_in: list[dict], ops: dict, limits: Limits) -> tuple[dict, list[dict], dict]:
    event_ids = {e["event_id"] for e in stream.events}
    rejected: dict[str, int] = dict(stream.sidecar_rejected)
    accepted: dict[str, dict] = {}

    def reject(code):
        rejected[code] = rejected.get(code, 0) + 1

    counts = {}
    for span in spans_in:
        counts[span["span_id"]] = counts.get(span["span_id"], 0) + 1
    request_spans = [s for s in spans_in if s["span_class"] == "request"]
    for span in spans_in:
        if counts[span["span_id"]] > 1:
            reject("span.duplicate_span_id")
        elif span["span_class"] == "request" and len(request_spans) > 1:
            reject("span.duplicate_request_span")
        elif any(link not in event_ids for link in span["linked_event_ids"]):
            reject("span.unknown_linked_event")
        elif span.get("operation_id") and span["operation_id"] not in ops:
            reject("span.unknown_operation")
        elif len(accepted) >= limits.max_spans_per_request:
            reject("span.over_span_limit")
        else:
            accepted[span["span_id"]] = span
    # Parents must exist in the same request and the graph must be acyclic.
    changed = True
    while changed:
        changed = False
        for sid, span in list(accepted.items()):
            parent = span["parent_span_id"]
            if parent is not None and parent not in accepted:
                del accepted[sid]
                reject("span.orphan_parent")
                changed = True
    for sid in list(accepted):
        seen, node = set(), sid
        while node is not None and node in accepted:
            if node in seen:
                for member in seen:
                    accepted.pop(member, None)
                reject("span.cycle")
                break
            seen.add(node)
            node = accepted[node]["parent_span_id"]
    spans = list(accepted.values())
    durations = {s["span_id"]: _duration(s) for s in spans}
    request = next((s for s in spans if s["span_class"] == "request"), None)
    measured = durations.get(request["span_id"]) if request else None
    others = [d for sid, d in durations.items() if d is not None and (request is None or sid != request["span_id"])]
    if measured is not None:
        kind, lower = "measured", None
    elif others:
        kind, lower = "lower_bound", max(others)  # a nested span's own duration; never a sum, never cross-clock
    else:
        kind, lower = "unknown", None

    def first(cls):
        values = [durations[s["span_id"]] for s in spans if s["span_class"] == cls and durations[s["span_id"]] is not None]
        return min(values) if values else None

    by_class = {}
    for span in spans:
        if span["span_class"] == "request":
            continue
        bucket = by_class.setdefault(span["span_class"], {"count": 0, "known": 0, "total_ns": 0})
        bucket["count"] += 1
        if durations[span["span_id"]] is not None:
            bucket["known"] += 1
            bucket["total_ns"] += durations[span["span_id"]]
    children: dict[str | None, list[dict]] = {}
    for span in spans:
        children.setdefault(span["parent_span_id"], []).append(span)
    overlap = False
    for kids in children.values():
        known = sorted((s for s in kids if durations[s["span_id"]] is not None), key=lambda s: (s["clock_domain"], s["start_mono_ns"]))
        for a, b in zip(known, known[1:]):
            if a["clock_domain"] == b["clock_domain"] and b["start_mono_ns"] < a["end_mono_ns"]:
                overlap = True
    direct = children.get(request["span_id"], []) if request else []
    domains = sorted({s["clock_domain"] for s in spans} | {s["end_clock_domain"] for s in spans if "end_clock_domain" in s})
    lookups = [s for s in spans if s["span_class"] == "reconciliation_lookup"]
    lanes = {}
    for domain in domains:
        members = [s for s in spans if s["clock_domain"] == domain and s["start_mono_ns"] is not None]
        origin = min((s["start_mono_ns"] for s in members), default=0)
        lanes[domain] = [{"span_id": s["span_id"], "span_class": s["span_class"], "status": s["status"],
                          "operation_id": s.get("operation_id"), "attempt_no": s.get("attempt_no"),
                          "error_code": s.get("error_code"), "offset_ns": s["start_mono_ns"] - origin,
                          "duration_ns": durations[s["span_id"]], "parent_span_id": s["parent_span_id"],
                          "start_mono_ns": str(s["start_mono_ns"]),
                          "end_mono_ns": None if s["end_mono_ns"] is None else str(s["end_mono_ns"]),
                          "end_clock_domain": s.get("end_clock_domain"),
                          "linked_event_ids": s["linked_event_ids"]} for s in sorted(members, key=lambda s: s["start_mono_ns"])]
    drops = sum(r["dropped_records"] for r in stream.sidecar if r["kind"] == "drop_report")
    out = {"request_duration_ns": measured, "duration_kind": kind, "lower_bound_ns": lower,
           "command_response_ns": first("command_response"), "first_event_ns": first("first_event_delivery"),
           "by_class": {c: by_class[c] for c in SPAN_CLASSES if c in by_class},
           "direct_children_sum_ns": sum(durations[s["span_id"]] for s in direct if durations[s["span_id"]] is not None),
           "children_overlap": overlap, "clock_domains": domains,
           "reconciliation_lookups": len(lookups), "reconciliation_lookup_errors": sum(s["status"] == "error" for s in lookups),
           "spans_accepted": len(spans), "spans_rejected": sum(n for c, n in rejected.items() if c.startswith("span.")),
           "rejection_codes": rejected,
           "telemetry_dropped": drops, "lanes": lanes,
           "request_span_status": request["status"] if request else None,
           "route_class": request.get("route_class") if request else None,
           "process_request_index": request.get("process_request_index") if request else None}
    parents = {s["span_id"]: s["parent_span_id"] for s in spans}
    return out, spans, parents


def wall_caveats(events: list[dict]) -> list[str]:
    times = [e["occurred_at"] for e in events if e.get("occurred_at")]
    caveats = []
    if any(b < a for a, b in zip(times, times[1:])):
        caveats.append("wall_clock_disagrees_with_sequence")
    if len(times) < len(events):
        caveats.append("some_events_have_no_wall_time")
    return caveats


def headline(stream: Stream, ops: dict, request_span_status: str | None, cancelled: bool) -> tuple[str, list[str]]:
    """A summary label only; evidence completeness, operation outcomes and history stay separately visible."""
    reasons = []
    if stream.completeness:
        return "incomplete", [f"evidence: {c}" for c in stream.completeness]
    if stream.contract_verdict == "INVALID" or stream.conflicting_event_ids:
        return "invalid_evidence", ["trace-v1 checker verdict INVALID on the artifact as delivered"]
    if any(op["outcome"] == "unknown" for op in ops.values()):
        return "unknown_outcome", [f"{k}: outcome unknown and unresolved" for k, o in ops.items() if o["outcome"] == "unknown"]
    if cancelled:
        return "cancelled", ["serving.run_cancelled observed (fixture-defined namespaced event, not a trace-v1 core type); "
                             "a claimed attempt is not undone by cancellation"]
    failed = [f"{k}: reconciliation confirmed no effect" for k, o in ops.items() if o["outcome"] == "no_effect_confirmed"]
    if request_span_status == "error":
        failed.append("request handler reported an error (telemetry request span)")
    if failed:
        return "failed", failed
    return "completed", reasons


def project(stream: Stream, tables: list[dict], data_origin: str, limits: Limits) -> dict:
    events, withheld, error_redactions = [], 0, 0
    for event in stream.events:
        view, dropped = redact_event(event)
        withheld += dropped
        error_redactions += bool(view.get("error_class_redacted"))
        events.append(view)
    ops = operations(stream.events)
    cancelled = any(e["event_type"] == "serving.run_cancelled" for e in stream.events)
    spans_in = [r for r in stream.sidecar if r["kind"] == "span"]
    time_view, spans, parents = timing(stream, spans_in, ops, limits)
    span_ids = {s["span_id"] for s in spans}
    usages = [r for r in stream.sidecar if r["kind"] == "usage" and r["span_id"] in span_ids]
    unbound_usage = sum(1 for r in stream.sidecar if r["kind"] == "usage" and r["span_id"] not in span_ids)
    costs = request_costs(usages, [r for r in stream.sidecar if r["kind"] == "billing_receipt"], tables,
                          {s: True for s in span_ids}, parents)
    costs["unbound_usage_records"] = unbound_usage
    status, reasons = headline(stream, ops, time_view["request_span_status"], cancelled)
    costs["uncertain_outcome"] = status == "unknown_outcome" and bool(usages)
    evidence_classes = sorted({e["evidence_class"] for e in stream.events})
    wall = [e["occurred_at"] for e in stream.events if e.get("occurred_at")]
    return {"key": stream.key, "case_id": stream.case_id, "run_id": stream.run_id, "trace_id": stream.trace_id,
            "artifact_sha256": stream.artifact_sha256, "data_origin": data_origin,
            "evidence_class": evidence_classes[0] if len(evidence_classes) == 1 else "mixed",
            "route_class": time_view["route_class"] or "unclassified",
            "status": status, "status_reasons": reasons,
            "started_at": min(wall) if wall else None,
            "evidence": {"contract_verdict": stream.contract_verdict, "contract_codes": stream.contract_codes,
                         "completeness": stream.completeness, "duplicate_lines_in_artifact": stream.duplicate_lines,
                         "conflicting_event_ids": stream.conflicting_event_ids, "reordered_lines": stream.reordered_lines,
                         "envelope_invalid_events": stream.envelope_invalid_events,
                         "diagnostic_view": bool(stream.duplicate_lines or stream.reordered_lines or stream.envelope_invalid_events),
                         "redacted_payload_fields": withheld, "redacted_error_class": error_redactions,
                         "sidecar_rejected": sum(stream.sidecar_rejected.values()),
                         "caveats": wall_caveats(stream.events) + (["multiple_clock_domains"] if len(time_view["clock_domains"]) > 1 else []),
                         "flags": []},
            "operations": ops, "events": events, "timing": time_view, "costs": costs}
