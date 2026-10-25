#!/usr/bin/env python3
"""Author the frozen synthetic fixture set observability/fixtures/v1 (deterministic, offline).

Every trace line is trace-v1 shaped (as contracts/examples/serving-*.jsonl are) and every payload_hash
is the real contract hash of synthetic simulator arguments. Ground-truth effects come from the
independent evaluation simulator (evalgate.simulator), never from the dashboard under test.
The hand-written oracle (fixtures/v1/oracle.json) is NOT produced here.

  python3 scripts/observability_fixtures.py          # write files and MANIFEST.json
  python3 scripts/observability_fixtures.py --check  # fail if regenerating would change any byte
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evalgate.scoring import payload_hash  # noqa: E402  (contract hash, unchanged)
from evalgate.simulator import RetailSimulator  # noqa: E402  (independent effect evidence)

OUT = ROOT / "observability" / "fixtures" / "v1"
GENERATOR_VERSION = "obs-fixture-generator-v1"
MAPPING = "obs-fixture-map-v1"
EMITTER = {"component": "fixture.backend_service", "emitter_version": "obs-fixture-emitter-v1"}
MONO0 = 5_000_000_000_000  # arbitrary per-process monotonic origin (ns); never comparable across domains
ADDRESS = {"order_id": "SYN-ORDER-1042", "address1": "1 Synthetic Way", "address2": "", "city": "Sampleton",
           "state": "CA", "country": "USA", "zip": "00000"}
ADDRESS_2 = {**ADDRESS, "address2": "Unit 9"}
CANCEL = {"order_id": "SYN-ORDER-2077", "reason": "no longer needed"}


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


class Run:
    """One request = one serving run = one (run_id, trace_id) stream, plus its sidecar records."""

    def __init__(self, run_id: str, start: str, route_class: str, domain: str = "proc-a:boot-1", index: int | None = None):
        self.run_id, self.t0, self.domain = run_id, datetime.fromisoformat(start.replace("Z", "+00:00")), domain
        self.events: list[dict] = []
        self.side: list[dict] = []
        self.route_class, self.index = route_class, index

    def ev(self, event_type: str, at_ms: float, *, op=None, tool=None, args=None, prompt=None, payload=None,
           wall_offset_ms: float = 0.0) -> dict:
        event = {"schema_version": "trace-v1", "event_id": f"{self.run_id}:e{len(self.events) + 1}",
                 "sequence": len(self.events) + 1, "event_type": event_type, "session_id": f"sess-{self.run_id}",
                 "run_id": self.run_id, "trace_id": self.run_id,
                 "occurred_at": iso(self.t0 + timedelta(milliseconds=at_ms + wall_offset_ms)),
                 "source": {"component": "backend.service", "mapping_version": MAPPING},
                 "evidence_class": "serving_observation", "payload": dict(payload or {})}
        if op:
            event["operation_id"] = op
        if tool is not None:
            event["payload_hash"] = payload_hash(tool, args)
        if prompt:
            event["prompt_id"] = prompt
        self.events.append(event)
        return event

    def prompt(self, at, op, tool, args, prompt_id):
        text = f"SYNTHETIC confirmation for {op} ({prompt_id}); text is never exported"
        return self.ev("prompt", at, op=op, tool=tool, args=args, prompt=prompt_id,
                       payload={"tool_name": tool, "prompt_sha256": sha256(text.encode()).hexdigest()})

    def authorized(self, at, op, tool, args, prompt_id, **kw):
        return self.ev("authorized", at, op=op, tool=tool, args=args, prompt=prompt_id,
                       payload={"classifier_version": "whole-response-assent-current-v5"}, **kw)

    def dispatch(self, kind, at, op, tool, args, error_class=None):
        payload = {"tool_name": tool, **({"error_class": error_class} if error_class else {})}
        return self.ev(kind, at, op=op, tool=tool, args=args, payload=payload)

    def _base(self, kind: str, key: str) -> dict:
        return {"sidecar_version": "obs-sidecar-v1", "record_id": f"{self.run_id}:{key}", "kind": kind,
                "run_id": self.run_id, "trace_id": self.run_id, "emitter": dict(EMITTER)}

    def span(self, span_id, span_class, start_ms, end_ms, *, parent="req", op=None, links=(), status="ok",
             domain=None, end_domain=None, attempt_no=None, error_code=None):
        record = {**self._base("span", f"s:{span_id}"), "span_id": f"{self.run_id}:{span_id}",
                  "parent_span_id": f"{self.run_id}:{parent}" if parent else None, "span_class": span_class,
                  "clock_domain": domain or self.domain,
                  "start_mono_ns": None if start_ms is None else MONO0 + int(round(start_ms * 1_000_000)),
                  "end_mono_ns": None if end_ms is None else MONO0 + int(round(end_ms * 1_000_000)),
                  "status": status, "linked_event_ids": list(links)}
        if end_domain:
            record["end_clock_domain"] = end_domain
        if op:
            record["operation_id"] = op
        if attempt_no is not None:
            record["attempt_no"] = attempt_no
        if error_code:
            record["error_code"] = error_code
        if span_class == "request":
            record["route_class"] = self.route_class
            if self.index is not None:
                record["process_request_index"] = self.index
        self.side.append(record)
        return record

    def usage(self, key, span_id, observed_at, tokens, *, measurement="measured", model="synthetic-model-a",
              provider="synthetic-provider", request_id=None):
        record = {**self._base("usage", f"u:{key}"), "span_id": f"{self.run_id}:{span_id}", "provider": provider,
                  "model_id": model, "provider_request_id": request_id, "measurement": measurement,
                  "usage_semantics": "final_per_call",
                  "tokens": {c: tokens.get(c) for c in ("input", "output", "cache_read", "cache_write", "reasoning")},
                  "observed_at": observed_at, "source": "fixture.provider_usage_field"}
        self.side.append(record)
        return record

    def receipt(self, key, usage_key, amount, currency="USD"):
        record = {**self._base("billing_receipt", f"b:{key}"), "usage_record_id": f"{self.run_id}:u:{usage_key}",
                  "amount": amount, "currency": currency, "receipt_id": f"syn-receipt-{self.run_id}-{key}",
                  "source": "fixture.synthetic_billing_receipt"}
        self.side.append(record)
        return record

    def drop(self, dropped, reason):
        self.side.append({**self._base("drop_report", f"d:{reason}"), "dropped_records": dropped, "reason": reason})


FULL = {"input": 1200, "output": 180, "cache_read": 800, "cache_write": 0, "reasoning": 64}


def healthy(run_id="run-healthy", start="2026-10-02T09:00:00.000Z", op="op-healthy-1"):
    r = Run(run_id, start, "address_change")
    tool, args = "modify_pending_order_address", ADDRESS
    e1 = r.ev("serving.run_accepted", 0)
    e2 = r.prompt(655, op, tool, args, f"prm-{op}")
    e3 = r.authorized(1100, op, tool, args, f"prm-{op}")
    e4 = r.dispatch("dispatch_started", 1115, op, tool, args)
    e5 = r.dispatch("dispatch_succeeded", 1390, op, tool, args)
    e6 = r.ev("serving.run_finished", 1450)
    r.span("req", "request", 0, 1450, parent=None, links=[e1["event_id"], e6["event_id"]])
    r.span("cmd", "command_response", 0, 12, links=[e1["event_id"]])
    r.span("queue", "queue_wait", 12, 40)
    r.span("model1", "model_call", 40, 640)
    r.span("first", "first_event_delivery", 0, 655, links=[e2["event_id"]])
    r.span("user", "user_wait", 660, 1100, op=op, links=[e2["event_id"], e3["event_id"]])
    r.span("auth", "authorization", 1100, 1108, op=op, links=[e3["event_id"]])
    r.span("persist", "local_persistence", 1108, 1115, op=op, links=[e4["event_id"]])
    r.span("provider", "dispatch_provider", 1115, 1390, op=op, links=[e4["event_id"], e5["event_id"]])
    r.span("sse", "sse_delivery", 1390, 1450, links=[e6["event_id"]])
    r.usage("m1", "model1", "2026-10-02T09:00:00.640Z", FULL, request_id="syn-req-healthy-1")
    r.receipt("m1", "m1", "0.0074")
    return r, [(op, tool, args, "healthy")]


def simple_open(r: Run, end_ms: float, links=()):
    r.span("req", "request", 0, end_ms, parent=None, links=list(links))


def rejected():
    r = Run("run-consent-rejected", "2026-10-02T10:00:00.000Z", "address_change")
    op, tool = "op-rejected-1", "modify_pending_order_address"
    r.ev("serving.run_accepted", 0)
    r.prompt(500, op, tool, ADDRESS, "prm-rejected-1")
    r.ev("rejected", 4200, op=op, tool=tool, args=ADDRESS)
    r.ev("serving.run_finished", 4260)
    simple_open(r, 4260)
    r.span("user", "user_wait", 505, 4200, op=op)
    return r, []


def ambiguous():
    r = Run("run-consent-ambiguous", "2026-10-02T10:10:00.000Z", "address_change")
    op, tool = "op-ambiguous-1", "modify_pending_order_address"
    r.ev("serving.run_accepted", 0)
    r.prompt(500, op, tool, ADDRESS, "prm-ambiguous-1")
    r.ev("blocked", 3000, op=op, tool=tool, args=ADDRESS, payload={"reason": "ambiguous_response"})
    r.ev("serving.run_finished", 3050)
    simple_open(r, 3050)
    return r, []


def changed_intent():
    r = Run("run-changed-intent", "2026-10-02T10:20:00.000Z", "address_change")
    op, tool = "op-change-1", "modify_pending_order_address"
    r.ev("serving.run_accepted", 0)
    r.prompt(400, op, tool, ADDRESS, "prm-change-1")
    r.ev("invalidated", 2000, op=op, tool=tool, args=ADDRESS, payload={"reason": "payload_or_operation_changed"})
    r.prompt(2100, op, tool, ADDRESS_2, "prm-change-2")
    r.authorized(3500, op, tool, ADDRESS_2, "prm-change-2")
    r.dispatch("dispatch_started", 3510, op, tool, ADDRESS_2)
    r.dispatch("dispatch_succeeded", 3800, op, tool, ADDRESS_2)
    r.ev("serving.run_finished", 3840)
    simple_open(r, 3840)
    r.span("provider", "dispatch_provider", 3510, 3800, op=op)
    return r, [(op, tool, ADDRESS_2, "healthy")]


def race():
    """Two concurrent requests race to claim one operation; the durable claim admits exactly one."""
    a = Run("run-race-winner", "2026-10-02T10:30:00.000Z", "cancel_order")
    b = Run("run-race-loser", "2026-10-02T10:30:00.004Z", "cancel_order")
    op, tool = "op-race-1", "cancel_pending_order"
    for r in (a, b):
        r.ev("serving.run_accepted", 0)
        r.prompt(300, op, tool, CANCEL, f"prm-race-{r.run_id[-5:]}")
        r.authorized(900, op, tool, CANCEL, f"prm-race-{r.run_id[-5:]}")
    a.span("persist", "local_persistence", 900, 906, op=op)
    a.dispatch("dispatch_started", 906, op, tool, CANCEL)
    a.dispatch("dispatch_succeeded", 1100, op, tool, CANCEL)
    a.ev("serving.run_finished", 1120)
    b.span("persist", "local_persistence", 900, 909, op=op, status="error", error_code="claim_already_held")
    b.ev("blocked", 909, op=op, tool=tool, args=CANCEL, payload={"reason": "repeat_dispatch"})
    b.ev("serving.run_finished", 930)
    simple_open(a, 1120)
    simple_open(b, 930)
    return [a, b], [(op, tool, CANCEL, "healthy")]


def lost_response(run_id="run-lost-response", start="2026-10-02T11:00:00.000Z", op="op-lost-1"):
    r = Run(run_id, start, "address_change")
    tool = "modify_pending_order_address"
    r.ev("serving.run_accepted", 0)
    r.prompt(400, op, tool, ADDRESS, f"prm-{op}")
    r.authorized(1200, op, tool, ADDRESS, f"prm-{op}")
    r.dispatch("dispatch_started", 1210, op, tool, ADDRESS)
    r.dispatch("dispatch_uncertain", 31210, op, tool, ADDRESS, error_class="provider_timeout")
    r.ev("serving.run_finished", 31230)
    simple_open(r, 31230)
    r.span("model1", "model_call", 20, 380)
    r.span("provider", "dispatch_provider", 1210, 31210, op=op, status="unknown", error_code="provider_timeout")
    r.usage("m1", "model1", start, FULL, request_id=f"syn-req-{op}")
    return r, [(op, tool, ADDRESS, "uncertain_after_effect")]


def restart():
    """Accepted, then the process restarted mid-request; unknown survives the restart, then reconciles."""
    r = Run("run-restart", "2026-10-02T11:10:00.000Z", "address_change")
    op, tool = "op-restart-1", "modify_pending_order_address"
    r.ev("serving.run_accepted", 0)
    r.prompt(400, op, tool, ADDRESS, "prm-restart-1")
    r.authorized(1000, op, tool, ADDRESS, "prm-restart-1")
    r.dispatch("dispatch_started", 1010, op, tool, ADDRESS)
    r.dispatch("dispatch_uncertain", 1500, op, tool, ADDRESS, error_class="worker_lost")
    r.ev("serving.process_restarted", 9000)
    r.ev("serving.reconciled", 9400, op=op, tool=tool, args=ADDRESS, payload={"outcome": "effect_confirmed", "by": "provider_ledger"})
    r.ev("serving.run_finished", 9420)
    r.span("req", "request", 0, 410, parent=None, end_domain="proc-a:boot-2")  # end measured in another clock domain
    r.span("provider", "dispatch_provider", 1010, None, op=op, status="unknown", error_code="worker_lost")
    r.span("recon1", "reconciliation_lookup", 100, 380, op=op, attempt_no=1, domain="proc-a:boot-2")
    return r, [(op, tool, ADDRESS, "uncertain_after_effect")]


def recon_success():
    r = Run("run-recon-confirmed", "2026-10-02T11:20:00.000Z", "address_change")
    op, tool = "op-recon-ok-1", "modify_pending_order_address"
    r.ev("serving.run_accepted", 0)
    r.prompt(300, op, tool, ADDRESS, "prm-recon-ok-1")
    r.authorized(800, op, tool, ADDRESS, "prm-recon-ok-1")
    r.dispatch("dispatch_started", 810, op, tool, ADDRESS)
    r.dispatch("dispatch_uncertain", 10810, op, tool, ADDRESS, error_class="provider_timeout")
    r.ev("serving.reconciled", 12900, op=op, tool=tool, args=ADDRESS, payload={"outcome": "effect_confirmed", "by": "provider_ledger"})
    r.ev("serving.run_finished", 12920)
    simple_open(r, 12920)
    r.span("provider", "dispatch_provider", 810, 10810, op=op, status="unknown", error_code="provider_timeout")
    r.span("recon1", "reconciliation_lookup", 10900, 11900, op=op, attempt_no=1, status="error", error_code="ledger_unavailable")
    r.span("recon2", "reconciliation_lookup", 12000, 12880, op=op, attempt_no=2)
    return r, [(op, tool, ADDRESS, "uncertain_after_effect")]


def recon_no_effect():
    r = Run("run-recon-no-effect", "2026-10-02T11:30:00.000Z", "address_change")
    op, tool = "op-recon-none-1", "modify_pending_order_address"
    r.ev("serving.run_accepted", 0)
    r.prompt(300, op, tool, ADDRESS, "prm-recon-none-1")
    r.authorized(800, op, tool, ADDRESS, "prm-recon-none-1")
    r.dispatch("dispatch_started", 810, op, tool, ADDRESS)
    r.dispatch("dispatch_uncertain", 10810, op, tool, ADDRESS, error_class="provider_timeout")
    r.ev("serving.reconciled", 11500, op=op, tool=tool, args=ADDRESS, payload={"outcome": "no_effect_confirmed", "by": "provider_ledger"})
    r.ev("serving.run_finished", 11520)
    simple_open(r, 11520)
    r.span("recon1", "reconciliation_lookup", 10900, 11480, op=op, attempt_no=1)
    return r, [(op, tool, ADDRESS, "error_before_effect")]


def recon_unavailable():
    r = Run("run-recon-unavailable", "2026-10-02T11:40:00.000Z", "address_change")
    op, tool = "op-recon-down-1", "modify_pending_order_address"
    r.ev("serving.run_accepted", 0)
    r.prompt(300, op, tool, ADDRESS, "prm-recon-down-1")
    r.authorized(800, op, tool, ADDRESS, "prm-recon-down-1")
    r.dispatch("dispatch_started", 810, op, tool, ADDRESS)
    r.dispatch("dispatch_uncertain", 10810, op, tool, ADDRESS, error_class="provider_timeout")
    r.ev("serving.reconciled", 14000, op=op, tool=tool, args=ADDRESS, payload={"outcome": "still_unknown", "by": "provider_ledger"})
    r.ev("serving.run_finished", 14020)
    simple_open(r, 14020)
    for n, (s, e) in enumerate(((10900, 11900), (12000, 13000), (13100, 13980)), 1):
        r.span(f"recon{n}", "reconciliation_lookup", s, e, op=op, attempt_no=n, status="error", error_code="ledger_unavailable")
    return r, [(op, tool, ADDRESS, "uncertain_after_effect")]


def cancel_before_claim():
    r = Run("run-cancel-before-claim", "2026-10-02T12:00:00.000Z", "cancel_order")
    op, tool = "op-cancel-early-1", "cancel_pending_order"
    r.ev("serving.run_accepted", 0)
    r.prompt(300, op, tool, CANCEL, "prm-cancel-early-1")
    r.authorized(900, op, tool, CANCEL, "prm-cancel-early-1")
    r.ev("serving.run_cancelled", 950)
    r.ev("serving.run_finished", 960)
    simple_open(r, 960)
    return r, []


def cancel_after_claim():
    r = Run("run-cancel-after-claim", "2026-10-02T12:10:00.000Z", "cancel_order")
    op, tool = "op-cancel-late-1", "cancel_pending_order"
    r.ev("serving.run_accepted", 0)
    r.prompt(300, op, tool, CANCEL, "prm-cancel-late-1")
    r.authorized(900, op, tool, CANCEL, "prm-cancel-late-1")
    r.dispatch("dispatch_started", 910, op, tool, CANCEL)
    r.ev("serving.run_cancelled", 950)
    r.dispatch("dispatch_succeeded", 1200, op, tool, CANCEL)
    r.ev("serving.run_finished", 1210)
    simple_open(r, 1210)
    return r, [(op, tool, CANCEL, "healthy")]


def gap():
    r, _ = healthy("run-sequence-gap", "2026-10-02T12:20:00.000Z", "op-gap-1")
    del r.events[2]  # authorized line dropped in transit; sequence 3 missing
    r.side = [s for s in r.side if "e3" not in json.dumps(s)]
    return r, [("op-gap-1", "modify_pending_order_address", ADDRESS, "healthy")]


def truncated():
    r, _ = healthy("run-truncated", "2026-10-02T12:30:00.000Z", "op-trunc-1")
    r.events = r.events[:4]  # cut after dispatch_started: no outcome, no run_finished
    r.side = [s for s in r.side if s["kind"] != "span" or s["span_class"] in ("command_response", "queue_wait", "model_call")]
    r.side = [s for s in r.side if s["kind"] != "billing_receipt"]
    r.span("req", "request", 0, None, parent=None, status="unknown")
    return r, [("op-trunc-1", "modify_pending_order_address", ADDRESS, "healthy")]


def out_of_order():
    r, _ = healthy("run-out-of-order", "2026-10-02T12:40:00.000Z", "op-ooo-1")
    r.events[3], r.events[4] = r.events[4], r.events[3]  # outcome line written before its start line (late write)
    r.side = [s for s in r.side if s["kind"] != "billing_receipt"]
    return r, [("op-ooo-1", "modify_pending_order_address", ADDRESS, "healthy")]


def clock_skew():
    """The authorizing process's wall clock runs 2.5 s behind; monotonic spans are unaffected."""
    r = Run("run-clock-skew", "2026-10-02T12:50:00.000Z", "address_change")
    op, tool = "op-skew-1", "modify_pending_order_address"
    r.ev("serving.run_accepted", 0)
    r.prompt(400, op, tool, ADDRESS, "prm-skew-1")
    r.authorized(1400, op, tool, ADDRESS, "prm-skew-1", wall_offset_ms=-2500)
    r.dispatch("dispatch_started", 1410, op, tool, ADDRESS)
    r.dispatch("dispatch_succeeded", 1700, op, tool, ADDRESS)
    r.ev("serving.run_finished", 1720)
    simple_open(r, 1720)
    r.span("user", "user_wait", 405, 1400, op=op)
    r.span("auth", "authorization", 0, 6, op=op, domain="proc-b:boot-1")
    return r, [(op, tool, ADDRESS, "healthy")]


