"""Paper-era candidate source: sole visible commit preceding paper submission."""
import importlib.metadata
import sys

from native.source import ROOT, verify_checkout

COMMIT = '6f4b718037db619539b8b692060e6686f3f0dcc9'
VENDOR = ROOT / 'vendor' / 'tau-bench-paper-era'


def provenance():
    result = verify_checkout(VENDOR, COMMIT, 'paper-era.json')
    result.update(selection='sole_visible_commit_preceding_paper_submission',
                  exact_paper_run_snapshot_proven=False, python=sys.version,
                  dependencies={name: importlib.metadata.version(name)
                                for name in ('openai', 'tenacity', 'termcolor')})
    return result


def bootstrap():
    sys.dont_write_bytecode = True
    if any(name == 'tau_bench' or name.startswith('tau_bench.') for name in sys.modules):
        raise RuntimeError('Paper-era source requires an isolated fresh Python process')
    verify_checkout(VENDOR, COMMIT, 'paper-era.json')
    sys.path.insert(0, str(VENDOR))
