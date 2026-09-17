"""smoke.py 的离线单元测试（F9、F11、A4、A5）。

fixture_dir 一律注入临时目录，绝不触碰 tests/fixtures/trending/——那是真实线上
快照的保留位，被测试污染后 online-smoke 会误判「fixture 已存在」而跳过引导采集。
"""

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import config  # noqa: E402
import net  # noqa: E402
import smoke  # noqa: E402

EXPECTED_FIXTURES = 21


def board_html(count=3, tag="a"):
    articles = []
    for index in range(count):
        articles.append(
            f"""
        <article class="Box-row" data-tag="{tag}">
          <h2 class="h3"><a href="/org{index}/repo{index}">org{index} / repo{index}</a></h2>
          <p class="col-9">Description for repo {index}</p>
          <div class="f6 color-fg-muted">
            <span itemprop="programmingLanguage">Go</span>
            <a href="/org{index}/repo{index}/stargazers">1,0{index}0</a>
            <a href="/org{index}/repo{index}/forks">5</a>
            <span class="d-inline-block float-sm-right">10 stars today</span>
          </div>
        </article>"""
        )
    return "<html><body>" + "".join(articles) + "</body></html>"


def transport_returning(html):
    """假传输层：接收 net.Request，返回固定 HTML。"""

    def transport(request):
        return html

    return transport


def silent_run(fixture_dir, transport, refresh=None, log=None):
    return smoke.run(
        transport=transport,
        sleep=lambda _: None,
        refresh=refresh,
        fixture_dir=fixture_dir,
        log=log or (lambda _: None),
    )


class BootstrapTest(unittest.TestCase):
    """首次运行：fixture 缺失，应写入并入库。"""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.fixture_dir = Path(self._tmp.name)
        self.html = board_html()

    def tearDown(self):
        self._tmp.cleanup()

    def test_bootstraps_every_board(self):
        exit_code, results = silent_run(self.fixture_dir, transport_returning(self.html))
        self.assertEqual(exit_code, 0)
        self.assertEqual(len(results), EXPECTED_FIXTURES)
        self.assertTrue(all(entry["action"] == "bootstrap" for entry in results))
        self.assertEqual(len(list(self.fixture_dir.rglob("*.html"))), EXPECTED_FIXTURES)

    def test_writes_raw_html_so_fixture_reparses_identically(self):
        silent_run(self.fixture_dir, transport_returning(self.html))
        written = (self.fixture_dir / "trending" / "daily_all.html").read_text(encoding="utf-8")
        self.assertEqual(written, self.html)

    def test_records_parsed_count_and_miss_rates(self):
        _, results = silent_run(self.fixture_dir, transport_returning(self.html))
        entry = results[0]
        self.assertEqual(entry["count"], 3)
        self.assertEqual(entry["miss_rates"]["name"], 0.0)


