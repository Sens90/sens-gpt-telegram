"""Pure helper tests: no bot startup, credentials or external requests."""
import ast
from pathlib import Path
import re
import unittest
from urllib.parse import urlsplit, urlunsplit


source = ast.parse(Path(__file__).with_name('app.py').read_text())
helpers = ast.Module(body=[node for node in source.body
    if isinstance(node, ast.FunctionDef) and node.name in
    {'italian_planet_url', 'telegram_text_chunks', 'brawlplanet_map_urls',
     'brawlplanet_rotation_manifest', 'compact_source_text',
     'build_web_context', 'clean_brawlplanet_cell',
     'brawlplanet_page_labels', 'brawlplanet_table_rows',
     'brawlplanet_structured_stats', 'invalid_exhaustive_map_answer'}], type_ignores=[])
scope = {'re': re, 'urlsplit': urlsplit, 'urlunsplit': urlunsplit}
exec(compile(helpers, 'app.py', 'exec'), scope)


class LocalizedStatsTests(unittest.TestCase):
    def test_localized_paths_and_filters(self):
        localize = scope['italian_planet_url']
        self.assertEqual(localize('https://www.brawlplanet.com/en/maps/test?dataset=ranked'),
                         'https://www.brawlplanet.com/it/maps/test?dataset=ranked')
        self.assertEqual(localize('https://brawlplanet.com/it/maps/test'),
                         'https://www.brawlplanet.com/it/maps/test')
        self.assertIsNone(localize('https://brawlplanet.com.example.org/maps'))

    def test_long_stat_rows_are_not_lost(self):
        text = 'Ambra: vittorie 62,1%; utilizzo 5,6%\n' * 400
        chunks = list(scope['telegram_text_chunks'](text))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(0 < len(chunk) <= 3500 for chunk in chunks))
        self.assertEqual(''.join(chunks).replace('\n', ''), text.replace('\n', ''))

    def test_no_newline_and_empty(self):
        split = scope['telegram_text_chunks']
        self.assertEqual(list(split('')), [])
        self.assertEqual(''.join(split('x' * 9000)), 'x' * 9000)

    def test_map_links_and_manifest(self):
        result = {
            'url': 'https://www.brawlplanet.com/it/maps',
            'raw_content': (
                'https://www.brawlplanet.com/it/maps/grassknot_brawlball\n'
                'https://www.brawlplanet.com/it/maps/hotpotato_heist'
            )
        }
        urls = scope['brawlplanet_map_urls']([result])
        self.assertEqual(len(urls), 2)
        pages = [
            {'url': urls[0], 'title': 'Migliori Brawler per Campetto in erba - Footbrawl'},
            {'url': urls[1], 'raw_content': '# Battigia ustionante\n## Rapina'},
        ]
        manifest = scope['brawlplanet_rotation_manifest'](pages)
        self.assertEqual([(row['map'], row['mode']) for row in manifest], [
            ('Campetto in erba', 'Footbrawl'), ('Battigia ustionante', 'Rapina')
        ])

    def test_context_is_bounded_and_keeps_primary_and_secondary(self):
        results = [
            {
                'url': 'https://www.brawlplanet.com/it/maps/test',
                'title': 'Mappa primaria',
                'raw_content': 'INDIVIDUALI\n' + ('A' * 12000) +
                               '\nSQUADRE\n' + ('B' * 100),
            },
            {
                'url': 'https://brawlify.com/maps/test',
                'source_role': 'secondary_fallback',
                'title': 'Fallback',
                'raw_content': 'Dati secondari ' + ('C' * 9000),
            },
        ]
        context, sources = scope['build_web_context'](results, exhaustive=True)
        self.assertLessEqual(len(context), 220000)
        self.assertIn('INDIVIDUALI', context)
        self.assertIn('SQUADRE', context)
        self.assertIn('FONTE PRIMARIA BRAWL PLANET', context)
        self.assertIn('FONTE SECONDARIA', context)
        self.assertEqual(len(sources), 2)

    def test_structured_brawlplanet_tables_keep_both_datasets(self):
        page = {
            'url': 'https://www.brawlplanet.com/it/maps/test_brawlball',
            'title': 'Migliori Brawler per Campetto in erba | Footbrawl',
            'raw_content': (
                'Percentuali di vittoria su 100.000 partite a trofei.\n'
                '### Individuale\n'
                'Brawler | Vitt. | Scelta | Stella\n'
                '--- | --- | --- | ---\n'
                'Wendy | 67.3 | 5.6 | 16.1\n'
                '### Squadre\n'
                'Squadra | Vitt.\n'
                '--- | ---\n'
                'Ambra · Gus · Shade | 86.9\n'
                'Percentuali di vittoria su 20.000 partite in Classificata.\n'
                '### Individuale\n'
                'Brawler | Vitt. | Scelta | Stella\n'
                '--- | --- | --- | ---\n'
                'Ollie | 59.3 | 6.6 | 14.6\n'
                '### Squadre\n'
                'Squadra | Vitt.\n'
                '--- | ---\n'
                'Jacky · Bibi · Buster | 75.2\n'
            ),
        }
        structured = scope['brawlplanet_structured_stats']([page])
        self.assertIn('DATASET: Trofei', structured)
        self.assertIn('DATASET: Classificata', structured)
        self.assertIn('Wendy | 67.3 | 5.6 | 16.1', structured)
        self.assertIn('Ambra · Gus · Shade | 86.9', structured)
        self.assertIn('Jacky · Bibi · Buster | 75.2', structured)

    def test_incomplete_rotation_answer_is_rejected(self):
        validate = scope['invalid_exhaustive_map_answer']
        manifest = [
            {'map': 'Campetto', 'mode': 'Footbrawl'},
            {'map': 'Arabesque', 'mode': 'Brawl Hockey'},
        ]
        bad = 'Footbrawl: Campetto - miglior vittoria Wendy, più scelto Colt.'
        self.assertTrue(validate(bad, manifest, expected_context='both'))
        good = (
            'Trofei - Individuali: Brawler | Vitt. | Scelta | Stella\n'
            'Trofei - Squadre: Composizione | Vitt.\n'
            'Classificata - Individuali: Brawler | Vitt. | Scelta | Stella\n'
            'Classificata - Squadre: Non disponibile\n'
            'Campetto\nArabesque'
        )
        self.assertFalse(validate(good, manifest, expected_context='both'))


if __name__ == '__main__':
    unittest.main()