def malformed_spans():
    r = Run("run-malformed-spans", "2026-10-02T13:00:00.000Z", "order_lookup")
    e1 = r.ev("serving.run_accepted", 0)
    r.ev("serving.run_finished", 800)
    simple_open(r, 800)
    r.span("backwards", "queue_wait", 500, 100)                      # end before start
    bad = r.span("badclass", "queue_wait", 10, 20)
    bad["span_class"] = "teleport"                                     # unknown span class
    r.span("ghostlink", "sse_delivery", 700, 790, links=["run-malformed-spans:e99"])  # links a missing event
    r.span("orphan", "queue_wait", 30, 60, parent="does-not-exist")    # parent not in request
    r.span("ok", "model_call", 100, 400, links=[e1["event_id"]])
    return r, []


def overlapping():
    """A planning model call with its own usage, and two concurrent child calls under it."""
    r = Run("run-overlap", "2026-10-02T13:10:00.000Z", "order_lookup")
    r.ev("serving.run_accepted", 0)
    r.ev("serving.run_finished", 1000)
    simple_open(r, 1000)
    r.span("plan", "model_call", 50, 950)
    r.span("m1", "model_call", 100, 700, parent="plan")
    r.span("m2", "model_call", 300, 900, parent="plan")  # concurrent with m1: children sum (1200 ms) exceeds elapsed
    small = {"input": 500, "output": 50, "cache_read": 0, "cache_write": 0, "reasoning": 0}
    r.usage("plan", "plan", "2026-10-02T13:10:00.950Z", small, request_id="syn-req-overlap-plan")
    r.usage("m1", "m1", "2026-10-02T13:10:00.700Z", {"input": 1000, "output": 100, "cache_read": 0, "cache_write": 0, "reasoning": 0},
            request_id="syn-req-overlap-m1")
    r.usage("m2", "m2", "2026-10-02T13:10:00.900Z", {"input": 2000, "output": 300, "cache_read": 0, "cache_write": 0, "reasoning": 0},
            request_id="syn-req-overlap-m2")
    # The emitter re-sent m2's usage under a new record id: same provider call, counted once.
    r.usage("m2-resent", "m2", "2026-10-02T13:10:00.900Z", {"input": 2000, "output": 300, "cache_read": 0, "cache_write": 0, "reasoning": 0},
            request_id="syn-req-overlap-m2")
    return r, []


