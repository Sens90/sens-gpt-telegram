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
     'brawlplanet_rotation_manifest'}], type_ignores=[])
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


if __name__ == '__main__':
    unittest.main()
