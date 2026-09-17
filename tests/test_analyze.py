"""analyze.py 的离线单元测试（B 组）。

真实感较强的响应放在 tests/fixtures/deepseek/ 下（纯文本，避免手写 JSON 转义），
短小的边界与畸形用例内联在此——它们本身是测试数据而非 fixture。

DeepSeek 响应属于 §4.8.3 中「手工构造、不参与漂移检测」的一类，因此放在 fixture
目录里不会与 trending 快照的引导写入逻辑冲突。
"""

import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import analyze  # noqa: E402
import cache  # noqa: E402
import config  # noqa: E402

REPO = {
    "name": "acme/duckpipe",
    "description": "声明式数据管道编排工具",
    "language": "Python",
    "stars": 17010,
    "add_stars": 1002,
    "forks": 1184,
}

VALID_PAYLOAD = {
    "one_liner": "把数据管道编排成可版本控制的代码",
    "core_features": ["定义即代码", "断点续跑", "多数据源适配"],
    "tech_stack": ["Python", "DuckDB"],
    "highlights": "管道定义可被单元测试",
    "target_audience": "数据工程师",
    "scores": {"innovation": 4, "practicality": 5, "learning_value": 3},
}


def payload(**overrides):
    merged = json.loads(json.dumps(VALID_PAYLOAD))
    merged.update(overrides)
    return merged


class PromptReproducibilityTest(unittest.TestCase):
    """B5：prompt 必须可复现——缓存键的稳定性依赖于此。"""

    def test_same_input_yields_byte_identical_prompt(self):
        readme = cache.read_fixture(f"{config.FIXTURE_README_SUBDIR}/sample.md")
        first = analyze.build_prompt(REPO, readme)
        second = analyze.build_prompt(REPO, readme)
        self.assertEqual(first, second)
        self.assertEqual(len(first.encode("utf-8")), len(second.encode("utf-8")))

    def test_key_order_in_repo_dict_does_not_matter(self):
        readme = "README 内容"
        shuffled = dict(reversed(list(REPO.items())))
        self.assertEqual(analyze.build_prompt(REPO, readme), analyze.build_prompt(shuffled, readme))

    def test_prompt_version_is_embedded(self):
        prompt = analyze.build_prompt(REPO, "x", prompt_version="v9")
        self.assertIn("prompt_version=v9", prompt)
        self.assertNotEqual(prompt, analyze.build_prompt(REPO, "x", prompt_version="v10"))

    def test_missing_fields_render_as_placeholder_not_none(self):
        prompt = analyze.build_prompt({"name": "a/b"}, None)
        self.assertIn("未知", prompt)
        self.assertNotIn("None", prompt)

    def test_readme_is_truncated(self):
        long_readme = "X" * config.README_MAX_CHARS + "SENTINEL_TAIL"
        prompt = analyze.build_prompt(REPO, long_readme)
        self.assertNotIn("SENTINEL_TAIL", prompt)
        self.assertIn("X" * config.README_MAX_CHARS, prompt)

    def test_missing_readme_is_stated_explicitly(self):
        for empty in (None, "", "   \n  "):
            prompt = analyze.build_prompt(REPO, empty)
            self.assertIn(analyze.READMISSING_PLACEHOLDER, prompt)

    def test_prompt_carries_all_metadata(self):
        prompt = analyze.build_prompt(REPO, "readme")
        for value in ("acme/duckpipe", "Python", "17010", "1002", "1184"):
            self.assertIn(value, prompt)


class ExtractionTest(unittest.TestCase):
    """模型输出的包装形式多样，三种都必须解析成同一结果。"""

    def load(self, name):
        return cache.read_fixture(f"{config.FIXTURE_DEEPSEEK_SUBDIR}/{name}")

    def test_three_wrappings_parse_identically(self):
        results = [
            analyze.parse_analysis_response(self.load(name))
            for name in ("bare.txt", "fenced.txt", "with_prose.txt")
        ]
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])

    def test_normalized_shape(self):
        result = analyze.parse_analysis_response(self.load("bare.txt"))
        self.assertEqual(
            set(result),
            {"one_liner", "core_features", "tech_stack", "highlights", "target_audience", "scores"},
        )
        self.assertEqual(set(result["scores"]), set(analyze.SCORE_KEYS))

    def test_empty_response_rejected(self):
        for empty in (None, "", "   \n "):
            with self.assertRaises(analyze.AnalysisError):
                analyze.parse_analysis_response(empty)

    def test_prose_without_json_rejected(self):
        with self.assertRaises(analyze.AnalysisError):
            analyze.parse_analysis_response("这个项目看起来不错，但我不打算输出结构化结果。")

    def test_truncated_json_rejected_at_extraction(self):
        """缺闭合大括号——在取 JSON 区间时就该失败，报错比「解析失败」更准确。"""
        with self.assertRaises(analyze.AnalysisError) as caught:
            analyze.parse_analysis_response('{"one_liner": "x", "core_features": [')
        self.assertIn("找不到完整的 JSON 对象", str(caught.exception))

    def test_braces_present_but_invalid_json_rejected_at_parse(self):
        with self.assertRaises(analyze.AnalysisError) as caught:
            analyze.parse_analysis_response('{"one_liner": xyz, "scores": }')
        self.assertIn("JSON 解析失败", str(caught.exception))

    def test_top_level_array_rejected(self):
        """数组响应会被取出其中最外层对象再校验，因此以字段不全为由失败。

        无论走哪条分支，结论都必须是拒绝——绝不能返回半成品。
        """
        with self.assertRaises(analyze.AnalysisError):
            analyze.parse_analysis_response('[{"one_liner": "x"}]')

    def test_validate_rejects_non_object_directly(self):
        for bad in (["not", "a", "dict"], "字符串", 42, None):
            with self.assertRaises(analyze.AnalysisError) as caught:
                analyze.validate_analysis(bad)
            self.assertIn("顶层不是 JSON 对象", str(caught.exception))


