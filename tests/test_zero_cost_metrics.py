"""Offline checks for complete author-file estimators and strict denominators."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from reproduction.metrics import (
    AUTHOR_RELEASES, TrialRecord, build_zero_cost_report, equal_domain_average,
    summarize_author_release, summarize_grid,
)


class ZeroCostMetricTests(unittest.TestCase):
    def rows(self):
        # Task 0 succeeds twice; task 1 succeeds once in two trials.
        return [TrialRecord('m', 's', 'retail', task, trial, reward,
                            'real_model_evaluation', 'author_recorded')
                for task, trial, reward in [(0, 0, 1), (0, 1, 1),
                                            (1, 0, 1), (1, 1, 0)]]

    def summary(self, rows=None):
        return summarize_grid(self.rows() if rows is None else rows,
                              model_label='m', strategy='s', domain='retail',
                              task_indices=(0, 1), trial_indices=(0, 1), ks=(1, 2))

    def test_hand_calculated_powers_and_any_success(self):
        got = self.summary()
        self.assertEqual(got['metrics']['1']['pass_power_k_exact'], '3/4')
        self.assertEqual(got['metrics']['1']['pass_at_k_exact'], '3/4')
        self.assertEqual(got['metrics']['2']['pass_power_k_exact'], '1/2')
        self.assertEqual(got['metrics']['2']['pass_at_k_exact'], '1')
        self.assertEqual(got['rollout_count'], 4)

    def test_missing_duplicate_censor_unplanned_and_oracle_rejected(self):
        rows = self.rows()
        for invalid in (rows[:-1], rows + [rows[0]],
                        rows[:-1] + [TrialRecord('m', 's', 'retail', 1, 1, 0,
                                                'real_model_evaluation', 'censored')],
                        rows[:-1] + [TrialRecord('m', 's', 'retail', 1, 2, 0,
                                                'real_model_evaluation', 'author_recorded')],
                        rows[:-1] + [TrialRecord('m', 's', 'retail', 1, 1, 0,
                                                'oracle_fixture', 'author_recorded')]):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.summary(invalid)

    def test_boolean_and_float_task_trial_aliases_rejected(self):
        rows = self.rows()
        for replacement in (
            TrialRecord('m', 's', 'retail', False, 0, 1, 'real_model_evaluation', 'author_recorded'),
            TrialRecord('m', 's', 'retail', 0.0, 0, 1, 'real_model_evaluation', 'author_recorded'),
            TrialRecord('m', 's', 'retail', 0, False, 1, 'real_model_evaluation', 'author_recorded'),
            TrialRecord('m', 's', 'retail', 0, 0.0, 1, 'real_model_evaluation', 'author_recorded'),
        ):
            with self.subTest(replacement=replacement):
                with self.assertRaisesRegex(ValueError, 'exact nonnegative integers'):
                    self.summary([replacement] + rows[1:])

    def test_author_file_reads_binary_float_and_caught_exception_zero(self):
        with TemporaryDirectory() as temp:
            file = Path(temp) / 'sample.json'
            data = [{'task_id': task, 'trial': trial, 'reward': float(reward),
                     'info': 'Error: parsed response' if task == 1 and trial == 1 else {},
                     'traj': [{'role': 'assistant', 'content': 'x'}]}
                    for task, trial, reward in [(0, 0, 1), (0, 1, 1),
                                                (1, 0, 1), (1, 1, 0)]]
            file.write_text(json.dumps(data))
            with patch.dict(AUTHOR_RELEASES, {'sample.json': ('m', 'retail', 2, 2)}):
                got = summarize_author_release(file)
                self.assertEqual(got['author_error_zero_count'], 1)
                self.assertEqual(got['metrics']['2']['pass_power_k_exact'], '1/2')
                data[3]['reward'] = 1.0
                file.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, 'Caught author exception'):
                    summarize_author_release(file)

    def test_equal_domain_average_requires_matched_complete_grid(self):
        retail = self.summary()
        airline = dict(retail, domain='airline')
        mean = equal_domain_average(retail, airline)
        self.assertEqual(mean['metrics']['2']['pass_power_k_exact'], '1/2')
        with self.assertRaises(ValueError):
            equal_domain_average(retail, dict(airline, status='incomplete'))

    def test_committed_author_files_match_reproducible_report(self):
        root = Path(__file__).resolve().parents[1]
        expected = json.loads((root / 'reproduction/evidence/zero-cost-results.json').read_text())
        got = build_zero_cost_report(root)
        self.assertEqual(got, expected)
        self.assertEqual(got['paid_calls'], 0)
        self.assertFalse(got['original_paper_era_results']['paper_table_2_reproduced'])
        files = got['later_author_release']['files']
        self.assertEqual({name: result['rollout_count'] for name, result in files.items()},
                         {'gpt-4o-retail.json': 460, 'gpt-4o-airline.json': 200,
                          'sonnet-35-new-retail.json': 920, 'sonnet-35-new-airline.json': 400})
        self.assertNotIn(str(root), json.dumps(got))

    def test_same_shape_tampered_author_file_cannot_claim_pinned_git_source(self):
        root = Path(__file__).resolve().parents[1]
        official = root / 'vendor/tau-bench/historical_trajectories'
        with TemporaryDirectory() as temp:
            fake = Path(temp) / 'vendor/tau-bench/historical_trajectories'
            fake.mkdir(parents=True)
            for name in AUTHOR_RELEASES:
                (fake / name).symlink_to(official / name)
            changed = fake / 'gpt-4o-airline.json'
            changed.unlink()
            changed.write_bytes((official / changed.name).read_bytes() + b'\n')
            with self.assertRaisesRegex(ValueError, 'pinned official Git bytes'):
                build_zero_cost_report(Path(temp))


if __name__ == '__main__':
    unittest.main()
