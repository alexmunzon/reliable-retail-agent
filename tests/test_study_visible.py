"""Synthetic actual-event fixtures for trusted visible material fact extraction."""
from copy import deepcopy
import json
import unittest

from study import MaterialFact, ObservationRef, StudyGate, ToolRegistry, build_intent
from study.intents import DerivedRef, FORMULA_VERSION, MutationIntent
from study.visible import VisibleStore


ADDRESS_ARGS = {"order_id": "#SYN1", "address1": "1 Main", "address2": "", "city": "Example", "state": "CA", "country": "USA", "zip": "00123"}
ORDER = {"order_id": "#SYN1", "user_id": "visible_user", "status": "pending",
         "payment_history": [{"transaction_type": "payment", "payment_method_id": "credit_card_visible", "amount": 100.01}],
         "items": [{"item_id": "0001", "price": 100.01}, {"item_id": "0002", "price": 5.01}]}
RESERVATION = {"reservation_id": "SYN1", "user_id": "visible_user",
               "flights": [{"flight_number": "SYN100", "date": "2024-06-01", "price": 100.01}],
               "passengers": [{"first_name": "Jo", "last_name": "Li", "dob": "1990-01-01"}],
               "payment_history": [{"payment_id": "credit_card_visible", "amount": 100.01}], "nonfree_baggages": 0}


class VisibleStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = ToolRegistry()

    def store(self, domain="retail"):
        return VisibleStore(domain, self.registry)

    def order(self, store, *, value=None, event_id="order-visible", sequence=1, order_id="#SYN1"):
        store.record(event_id, sequence, {"name": "get_order_details", "arguments": {"order_id": order_id}},
                     json.dumps(ORDER if value is None else value))

    def reservation(self, store, *, value=None, event_id="reservation-visible", sequence=1, reservation_id="SYN1"):
        store.record(event_id, sequence, {"name": "get_reservation_details", "arguments": {"reservation_id": reservation_id}},
                     json.dumps(RESERVATION if value is None else value))

    def test_address_facts_resolve_actual_matching_order(self):
        store = self.store()
        self.order(store)
        facts = store.facts_for("modify_pending_order_address", ADDRESS_ARGS)
        self.assertEqual(["user_id"], [fact.name for fact in facts])
        self.assertEqual("visible_user", facts[0].value)
        self.assertEqual("/user_id", facts[0].evidence.field_path)
        self.assertEqual("visible_user", store.resolve(facts[0].evidence))
        built = build_intent("retail", "modify_pending_order_address", ADDRESS_ARGS, intent_id="i", revision=1, facts=facts, registry=self.registry)
        self.assertEqual("PREPARED", built.status)

    def test_unrelated_or_contradictory_object_id_never_used(self):
        store = self.store()
        self.order(store, order_id="#other")
        self.assertEqual((), store.facts_for("modify_pending_order_address", ADDRESS_ARGS))
        self.order(store, value={**ORDER, "order_id": "#other"}, event_id="wrong-return", sequence=2)
        self.assertEqual((), store.facts_for("modify_pending_order_address", ADDRESS_ARGS))

    def test_latest_error_or_malformed_reply_does_not_resurrect_old_facts(self):
        for observation in ("Error: order not found", "{broken", "[]", json.dumps({"order_id": "#wrong"})):
            store = self.store()
            self.order(store)
            store.record("latest-error", 2, {"name": "get_order_details", "arguments": {"order_id": "#SYN1"}}, observation)
            self.assertEqual((), store.facts_for("modify_pending_order_address", ADDRESS_ARGS))
            self.assertEqual(observation, store.events[-1]["observation"])

    def test_refs_must_match_actual_source_sequence_field_and_value(self):
        store = self.store()
        self.order(store)
        original = store.facts_for("modify_pending_order_address", ADDRESS_ARGS)[0].evidence
        for kwargs in ({"event_id": "hidden"}, {"source": "tool:retail/get_user_details"}, {"sequence": 2}, {"field_path": "/missing"}, {"value": "invented_user"}):
            args = {"event_id": original.event_id, "source": original.source, "field_path": original.field_path, "sequence": original.sequence, "value": original.value, **kwargs}
            with self.assertRaises(ValueError):
                store.resolve(ObservationRef(**args))

    def test_unique_monotone_events_and_exact_actions(self):
        store = self.store()
        self.order(store)
        with self.assertRaises(ValueError):
            self.order(store, sequence=2)
        with self.assertRaises(ValueError):
            self.order(store, event_id="second", sequence=1)
        with self.assertRaises(ValueError):
            store.record("e", 3, {"name": "get_order_details", "arguments": {"order_id": "#SYN1"}, "data": {}}, "{}")
        with self.assertRaises(ValueError):
            store.record("e", 3, {"name": "get_item_details", "arguments": {}}, "{}")
        with self.assertRaises(ValueError):
            store.record("e", 3, {"name": "get_order_details", "arguments": {"order_id": "#SYN1", "approved": True}}, "{}")

    def test_native_action_and_exposed_events_are_defensive_copies(self):
        store = self.store()
        action = {"name": "get_order_details", "arguments": {"order_id": "#SYN1"}}
        store.record("e", 1, action, json.dumps(ORDER))
        action["arguments"]["order_id"] = "#wrong"
        store.events[0]["action"]["arguments"]["order_id"] = "#wrong"
        self.assertEqual("#SYN1", store.events[0]["action"]["arguments"]["order_id"])
        self.assertEqual("visible_user", store.facts_for("modify_pending_order_address", ADDRESS_ARGS)[0].value)

    def test_retail_direct_cancel_and_payment_facts(self):
        store = self.store()
        self.order(store)
        for tool, args, names in (
            ("cancel_pending_order", {"order_id": "#SYN1", "reason": "no longer needed"}, ["user_id", "refund_payments"]),
            ("modify_pending_order_payment", {"order_id": "#SYN1", "payment_method_id": "gift_card_other"}, ["user_id", "original_payment_id", "charge_amount"]),
        ):
            facts = store.facts_for(tool, args)
            self.assertEqual(names, [fact.name for fact in facts])
            for fact in facts:
                self.assertEqual(fact.value, store.resolve(fact.evidence))
            self.assertEqual("PREPARED", build_intent("retail", tool, args, intent_id="i", revision=1, facts=facts, registry=self.registry).status)

    def test_payment_switch_requires_one_original_payment(self):
        store = self.store()
        value = deepcopy(ORDER)
        value["payment_history"] *= 2
        self.order(store, value=value)
        args = {"order_id": "#SYN1", "payment_method_id": "gift_card_other"}
        facts = store.facts_for("modify_pending_order_payment", args)
        self.assertEqual(["user_id"], [fact.name for fact in facts])

    def test_single_item_return_price_exact_missing_duplicate_unresolved(self):
        store = self.store()
        self.order(store)
        args = {"order_id": "#SYN1", "item_ids": ["0001"], "payment_method_id": "credit_card_visible"}
        facts = store.facts_for("return_delivered_order_items", args)
        self.assertEqual("100.01", facts[-1].value)
        self.assertIsInstance(facts[-1].evidence, DerivedRef)
        self.assertEqual("/items/0", facts[-1].evidence.inputs[1].field_path)
        args["item_ids"] *= 2
        facts = store.facts_for("return_delivered_order_items", args)
        self.assertEqual(["user_id"], [fact.name for fact in facts])

    def test_airline_passenger_mutation_direct_facts_complete(self):
        store = self.store("airline")
        self.reservation(store)
        args = {"reservation_id": "SYN1", "passengers": [{"first_name": "New", "last_name": "Name", "dob": "1990-01-01"}]}
        facts = store.facts_for("update_reservation_passengers", args)
        self.assertEqual(["user_id", "original_passengers"], [fact.name for fact in facts])
        self.assertEqual("PREPARED", build_intent("airline", "update_reservation_passengers", args, intent_id="i", revision=1, facts=facts, registry=self.registry).status)

    def test_airline_cancellation_exact_reason_single_reservation_only(self):
        store = self.store("airline")
        store.record_user("reason", 0, "change of plan")
        self.reservation(store)
        args = {"reservation_id": "SYN1"}
        facts = store.facts_for("cancel_reservation", args)
        self.assertEqual(["user_id", "cancellation_reason", "flights", "passengers", "refund_payments"], [fact.name for fact in facts])
        self.assertEqual("change of plan", store.resolve(facts[1].evidence))
        self.assertEqual("PREPARED", build_intent("airline", "cancel_reservation", args, intent_id="i", revision=1, facts=facts, registry=self.registry).status)
        store.record_user("arbitrary", 2, "Maybe change plans, cancel something")
        self.assertNotIn("cancellation_reason", [fact.name for fact in store.facts_for("cancel_reservation", args)])
        store.record_user("reason2", 3, "change of plan")
        self.reservation(store, value={**RESERVATION, "reservation_id": "SYN2"}, event_id="other", sequence=4, reservation_id="SYN2")
        self.assertNotIn("cancellation_reason", [fact.name for fact in store.facts_for("cancel_reservation", args)])

    def test_financial_derivations_not_fabricated_for_any_mutation(self):
        store = self.store()
        self.order(store)
        for tool in ("modify_pending_order_items", "exchange_delivered_order_items"):
            args = {"order_id": "#SYN1", "item_ids": ["0001"], "new_item_ids": ["0002"], "payment_method_id": "credit_card_visible"}
            facts = store.facts_for(tool, args)
            self.assertEqual(["user_id"], [fact.name for fact in facts])
            built = build_intent("retail", tool, args, intent_id="i", revision=1, facts=facts, registry=self.registry)
            self.assertEqual(("price_difference",), built.missing_fields)
        store = self.store("airline")
        self.reservation(store)
        examples = {
            "update_reservation_flights": ({"reservation_id": "SYN1", "cabin": "business", "flights": [{"flight_number": "SYN200", "date": "2024-06-02"}], "payment_id": "credit_card_visible"}, "price_difference"),
            "book_reservation": ({"user_id": "visible_user", "origin": "SFO", "destination": "JFK", "flight_type": "one_way", "cabin": "economy", "flights": [{"flight_number": "SYN100", "date": "2024-06-01"}], "passengers": RESERVATION["passengers"], "payment_methods": [{"payment_id": "credit_card_visible", "amount": 100.01}], "total_baggages": 0, "nonfree_baggages": 0, "insurance": "no"}, "total_price"),
        }
        for tool, (args, missing) in examples.items():
            facts = store.facts_for(tool, args)
            built = build_intent("airline", tool, args, intent_id="i", revision=1, facts=facts, registry=self.registry)
            self.assertEqual("NEED_DETAILS", built.status)
            self.assertIn(missing, built.missing_fields)
        self.assertEqual((), store.facts_for("send_certificate", {"user_id": "visible_user", "amount": 100}))

    def priced_retail(self):
        store = self.store()
        order = deepcopy(ORDER)
        order["items"] = [{"item_id": "0001", "product_id": "P", "price": 100.01},
                          {"item_id": "0001", "product_id": "P", "price": 100.01},
                          {"item_id": "0002", "product_id": "P", "price": 5.01}]
        self.order(store, value=order)
        store.record("product", 2, {"name": "get_product_details", "arguments": {"product_id": "P"}},
                     json.dumps({"product_id": "P", "variants": {"new1": {"price": 105.02, "available": True}, "new2": {"price": 3.99, "available": True}}}))
        return store

    def priced_airline(self):
        store = self.store("airline")
        store.record_user("reason", 0, "change of plan")
        self.reservation(store, value={**RESERVATION, "cabin": "economy"})
        for sequence, date, flight, economy, business in ((2, "2024-06-01", "SYN100", 100.01, 130.02),
                                                        (3, "2024-06-02", "SYN200", 120.03, 150.04)):
            store.record(f"search-{sequence}", sequence,
                         {"name": "search_direct_flight", "arguments": {"origin": "SFO", "destination": "JFK", "date": date}},
                         json.dumps([{"flight_number": flight, "origin": "SFO", "destination": "JFK", "prices": {"economy": economy, "business": business}, "status": "available"}]))
        return store

    def test_all_seven_retail_mutations_complete_with_visible_prices(self):
        store = self.priced_retail()
        changes = {"order_id": "#SYN1", "item_ids": ["0001", "0001", "0002"], "new_item_ids": ["new1", "new1", "new2"], "payment_method_id": "credit_card_visible"}
        fixtures = {
            "cancel_pending_order": {"order_id": "#SYN1", "reason": "no longer needed"},
            "modify_pending_order_address": ADDRESS_ARGS,
            "modify_user_address": {"user_id": "visible_user", **{k: v for k, v in ADDRESS_ARGS.items() if k != "order_id"}},
            "modify_pending_order_payment": {"order_id": "#SYN1", "payment_method_id": "gift_card_other"},
            "modify_pending_order_items": changes,
            "exchange_delivered_order_items": changes,
            "return_delivered_order_items": {"order_id": "#SYN1", "item_ids": ["0001", "0001", "0002"], "payment_method_id": "credit_card_visible"},
        }
        for tool, args in fixtures.items():
            facts = store.facts_for(tool, args)
            built = build_intent("retail", tool, args, intent_id=tool, revision=1, facts=facts, registry=self.registry)
            self.assertEqual("PREPARED", built.status, (tool, built.missing_fields))
            for fact in facts:
                self.assertEqual(fact.value, store.resolve(fact.evidence))
        self.assertEqual("9.00", store.facts_for("modify_pending_order_items", changes)[-1].value)
        returned = store.facts_for("return_delivered_order_items", fixtures["return_delivered_order_items"])[-1]
        self.assertEqual("205.03", returned.value)
        self.assertEqual(["/items/0", "/items/1", "/items/2"], [ref.field_path for ref in returned.evidence.inputs[1:]])

    def test_all_six_airline_mutations_complete_and_affirm_does_not_erase_reason(self):
        store = self.priced_airline()
        fixtures = {
            "book_reservation": {"user_id": "visible_user", "origin": "SFO", "destination": "JFK", "flight_type": "one_way", "cabin": "economy", "flights": [{"flight_number": "SYN100", "date": "2024-06-01"}], "passengers": RESERVATION["passengers"], "payment_methods": [{"payment_id": "credit_card_visible", "amount": 130.01}], "total_baggages": 1, "nonfree_baggages": 0, "insurance": "yes"},
            "cancel_reservation": {"reservation_id": "SYN1"},
            "update_reservation_flights": {"reservation_id": "SYN1", "cabin": "business", "flights": [{"flight_number": "SYN200", "date": "2024-06-02"}], "payment_id": "credit_card_visible"},
            "update_reservation_baggages": {"reservation_id": "SYN1", "total_baggages": 3, "nonfree_baggages": 2, "payment_id": "credit_card_visible"},
            "update_reservation_passengers": {"reservation_id": "SYN1", "passengers": RESERVATION["passengers"]},
            "send_certificate": {"user_id": "visible_user", "amount": 100},
        }
        before = {}
        for tool, args in fixtures.items():
            facts = store.facts_for(tool, args)
            built = build_intent("airline", tool, args, intent_id=tool, revision=1, facts=facts, registry=self.registry)
            self.assertEqual("PREPARED", built.status, (tool, built.missing_fields))
            before[tool] = built.intent.fingerprint
            for fact in facts:
                self.assertEqual(fact.value, store.resolve(fact.evidence))
        store.record_user("confirmation", 11, "yes")
        for tool, args in fixtures.items():
            built = build_intent("airline", tool, args, intent_id=tool, revision=1, facts=store.facts_for(tool, args), registry=self.registry)
            self.assertEqual("PREPARED", built.status, tool)
            self.assertEqual(before[tool], built.intent.fingerprint)
        self.assertEqual("50.03", store.facts_for("update_reservation_flights", fixtures["update_reservation_flights"])[-1].value)
        self.assertEqual("100", store.facts_for("update_reservation_baggages", fixtures["update_reservation_baggages"])[-1].value)

    def test_derived_roundtrip_and_forged_result_or_version_rejected(self):
        store = self.priced_retail()
        args = {"order_id": "#SYN1", "item_ids": ["0001"], "new_item_ids": ["new1"], "payment_method_id": "credit_card_visible"}
        ref = store.facts_for("modify_pending_order_items", args)[-1].evidence
        self.assertEqual(ref, DerivedRef.from_dict(ref.to_dict()))
        self.assertTrue(ref.source.startswith("derived:"))
        self.assertNotIn("calculate", ref.source)
        self.assertEqual("5.01", store.resolve(ref))
        with self.assertRaises(ValueError):
            MaterialFact("user_id", ref)
        for key, value in (("value", "999"), ("formula_version", "unknown"), ("sequence", 999), ("formula_id", "evaluate_any_expression")):
            payload = ref.to_dict()
            payload[key] = value
            with self.assertRaises(ValueError):
                DerivedRef.from_dict(payload)

    def test_changed_latest_price_invalidates_derivation_and_consent(self):
        store = self.priced_retail()
        args = {"order_id": "#SYN1", "item_ids": ["0001"], "new_item_ids": ["new1"], "payment_method_id": "credit_card_visible"}
        facts = store.facts_for("modify_pending_order_items", args)
        initial = build_intent("retail", "modify_pending_order_items", args, intent_id="i", revision=1, facts=facts, registry=self.registry).intent
        gate = StudyGate({("retail", "modify_pending_order_items"): lambda kwargs: self.fail("Changed financial terms must not dispatch")}, registry=self.registry)
        prompt = gate.propose(initial, prompt_event_id="prompt", prompt_sequence=10)
        gate.respond("yes", prompt_id=prompt.prompt_id, event_id="yes", sequence=11, source="user")
        store.record("new-price", 12, {"name": "get_product_details", "arguments": {"product_id": "P"}}, json.dumps({"product_id": "P", "variants": {"new1": {"price": 106.02}}}))
        with self.assertRaises(ValueError):
            store.resolve(facts[-1].evidence)
        changed = build_intent("retail", "modify_pending_order_items", args, intent_id="i", revision=1,
                               facts=store.facts_for("modify_pending_order_items", args), registry=self.registry).intent
        self.assertNotEqual(initial.fingerprint, changed.fingerprint)
        self.assertEqual("INVALIDATED", gate.dispatch(changed).status)

    def test_unchanged_flight_uses_original_price_and_new_cabin_uses_search_price(self):
        store = self.priced_airline()
        args = {"reservation_id": "SYN1", "cabin": "economy", "flights": [{"flight_number": "SYN100", "date": "2024-06-01"}], "payment_id": "credit_card_visible"}
        same = store.facts_for("update_reservation_flights", args)[-1]
        self.assertEqual("0.00", same.value)
        self.assertEqual("/flights/0", same.evidence.inputs[1].field_path)
        args["cabin"] = "business"
        changed = store.facts_for("update_reservation_flights", args)[-1]
        self.assertEqual("30.01", changed.value)
        self.assertEqual("search-2", changed.evidence.inputs[1].event_id)

    def test_baggage_formula_keeps_zero_charge_on_reduced_nonfree_count(self):
        store = self.priced_airline()
        self.reservation(store, value={**RESERVATION, "cabin": "economy", "nonfree_baggages": 3}, event_id="latest", sequence=4)
        args = {"reservation_id": "SYN1", "total_baggages": 1, "nonfree_baggages": 1, "payment_id": "credit_card_visible"}
        self.assertEqual("0", store.facts_for("update_reservation_baggages", args)[-1].value)

    def test_latest_failed_product_or_empty_search_keeps_need_details(self):
        store = self.priced_retail()
        args = {"order_id": "#SYN1", "item_ids": ["0001"], "new_item_ids": ["new1"], "payment_method_id": "credit_card_visible"}
        store.record("product-error", 3, {"name": "get_product_details", "arguments": {"product_id": "P"}}, "Error: product not found")
        self.assertNotIn("price_difference", [f.name for f in store.facts_for("modify_pending_order_items", args)])
        store = self.priced_airline()
        args = {"reservation_id": "SYN1", "cabin": "business", "flights": [{"flight_number": "SYN200", "date": "2024-06-02"}], "payment_id": "credit_card_visible"}
        store.record("empty", 4, {"name": "search_direct_flight", "arguments": {"origin": "SFO", "destination": "JFK", "date": "2024-06-02"}}, "[]")
        self.assertNotIn("price_difference", [f.name for f in store.facts_for("update_reservation_flights", args)])

    def test_cancellation_reason_survives_affirm_but_not_correction_or_condition(self):
        for reply, expected in (("yes", True), ("I confirm.", True), ("yes, but change date", False), ("yes if free", False), ("no", False)):
            store = self.priced_airline()
            store.record_user("customer", 11, reply)
            self.assertEqual(expected, "cancellation_reason" in [f.name for f in store.facts_for("cancel_reservation", {"reservation_id": "SYN1"})])

    def test_signed_original_airline_payment_history_is_preserved(self):
        store = self.priced_airline()
        value = {**RESERVATION, "payment_history": [{"payment_id": "credit_card_visible", "amount": 100.01}, {"payment_id": "credit_card_visible", "amount": -5.01}]}
        self.reservation(store, value=value, event_id="signed", sequence=4)
        args = {"reservation_id": "SYN1"}
        built = build_intent("airline", "cancel_reservation", args, intent_id="i", revision=1, facts=store.facts_for("cancel_reservation", args), registry=self.registry)
        self.assertEqual("PREPARED", built.status)
        self.assertIn("negative original refund becomes a charge", built.intent.payload["summary"])

    def test_onestop_next_day_inputs_preserve_segment_order(self):
        store = self.store("airline")
        store.record("connection", 1, {"name": "search_onestop_flight", "arguments": {"origin": "SFO", "destination": "JFK", "date": "2024-06-01"}},
                     json.dumps([[{"flight_number": "LEG1", "date": "2024-06-01", "prices": {"economy": 10.01}},
                                  {"flight_number": "LEG2", "date": "2024-06-02", "prices": {"economy": 20.02}}]]))
        args = {"user_id": "visible_user", "origin": "SFO", "destination": "JFK", "flight_type": "one_way", "cabin": "economy",
                "flights": [{"flight_number": "LEG1", "date": "2024-06-01"}, {"flight_number": "LEG2", "date": "2024-06-02"}],
                "passengers": RESERVATION["passengers"] * 2, "payment_methods": [{"payment_id": "credit_card_visible", "amount": 120.06}],
                "total_baggages": 0, "nonfree_baggages": 0, "insurance": "yes"}
        fact = store.facts_for("book_reservation", args)[0]
        self.assertEqual("120.06", fact.value)
        self.assertEqual(["/0/0", "/0/1"], [ref.field_path for ref in fact.evidence.inputs])
        args["flights"].reverse()
        reversed_fact = store.facts_for("book_reservation", args)[0]
        self.assertNotEqual(fact.evidence.event_id, reversed_fact.evidence.event_id)
        self.assertEqual(["/0/1", "/0/0"], [ref.field_path for ref in reversed_fact.evidence.inputs])

    def test_decimal_display_arithmetic_retains_exact_observed_inputs(self):
        store = self.store()
        self.order(store, value={**ORDER, "items": [{"item_id": "old", "product_id": "P", "price": 100.0}]})
        store.record("p", 2, {"name": "get_product_details", "arguments": {"product_id": "P"}}, json.dumps({"product_id": "P", "variants": {"new": {"price": 100.01}}}))
        args = {"order_id": "#SYN1", "item_ids": ["old"], "new_item_ids": ["new"], "payment_method_id": "credit_card_visible"}
        fact = store.facts_for("modify_pending_order_items", args)[-1]
        self.assertEqual("0.01", fact.value)
        self.assertNotEqual(float(fact.value), 100.01 - 100.0)
        self.assertEqual(100.0, fact.evidence.inputs[1].value["price"])
        self.assertEqual(100.01, fact.evidence.inputs[2].value["price"])

    def test_derived_native_argument_reference_cannot_float_to_new_payload(self):
        store = self.priced_airline()
        args = {"reservation_id": "SYN1", "total_baggages": 2, "nonfree_baggages": 1, "payment_id": "credit_card_visible"}
        facts = store.facts_for("update_reservation_baggages", args)
        changed = {**args, "nonfree_baggages": 2}
        with self.assertRaises(ValueError):
            build_intent("airline", "update_reservation_baggages", changed, intent_id="i", revision=1, facts=facts, registry=self.registry)
        built = build_intent("airline", "update_reservation_baggages", args, intent_id="i", revision=1, facts=facts, registry=self.registry)
        gate = StudyGate({("airline", "update_reservation_baggages"): lambda kwargs: json.dumps({"reservation_id": kwargs["reservation_id"], "total_baggages": kwargs["total_baggages"]})}, registry=self.registry)
        prompt = gate.propose(built.intent, prompt_event_id="p", prompt_sequence=10)
        gate.respond("yes", prompt_id=prompt.prompt_id, event_id="u", sequence=11, source="user")
        self.assertEqual("SUCCEEDED", gate.dispatch(built.intent).status)

    def test_native_booking_observation_can_anchor_later_baggage_facts(self):
        store = self.store("airline")
        args = {"user_id": "visible_user", "origin": "SFO", "destination": "JFK", "flight_type": "one_way", "cabin": "economy", "flights": [], "passengers": RESERVATION["passengers"], "payment_methods": [], "total_baggages": 0, "nonfree_baggages": 0, "insurance": "no"}
        store.record("booking", 1, {"name": "book_reservation", "arguments": args}, json.dumps({**RESERVATION, "reservation_id": "HATHAT"}))
        update = {"reservation_id": "HATHAT", "total_baggages": 1, "nonfree_baggages": 1, "payment_id": "credit_card_visible"}
        facts = store.facts_for("update_reservation_baggages", update)
        self.assertEqual(["user_id", "original_nonfree_baggages", "charge_amount"], [f.name for f in facts])
        self.assertEqual("50", facts[-1].value)

    def test_unrelated_failed_flight_search_does_not_poison_matching_price(self):
        store = self.priced_airline()
        store.record("unrelated-failure", 4, {"name": "search_direct_flight", "arguments": {"origin": "LAX", "destination": "SEA", "date": "2024-06-02"}}, "Error: unavailable")
        args = {"reservation_id": "SYN1", "cabin": "business", "flights": [{"flight_number": "SYN200", "date": "2024-06-02"}], "payment_id": "credit_card_visible"}
        self.assertEqual("50.03", store.facts_for("update_reservation_flights", args)[-1].value)

    def test_calculation_text_recorded_without_fabricated_price_binding(self):
        store = self.store()
        self.order(store)
        store.record("calc", 2, {"name": "calculate", "arguments": {"expression": "100 - 90"}}, "10.00")
        ref = ObservationRef("calc", "tool:retail/calculate", "$", "10.00", 2)
        self.assertEqual("10.00", store.resolve(ref))
        args = {"order_id": "#SYN1", "item_ids": ["0001"], "new_item_ids": ["0002"], "payment_method_id": "credit_card_visible"}
        self.assertEqual(["user_id"], [fact.name for fact in store.facts_for("modify_pending_order_items", args)])

    def test_latest_native_mutation_observation_updates_related_visible_facts(self):
        store = self.store()
        self.order(store)
        latest = {**ORDER, "user_id": "changed_visible_user", "address": {k: v for k, v in ADDRESS_ARGS.items() if k != "order_id"}}
        store.record("native-write", 2, {"name": "modify_pending_order_address", "arguments": ADDRESS_ARGS}, json.dumps(latest))
        fact = store.facts_for("modify_pending_order_address", ADDRESS_ARGS)[0]
        self.assertEqual("native-write", fact.evidence.event_id)
        self.assertEqual("changed_visible_user", fact.value)


if __name__ == "__main__":
    unittest.main()
