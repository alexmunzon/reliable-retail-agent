"""Exposed post-build regression probes; original fixtures and oracles remain unchanged."""
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from observability import costs, ingest, project, sidecar
from observability.report import Dataset

FIXTURE = Path(__file__).resolve().parents[1] / 'observability' / 'fixtures' / 'v1'


class OptionalPricingFailures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = Dataset(FIXTURE)

    def business(self, dataset):
        return {key: (r['status'], r['operations'], r['events'], r['evidence'])
                for key, r in dataset.requests.items()}

    def test_optional_pricing_corruption_preserves_business_evidence_without_stale_prices(self):
        # Damage the newer table: dropping it alone would silently reuse older rates.
        for scenario, content in [('missing', None), ('malformed', b'{'), ('wrong_shape', b'[]'),
                                  ('invalid_utf8', b'\xff'), ('deep_nesting', b'[' * 2000 + b']' * 2000)]:
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'fixture'
                shutil.copytree(FIXTURE, root)
                target = root / 'pricing/synthetic-2026-11-01.json'
                if content is None:
                    target.unlink()
                else:
                    target.write_bytes(content)
                dataset = Dataset(root)
                self.assertEqual(self.business(dataset), self.business(self.baseline))
                self.assertEqual(len(dataset.visible('tenant-a')), 53)
                self.assertIn({'case': None, 'code': 'pricing_set_incomplete'}, dataset.problems)
                self.assertEqual(dataset.tables, [])
                for key, row in dataset.requests.items():
                    self.assertIsNone(row['costs']['estimated_charge']['total'])
                    self.assertEqual(row['costs']['estimated_charge']['known_subtotal'], {})
                    self.assertEqual(row['costs']['confirmed_charge'], self.baseline.requests[key]['costs']['confirmed_charge'])
                    self.assertEqual(row['costs']['measured_tokens'], self.baseline.requests[key]['costs']['measured_tokens'])
                self.assertNotIn(str(target), json.dumps(dataset.meta('tenant-a')))


class CalendarDateFailures(unittest.TestCase):
    def setUp(self):
        self.records = [json.loads(line) for line in (FIXTURE / 'sidecar/healthy.jsonl').read_text().splitlines()]
        self.usage = next(r for r in self.records if r['kind'] == 'usage')
        self.table = json.loads((FIXTURE / 'pricing/synthetic-2026-01-01.json').read_text())

    def test_impossible_usage_dates_are_rejected_and_never_priced(self):
        for value in ['2026-02-30T12:00:00Z', '2026-99-01T00:00:00Z', '2026-01-01T24:00:00Z',
                      '2026-01-01T23:60:00Z', '2026-01-01T23:59:60Z', '0000-01-01T00:00:00Z']:
            with self.subTest(value=value):
                usage = {**self.usage, 'observed_at': value}
                self.assertIn('usage.invalid_observed_at', sidecar.validate_record(usage))
                self.assertIsNone(costs.estimate(usage, [self.table])['amount'])
                self.assertEqual(costs.estimate(usage, [self.table])['reason'], 'invalid_usage_time')

    def test_bad_date_is_quarantined_with_unknown_total_not_zero(self):
        events = [json.loads(line) for line in (FIXTURE / 'traces/healthy.jsonl').read_text().splitlines()]
        streams, _ = ingest.build_streams('healthy', '0' * 64, list(enumerate(events, 1)), {})
        records = copy.deepcopy(self.records)
        next(r for r in records if r['kind'] == 'usage')['observed_at'] = '2026-99-01T00:00:00Z'
        ingest.attach_sidecar(streams, list(enumerate(records, 1)), {}, ingest.Limits())
        row = project.project(streams[0], [self.table], 'synthetic_fixture', ingest.Limits())
        self.assertEqual(row['status'], 'completed')
        self.assertIsNone(row['costs']['estimated_charge']['total'])
        self.assertEqual(row['costs']['usage'], [])
        self.assertIn('usage.invalid_observed_at', row['costs']['incomplete_evidence_reasons'])

    def test_utc_leap_day_and_nanosecond_usage_remain_valid(self):
        for value in ['2024-02-29T23:59:59.123456789Z', '2028-02-29T00:00:00Z', '2026-01-01T00:00:00Z']:
            self.assertEqual(sidecar.validate_record({**self.usage, 'observed_at': value}), [])

    def test_pricing_effective_dates_must_be_calendar_days(self):
        for value in ['2026-02-29', '2026-02-30', '2026-99-01', '0000-01-01']:
            self.assertIn('pricing.invalid_effective_from', costs.validate_table({**self.table, 'effective_from': value}))
        self.assertEqual(costs.validate_table({**self.table, 'effective_from': '2024-02-29'}), [])