class FieldValidationTest(unittest.TestCase):
    """B1、B2：结构与取值。"""

    def validate(self, p):
        return analyze.validate_analysis(p)

    def test_valid_payload_passes(self):
        result = self.validate(payload())
        self.assertEqual(result["scores"]["innovation"], 4)
        self.assertIsInstance(result["scores"]["innovation"], int)

    def test_feature_count_boundaries(self):
        self.assertEqual(len(self.validate(payload(core_features=["a", "b", "c"]))["core_features"]), 3)
        self.assertEqual(
            len(self.validate(payload(core_features=["a", "b", "c", "d", "e"]))["core_features"]), 5
        )
        for bad in (["a", "b"], []):
            with self.assertRaises(analyze.AnalysisError) as caught:
                self.validate(payload(core_features=bad))
            self.assertIn("core_features", str(caught.exception))

    def test_slightly_over_target_is_kept_and_trimmed(self):
        """擦边不算错：6 条是实测最常出现的偏差，为它丢掉一次已付费调用不划算。"""
        result = self.validate(payload(core_features=[f"f{i}" for i in range(6)]))
        self.assertEqual(len(result["core_features"]), analyze.FEATURE_KEEP)
        result = self.validate(
            payload(core_features=[f"f{i}" for i in range(analyze.FEATURE_SANITY_MAX)])
        )
        self.assertEqual(len(result["core_features"]), analyze.FEATURE_KEEP)

    def test_far_over_target_is_rejected(self):
        too_many = [f"f{i}" for i in range(analyze.FEATURE_SANITY_MAX + 1)]
        with self.assertRaises(analyze.AnalysisError):
            self.validate(payload(core_features=too_many))

    def test_one_liner_within_target_is_untouched(self):
        for length in (10, analyze.ONE_LINER_TARGET_CHARS):
            text = "字" * length
            with self.subTest(length=length):
                self.assertEqual(self.validate(payload(one_liner=text))["one_liner"], text)

    def test_one_liner_over_target_is_trimmed_not_rejected(self):
        """84 字是实测真实出现过的值（googleworkspace/cli）。

        截断而非拒绝：若拒绝，该仓库会永远进不了缓存，在站点上永久缺卡。
        +1 是硬截断时补的省略号。
        """
        result = self.validate(payload(one_liner="字" * 84))
        self.assertLessEqual(len(result["one_liner"]), analyze.ONE_LINER_SANITY_MAX + 1)

    def test_trim_prefers_clause_boundary(self):
        text = "字" * 50 + "。" + "字" * 80
        trimmed = self.validate(payload(one_liner=text))["one_liner"]
        self.assertTrue(trimmed.endswith("。"), f"应在标点处断开：{trimmed!r}")
        self.assertLessEqual(len(trimmed), analyze.ONE_LINER_SANITY_MAX)

    def test_trim_hard_cuts_with_ellipsis_when_no_boundary(self):
        trimmed = self.validate(payload(one_liner="字" * 120))["one_liner"]
        self.assertEqual(len(trimmed), analyze.ONE_LINER_SANITY_MAX + 1)
        self.assertTrue(trimmed.endswith("…"))

    def test_trim_is_idempotent(self):
        once = analyze.trim_one_liner("字" * 200)
        self.assertEqual(analyze.trim_one_liner(once), once)

    def test_pathological_one_liner_is_rejected(self):
        """只有模型把整段分析塞进一句话时才拒绝。"""
        with self.assertRaises(analyze.AnalysisError) as caught:
            self.validate(payload(one_liner="字" * (analyze.ONE_LINER_HARD_MAX + 1)))
        self.assertIn("未按结构作答", str(caught.exception))

    def test_score_range_boundaries(self):
        for value in (1, 5):
            self.validate(payload(scores={"innovation": value, "practicality": 3, "learning_value": 3}))
        for value in (0, 6, -1):
            with self.assertRaises(analyze.AnalysisError):
                self.validate(payload(scores={"innovation": value, "practicality": 3, "learning_value": 3}))

    def test_score_must_not_be_string_or_bool(self):
        for bad in ("5", True, None, 3.5):
            with self.assertRaises(analyze.AnalysisError) as caught:
                self.validate(payload(scores={"innovation": bad, "practicality": 3, "learning_value": 3}))
            self.assertIn("scores.innovation", str(caught.exception))

    def test_integral_float_score_is_accepted(self):
        """模型偶尔把 4 写成 4.0，为此丢弃一次调用不划算。"""
        result = self.validate(
            payload(scores={"innovation": 4.0, "practicality": 3.0, "learning_value": 5.0})
        )
        self.assertEqual(result["scores"], {"innovation": 4, "practicality": 3, "learning_value": 5})
        self.assertIsInstance(result["scores"]["innovation"], int)

    def test_missing_scores_object_rejected(self):
        broken = payload()
        del broken["scores"]
        with self.assertRaises(analyze.AnalysisError) as caught:
            self.validate(broken)
        self.assertIn("scores 缺失", str(caught.exception))

    def test_all_problems_reported_at_once(self):
        """一次列出全部问题，便于单轮修复——比逐个报错省调用次数。"""
        with self.assertRaises(analyze.AnalysisError) as caught:
            self.validate({"one_liner": "", "core_features": [], "scores": {}})
        message = str(caught.exception)
        for expected in ("one_liner", "core_features", "tech_stack", "highlights", "target_audience", "innovation"):
            self.assertIn(expected, message)

    def test_blank_list_items_are_dropped(self):
        result = self.validate(payload(core_features=["a", "  ", "", "b", "c"]))
        self.assertEqual(result["core_features"], ["a", "b", "c"])


