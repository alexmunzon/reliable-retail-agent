"""Synthetic interface tests; these do not execute tau-bench or a model.

FakeEnv mirrors only the reviewed dispatch and scoring-replay behaviors needed
for these tests. It is intentionally not presented as the complete benchmark.
"""
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
import unittest

from adapters.tau_dispatch import TauDispatchAdapter


@dataclass
class Action:
    name: str
    kwargs: dict


def address_action(street="20 Synthetic Way"):
    return Action("modify_pending_order_address", {
        "order_id": "#SYNTHETIC-1", "address1": street, "address2": "",
        "city": "Example City", "state": "CA", "country": "USA", "zip": "00000",
    })


class FakeEnv:
    def __init__(self, replies=("yes",)):
        self.replies = iter(replies)
        self.actions = []
        self.data = {"address1": "10 Synthetic Way"}
        self.mutations = 0
        self.reward_replays = 0
        self.raise_after_mutation = False
        self.source_override = None

    def step(self, action):
        self.actions.append(deepcopy(action))
        done, reward = False, 0
        if action.name == "respond":
            observation = next(self.replies)
            source = self.source_override or "user"
            done = "###STOP###" in observation
        elif action.name == "modify_pending_order_address":
            self.mutations += 1
            self.data = deepcopy(action.kwargs)
            source, observation = action.name, "updated"
            if self.raise_after_mutation:
                # Native Env.step catches tool exceptions and returns Error text.
                observation = "Error: synthetic uncertain downstream result"
        else:
            source, observation = action.name, "synthetic read result"
        if done:
            reward = self.calculate_reward()
        return SimpleNamespace(observation=observation, reward=reward, done=done,
                               info=SimpleNamespace(source=source))

    def calculate_reward(self):
        self.reward_replays += 1
        before = deepcopy(self.data)
        self.data = {"address1": "10 Synthetic Way"}
        self.step(address_action())
        return float(before == self.data)