class FixtureFreezeTest(unittest.TestCase):
    """F9：fixture 已存在时只校验，绝不覆盖。"""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.fixture_dir = Path(self._tmp.name)
        silent_run(self.fixture_dir, transport_returning(board_html(tag="first")))

    def tearDown(self):
        self._tmp.cleanup()

    def test_second_run_only_verifies(self):
        exit_code, results = silent_run(
            self.fixture_dir, transport_returning(board_html(tag="second"))
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue(all(entry["action"] == "verify" for entry in results))
        self.assertTrue(all(entry["existed"] for entry in results))

    def test_frozen_content_is_not_replaced(self):
        """线上内容变了，但 fixture 必须保持首次引导时的内容。"""
        before = (self.fixture_dir / "trending" / "daily_all.html").read_text(encoding="utf-8")
        silent_run(self.fixture_dir, transport_returning(board_html(tag="second")))
        after = (self.fixture_dir / "trending" / "daily_all.html").read_text(encoding="utf-8")
        self.assertEqual(before, after)
        self.assertIn('data-tag="first"', after)

    def test_refresh_overwrites_when_explicitly_requested(self):
        """F11：只有显式 refresh 才覆盖。"""
        _, results = silent_run(
            self.fixture_dir, transport_returning(board_html(tag="second")), refresh=True
        )
        self.assertTrue(all(entry["action"] == "refresh" for entry in results))
        after = (self.fixture_dir / "trending" / "daily_all.html").read_text(encoding="utf-8")
        self.assertIn('data-tag="second"', after)


class InvariantFailureTest(unittest.TestCase):
    """A4／A5：上游改版必须让 smoke 失败，且不得冻结坏快照。"""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.fixture_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_zero_results_fails_and_writes_nothing(self):
        html = '<html><body><div class="ProjectCard">renamed container</div></body></html>'
        exit_code, results = silent_run(self.fixture_dir, transport_returning(html))
        self.assertEqual(exit_code, 1)
        self.assertTrue(all(entry["action"] == "write-skipped" for entry in results))
        self.assertEqual(list(self.fixture_dir.rglob("*.html")), [])

    def test_broken_language_selector_is_detected(self):
        """条数仍为 3，但 language 全部缺失——只有缺失率能发现这种漂移。"""
        html = board_html().replace('itemprop="programmingLanguage"', 'data-x="moved"')
        exit_code, results = silent_run(self.fixture_dir, transport_returning(html))
        self.assertEqual(exit_code, 1)
        self.assertEqual(results[0]["count"], 3)
        self.assertEqual(results[0]["miss_rates"]["language"], 1.0)
        self.assertEqual(list(self.fixture_dir.rglob("*.html")), [])

    def test_verify_mode_also_flags_drift(self):
        """fixture 已冻结后，线上漂移仍必须报错而非静默通过。"""
        silent_run(self.fixture_dir, transport_returning(board_html()))
        drifted = board_html().replace('itemprop="programmingLanguage"', 'data-x="moved"')
        exit_code, results = silent_run(self.fixture_dir, transport_returning(drifted))
        self.assertEqual(exit_code, 1)
        self.assertTrue(all(entry["action"] == "verify" for entry in results))


class FetchFailureTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.fixture_dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_network_failure_fails_run_without_writing(self):
        def transport(request):
            raise net.RetryableError("HTTP 503")

        exit_code, results = silent_run(self.fixture_dir, transport)
        self.assertEqual(exit_code, 1)
        self.assertTrue(all(entry["action"] == "fetch-failed" for entry in results))
        self.assertEqual(list(self.fixture_dir.rglob("*.html")), [])


class SummaryTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.fixture_dir = Path(self._tmp.name) / "fixtures"
        self.summary_path = Path(self._tmp.name) / "summary.md"

    def tearDown(self):
        self._tmp.cleanup()

    def test_appends_diagnostics_to_step_summary(self):
        with mock.patch.dict(os.environ, {config.ENV_STEP_SUMMARY: str(self.summary_path)}):
            silent_run(self.fixture_dir, transport_returning(board_html()))
        text = self.summary_path.read_text(encoding="utf-8")
        self.assertIn("online-smoke", text)
        self.assertIn("daily/all", text)
        self.assertIn("引导/校验", text)

    def test_survives_missing_summary_env(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            exit_code, _ = silent_run(self.fixture_dir, transport_returning(board_html()))
        self.assertEqual(exit_code, 0)

    def test_failure_detail_is_included(self):
        html = '<html><body><div class="ProjectCard">x</div></body></html>'
        with mock.patch.dict(os.environ, {config.ENV_STEP_SUMMARY: str(self.summary_path)}):
            silent_run(self.fixture_dir, transport_returning(html))
        text = self.summary_path.read_text(encoding="utf-8")
        self.assertIn("失败明细", text)
        self.assertIn("解析结果为 0 条", text)


class BoardLabelTest(unittest.TestCase):
    def test_all_languages_label(self):
        self.assertEqual(smoke.board_label("daily", ""), "daily/all")

    def test_language_label(self):
        self.assertEqual(smoke.board_label("monthly", "rust"), "monthly/rust")


if __name__ == "__main__":
    unittest.main()
