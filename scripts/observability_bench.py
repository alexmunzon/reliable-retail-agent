#!/usr/bin/env python3
"""Measure the declared synthetic workload: ingest+projection, list page, detail, aggregates, export, memory.

  python3 scripts/observability_bench.py [--requests 2000] [--out RECEIPT.json]

Numbers describe this machine on this run only; they are evidence, not CI thresholds.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import observability_fixtures as gen  # noqa: E402
from observability.report import Dataset  # noqa: E402


def synthesize(root: Path, count: int, per_artifact: int = 250) -> None:
    """`count` healthy-shaped requests (10 spans, 1 usage, 1 receipt each), rotated into artifact pairs of
    `per_artifact` requests so each file stays under the 4 MiB ingress limit."""
    runs = [gen.healthy(f"run-bench-{i:05d}", f"2026-10-04T{(i // 3600) % 24:02d}:{(i // 60) % 60:02d}:{i % 60:02d}.000Z",
                        f"op-bench-{i:05d}")[0] for i in range(count)]
    for folder in ("traces", "sidecar", "pricing"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    cases, owners = [], {}
    for n, start in enumerate(range(0, count, per_artifact)):
        chunk, name = runs[start:start + per_artifact], f"bench-{n:03d}"
        trace = ("\n".join(gen.dumps_line(e) for r in chunk for e in r.events) + "\n").encode()
        side = ("\n".join(gen.dumps_line(s) for r in chunk for s in r.side) + "\n").encode()
        (root / "traces" / f"{name}.jsonl").write_bytes(trace)
        (root / "sidecar" / f"{name}.jsonl").write_bytes(side)
        cases.append({"case_id": name, "trace": f"traces/{name}.jsonl", "trace_sha256": sha256(trace).hexdigest(),
                      "sidecar": f"sidecar/{name}.jsonl", "sidecar_sha256": sha256(side).hexdigest()})
        owners.update({f"{name}/{r.run_id}": "tenant-a" for r in chunk})
    for table in gen.PRICING:
        (root / "pricing" / f"{table['table_id']}.json").write_text(json.dumps(table))
    (root / "access.json").write_text(json.dumps({"owners": owners}))
    (root / "cases.json").write_text(json.dumps({
        "fixture_set": "obs-fixtures-v1", "data_origin": "synthetic_fixture", "cases": cases,
        "pricing": [f"pricing/{t['table_id']}.json" for t in gen.PRICING], "access": "access.json"}))


def timed(fn, repeat=5):
    best = None
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn()
        elapsed = time.perf_counter() - start
        best = elapsed if best is None else min(best, elapsed)
    return result, round(best * 1000, 3)


def measure(root: Path | None, label: str) -> dict:
    data, load_ms = timed(lambda: Dataset(root) if root else Dataset(), repeat=3)
    tracemalloc.start()  # separate pass: the tracer slows Python several-fold, so it never shares a timing run
    Dataset(root) if root else Dataset()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    keys = [r["key"] for r in data.visible("tenant-a")]
    _, page_ms = timed(lambda: data.page("tenant-a", page=2, page_size=100))
    _, filter_ms = timed(lambda: data.page("tenant-a", status="unknown_outcome", page_size=100))
    _, detail_ms = timed(lambda: data.get("tenant-a", keys[len(keys) // 2]))
    _, agg_ms = timed(lambda: data.aggregates("tenant-a"))
    export, export_ms = timed(lambda: json.dumps(data.export("tenant-a", keys[0])))
    folder = root or (ROOT / "observability" / "fixtures" / "v1")
    return {"workload": label, "requests": len(data.requests),
            "spans": sum(r["timing"]["spans_accepted"] for r in data.requests.values()),
            "input_bytes": sum(p.stat().st_size for p in folder.rglob("*") if p.is_file()),
            "ingest_and_project_ms_best_of_3": load_ms, "peak_python_heap_mib_during_load": round(peak / 2**20, 2),
            "list_page_ms_best_of_5": page_ms, "filtered_list_ms_best_of_5": filter_ms,
            "detail_ms_best_of_5": detail_ms, "aggregates_ms_best_of_5": agg_ms,
            "export_one_request_ms_best_of_5": export_ms, "export_bytes": len(export),
            "set_problems": sorted({p["code"] for p in data.problems})}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=2000)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    results = [measure(None, "frozen fixture set obs-fixtures-v1")]
    with tempfile.TemporaryDirectory(prefix="obs-bench-") as tmp:
        root = Path(tmp) / "set"
        synthesize(root, args.requests)
        results.append(measure(root, f"synthesized {args.requests} healthy-shaped requests"))
        shutil.rmtree(root)
    receipt = {"bench": "obs-bench-v1", "python": platform.python_version(), "machine": platform.machine(),
               "system": platform.system(), "note": "single local run; not a CI threshold", "results": results,
               "instrumentation_overhead": "not applicable: no code in any dispatch or serving path (integration held)"}
    text = json.dumps(receipt, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
