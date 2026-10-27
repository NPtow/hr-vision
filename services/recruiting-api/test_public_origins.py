import unittest

from browser_access import allowed_origins


class PublicOriginsTest(unittest.TestCase):
    def test_rejects_non_origin_values(self):
        for value in ('*', 'https://*.example.test', 'https://example.test/path',
                      'https://user:pass@example.test', 'https://example.test?query',
                      'https://example.test#fragment', 'file:///tmp/site', ''):
            with self.subTest(value=value), self.assertRaises(ValueError):
                allowed_origins('https://app.example.test', [value])

    def test_exact_origins_only(self):
        origins = allowed_origins('https://app.example.test', ['https://old.example.test'])
        self.assertIn('https://old.example.test', origins)
        self.assertNotIn('https://old.example.test.attacker.test', origins)
