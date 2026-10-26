"""Legacy namespaced payloads never gain v3 recovery privileges."""
import unittest
from evalgate.trace import validate_trace
from observability.project import operations, redact_event


class LegacyRestoreProjectionTests(unittest.TestCase):
    def test_legacy_restore_payload_is_opaque_and_never_a_business_claim(self):
        for version in ("trace-v1", "trace-v2"):
            for by in ("SENTINEL-SECRET", {}, 17):
                with self.subTest(version=version, by=by):
                    event = {"schema_version": version, "event_id": "ev-1", "sequence": 1,
                             "event_type": "serving.restore_observed", "session_id": "s", "run_id": "r", "trace_id": "t",
                             "operation_id": "op-1", "payload_hash": "a"*64, "occurred_at": "2026-10-25T00:00:00Z",
                             "source": {"component": "backend.service", "mapping_version": "test"},
                             "evidence_class": "serving_observation",
                             "payload": {"by": by, "snapshot_sha256": "SENTINEL-PRIVATE-PATH", "effect_id": "SENTINEL-TOKEN",
                                         "tool_name": "SENTINEL-TOOL", "outcome": "effect_confirmed"}}
                    finish = {**event, "event_id": "ev-2", "sequence": 2, "event_type": "serving.run_finished", "payload": {}}
                    self.assertEqual(validate_trace([event, finish]), [])  # Legacy namespaced contract remains unchanged.
                    view, count = redact_event(event)
                    self.assertNotIn("SENTINEL", str(view))
                    self.assertEqual(count, 5)
                    self.assertEqual(view["label"], "Other namespaced event (not a business event)")
                    op = operations([event, finish])["op-1"]
                    self.assertIsNone(op["tool_name"])
                    self.assertEqual(op["outcome"], "not_attempted")
                    self.assertFalse(op["history_incomplete"])


class RecoveryShapeTests(unittest.TestCase):
    def test_malformed_containers_are_invalid_not_exceptions(self):
        from evalgate.recovery import check_recovery
        for events, effects, expected in ((None, [], {}), ({}, [], {}), ([], None, {}), ([], [], None), ([], [], {})):
            with self.subTest(events=events, effects=effects, expected=expected):
                self.assertEqual(check_recovery(events, effects, expected)["verdict"], "INVALID")
