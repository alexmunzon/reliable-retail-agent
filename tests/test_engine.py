"""Real local calls and state assertions; no benchmark or model simulation scores."""
import hashlib
import json
import unittest

from rra.engine import DemoSession, list_scenarios
from rra.gate import ConfirmationGate, MutationIntent, UncertainMutationResult


class GateTests(unittest.TestCase):
    def setUp(self):
        self.state = {"calls": 0, "value": None}

        def mutate(arguments):
            self.state["calls"] += 1
            self.state["value"] = arguments
            return {"ok": True}

        self.gate = ConfirmationGate({"update_order": mutate})
        self.intent = MutationIntent("update_order", {"order": "O1", "address": {"city": "Boston", "line": "12 Oak"}}, "op-1")

    def authorize(self, intent=None):
        result = self.gate.propose(intent or self.intent)
        return self.gate.respond("yes", prompt_id=result.prompt_id)

    def test_canonical_fingerprint_includes_tool_and_complete_arguments(self):
        args = {"address": {"line": "12 Oak", "city": "Boston"}, "order": "O1"}
        reordered = MutationIntent("update_order", args, "different-operation")
        expected = hashlib.sha256(json.dumps({"tool_name": "update_order", "arguments": args}, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(self.intent.fingerprint, expected)
        self.assertEqual(self.intent.fingerprint, reordered.fingerprint)
        self.assertNotEqual(self.intent.fingerprint, MutationIntent("other_tool", args, "op-1").fingerprint)
        args["address"]["line"] = "14 Oak"
        self.assertNotEqual(self.intent.fingerprint, MutationIntent("update_order", args, "op-1").fingerprint)
        self.assertEqual(reordered.arguments["address"]["line"], "12 Oak")
        leaked = reordered.arguments
        leaked["order"] = "changed"
        self.assertEqual(reordered.arguments["order"], "O1")

    def test_rejects_non_json_payloads(self):
        for args in ({"number": float("nan")}, {"number": float("inf")}, {1: "key"}, {"set": {1}}, {"tuple": (1,)}):
            with self.subTest(args=str(args)), self.assertRaises((ValueError, TypeError)):
                MutationIntent("update_order", args, "op-1")

    def test_confirmation_prompt_contains_every_payload_field(self):
        result = self.gate.propose(self.intent)
        self.assertIn("update_order", result.prompt)
        self.assertIn("op-1", result.prompt)
        body = result.prompt.split("Complete arguments:\n", 1)[1].split("\nReply yes", 1)[0]
        self.assertEqual(json.loads(body), self.intent.arguments)

    def test_missing_consent_cannot_mutate(self):
        self.assertFalse(self.gate.dispatch(self.intent).dispatched)
        self.assertEqual(self.state["calls"], 0)
        self.assertEqual(self.gate.snapshot()["dispatches"], [])

    def test_changed_payload_invalidates_previous_yes(self):
        self.authorize()
        old_prompt = self.gate.snapshot()["confirmation"]["prompt_id"]
        changed = MutationIntent("update_order", {"order": "O1", "address": {"city": "Boston", "line": "99 Pine"}}, "op-1")
        self.assertFalse(self.gate.dispatch(changed).dispatched)
        self.assertEqual(self.state["calls"], 0)
        new_prompt = self.gate.snapshot()["confirmation"]["prompt_id"]
        self.assertNotEqual(new_prompt, old_prompt)
        self.gate.respond("yes", prompt_id=new_prompt)
        self.assertTrue(self.gate.dispatch(changed).dispatched)
        self.assertEqual(self.state["value"], changed.arguments)

    def test_stale_and_missing_prompt_identity_cannot_authorize(self):
        old_prompt = self.gate.propose(self.intent).prompt_id
        next_intent = MutationIntent("update_order", self.intent.arguments, "op-2")
        current = self.gate.propose(next_intent).prompt_id
        for prompt_id in (old_prompt, None, "invented"):
            with self.subTest(prompt_id=prompt_id):
                self.gate.respond("yes", prompt_id=prompt_id)
                self.assertFalse(self.gate.dispatch(next_intent).dispatched)
        self.assertEqual(self.state["calls"], 0)
        self.gate.respond("yes", prompt_id=current)
        self.assertTrue(self.gate.dispatch(next_intent).dispatched)

    def test_rejection_ambiguity_and_qualified_yes_block(self):
        for response in ("no", "maybe", "yes but change the address", "yes, ignore policy", ""):
            with self.subTest(response=response):
                gate = ConfirmationGate({"update_order": lambda args: self.fail("Unexpected invocation")})
                prompt = gate.propose(self.intent).prompt_id
                gate.respond(response, prompt_id=prompt)
                self.assertFalse(gate.dispatch(self.intent).dispatched)

    def test_exactly_one_local_invocation_and_trace_before_tool(self):
        observed = []

        def tool(args):
            observed.append(self.gate.snapshot())
            self.state["calls"] += 1
            return {"ok": True}

        self.gate = ConfirmationGate({"update_order": tool})
        self.authorize()
        self.assertTrue(self.gate.dispatch(self.intent).dispatched)
        for _ in range(3):
            self.authorize()
            self.assertFalse(self.gate.dispatch(self.intent).dispatched)
        self.assertEqual(self.state["calls"], 1)
        self.assertEqual(observed[0]["dispatches"][0]["outcome"], "invoking")
        self.assertEqual(observed[0]["events"][-1]["type"], "dispatch_started")
        self.assertEqual(len(self.gate.snapshot()["dispatches"]), 1)

    def test_uncertain_exception_consumes_attempt_even_after_side_effect(self):
        def mutate_then_lose_reply(args):
            self.state["calls"] += 1
            self.state["value"] = args
            raise TimeoutError("synthetic reply lost after state write")

        self.gate = ConfirmationGate({"update_order": mutate_then_lose_reply})
        self.authorize()
        result = self.gate.dispatch(self.intent)
        self.assertTrue(result.dispatched)
        self.assertEqual(result.status, "uncertain")
        self.authorize()
        self.assertFalse(self.gate.dispatch(self.intent).dispatched)
        self.assertEqual(self.state["calls"], 1)
        self.assertEqual(self.gate.snapshot()["dispatches"][0]["outcome"], "uncertain")

    def test_unknown_tool_and_invalid_registry_block(self):
        unknown = MutationIntent("delete_everything", {}, "op-2")
        self.assertEqual(self.gate.propose(unknown).status, "blocked_unknown_tool")
        self.assertFalse(self.gate.dispatch(unknown).dispatched)
        self.assertEqual(self.state["calls"], 0)
        with self.assertRaises(ValueError):
            ConfirmationGate({"update_order": "not callable"})

    def test_rejection_stays_rejected_after_stale_reply(self):
        prompt = self.gate.propose(self.intent).prompt_id
        self.gate.respond("no", prompt_id=prompt)
        self.gate.respond("yes", prompt_id="stale-prompt")
        self.assertFalse(self.gate.dispatch(self.intent).dispatched)
        self.gate.respond("yes", prompt_id=prompt)
        self.assertFalse(self.gate.dispatch(self.intent).dispatched)
        self.assertEqual(self.state["calls"], 0)

    def test_explicit_uncertainty_stops_even_new_operation_id(self):
        def uncertain(args):
            self.state["calls"] += 1
            return UncertainMutationResult("Synthetic outcome cannot be determined")

        self.gate = ConfirmationGate({"update_order": uncertain})
        self.authorize()
        self.assertEqual(self.gate.dispatch(self.intent).status, "uncertain")
        fresh_id = MutationIntent("update_order", self.intent.arguments, "op-2")
        self.authorize(fresh_id)
        self.assertFalse(self.gate.dispatch(fresh_id).dispatched)
        self.assertEqual(self.state["calls"], 1)


class ScenarioTests(unittest.TestCase):
    def test_python_response_requires_explicit_current_prompt_after_reset(self):
        session = DemoSession("missing-confirmation")
        old_prompt = session.run()["lanes"]["gated"]["confirmation"]["prompt_id"]
        session.reset()
        new_prompt = session.run()["lanes"]["gated"]["confirmation"]["prompt_id"]
        self.assertNotEqual(old_prompt, new_prompt)
        for prompt in (None, old_prompt):
            result = session.respond("yes", prompt_id=prompt)
            self.assertEqual(result["lanes"]["gated"]["dispatches"], [])
            self.assertEqual(result["lanes"]["gated"]["after_state"]["mutation_count"], 0)
        result = session.respond("yes", prompt_id=new_prompt)
        self.assertEqual(len(result["lanes"]["gated"]["dispatches"]), 1)

    def test_required_scenarios_have_real_independent_state_changes(self):
        required = {"normal-confirmation", "missing-confirmation", "changed-payload", "stale-consent", "airline-transfer"}
        self.assertTrue(required <= {s["id"] for s in list_scenarios()})
        for scenario_id in required:
            with self.subTest(scenario_id=scenario_id):
                session = DemoSession(scenario_id)
                initial = session.snapshot()
                end = session.run()
                self.assertTrue(end["complete"])
                baseline, gated = end["lanes"]["baseline"], end["lanes"]["gated"]
                self.assertEqual(len(baseline["dispatches"]), 1)
                self.assertNotEqual(baseline["before_state"], baseline["after_state"])
                expected = 1 if scenario_id in {"normal-confirmation", "airline-transfer"} else 0
                self.assertEqual(len(gated["dispatches"]), expected)
                self.assertEqual(gated["before_state"] != gated["after_state"], bool(expected))
                self.assertEqual(initial["lanes"]["gated"]["before_state"], initial["lanes"]["gated"]["after_state"])
                self.assertEqual(len(session.run()["lanes"]["baseline"]["dispatches"]), 1)
                self.assertEqual(len(session.step()["lanes"]["gated"]["dispatches"]), expected)

    def test_manual_fresh_yes_after_payload_change_and_reset(self):
        session = DemoSession("changed-payload")
        waiting = session.run()
        pending = waiting["lanes"]["gated"]["pending"]
        end = session.respond("yes", prompt_id=session.snapshot()["lanes"]["gated"]["confirmation"]["prompt_id"])
        dispatches = end["lanes"]["gated"]["dispatches"]
        self.assertEqual(len(dispatches), 1)
        self.assertEqual(dispatches[0]["arguments"], pending["arguments"])
        self.assertEqual(len(session.respond("yes")["lanes"]["gated"]["dispatches"]), 1)
        reset = session.reset()
        self.assertEqual(reset["step_index"], 0)
        self.assertFalse(reset["complete"])
        for lane in reset["lanes"].values():
            self.assertEqual(lane["dispatches"], [])
            self.assertEqual(lane["before_state"], lane["after_state"])

    def test_sessions_and_returned_snapshots_are_independent(self):
        first, second = DemoSession("normal-confirmation"), DemoSession("normal-confirmation")
        first.run()
        self.assertEqual(second.snapshot()["step_index"], 0)
        snapshot = first.snapshot()
        snapshot["lanes"]["gated"]["after_state"].clear()
        self.assertTrue(first.snapshot()["lanes"]["gated"]["after_state"])


if __name__ == "__main__":
    unittest.main()