class TextQualityHelpersTest(unittest.TestCase):
    """B3、B4：语言与 URL 检查所依赖的纯helper。"""

    def fixture_analyses(self):
        text = cache.read_fixture(f"{config.FIXTURE_DEEPSEEK_SUBDIR}/bare.txt")
        return [analyze.parse_analysis_response(text)]

    def test_fixture_has_enough_chinese(self):
        """B3：叙述字段必须含足够的中文字符。"""
        for analysis in self.fixture_analyses():
            for text in analyze.iter_prose(analysis):
                with self.subTest(text=text[:20]):
                    self.assertGreaterEqual(
                        analyze.chinese_char_count(text), analyze.MIN_CHINESE_CHARS
                    )

    def test_ratio_is_the_wrong_instrument_for_language_checks(self):
        """固化「占比不适合做语言检查」这一结论。

        下面这句是**完全正确的中文**，只因密布专有名词，占比仅约 0.3。真实数据集
        里 552 个叙述字段有 109 个占比低于 0.6，中位却是 0.759——占比度量的是专有
        名词密度，不是回答语言。改用绝对字符数后这些都不再误报。
        """
        text = "基于 Tauri 的现代化 Clash Meta 图形客户端，支持 Windows、macOS 和 Linux。"
        self.assertLess(analyze.chinese_ratio(text), 0.35)
        self.assertGreaterEqual(analyze.chinese_char_count(text), analyze.MIN_CHINESE_CHARS)

    def test_english_answer_would_be_caught(self):
        """真正的失败模式是模型用英文作答，绝对字符数能可靠拦住。"""
        for english in (
            "A fast web framework for building APIs.",
            "This tool helps you deploy containers to Kubernetes clusters safely.",
            "CI/CD automation with declarative pipelines and rollback support.",
        ):
            with self.subTest(text=english[:24]):
                self.assertLess(analyze.chinese_char_count(english), analyze.MIN_CHINESE_CHARS)

    def test_tech_stack_is_exempt_from_the_language_check(self):
        """tech_stack 装的是框架与工具名，实测中文占比为 0——对它做语言检查没有意义。

        这条断言固化该事实，防止后人「顺手」把语言检查扩回全部字段。
        """
        for analysis in self.fixture_analyses():
            ratios = [analyze.chinese_ratio(item) for item in analysis["tech_stack"]]
            self.assertTrue(all(ratio == 0.0 for ratio in ratios), ratios)
        self.assertNotIn("tech_stack", analyze.PROSE_FIELDS)

    def test_fixture_contains_no_urls(self):
        """B4：模型不得编造链接。"""
        for analysis in self.fixture_analyses():
            for text in analyze.iter_texts(analysis):
                self.assertEqual(analyze.find_urls(text), [])

    def test_chinese_ratio_edges(self):
        self.assertEqual(analyze.chinese_ratio("全部是中文"), 1.0)
        self.assertEqual(analyze.chinese_ratio("all latin text"), 0.0)
        self.assertEqual(analyze.chinese_ratio(""), 0.0)
        self.assertEqual(analyze.chinese_ratio("   \n  "), 0.0)
        self.assertAlmostEqual(analyze.chinese_ratio("中文ab"), 0.5)

    def test_chinese_char_count_edges(self):
        self.assertEqual(analyze.chinese_char_count("全部是中文"), 5)
        self.assertEqual(analyze.chinese_char_count("all latin text"), 0)
        self.assertEqual(analyze.chinese_char_count(None), 0)
        self.assertEqual(analyze.chinese_char_count(""), 0)
        self.assertEqual(analyze.chinese_char_count("中文 with latin"), 2)

    def test_find_urls_detects_and_strips_punctuation(self):
        found = analyze.find_urls("参考 https://example.com/a，以及 http://x.test/b。")
        self.assertEqual(found, ["https://example.com/a", "http://x.test/b"])

    def test_iter_texts_covers_every_field(self):
        analysis = payload()
        joined = "|".join(analyze.iter_texts(analysis))
        for value in (analysis["one_liner"], analysis["highlights"], analysis["target_audience"]):
            self.assertIn(value, joined)
        for item in analysis["core_features"] + analysis["tech_stack"]:
            self.assertIn(item, joined)


