"""Content-bound dry plan. Exact historical execution stays blocked."""
import hashlib
import json
from reproduction.inventory import inventory

def dry_plan():
    data = inventory()
    body = {'schema_version': 1, 'mode': 'offline_dry_plan', 'paid_execution_enabled': False,
        'original_baseline_only': True, 'model_evaluated': False,
        'source_commit': data['primary']['commit'],
        'source_sha256': data['primary']['source_sha256'],
        'paper_audit_sha256': data['paper_audit_sha256'],
        'experiment_scopes': data['experiments'], 'model_profiles': data['model_profiles'],
        'historical_settings': data['paper_method'],
        'offline_contract_grid': {'strategies': ['function_calling', 'react', 'act'],
            'synthetic_scenarios': ['success', 'missing_output', 'action_cutoff'], 'trial_indices': [0]},
        'blocked_reasons': ['Exact executed paper source revision is unproven',
            'Most immutable paper model snapshots and exact per-task trial counts are unspecified',
            'Original paper-era raw trajectories and annotation records are not released in the inspected tree',
            'Historical model availability is separately audited; substitutes change the experiment',
            'Paid execution is disabled in this entry point; no remaining budget is inferred'],
        'completion_claim': 'Offline implementation verification only; no paper result cell reproduced'}
    body['manifest_sha256'] = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return body
