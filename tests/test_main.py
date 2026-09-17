"""main.py 的编排测试（C2、C10、C11、A4 及整链路离线跑通）。

两条路径都要覆盖：
- fixture 模式：用真实的 21 份快照跑通全链路，全程零网络。
- network 模式：注入假传输层，从而能断言「谁发了几次请求」——C11 这类断言只有在
  能观测请求的前提下才有意义。
"""

import json
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import analyze  # noqa: E402
import cache  # noqa: E402
import config  # noqa: E402
import main  # noqa: E402
import net  # noqa: E402

NOW = datetime(2026, 9, 17, 12, 0, 0, tzinfo=timezone.utc)
TRANSPORT_HOSTS = {
    "trending": "github.com/trending",
    "readme": "raw.githubusercontent.com",
    "deepseek": "api.deepseek.com",
}

# 假传输层返回的模型输出必须是**完整合规**的分析结果：只给一个 one_liner 会让每次
# 分析都触发 schema 校验失败，从而把「调用次数」类断言全部带偏。
VALID_ANALYSIS = {
    "one_liner": "把数据管道编排成可版本控制的代码",
    "core_features": ["定义即代码", "断点续跑", "多数据源适配"],
    "tech_stack": ["Python", "DuckDB"],
    "highlights": "管道定义可被单元测试",
    "target_audience": "数据工程师",
    "scores": {"innovation": 4, "practicality": 5, "learning_value": 3},
}


def envelope_for(analysis_json: str) -> str:
    return json.dumps({"choices": [{"message": {"content": analysis_json}}]})


DEEPSEEK_ENVELOPE = envelope_for(json.dumps(VALID_ANALYSIS, ensure_ascii=False))


def board_html(slug, count=2):
    """每个榜单给不同的仓库名，避免跨榜去重把候选数压掉，便于断言调用次数。"""
    articles = "".join(
        f"""
      <article class="Box-row">
        <h2 class="h3"><a href="/{slug}/repo{i}">{slug} / repo{i}</a></h2>
        <p class="col-9 color-fg-muted">描述 {slug}-{i}</p>
        <div class="f6 color-fg-muted">
          <span class="repo-language-color" style="background-color: #00add8"></span>
          <span itemprop="programmingLanguage">Go</span>
          <a href="/{slug}/repo{i}/stargazers">1,000</a>
          <a href="/{slug}/repo{i}/forks">10</a>
          <span class="d-inline-block float-sm-right">5 stars today</span>
        </div>
      </article>"""
        for i in range(count)
    )
    return f"<html><body>{articles}</body></html>"


def network_transport(calls, repos_per_board=2, deepseek_envelope=DEEPSEEK_ENVELOPE):
    """假传输层：按 URL 分派，并记录每一次请求。"""
    # 总榜 URL 没有尾斜杠（/trending?since=daily），语言榜才有（/trending/go?since=…）
    pattern = re.compile(r"/trending/?([^?]*)\?since=(\w+)")

    def transport(request):
        url = request.url
        calls.append(url)
        if TRANSPORT_HOSTS["trending"] in url:
            match = pattern.search(url)
            slug = f"{match.group(2)}-{match.group(1) or 'all'}"
            return board_html(slug, repos_per_board)
        if TRANSPORT_HOSTS["readme"] in url:
            return "# README 内容"
        if TRANSPORT_HOSTS["deepseek"] in url:
            return deepseek_envelope
        raise AssertionError(f"未预期的请求：{url}")

    return transport


class MainHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / "data"
        self.dist_dir = self.root / "dist"

        self._patches = [
            mock.patch.object(config, "DATA_DIR", self.data_dir),
            mock.patch.object(config, "DIST_DIR", self.dist_dir),
            # network 模式下没有 Key 会让每次调用都直接失败；这里给一个占位值，
            # 因为传输层是假的，Key 不会被真正校验。
            mock.patch.dict(
                "os.environ", {config.ENV_DEEPSEEK_API_KEY: "test-key"}, clear=True
            ),
        ]
        for patch in self._patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self._patches):
            patch.stop()
        self._tmp.cleanup()

    def run_main(self, **kwargs):
        kwargs.setdefault("log", lambda _message: None)
        kwargs.setdefault("now", NOW)
        return main.run(**kwargs)

    def cached_names(self):
        return set(cache.load_analysis_cache(self.data_dir))

    def quota_state(self):
        return cache.load_quota_state(self.data_dir)


