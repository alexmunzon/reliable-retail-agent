"""No-key original-agent/scorer plumbing, not paper performance tests."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import shutil

ROOT = Path(__file__).resolve().parents[1]

class OriginalOfflineReproductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = subprocess.run([sys.executable, '-m', 'reproduction.runner'], cwd=ROOT,
                                check=True, capture_output=True, text=True)
        cls.evidence = json.loads(result.stdout)

    def test_original_agents_native_methods_and_sampling(self):
        rows = self.evidence['synthetic_runs']
        self.assertEqual(len(rows), 9)
        self.assertEqual({row['strategy'] for row in rows}, {'function_calling','react','act'})
        for row in rows:
            self.assertEqual(row['model_api_requests'], 0)
            self.assertTrue(row['network_blocked'])
            self.assertFalse(row['is_model_evaluation'])
            self.assertFalse(row['hidden_task_instruction_in_actor'])
            self.assertTrue(row['original_method_prompt_preserved'])
            self.assertIn('tau-bench-paper-era', row['native_methods']['act'])
            self.assertIn('tau-bench-paper-era', row['native_methods']['calculate_reward'])
            self.assertTrue(all(call['temperature']==0 for call in row['actor_request_parameters']))
            self.assertTrue(all(call['temperature']==1 and call['max_tokens']==150
                                for call in row['simulator_request_parameters']))
            if row['strategy']=='act':
                self.assertTrue(all('Thought:' not in message['content'] for message in row['actor_messages'] if message['role']=='assistant'))
            elif row['strategy']=='react':
                self.assertTrue(all('Thought:' in message['content'] for message in row['actor_messages'] if message['role']=='assistant'))

    def test_success_and_required_output_failure_use_original_scorer(self):
        for row in self.evidence['synthetic_runs']:
            if row['scenario']=='success':
                self.assertEqual(row['native_reward'],1)
                self.assertEqual(row['termination'],'native_terminal')
                self.assertEqual(row['native_info']['r_outputs'],1)
            elif row['scenario']=='missing_output':
                self.assertEqual(row['native_reward'],0)
                self.assertEqual(row['termination'],'native_terminal')
                self.assertEqual(row['native_info']['r_outputs'],0)
                self.assertTrue(row['native_info']['r_actions'])

    def test_native_thirty_action_cutoff_never_invokes_scorer(self):
        for row in self.evidence['synthetic_runs']:
            if row['scenario']=='action_cutoff':
                self.assertEqual(row['native_reward'],0)
                self.assertEqual(row['termination'],'native_action_budget')
                self.assertEqual(row['actor_requests'],30)
                self.assertEqual(len(row['actor_dispatches']),30)
                self.assertNotIn('r_actions',row['native_info'])
                self.assertIsNone(row['actor_data_before_terminal_scoring'])

    def test_actor_trace_excludes_reference_replay_and_retains_prescore_state(self):
        for row in self.evidence['synthetic_runs']:
            if row['termination']=='native_terminal':
                self.assertEqual(row['actor_data_before_terminal_scoring'],{'counter':1})
                self.assertEqual([d['action']['name'] for d in row['actor_dispatches']],['set_counter','respond'])
                self.assertEqual([a['name'] for a in row['native_actions_after_scoring_replay']],['set_counter','respond','set_counter'])

    def test_actual_original_runner_exception_zero_is_not_external_censor_zero(self):
        result=self.evidence['original_runner_errors']
        self.assertTrue(result['actual_original_run_executed'])
        self.assertEqual(result['ordinary_act_exception_reward'],0)
        self.assertTrue(result['setup_failures_outside_act_catch'])
        self.assertTrue(result['external_BaseException_stops_not_converted_to_zero'])

    def test_default_dry_plan_has_all_scopes_but_no_paid_path(self):
        from reproduction.manifest import dry_plan
        plan=dry_plan()
        self.assertFalse(plan['paid_execution_enabled'])
        self.assertTrue(plan['original_baseline_only'])
        self.assertFalse(plan['model_evaluated'])
        self.assertEqual(len(plan['experiment_scopes']),7)
        self.assertTrue(plan['blocked_reasons'])
        self.assertEqual(plan['source_commit'],'6f4b718037db619539b8b692060e6686f3f0dcc9')

    def test_exclusive_output_and_no_paid_command(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'evidence.json'
            cmd=[sys.executable,'scripts/reproduction.py','plan','--output',str(path)]
            subprocess.run(cmd,cwd=ROOT,check=True,capture_output=True)
            original=path.read_bytes()
            again=subprocess.run(cmd,cwd=ROOT,capture_output=True)
            self.assertNotEqual(again.returncode,0)
            self.assertEqual(path.read_bytes(),original)
        paid=subprocess.run([sys.executable,'scripts/reproduction.py','paid'],cwd=ROOT,capture_output=True)
        self.assertNotEqual(paid.returncode,0)

    def test_portable_snapshot_without_vendor_git_or_later_runtime(self):
        from native.source import verify_checkout
        import reproduction.inventory as module
        primary=json.loads((ROOT/'native/source_manifests/paper-era.json').read_text())
        later=json.loads((ROOT/'native/source_manifests/later.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            public=Path(directory)
            vendor=public/'vendor/tau-bench-paper-era'
            for name in primary['source_sha256']:
                dest=vendor/name
                dest.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(ROOT/'vendor/tau-bench-paper-era'/name,dest)
            proof=verify_checkout(vendor,primary['commit'],'paper-era.json')
            self.assertEqual(proof['tracked_files_verified'],70)
            self.assertEqual(proof['source_validation_method'],'bundled_sha256_manifest_previously_verified_against_git')
            for name in later['source_sha256']:
                if name.startswith('historical_trajectories/'):
                    dest=public/'vendor/tau-bench'/name
                    dest.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copyfile(ROOT/'vendor/tau-bench'/name,dest)
            (public/'native/source_manifests').mkdir(parents=True)
            shutil.copyfile(ROOT/'native/source_manifests/later.json',public/'native/source_manifests/later.json')
            (public/'reproduction/evidence').mkdir(parents=True)
            shutil.copyfile(ROOT/'reproduction/evidence/paper-audit.json',public/'reproduction/evidence/paper-audit.json')
            with patch.object(module,'ROOT',public),patch.object(module,'provenance',return_value=proof):
                result=module.inventory()
            self.assertEqual(len(result['later_author_release']['files']),4)
            self.assertEqual(result['original_paper_era_released_trajectory_files'],[])

if __name__=='__main__':
    unittest.main()
