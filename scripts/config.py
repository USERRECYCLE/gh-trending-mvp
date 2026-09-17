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

# 配额不足时按此优先级选取候选，小者优先（§2.4）
WINDOW_PRIORITY = {"daily": 0, "weekly": 1, "monthly": 2}

# 空字符串代表 All Languages 维度，与 GitHub 的 URL 形态一致
LANGUAGES = {
    "": "All Languages",
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

ENV_DEEPSEEK_API_KEY = "DEEPSEEK_API_KEY"
ENV_INPUT_SOURCE = "INPUT_SOURCE"
ENV_REFRESH_FIXTURES = "REFRESH_FIXTURES"
ENV_STEP_SUMMARY = "GITHUB_STEP_SUMMARY"

INPUT_SOURCE_NETWORK = "network"
INPUT_SOURCE_FIXTURE = "fixture"