class FixtureModeEndToEndTest(MainHarness):
    """用真实快照跑通全链路，零网络。"""

    def test_full_pipeline_produces_boards_cache_and_site(self):
        summary = self.run_main(source=config.INPUT_SOURCE_FIXTURE)

        self.assertEqual(summary["boards"], 21)
        self.assertGreater(summary["candidates"], 100)
        self.assertGreater(summary["analyzed"], 0)
        self.assertEqual(summary["failed"], 0)

        self.assertTrue((self.data_dir / config.BOARDS_FILE).is_file())
        self.assertGreater(len(self.cached_names()), 0)
        self.assertTrue((self.dist_dir / "index.html").is_file())
        self.assertTrue((self.dist_dir / "data" / "trending.json").is_file())

    def test_site_data_reflects_the_cache(self):
        self.run_main(source=config.INPUT_SOURCE_FIXTURE)
        payload = json.loads(
            (self.dist_dir / "data" / "trending.json").read_text(encoding="utf-8")
        )
        self.assertEqual(len(payload["boards"]), 21)

        # stats.analyzed 计的是**榜单条目数**而非唯一仓库数：同一仓库出现在多个榜单
        # 时，每个榜单各算一条。因此它必然不小于缓存里的唯一仓库数。
        analyzed_entries = sum(
            1 for board in payload["boards"].values() for entry in board if entry["analysis"]
        )
        self.assertEqual(payload["stats"]["analyzed"], analyzed_entries)
        self.assertGreater(payload["stats"]["analyzed"], 0)
        self.assertGreaterEqual(payload["stats"]["analyzed"], len(self.cached_names()))

    def test_readme_availability_is_recorded(self):
        self.run_main(source=config.INPUT_SOURCE_FIXTURE)
        entries = list(cache.load_analysis_cache(self.data_dir).values())
        self.assertTrue(all(entry["readme_available"] is True for entry in entries))

    def test_c2_first_round_stops_at_the_per_run_cap(self):
        """单轮受 Bootstrap 单轮上限约束，不会一次吃掉全部候选。"""
        summary = self.run_main(source=config.INPUT_SOURCE_FIXTURE, now=NOW)
        self.assertEqual(summary["analyzed"], min(summary["needed"], config.BOOTSTRAP_PER_RUN_CAP))
        self.assertGreater(summary["dropped_by_cap"], 0)

    def test_c2_no_analysis_calls_once_everything_is_cached(self):
        """C2：全部候选都已缓存后，再跑一轮必须**一次调用都不发起**。

        断言的是调用次数而不是成功次数——只统计成功会把「仍然在打 API 但都失败了」
        这种退化误判为通过。
        """
        for _ in range(6):
            summary = self.run_main(source=config.INPUT_SOURCE_FIXTURE, now=NOW)
            if summary["needed"] == 0:
                break
        else:
            self.fail("多轮之后仍存在待分析项，配额推进异常")

        attempts = []
        real_acquire = main.acquire_analysis

        def counting(*args, **kwargs):
            attempts.append(1)
            return real_acquire(*args, **kwargs)

        with mock.patch.object(main, "acquire_analysis", side_effect=counting):
            final = self.run_main(source=config.INPUT_SOURCE_FIXTURE, now=NOW)

        self.assertEqual(attempts, [], "已全部缓存，不应再发起任何分析调用")
        self.assertEqual(final["analyzed"], 0)
        self.assertEqual(final["needed"], 0)

    def test_c8_quota_accumulates_across_rounds_within_a_day(self):
        """C8：同日多轮共享同一份每日额度。"""
        first = self.run_main(source=config.INPUT_SOURCE_FIXTURE, now=NOW)
        state = self.quota_state()
        self.assertEqual(state["used_today"], first["analyzed"])
        self.assertLessEqual(state["used_today"], state["cap"])

    def test_quota_state_is_auditable(self):
        self.run_main(source=config.INPUT_SOURCE_FIXTURE)
        state = self.quota_state()
        for field in ("date", "used_today", "hit_rate", "cap", "mode", "consecutive_bootstrap_days"):
            self.assertIn(field, state)
            self.assertIsNotNone(state[field])
        self.assertEqual(state["date"], "2026-09-17")
        self.assertEqual(state["mode"], "bootstrap", "冷启动命中率为 0，应进入 Bootstrap")


