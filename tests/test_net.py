"""net.py 的离线单元测试（E18：重试与退避无需真实网络即可覆盖）。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import config  # noqa: E402
import net  # noqa: E402


class TrendingUrlTest(unittest.TestCase):
    def test_all_languages_has_no_extra_slash(self):
        self.assertEqual(net.trending_url("daily", ""), "https://github.com/trending?since=daily")

    def test_language_board(self):
        self.assertEqual(
            net.trending_url("weekly", "go"), "https://github.com/trending/go?since=weekly"
        )

    def test_covers_all_twenty_one_boards(self):
        urls = {
            net.trending_url(window, language)
            for window in config.TIME_WINDOWS
            for language in config.LANGUAGES
        }
        self.assertEqual(len(urls), 21)
        self.assertFalse(any("//?" in url for url in urls))


class RetryTest(unittest.TestCase):
    """E18：以假传输层驱动，不触网、不真睡。"""

    def test_retries_then_succeeds(self):
        attempts = []

        def transport(url, headers, timeout):
            attempts.append(url)
            if len(attempts) < 3:
                raise net.RetryableError("瞬时故障")
            return "<html/>"

        slept = []
        text = net.fetch_text(
            "https://example.test/x", transport=transport, retries=2, sleep=slept.append
        )
        self.assertEqual(text, "<html/>")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(slept, [1.0, 2.0], "退避应为 backoff**attempt")

    def test_exhausted_retries_raise(self):
        def transport(url, headers, timeout):
            raise net.RetryableError("一直失败")

        with self.assertRaises(net.FetchError) as caught:
            net.fetch_text("https://example.test/x", transport=transport, retries=2, sleep=lambda _: None)
        self.assertIn("3 次", str(caught.exception))

    def test_non_retryable_fails_fast_without_sleeping(self):
        slept = []

        def transport(url, headers, timeout):
            raise ValueError("不该重试的错误")

        with self.assertRaises(net.FetchError):
            net.fetch_text(
                "https://example.test/x", transport=transport, retries=2, sleep=slept.append
            )
        self.assertEqual(slept, [], "不可重试的错误不得消耗退避")

    def test_default_retry_count_comes_from_config(self):
        attempts = []

        def transport(url, headers, timeout):
            attempts.append(url)
            raise net.RetryableError("x")

        with self.assertRaises(net.FetchError):
            net.fetch_text("https://example.test/x", transport=transport, sleep=lambda _: None)
        self.assertEqual(len(attempts), config.HTTP_RETRIES + 1)

    def test_headers_and_timeout_are_forwarded(self):
        seen = {}

        def transport(url, headers, timeout):
            seen["headers"] = headers
            seen["timeout"] = timeout
            return "ok"

        net.fetch_text(
            "https://example.test/x",
            transport=transport,
            headers={"User-Agent": "test-agent"},
            timeout=7,
        )
        self.assertEqual(seen["headers"], {"User-Agent": "test-agent"})
        self.assertEqual(seen["timeout"], 7)


class TrendingFetchTest(unittest.TestCase):
    def test_uses_trending_url(self):
        seen = []

        def transport(url, headers, timeout):
            seen.append(url)
            return "<html/>"

        net.fetch_trending_html("monthly", "rust", transport=transport)
        self.assertEqual(seen, ["https://github.com/trending/rust?since=monthly"])


if __name__ == "__main__":
    unittest.main()
