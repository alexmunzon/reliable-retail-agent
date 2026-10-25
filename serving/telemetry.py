"""Allowlisted structured logs and low-cardinality in-memory counters.

Fields are allowlisted rather than redacted after serialization: customer text,
addresses, prompts, tokens and Authorization headers have no field to land in.
"""
from __future__ import annotations

from collections import Counter
import json
import sys
import threading
import time

LOG_FIELDS = frozenset({"event", "request_id", "method", "route", "status", "duration_ms", "code",
                        "actor_id", "tenant_id", "run_id", "operation_id", "trace_id", "sequence",
                        "state", "count", "reason", "detail_code", "effect_id", "operation_ids"})
MAX_DROPPED_LOGS = (1 << 63) - 1


class Telemetry:
    def __init__(self, stream=None):
        self._stream = stream or sys.stderr
        self._lock = threading.Lock()
        self.counters: Counter[str] = Counter()
        self.gauges: dict[str, int] = {}

    def log(self, event: str, **fields) -> None:
        with self._lock:
            try:
                record = {"ts": round(time.time(), 3), "event": event}
                record.update({key: value for key, value in fields.items() if key in LOG_FIELDS and value is not None})
                line = json.dumps(record, sort_keys=True, separators=(",", ":"))
                self._stream.write(line + "\n")
                self._stream.flush()
            except Exception:
                # Optional telemetry must never affect the durable dispatch/recording path.
                # Do not recursively log the failure or retain exception text/customer data.
                self.counters["telemetry_log_dropped"] = min(
                    MAX_DROPPED_LOGS, self.counters["telemetry_log_dropped"] + 1)

    def count(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self.counters[name] += amount

    def gauge(self, name: str, value: int) -> None:
        with self._lock:
            self.gauges[name] = value

    def snapshot(self) -> dict:
        with self._lock:
            return {"counters": dict(sorted(self.counters.items())), "gauges": dict(sorted(self.gauges.items()))}
