import unittest
import browser_access as access


class BrowserAccessTest(unittest.TestCase):
    def test_signed_grant_has_required_cookie_attributes(self):
        cookie = access.issue('test-secret', now=100)
        self.assertTrue(access.valid(cookie, 'test-secret', now=101))
        for attribute in ('Secure', 'HttpOnly', 'SameSite=Lax', 'Path=/', '__Host-'):
            self.assertIn(attribute, cookie)
        self.assertNotIn('test-secret', cookie)

    def test_modified_expired_or_rotated_grant_is_rejected(self):
        cookie = access.issue('test-secret', now=100)
        self.assertFalse(access.valid(cookie.replace(str(100 + access.LIFETIME), '99999999'), 'test-secret', now=101))
        self.assertFalse(access.valid(cookie, 'test-secret', now=100 + access.LIFETIME))
        self.assertFalse(access.valid(cookie, 'new-secret', now=101))

    def test_missing_or_malformed_cookie_is_rejected(self):
        for cookie in (None, '', 'other=value', access.COOKIE + '=invalid', access.COOKIE + '=999.a.b'):
            self.assertFalse(access.valid(cookie, 'test-secret', now=100))
