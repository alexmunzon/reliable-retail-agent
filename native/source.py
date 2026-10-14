"""Pin verification and explicit import bootstrap for unchanged author source."""
from __future__ import annotations

from hashlib import sha256
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'vendor' / 'tau-bench'
COMMIT = '59a200c6d575d595120f1cb70fea53cef0632f6b'


def verify_checkout(vendor: Path, commit: str, manifest_name: str) -> dict:
    """Live Git tree verification, or portable hashes produced from that tree.

    Bundled offline manifests are provenance records, not independent signatures.
    Independent provenance can be rechecked by fetching the public declared pin.
    """
    manifest_path = ROOT/'native'/'source_manifests'/manifest_name
    if (vendor/'.git').exists():
        def git(*args):
            return subprocess.check_output(['git', '-C', str(vendor), *args])
        if git('rev-parse', 'HEAD').decode().strip() != commit:
            raise RuntimeError('Author checkout is not at the declared pin')
        tracked = filter(None, git('ls-files', '-z').decode().split('\0'))
        hashes = {}
        for name in tracked:
            expected = git('show', f'{commit}:{name}')
            actual = (vendor/name).read_bytes()
            if expected != actual:
                raise RuntimeError(f'Author source differs from pin: {name}')
            hashes[name] = sha256(actual).hexdigest()
        method = 'live_git_tree_at_declared_commit'
    else:
        manifest = json.loads(manifest_path.read_text())
        if manifest['commit'] != commit:
            raise RuntimeError('Bundled provenance manifest pin mismatch')
        hashes = manifest['source_sha256']
        for name, expected in hashes.items():
            if sha256((vendor/name).read_bytes()).hexdigest() != expected:
                raise RuntimeError(f'Author source differs from pinned source manifest: {name}')
        method = 'bundled_sha256_manifest_previously_verified_against_git'
    expected_python = {name for name in hashes if name.endswith('.py')}
    actual_python = {str(path.relative_to(vendor)) for path in vendor.rglob('*.py')}
    if expected_python != actual_python:
        raise RuntimeError('Unexpected or missing Python files in author source tree')
    if 'MIT License' not in (vendor/'LICENSE').read_text():
        raise RuntimeError('Expected retained upstream MIT license')
    return {'repository': 'https://github.com/sierra-research/tau-bench', 'commit': commit,
            'license': 'MIT', 'tracked_files_verified': len(hashes), 'source_sha256': hashes,
            'upstream_modified': False, 'source_validation_method': method}


def provenance() -> dict:
    result = verify_checkout(VENDOR, COMMIT, 'later.json')
    result['dependencies'] = {}
    for name in ('litellm', 'pydantic', 'openai'):
        try:
            result['dependencies'][name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result['dependencies'][name] = None
    result['python'] = sys.version
    return result


def bootstrap() -> None:
    sys.dont_write_bytecode = True
    if not VENDOR.is_dir():
        raise RuntimeError('Missing vendor/tau-bench; see docs/NATIVE_INTEGRATION.md')
    verify_checkout(VENDOR, COMMIT, 'later.json')
    sys.path.insert(0, str(VENDOR))