class TauAdapterTests(unittest.TestCase):
    def test_unconfirmed_mutation_never_enters_native_environment(self):
        env = FakeEnv()
        adapter = TauDispatchAdapter(env, Action)
        result = adapter.dispatch(address_action(), operation_id="op-1")
        self.assertFalse(result.gate_result.dispatched)
        self.assertEqual(env.actions, [])
        self.assertEqual(adapter.snapshot()["actor_dispatches"], [])

    def test_fresh_confirmation_binds_complete_arguments_and_dispatches_once(self):
        env = FakeEnv()
        adapter = TauDispatchAdapter(env, Action)
        original_step = env.step
        confirmation = adapter.request_confirmation(address_action(), operation_id="op-1")
        self.assertEqual(confirmation.status, "authorized")
        for key, value in address_action().kwargs.items():
            self.assertIn('"' + key + '"', env.actions[0].kwargs["content"])
            self.assertIn(value, env.actions[0].kwargs["content"])
        result = adapter.dispatch(address_action(), operation_id="op-1")
        self.assertIsNotNone(result.native_response)
        self.assertEqual(env.mutations, 1)
        self.assertFalse(adapter.dispatch(address_action(), operation_id="op-1").gate_result.dispatched)
        self.assertEqual(env.mutations, 1)
        self.assertEqual(env.step, original_step)

    def test_changed_payload_requires_second_user_exchange(self):
        env = FakeEnv(("yes", "yes"))
        adapter = TauDispatchAdapter(env, Action)
        adapter.request_confirmation(address_action(), operation_id="op-1")
        changed = address_action("30 Synthetic Way")
        result = adapter.dispatch(changed, operation_id="op-1")
        self.assertFalse(result.gate_result.dispatched)
        self.assertEqual(env.mutations, 0)
        adapter.request_confirmation(changed, operation_id="op-1")
        adapter.dispatch(changed, operation_id="op-1")
        self.assertEqual(env.data["address1"], "30 Synthetic Way")
        self.assertEqual(env.mutations, 1)

    def test_new_operation_cannot_reuse_previous_yes(self):
        env = FakeEnv()
        adapter = TauDispatchAdapter(env, Action)
        adapter.request_confirmation(address_action(), operation_id="old-op")
        self.assertFalse(adapter.dispatch(address_action(), operation_id="new-op").gate_result.dispatched)
        self.assertEqual(env.mutations, 0)

    def test_negative_and_qualified_yes_block(self):
        for reply in ("no", "yes, but use 30 Synthetic Way", "maybe"):
            with self.subTest(reply=reply):
                env = FakeEnv((reply,))
                adapter = TauDispatchAdapter(env, Action)
                adapter.request_confirmation(address_action(), operation_id="op")
                self.assertFalse(adapter.dispatch(address_action(), operation_id="op").gate_result.dispatched)
                self.assertEqual(env.mutations, 0)

    def test_unrelated_conversation_yes_does_not_authorize(self):
        env = FakeEnv()
        adapter = TauDispatchAdapter(env, Action)
        adapter.dispatch(Action("respond", {"content": "Are you there?"}))
        self.assertFalse(adapter.dispatch(address_action(), operation_id="op").gate_result.dispatched)

    def test_later_conversation_revokes_previous_authorization(self):
        env = FakeEnv(("yes", "no, stop"))
        adapter = TauDispatchAdapter(env, Action)
        adapter.request_confirmation(address_action(), operation_id="op")
        adapter.dispatch(Action("respond", {"content": "Anything else?"}))
        self.assertFalse(adapter.dispatch(address_action(), operation_id="op").gate_result.dispatched)

    def test_non_user_source_cannot_supply_consent(self):
        env = FakeEnv()
        env.source_override = "get_order_details"
        adapter = TauDispatchAdapter(env, Action)
        self.assertEqual(adapter.request_confirmation(address_action(), operation_id="op").status,
                         "no_fresh_user_confirmation")
        self.assertFalse(adapter.dispatch(address_action(), operation_id="op").gate_result.dispatched)

    def test_native_error_observation_is_preserved_and_attempt_not_retried(self):
        env = FakeEnv()
        env.raise_after_mutation = True
        adapter = TauDispatchAdapter(env, Action)
        adapter.request_confirmation(address_action(), operation_id="op")
        result = adapter.dispatch(address_action(), operation_id="op")
        self.assertTrue(result.native_response.observation.startswith("Error:"))
        self.assertEqual(result.status, "uncertain")
        self.assertFalse(adapter.dispatch(address_action(), operation_id="op").gate_result.dispatched)
        self.assertFalse(adapter.dispatch(address_action(), operation_id="new-op").gate_result.dispatched)
        self.assertEqual(env.mutations, 1)

    def test_raised_native_exception_records_attempt_before_call_and_stops(self):
        class RaisingEnv(FakeEnv):
            def step(self, action):
                if action.name == "modify_pending_order_address":
                    # Assert the adapter trace was committed before entry.
                    assert adapter.snapshot()["actor_dispatches"][-1]["outcome"] == "invoking"
                    self.mutations += 1
                    raise RuntimeError("synthetic lost response")
                return super().step(action)
        env = RaisingEnv()
        adapter = TauDispatchAdapter(env, Action)
        adapter.request_confirmation(address_action(), operation_id="op")
        result = adapter.dispatch(address_action(), operation_id="op")
        self.assertEqual(result.status, "uncertain")
        self.assertEqual(adapter.snapshot()["actor_dispatches"][-1]["outcome"], "uncertain")
        self.assertFalse(adapter.dispatch(address_action(), operation_id="new-op").gate_result.dispatched)
        self.assertEqual(env.mutations, 1)

    def test_terminal_confirmation_reply_cannot_authorize(self):
        env = FakeEnv(("yes ###STOP###",))
        adapter = TauDispatchAdapter(env, Action)
        result = adapter.request_confirmation(address_action(), operation_id="op")
        self.assertEqual(result.status, "no_fresh_user_confirmation")
        self.assertEqual(adapter.dispatch(address_action(), operation_id="op").status, "terminated")
        self.assertEqual(len(adapter.snapshot()["actor_dispatches"]), 1)

    def test_native_reward_replay_remains_native_and_outside_actor_trace(self):
        env = FakeEnv(("yes", "###STOP###"))
        original_reward = env.calculate_reward
        adapter = TauDispatchAdapter(env, Action)
        adapter.request_confirmation(address_action(), operation_id="op")
        adapter.dispatch(address_action(), operation_id="op")
        result = adapter.dispatch(Action("respond", {"content": "Done."}))
        self.assertTrue(result.native_response.done)
        self.assertEqual(result.native_response.reward, 1.0)  # synthetic equality, not a benchmark score
        self.assertEqual(env.calculate_reward, original_reward)
        self.assertEqual(env.reward_replays, 1)
        self.assertEqual(len(env.actions), 4)  # includes reference replay
        self.assertEqual(len(adapter.snapshot()["actor_dispatches"]), 3)
        self.assertEqual(adapter.dispatch(address_action(), operation_id="later").status, "terminated")

    def test_nonterminal_cutoff_does_not_trigger_scoring_or_invent_reward(self):
        class NoEarlyScoringEnv(FakeEnv):
            def calculate_reward(self):
                raise AssertionError("A nonterminal controller cutoff must not score")
        env = NoEarlyScoringEnv()
        adapter = TauDispatchAdapter(env, Action)
        reply = adapter.request_confirmation(address_action(), operation_id="op")
        self.assertFalse(reply.native_response.done)
        self.assertEqual(reply.native_response.reward, 0)
        result = adapter.dispatch(address_action(), operation_id="op")
        self.assertFalse(result.native_response.done)
        self.assertEqual(result.native_response.reward, 0)
        # A controller can stop here and inspect the trace; the adapter must
        # neither manufacture native termination nor call the scorer.
        self.assertFalse(adapter.snapshot()["terminated"])
        self.assertEqual(env.reward_replays, 0)

    def test_unknown_tool_and_overlapping_classifications_fail_closed(self):
        env = FakeEnv()
        adapter = TauDispatchAdapter(env, Action)
        with self.assertRaises(ValueError):
            adapter.dispatch(Action("invented_mutation", {}), operation_id="op")
        with self.assertRaises(ValueError):
            TauDispatchAdapter(env, Action, passthrough_tools={"modify_pending_order_address"})
        self.assertEqual(env.actions, [])

    def test_explicit_read_passes_through_and_mutation_requires_operation_id(self):
        env = FakeEnv()
        adapter = TauDispatchAdapter(env, Action)
        result = adapter.dispatch(Action("get_order_details", {"order_id": "#SYNTHETIC-1"}))
        self.assertEqual(result.native_response.observation, "synthetic read result")
        with self.assertRaises(ValueError):
            adapter.dispatch(address_action())


if __name__ == "__main__":
    unittest.main()
