"""需要真实浏览器的验收（E2、E3、E4）。

用 Chromium 系浏览器的 --dump-dom 取回渲染后的序列化 DOM。两个必须注意的点：

1. 必须用 --headless 而非 --headless=new：后者不遵守 --virtual-time-budget，
   页面里的异步 fetch 不会完成，dump 出来的是未渲染状态。
2. 页面通过本地 HTTP 提供：file:// 下 fetch 会被 CORS 拦掉，测不到真实加载路径。

浏览器需自备（优先 BROWSER_BIN 环境变量，其次探测常见安装位置）。找不到则整体
跳过并说明原因，而不是伪装通过。
"""

import os
import subprocess
import sys
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import cache  # noqa: E402
import config  # noqa: E402
import fetch  # noqa: E402
import render  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures"

# 本地提供数据，1 秒虚拟时间足够让 fetch 与首屏渲染完成
VIRTUAL_TIME_MS = 1000
DUMP_TIMEOUT = 60

# 本模块每个用例都要拉起一次浏览器进程，全量约 1 分钟。本地快速迭代时设
# SKIP_BROWSER_TESTS=1 跳过，CI 与发版前必须完整跑。
SKIP_REASON = ""
if os.environ.get("SKIP_BROWSER_TESTS") == "1":
    SKIP_REASON = "SKIP_BROWSER_TESTS=1，已按需跳过浏览器验收"

