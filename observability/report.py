"""Fixture-set report: owner-scoped requests, cross-run attempt flags, aggregates and redacted export.

Every accessor takes the viewer principal from trusted configuration (never from a request parameter) and
returns only requests that principal owns. This is fixture isolation, not authentication: a live integration
must reuse serving's principal/tenant authorization, and a run id is never authorization.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import re

from . import FIXTURE_SET_VERSION, REPORT_VERSION, SIDECAR_VERSION
from .costs import money
from .ingest import Limits, load_fixture_set
from .project import project

DATA_ORIGINS = ("synthetic_fixture",)  # live serving telemetry is NOT INTEGRATED (see docs/observability)
STATUSES = ("completed", "failed", "unknown_outcome", "cancelled", "incomplete", "invalid_evidence")
DEFAULT_ROOT = Path(__file__).resolve().parent / "fixtures" / "v1"


def nearest_rank(values: list[int], percent: int) -> tuple[int | None, int | None]:
    """Nearest-rank percentile: the ceil(p/100 * n)-th smallest value (integer arithmetic). n=0 -> unknown."""
    if not values:
        return None, None
    ordered = sorted(values)
    rank = max(1, -(-percent * len(ordered) // 100))
    return ordered[rank - 1], rank


class Dataset:
    def __init__(self, root: Path = DEFAULT_ROOT, limits: Limits = Limits()):
        loaded = load_fixture_set(root, limits)
        if loaded["data_origin"] not in DATA_ORIGINS or loaded["fixture_set"] != FIXTURE_SET_VERSION:
            raise ValueError("unsupported fixture set or data origin")
        self.limits = limits
        self.fixture_set, self.data_origin = loaded["fixture_set"], loaded["data_origin"]
        self.problems, self.redeliveries = loaded["problems"], loaded["redeliveries"]
        self.tables = loaded["tables"]
        self.owners = loaded["owners"]
        self.requests = {s.key: project(s, self.tables, self.data_origin, limits) for s in loaded["streams"]}
        self._flag_cross_run_attempts()
        # Index once at load: list/detail/aggregate never rescan raw artifacts.
        self._by_owner: dict[str, list[str]] = {}
        for key in sorted(self.requests, key=lambda k: (self.requests[k]["started_at"] or "", k)):
            owner = self.owners.get(key)
            if owner:  # unowned requests are visible to no one
                self._by_owner.setdefault(owner, []).append(key)

    def _flag_cross_run_attempts(self):
        """trace-check cannot see one operation attempted in two streams (contracts/HANDOFF.md); flag it here,
        within one owner only, so the flag itself never reveals another tenant's activity."""
        attempts: dict[tuple, list[str]] = {}
        for key, request in self.requests.items():
            for op_id, op in request["operations"].items():
                if op["mutation_attempts"]:
                    attempts.setdefault((self.owners.get(key), op_id), []).append(key)
        for (owner, op_id), keys in attempts.items():
            total = sum(self.requests[k]["operations"][op_id]["mutation_attempts"] for k in keys)
            for k in keys:
                self.requests[k]["operations"][op_id]["attempts_across_runs"] = total
                if len(keys) > 1:
                    flag = "repeated_attempt_across_runs"
                    if flag not in self.requests[k]["evidence"]["flags"]:
                        self.requests[k]["evidence"]["flags"].append(flag)

    # ---- owner-scoped accessors (the only way the server reads data)
    def visible(self, principal: str) -> list[dict]:
        return [self.requests[k] for k in self._by_owner.get(principal, [])]

    def get(self, principal: str, key: str) -> dict | None:
        return self.requests.get(key) if self.owners.get(key) == principal and key in self.requests else None

    def page(self, principal: str, *, status=None, route=None, start=None, end=None, page=1, page_size=20) -> dict:
        page_size = max(1, min(int(page_size), self.limits.max_page_size))
        rows = filter_requests(self.visible(principal), status=status, route=route, start=start, end=end)
        pages = max(1, -(-len(rows) // page_size))
        page = max(1, min(int(page), pages))
        chunk = rows[(page - 1) * page_size: page * page_size]
        return {"total": len(rows), "page": page, "pages": pages, "page_size": page_size,
                "items": [summary(r) for r in chunk]}

    def aggregates(self, principal: str, *, start=None, end=None, warmup_per_process: int = 0) -> dict:
        return aggregate(self.visible(principal), start=start, end=end, warmup_per_process=warmup_per_process)

    def meta(self, principal: str) -> dict:
        return {"report_version": REPORT_VERSION, "sidecar_version": SIDECAR_VERSION, "fixture_set": self.fixture_set,
                "data_origin": self.data_origin, "synthetic": True, "live_serving_integration": "NOT INTEGRATED (held)",
                "viewer": principal, "isolation": "fixture principal from server configuration; not authentication",
                "visible_requests": len(self.visible(principal)), "artifact_redeliveries": self.redeliveries,
                "set_problems": self.problems,
                "pricing_tables": [{k: t[k] for k in ("table_id", "effective_from", "currency", "unit", "synthetic",
                                                      "source", "assumptions")} for t in self.tables],
                "limits": {"max_page_size": self.limits.max_page_size, "max_spans_per_request": self.limits.max_spans_per_request,
                           "max_line_bytes": self.limits.max_line_bytes, "max_artifact_bytes": self.limits.max_artifact_bytes},
                "statuses": list(STATUSES)}

    def export(self, principal: str, key: str) -> dict | None:
        request = self.get(principal, key)
        if request is None:
            return None
        return {"export_version": REPORT_VERSION, "synthetic": True, "data_origin": self.data_origin,
                "redaction": "allowlisted fields only; prompt text, tool arguments, free-form payloads and raw titles are never exported",
                "request": request}


_UTC = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,9}))?)?Z")


