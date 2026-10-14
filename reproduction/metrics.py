"""Paper pass^k and distinct pass@k from a complete real-model trial grid.

This module reads supplied records only. It never imports an actor, native
environment, provider, budget writer, or historical cost calculator.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
from math import comb
from pathlib import Path
from typing import Iterable


ACCEPTED_TERMINATIONS = frozenset({'native_terminal', 'native_action_budget', 'native_runner_error', 'author_recorded'})


@dataclass(frozen=True)
class TrialRecord:
    model_label: str
    strategy: str
    domain: str
    task_index: int
    trial_index: int
    native_reward: int
    evidence_type: str
    termination: str


def _indices(values: Iterable[int], name: str) -> tuple[int, ...]:
    result = tuple(values)
    if (not result or any(type(value) is not int or value < 0 for value in result)
            or len(set(result)) != len(result)):
        raise ValueError(f'{name} must be distinct nonnegative integers')
    return result


def summarize_grid(rows: Iterable[TrialRecord], *, model_label: str, strategy: str,
                   domain: str, task_indices: Iterable[int], trial_indices: Iterable[int],
                   ks: Iterable[int]) -> dict:
    """Compute exact task-mean estimators or reject any incomplete/censored grid.

    The paper's native runner records caught model/parser errors as reward zero.
    Only such explicitly sealed native-runner failures, or the declared native
    30-action limit, may enter as nonterminal zero. An externally censored run,
    missing journal, unknown scorer result, or duplicate never becomes zero.
    """
    if (not all(type(value) is str and value for value in (model_label, strategy, domain))
            or domain not in {'retail', 'airline'}):
        raise ValueError('A single named model, strategy and domain are required')
    tasks, trials, requested = (_indices(task_indices, 'task_indices'),
                                _indices(trial_indices, 'trial_indices'),
                                _indices(ks, 'ks'))
    if any(k == 0 or k > len(trials) for k in requested):
        raise ValueError('Each k must fit the declared complete trial count')
    expected = {(task, trial) for task in tasks for trial in trials}
    observed: dict[tuple[int, int], TrialRecord] = {}
    terminations = {name: 0 for name in sorted(ACCEPTED_TERMINATIONS)}
    for row in rows:
        if type(row) is not TrialRecord:
            raise ValueError('Typed TrialRecord required; raw unverified records cannot be scored')
        if (type(row.task_index) is not int or row.task_index < 0
                or type(row.trial_index) is not int or row.trial_index < 0):
            raise ValueError('Task and trial IDs must be exact nonnegative integers')
        key = (row.task_index, row.trial_index)
        if (row.model_label != model_label or row.strategy != strategy or row.domain != domain
                or key not in expected or key in observed):
            raise ValueError(f'Unplanned, mismatched or duplicate trial: {key}')
        if row.evidence_type != 'real_model_evaluation':
            raise ValueError('Only actual real-model evaluation evidence is eligible')
        if type(row.native_reward) is not int or row.native_reward not in (0, 1):
            raise ValueError('Native reward must be the binary integer returned by the scorer')
        if row.termination not in ACCEPTED_TERMINATIONS:
            raise ValueError('Unknown or censored termination cannot enter a completed grid')
        if row.termination not in {'native_terminal', 'author_recorded'} and row.native_reward != 0:
            raise ValueError('Nonterminal/error record cannot claim native success')
        observed[key] = row
        terminations[row.termination] += 1
    missing = expected - set(observed)
    if missing:
        raise ValueError(f'Incomplete grid: {len(missing)} declared trials missing')
    successes = {task: sum(observed[(task, trial)].native_reward for trial in trials)
                 for task in tasks}
    n = len(trials)
    metrics = {}
    for k in requested:
        denominator = comb(n, k)
        power = sum((Fraction(comb(c, k), denominator) for c in successes.values()), Fraction()) / len(tasks)
        at = 1 - sum((Fraction(comb(n - c, k), denominator) for c in successes.values()), Fraction()) / len(tasks)
        metrics[str(k)] = {'pass_power_k_exact': str(power), 'pass_at_k_exact': str(at),
                           'pass_power_k': float(power), 'pass_at_k': float(at)}
    return {'status': 'complete', 'model_label': model_label, 'strategy': strategy,
            'domain': domain, 'task_count': len(tasks), 'trials_per_task': n,
            'rollout_count': len(observed), 'task_indices': list(tasks),
            'trial_indices': list(trials), 'successes_by_task': {str(k): v for k, v in successes.items()},
            'termination_counts': terminations, 'metrics': metrics,
            'denominator': 'all declared tasks and all declared real-model trials per task'}


def equal_domain_average(retail: dict, airline: dict) -> dict:
    """Paper Table 2-style equal-domain mean, never a 115:50 task-weighted mean."""
    if (retail.get('status') != 'complete' or airline.get('status') != 'complete'
            or retail.get('domain') != 'retail' or airline.get('domain') != 'airline'
            or retail.get('model_label') != airline.get('model_label')
            or retail.get('strategy') != airline.get('strategy')
            or retail.get('trials_per_task') != airline.get('trials_per_task')
            or retail.get('metrics', {}).keys() != airline.get('metrics', {}).keys()):
        raise ValueError('Equal-domain mean requires matched complete retail and airline grids')
    metrics = {}
    for k in retail['metrics']:
        metrics[k] = {}
        for exact_name, float_name in (('pass_power_k_exact', 'pass_power_k'),
                                       ('pass_at_k_exact', 'pass_at_k')):
            value = (Fraction(retail['metrics'][k][exact_name])
                     + Fraction(airline['metrics'][k][exact_name])) / 2
            metrics[k][exact_name] = str(value)
            metrics[k][float_name] = float(value)
    return {'status': 'complete', 'model_label': retail['model_label'],
            'strategy': retail['strategy'], 'domain': 'equal_domain_average',
            'retail_task_count': retail['task_count'], 'airline_task_count': airline['task_count'],
            'metrics': metrics, 'weighting': 'one half retail plus one half airline'}


AUTHOR_RELEASES = {
    'gpt-4o-retail.json': ('gpt-4o', 'retail', 115, 4),
    'gpt-4o-airline.json': ('gpt-4o', 'airline', 50, 4),
    'sonnet-35-new-retail.json': ('sonnet-35-new', 'retail', 115, 8),
    'sonnet-35-new-airline.json': ('sonnet-35-new', 'airline', 50, 8),
}

# SHA-256 of Git objects at the independently inspected author commit
# 59a200c6d575d595120f1cb70fea53cef0632f6b. These fixed values prevent
# arbitrary same-shape local JSON from receiving that source claim.
AUTHOR_RELEASE_SHA256 = {
    'gpt-4o-airline.json': 'e9e6c0297660c537f83d4fd9c476ce7a9a86ecd2784874b7bfc13be598e37bfa',
    'gpt-4o-retail.json': 'df01707894836168ff0ec9616b0bf08f66c7e5afcf313e5fe4f7a2f5c2ec938b',
    'sonnet-35-new-airline.json': 'fe62fcd514b855b36f156dd4c3c7748597b392b006aff739b53337a9f3ba94d1',
    'sonnet-35-new-retail.json': '0df526398e9d2720c32d340815cffb04fe8c4f8a61b1f4f84bf3bb558f760131',
}


def summarize_author_release(path: Path) -> dict:
    """Recompute a released author's complete historical-trajectory file offline.

    Caller must independently establish the Git provenance. This verifies file
    shape and complete denominators, not that the trajectory was honestly run.
    """
    path = Path(path)
    if path.name not in AUTHOR_RELEASES:
        raise ValueError('Not one of the four inventoried author releases')
    label, domain, tasks, trials = AUTHOR_RELEASES[path.name]
    raw = path.read_bytes()
    data = json.loads(raw)
    if type(data) is not list:
        raise ValueError('Author release must be a list')
    records = []
    errors = 0
    for item in data:
        if type(item) is not dict or not {'task_id', 'trial', 'reward', 'info', 'traj'} <= item.keys():
            raise ValueError('Malformed author trajectory')
        reward = item['reward']
        if type(reward) not in (float, int) or reward not in (0, 1):
            raise ValueError('Author reward must be binary')
        if type(item['traj']) is not list or not item['traj']:
            raise ValueError('Missing author trajectory')
        error = isinstance(item['info'], str) and item['info'].startswith('Error: ')
        if error and reward != 0:
            raise ValueError('Caught author exception cannot claim success')
        errors += int(error)
        records.append(TrialRecord(label, 'author_release_unspecified', domain,
                                   item['task_id'], item['trial'], int(reward),
                                   'real_model_evaluation', 'author_recorded'))
    ks = tuple(k for k in (1, 2, 4, 8) if k <= trials)
    summary = summarize_grid(records, model_label=label,
                             strategy='author_release_unspecified', domain=domain,
                             task_indices=range(tasks), trial_indices=range(trials), ks=ks)
    summary['source_file'] = path.name
    summary['source_sha256'] = hashlib.sha256(raw).hexdigest()
    summary['author_error_zero_count'] = errors
    summary['provenance_limit'] = 'published later author Git release; not original paper-run logs'
    return summary


def build_zero_cost_report(repo_root: Path) -> dict:
    """Build only retrospective author-release metrics; original results absent."""
    root = Path(repo_root)
    releases = root / 'vendor' / 'tau-bench' / 'historical_trajectories'
    per_file = {name: summarize_author_release(releases / name)
                for name in sorted(AUTHOR_RELEASES)}
    if {name: summary['source_sha256'] for name, summary in per_file.items()} != AUTHOR_RELEASE_SHA256:
        raise ValueError('Author files do not match the pinned official Git bytes')
    averages = {}
    for label in ('gpt-4o', 'sonnet-35-new'):
        averages[label] = equal_domain_average(
            per_file[f'{label}-retail.json'], per_file[f'{label}-airline.json'])
    return {
        'schema': 'zero-cost-author-artifacts-v1',
        'paid_calls': 0,
        'original_paper_era_results': {
            'status': 'unavailable',
            'pinned_git_commit': '6f4b718037db619539b8b692060e6686f3f0dcc9',
            'reason': 'Pinned 2024-06-06 author source tree contains no released result or trajectory files',
            'paper_table_2_reproduced': False,
        },
        'later_author_release': {
            'status': 'recomputed_from_released_trajectories',
            'source_git_origin': 'https://github.com/sierra-research/tau-bench',
            'source_git_commit': '59a200c6d575d595120f1cb70fea53cef0632f6b',
            'file_introduction_commit': '7362a1708d720a4d697d042f21fd8637ea144c3b',
            'interpretation': 'Later historical trajectories, not original 2024 paper-run logs or a fresh provider experiment',
            'files': per_file,
            'equal_domain_averages': averages,
        },
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Recompute released author trajectories without model calls')
    parser.add_argument('--repo-root', type=Path, default=Path.cwd())
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    result = build_zero_cost_report(arguments.repo_root)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
