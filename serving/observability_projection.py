"""Read-only authenticated serving-to-observability adapter; no command or provider access."""
from hashlib import sha256
import json
from observability.ingest import Limits, Stream, build_streams, attach_sidecar
from observability.project import project
from observability.report import Dataset
MAX_RUNS = 100
MAX_EVENTS = 2000

class LiveDataset(Dataset):
    """One immutable, digest-bound response snapshot, never cached across identities."""
    def __init__(self, service, principal, recorder, *, selected=None):
        from serving.trace_export import to_trace
        import time
        self.limits = Limits()
        self.fixture_set, self.data_origin = None, "live_synthetic_provider"
        self.tables, self.problems, self.redeliveries = [], [], 0
        self.requests, self.owners, self._by_owner = {}, {}, {}
        self.viewer = principal.tenant_id + ":" + principal.actor_id

        def read(conn):
            service.authorize(conn, principal, "runs:read")
            params = [principal.tenant_id, principal.actor_id]
            where = "r.tenant_id=? AND r.actor_id=? AND s.tenant_id=r.tenant_id AND s.actor_id=r.actor_id AND res.owner_actor_id=r.actor_id"
            if selected:
                where += " AND r.run_id=? AND r.trace_id=?"
                params.extend(selected)
            rows = conn.execute("SELECT r.run_id,r.trace_id,r.purged_through_seq FROM runs r "
                                "JOIN sessions s USING(session_id) JOIN resources res "
                                "ON res.tenant_id=s.tenant_id AND res.resource_id=s.resource_id "
                                "WHERE " + where + " ORDER BY r.created_at DESC,r.run_id LIMIT ?",
                                (*params, MAX_RUNS + 1)).fetchall()
            output, total_bytes = [], 0
            for row in rows[:MAX_RUNS]:
                events, limited = [], False
                for event in conn.execute("SELECT envelope FROM events WHERE run_id=? ORDER BY sequence LIMIT ?",
                                          (row["run_id"], MAX_EVENTS + 1)):
                    total_bytes += len(event[0].encode("utf-8"))
                    if len(events) >= MAX_EVENTS or total_bytes > self.limits.max_artifact_bytes:
                        limited = True
                        break
                    events.append(to_trace(json.loads(event[0]), "trace-v3"))
                output.append((dict(row), events, limited))
            return output, len(rows) > MAX_RUNS
        runs, limited = service._read(read)
        self.sample = {"membership": "newest_100_owned_runs" if not selected else "selected_owned_run",
                       "run_limit": MAX_RUNS, "truncated": limited, "all_history": False,
                       "event_limit_per_run": MAX_EVENTS, "byte_limit": self.limits.max_artifact_bytes}
        if limited:
            self.problems.append({"code": "newest_100_runs_only"})
        for row, events, truncated in runs:
            encoded = "".join(json.dumps(e,sort_keys=True,separators=(",",":"))+"\n" for e in events)
            digest = sha256(encoded.encode()).hexdigest()
            streams, _ = build_streams("serving", digest, list(enumerate(events, 1)), {})
            if not streams:
                streams = [Stream("serving/"+row["run_id"]+"/"+row["trace_id"], "serving", digest,
                                  row["run_id"], row["trace_id"], contract_codes={"contract.no_events":1},
                                  completeness=["missing_events"])]
            sidecar = recorder.for_run(row["run_id"], row["trace_id"], time.monotonic_ns())
            attach_sidecar(streams, list(enumerate(sidecar, 1)), {}, self.limits)
            for stream in streams:
                if truncated or row["purged_through_seq"]:
                    stream.completeness.append("live_export_truncated" if truncated else "retention_gap")
                    stream.contract_codes["contract.incomplete_live_export"] = 1
                view = project(stream, [], self.data_origin, self.limits)
                view["timing"]["coverage"] = "provider call only; process-local, expires after restart, eviction or one hour"
                view["costs"]["model_usage_applicability"] = "not_applicable_no_model_calls"
                view["sidecar_sha256"] = sha256(json.dumps(sidecar,sort_keys=True,separators=(",",":")).encode()).hexdigest()
                self.requests[stream.key], self.owners[stream.key] = view, self.viewer
        self._by_owner[self.viewer] = sorted(self.requests, key=lambda k:(self.requests[k]["started_at"] or "",k))
        self._flag_cross_run_attempts()

    def meta(self, principal):
        result = super().meta(principal)
        result.update(data_origin=self.data_origin, synthetic=True, fixture_set=None,
                      live_serving_integration="authenticated local synthetic provider",
                      isolation="current bearer credential, tenant, actor and resource ownership",
                      sample_window="newest 100 owned runs; selected detail independently addressable",
                      measurement_scope="provider span only; no model calls or billed receipts")
        return result
