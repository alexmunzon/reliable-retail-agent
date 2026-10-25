"""Bounded, digest-bound ingestion of versioned traces + obs-sidecar-v1. Read-only, stdlib only.

Order of work (each step bounds the next): manifest path allowlist -> byte/line/depth/string limits ->
sha256 binding -> strict JSON (no duplicate keys, no NaN) -> the UNCHANGED evalgate.trace checker on each
stream in original file order (its verdict is kept as-is) -> a separately labelled diagnostic view that
drops identical repeated lines and orders by sequence, without ever upgrading the contract verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
from pathlib import Path
import re

from evalgate import trace as contract  # versioned contract checker, used as-is

from .sidecar import ID, validate_record

CASE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
ARTIFACT = re.compile(r"(traces|sidecar)/[a-z0-9][a-z0-9-]{0,63}\.jsonl")
PRICING_FILE = re.compile(r"pricing/[a-z0-9][a-z0-9-]{0,63}\.json")
SHA = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class Limits:
    max_manifest_bytes: int = 256 * 1024
    max_artifact_bytes: int = 4 * 1024 * 1024
    max_line_bytes: int = 64 * 1024
    max_lines: int = 20_000
    max_depth: int = 8
    max_string: int = 512
    max_cases: int = 200
    max_requests: int = 10_000
    max_spans_per_request: int = 2_000
    max_sidecar_records_per_request: int = 4_000
    max_page_size: int = 100


# Contract diagnostics echo field names, event types and ids; they are reduced to fixed codes here.
_CONTRACT_CODES = (
    ("duplicate event_id", "contract.duplicate_event_id"), ("not strictly increasing", "contract.sequence_not_increasing"),
    ("1..N without gaps", "contract.sequence_gap"), ("serving.run_finished", "contract.missing_run_finished"),
    ("no recorded outcome", "contract.attempt_without_outcome"), ("without a fresh authorization", "contract.attempt_without_authorization"),
    ("second attempt", "contract.second_attempt"), ("unknown and unreconciled", "contract.attempt_while_unknown"),
    ("before the previous attempt", "contract.overlapping_attempts"), ("reconciliation of", "contract.bad_reconciliation"),
    ("does not answer", "contract.authorization_mismatch"), ("differs from", "contract.tool_changed"),
    ("outcome without", "contract.outcome_without_attempt"), ("disallowed fields", "contract.disallowed_fields"),
    ("missing fields", "contract.missing_fields"), ("payload has disallowed", "contract.disallowed_payload_fields"),
    ("not one complete JSON", "contract.unparseable_line"), ("no events", "contract.no_events"),
)


def contract_code(message: str) -> str:
    for needle, code in _CONTRACT_CODES:
        if needle in message:
            return code
    return "contract.other_problem"


def _unique_object(pairs):
    if len({k for k, _ in pairs}) != len(pairs):
        raise ValueError("duplicate key")
    return dict(pairs)


def _reject_constant(_):
    raise ValueError("non-finite number")


def _shape_ok(value, limits: Limits, depth: int = 0) -> str | None:
    if depth > limits.max_depth:
        return "value_too_deep"
    if isinstance(value, str):
        return "string_too_long" if len(value) > limits.max_string else None
    if isinstance(value, dict):
        for k, v in value.items():
            problem = (_shape_ok(k, limits, depth + 1) or _shape_ok(v, limits, depth + 1))
            if problem:
                return problem
    elif isinstance(value, list):
        for v in value:
            problem = _shape_ok(v, limits, depth + 1)
            if problem:
                return problem
    return None


def safe_path(root: Path, relative, pattern: re.Pattern) -> Path | None:
    """Only manifest-declared names of a fixed shape, resolved inside root, never through a symlink."""
    if not (isinstance(relative, str) and pattern.fullmatch(relative)):
        return None
    candidate = root / relative
    try:
        if candidate.is_symlink() or candidate.parent.is_symlink():
            return None
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    return resolved if resolved.is_relative_to(root) and resolved.is_file() else None


def read_bounded(path: Path, limit: int) -> tuple[bytes | None, str | None]:
    try:
        if path.stat().st_size > limit:
            return None, "artifact_too_large"
        with path.open("rb") as handle:
            data = handle.read(limit + 1)
    except OSError:
        return None, "artifact_unreadable"
    return (None, "artifact_too_large") if len(data) > limit else (data, None)


def parse_lines(data: bytes, limits: Limits) -> tuple[list[tuple[int, dict]], dict[str, int]]:
    """Decoded objects with their 1-based line numbers, plus counts of rejected lines by code."""
    lines = data.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    rejected: dict[str, int] = {}
    if len(lines) > limits.max_lines:
        rejected["too_many_lines"] = len(lines) - limits.max_lines
        lines = lines[:limits.max_lines]
    out = []
    for number, raw in enumerate(lines, 1):
        raw = raw.removesuffix(b"\r")
        code = None
        if len(raw) > limits.max_line_bytes:
            code = "line_too_large"  # never decoded or parsed
        else:
            try:
                value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_reject_constant)
            except (UnicodeDecodeError, ValueError, RecursionError):
                code = "unparseable_line"
            else:
                code = "not_object" if not isinstance(value, dict) else _shape_ok(value, limits)
        if code:
            rejected[code] = rejected.get(code, 0) + 1
        else:
            out.append((number, value))
    return out, rejected


@dataclass
class Stream:
    """One request: (artifact, run_id, trace_id). `raw` is file order; `events` is the diagnostic view."""
    key: str
    case_id: str
    artifact_sha256: str
    run_id: str
    trace_id: str
    raw: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    contract_codes: dict[str, int] = field(default_factory=dict)
    completeness: list[str] = field(default_factory=list)
    duplicate_lines: int = 0
    conflicting_event_ids: int = 0
    reordered_lines: int = 0
    envelope_invalid_events: int = 0
    sidecar: list[dict] = field(default_factory=list)
    sidecar_rejected: dict[str, int] = field(default_factory=dict)
    sidecar_problems: list[str] = field(default_factory=list)

    @property
    def contract_verdict(self) -> str:
        return "INVALID" if self.contract_codes else "CONSISTENT"


def _bump(counter: dict, code: str, n: int = 1) -> None:
    counter[code] = counter.get(code, 0) + n


def build_streams(case_id: str, artifact_sha: str, parsed: list[tuple[int, dict]], rejected_lines: dict[str, int]) -> tuple[list[Stream], dict]:
    streams: dict[tuple, Stream] = {}
    unattributable = sum(rejected_lines.values())
    for _, event in parsed:
        run, trace = event.get("run_id"), event.get("trace_id")
        if not (isinstance(run, str) and ID.fullmatch(run) and isinstance(trace, str) and ID.fullmatch(trace)):
            unattributable += 1
            continue
        key = f"{case_id}/{run}" if run == trace else f"{case_id}/{run}/{trace}"
        stream = streams.setdefault((run, trace), Stream(key, case_id, artifact_sha, run, trace))
        stream.raw.append(event)
    for stream in streams.values():
        # 1. The frozen contract verdict on exactly what the artifact holds, in file order.
        for message in contract.validate_trace(stream.raw):
            _bump(stream.contract_codes, contract_code(message))
        if unattributable:
            _bump(stream.contract_codes, "contract.unparseable_line", unattributable)
            stream.completeness.append("unattributable_line_in_artifact")
        # 2. Diagnostic view: envelope-valid events, identical repeats removed, causal (sequence) order.
        by_id, view = {}, []
        for event in stream.raw:
            if contract.validate_event(event):
                stream.envelope_invalid_events += 1
                continue
            previous = by_id.get(event["event_id"])
            if previous is not None:
                if previous == event:
                    stream.duplicate_lines += 1
                else:
                    stream.conflicting_event_ids += 1
                continue
            by_id[event["event_id"]] = event
            view.append(event)
        highest = 0
        for event in view:
            if event["sequence"] < highest:
                stream.reordered_lines += 1
            highest = max(highest, event["sequence"])
        stream.events = sorted(view, key=lambda e: e["sequence"])
        serving = any(e["evidence_class"] == "serving_observation" for e in stream.events)
        if serving and [e["sequence"] for e in stream.events] != list(range(1, len(stream.events) + 1)):
            stream.completeness.append("sequence_gap")
        open_attempts = {}
        for event in stream.events:
            k = event.get("operation_id") or event.get("payload_hash")
            if event["event_type"] == "dispatch_started":
                open_attempts[k] = True
            elif event["event_type"] in contract.OUTCOME_TYPES:
                open_attempts.pop(k, None)
        if open_attempts:
            stream.completeness.append("attempt_without_outcome")
        finished = [e for e in stream.events if e["event_type"] == "serving.run_finished"]
        if serving and (len(finished) != 1 or stream.events[-1]["event_type"] != "serving.run_finished"):
            stream.completeness.append("missing_run_finished")
        if len({e["evidence_class"] for e in stream.events}) > 1:
            _bump(stream.contract_codes, "observability.mixed_evidence_classes")
    return list(streams.values()), {"unattributable_lines": unattributable, "rejected_lines": rejected_lines}


def attach_sidecar(streams: list[Stream], parsed: list[tuple[int, dict]], rejected_lines: dict[str, int], limits: Limits) -> dict:
    index = {(s.run_id, s.trace_id): s for s in streams}
    artifact = {"rejected_lines": dict(rejected_lines), "unbound_records": 0, "invalid_unattributable": 0}
    seen: dict[tuple, dict] = {}
    conflicted: set[tuple] = set()
    for _, record in parsed:
        problems = validate_record(record)
        identity = (record.get("run_id"), record.get("trace_id")) if isinstance(record, dict) else ()
        stream = index.get(identity) if len(identity) == 2 and all(isinstance(v, str) for v in identity) else None
        if problems:
            if stream is not None:
                _bump(stream.sidecar_rejected, problems[0])  # one count per rejected record
            else:
                artifact["invalid_unattributable"] += 1
            continue
        if stream is None:
            artifact["unbound_records"] += 1  # names a stream this artifact pair does not contain
            continue
        key = (stream.key, record["record_id"])
        if key in seen:
            if seen[key] != record:
                conflicted.add(key)
            else:
                _bump(stream.sidecar_rejected, "sidecar.duplicate_record")
            continue
        if len(stream.sidecar) >= limits.max_sidecar_records_per_request:
            _bump(stream.sidecar_rejected, "sidecar.over_record_limit")
            continue
        seen[key] = record
        stream.sidecar.append(record)
    for stream in streams:
        keep = []
        for record in stream.sidecar:
            if (stream.key, record["record_id"]) in conflicted:
                _bump(stream.sidecar_rejected, "sidecar.conflicting_record_id")
            else:
                keep.append(record)
        stream.sidecar = keep
        if rejected_lines or artifact["unbound_records"] or artifact["invalid_unattributable"]:
            # The bad record cannot safely be assigned to one request; every request in
            # this bounded artifact pair must disclose that its metrics may be incomplete.
            stream.sidecar_problems.append("sidecar.unattributable_records")
    return artifact


def load_fixture_set(root: Path, limits: Limits = Limits()) -> dict:
    """Load cases.json-declared artifact pairs. Returns streams, pricing tables, owners and set-level problems."""
    root = Path(root).resolve()
    problems: list[dict] = []
    manifest_path = safe_path(root, "cases.json", re.compile(r"cases\.json"))
    data, error = (read_bounded(manifest_path, limits.max_manifest_bytes) if manifest_path else (None, "manifest_missing"))
    if error:
        raise ValueError(error)
    manifest = json.loads(data, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    cases = manifest.get("cases", [])
    if not isinstance(cases, list) or len(cases) > limits.max_cases:
        raise ValueError("manifest_cases_invalid")
    streams: list[Stream] = []
    delivered: dict[str, str] = {}
    redeliveries = 0
    for entry in cases:
        case_id = entry.get("case_id") if isinstance(entry, dict) else None
        if not (isinstance(case_id, str) and CASE.fullmatch(case_id)):
            problems.append({"case": None, "code": "manifest_entry_invalid"})
            continue
        trace_sha, side_sha = entry.get("trace_sha256"), entry.get("sidecar_sha256")
        if not (isinstance(trace_sha, str) and SHA.fullmatch(trace_sha)):
            problems.append({"case": case_id, "code": "manifest_digest_missing"})
            continue
        if trace_sha in delivered:
            if delivered[trace_sha] == side_sha:
                redeliveries += 1  # the identical artifact pair again: duplicate ingestion, not new requests
                problems.append({"case": case_id, "code": "artifact_redelivery_ignored"})
            else:
                problems.append({"case": case_id, "code": "artifact_redelivery_conflict"})
            continue
        loaded, side_error = {}, None
        for role, pattern_key, digest in (("trace", "trace", trace_sha), ("sidecar", "sidecar", side_sha)):
            path = safe_path(root, entry.get(pattern_key), ARTIFACT)
            if not (isinstance(digest, str) and SHA.fullmatch(digest)):
                raw, error = None, "digest_missing"
            else:
                raw, error = read_bounded(path, limits.max_artifact_bytes) if path else (None, "path_rejected")
            if not error and sha256(raw).hexdigest() != digest:
                error = "digest_mismatch"
            if error:
                problems.append({"case": case_id, "code": f"{role}_{error}"})
                if role == "sidecar":
                    side_error = f"sidecar.{error}"
                break
            loaded[role] = raw
        if "trace" not in loaded:
            continue
        delivered[trace_sha] = side_sha
        parsed, rejected = parse_lines(loaded["trace"], limits)
        for code, count in rejected.items():
            problems.append({"case": case_id, "code": code, "count": count})
        case_streams, _ = build_streams(case_id, trace_sha, parsed, rejected)
        # An optional metrics artifact cannot erase a verified authoritative trace.
        # Unverified sidecar bytes are never parsed, even when the trace remains visible.
        if side_error:
            for stream in case_streams:
                _bump(stream.sidecar_rejected, side_error)
        side_parsed, side_rejected = parse_lines(loaded.get("sidecar", b""), limits)
        side_summary = attach_sidecar(case_streams, side_parsed, side_rejected, limits)
        if side_summary["unbound_records"] or side_summary["invalid_unattributable"] or side_rejected:
            problems.append({"case": case_id, "code": "sidecar_records_unbound_or_unparseable",
                             "count": side_summary["unbound_records"] + side_summary["invalid_unattributable"] + sum(side_rejected.values())})
        streams.extend(case_streams)
        if len(streams) > limits.max_requests:
            raise ValueError("too_many_requests")
    tables = []
    for relative in manifest.get("pricing", []):
        path = safe_path(root, relative, PRICING_FILE)
        raw, error = read_bounded(path, limits.max_manifest_bytes) if path else (None, "path_rejected")
        if error:
            problems.append({"case": None, "code": f"pricing_{error}"})
            continue
        table = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        from .costs import validate_table
        if validate_table(table):
            problems.append({"case": None, "code": "pricing_table_rejected"})
            continue
        tables.append(table)
    access_path = safe_path(root, manifest.get("access"), re.compile(r"access\.json"))
    raw, error = read_bounded(access_path, limits.max_manifest_bytes) if access_path else (None, "access_missing")
    owners = {} if error else json.loads(raw, object_pairs_hook=_unique_object).get("owners", {})
    return {"fixture_set": manifest.get("fixture_set"), "data_origin": manifest.get("data_origin"),
            "streams": streams, "tables": tables, "owners": owners, "problems": problems, "redeliveries": redeliveries}