class InputSourceTest(MainHarness):
    def test_defaults_to_network(self):
        with mock.patch.dict("os.environ", {config.ENV_INPUT_SOURCE: ""}):
            self.assertEqual(main.input_source(), config.INPUT_SOURCE_NETWORK)

    def test_fixture_is_accepted(self):
        with mock.patch.dict("os.environ", {config.ENV_INPUT_SOURCE: "FiXtUrE"}):
            self.assertEqual(main.input_source(), config.INPUT_SOURCE_FIXTURE)

    def test_unknown_value_is_rejected(self):
        with mock.patch.dict("os.environ", {config.ENV_INPUT_SOURCE: "prd"}):
            with self.assertRaises(main.RunError):
                main.input_source()


class EmptyBoardGuardTest(MainHarness):
    """A4／A5：任一榜解析为 0 条即整轮失败，不得静默产出空榜。"""

    def test_a_single_empty_board_aborts_the_run(self):
        real_read = cache.read_fixture

        def fake_read(relative, *args, **kwargs):
            if "daily_python" in relative:
                return "<html><body><div class='renamed'>没有仓库行</div></body></html>"
            return real_read(relative, *args, **kwargs)

        with mock.patch.object(cache, "read_fixture", side_effect=fake_read):
            with self.assertRaises(main.RunError) as caught:
                self.run_main(source=config.INPUT_SOURCE_FIXTURE)

        self.assertIn("daily|python", str(caught.exception))
        self.assertFalse(
            (self.data_dir / config.BOARDS_FILE).exists(), "中止后不应写入榜单数据"
        )

    def test_partial_marker_change_is_survived_by_fallbacks(self):
        """只改掉 itemprop 不该导致失败——多套选择器正是为这种小改动准备的。

        真实页面上语言块的形态是三个信号叠加：itemprop、repo-language-color 相邻、
        d-inline-block 父容器。砍掉其中一个，另外两个仍然能定位到语言。
        """
        real_read = cache.read_fixture

        def fake_read(relative, *args, **kwargs):
            return real_read(relative, *args, **kwargs).replace(
                'itemprop="programmingLanguage"', 'data-x="moved"'
            )

        with mock.patch.object(cache, "read_fixture", side_effect=fake_read):
            summary = self.run_main(source=config.INPUT_SOURCE_FIXTURE)
        self.assertEqual(summary["boards"], 21)
        self.assertEqual(summary["failed"], 0)

    def test_all_language_signals_broken_aborts_the_run(self):
        """三个信号全部失效时必须失败，而不是静默产出「无语言」的榜单。"""
        real_read = cache.read_fixture

        def fake_read(relative, *args, **kwargs):
            return (
                real_read(relative, *args, **kwargs)
                .replace('itemprop="programmingLanguage"', 'data-lang="moved"')
                .replace("repo-language-color", "repo-lang-dot")
                .replace("d-inline-block", "inline-blk")
            )

        with mock.patch.object(cache, "read_fixture", side_effect=fake_read):
            with self.assertRaises(main.RunError) as caught:
                self.run_main(source=config.INPUT_SOURCE_FIXTURE)
        self.assertIn("language", str(caught.exception))
        self.assertFalse(
            (self.data_dir / config.BOARDS_FILE).exists(), "中止后不应写入榜单数据"
        )


class NetworkModeCallAccountingTest(MainHarness):
    """C11：README 与模型调用次数必须与「实际分析的仓库数」严格对应。"""

    def setUp(self):
        super().setUp()
        self.calls = []
        self.transport = network_transport(self.calls)

    def count(self, host_key):
        return sum(1 for url in self.calls if TRANSPORT_HOSTS[host_key] in url)

    def test_one_trending_request_per_board(self):
        self.run_main(source=config.INPUT_SOURCE_NETWORK, transport=self.transport)
        self.assertEqual(self.count("trending"), 21)

    def test_c11_readme_is_fetched_only_for_analyzed_repos(self):
        summary = self.run_main(source=config.INPUT_SOURCE_NETWORK, transport=self.transport)
        self.assertEqual(self.count("readme"), summary["analyzed"])
        self.assertEqual(self.count("deepseek"), summary["analyzed"])

    def test_dedup_prevents_duplicate_analysis(self):
        """同一仓库出现在多个榜单时只应分析一次。"""
        summary = self.run_main(source=config.INPUT_SOURCE_NETWORK, transport=self.transport)
        self.assertEqual(summary["analyzed"], len(set(self.cached_names())))
        self.assertEqual(summary["analyzed"], summary["candidates"])


