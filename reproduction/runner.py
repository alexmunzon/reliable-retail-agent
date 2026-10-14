"""Execute unchanged author agents on tiny synthetic fixtures, offline only."""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from native.paper_source import ROOT, VENDOR, bootstrap, provenance
from reproduction.observer import DispatchObserver
from reproduction.schema import OfflineCell

class Usage(dict):
    def __getattr__(self, name):
        return self[name]

class SyntheticClient:
    def __init__(self, cell):
        self.cell = cell
        self.calls = []
        self.actor_count = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        if kwargs['model'] == 'gpt-4':
            text = ('Please set the visible counter to one and report receipt.'
                    if len(kwargs['messages']) == 2 else '###STOP###')
            message = SimpleNamespace(content=text, tool_calls=None)
        else:
            self.actor_count += 1
            if self.cell.scenario == 'action_cutoff':
                name, arguments = 'read_counter', {}
            elif self.actor_count == 1:
                name, arguments = 'set_counter', {'value': 1}
            else:
                name, arguments = 'respond', {'content': 'receipt' if self.cell.scenario == 'success' else 'Done.'}
            if self.cell.strategy == 'function_calling':
                calls = None if name == 'respond' else [SimpleNamespace(id='offline-call',
                    function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))]
                message = SimpleNamespace(content=arguments.get('content'), tool_calls=calls)
            else:
                argument_text = arguments['content'] if name == 'respond' else json.dumps(arguments)
                text = ('Thought:\nSynthetic fixture script.\n' if self.cell.strategy=='react' else '')+'Action:\n'+name+'\nArguments:\n'+argument_text
                message = SimpleNamespace(content=text, tool_calls=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)],
            usage=Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0))

def _blocked(*args, **kwargs):
    raise RuntimeError('Network disabled in reproduction offline contract')

