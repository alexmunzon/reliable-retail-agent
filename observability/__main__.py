"""python3 -m observability {report,serve,export} — offline, read-only, synthetic fixtures only.

  report  [--principal P] [--json OUT]      print the aggregate report (and optionally write full JSON)
  serve   [--principal P] [--port N]        loopback-only dashboard at http://127.0.0.1:N
  export  KEY [--principal P] --out FILE    write one request's redacted evidence export
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .report import Dataset


def _ms(ns):
    return "unknown" if ns is None else f"{ns / 1_000_000:g} ms"


def cmd_report(args) -> int:
    data = Dataset()
    viewer = args.principal
    rows = data.visible(viewer)
    print(f"SYNTHETIC fixture set {data.fixture_set} (data origin: {data.data_origin}); live serving integration: NOT INTEGRATED")
    print(f"viewer {viewer}: {len(rows)} request(s); artifact redeliveries ignored: {data.redeliveries}; "
          f"set problems: {', '.join(sorted({p['code'] for p in data.problems})) or 'none'}")
    for name, agg in data.aggregates(viewer, warmup_per_process=args.warmup).items():
        lat = agg["latency"]
        print(f"\n[{name}] requests={agg['requests']} statuses={agg['status_counts']}")
        print(f"  latency (nearest rank, failed included): n={lat['eligible']} excluded={lat['excluded'] or 'none'} "
              f"p50={_ms(lat['p50_ns'])} (rank {lat['p50_rank']}) p95={_ms(lat['p95_ns'])} (rank {lat['p95_rank']})")
        if lat["warning"]:
            print(f"  note: {lat['warning']}")
        c = agg["costs"]
        print(f"  estimated charge (SYNTHETIC prices, complete requests): {c['estimated_charge_complete_requests'] or 'none'}; "
              f"requests with unknown estimate (no usage or unpriced): {c['requests_with_unknown_estimate']}; confirmed (receipts): {c['confirmed_charge'] or 'none'}")
    if args.json:
        out = {"meta": data.meta(viewer), "aggregates": data.aggregates(viewer, warmup_per_process=args.warmup),
               "requests": rows}
        Path(args.json).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
        print(f"\nwrote {args.json}")
    return 0


def cmd_serve(args) -> int:
    from .server import DashboardServer
    server = DashboardServer(Dataset(), args.principal, args.port)
    print(f"SYNTHETIC trace dashboard (read-only) for {args.principal}: http://127.0.0.1:{server.server_address[1]}  (Ctrl+C stops)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def cmd_export(args) -> int:
    found = Dataset().export(args.principal, args.key)
    if found is None:
        print("not found for this principal", file=sys.stderr)
        return 2
    Path(args.out).write_text(json.dumps(found, indent=2, sort_keys=True) + "\n")
    print(f"wrote redacted export {args.out}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m observability", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("report", "serve", "export"):
        p = sub.add_parser(name)
        p.add_argument("--principal", default="tenant-a", help="fixture viewer (server-side configuration)")
        if name == "report":
            p.add_argument("--json")
            p.add_argument("--warmup", type=int, default=0, help="exclude the first N requests per process incarnation")
        if name == "serve":
            p.add_argument("--port", type=int, default=8770)
        if name == "export":
            p.add_argument("key")
            p.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    return {"report": cmd_report, "serve": cmd_serve, "export": cmd_export}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
