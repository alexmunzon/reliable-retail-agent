#!/usr/bin/env python3
"""Run the mandatory serving suite offline: no inherited credentials, outbound TCP blocked.

Requires the pinned serving environment (requirements-serving.lock). Child processes
(provider, uvicorn) bind loopback only and inherit the same stripped environment.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SUITES = ("tests_serving.test_units", "tests_serving.test_http_api", "tests_serving.test_crash_recovery",
          "tests_serving.test_trace_contract")


def worker(names):
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from scripts.ci import block_external_connections
    block_external_connections()
    tests = unittest.defaultTestLoader.loadTestsFromNames(names)
    result = unittest.TextTestRunner(verbosity=2).run(tests)
    return 0 if result.wasSuccessful() else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", nargs="+", help=argparse.SUPPRESS)
    parser.add_argument("suites", nargs="*", default=list(SUITES))
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker)
    env = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--worker", *args.suites],
                            cwd=ROOT, env=env, timeout=1800)
    if result.returncode == 0:
        print("Serving CI passed: no credentials inherited; outbound TCP blocked; synthetic provider only.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
