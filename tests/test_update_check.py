# -*- coding: utf-8 -*-
"""update_check 的离线单元测试：全部走桩，不访问网络。"""

import io
import unittest

import update_check as u


class FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes = b""):
        super().__init__(payload)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class RedirectResponse(FakeResponse):
    def __init__(self, url: str):
        super().__init__()
        self._url = url

    def geturl(self):
        return self._url


class VersionRules(unittest.TestCase):
    def test_parse_version(self):
        self.assertEqual(u.parse_version("v1.2.3"), (1, 2, 3))
        self.assertEqual(u.parse_version("1.2.3-rc1"), (1, 2, 3))
        self.assertEqual(u.parse_version(" 2.0 "), (2,))
        self.assertEqual(u.parse_version(""), ())
        self.assertEqual(u.parse_version("nightly"), ())

    def test_is_newer(self):
        self.assertTrue(u.is_newer("v1.2.0", "1.1.9"))
        self.assertTrue(u.is_newer("1.0.1", "1.0.0"))
        self.assertTrue(u.is_newer("2.0.0", "1.9.9"))
        self.assertFalse(u.is_newer("v1.0.0", "1.0.0"))
        self.assertFalse(u.is_newer("1.0.0", "1.0.1"))
        self.assertFalse(u.is_newer("garbage", "1.0.0"))


class FetchBehaviour(unittest.TestCase):
    def setUp(self):
        self._urlopen = u.urllib.request.urlopen

    def tearDown(self):
        u.urllib.request.urlopen = self._urlopen

    def test_release_payload(self):
        body = b'{"tag_name": "v2.0.0", "html_url": "https://example/v2.0.0", "body": "notes"}'
        u.urllib.request.urlopen = lambda *a, **k: FakeResponse(body)
        info = u.fetch_latest()
        self.assertEqual(info["version"], "2.0.0")
        self.assertEqual(info["notes"], "notes")
        self.assertEqual(u.check("1.0.0")["status"], "update")
        self.assertEqual(u.check("2.0.0")["status"], "latest")
        self.assertEqual(u.check("2.0.1")["status"], "latest")

    def test_bad_payload_degrades_to_error(self):
        u.urllib.request.urlopen = lambda *a, **k: FakeResponse(b"not json")
        result = u.check("1.0.0")
        self.assertEqual(result["status"], "error")
        self.assertIn("JSON", result["message"])

    def test_rate_limit_falls_back_to_redirect(self):
        tag_url = f"https://github.com/{u.REPOSITORY}/releases/tag/v3.1.0"
        seen = []

        def fake_urlopen(request, timeout=None):
            seen.append(request.full_url)
            if request.full_url == u.API_LATEST:
                raise u.urllib.error.HTTPError(u.API_LATEST, 403, "rate limited", {}, None)
            return RedirectResponse(tag_url)

        u.urllib.request.urlopen = fake_urlopen
        result = u.check("3.0.0")
        self.assertEqual(result["status"], "update")
        self.assertEqual(result["version"], "3.1.0")
        self.assertEqual(result["url"], tag_url)
        self.assertEqual(u.check("3.1.0")["status"], "latest")
        self.assertEqual(len(seen), 4)


if __name__ == "__main__":
    unittest.main()
