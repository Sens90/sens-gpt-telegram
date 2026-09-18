"""Regression checks against a public-source sample captured 2026-09-15.

Each source table keeps its first six rows, unmodified. Tests run without keys.
"""
import copy
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
import unittest

from live_maps import active_events, brawltrack_pro_map_stats, collect_report, localized, render_report, report_csv, valid_rows

NOW = datetime(2026, 9, 15, 15, 40, tzinfo=timezone.utc)
SAMPLE = json.loads((Path(__file__).parent / 'tests/fixtures/planet_live_sample.json').read_text())


class LiveMapTests(unittest.TestCase):
    def collect(self, data=None, dataset='both'):
        self.fallback_calls = []
        return collect_report(dataset, NOW, fetch=(data or SAMPLE).get,
                              secondary=lambda e: self.fallback_calls.append(e['event_map_id']) or 'Secondaria senza dati')

    def test_real_rotation_not_catalogue_and_exact_expiry(self):
        active = active_events(SAMPLE['event_rotation.json.gz'], NOW)
        self.assertEqual(len(active), 14)
        self.assertNotIn('backyardbowl_brawlball', [e['event_map_id'] for e in active])
        at_end = active_events(SAMPLE['event_rotation.json.gz'], NOW.replace(hour=16, minute=0))
        self.assertEqual(len(at_end), 12)
        invalid = copy.deepcopy(active[:1])
        invalid[0]['start_time'] = 'unknown'
        self.assertEqual(active_events(invalid, NOW), [])

    def test_partial_data_preserves_every_map_and_tries_secondary(self):
        report = self.collect()
        self.assertEqual(self.fallback_calls, ['bigbattlebasin_deathmatch5v5'])
        text = render_report(report)
        for event in report['events']:
            self.assertIn(localized(report['names'], 'maps', event['event_map']), text)
        self.assertIn('Star Player', text)
        self.assertIn('Squadre:', text)
        self.assertIn('Campetto sabbioso', text)
        self.assertIn('Secondaria senza dati', text)

    def test_dataset_boundaries_and_rows_are_not_reassigned(self):
        report = self.collect(dataset='ladder')
        self.assertTrue(all(d['label'] == 'Trofei' for m in report['maps'] for d in m['datasets']))
        sunny = next(m for m in report['maps'] if m['event']['event_map_id'] == 'sunnysoccer_brawlball')
        self.assertEqual(sunny['datasets'][0]['sections']['individual'][0],
                         {'brawler': 'COSMO', 'wr': 70.4, 'ur': 0.0, 'sr': 9.6})
        self.assertEqual(sunny['datasets'][0]['sections']['teams'][0],
                         {'team': ['AMBER', 'FANG', 'SHADE'], 'wr': 83.1})
        text = render_report(report)
        self.assertNotIn('Cosmo — Vittorie 70,4%', text)  # Below the declared pick threshold.
        self.assertIn('Cosmo', report_csv(report).decode('utf-8-sig'))

    def test_solo_duo_trio_and_all_metrics_survive_csv(self):
        report = self.collect(dataset='ladder')
        text = render_report(report)
        for section in ['Solo — individuali', 'Duo — squadre', 'Trio — squadre']:
            self.assertIn(section, text)
        rows = list(csv.DictReader(io.StringIO(report_csv(report).decode('utf-8-sig'))))
        self.assertIn('tm', {r['metrica'] for r in rows})
        self.assertIn('avg_rank', {r['metrica'] for r in rows})
        self.assertIn('sr', {r['metrica'] for r in rows})
        report['maps'][0]['datasets'][0]['sections']['individual'].append({'brawler': 'MR. P', 'wr': 60})
        rows = list(csv.DictReader(io.StringIO(report_csv(report).decode('utf-8-sig'))))
        self.assertIn('Mr. P', {r['brawler_o_squadra'] for r in rows})
        self.assertNotIn('Mister P', {r['brawler_o_squadra'] for r in rows})

    def test_bad_mode_payload_does_not_discard_other_maps(self):
        data = copy.deepcopy(SAMPLE)
        data['normal-results/brawlBall.json.gz'] = ['unexpected schema']
        report = self.collect(data)
        self.assertEqual(len(report['events']), 14)
        self.assertIn('Individuali:', render_report(report))
        self.assertIn('sunnysoccer_brawlball', self.fallback_calls)

    def test_unknown_rotation_never_uses_catalogue(self):
        data = copy.deepcopy(SAMPLE)
        data['event_rotation.json.gz'] = None
        report = self.collect(data)
        self.assertTrue(report['rotation_missing'])
        self.assertEqual(report['maps'], [])

    def test_duplicate_teams_and_invalid_numbers_are_rejected_locally(self):
        rows = valid_rows([
            {'team': ['COSMO', 'COSMO', 'COSMO'], 'wr': 90},
            {'brawler': 'COLT', 'wr': float('nan')},
            {'brawler': 'NITA', 'wr': 101},
            {'brawler': 'MR. P', 'wr': 60, 'ur': 110},
        ])
        self.assertEqual(rows, [{'brawler': 'MR. P', 'wr': 60, 'ur': 110}])


    def test_brawltrack_pro_live_smoke(self):
        """Opt-in runtime smoke test against a known BrawlTrack Pro map."""
        import os
        if os.getenv("BRAWLTRACK_LIVE_TEST") != "1":
            self.skipTest("set BRAWLTRACK_LIVE_TEST=1 for the live BrawlTrack smoke test")
        data = brawltrack_pro_map_stats("Hard Rock Mine", ttl=0)
        self.assertIsInstance(data, dict)
        self.assertEqual(data.get("scope"), "competitive_pro")
        self.assertEqual(data.get("map_id"), 15000007)
        self.assertTrue(data.get("priority_picks"), "BrawlTrack returned no priority picks")
        self.assertTrue(data.get("final_comps"), "BrawlTrack returned no final comps")
        for row in data["priority_picks"]:
            self.assertIn("brawler", row)
            self.assertGreaterEqual(row["use_rate"], 0)
            self.assertGreaterEqual(row["win_rate"], 0)


if __name__ == '__main__':
    unittest.main()