class RealDatasetQualityTest(unittest.TestCase):
    """对真实 AI 输出做质量断言。

    缓存由构建流水线写入并提交入库，因此是确定数据而非实时数据——对这些内容断言
    与 test_fixtures.py 对榜单快照断言是同一思路。这一层才是能真正发现「阈值在
    真实数据上站不住」的地方：B3 原先的占比阈值正是在这里暴露的。
    """

    @classmethod
    def setUpClass(cls):
        cls.entries = cache.load_analysis_cache()
        if not cls.entries:
            raise unittest.SkipTest("尚无真实解读缓存（首次构建前）")

    def analyses(self):
        return {key: entry["analysis"] for key, entry in self.entries.items()}

    def test_b1_every_stored_analysis_passes_validation(self):
        for key, analysis in self.analyses().items():
            with self.subTest(repo=key):
                revalidated = analyze.validate_analysis(analysis)
                self.assertEqual(revalidated["scores"], analysis["scores"])

    def test_b3_every_prose_field_has_enough_chinese(self):
        offenders = []
        for key, analysis in self.analyses().items():
            for text in analyze.iter_prose(analysis):
                if analyze.chinese_char_count(text) < analyze.MIN_CHINESE_CHARS:
                    offenders.append(f"{key}: {text[:40]}")
        self.assertEqual(offenders, [], f"叙述字段中文字符不足：{offenders[:5]}")

    def test_b4_no_hallucinated_urls(self):
        offenders = [
            key
            for key, analysis in self.analyses().items()
            if any(analyze.find_urls(text) for text in analyze.iter_texts(analysis))
        ]
        self.assertEqual(offenders, [], f"解读中出现 URL：{offenders[:5]}")

    def test_b2_scores_and_feature_counts_within_range(self):
        for key, analysis in self.analyses().items():
            with self.subTest(repo=key):
                self.assertLessEqual(len(analysis["core_features"]), analyze.FEATURE_KEEP)
                self.assertGreaterEqual(len(analysis["core_features"]), analyze.FEATURE_TARGET_MIN)
                for score in analysis["scores"].values():
                    self.assertIsInstance(score, int)
                    self.assertTrue(1 <= score <= 5)

    def test_b2_one_liner_lengths_within_bound(self):
        """+1 是留给硬截断时补的省略号。"""
        limit = analyze.ONE_LINER_SANITY_MAX + 1
        offenders = {
            key: len(analysis["one_liner"])
            for key, analysis in self.analyses().items()
            if len(analysis["one_liner"]) > limit
        }
        self.assertEqual(offenders, {}, f"以下条目总结超长：{offenders}")

    def test_cache_entries_record_provenance(self):
        """缓存条目必须记下版本与模型，否则切换模型时无法定向失效。"""
        for key, entry in self.entries.items():
            with self.subTest(repo=key):
                self.assertEqual(entry["prompt_version"], config.PROMPT_VERSION)
                self.assertTrue(entry["model"])
                self.assertTrue(entry["analyzed_at"])
                self.assertIn("readme_available", entry)
                self.assertIsInstance(entry["stars_at_analysis"], int)


if __name__ == "__main__":
    unittest.main()
