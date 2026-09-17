"""A 组验收：对真实线上快照的解析断言。

这些 fixture 由 online-smoke 首次运行引导写入并提交入库，此后冻结不覆盖。因此本
文件断言的是**一份确定的、不会自动漂移的数据**——上游若改版，表现是 online-smoke
失败，而这些断言继续通过（这正是设计意图：漂移必须暴露为红灯，不能悄悄改测试）。
"""

import sys
import unittest
from functools import lru_cache
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cache  # noqa: E402
import config  # noqa: E402
import fetch  # noqa: E402

BOARDS = [(w, l) for w in config.TIME_WINDOWS for l in config.LANGUAGES]


@lru_cache(maxsize=None)
def load(window, language):
    """解析结果按榜单缓存：快照共 14MB，逐个测试重复解析会把套件拖到一分钟以上。"""
    relative = fetch.fixture_relative_path(window, language)
    return fetch.parse_trending_html(cache.read_fixture(relative))


class FixturePresenceTest(unittest.TestCase):
    """A1：21 个榜单快照齐备。"""

    def test_all_boards_have_fixtures(self):
        missing = [
            fetch.fixture_relative_path(w, l)
            for w, l in BOARDS
            if not cache.fixture_exists(fetch.fixture_relative_path(w, l))
        ]
        self.assertEqual(missing, [], f"缺少快照：{missing}")

    def test_board_count_is_twenty_one(self):
        self.assertEqual(len(BOARDS), 21)
        self.assertEqual(len(set(BOARDS)), 21)


class BoardContentTest(unittest.TestCase):
    """A2、A3：条数与关键字段。"""

    def test_every_board_parses_to_a_non_empty_list(self):
        for window, language in BOARDS:
            with self.subTest(board=f"{window}/{language or 'all'}"):
                self.assertGreaterEqual(len(load(window, language)), 1)

    def test_every_board_stays_within_the_page_limit(self):
        for window, language in BOARDS:
            with self.subTest(board=f"{window}/{language or 'all'}"):
                self.assertLessEqual(len(load(window, language)), config.MAX_REPOS_PER_BOARD)

    def test_counts_are_not_uniform_so_hardcoding_25_would_be_wrong(self):
        """真实榜单条数在 14–24 之间波动，并非固定 25 条。

        这是 A2 拒绝硬编码 25 的实证依据——fixture 已冻结，故该断言长期稳定。
        """
        counts = {len(load(w, l)) for w, l in BOARDS}
        self.assertGreater(len(counts), 1, "条数出现单一取值，说明断言方式需重新审视")
        self.assertTrue(any(c < config.MAX_REPOS_PER_BOARD for c in counts))

    def test_key_fields_are_never_missing(self):
        for window, language in BOARDS:
            with self.subTest(board=f"{window}/{language or 'all'}"):
                rates = fetch.field_miss_rates(load(window, language))
                for field in ("name", "url", "stars"):
                    self.assertEqual(rates[field], 0.0, f"{field} 出现缺失：{rates}")

    def test_language_is_captured_on_real_boards(self):
        for window, language in BOARDS:
            with self.subTest(board=f"{window}/{language or 'all'}"):
                rates = fetch.field_miss_rates(load(window, language))
                self.assertLessEqual(rates["language"], config.FIELD_MISS_TOLERANCE["language"])

    def test_description_may_legitimately_be_absent(self):
        """有些仓库本就没有简介，所以 description 不参与失败判定。

        本断言固化这一事实：真实数据里确实存在无简介的仓库。
        """
        rates = [fetch.field_miss_rates(load(w, l))["description"] for w, l in BOARDS]
        self.assertTrue(any(rate > 0 for rate in rates), "样本中无缺简介仓库，断言前提需复核")


class InvariantTest(unittest.TestCase):
    """A4／A5 的正面：真实快照必须全部通过结构不变量。"""

    def test_no_board_violates_invariants(self):
        for window, language in BOARDS:
            with self.subTest(board=f"{window}/{language or 'all'}"):
                self.assertEqual(fetch.invariant_failures(load(window, language)), [])

    def test_every_record_has_a_plausible_repo_url(self):
        for window, language in BOARDS:
            repos = load(window, language)
            for repo in repos:
                self.assertTrue(repo["url"].startswith("https://github.com/"), repo["url"])
                self.assertRegex(repo["name"], r"^[^/]+/[^/]+$")

    def test_language_boards_only_contain_that_language_except_all(self):
        """语言榜里绝大多数条目应是该语言——这条能发现「抓错了页面」。

        存在少量例外是正常的（仓库在榜期内改主语言、或 GitHub 归类与榜位不一致），
        因此用比例而非全等。
        """
        for window in config.TIME_WINDOWS:
            for language, label in config.LANGUAGES.items():
                if not language:
                    continue
                repos = load(window, language)
                matching = sum(1 for r in repos if r["language"].lower() == label.lower())
                with self.subTest(board=f"{window}/{language}"):
                    self.assertGreaterEqual(matching / len(repos), 0.7)


if __name__ == "__main__":
    unittest.main()
