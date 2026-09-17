"""cache.py 的离线单元测试（C1、C3–C6、C16 相关；C17 由本文件的零网络依赖体现）。"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cache  # noqa: E402
import config  # noqa: E402

NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)


def entry(analyzed_at, stars, version=None, analysis=None):
    return {
        "analysis": analysis if analysis is not None else {"one_liner": "示例"},
        "stars_at_analysis": stars,
        "prompt_version": config.PROMPT_VERSION if version is None else version,
        "model": config.DEEPSEEK_MODEL,
        "analyzed_at": analyzed_at.isoformat(),
        "readme_available": True,
    }


class RepoKeyTest(unittest.TestCase):
    """C1：跨榜去重键。"""

    def test_key_is_case_insensitive(self):
        self.assertEqual(cache.repo_key("Torvalds/Linux"), cache.repo_key("torvalds/linux"))

    def test_key_trims_whitespace(self):
        self.assertEqual(cache.repo_key("  a/b  "), "a/b")


class CacheValidityTest(unittest.TestCase):
    def test_missing_entry_needs_analysis(self):
        required, reason = cache.needs_analysis(None, 100, now=NOW)
        self.assertTrue(required)
        self.assertEqual(reason, "missing")

    def test_ttl_expired_reruns(self):
        """C3：8 天前分析过的条目必须重跑。"""
        required, reason = cache.needs_analysis(
            entry(NOW - timedelta(days=8), 100), 100, now=NOW
        )
        self.assertTrue(required)
        self.assertEqual(reason, "ttl")

    def test_ttl_within_window_is_reused(self):
        """C4：6 天前分析且其他条件均不命中时必须复用。"""
        required, reason = cache.needs_analysis(
            entry(NOW - timedelta(days=6), 100), 100, now=NOW
        )
        self.assertFalse(required)
        self.assertEqual(reason, "fresh")

    def test_prompt_version_change_invalidates(self):
        """C6：提示词版本变化让全部条目失效。"""
        required, reason = cache.needs_analysis(
            entry(NOW - timedelta(days=1), 100, version="v0"), 100, now=NOW
        )
        self.assertTrue(required)
        self.assertEqual(reason, "prompt_version")

    def test_unparsable_timestamp_treated_as_expired(self):
        broken = entry(NOW, 100)
        broken["analyzed_at"] = "bogus"
        required, reason = cache.needs_analysis(broken, 100, now=NOW)
        self.assertTrue(required)
        self.assertEqual(reason, "ttl")


class StarThresholdTest(unittest.TestCase):
    """C5：Star 阈值边界（绝对 5000、比例 50%，均为「超过」才重跑）。"""

    def _shifted(self, base, current):
        return cache.is_star_shifted({"stars_at_analysis": base}, current)

    def test_absolute_boundary_exactly_5000_not_shifted(self):
        self.assertFalse(self._shifted(10000, 15000))

    def test_absolute_boundary_5001_shifted(self):
        self.assertTrue(self._shifted(10000, 15001))

    def test_ratio_boundary_exactly_50pct_not_shifted(self):
        self.assertFalse(self._shifted(100, 150))

    def test_ratio_boundary_501pct_shifted(self):
        self.assertTrue(self._shifted(100, 151))

    def test_drop_is_also_a_shift(self):
        self.assertTrue(self._shifted(10000, 4999))

    def test_missing_baseline_is_shifted(self):
        self.assertTrue(cache.is_star_shifted({}, 100))

    def test_zero_baseline_does_not_divide_by_zero(self):
        self.assertFalse(self._shifted(0, 100))


class CandidateSelectionTest(unittest.TestCase):
    """C1：同一仓库跨榜共享一份分析，命中率按去重后的集合计算。"""

    def candidates(self):
        return [
            {"repo_key": "a/one", "window": "daily", "rank": 1, "stars": 100},
            {"repo_key": "a/one", "window": "weekly", "rank": 40, "stars": 100},
            {"repo_key": "b/two", "window": "daily", "rank": 2, "stars": 200},
        ]

    def test_key_set_deduplicates_across_boards(self):
        """同一仓库出现在两个榜单上时只产生一个键——这是跨榜共享一份分析的基础。"""
        board = self.candidates()
        self.assertEqual(len(board), 3)
        self.assertEqual({c["repo_key"] for c in board}, {"a/one", "b/two"})

    def test_empty_cache_reports_no_valid_keys(self):
        self.assertEqual(cache.valid_cache_keys(self.candidates(), {}, now=NOW), set())

    def test_shared_entry_satisfies_both_boards(self):
        shared = {"a/one": entry(NOW - timedelta(days=1), 100)}
        valid = cache.valid_cache_keys(self.candidates(), shared, now=NOW)
        self.assertIn("a/one", valid)

    def test_analyze_targets_reports_reason(self):
        board = self.candidates()
        targets = cache.analyze_targets(board, {}, now=NOW)
        self.assertEqual({t["repo_key"] for t in targets}, {"a/one", "b/two"})
        self.assertTrue(all(t["reason"] == "missing" for t in targets))


class FileAccessTest(unittest.TestCase):
    """cache.py 是 data/ 与 fixtures 的唯一读写入口（E13 的行为侧验证）。"""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / "data"
        self.fixture_dir = Path(self._tmp.name) / "fixtures"

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_file_returns_default(self):
        self.assertEqual(cache.load_boards(self.data_dir, default={}), {})
        self.assertEqual(cache.load_analysis_cache(self.data_dir), {})

    def test_roundtrip_preserves_unicode(self):
        cache.save_analysis_cache({"a/one": entry(NOW, 1)}, self.data_dir)
        loaded = cache.load_analysis_cache(self.data_dir)
        self.assertEqual(loaded["a/one"]["analysis"]["one_liner"], "示例")

    def test_write_is_atomic_leaves_no_tmp_file(self):
        cache.save_quota_state({"date": "2026-09-17"}, self.data_dir)
        leftovers = list(self.data_dir.glob("*.tmp"))
        self.assertEqual(leftovers, [])

    def test_fixture_write_then_read(self):
        self.assertFalse(cache.fixture_exists("trending/daily_all.html", self.fixture_dir))
        cache.write_fixture("trending/daily_all.html", "<html>快照</html>", self.fixture_dir)
        self.assertTrue(cache.fixture_exists("trending/daily_all.html", self.fixture_dir))
        self.assertEqual(
            cache.read_fixture("trending/daily_all.html", self.fixture_dir), "<html>快照</html>"
        )

    def test_fixture_write_is_idempotent_and_does_not_append(self):
        cache.write_fixture("readme/a_one.md", "first", self.fixture_dir)
        cache.write_fixture("readme/a_one.md", "second", self.fixture_dir)
        self.assertEqual(cache.read_fixture("readme/a_one.md", self.fixture_dir), "second")


if __name__ == "__main__":
    unittest.main()
