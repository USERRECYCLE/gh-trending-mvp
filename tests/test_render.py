"""render.py 的离线单元测试（E1、E7、E9、E10、E11、F4 及纯函数部分）。

需要真实浏览器的 E2／E3／E4 见 test_browser.py。
"""

import json
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cache  # noqa: E402
import config  # noqa: E402
import render  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"

ANALYSIS = {
    "one_liner": "把数据管道编排成可版本控制的代码",
    "core_features": ["定义即代码", "断点续跑", "多数据源"],
    "tech_stack": ["Python", "DuckDB"],
    "highlights": "管道定义可被单元测试",
    "target_audience": "数据工程师",
    "scores": {"innovation": 4, "practicality": 5, "learning_value": 3},
}


def repo(name, rank=1, **overrides):
    entry = {
        "rank": rank,
        "name": name,
        "url": f"https://github.com/{name}",
        "description": "示例描述",
        "language": "Python",
        "language_color": "#3572a5",
        "stars": 17010,
        "forks": 1184,
        "add_stars": 1002,
    }
    entry.update(overrides)
    return entry


def boards_doc(rows_by_key):
    return {"fetched_at": "2026-09-17T07:00:00+00:00", "boards": rows_by_key}


def cache_entry(**overrides):
    entry = {
        "analysis": ANALYSIS,
        "stars_at_analysis": 17010,
        "prompt_version": config.PROMPT_VERSION,
        "model": config.DEEPSEEK_MODEL,
        "analyzed_at": "2026-09-17T07:00:00+00:00",
        "readme_available": True,
    }
    entry.update(overrides)
    return entry


class BoardKeyTest(unittest.TestCase):
    def test_round_trip(self):
        key = config.board_key("daily", "python")
        self.assertEqual(config.parse_board_key(key), ("daily", "python"))

    def test_all_languages_key(self):
        self.assertEqual(config.parse_board_key(config.board_key("weekly", "")), ("weekly", ""))

    def test_twenty_one_distinct_keys(self):
        keys = {config.board_key(w, l) for w in config.TIME_WINDOWS for l in config.LANGUAGES}
        self.assertEqual(len(keys), 21)


class BuildSiteDataTest(unittest.TestCase):
    def build(self, rows_by_key=None, cache_doc=None, generated_at="2026-09-17T07:00:00+00:00"):
        return render.build_site_data(
            boards_doc(rows_by_key or {}), cache_doc or {}, generated_at
        )

    def test_every_view_key_exists_even_when_source_is_missing(self):
        """前端切到任一时间／语言组合都必须有键，否则会渲染失败。"""
        site = self.build()
        self.assertEqual(len(site["boards"]), 21)
        for window in config.TIME_WINDOWS:
            for language in config.LANGUAGES:
                self.assertIn(config.board_key(window, language), site["boards"])
                self.assertEqual(site["boards"][config.board_key(window, language)], [])

    def test_analysis_is_attached_and_counted(self):
        rows = {config.board_key("daily", ""): [repo("acme/one"), repo("acme/two", rank=2)]}
        site = self.build(rows, {"acme/one": cache_entry()})
        entries = site["boards"][config.board_key("daily", "")]
        self.assertEqual(site["stats"]["analyzed"], 1)
        self.assertEqual(entries[0]["analysis"], ANALYSIS)
        self.assertIsNone(entries[1]["analysis"])

    def test_lookup_is_case_insensitive(self):
        """去重键小写化，因此大小写不同的名字也应命中同一份分析。"""
        rows = {config.board_key("daily", ""): [repo("Acme/One")]}
        site = self.build(rows, {"acme/one": cache_entry()})
        self.assertIsNotNone(site["boards"][config.board_key("daily", "")][0]["analysis"])

    def test_same_repo_on_two_boards_shares_one_analysis(self):
        rows = {
            config.board_key("daily", ""): [repo("acme/one")],
            config.board_key("daily", "python"): [repo("acme/one")],
        }
        site = self.build(rows, {"acme/one": cache_entry()})
        first = site["boards"][config.board_key("daily", "")][0]["analysis"]
        second = site["boards"][config.board_key("daily", "python")][0]["analysis"]
        self.assertEqual(first, second)

    def test_malformed_cache_entry_degrades_to_pending(self):
        """缓存条目存在但 analysis 不是对象时，必须退化为「无解读」而不是崩溃。"""
        rows = {config.board_key("daily", ""): [repo("acme/one")]}
        for bad in ({"analysis": None}, {"analysis": "字符串"}, {}, "不是字典"):
            with self.subTest(bad=bad):
                site = self.build(rows, {"acme/one": bad})
                self.assertIsNone(site["boards"][config.board_key("daily", "")][0]["analysis"])

    def test_language_color_and_rank_pass_through(self):
        rows = {config.board_key("daily", ""): [repo("acme/one", rank=7, language_color="#f05138")]}
        entry = self.build(rows)["boards"][config.board_key("daily", "")][0]
        self.assertEqual(entry["rank"], 7)
        self.assertEqual(entry["language_color"], "#f05138")

    def test_generated_at_and_metadata_are_included(self):
        site = self.build(generated_at="2026-01-02T03:04:05+00:00")
        self.assertEqual(site["generated_at"], "2026-01-02T03:04:05+00:00")
        self.assertEqual(site["prompt_version"], config.PROMPT_VERSION)
        self.assertEqual(len(site["windows"]), 3)
        self.assertEqual(len(site["languages"]), 7)

    def test_stats_count_entries_across_all_boards(self):
        rows = {
            config.board_key("daily", ""): [repo("a/1"), repo("a/2", rank=2)],
            config.board_key("weekly", "go"): [repo("b/3")],
        }
        site = self.build(rows)
        self.assertEqual(site["stats"]["entries"], 3)
        self.assertEqual(site["stats"]["boards"], 21)