def _load_author_run():
    spec = importlib.util.spec_from_file_location('reproduction_original_run', VENDOR/'run.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def run_cell(cell):
    """Fresh-process only; fixture actor does not read tasks/reference metadata."""
    bootstrap()
    client = SyntheticClient(cell)
    with patch('socket.socket.connect', _blocked), patch('socket.create_connection', _blocked), patch('openai.OpenAI', return_value=client):
        from tau_bench.envs.base import BaseEnv
        author_run = _load_author_run()
        def set_counter(data, value):
            data['counter'] = value
            return json.dumps({'counter': data['counter']})
        def read_counter(data):
            return json.dumps({'counter': data['counter']})
        for tool, properties, required in [(set_counter, {'value': {'type': 'integer'}}, ['value']), (read_counter, {}, [])]:
            tool.__info__ = {'type': 'function', 'function': {'name': tool.__name__,
                'description': 'Synthetic local contract tool', 'parameters': {'type': 'object', 'properties': properties, 'required': required}}}
        task = {'instruction': 'HIDDEN_SYNTHETIC_TASK_CANARY',
            'actions': [{'name': 'set_counter', 'arguments': {'value': 1}}], 'outputs': ['receipt']}
        raw = BaseEnv({'counter': 0}, [set_counter, read_counter], [task],
            'Synthetic policy: set the requested visible counter and report receipt.', [], 'naive', 'gpt-4')
        env = DispatchObserver(raw)
        args = SimpleNamespace(agent_strategy='function_calling' if cell.strategy == 'function_calling' else 'react',
            model='gpt-4o', think=int(cell.strategy == 'react'), num_gpus=None)
        agent = author_run.agent_factory(raw.tools_info, raw.wiki, args)
        if cell.strategy != 'function_calling':
            from tau_bench.agents.chat_react_agent import act_instruction, react_instruction
            expected_prompt = react_instruction if cell.strategy=='react' else act_instruction
            if not agent.prompt.endswith(expected_prompt):
                raise AssertionError('Original method prompt not preserved')
        reward, info = agent.act(env, index=0, temperature=0.0)
        actor_calls = [call for call in client.calls if call['model'] == 'gpt-4o']
        actor_text = json.dumps(actor_calls, default=lambda value: vars(value))
        if task['instruction'] in actor_text:
            raise AssertionError('Hidden task instruction reached actor')
        return {'strategy': cell.strategy, 'scenario': cell.scenario, 'trial_index': 0,
            'evidence_type': 'synthetic_original_author_code_contract', 'is_model_evaluation': False,
            'model_api_requests': 0, 'network_blocked': True, 'native_reward': reward,
            'original_method_prompt_preserved': True,
            'termination': 'native_terminal' if env.native_done else 'native_action_budget',
            'actor_requests': len(actor_calls), 'user_requests': len(client.calls)-len(actor_calls),
            'actor_dispatches': env.dispatches, 'actor_data_before_terminal_scoring': env.pre_terminal_data,
            'native_actions_after_scoring_replay': deepcopy(raw.actions), 'native_info': info,
            'actor_messages': agent.get_messages(), 'hidden_task_instruction_in_actor': False,
            'native_methods': {'agent': type(agent).__name__, 'act': str(Path(agent.act.__code__.co_filename).relative_to(ROOT)),
                'step': str(Path(raw.step.__code__.co_filename).relative_to(ROOT)),
                'calculate_reward': str(Path(raw.calculate_reward.__code__.co_filename).relative_to(ROOT))},
            'actor_request_parameters': [{key: call[key] for key in ('model','temperature','tool_choice') if key in call} for call in actor_calls],
            'simulator_request_parameters': [{key: call[key] for key in ('model','temperature','max_tokens')} for call in client.calls if call['model']=='gpt-4']}

def verify_runner_exception_semantics():
    bootstrap()
    with patch('socket.socket.connect', _blocked), patch('socket.create_connection', _blocked):
        module = _load_author_run()
        args = SimpleNamespace(env='retail', user_model='unused-offline', task_split='test',
            end_index=1, start_index=0, num_trials=1, shuffle=False, verbose=False,
            temperature=0.0, max_concurrency=1)
        class FailingAgent:
            def act(self, *args, **kwargs):
                raise RuntimeError('Synthetic ordinary actor exception')
            def get_messages(self):
                return []
        fake_env = SimpleNamespace(tasks=[{}], tools_info=[], wiki='')
        with tempfile.TemporaryDirectory() as directory, patch.object(module, 'get_env', return_value=fake_env), patch.object(module, 'agent_factory', return_value=FailingAgent()):
            results = module.run(args, Path(directory)/'checkpoint.json')
        class ExternalStop(BaseException):
            pass
        class ExternallyStoppedAgent(FailingAgent):
            def act(self, *args, **kwargs):
                raise ExternalStop('Synthetic external stop')
        external_propagated = False
        with tempfile.TemporaryDirectory() as directory, patch.object(module, 'get_env', return_value=fake_env), patch.object(module, 'agent_factory', return_value=ExternallyStoppedAgent()):
            try:
                module.run(args, Path(directory)/'checkpoint.json')
            except ExternalStop:
                external_propagated = True
        setup_propagated = False
        with tempfile.TemporaryDirectory() as directory, patch.object(module, 'get_env', side_effect=[fake_env, RuntimeError('Synthetic isolated setup failure')]):
            try:
                module.run(args, Path(directory)/'checkpoint.json')
            except RuntimeError:
                setup_propagated = True
        return {'actual_original_run_executed': True, 'ordinary_act_exception_reward': results[0]['reward'],
                'recorded_trial_count': len(results), 'setup_failures_outside_act_catch': setup_propagated,
                'catch_class': 'Exception', 'external_BaseException_stops_not_converted_to_zero': external_propagated}

def offline_grid():
    cells = [OfflineCell(strategy, scenario) for strategy in ('function_calling','react','act')
             for scenario in ('success','missing_output','action_cutoff')]
    rows = []
    # A fixed minimal child environment never reads or forwards provider keys.
    child_env = {'PATH': os.defpath, 'PYTHONDONTWRITEBYTECODE': '1'}
    for cell in cells:
        completed = subprocess.run([sys.executable, '-m', 'reproduction.runner', '--worker', cell.strategy, cell.scenario],
            cwd=ROOT, check=True, capture_output=True, text=True, env=child_env)
        rows.append(json.loads(completed.stdout))
    error = subprocess.run([sys.executable, '-m', 'reproduction.runner', '--exceptions'],
        cwd=ROOT, check=True, capture_output=True, text=True, env=child_env)
    return {'schema_version': 1, 'evidence_type': 'offline_original_native_verification',
        'attempt_id': str(uuid.uuid4()), 'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'model_api_requests': 0, 'paid_execution_enabled': False, 'is_paper_reproduction': False,
        'worker_environment': 'fixed minimal PATH; provider credentials never read or forwarded',
        'provenance': provenance(), 'synthetic_runs': rows, 'original_runner_errors': json.loads(error.stdout),
        'claim': 'Original FC, ReAct, Act and native scorer plumbing verified; no model benchmark result'}

if __name__ == '__main__':
    import argparse
    import contextlib
    import io
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', nargs=2)
    parser.add_argument('--exceptions', action='store_true')
    args = parser.parse_args()
    # Keep the machine result separate from original runner print diagnostics.
    with contextlib.redirect_stdout(io.StringIO()):
        result = run_cell(OfflineCell(*args.worker)) if args.worker else verify_runner_exception_semantics() if args.exceptions else offline_grid()
    print(json.dumps(result, default=lambda value: vars(value)))
