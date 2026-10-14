"""Recorded native user assent preserves exact cancellation reason provenance."""
import json
import unittest

from study import build_intent
from study.visible import VisibleStore

FULL = 'Yes, I approve this entire operation and these exact details. Thank you!'
RESERVATION = dict(reservation_id='SYN', user_id='synthetic',
    flights=[dict(flight_number='SYN001', date='2024-06-01', price=100)],
    passengers=[dict(first_name='Jo', last_name='Li', dob='1990-01-01')],
    payment_history=[dict(payment_id='card', amount=100)])


class RecordedAssentTests(unittest.TestCase):
    def store(self):
        store = VisibleStore('airline')
        store.record_user('reason', 1, 'change of plan')
        store.record('reservation', 2, dict(name='get_reservation_details', arguments=dict(reservation_id='SYN')),
                     json.dumps(RESERVATION))
        return store

    def build(self, store):
        facts = store.facts_for('cancel_reservation', dict(reservation_id='SYN'))
        return build_intent('airline', 'cancel_reservation', dict(reservation_id='SYN'),
                            intent_id='cancel', revision=1, facts=facts), facts

    def test_polite_native_assent_with_terminal_marker_preserves_original_ref(self):
        for text in [FULL, FULL + '###STOP###']:
            store = self.store(); before, facts = self.build(store)
            self.assertEqual('PREPARED', before.status)
            original = next(f for f in facts if f.name == 'cancellation_reason')
            store.record_user('confirmation', 3, text)
            after, facts = self.build(store)
            self.assertEqual(before.intent.fingerprint, after.intent.fingerprint)
            reason = next(f for f in facts if f.name == 'cancellation_reason')
            self.assertEqual(original.evidence.to_dict(), reason.evidence.to_dict())
            self.assertEqual('reason', reason.evidence.event_id)
            self.assertEqual('change of plan', store.resolve(reason.evidence))

    def test_exact_changed_reason_has_new_provenance_and_invalidates_old_intent(self):
        store = self.store(); before, _ = self.build(store)
        store.record_user('changed-reason', 3, 'other reasons')
        after, facts = self.build(store)
        reason = next(f for f in facts if f.name == 'cancellation_reason')
        self.assertEqual('changed-reason', reason.evidence.event_id)
        self.assertEqual('other reasons', reason.value)
        self.assertNotEqual(before.intent.fingerprint, after.intent.fingerprint)

    def test_conditional_changed_quoted_or_repeated_marker_reply_needs_details(self):
        for text in [FULL + ' But the reason is other reasons.', 'Yes if free.###STOP###',
                     '"' + FULL + '"###STOP###', FULL + '###STOP######STOP###',
                     'Actually, airline cancelled flight.']:
            store = self.store(); store.record_user('correction', 3, text)
            built, facts = self.build(store)
            self.assertEqual('NEED_DETAILS', built.status)
            self.assertIn('cancellation_reason', built.missing_fields)
            self.assertNotIn('cancellation_reason', [f.name for f in facts])


if __name__ == '__main__': unittest.main()
