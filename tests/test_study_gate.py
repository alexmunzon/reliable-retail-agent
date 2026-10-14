"""Public-schema and handcrafted visible-evidence fixtures; no benchmark tasks."""
from copy import deepcopy
import ast
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import unittest

from study import MaterialFact, MutationIntent, ObservationRef, StudyGate, ToolRegistry, build_intent, canonical_json
from study.gate import PRE_EFFECT_ERRORS, classify_native_result, classify_response


ADDRESS = dict(address1="123 Main St", address2="", city="Example", state="CA", country="USA", zip="00123")
PASSENGERS = [{"first_name": "Jo", "last_name": "Li", "dob": "1990-01-01"}]
FLIGHTS = [{"flight_number": "SYN001", "date": "2024-06-01"}]
RETAIL_ARGS = {
    "cancel_pending_order": {"order_id": "#SYN001", "reason": "ordered by mistake"},
    "modify_pending_order_address": {"order_id": "#SYN001", **ADDRESS},
    "modify_user_address": {"user_id": "synthetic_user", **ADDRESS},
    "modify_pending_order_payment": {"order_id": "#SYN001", "payment_method_id": "gift_card_synthetic"},
    "modify_pending_order_items": {"order_id": "#SYN001", "item_ids": ["0001", "0001", "0002"], "new_item_ids": ["0003", "0004", "0005"], "payment_method_id": "credit_card_synthetic"},
    "return_delivered_order_items": {"order_id": "#SYN001", "item_ids": ["0001", "0001"], "payment_method_id": "credit_card_synthetic"},
    "exchange_delivered_order_items": {"order_id": "#SYN001", "item_ids": ["0001", "0001"], "new_item_ids": ["0003", "0004"], "payment_method_id": "credit_card_synthetic"},
}
AIRLINE_ARGS = {
    "book_reservation": {"user_id": "synthetic_user", "origin": "SFO", "destination": "JFK", "flight_type": "one_way", "cabin": "economy", "flights": FLIGHTS, "passengers": PASSENGERS, "payment_methods": [{"payment_id": "credit_card_synthetic", "amount": 130.01}], "total_baggages": 1, "nonfree_baggages": 0, "insurance": "yes"},
    "cancel_reservation": {"reservation_id": "SYN001"},
    "update_reservation_flights": {"reservation_id": "SYN001", "cabin": "business", "flights": FLIGHTS, "payment_id": "credit_card_synthetic"},
    "update_reservation_baggages": {"reservation_id": "SYN001", "total_baggages": 3, "nonfree_baggages": 2, "payment_id": "credit_card_synthetic"},
    "update_reservation_passengers": {"reservation_id": "SYN001", "passengers": PASSENGERS},
    "send_certificate": {"user_id": "synthetic_user", "amount": 100.01},
}


def fixture_facts(registry, domain, tool, *, sequence=1):
    values = {"user_id": "synthetic_user", "price_difference": "10.010", "charge_amount": "100.010",
              "refund_amount": "100.010", "total_price": "130.01", "original_payment_id": "credit_card_original",
              "original_nonfree_baggages": 0, "cancellation_reason": "change of plan",
              "flights": FLIGHTS, "original_flights": FLIGHTS, "passengers": PASSENGERS, "original_passengers": PASSENGERS,
              "refund_payments": [{"payment_method_id" if domain == "retail" else "payment_id": "credit_card_original", "amount": 100.01}]}
    source = f"tool:{domain}/get_order_details" if domain == "retail" else "tool:airline/get_reservation_details"
    return [MaterialFact(name, ObservationRef("visible-read-1", "user" if name == "cancellation_reason" else source,
                                              f"$.{name}", values[name], sequence))
            for name in registry.get(domain, tool).display_fields]


class StudyGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = ToolRegistry()

    def intent(self, tool="modify_user_address", domain="retail", *, args=None, revision=1, intent_id="logical-operation", facts=None):
        arguments = deepcopy(args if args is not None else (RETAIL_ARGS if domain == "retail" else AIRLINE_ARGS)[tool])
        facts = fixture_facts(self.registry, domain, tool) if facts is None else facts
        built = build_intent(domain, tool, arguments, intent_id=intent_id, revision=revision, facts=facts, registry=self.registry)
        self.assertEqual("PREPARED", built.status, built)
        return built.intent

    def gate(self, intent=None, fn=None, callback=None):
        intent = intent or self.intent()
        def default(args):
            return json.dumps({"user_id": args["user_id"], "address": {k: args[k] for k in ADDRESS}})
        return StudyGate({(intent.domain, intent.tool_name): fn or default}, registry=self.registry, event_callback=callback)

    def approve(self, gate, intent, text="yes", **reply):
        prompt = gate.propose(intent, prompt_event_id="actual-prompt", prompt_sequence=10)
        self.assertEqual("WAITING_FOR_CUSTOMER", prompt.status)
        return gate.respond(text, prompt_id=reply.get("prompt_id", prompt.prompt_id), event_id="actual-customer", sequence=reply.get("sequence", 11), source=reply.get("source", "user"))

    def test_complete_registry_native_names_and_effects(self):
        self.assertEqual(16, len(self.registry.names("retail")))
        self.assertEqual(14, len(self.registry.names("airline")))
        for domain, count in (("retail", 7), ("airline", 6)):
            mutations = [n for n in self.registry.names(domain) if self.registry.get(domain, n).effect == "mutation"]
            self.assertEqual(count, len(mutations))
            self.registry.validate_inventory(domain, self.registry.names(domain) - {"think"})
            for name in self.registry.names(domain):
                self.assertIn(name, self.registry.get(domain, name).source)
                self.assertTrue(self.registry.get(domain, name).policy_version.startswith("sha256:"))
        self.assertEqual("conservative_extension", self.registry.get("airline", "send_certificate").confirmation_scope)
        self.assertEqual("control", self.registry.get("retail", "transfer_to_human_agents").effect)
        self.assertEqual("internal", self.registry.get("airline", "think").effect)
        self.assertEqual("calculation", self.registry.get("airline", "calculate").effect)

    def test_unknown_and_incomplete_inventories_fail_closed(self):
        for names in (self.registry.names("retail") | {"get_item_details"}, self.registry.names("retail") - {"calculate"}, ["calculate", "calculate"]):
            with self.assertRaises(ValueError):
                self.registry.validate_inventory("retail", names)
        with self.assertRaises(ValueError):
            self.registry.get("retail", "modify_looks_like_a_mutation")

    def test_all_seven_retail_schemas_and_exact_json(self):
        for tool, args in RETAIL_ARGS.items():
            with self.subTest(tool=tool):
                intent = self.intent(tool)
                self.assertEqual(args, intent.arguments)
                self.assertIn(json.dumps(args, sort_keys=True, indent=2), intent.payload["summary"])
                self.assertEqual("#SYN001", args.get("order_id", "#SYN001"))
                reordered = {k: args[k] for k in reversed(args)}
                self.assertEqual(intent.fingerprint, self.intent(tool, args=reordered).fingerprint)
                injected = {**args, "approved": True}
                with self.assertRaises(ValueError):
                    self.intent(tool, args=injected)

    def test_airline_synthetic_public_schemas(self):
        for tool, args in AIRLINE_ARGS.items():
            with self.subTest(tool=tool):
                intent = self.intent(tool, "airline")
                self.assertEqual(args, intent.arguments)
                self.assertIn(json.dumps(args, sort_keys=True, indent=2), intent.payload["summary"])
                if tool == "send_certificate":
                    self.assertIn("conservative study extension", intent.payload["summary"])
        args = deepcopy(AIRLINE_ARGS["book_reservation"])
        args["flights"][0]["approved"] = True
        with self.assertRaises(ValueError):
            self.intent("book_reservation", "airline", args=args)
        args = deepcopy(AIRLINE_ARGS["book_reservation"])
        args["total_baggages"] = True
        with self.assertRaises(ValueError):
            self.intent("book_reservation", "airline", args=args)

    def test_each_native_argument_changes_fingerprint(self):
        for domain, fixtures in (("retail", RETAIL_ARGS), ("airline", AIRLINE_ARGS)):
            for tool, args in fixtures.items():
                original = self.intent(tool, domain)
                for name, value in args.items():
                    changed = deepcopy(args)
                    schema = self.registry.get(domain, tool).schema["properties"][name]
                    if schema.get("enum"):
                        changed[name] = next(v for v in schema["enum"] if v != value)
                    elif type(value) is str:
                        changed[name] += "X"
                    elif type(value) in (int, float):
                        changed[name] += 1
                    elif type(value) is list:
                        changed[name].append(deepcopy(value[0]))
                        if name == "item_ids" and "new_item_ids" in args:
                            changed["new_item_ids"].append(deepcopy(args["new_item_ids"][0]))
                        if name == "new_item_ids":
                            changed["item_ids"].append(deepcopy(args["item_ids"][0]))
                        if name == "payment_methods":
                            # Keep observed total consistent to isolate native-argument identity.
                            facts = fixture_facts(self.registry, domain, tool)
                            total = MaterialFact("total_price", ObservationRef("visible-2", "tool:airline/calculate", "$", "260.02", 2))
                            changed_intent = self.intent(tool, domain, args=changed, facts=[total])
                            self.assertNotEqual(original.fingerprint, changed_intent.fingerprint)
                            continue
                    with self.subTest(domain=domain, tool=tool, field=name):
                        self.assertNotEqual(original.fingerprint, self.intent(tool, domain, args=changed).fingerprint)

    def test_lists_duplicate_order_pairing_and_strings_not_normalized(self):
        original = self.intent("modify_pending_order_items")
        summary = original.payload["summary"]
        self.assertIn('Item pair 1: "0001" -> "0003"', summary)
        self.assertIn('Item pair 2: "0001" -> "0004"', summary)
        args = original.arguments
        args["new_item_ids"].reverse()
        self.assertNotEqual(original.fingerprint, self.intent("modify_pending_order_items", args=args).fingerprint)
        args = original.arguments
        args["item_ids"] = ["0001", "0002"]
        args["new_item_ids"] = ["0003", "0005"]
        self.assertNotEqual(original.fingerprint, self.intent("modify_pending_order_items", args=args).fingerprint)
        self.assertEqual("00123", self.intent().arguments["zip"])

    def test_nested_order_and_duplicate_passengers_and_payment_allocations(self):
        args = deepcopy(AIRLINE_ARGS["book_reservation"])
        args["passengers"] += [{"first_name": "Ana", "last_name": "O'Neil", "dob": "1991-01-01"}]
        original = self.intent("book_reservation", "airline", args=args)
        args["passengers"].reverse()
        self.assertNotEqual(original.fingerprint, self.intent("book_reservation", "airline", args=args).fingerprint)
        args = deepcopy(AIRLINE_ARGS["book_reservation"])
        args["payment_methods"] = [{"payment_id": "a", "amount": 65.005}, {"payment_id": "a", "amount": 65.005}]
        intent = self.intent("book_reservation", "airline", args=args)
        self.assertEqual(2, len(intent.arguments["payment_methods"]))

    def test_canonical_rejects_non_json_and_nonfinite(self):
        for value in ({1: "key"}, {"x": float("nan")}, {"x": float("inf")}, {"x": (1, 2)}, {"x": object()}):
            with self.assertRaises((ValueError, TypeError)):
                canonical_json(value)
        self.assertNotEqual(canonical_json({"amount": "10.010"}), canonical_json({"amount": "10.01"}))

    def test_immutable_arguments_and_evidence_copies(self):
        args = deepcopy(RETAIL_ARGS["modify_user_address"])
        intent = self.intent(args=args)
        args["zip"] = "99999"
        exposed = intent.arguments
        exposed["zip"] = "22222"
        self.assertEqual("00123", intent.arguments["zip"])
        value = [{"x": 1}]
        ref = ObservationRef("e", "user", "$.x", value, 1)
        value[0]["x"] = 2
        ref.value[0]["x"] = 3
        self.assertEqual([{"x": 1}], ref.value)
        with self.assertRaises(FrozenInstanceError):
            intent.revision = 9

    def test_missing_material_facts_returns_control_need_details(self):
        for domain, fixtures in (("retail", RETAIL_ARGS), ("airline", AIRLINE_ARGS)):
            for tool, args in fixtures.items():
                required = self.registry.get(domain, tool).display_fields
                built = build_intent(domain, tool, args, intent_id="i", revision=1, registry=self.registry)
                self.assertEqual("NEED_DETAILS" if required else "PREPARED", built.status)
                self.assertEqual(required, built.missing_fields)
        args = deepcopy(RETAIL_ARGS["modify_pending_order_items"])
        args["new_item_ids"].pop()
        built = build_intent("retail", "modify_pending_order_items", args, intent_id="i", revision=1,
                             facts=fixture_facts(self.registry, "retail", "modify_pending_order_items"), registry=self.registry)
        self.assertEqual("NEED_DETAILS", built.status)

    def test_observation_domain_and_source_validation(self):
        for source in ("assistant", "hidden_database", "task", "reference_actions"):
            with self.assertRaises(ValueError):
                ObservationRef("e", source, "$", 1, 0)
        wrong = [MaterialFact("user_id", ObservationRef("e", "tool:airline/get_user_details", "$.user_id", "u", 1))]
        with self.assertRaises(ValueError):
            self.intent("modify_pending_order_address", facts=wrong)

    def test_facts_refs_revision_and_versions_bound_to_fingerprint(self):
        original = self.intent("modify_pending_order_items")
        facts = fixture_facts(self.registry, "retail", "modify_pending_order_items")
        facts[-1] = MaterialFact("price_difference", ObservationRef("visible-new", "tool:retail/calculate", "$", "10.010", 2))
        changed = self.intent("modify_pending_order_items", facts=facts)
        self.assertNotEqual(original.fingerprint, changed.fingerprint)
        self.assertNotEqual(original.fingerprint, self.intent("modify_pending_order_items", revision=2).fingerprint)
        for key in ("policy_version", "registry_version", "renderer_version", "tool_source_sha256"):
            payload = original.payload
            payload[key] += "changed"
            forged = MutationIntent(original.intent_id, original.revision, payload)
            self.assertNotEqual(original.fingerprint, forged.fingerprint)
            with self.assertRaises(ValueError):
                self.gate(original).propose(forged, prompt_event_id="p", prompt_sequence=10)

    def test_money_direction_no_guessed_charges(self):
        facts = fixture_facts(self.registry, "retail", "modify_pending_order_items")
        facts[-1] = MaterialFact("price_difference", ObservationRef("calc", "tool:retail/calculate", "$", "-10.010", 2))
        summary = self.intent("modify_pending_order_items", facts=facts).payload["summary"]
        self.assertIn("direction: refund; amount: 10.010", summary)
        self.assertIn("not a verified entitlement", summary)

    def test_assent_full_response_rule(self):
        for text in ("yes", " YES. ", "I   confirm"):
            self.assertEqual("AFFIRM", classify_response(text))
        for text in ("yes if the price is lower", '"yes"', "yes and no", "sure", "yesterday", "not yes"):
            self.assertEqual("AMBIGUOUS", classify_response(text))
        self.assertEqual("CHANGE", classify_response("yes, but change the date"))
        self.assertEqual("REJECT", classify_response("no"))

    def test_fresh_affirmative_dispatch_once_and_consumed_before_callback(self):
        intent = self.intent()
        calls = []
        events = []
        gate = self.gate(intent, fn=lambda args: (calls.append(args), json.dumps({"user_id": args["user_id"], "address": {k: args[k] for k in ADDRESS}}))[1], callback=lambda kind, payload: events.append((kind, len(calls), payload)))
        self.assertEqual("INVALIDATED", gate.dispatch(intent).status)
        self.assertEqual("AUTHORIZED", self.approve(gate, intent).status)
        self.assertEqual("SUCCEEDED", gate.dispatch(intent).status)
        self.assertEqual("ALREADY_ATTEMPTED", gate.dispatch(intent).status)
        self.assertEqual(1, len(calls))
        attempted = next(event for event in events if event[0] == "ATTEMPTED")
        self.assertEqual(0, attempted[1])
        self.assertEqual("ATTEMPTED", attempted[2]["outcome"])
        self.assertEqual(intent.arguments, calls[0])
        self.assertEqual("SUCCEEDED", gate.snapshot()["attempts"][0]["outcome"])

    def test_reject_change_ambiguous_never_native_results(self):
        for text, status in (("no", "REJECT"), ("yes, but change city", "CHANGE"), ("yes if free", "AMBIGUOUS")):
            intent = self.intent()
            gate = self.gate(intent, fn=lambda args: self.fail("Native write must not execute"))
            control = self.approve(gate, intent, text)
            self.assertEqual(status, control.status)
            self.assertFalse(control.dispatched)
            self.assertIsNone(control.native_result)
            self.assertEqual("NOT_AUTHORIZED", gate.dispatch(intent).status)
            self.assertEqual("STALE_RESPONSE", gate.respond("yes", prompt_id=control.prompt_id, event_id="second", sequence=12, source="user").status)

    def test_stale_non_user_and_non_immediate_responses_block(self):
        for reply in ({"prompt_id": "old"}, {"sequence": 12}, {"source": "tool"}, {"source": "assistant"}):
            intent = self.intent()
            gate = self.gate(intent, fn=lambda args: self.fail("Must not execute"))
            self.assertEqual("STALE_RESPONSE", self.approve(gate, intent, **reply).status)
            self.assertEqual("NOT_AUTHORIZED", gate.dispatch(intent).status)

    def test_new_payload_and_new_evidence_invalidate_approval(self):
        intent = self.intent("modify_pending_order_address")
        gate = self.gate(intent, fn=lambda args: self.fail("Must not execute"))
        self.approve(gate, intent)
        args = intent.arguments
        args["city"] = "Changed"
        changed = self.intent("modify_pending_order_address", args=args, revision=2)
        self.assertEqual("INVALIDATED", gate.dispatch(changed).status)
        self.assertEqual("INVALIDATED", gate.dispatch(intent).status)
        gate = self.gate(intent, fn=lambda args: self.fail("Must not execute"))
        self.approve(gate, intent)
        facts = [MaterialFact("user_id", ObservationRef("read-new", "tool:retail/get_order_details", "$.user_id", "synthetic_user", 2))]
        changed = self.intent("modify_pending_order_address", facts=facts)
        self.assertEqual("INVALIDATED", gate.dispatch(changed).status)

    def test_restart_and_explicit_evidence_invalidation(self):
        for invalidate in (lambda gate: gate.restart(), lambda gate: gate.invalidate("Relevant new evidence")):
            intent = self.intent()
            gate = self.gate(intent, fn=lambda args: self.fail("Must not execute"))
            self.approve(gate, intent)
            invalidate(gate)
            self.assertEqual("INVALIDATED", gate.dispatch(intent).status)

    def test_new_prompt_revision_and_old_prompt_binding(self):
        intent = self.intent()
        gate = self.gate(intent)
        first = gate.propose(intent, prompt_event_id="p1", prompt_sequence=10)
        second_intent = self.intent(revision=2)
        second = gate.propose(second_intent, prompt_event_id="p2", prompt_sequence=20)
        self.assertNotEqual(first.prompt_id, second.prompt_id)
        result = gate.respond("yes", prompt_id=first.prompt_id, event_id="u2", sequence=21, source="user")
        self.assertEqual("STALE_RESPONSE", result.status)
        self.assertEqual("NOT_AUTHORIZED", gate.dispatch(second_intent).status)
        self.assertEqual("INVALID_REVISION", gate.propose(second_intent, prompt_event_id="p3", prompt_sequence=30).status)

    def test_mutate_then_raise_uncertain_no_retries_even_new_id(self):
        intent = self.intent()
        state = []
        def mutation(args):
            state.append(args)
            raise RuntimeError("Error: user not found")
        gate = self.gate(intent, fn=mutation)
        self.approve(gate, intent)
        result = gate.dispatch(intent)
        self.assertEqual("UNCERTAIN_MUTATION", result.status)
        self.assertTrue(result.dispatched)
        self.assertEqual("UNCERTAIN_MUTATION", gate.dispatch(intent).status)
        self.assertEqual("UNCERTAIN_MUTATION", gate.propose(self.intent(intent_id="retry"), prompt_event_id="retry", prompt_sequence=30).status)
        gate.restart()
        self.assertEqual("UNCERTAIN_MUTATION", gate.dispatch(intent).status)
        self.assertEqual(1, len(state))

    def test_native_caught_exception_error_observation_uncertain(self):
        intent = self.intent()
        state = []
        def native_shaped(args):
            try:
                state.append(args)
                raise RuntimeError("partial write")
            except RuntimeError as error:
                return "Error: " + str(error)
        gate = self.gate(intent, fn=native_shaped)
        self.approve(gate, intent)
        self.assertEqual("UNCERTAIN_MUTATION", gate.dispatch(intent).status)
        self.assertEqual(1, len(state))

    def test_exact_audited_error_rejected_unknown_shape_uncertain(self):
        intent = self.intent()
        for result, expected in (("Error: user not found", "REJECTED"), ("Error: user not found ", "UNCERTAIN"), ("Error: brand new", "UNCERTAIN"), ("looks successful", "UNCERTAIN"), ({"user_id": "u"}, "UNCERTAIN")):
            self.assertEqual(expected, classify_native_result(intent, result))
        gate = self.gate(intent, fn=lambda args: "Error: user not found")
        self.approve(gate, intent)
        self.assertEqual("REJECTED", gate.dispatch(intent).status)
        self.assertEqual("ALREADY_ATTEMPTED", gate.dispatch(intent).status)

    def test_all_public_mutation_return_shapes_and_native_kwargs(self):
        statuses = {"cancel_pending_order": "cancelled", "modify_pending_order_address": "pending",
                    "modify_pending_order_payment": "pending", "modify_pending_order_items": "pending (item modified)",
                    "return_delivered_order_items": "return requested", "exchange_delivered_order_items": "exchange requested"}
        for domain, fixtures in (("retail", RETAIL_ARGS), ("airline", AIRLINE_ARGS)):
            for tool, args in fixtures.items():
                with self.subTest(domain=domain, tool=tool):
                    intent = self.intent(tool, domain)
                    returned = {"user_id": args.get("user_id", "synthetic_user")}
                    if domain == "retail" and "order_id" in args:
                        returned.update(order_id=args["order_id"], status=statuses[tool])
                    if tool in {"modify_user_address", "modify_pending_order_address"}:
                        returned["address"] = {k: args[k] for k in ADDRESS}
                    if domain == "airline":
                        returned["reservation_id"] = args.get("reservation_id", "HATHAT")
                        if tool == "cancel_reservation":
                            returned["status"] = "cancelled"
                    native_result = (f"Certificate certificate_3221322 added to user {args['user_id']} with amount {args['amount']}."
                                     if tool == "send_certificate" else json.dumps(returned))
                    calls = []
                    def invoke(kwargs, observation=native_result):
                        calls.append(kwargs)
                        return observation
                    gate = self.gate(intent, fn=invoke)
                    self.approve(gate, intent)
                    result = gate.dispatch(intent)
                    self.assertEqual("SUCCEEDED", result.status)
                    self.assertEqual([args], calls)
                    self.assertEqual(native_result, result.native_result)

    def test_audited_error_literals_exist_in_public_sources(self):
        source_root = Path(__file__).resolve().parents[1] / "vendor/tau-bench-paper-era"
        for (domain, tool), errors in PRE_EFFECT_ERRORS.items():
            spec = self.registry.get(domain, tool)
            tree = ast.parse((source_root / spec.source).read_text())
            returns = {node.value.value for node in ast.walk(tree)
                       if isinstance(node, ast.Return) and isinstance(node.value, ast.Constant)
                       and type(node.value.value) is str}
            self.assertTrue(errors <= returns, (domain, tool, errors - returns))
            intent = self.intent(tool, domain)
            for error in errors:
                self.assertEqual("REJECTED", classify_native_result(intent, error))

    def test_displayed_total_contradiction_needs_details(self):
        facts = [MaterialFact("total_price", ObservationRef("calc", "tool:airline/calculate", "$", "99.00", 1))]
        built = build_intent("airline", "book_reservation", AIRLINE_ARGS["book_reservation"], intent_id="i", revision=1,
                             facts=facts, registry=self.registry)
        self.assertEqual("NEED_DETAILS", built.status)
        self.assertEqual(("consistent_total_price",), built.missing_fields)

    def test_callback_failure_before_dispatch_fails_closed(self):
        intent = self.intent()
        calls = []
        def persist(kind, payload):
            if kind == "ATTEMPTED":
                raise OSError("disk full")
        gate = self.gate(intent, fn=lambda args: calls.append(args), callback=persist)
        self.approve(gate, intent)
        result = gate.dispatch(intent)
        self.assertEqual("PERSISTENCE_ERROR", result.status)
        self.assertFalse(result.dispatched)
        self.assertEqual([], calls)
        self.assertFalse(gate.snapshot()["attempts"][0]["native_call_started"])
        self.assertEqual("PERSISTENCE_ERROR", gate.dispatch(intent).status)

    def test_callback_failure_after_dispatch_no_retry(self):
        intent = self.intent()
        def persist(kind, payload):
            if kind == "SUCCEEDED":
                raise OSError("disk full")
        gate = self.gate(intent, callback=persist)
        self.approve(gate, intent)
        result = gate.dispatch(intent)
        self.assertEqual("PERSISTENCE_ERROR", result.status)
        self.assertTrue(result.dispatched)
        self.assertEqual("PERSISTENCE_ERROR", gate.dispatch(intent).status)

    def test_callback_reentry_fails_closed_and_snapshot_allowed(self):
        intent = self.intent()
        calls = []
        def persist(kind, payload):
            if kind == "ATTEMPTED":
                self.assertEqual("ATTEMPTED", gate.snapshot()["attempts"][0]["outcome"])
                gate.dispatch(intent)
        gate = self.gate(intent, fn=lambda args: calls.append(args), callback=persist)
        self.approve(gate, intent)
        self.assertEqual("PERSISTENCE_ERROR", gate.dispatch(intent).status)
        self.assertEqual([], calls)

    def test_duplicate_payload_new_id_after_success_blocked(self):
        intent = self.intent()
        gate = self.gate(intent)
        self.approve(gate, intent)
        gate.dispatch(intent)
        second = self.intent(intent_id="new-id")
        self.assertEqual("ALREADY_ATTEMPTED", gate.propose(second, prompt_event_id="p2", prompt_sequence=30).status)

    def test_future_evidence_and_forged_summary_denied(self):
        intent = self.intent("modify_pending_order_address")
        gate = self.gate(intent)
        with self.assertRaises(ValueError):
            gate.propose(intent, prompt_event_id="p", prompt_sequence=1)
        payload = intent.payload
        payload["summary"] = "Approve something harmless?"
        with self.assertRaises(ValueError):
            gate.propose(MutationIntent(intent.intent_id, intent.revision, payload), prompt_event_id="p", prompt_sequence=10)


if __name__ == "__main__":
    unittest.main()