class WriteSiteDataTest(unittest.TestCase):
    def test_writes_utf8_json_without_escaping_chinese(self):
        with TemporaryDirectory() as tmp:
            path = render.write_site_data({"名字": "中文值"}, tmp)
            raw = path.read_bytes()
            self.assertIn("中文值".encode("utf-8"), raw)
            self.assertNotIn(b"\\u", raw)
            self.assertEqual(json.loads(raw.decode("utf-8")), {"名字": "中文值"})

    def test_uses_compact_separators(self):
        with TemporaryDirectory() as tmp:
            path = render.write_site_data({"a": [1, 2, 3]}, tmp)
            self.assertEqual(path.read_text(encoding="utf-8"), '{"a":[1,2,3]}')

    def test_creates_data_subdirectory(self):
        with TemporaryDirectory() as tmp:
            path = render.write_site_data({"a": 1}, tmp)
            self.assertEqual(path.parent.name, "data")


class RenderDistTest(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.dist = Path(self._tmp.name) / "dist"

    def tearDown(self):
        self._tmp.cleanup()

    def site(self):
        return render.build_site_data(
            boards_doc({config.board_key("daily", ""): [repo("acme/one")]}),
            {"acme/one": cache_entry()},
            "2026-09-17T07:00:00+00:00",
        )

    def test_e1_entry_file_exists(self):
        render.render_dist(self.site(), WEB_DIR, self.dist)
        self.assertTrue((self.dist / "index.html").is_file())

    def test_e10_assets_are_copied_byte_identically(self):
        render.render_dist(self.site(), WEB_DIR, self.dist)
        for name in ("index.html", "css/style.css", "js/adapter.js", "js/app.js"):
            with self.subTest(asset=name):
                self.assertEqual(
                    (WEB_DIR / name).read_bytes(),
                    (self.dist / name).read_bytes(),
                    f"{name} 在 dist/ 与 web/ 之间不一致，说明被编译或改写过",
                )

    def test_data_lands_under_dist_data(self):
        render.render_dist(self.site(), WEB_DIR, self.dist)
        payload = json.loads((self.dist / "data" / "trending.json").read_text(encoding="utf-8"))
        self.assertIn(config.board_key("daily", ""), payload["boards"])

    def test_rerender_removes_stale_files(self):
        render.render_dist(self.site(), WEB_DIR, self.dist)
        stale = self.dist / "js" / "leftover.js"
        stale.write_text("旧文件", encoding="utf-8")
        render.render_dist(self.site(), WEB_DIR, self.dist)
        self.assertFalse(stale.exists(), "重复渲染应清空 dist/，否则会残留上一次的旧文件")

    def test_missing_web_dir_is_reported(self):
        with self.assertRaises(render.RenderError):
            render.render_dist(self.site(), Path(self._tmp.name) / "nope", self.dist)

    def test_missing_index_html_is_reported(self):
        bare = Path(self._tmp.name) / "bare_web"
        bare.mkdir()
        with self.assertRaises(render.RenderError) as caught:
            render.render_dist(self.site(), bare, self.dist)
        self.assertIn("index.html", str(caught.exception))


class FrontendBoundaryTest(unittest.TestCase):
    """E9：前端的数据访问必须只发生在 adapter 模块内。"""

    def scripts(self):
        return sorted((WEB_DIR / "js").glob("*.js"))

    def test_only_adapter_uses_fetch(self):
        offenders = []
        for path in self.scripts():
            if "fetch(" in path.read_text(encoding="utf-8") and path.name != "adapter.js":
                offenders.append(path.name)
        self.assertEqual(offenders, [], "除 adapter.js 外不得出现 fetch（E9）")

    def test_adapter_exposes_the_expected_contract(self):
        text = (WEB_DIR / "js" / "adapter.js").read_text(encoding="utf-8")
        for method in ("load", "meta", "getBoard", "boardKey"):
            self.assertIn(method, text)
        self.assertIn("TrendingAdapter", text)

    def test_app_uses_the_adapter(self):
        text = (WEB_DIR / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("TrendingAdapter", text)
        self.assertNotIn("data/trending.json", text, "app.js 不应知道数据源路径")

    def test_no_html_injection_via_innerhtml(self):
        """仓库描述与 AI 解读都是外部文本，拼接 HTML 会形成注入面。

        判定用带点号的属性访问（如 node.innerHTML）而非裸词：注释里提到「不用
        innerHTML」这类说明文字不应被误判——这正是文本 grep 最容易犯的错。
        """
        import re

        assignment = re.compile(r"\.(innerHTML|outerHTML)\b|insertAdjacentHTML|document\s*\.\s*write")
        offenders = []
        for path in self.scripts():
            for hit in assignment.findall(path.read_text(encoding="utf-8")):
                offenders.append(f"{path.name}: {hit}")
        self.assertEqual(offenders, [], f"发现 HTML 拼接（存在注入面）：{offenders}")

    def test_e6_no_external_resource_references(self):
        """零第三方请求：源码中不得引用外部资源。"""
        import re

        external = re.compile(
            r"""(?:<link[^>]+href|<script[^>]+src|<img[^>]+src)\s*=\s*["']https?://|@import\s+url\(|url\(\s*["']?https?://""",
            re.IGNORECASE,
        )
        offenders = []
        for path in sorted(WEB_DIR.rglob("*")):
            if path.is_file() and path.suffix in {".html", ".css", ".js"}:
                for hit in external.findall(path.read_text(encoding="utf-8")):
                    offenders.append(f"{path.name}: {hit}")
        self.assertEqual(offenders, [], f"发现外部资源引用（E6）：{offenders}")


class RenderBudgetTest(unittest.TestCase):
    """E7、E11：体积与耗时上限。"""

    def _worst_case_site(self):
        rows = {}
        for window in config.TIME_WINDOWS:
            for language in config.LANGUAGES:
                rows[config.board_key(window, language)] = [
                    repo(f"org{i}/repo{i}", rank=i + 1) for i in range(config.MAX_REPOS_PER_BOARD)
                ]
        cache_doc = {f"org{i}/repo{i}": cache_entry() for i in range(config.MAX_REPOS_PER_BOARD)}
        return render.build_site_data(
            boards_doc(rows), cache_doc, "2026-09-17T07:00:00+00:00"
        )

    def test_e7_payload_stays_well_under_two_megabytes(self):
        """以最坏情况估算：21 个榜单全部满 25 条且每条都带完整解读。"""
        with TemporaryDirectory() as tmp:
            path = render.write_site_data(self._worst_case_site(), tmp)
            size_kb = path.stat().st_size / 1024
            self.assertLess(size_kb, 2048, f"前端 JSON {size_kb:.0f} KB 超过 E7 上限")

    def test_e11_render_is_seconds_not_minutes(self):
        site = self._worst_case_site()
        with TemporaryDirectory() as tmp:
            start = time.monotonic()
            render.render_dist(site, WEB_DIR, Path(tmp) / "dist")
            elapsed = time.monotonic() - start
        self.assertLess(elapsed, 30, f"渲染耗时 {elapsed:.1f}s 超过 E11 上限")


class RenderDoesNotTouchDataTest(unittest.TestCase):
    """F4：渲染只读 data/，重复执行不改变数据。"""

    def test_four_repeated_renders_leave_data_untouched(self):
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            data_dir.mkdir()
            cache.save_boards({"fetched_at": "t", "boards": {}}, data_dir)
            cache.save_analysis_cache({"acme/one": cache_entry()}, data_dir)

            before = {p.name: p.read_bytes() for p in sorted(data_dir.iterdir())}

            with mock.patch.object(config, "DATA_DIR", data_dir):
                for index in range(4):
                    render.render_now(
                        generated_at=f"2026-09-17T07:0{index}:00+00:00",
                        web_dir=WEB_DIR,
                        dist_dir=Path(tmp) / f"dist{index}",
                    )

            after = {p.name: p.read_bytes() for p in sorted(data_dir.iterdir())}
            self.assertEqual(before, after, "渲染阶段改动了 data/，违反两阶段分离")


if __name__ == "__main__":
    unittest.main()
