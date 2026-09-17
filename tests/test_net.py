"""net.py 的离线单元测试（E18：重试与退避无需真实网络即可覆盖）。

假传输层接收一个 Request 具名元组，因此能在断言里直接看到方法、URL、请求头与请求体，
不必为了取到这些信息而让签名越长越长。
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import config  # noqa: E402
import net  # noqa: E402


def recording(result="<html/>"):
    """记录收到的请求并返回固定响应的假传输层。"""
    seen = []

    def transport(request):
        seen.append(request)
        return result

    return seen, transport


class TransportContractTest(unittest.TestCase):
    def test_request_is_a_named_tuple_with_method_url_headers_body_timeout(self):
        fields = net.Request._fields
        self.assertEqual(fields, ("method", "url", "headers", "body", "timeout"))

    def test_get_sets_user_agent_and_no_body(self):
        seen, transport = recording()
        net.get("https://example.test/x", transport=transport)
        request = seen[0]
        self.assertEqual(request.method, "GET")
        self.assertIsNone(request.body)
        self.assertIn("User-Agent", request.headers)

    def test_post_json_serializes_body_and_sets_content_type(self):
        seen, transport = recording("{}")
        net.post_json("https://example.test/x", {"a": "中"}, transport=transport)
        request = seen[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers["Content-Type"], "application/json")
        self.assertEqual(json.loads(request.body), {"a": "中"})
        self.assertIn("中", request.body, "应保留非 ASCII 原文而非转义")


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

        def transport(request):
            attempts.append(request.url)
            if len(attempts) < 3:
                raise net.RetryableError("瞬时故障")
            return "<html/>"

        slept = []
        text = net.get("https://example.test/x", transport=transport, retries=2, sleep=slept.append)
        self.assertEqual(text, "<html/>")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(slept, [1.0, 2.0], "退避应为 backoff**attempt")

    def test_exhausted_retries_raise(self):
        def transport(request):
            raise net.RetryableError("一直失败")

        with self.assertRaises(net.FetchError) as caught:
            net.get("https://example.test/x", transport=transport, retries=2, sleep=lambda _: None)
        self.assertIn("3 次", str(caught.exception))

    def test_non_retryable_fails_fast_without_sleeping(self):
        slept = []

        def transport(request):
            raise ValueError("不该重试的错误")

        with self.assertRaises(net.FetchError):
            net.get("https://example.test/x", transport=transport, retries=2, sleep=slept.append)
        self.assertEqual(slept, [], "不可重试的错误不得消耗退避")

    def test_default_retry_count_comes_from_config(self):
        attempts = []

        def transport(request):
            attempts.append(request.url)
            raise net.RetryableError("x")

        with self.assertRaises(net.FetchError):
            net.get("https://example.test/x", transport=transport, sleep=lambda _: None)
        self.assertEqual(len(attempts), config.HTTP_RETRIES + 1)

    def test_timeout_is_forwarded(self):
        seen, transport = recording()
        net.get("https://example.test/x", transport=transport, timeout=7)
        self.assertEqual(seen[0].timeout, 7)


class NotFoundTest(unittest.TestCase):
    """404 对榜单是故障，对 README 是正常答复——同一状态码两种语义。"""

    def transport_404(self):
        def transport(request):
            raise net.NotFoundError(request.url)

        return transport

    def test_404_is_an_error_by_default(self):
        with self.assertRaises(net.FetchError) as caught:
            net.get("https://example.test/x", transport=self.transport_404())
        self.assertIn("404", str(caught.exception))

    def test_404_returns_none_when_missing_is_acceptable(self):
        self.assertIsNone(
            net.get("https://example.test/x", transport=self.transport_404(), missing_ok=True)
        )

    def test_404_is_not_retried(self):
        calls = []

        def transport(request):
            calls.append(request.url)
            raise net.NotFoundError(request.url)

        net.get("https://example.test/x", transport=transport, missing_ok=True, sleep=lambda _: None)
        self.assertEqual(len(calls), 1, "404 是确定答复，重试只会浪费配额")


class ReadmeTest(unittest.TestCase):
    def test_url_shape(self):
        self.assertEqual(
            net.readme_url("acme", "widget"),
            "https://raw.githubusercontent.com/acme/widget/HEAD/README.md",
        )

    def test_first_candidate_wins(self):
        seen, transport = recording("# 标题")
        text = net.fetch_readme("acme", "widget", transport=transport)
        self.assertEqual(text, "# 标题")
        self.assertEqual(len(seen), 1, "首个候选命中就不该继续请求")
        self.assertTrue(seen[0].url.endswith("/README.md"))

    def test_falls_through_to_later_candidates(self):
        seen = []

        def transport(request):
            seen.append(request)
            if request.url.endswith("/README.md"):
                raise net.NotFoundError(request.url)
            return "# 备用标题"

        text = net.fetch_readme("acme", "widget", transport=transport)
        self.assertEqual(text, "# 备用标题")
        self.assertEqual(len(seen), 2)
        self.assertTrue(seen[1].url.endswith("/readme.md"))

    def test_all_candidates_missing_returns_none(self):
        def transport(request):
            raise net.NotFoundError(request.url)

        self.assertIsNone(net.fetch_readme("acme", "widget", transport=transport))

    def test_empty_file_is_treated_as_missing(self):
        def transport(request):
            if request.url.endswith("/README.md"):
                return "   \n  "
            return "# 有内容"

        self.assertEqual(net.fetch_readme("acme", "widget", transport=transport), "# 有内容")

    def test_result_is_truncated(self):
        long_readme = "X" * (config.README_MAX_CHARS + 500)
        _, transport = recording(long_readme)
        text = net.fetch_readme("acme", "widget", transport=transport)
        self.assertEqual(len(text), config.README_MAX_CHARS)


class DeepSeekPayloadTest(unittest.TestCase):
    def test_payload_requests_json_output(self):
        payload = net.deepseek_payload([{"role": "user", "content": "x"}])
        self.assertEqual(payload["model"], config.DEEPSEEK_MODEL)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["temperature"], config.DEEPSEEK_TEMPERATURE)
        self.assertEqual(payload["max_tokens"], config.DEEPSEEK_MAX_TOKENS)
        self.assertFalse(payload["stream"])

    def test_model_can_be_overridden(self):
        self.assertEqual(net.deepseek_payload([], model="other")["model"], "other")

    def test_url_is_well_formed(self):
        self.assertEqual(net.deepseek_url(), "https://api.deepseek.com/chat/completions")


class AssistantContentTest(unittest.TestCase):
    def envelope(self, content):
        return {"choices": [{"message": {"role": "assistant", "content": content}}]}

    def test_extracts_content(self):
        self.assertEqual(net.extract_assistant_content(self.envelope('{"a":1}')), '{"a":1}')

    def test_rejects_unexpected_shapes(self):
        for bad in (
            "字符串",
            None,
            [],
            {},
            {"choices": []},
            {"choices": "不是列表"},
            {"choices": [{}]},
            {"choices": [{"message": {}}]},
            {"choices": [{"message": {"content": ""}}]},
            {"choices": [{"message": {"content": "   "}}]},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(net.TransportError):
                    net.extract_assistant_content(bad)


class CallDeepSeekTest(unittest.TestCase):
    def envelope_text(self, content='{"ok":true}'):
        return json.dumps(self.envelope(content))

    def envelope(self, content):
        return {"choices": [{"message": {"content": content}}]}

    def test_requires_api_key(self):
        with self.assertRaises(net.FetchError) as caught:
            net.call_deepseek([{"role": "user", "content": "x"}], "")
        self.assertIn("API Key", str(caught.exception))

    def test_sends_bearer_authorization(self):
        seen, transport = recording(self.envelope_text())
        net.call_deepseek([{"role": "user", "content": "x"}], "sk-test", transport=transport)
        self.assertEqual(seen[0].headers["Authorization"], "Bearer sk-test")
        self.assertEqual(seen[0].method, "POST")

    def test_returns_content(self):
        _, transport = recording(self.envelope_text('{"one_liner":"x"}'))
        self.assertEqual(
            net.call_deepseek([{"role": "user", "content": "x"}], "k", transport=transport),
            '{"one_liner":"x"}',
        )

    def test_unparseable_envelope_is_a_transport_error(self):
        _, transport = recording("这不是 JSON")
        with self.assertRaises(net.TransportError):
            net.call_deepseek([{"role": "user", "content": "x"}], "k", transport=transport)

    def test_transient_failure_is_retried(self):
        attempts = []

        def transport(request):
            attempts.append(request.url)
            if len(attempts) == 1:
                raise net.RetryableError("HTTP 503")
            return self.envelope_text()

        net.call_deepseek(
            [{"role": "user", "content": "x"}], "k", transport=transport, sleep=lambda _: None
        )
        self.assertEqual(len(attempts), 2)


if __name__ == "__main__":
    unittest.main()
