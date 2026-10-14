"""One actor, original author parser/prompt, external pre-dispatch gate.

The common controller is a disclosed adaptation, separate from the untouched
author baseline. It never reads task instructions, reference actions, or data.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
import time

from native.project_budget import BudgetStop

from .gate import StudyGate, classify_native_result
from .intents import build_intent
from .registry import ToolRegistry
from .visible import VisibleStore


class ControllerStop(BaseException):
    """Do not let native/provider Exception retry wrappers repeat an operation."""


@dataclass(frozen=True)
class Limits:
    native_actions: int = 30
    actor_turns: int = 30
    deadline_seconds: float = 600

    def __post_init__(self):
        if (type(self.native_actions) is not int or self.native_actions < 1 or
                type(self.actor_turns) is not int or self.actor_turns < 1 or
                not 0 < self.deadline_seconds <= 3600):
            raise ValueError('Invalid controller limits')


def dialogue_message(text):
    return ('Thought:\nThe confirmation controller requests fresh customer consent.\n'
            'Action:\nrespond\nArguments:\n' + text)


class StudyController:
    def __init__(self, *, domain, arm, agent, actor_module, env, journal,
                 registry=None, limits=Limits(), clock=time.monotonic):
        if domain not in {'retail', 'airline'} or arm not in {'B', 'G'}:
            raise ValueError('Invalid study controller identity')
        self.domain, self.arm, self.agent = domain, arm, agent
        self.actor_module, self.env, self.journal = actor_module, env, journal
        self.registry = registry or ToolRegistry()
        self.registry.validate_inventory(domain, env.tools_dict.keys())
        self.visible = VisibleStore(domain, registry=self.registry)
        self.limits, self.clock = limits, clock
        self.started = clock()
        self.native_actions = self.actor_turns = self.dialogue_sequence = 0
        self.reward, self.info, self.done = 0, {}, False
        self.stop_reason = 'not_started'
        self.proposals = []
        tools = {(domain, name): self._mutation_callable(name)
                 for name in env.tools_dict if self.registry.get(domain, name).effect == 'mutation'}
        self.gate = StudyGate(tools, registry=self.registry,
                              event_callback=lambda kind, payload: self._record('gate_'+kind, payload))

    def _record(self, kind, payload):
        try:
            return self.journal.append(kind, payload)
        except BaseException as error:
            self.stop_reason = 'evidence_error'
            raise ControllerStop('Evidence durability failed; no further actions') from error

    def _check(self, *, native=False):
        if self.clock()-self.started >= self.limits.deadline_seconds:
            self.stop_reason = 'deadline'
            raise ControllerStop('Episode deadline reached')
        if native and self.native_actions >= self.limits.native_actions:
            self.stop_reason = 'native_action_budget'
            raise ControllerStop('Native action budget reached')

    def _mutation_callable(self, name):
        def dispatch(arguments):
            return self._dispatch({'name': name, 'arguments': deepcopy(arguments)})
        return dispatch

    def _next_prompt_identity(self):
        return f'native-{self.native_actions+1}-assistant', self.dialogue_sequence+1

    def _dispatch(self, action):
        self._check(native=True)
        event_id, sequence = self._next_prompt_identity()
        self._record('native_dispatch_started', {'action': deepcopy(action),
                     'event_id': event_id, 'dialogue_sequence': sequence})
        self.native_actions += 1
        # Record the true actor dispatch before native terminal scoring can replay
        # reference actions. We neither wrap nor modify the native reward path.
        try:
            observation, reward, done, info = self.env.step(deepcopy(action))
        except BudgetStop as error:
            # Native respond calls the simulator. Transport/accounting aborts
            # are external stops, not domain-tool failures or retry signals.
            self.stop_reason = getattr(error, 'stop_reason', 'controller_budget')
            self._record('native_dispatch_external_stop', {'event_id': event_id,
                         'error_type': type(error).__name__, 'stop_reason': self.stop_reason})
            raise
        except BaseException as error:
            self.stop_reason = 'tool_error'
            self._record('native_dispatch_raised', {'event_id': event_id,
                         'error_type': type(error).__name__})
            raise ControllerStop('Native dispatch raised; no automatic retry') from error
        self.reward, self.info, self.done = reward, info, done
        self.dialogue_sequence += 2
        response_id = f'native-{self.native_actions}-observation'
        self._record('native_dispatch_returned', {'action': deepcopy(action),
                     'event_id': response_id, 'dialogue_sequence': self.dialogue_sequence,
                     'observation': observation, 'native_reward': reward, 'native_done': done})
        if action['name'] == 'respond':
            self.visible.record_user(response_id, self.dialogue_sequence, observation)
        elif self.registry.get(self.domain, action['name']).effect in {'read', 'calculation', 'mutation'}:
            self.visible.record(response_id, self.dialogue_sequence, action, observation)
        return SimpleNamespace(observation=observation, reward=reward, done=done,
                               info=info, event_id=response_id, sequence=self.dialogue_sequence)

    def _append_exchange(self, message, action, response):
        observation = response.observation
        if action['name'] != 'respond':
            observation = 'API output: ' + observation
        self.agent.messages.extend([{'role':'assistant','content':message},
                                    {'role':'user','content':observation}])

    def _feedback(self, text):
        # Explicit controller feedback is not a fabricated native observation.
        content = '[Confirmation controller: no mutation was dispatched.] ' + text
        self._record('controller_feedback', {'content':content})
        self.agent.messages.append({'role':'user','content':content})

    def _gate_mutation(self, message, action):
        proposal_id = f'proposal-{len(self.proposals)}'
        try:
            facts = self.visible.facts_for(action['name'], action['arguments'])
            built = build_intent(self.domain, action['name'], action['arguments'],
                                 intent_id=proposal_id, revision=1, facts=facts, registry=self.registry)
        except (TypeError, ValueError):
            self._feedback('The proposal does not satisfy the native tool schema or material evidence contract. Replan with valid exact arguments and visible facts.')
            return
        if built.status == 'NEED_DETAILS':
            self._feedback('Missing material details: '+', '.join(built.missing_fields)+
                           '. Obtain them through ordinary tools or customer clarification before proposing again.')
            return
        self._check(native=True)
        if self.limits.native_actions-self.native_actions < 2:
            self.stop_reason = 'native_action_budget'
            raise ControllerStop('Insufficient native actions for confirmation and mutation')
        prompt_event_id, prompt_sequence = self._next_prompt_identity()
        control = self.gate.propose(built.intent, prompt_event_id=prompt_event_id,
                                    prompt_sequence=prompt_sequence)
        if control.status != 'WAITING_FOR_CUSTOMER':
            self._feedback(control.detail)
            return
        prompt_action = {'name':'respond','arguments':{'content':control.prompt}}
        reply = self._dispatch(prompt_action)
        self._append_exchange(dialogue_message(control.prompt), prompt_action, reply)
        if reply.done:
            self.stop_reason = 'native_done'
            return
        decision = self.gate.respond(reply.observation, prompt_id=control.prompt_id,
                                     event_id=reply.event_id, sequence=reply.sequence, source='user')
        if decision.status != 'AUTHORIZED':
            # The actual customer answer is already in the actor conversation.
            # A proposed write has not been appended as an executed action.
            self._record('confirmation_not_authorized', {'status':decision.status})
            return
        current = build_intent(self.domain, action['name'], action['arguments'],
                               intent_id=proposal_id, revision=1,
                               facts=self.visible.facts_for(action['name'], action['arguments']),
                               registry=self.registry)
        if current.intent is None:
            self.gate.invalidate('Current visible material details are incomplete')
            return
        self._check(native=True)
        decision = self.gate.dispatch(current.intent)
        if decision.dispatched and decision.native_result is not None:
            self._append_exchange(message, action, decision.native_result)
        if decision.status in {'UNCERTAIN_MUTATION', 'PERSISTENCE_ERROR'}:
            self.stop_reason = 'uncertain_mutation' if decision.status == 'UNCERTAIN_MUTATION' else 'evidence_error'
            raise ControllerStop('Mutation outcome requires reconciliation')
        if decision.status not in {'SUCCEEDED', 'REJECTED'}:
            self._feedback(decision.detail)

    def run(self, index):
        self.agent.reset()
        self._record('native_reset_started', {'index':index})
        observation, self.info = self.env.reset(index=index)
        self.dialogue_sequence = 0
        self.visible.record_user('reset-user', 0, observation)
        self._record('native_reset_returned', {'event_id':'reset-user', 'observation':observation})
        self.agent.messages.append({'role':'user','content':observation})
        self.stop_reason = 'actor_budget'
        try:
            while self.actor_turns < self.limits.actor_turns and not self.done:
                self._check(native=True)
                if self.arm == 'G':
                    self.gate.invalidate('Next actor turn invalidates any previous pending confirmation')
                self.actor_turns += 1
                try:
                    message, action = self.actor_module.get_message_action(
                        self.agent.messages, self.agent.model, temperature=0.0)
                except Exception as error:
                    self.stop_reason = 'model_error'
                    self._record('actor_generation_failed', {'error_type':type(error).__name__})
                    break
                self._record('actor_proposal', {'message':message, 'action':deepcopy(action)})
                self.proposals.append(deepcopy(action))
                name = action['name']
                spec = None if name == 'respond' else self.registry.get(self.domain, name)
                if self.arm == 'G' and spec and spec.effect == 'mutation':
                    self._gate_mutation(message, action)
                else:
                    response = self._dispatch(action)
                    self._append_exchange(message, action, response)
                    if spec and spec.effect == 'mutation':
                        observed = classify_native_result(SimpleNamespace(domain=self.domain,
                                    tool_name=name, arguments=action['arguments']), response)
                        self._record('baseline_mutation_outcome', {'classification':observed})
                        if observed == 'UNCERTAIN':
                            self.stop_reason = 'uncertain_mutation'
                            break
                if self.done:
                    self.stop_reason = 'native_done'
        except ControllerStop:
            pass
        return {'native_reward': self.reward, 'native_info':self.info, 'native_done':self.done,
                'stop_reason':self.stop_reason, 'native_actions':self.native_actions,
                'actor_turns':self.actor_turns, 'messages':deepcopy(self.agent.messages),
                'gate':self.gate.snapshot(), 'proposals':deepcopy(self.proposals),
                'adaptation':'common_controller_with_original_author_prompt_and_parser',
                'arm':self.arm}
