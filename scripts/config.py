"""集中常量：语言维度、时间窗口、路径、阈值、配额参数。

本模块不依赖任何其他模块。
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = PROJECT_ROOT / "data"
DIST_DIR = PROJECT_ROOT / "dist"
WEB_DIR = PROJECT_ROOT / "web"
FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures"

BOARDS_FILE = "boards.json"
ANALYSIS_CACHE_FILE = "analysis_cache.json"
QUOTA_FILE = "quota.json"

TIME_WINDOWS = ("daily", "weekly", "monthly")

WINDOW_LABELS = {"daily": "日榜", "weekly": "周榜", "monthly": "月榜"}

# 榜单在前端数据与 boards.json 中的键分隔符
BOARD_KEY_SEPARATOR = "|"


def board_key(window: str, language: str) -> str:
    """榜单键。放在 config 里是因为采集侧与渲染侧都要用它，而两侧不应互相 import。"""
    return f"{window}{BOARD_KEY_SEPARATOR}{language}"


def repo_key(full_name: str) -> str:
    """去重键。GitHub 的 owner/repo 大小写不敏感，统一小写以避免重复分析。

    与 board_key 一样放在 config：采集侧、渲染侧与配额侧都要用它，而这些模块之间
    不应互相 import。
    """
    return (full_name or "").strip().lower()


def parse_board_key(key: str) -> tuple[str, str]:
    window, _, language = key.partition(BOARD_KEY_SEPARATOR)
    return window, language

# 配额不足时按此优先级选取候选，小者优先（§2.4）
WINDOW_PRIORITY = {"daily": 0, "weekly": 1, "monthly": 2}

# 空字符串代表 All Languages 维度，与 GitHub 的 URL 形态一致。
# 界面为中文，故该维度的标签取中文；语言名本身是专有名词，保持原样。
LANGUAGES = {
    "": "全部",
    "python": "Python",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "go": "Go",
    "rust": "Rust",
    "java": "Java",
}

MAX_REPOS_PER_BOARD = 25

# 结构不变量容差（§4.8.2）。language 的容差刻意远高于其他字段——部分仓库确实没有
# 可识别的主语言，这是正常现象而非上游改版，用统一的 5% 会持续误报。
FIELD_MISS_TOLERANCE = {
    "name": 0.0,
    "url": 0.0,
    "stars": 0.05,
    "language": 0.20,
}

PROMPT_VERSION = "v1"
DEEPSEEK_MODEL = "deepseek-chat"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_TEMPERATURE = 0.3
DEEPSEEK_MAX_TOKENS = 1500
DEEPSEEK_TIMEOUT_SECONDS = 60
DEEPSEEK_RETRIES = 2

README_MAX_CHARS = 8000
TRENDING_TIMEOUT_SECONDS = 20
README_TIMEOUT_SECONDS = 20
HTTP_RETRIES = 2
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# 缓存在此天数内不重跑（§2.4 第 4 条）
CACHE_TTL_DAYS = 7
# Star 变动超过绝对值或比例阈值即重跑，两者取或（§2.4 第 5 条）
STAR_DELTA_ABSOLUTE = 5000
STAR_DELTA_RATIO = 0.50

STEADY_DAILY_CAP = 50
BOOTSTRAP_DAILY_CAP = 400
BOOTSTRAP_PER_RUN_CAP = 200
# 命中率低于此值进入 Bootstrap，高于另一阈值才退出，中间维持上一轮（滞回）
HIT_RATE_BOOTSTRAP_ENTER = 0.60
HIT_RATE_STEADY_EXIT = 0.90
# 连续满足 Bootstrap 条件超过此天数即熔断，强制回落稳态配额
BOOTSTRAP_FUSE_DAYS = 3

FIXTURE_TENDING_SUBDIR = "trending"
FIXTURE_README_SUBDIR = "readme"
FIXTURE_DEEPSEEK_SUBDIR = "deepseek"

# fixture 模式下使用的样本输入。DeepSeek 响应属「手工构造、不参与漂移检测」一类
# （§4.8.3），因此可以固定用同一份来驱动离线跑通全链路。
FIXTURE_README_SAMPLE = "readme/sample.md"
FIXTURE_DEEPSEEK_RESPONSE = "deepseek/bare.txt"

ENV_DEEPSEEK_API_KEY = "DEEPSEEK_API_KEY"
ENV_INPUT_SOURCE = "INPUT_SOURCE"
ENV_REFRESH_FIXTURES = "REFRESH_FIXTURES"
ENV_STEP_SUMMARY = "GITHUB_STEP_SUMMARY"

INPUT_SOURCE_NETWORK = "network"
INPUT_SOURCE_FIXTURE = "fixture"
