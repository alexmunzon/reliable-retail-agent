"""Offline source and published-experiment inventory; never imports actors."""
from pathlib import Path
import hashlib
import json
from native.paper_source import ROOT, provenance

def inventory():
    primary = provenance()
    # Public snapshots may retain only the four released trajectories, rather
    # than the unrelated later runtime. Check those bytes against the existing
    # manifest previously verified from the official Git tree.
    later = json.loads((ROOT/'native'/'source_manifests'/'later.json').read_text())
    if later['commit'] != '59a200c6d575d595120f1cb70fea53cef0632f6b':
        raise ValueError('Later author release pin mismatch')
    audit_path = ROOT/'reproduction'/'evidence'/'paper-audit.json'
    audit = json.loads(audit_path.read_text())
    result_files = [p for p in later['source_sha256'] if p.startswith('historical_trajectories/') and p.endswith('.json')]
    if len(result_files) != 4:
        raise ValueError('Expected four official later released trajectory files')
    for path in result_files:
        if hashlib.sha256((ROOT/'vendor'/'tau-bench'/path).read_bytes()).hexdigest() != later['source_sha256'][path]:
            raise ValueError(f'Author release artifact hash mismatch: {path}')
    return {'schema_version': 1, 'evidence_type': 'offline_source_inventory',
        'model_api_requests': 0, 'primary': primary,
        'paper_audit_sha256': hashlib.sha256(audit_path.read_bytes()).hexdigest(),
        'experiments': audit['experiment_inventory'], 'paper_method': audit['paper_method'],
        'model_profiles': audit['historical_model_profiles'],
        'original_paper_era_released_trajectory_files': [p for p in primary['source_sha256'] if 'trajector' in p or p.startswith('results/')],
        'later_author_release': {'commit': later['commit'], 'files': {p: later['source_sha256'][p] for p in result_files},
            'verification': 'artifact SHA-256 against bundled manifest previously verified from official Git tree',
            'interpretation': 'Later released author artifacts; not original paper-v1 trials'},
        'source_limitations': audit['source_discrepancies_and_limitations']}
