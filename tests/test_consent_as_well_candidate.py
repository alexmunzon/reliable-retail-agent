"""Offline-only regression; never changes frozen runtime or paid outcomes."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from study.consent_as_well_candidate import classify_response
from study.consent_candidate import classify_response as classify_v4
from study.gate_v4 import StudyGate
from study.intents import build_intent

OBSERVED = "Yes, I approve these details as well."
ARGS = dict(user_id="synthetic", address1="18 Example Lane", address2="", city="Example",
            state="CA", country="USA", zip="00123")


class AdditiveAssentCandidateTests(unittest.TestCase):
    def intent(self, revision=1, **changes):
        return build_intent("retail", "modify_user_address", {**ARGS, **changes},
                            intent_id="synthetic-operation", revision=revision).intent

    def test_observed_miss_and_narrow_variations(self):
        self.assertEqual("AMBIGUOUS", classify_v4(OBSERVED))
        for text in [OBSERVED, "I approve these exact details as well!",
                     "YES, I APPROVE THE DETAILS AS WELL. Thanks!",
                     "  Yes, I approve these details as well. Thank you!  "]:
            with self.subTest(text=text):
                self.assertEqual("AFFIRM", classify_response(text))

    def test_qualifiers_changes_quotes_and_prior_consent_stay_blocked(self):
        qualifiers = ["if free", "unless the price changes", "but use ZIP 99999",
                      "except insurance", "only the first passenger", "not payment",
                      "maybe", "subject to review", "after you change the date",
                      "and book another flight", "and no", "for example"]
        for qualifier in qualifiers:
            for text in [f"{qualifier}, {OBSERVED}", f"{OBSERVED} {qualifier}.",
                         f"{OBSERVED[:-1]}, {qualifier}."]:
                with self.subTest(text=text):
                    self.assertNotEqual("AFFIRM", classify_response(text))
        for text in ['"' + OBSERVED + '"', "The customer said: " + OBSERVED,
                     "I approved these details as well.", "I approved it earlier.",
                     "Yes, I approve these details as well?", "Yes, as well.",
                     "No. " + OBSERVED, OBSERVED + " Use gift_card_2.",
                     OBSERVED + " Refund 999.", OBSERVED + " Change the reason."]:
            with self.subTest(text=text):
                self.assertNotEqual("AFFIRM", classify_response(text))

    def test_terminal_and_type_boundaries(self):
        for marker_form in ["###STOP###", OBSERVED + "###STOP###",
                            "###STOP###" + OBSERVED, OBSERVED + "###STOP######STOP###"]:
            for protocol_option in [False, True]:
                self.assertNotEqual("AFFIRM", classify_response(marker_form,
                                                                allow_terminal_marker=protocol_option))
        for value in [None, 1, {}, []]:
            with self.assertRaises(TypeError):
                classify_response(value)
        with self.assertRaises(TypeError):
            classify_response(OBSERVED, allow_terminal_marker="true")

    def test_fresh_complete_payload_dispatches_once_in_test_only_injection(self):
        calls = []
        def tool(args):
            calls.append(args)
            return json.dumps(dict(user_id=args["user_id"],
                                   address={k: v for k, v in args.items() if k != "user_id"}))
        gate = StudyGate({("retail", "modify_user_address"): tool})
        intent = self.intent()
        prompt = gate.propose(intent, prompt_event_id="prompt", prompt_sequence=10)
        with patch("study.gate_v4.classify_response", classify_response):
            gate.respond(OBSERVED, prompt_id=prompt.prompt_id, event_id="reply", sequence=11, source="user")
            self.assertTrue(gate.dispatch(intent).dispatched)
            self.assertFalse(gate.dispatch(intent).dispatched)
        self.assertEqual([ARGS], calls)

    def test_recognition_does_not_bypass_freshness_or_changed_payload(self):
        for bad in [dict(prompt_id="old"), dict(sequence=12), dict(source="tool")]:
            gate = StudyGate({("retail", "modify_user_address"): lambda args: self.fail("No dispatch")})
            intent = self.intent()
            prompt = gate.propose(intent, prompt_event_id="prompt", prompt_sequence=10)
            kwargs = dict(prompt_id=prompt.prompt_id, event_id="reply", sequence=11, source="user")
            kwargs.update(bad)
            with patch("study.gate_v4.classify_response", classify_response):
                self.assertEqual("STALE_RESPONSE", gate.respond(OBSERVED, **kwargs).status)
                self.assertFalse(gate.dispatch(intent).dispatched)
        gate = StudyGate({("retail", "modify_user_address"): lambda args: self.fail("No dispatch")})
        prompt = gate.propose(self.intent(), prompt_event_id="prompt", prompt_sequence=10)
        with patch("study.gate_v4.classify_response", classify_response):
            gate.respond(OBSERVED, prompt_id=prompt.prompt_id, event_id="reply", sequence=11, source="user")
            self.assertEqual("INVALIDATED", gate.dispatch(self.intent(2, zip="99999")).status)

    def test_frozen_runtime_imports_and_existing_outcomes_preserved(self):
        root = Path(__file__).resolve().parents[1]
        # Current development may adopt the candidate; frozen v4 must not.
        for name in ["study/gate_v4.py", "study/visible_v4.py", "study/controller_v4.py"]:
            self.assertNotIn("consent_as_well_candidate", (root / name).read_text())
        for text in ["yes", "no", "Yes, please approve the operation.", "Maybe.",
                     "Yes, but change the amount."]:
            self.assertEqual(classify_v4(text), classify_response(text))


if __name__ == "__main__":
    unittest.main()