def parse_utc(text: str) -> datetime:
    """Compare instants, never strings: '10:12Z' and '10:12:00.000Z' are the same moment."""
    match = _UTC.fullmatch(text)
    if not match:
        raise ValueError("invalid UTC time")
    day, hour, minute, second, fraction = match.groups()
    micro = int((fraction or "0")[:6].ljust(6, "0"))
    return datetime.fromisoformat(f"{day}T{hour}:{minute}:{second or '00'}").replace(microsecond=micro, tzinfo=timezone.utc)


def filter_requests(rows, *, status=None, route=None, start=None, end=None):
    start, end = (parse_utc(start) if start else None), (parse_utc(end) if end else None)
    out = []
    for r in rows:
        if status and r["status"] != status:
            continue
        if route and r["route_class"] != route:
            continue
        if (start or end) and r["started_at"] is None:
            continue  # undated requests are outside every window (counted in aggregates)
        began = parse_utc(r["started_at"]) if r["started_at"] else None
        if start and began < start:
            continue
        if end and began >= end:
            continue
        out.append(r)
    return out


def summary(r: dict) -> dict:
    est = r["costs"]["estimated_charge"]
    return {"key": r["key"], "case_id": r["case_id"], "run_id": r["run_id"], "status": r["status"],
            "route_class": r["route_class"], "started_at": r["started_at"], "data_origin": r["data_origin"],
            "contract_verdict": r["evidence"]["contract_verdict"], "completeness": r["evidence"]["completeness"],
            "flags": r["evidence"]["flags"], "duration_kind": r["timing"]["duration_kind"],
            "request_duration_ns": r["timing"]["request_duration_ns"], "lower_bound_ns": r["timing"]["lower_bound_ns"],
            "estimated_charge": est["total"], "estimate_reason": est["reason"],
            "confirmed_charge": r["costs"]["confirmed_charge"],
            "mutation_attempts": sum(o["mutation_attempts"] for o in r["operations"].values())}


def aggregate(rows: list[dict], *, start=None, end=None, warmup_per_process: int = 0) -> dict:
    """Groups never mix data origin or evidence class. Latency: nearest-rank over measured request durations."""
    undated = sum(1 for r in rows if r["started_at"] is None) if (start or end) else 0
    members = filter_requests(rows, start=start, end=end)
    groups: dict[str, list[dict]] = {}
    for r in members:
        groups.setdefault(f"{r['data_origin']}/{r['evidence_class']}/{r['route_class']}", []).append(r)
    out = {}
    for name, items in sorted(groups.items()):
        counts = {s: 0 for s in STATUSES}
        sample, excluded = [], {}
        estimated, partial_known, unknown_cost, confirmed = {}, {}, 0, {}
        for r in items:
            counts[r["status"]] += 1
            reason = None
            if r["status"] == "incomplete":
                reason = "incomplete_evidence"
            elif r["status"] == "invalid_evidence":
                reason = "invalid_evidence"
            elif r["timing"]["duration_kind"] == "lower_bound":
                reason = "duration_lower_bound_only"
            elif r["timing"]["duration_kind"] == "unknown":
                reason = "duration_unknown"
            elif warmup_per_process and (r["timing"]["process_request_index"] or 0) and r["timing"]["process_request_index"] <= warmup_per_process:
                reason = "warmup"
            if reason:
                excluded[reason] = excluded.get(reason, 0) + 1
            else:
                sample.append(r["timing"]["request_duration_ns"])
            est = r["costs"]["estimated_charge"]
            if est["total"] is not None:
                for currency, amount in est["total"].items():
                    estimated[currency] = estimated.get(currency, Decimal(0)) + Decimal(amount)
            else:
                unknown_cost += 1
                for currency, amount in est["known_subtotal"].items():
                    partial_known[currency] = partial_known.get(currency, Decimal(0)) + Decimal(amount)
            for currency, amount in r["costs"]["confirmed_charge"].items():
                confirmed[currency] = confirmed.get(currency, Decimal(0)) + Decimal(amount)
        p50, r50 = nearest_rank(sample, 50)
        p95, r95 = nearest_rank(sample, 95)
        out[name] = {
            "requests": len(items), "status_counts": {k: v for k, v in counts.items() if v},
            "window": {"start": start, "end": end, "membership": "request start wall time in [start, end); display-only clock",
                       "undated_requests_outside_window": undated},
            "latency": {"metric": "request completion (request span, same process incarnation)", "unit": "ns",
                        "method": "nearest_rank", "eligible": len(sample), "excluded": excluded,
                        "warmup_excluded_per_process": warmup_per_process,
                        "p50_ns": p50, "p50_rank": r50, "p95_ns": p95, "p95_rank": r95,
                        "includes_failed_requests": True,
                        "warning": "no eligible samples: percentiles unknown" if not sample else None if len(sample) >= 100 else
                        f"small sample (n={len(sample)}): p95 is one observed value, not a stable tail estimate"},
            "costs": {"estimated_charge_complete_requests": {c: money(v) for c, v in sorted(estimated.items())},
                      "requests_with_unknown_estimate": unknown_cost,
                      "known_subtotal_of_unknown_requests": {c: money(v) for c, v in sorted(partial_known.items())},
                      "confirmed_charge": {c: money(v) for c, v in sorted(confirmed.items())},
                      "note": "estimates are tokens x dated synthetic prices; a request with no usage records or an unpriced "
                              "record has an unknown estimate (never $0); confirmed charges come only from receipts"},
        }
    return out
