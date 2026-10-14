#!/usr/bin/env python3
"""Run the published test suites without inherited credentials or outbound sockets."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]


def block_external_connections():
    """Keep loopback HTTP tests available; disallow outbound Python connections."""
    connect = socket.socket.connect
    connect_ex = socket.socket.connect_ex
    def local_only(address):
        if not isinstance(address, tuple) or address[0] not in {'127.0.0.1', '::1', 'localhost'}:
            raise RuntimeError('Outbound TCP connections are disabled in offline CI')
    def guarded_connect(self, address):
        local_only(address)
        return connect(self, address)
    def guarded_connect_ex(self, address):
        local_only(address)
        return connect_ex(self, address)
    socket.socket.connect = guarded_connect
    socket.socket.connect_ex = guarded_connect_ex
    with socket.socket() as probe:
        try:
            probe.connect(('192.0.2.1', 443))
        except RuntimeError:
            pass
        else:
            raise AssertionError('Outbound-network guard did not engage')


def worker(suite):
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    block_external_connections()
    tests = unittest.defaultTestLoader.discover(str(ROOT / suite), top_level_dir=str(ROOT))
    result = unittest.TextTestRunner(verbosity=2).run(tests)
    return 0 if result.wasSuccessful() else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', action='store_true', help='Also run original-author checks (optional packages required)')
    parser.add_argument('--worker', choices=('tests', 'tests_native'), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        return worker(args.worker)
    suites = ['tests'] + (['tests_native'] if args.native else [])
    clean_environment = {'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1'}
    for suite in suites:
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', suite],
                                cwd=ROOT, env=clean_environment, timeout=120)
        if result.returncode:
            return result.returncode
    print('Offline CI passed: no inherited credentials; outbound Python TCP connections blocked.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