BROWSER_CANDIDATES = (
    os.environ.get("BROWSER_BIN"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)


def find_browser():
    for candidate in BROWSER_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


BROWSER = find_browser()


def sample_analysis():
    import json

    return json.loads(
        (FIXTURE_DIR / config.FIXTURE_DEEPSEEK_SUBDIR / "bare.txt").read_text(encoding="utf-8")
    )


def build_site(with_analysis: bool):
    """用真实快照构建一份站点数据。with_analysis=False 时全部走降级分支。"""
    parsed = {}
    for window in config.TIME_WINDOWS:
        for language in config.LANGUAGES:
            html = (FIXTURE_DIR / fetch.fixture_relative_path(window, language)).read_text(
                encoding="utf-8"
            )
            parsed[config.board_key(window, language)] = fetch.parse_trending_html(html)

    boards = fetch.build_boards(parsed, "2026-09-17T07:30:00+00:00")
    analyses = {}
    if with_analysis:
        for repo in boards["boards"][config.board_key("daily", "")][:5]:
            analyses[cache.repo_key(repo["name"])] = {
                "analysis": sample_analysis(),
                "stars_at_analysis": repo["stars"],
                "prompt_version": config.PROMPT_VERSION,
                "model": config.DEEPSEEK_MODEL,
                "analyzed_at": "2026-09-17T07:30:00+00:00",
                "readme_available": True,
            }
    return render.build_site_data(boards, analyses, "2026-09-17T07:30:00+00:00")


class QuietHandler(SimpleHTTPRequestHandler):
    """静音访问日志：几十次页面加载会把测试输出淹掉。"""

    def log_message(self, *args):
        pass


@unittest.skipIf(BROWSER is None, "未找到 Chromium 系浏览器；设置 BROWSER_BIN 后重跑")
@unittest.skipIf(bool(SKIP_REASON), SKIP_REASON)
class BrowserHarness(unittest.TestCase):
    """起一个本地静态服务，把 with-ai 与 no-ai 两份站点挂在同一端口下。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = TemporaryDirectory()
        root = Path(cls._tmp.name)
        cls.with_ai = root / "with-ai"
        cls.no_ai = root / "no-ai"
        cls.broken = root / "broken"

        render.render_dist(build_site(True), WEB_DIR, cls.with_ai)
        render.render_dist(build_site(False), WEB_DIR, cls.no_ai)

        # 负向对照：站点文件齐全，但数据文件缺失。用于验证「横幅不可见」这个断言
        # 真的能失败——否则 E2 是个永远通过的假验收。
        render.render_dist(build_site(True), WEB_DIR, cls.broken)
        (cls.broken / "data" / "trending.json").unlink()

        handler = partial(QuietHandler, directory=str(root))
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls._tmp.cleanup()

    def dump(self, site, hash_path=""):
        url = f"http://127.0.0.1:{self.port}/{site}/index.html{hash_path}"
        proc = subprocess.run(
            [
                BROWSER,
                "--headless",
                "--disable-gpu",
                "--no-sandbox",
                "--disable-extensions",
                f"--virtual-time-budget={VIRTUAL_TIME_MS}",
                "--dump-dom",
                url,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=DUMP_TIMEOUT,
        )
        return proc.stdout or ""

    def parse(self, dom):
        soup = BeautifulSoup(dom, "html.parser")
        banner = soup.select_one("#error-banner")
        return {
            "soup": soup,
            "cards": soup.select("article.card"),
            "pending": soup.select(".ai--pending"),
            "with_analysis": soup.select(".ai:not(.ai--pending)"),
            "banner_visible": banner is not None and not banner.has_attr("hidden"),
            "banner_text": banner.get_text(strip=True) if banner else "",
            "selected_tab": soup.select_one('.tab[aria-selected="true"]'),
            "pressed_chip": soup.select_one('.chip[aria-pressed="true"]'),
        }


class E2NoRuntimeErrorTest(BrowserHarness):
    """E2：加载页面不得出现运行期异常。

    未捕获错误会被 app.js 的全局处理写成可见横幅，因此「横幅不可见」同时覆盖了
    脚本报错与数据加载失败两种情况。
    """

    def test_default_view_has_no_error_banner(self):
        state = self.parse(self.dump("with-ai"))
        self.assertFalse(
            state["banner_visible"], f"页面出现错误横幅：{state['banner_text']}"
        )

    def test_error_banner_honesty_check(self):
        """负向对照：数据文件缺失时横幅必须出现。

        没有这条，前一条断言可能在横幅机制失效的情况下依然通过。
        """
        state = self.parse(self.dump("broken"))
        self.assertTrue(
            state["banner_visible"], "横幅机制失效，E2 的断言会变成永远通过"
        )
        self.assertIn("加载失败", state["banner_text"])


class E3AllViewsRenderTest(BrowserHarness):
    """E3：21 个「时间 × 语言」组合都必须渲染出卡片列表。"""

    def test_every_view_renders_cards(self):
        failures = []
        for window in config.TIME_WINDOWS:
            for language in config.LANGUAGES:
                view = f"{window}/{language or 'all'}"
                state = self.parse(self.dump("with-ai", f"#/{window}/{language}"))
                if state["banner_visible"]:
                    failures.append(f"{view}: 出现错误横幅 {state['banner_text']}")
                if len(state["cards"]) < 1:
                    failures.append(f"{view}: 卡片数为 0")
        self.assertEqual(failures, [], f"以下视图未通过：{failures}")

    def test_selection_follows_the_route(self):
        state = self.parse(self.dump("with-ai", "#/weekly/go"))
        self.assertEqual(state["selected_tab"].get_text(strip=True), "周榜")
        self.assertEqual(state["pressed_chip"].get_text(strip=True), "Go")

    def test_board_switch_changes_the_cards(self):
        daily = self.parse(self.dump("with-ai", "#/daily/go"))
        monthly = self.parse(self.dump("with-ai", "#/monthly/go"))
        daily_repos = {c.get("data-repo") for c in daily["cards"]}
        monthly_repos = {c.get("data-repo") for c in monthly["cards"]}
        self.assertNotEqual(daily_repos, monthly_repos, "切换时间范围后卡片未变化")

    def test_unknown_view_falls_back_without_error(self):
        """未知路由不应崩溃；至少横幅不能出现。"""
        state = self.parse(self.dump("with-ai", "#/nonsense/whatever"))
        self.assertFalse(state["banner_visible"], state["banner_text"])


class E4DegradedRenderingTest(BrowserHarness):
    """E4：无 AI 解读的仓库必须正常渲染并显示占位，不得留白或报错。"""

    def test_cards_without_analysis_show_placeholder(self):
        state = self.parse(self.dump("no-ai"))
        self.assertFalse(state["banner_visible"], state["banner_text"])
        self.assertGreater(len(state["cards"]), 0)
        self.assertEqual(len(state["pending"]), len(state["cards"]), "所有卡片都应为待生成状态")
        self.assertIn("解读生成中", state["pending"][0].get_text(strip=True))

    def test_degraded_cards_still_show_base_info(self):
        state = self.parse(self.dump("no-ai"))
        card = state["cards"][0]
        self.assertTrue(card.select_one(".card__name").get_text(strip=True))
        self.assertTrue(card.select_one(".card__meta").get_text(strip=True))
        self.assertTrue(card.select_one(".card__rank").get_text(strip=True))

    def test_mixed_board_shows_both_states(self):
        state = self.parse(self.dump("with-ai"))
        self.assertGreater(len(state["with_analysis"]), 0)
        self.assertGreater(len(state["pending"]), 0)


if __name__ == "__main__":
    unittest.main()