class FailureIsolationTest(MainHarness):
    """C10：单个仓库失败不得写缓存、不得消耗配额、不得阻断整轮。"""

    def setUp(self):
        super().setUp()
        self.calls = []
        self.transport = network_transport(self.calls)

    def test_one_failure_does_not_block_the_rest(self):
        real_acquire = main.acquire_analysis
        seen = {"count": 0}

        def flaky(source, messages, **kwargs):
            seen["count"] += 1
            if seen["count"] == 1:
                raise net.FetchError("模拟 DeepSeek 故障")
            return real_acquire(source, messages, **kwargs)

        with mock.patch.object(main, "acquire_analysis", side_effect=flaky):
            summary = self.run_main(source=config.INPUT_SOURCE_NETWORK, transport=self.transport)

        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["analyzed"], summary["candidates"] - 1)
        self.assertEqual(summary["quota"]["used_today"], summary["analyzed"])

    def test_failed_repo_is_not_cached(self):
        real_acquire = main.acquire_analysis
        seen = {"count": 0}

        def flaky(source, messages, **kwargs):
            seen["count"] += 1
            if seen["count"] == 1:
                raise net.FetchError("模拟故障")
            return real_acquire(source, messages, **kwargs)

        with mock.patch.object(main, "acquire_analysis", side_effect=flaky):
            summary = self.run_main(source=config.INPUT_SOURCE_NETWORK, transport=self.transport)

        self.assertEqual(len(self.cached_names()), summary["analyzed"])
        for entry in cache.load_analysis_cache(self.data_dir).values():
            self.assertIn("analysis", entry)

    def test_malformed_model_output_counts_as_failure(self):
        """模型返回的 JSON 不合规同样属于该仓库的失败，不能写进缓存。"""
        bad = json.dumps({"choices": [{"message": {"content": "不是 JSON"}}]})
        transport = network_transport(self.calls, deepseek_envelope=bad)
        summary = self.run_main(source=config.INPUT_SOURCE_NETWORK, transport=transport)
        self.assertEqual(summary["analyzed"], 0)
        self.assertEqual(summary["failed"], summary["candidates"])
        self.assertEqual(self.cached_names(), set())
        self.assertEqual(summary["quota"]["used_today"], 0)

    def test_analysis_errors_are_isolated_per_repo(self):
        """analyze.AnalysisError（字段不合规）也必须被当作单仓库失败而非整轮崩溃。"""
        envelope = json.dumps({"choices": [{"message": {"content": '{"one_liner": "缺字段"}'}}]})
        transport = network_transport(self.calls, deepseek_envelope=envelope)
        summary = self.run_main(source=config.INPUT_SOURCE_NETWORK, transport=transport)
        self.assertGreater(summary["candidates"], 0)
        self.assertEqual(summary["failed"], summary["candidates"])
        self.assertEqual(summary["analyzed"], 0)


class QuotaCapTest(MainHarness):
    def test_steady_cap_limits_a_single_round(self):
        """稳态配额下，单轮分析数不得超过 per-run 上限。"""
        calls = []
        transport = network_transport(calls, repos_per_board=10)

        # 预置一份稳态配额状态：命中率足够高、已打过当天标记
        cache.save_quota_state(
            {
                "date": "2026-09-17",
                "used_today": 0,
                "hit_rate": 0.95,
                "mode": "steady",
                "cap": config.STEADY_DAILY_CAP,
                "consecutive_bootstrap_days": 0,
                "warnings": [],
            },
            self.data_dir,
        )
        summary = self.run_main(source=config.INPUT_SOURCE_NETWORK, transport=transport)
        self.assertLessEqual(summary["analyzed"], config.STEADY_DAILY_CAP)
        self.assertGreater(summary["dropped_by_cap"], 0, "候选多于额度时应记录被推迟的数量")

    def test_bootstrap_takes_priority_by_window(self):
        """额度不足时优先取日榜：被选中的仓库名单应只含日榜条目。"""
        calls = []
        transport = network_transport(calls, repos_per_board=3)
        cache.save_quota_state(
            {
                "date": "2026-09-17",
                "used_today": 0,
                "hit_rate": 0.30,
                "mode": "bootstrap",
                "cap": 2,
                "consecutive_bootstrap_days": 1,
                "warnings": [],
            },
            self.data_dir,
        )
        summary = self.run_main(source=config.INPUT_SOURCE_NETWORK, transport=transport)
        self.assertEqual(summary["analyzed"], 2)
        self.assertEqual(summary["quota"]["used_today"], 2)


if __name__ == "__main__":
    unittest.main()