def still_unknown_then_confirmed():
    """A first reconciliation lookup cannot decide; a later authoritative receipt resolves it. History is kept."""
    r = Run("run-unknown-then-confirmed", "2026-10-02T11:50:00.000Z", "address_change")
    op, tool = "op-late-confirm-1", "modify_pending_order_address"
    r.ev("serving.run_accepted", 0)
    r.prompt(300, op, tool, ADDRESS, "prm-late-confirm-1")
    r.authorized(800, op, tool, ADDRESS, "prm-late-confirm-1")
    r.dispatch("dispatch_started", 810, op, tool, ADDRESS)
    r.dispatch("dispatch_uncertain", 10810, op, tool, ADDRESS, error_class="provider_timeout")
    r.ev("serving.reconciled", 12000, op=op, tool=tool, args=ADDRESS, payload={"outcome": "still_unknown", "by": "provider_ledger"})
    r.ev("serving.reconciled", 30000, op=op, tool=tool, args=ADDRESS, payload={"outcome": "effect_confirmed", "by": "provider_ledger"})
    r.ev("serving.run_finished", 30020)
    simple_open(r, 30020)
    r.span("recon1", "reconciliation_lookup", 10900, 11990, op=op, attempt_no=1, status="error", error_code="ledger_unavailable")
    r.span("recon2", "reconciliation_lookup", 29000, 29990, op=op, attempt_no=2)
    return r, [(op, tool, ADDRESS, "uncertain_after_effect")]


