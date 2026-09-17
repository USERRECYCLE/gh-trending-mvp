"""fetch.py 的离线单元测试（A 组中不依赖 fixture 的部分）。

合成 HTML 刻意内联在本文件，不放进 tests/fixtures/trending/——那个目录是真实
线上快照的保留位。若在此放一个手写 HTML，online-smoke 会误判「fixture 已存在」
而跳过引导采集，漂移检测的前提就没了。

注意：合成样本验证的是解析器的接线正确性，不能证明选择器对得上真实 GitHub DOM。
后者只能由 online-smoke 的首次引导采集来验证。
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import fetch  # noqa: E402

NBSP = chr(0x00A0)

FULL_ARTICLE = """
<article class="Box-row">
  <div class="float-sm-right d-flex flex-row flex-justify-end">
    <a class="btn btn-sm btn-with-count" href="/acme/widget/stargazers">
      <svg class="octicon octicon-star"></svg> 17,010
    </a>
  </div>
  <h2 class="h3 lh-condensed">
    <a href="/acme/widget" data-view-component="true">
      <svg class="octicon octicon-repo"></svg>
      <span class="text-normal">acme /</span>
      widget
    </a>
  </h2>
  <p class="col-9 color-fg-muted my-1 pr-4">A tiny widget library for demos.</p>
  <div class="f6 color-fg-muted mt-2">
    <span class="d-inline-block ml-0 mr-3">
      <span class="repo-language-color" style="background-color: #3572A5"></span>
      <span itemprop="programmingLanguage">Python</span>
    </span>
    <a class="Link--muted d-inline-block mr-3" href="/acme/widget/stargazers">
      <svg></svg> 17,010
    </a>
    <a class="Link--muted d-inline-block mr-3" href="/acme/widget/forks">
      <svg></svg> 1,184
    </a>
    <span class="d-inline-block float-sm-right">
      <svg></svg> 1,002 stars today
    </span>
  </div>
</article>
"""

SPARSE_ARTICLE = """
<article class="Box-row">
  <h2 class="h3 lh-condensed">
    <a href="/beta/tool">beta / tool</a>
  </h2>
  <div class="f6 color-fg-muted mt-2">
    <a class="Link--muted" href="/beta/tool/stargazers"><svg></svg> 42</a>
  </div>
</article>
"""

NOT_A_REPO = """
<article class="Box-row">
  <p class="col-9">Promotional row without a repository link.</p>
</article>
"""


def page(*articles):
    return "<html><body><div class='Box'>" + "".join(articles) + "</div></body></html>"


class ToIntTest(unittest.TestCase):
    def test_plain_number(self):
        self.assertEqual(fetch.to_int("123"), 123)

    def test_thousands_separator(self):
        self.assertEqual(fetch.to_int("17,010"), 17010)

    def test_abbreviated(self):
        self.assertEqual(fetch.to_int("1.2k"), 1200)
        self.assertEqual(fetch.to_int("3M"), 3_000_000)

    def test_unicode_space_separator(self):
        self.assertEqual(fetch.to_int(f"1{NBSP}002"), 1002)

    def test_trailing_words_ignored(self):
        self.assertEqual(fetch.to_int("1,002 stars today"), 1002)

    def test_unparsable_returns_none(self):
        for value in (None, "", "   ", "No stars", "stars"):
            self.assertIsNone(fetch.to_int(value), value)


class ParseArticleTest(unittest.TestCase):
    def setUp(self):
        self.repos = fetch.parse_trending_html(page(FULL_ARTICLE, SPARSE_ARTICLE, NOT_A_REPO))

    def test_drops_rows_without_repo_link(self):
        self.assertEqual([r["name"] for r in self.repos], ["acme/widget", "beta/tool"])

    def test_extracts_full_record(self):
        repo = self.repos[0]
        self.assertEqual(repo["name"], "acme/widget")
        self.assertEqual(repo["url"], "https://github.com/acme/widget")
        self.assertEqual(repo["description"], "A tiny widget library for demos.")
        self.assertEqual(repo["language"], "Python")
        self.assertEqual(repo["stars"], 17010)
        self.assertEqual(repo["forks"], 1184)
        self.assertEqual(repo["add_stars"], 1002)

    def test_missing_fields_are_empty_not_fabricated(self):
        repo = self.repos[1]
        self.assertEqual(repo["description"], "")
        self.assertEqual(repo["language"], "")
        self.assertIsNone(repo["forks"])
        self.assertEqual(repo["stars"], 42)

    def test_nested_path_is_not_a_repo_row(self):
        html = page('<article class="Box-row"><h2><a href="/a/b/tree/main">x</a></h2></article>')
        self.assertEqual(fetch.parse_trending_html(html), [])


class EmptyResultTest(unittest.TestCase):
    """A4／A5：上游改版的主要症状是解析结果为 0，必须能被断言捕获。"""

    def test_no_articles_yields_empty_list(self):
        self.assertEqual(fetch.parse_trending_html(page()), [])

    def test_renamed_container_yields_empty_list(self):
        html = '<html><body><div class="ProjectCard"><a href="/a/b">a / b</a></div></body></html>'
        self.assertEqual(fetch.parse_trending_html(html), [])


class FieldMissRateTest(unittest.TestCase):
    """§4.8.2：字段缺失率比「条数 > 0」更敏感。"""

    def test_reports_no_misses_when_all_present(self):
        repos = fetch.parse_trending_html(page(FULL_ARTICLE))
        rates = fetch.field_miss_rates(repos)
        self.assertEqual(set(rates.values()), {0.0})

    def test_reports_language_miss_only(self):
        repos = fetch.parse_trending_html(page(FULL_ARTICLE, SPARSE_ARTICLE))
        rates = fetch.field_miss_rates(repos)
        self.assertEqual(rates["language"], 0.5)
        self.assertEqual(rates["name"], 0.0)
        self.assertEqual(rates["stars"], 0.0)

    def test_empty_input_reports_total_miss(self):
        rates = fetch.field_miss_rates([])
        self.assertEqual(set(rates.values()), {1.0})


class DiagnoseTest(unittest.TestCase):
    """online-smoke 的结构化诊断输入。"""

    def setUp(self):
        self.diag = fetch.diagnose_html(page(FULL_ARTICLE, SPARSE_ARTICLE, NOT_A_REPO))

    def test_reports_selector_presence(self):
        self.assertTrue(self.diag["has_stargazers_link"])
        self.assertTrue(self.diag["has_language_itemprop"])
        self.assertTrue(self.diag["has_period_badge"])

    def test_separates_found_from_parsed(self):
        self.assertEqual(self.diag["articles_found"], 3)
        self.assertEqual(self.diag["parsed"], 2)

    def test_carries_miss_rates(self):
        self.assertEqual(self.diag["field_miss_rates"]["language"], 0.5)


class FixturePathTest(unittest.TestCase):
    def test_all_languages_uses_all_token(self):
        self.assertEqual(fetch.fixture_relative_path("daily", ""), "trending/daily_all.html")

    def test_language_board_path(self):
        self.assertEqual(fetch.fixture_relative_path("weekly", "go"), "trending/weekly_go.html")


if __name__ == "__main__":
    unittest.main()
