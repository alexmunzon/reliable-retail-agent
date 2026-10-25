"""Bounded read-only projection of authenticated synthetic serving observations.

The durable outbox remains authoritative. Optional provider durations are process-local,
expire on restart/eviction, and never reconstruct wall-clock durations or model usage.
"""
from collections import deque
import threading
from uuid import uuid4

from . import SIDECAR_VERSION


class SpanRecorder:
    """At most 512 records / 1 hour; no payloads, credentials or provider responses."""
    def __init__(self, capacity=512):
        self.records = deque(maxlen=capacity)
        self.evicted = 0
        self.clock_domain = "serving:" + uuid4().hex
        self.lock = threading.Lock()

    def record(self, claim, start_ns, end_ns, status):
        with self.lock:
            if len(self.records) == self.records.maxlen:
                self.evicted = min(self.evicted + 1, 2**63 - 1)
            self.records.append((claim.run_id, claim.operation_id, start_ns, end_ns, status, uuid4().hex))

    def stats(self):
        with self.lock:
            return {"retained": len(self.records), "capacity": self.records.maxlen,
                    "evicted": self.evicted, "coverage": "process-local provider spans; one-hour export age limit"}

    def for_run(self, run_id, trace_id, now_ns):
        with self.lock:
            rows = list(self.records)
        return [{"sidecar_version": SIDECAR_VERSION, "record_id": "rec-" + identity, "kind": "span",
                 "run_id": run_id, "trace_id": trace_id,
                 "emitter": {"component": "serving.worker", "emitter_version": "live-provider-v1"},
                 "span_id": "span-" + identity, "parent_span_id": None, "span_class": "dispatch_provider",
                 "clock_domain": self.clock_domain, "start_mono_ns": start, "end_mono_ns": end,
                 "status": status, "linked_event_ids": [], "operation_id": operation}
                for run, operation, start, end, status, identity in rows
                if run == run_id and 0 <= now_ns - end <= 3_600_000_000_000]