def duplicate_line():
    r, facts = healthy("run-duplicate-line", "2026-10-02T09:30:00.000Z", "op-dupline-1")
    r.side = [s for s in r.side if s["kind"] != "billing_receipt"]
    for s in r.side:
        if s["kind"] == "usage":
            s["provider_request_id"] = "syn-req-dupline-1"
    return r, facts


def missing_tokens():
    r = Run("run-missing-tokens", "2026-10-02T13:20:00.000Z", "order_lookup")
    r.ev("serving.run_accepted", 0)
    r.ev("serving.run_finished", 900)
    simple_open(r, 900)
    r.span("m1", "model_call", 50, 850)
    r.usage("m1", "m1", "2026-10-02T13:20:00.850Z", {"input": 1500, "output": None, "cache_read": 0, "cache_write": 0, "reasoning": None})
    r.usage("e1", "m1", "2026-10-02T13:20:00.850Z", {"input": 1500, "output": 400, "cache_read": 0, "cache_write": 0, "reasoning": 0},
            measurement="estimated")  # a local estimate, kept in its own series
    return r, []


def dated_prices():
    r = Run("run-dated-prices", "2026-10-31T23:59:00.000Z", "order_lookup")
    r.ev("serving.run_accepted", 0)
    r.ev("serving.run_finished", 120000)
    simple_open(r, 120000)
    r.span("m1", "model_call", 1000, 2000)
    r.span("m2", "model_call", 61000, 62000)
    zero = {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_write": 0, "reasoning": 0}
    r.usage("m1", "m1", "2026-10-31T23:59:02.000Z", zero)   # priced by the 2026-01-01 table
    r.usage("m2", "m2", "2026-11-01T00:00:02.000Z", zero)   # priced by the 2026-11-01 table
    return r, []


def zero_vs_unknown():
    r = Run("run-zero-vs-unknown-price", "2026-10-02T13:40:00.000Z", "order_lookup")
    r.ev("serving.run_accepted", 0)
    r.ev("serving.run_finished", 1500)
    simple_open(r, 1500)
    r.span("m1", "model_call", 10, 500)
    r.span("m2", "model_call", 600, 1400)
    r.usage("free", "m1", "2026-10-02T13:40:00.500Z", {"input": 5000, "output": 500, "cache_read": 0, "cache_write": 0, "reasoning": 0},
            model="synthetic-free-tier-model")   # priced explicitly at 0
    r.usage("unpriced", "m2", "2026-10-02T13:40:01.400Z", {"input": 5000, "output": 500, "cache_read": 0, "cache_write": 0, "reasoning": 0},
            model="synthetic-unlisted-model")    # no price entry: unknown, never $0
    return r, []


def redaction():
    r = Run("run-redaction", "2026-10-02T13:50:00.000Z", "order_lookup")
    op, tool = "op-redact-1", "cancel_pending_order"
    r.ev("serving.run_accepted", 0)
    r.ev("serving.diagnostic", 50, payload={"note": "customer sam@example.invalid asked about 7 Original Road",
                                            "prompt_text": "SYNTHETIC full prompt text that must never be shown"})
    r.prompt(300, op, tool, CANCEL, "prm-redact-1")
    r.authorized(900, op, tool, CANCEL, "prm-redact-1")
    r.dispatch("dispatch_started", 910, op, tool, CANCEL)
    r.dispatch("dispatch_uncertain", 1910, op, tool, CANCEL, error_class="timeout for sam@example.invalid")
    r.ev("serving.run_finished", 1930)
    simple_open(r, 1930)
    leaky = r.span("leaky", "queue_wait", 10, 40)
    leaky["prompt"] = "SYNTHETIC prompt copied into telemetry"      # disallowed field: record rejected
    r.span("m1", "model_call", 50, 280)
    r.usage("m1", "m1", "2026-10-02T13:50:00.280Z", FULL, request_id="sam@example.invalid")  # not an opaque id: rejected
    return r, [(op, tool, CANCEL, "uncertain_after_effect")]


def restart_redispatch():
    """A crashed process's successor minted a fresh approval and re-attempted the SAME operation in a new run.
    Each stream alone passes trace-check (contracts/HANDOFF.md compatibility limit); the dashboard must flag it."""
    a = Run("run-redispatch-first", "2026-10-02T14:00:00.000Z", "cancel_order")
    b = Run("run-redispatch-second", "2026-10-02T14:05:00.000Z", "cancel_order", domain="proc-a:boot-3")
    op, tool = "op-redispatch-1", "cancel_pending_order"
    a.ev("serving.run_accepted", 0)
    a.prompt(300, op, tool, CANCEL, "prm-redispatch-a")
    a.authorized(900, op, tool, CANCEL, "prm-redispatch-a")
    a.dispatch("dispatch_started", 910, op, tool, CANCEL)
    a.dispatch("dispatch_uncertain", 5910, op, tool, CANCEL, error_class="worker_lost")
    a.ev("serving.run_finished", 5920)
    b.ev("serving.run_accepted", 0)
    b.prompt(300, op, tool, CANCEL, "prm-redispatch-b")
    b.authorized(900, op, tool, CANCEL, "prm-redispatch-b")
    b.dispatch("dispatch_started", 910, op, tool, CANCEL)
    b.dispatch("dispatch_succeeded", 1100, op, tool, CANCEL)
    b.ev("serving.run_finished", 1120)
    simple_open(a, 5920)
    simple_open(b, 1120)
    return [a, b], [(op, tool, CANCEL, "uncertain_after_effect")]


def tenant_b():
    r, facts = healthy("run-tenant-b-private", "2026-10-02T14:10:00.000Z", "op-tenantb-1")
    r.side = [s for s in r.side if s["kind"] != "billing_receipt"]
    return r, facts


def large():
    r = Run("run-large", "2026-10-02T14:20:00.000Z", "order_lookup")
    r.ev("serving.run_accepted", 0)
    r.ev("serving.run_finished", 60500)
    simple_open(r, 60500)
    for n in range(1, 601):
        r.span(f"sse{n:03d}", "sse_delivery", n * 100, n * 100 + 1)
    r.drop(37, "queue_full")   # emitter reported 37 telemetry records dropped under backpressure
    return r, []


def load_runs():
    """24 small order lookups with hand-checkable durations 110..340 ms; the 3 slowest failed."""
    runs = []
    for i in range(1, 25):
        r = Run(f"run-load-{i:02d}", f"2026-10-03T10:{i:02d}:00.000Z", "load_probe", index=i)
        duration = 100 + 10 * i
        r.ev("serving.run_accepted", 0)
        r.ev("serving.run_finished", duration)
        failed = i >= 22
        r.span("req", "request", 0, duration, parent=None, status="error" if failed else "ok",
               error_code="handler_error" if failed else None)
        r.span("m1", "model_call", 5, duration - 20)
        r.usage("m1", "m1", f"2026-10-03T10:{i:02d}:00.100Z",
                {"input": 1000 + i, "output": 200, "cache_read": 0, "cache_write": 0, "reasoning": 0})
        runs.append(r)
    return runs


PRICING = [
    {"pricing_version": "obs-pricing-v1", "table_id": "synthetic-2026-01-01", "effective_from": "2026-01-01",
     "currency": "USD", "unit": "per_1m_tokens", "synthetic": True,
     "source": "SYNTHETIC user-supplied demonstration table; not a current or real provider price list",
     "assumptions": ["Prices apply per token category; a missing category or model is unknown, never 0",
                     "No currency conversion is performed", "Included development usage is not priced"],
     "models": {"synthetic-model-a": {"provider": "synthetic-provider", "input": "3.00", "output": "15.00",
                                      "cache_read": "0.30", "cache_write": "3.75", "reasoning": "15.00"},
                "synthetic-free-tier-model": {"provider": "synthetic-provider", "input": "0", "output": "0",
                                              "cache_read": "0", "cache_write": "0", "reasoning": "0"}}},
    {"pricing_version": "obs-pricing-v1", "table_id": "synthetic-2026-11-01", "effective_from": "2026-11-01",
     "currency": "USD", "unit": "per_1m_tokens", "synthetic": True,
     "source": "SYNTHETIC user-supplied demonstration table; not a current or real provider price list",
     "assumptions": ["Input price change demonstrates dated selection only"],
     "models": {"synthetic-model-a": {"provider": "synthetic-provider", "input": "2.50", "output": "15.00",
                                      "cache_read": "0.30", "cache_write": "3.75", "reasoning": "15.00"},
                "synthetic-free-tier-model": {"provider": "synthetic-provider", "input": "0", "output": "0",
                                              "cache_read": "0", "cache_write": "0", "reasoning": "0"}}},
]


def dumps_line(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(", ", ": "), ensure_ascii=True)


def build() -> dict[str, bytes]:
    """Return {relative path: bytes} for the whole fixture set except the hand-written oracle."""
    cases: list[tuple[str, list[Run], list]] = []
    for name, made in (("healthy", healthy()), ("consent-rejected", rejected()), ("consent-ambiguous", ambiguous()),
                       ("changed-intent", changed_intent()), ("race-claim", race()), ("lost-response", lost_response()),
                       ("process-restart", restart()), ("reconcile-confirmed", recon_success()),
                       ("reconcile-no-effect", recon_no_effect()), ("reconcile-unavailable", recon_unavailable()),
                       ("unknown-then-confirmed", still_unknown_then_confirmed()), ("duplicate-line", duplicate_line()),
                       ("cancel-before-claim", cancel_before_claim()), ("cancel-after-claim", cancel_after_claim()),
                       ("sequence-gap", gap()), ("truncated", truncated()), ("out-of-order", out_of_order()),
                       ("clock-skew", clock_skew()), ("malformed-spans", malformed_spans()), ("overlap", overlapping()),
                       ("missing-tokens", missing_tokens()), ("dated-prices", dated_prices()),
                       ("zero-vs-unknown-price", zero_vs_unknown()), ("redaction", redaction()),
                       ("restart-redispatch", restart_redispatch()), ("tenant-b", tenant_b()), ("large", large())):
        runs, facts = made
        cases.append((name, runs if isinstance(runs, list) else [runs], facts))
    cases.append(("load", load_runs(), []))

    files: dict[str, bytes] = {}
    effects, access = {}, {}
    for name, runs, facts in cases:
        trace_lines = [dumps_line(e) for r in runs for e in r.events]
        if name == "duplicate-line":
            trace_lines.insert(3, trace_lines[2])  # the identical authorized line written twice inside one artifact
        files[f"traces/{name}.jsonl"] = ("\n".join(trace_lines) + "\n").encode()
        files[f"sidecar/{name}.jsonl"] = ("\n".join(dumps_line(s) for r in runs for s in r.side) + "\n").encode()
        for r in runs:
            access[f"{name}/{r.run_id}"] = "tenant-b" if name == "tenant-b" else "tenant-a"
        for op, tool, args, mode in facts:
            sim = RetailSimulator(mode)
            try:
                sim.invoke(tool, args)
            except Exception:  # raise_after_effect style timeouts are evidence, not crashes
                pass
            record = sim.invocations[0]
            effects[op] = {"tool": tool, "simulator_mode": mode, "effect_applied": record["effect_applied"],
                           "simulator_outcome": record["outcome"], "payload_hash": payload_hash(tool, args)}
    # A cut-off line and an over-limit line: whole-file evidence problems, not attributable to one stream.
    healthy_line = dumps_line(healthy("run-malformed-file", "2026-10-02T15:00:00.000Z", "op-malformed-1")[0].events[0])
    files["traces/malformed-line.jsonl"] = (healthy_line + "\n" + healthy_line[:57] + "\n").encode()
    files["sidecar/malformed-line.jsonl"] = b""
    oversize = {"schema_version": "trace-v1", "event_id": "run-oversize:e1", "sequence": 1,
                "event_type": "serving.diagnostic", "session_id": "sess-oversize", "run_id": "run-oversize",
                "trace_id": "run-oversize", "occurred_at": "2026-10-02T15:10:00.000Z",
                "source": {"component": "backend.service", "mapping_version": MAPPING},
                "evidence_class": "serving_observation", "payload": {"blob": "x" * 70_000}}
    files["traces/oversize-line.jsonl"] = (dumps_line(oversize) + "\n").encode()
    files["sidecar/oversize-line.jsonl"] = b""
    access["malformed-line/run-malformed-file"] = access["oversize-line/run-oversize"] = "tenant-a"
    for table in PRICING:
        files[f"pricing/{table['table_id']}.json"] = (json.dumps(table, indent=2, sort_keys=True) + "\n").encode()
    files["access.json"] = (json.dumps({"access_version": "obs-access-fixture-v1", "synthetic": True,
                                        "note": "Fixture stand-in for serving's principal/tenant ownership table. "
                                                "A run id is never authorization.",
                                        "owners": dict(sorted(access.items()))}, indent=2) + "\n").encode()
    files["effects.json"] = (json.dumps({"effects_version": "obs-effects-v1", "source": "evalgate.simulator.RetailSimulator",
                                         "note": "Independent ground truth per operation; the dashboard never reads this file.",
                                         "operations": dict(sorted(effects.items()))}, indent=2) + "\n").encode()
    case_names = [name for name, _, _ in cases] + ["malformed-line", "oversize-line"]
    entries = [{"case_id": n, "trace": f"traces/{n}.jsonl", "trace_sha256": sha256(files[f"traces/{n}.jsonl"]).hexdigest(),
                "sidecar": f"sidecar/{n}.jsonl", "sidecar_sha256": sha256(files[f"sidecar/{n}.jsonl"]).hexdigest()}
               for n in case_names]
    # The healthy artifact delivered a second time (e.g. an exporter retry): duplicate ingestion, not a new request.
    entries.append({**entries[0], "case_id": "healthy-redelivered"})
    files["cases.json"] = (json.dumps({"fixture_set": "obs-fixtures-v1", "data_origin": "synthetic_fixture",
                                       "cases": entries,
                                       "pricing": [f"pricing/{t['table_id']}.json" for t in PRICING],
                                       "access": "access.json"}, indent=2) + "\n").encode()
    return files


def manifest(files: dict[str, bytes]) -> bytes:
    return (json.dumps({"fixture_set": "obs-fixtures-v1", "generator": GENERATOR_VERSION,
                        "generated_from": {"contract": "contracts/trace-v1.schema.json", "hash": "evalgate.scoring.payload_hash",
                                           "effects": "evalgate.simulator.RetailSimulator"},
                        "synthetic": True,
                        "files": {path: sha256(data).hexdigest() for path, data in sorted(files.items())}},
                       indent=2) + "\n").encode()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    files = build()
    hashed = dict(files)
    oracle = OUT / "oracle.json"  # hand-written; frozen by hash here, never generated
    if oracle.is_file():
        hashed["oracle.json"] = oracle.read_bytes()
    files["MANIFEST.json"] = manifest(hashed)
    if args.check:
        drift = [p for p, data in files.items() if not (OUT / p).is_file() or (OUT / p).read_bytes() != data]
        print("fixtures: UNCHANGED" if not drift else f"fixtures: DRIFT in {drift}")
        return 1 if drift else 0
    for path, data in files.items():
        target = OUT / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    print(f"wrote {len(files)} files to {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
