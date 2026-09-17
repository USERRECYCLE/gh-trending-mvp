"""GitHub Trending HTML 的纯解析（§2.10.3）。

本模块不联网、不读文件、不判断运行模式（E16）。输入是 HTML 字符串，输出是仓库
列表——网络与文件读取都在 main.py 完成，因此本模块可用 fixture 完全离线测试。
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

import config

# 每个字段都配多套选择器：GitHub 调整标记时先靠后备选择器兜住，兜不住则由
# §4.8.2 的字段缺失率断言暴露，而不是静默产出空值。
DESCRIPTION_SELECTORS = ("p.col-9", "p.color-fg-muted", "div.Box-row > p")
LANGUAGE_SELECTORS = (
    'span[itemprop="programmingLanguage"]',
    "span.repo-language-color + span",
    "span.d-inline-block > span:not(.repo-language-color)",
)

_STARS_SUFFIX = "/stargazers"
_FORKS_SUFFIX = "/forks"
_HREF_RE = re.compile(r"^/([^/]+)/([^/]+?)/?$")
_ABBREVIATED_RE = re.compile(r"^([\d.,]+)\s*([kKmM])$")
# GitHub 用逗号或各类 Unicode 空白作千位分隔符。只删夹在数字之间的空白，
# 这样 "1 002" 归一为 1002，而 "3 forks 42 stars" 不会被错误合并。
_DIGIT_GAP_RE = re.compile(r"(?<=\d)\s(?=\d)")
_DIGITS_RE = re.compile(r"([\d][\d,.]*)")


def fixture_relative_path(window: str, language: str) -> str:
    """榜单快照在 tests/fixtures/ 下的相对路径。language 为空串表示总榜。"""
    return f"{config.FIXTURE_TENDING_SUBDIR}/{window}_{language or 'all'}.html"


def to_int(text: str | None) -> int | None:
    """把星标文本转成整数。'17,010' → 17010，'1.2k' → 1200。无法解析返回 None。"""
    if text is None:
        return None
    # GitHub 用不间断空格（U+00A0）分隔数字，先归一化再解析
    cleaned = _DIGIT_GAP_RE.sub("", text).strip()
    if not cleaned:
        return None
    abbreviated = _ABBREVIATED_RE.match(cleaned)
    if abbreviated:
        number = float(abbreviated.group(1).replace(",", ""))
        scale = 1000 if abbreviated.group(2).lower() == "k" else 1_000_000
        return int(number * scale)
    digits = _DIGITS_RE.search(cleaned.replace(",", ""))
    if not digits:
        return None
    try:
        return int(float(digits.group(1).replace(",", "")))
    except ValueError:
        return None


def _first_text(article, selectors) -> str:
    for selector in selectors:
        found = article.select_one(selector)
        if found is not None:
            text = found.get_text(" ", strip=True)
            if text:
                return text
    return ""


def _link_count(article, suffix: str) -> int | None:
    for anchor in article.select(f'a[href$="{suffix}"]'):
        value = to_int(anchor.get_text(" ", strip=True))
        if value is not None:
            return value
    return None


def _period_added_stars(article) -> int | None:
    badge = article.select_one("span.float-sm-right")
    if badge is not None:
        value = to_int(badge.get_text(" ", strip=True))
        if value is not None:
            return value
    # 后备：直接在文本里找「N stars today / this week / this month」
    match = re.search(
        r"([\d][\d,.]*)\s+stars?\s+(?:today|this\s+(?:week|month))",
        article.get_text(" ", strip=True),
        re.IGNORECASE,
    )
    return to_int(match.group(1)) if match else None


def parse_article(article) -> dict | None:
    """解析单个 <article>。取不到 owner/repo 的条目直接丢弃——那多半不是仓库行，
    丢弃数量会在 diagnose_html 的 articles_found 与 parsed 之差中体现。"""
    anchor = article.select_one("h2 a[href]")
    if anchor is None:
        return None
    match = _HREF_RE.match((anchor.get("href") or "").strip())
    if not match:
        return None

    owner, name = match.group(1), match.group(2)
    return {
        "name": f"{owner}/{name}",
        "url": f"https://github.com/{owner}/{name}",
        "description": _first_text(article, DESCRIPTION_SELECTORS),
        "language": _first_text(article, LANGUAGE_SELECTORS),
        "stars": _link_count(article, _STARS_SUFFIX),
        "forks": _link_count(article, _FORKS_SUFFIX),
        "add_stars": _period_added_stars(article),
    }


def parse_trending_html(html: str) -> list[dict]:
    """解析 GitHub Trending 页面，返回仓库列表。"""
    soup = BeautifulSoup(html, "html.parser")
    repos = []
    for article in soup.select("article.Box-row"):
        parsed = parse_article(article)
        if parsed is not None:
            repos.append(parsed)
    return repos


# description 与 language 都可能合法为空（仓库可以没有简介，也可以没有可识别的主
# 语言），因此「报告」的字段比「断言」的字段多：全部字段都算缺失率供人查看，但只有
# config.FIELD_MISS_TOLERANCE 中列出的字段参与失败判定。
TRACKED_FIELDS = ("name", "url", "stars", "language", "description")

_REPO_URL_RE = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+$")


def field_miss_rates(repos) -> dict:
    """各字段的缺失率（§4.8.2）。比「条数 > 0」更敏感：上游只挪动某个字段的
    DOM 位置时条数可能仍然正常，只有缺失率会暴露问题。"""
    if not repos:
        return {field: 1.0 for field in TRACKED_FIELDS}
    rates = {}
    for field in TRACKED_FIELDS:
        missing = sum(1 for repo in repos if repo.get(field) in (None, ""))
        rates[field] = missing / len(repos)
    return rates


def invariant_failures(repos, tolerances=None, max_per_board=None) -> list[str]:
    """检查 §4.8.2 的结构不变量，返回问题描述列表。空列表表示通过。

    断言的是结构而非内容：榜单内容每小时都在变，只有结构性质对上游改版敏感。
    """
    tolerances = config.FIELD_MISS_TOLERANCE if tolerances is None else tolerances
    limit = config.MAX_REPOS_PER_BOARD if max_per_board is None else max_per_board

    if not repos:
        return ["解析结果为 0 条（上游改版的首要症状）"]

    problems = []
    if len(repos) > limit:
        problems.append(f"条数 {len(repos)} 超过上限 {limit}")

    rates = field_miss_rates(repos)
    for field, tolerance in tolerances.items():
        if rates.get(field, 1.0) > tolerance:
            problems.append(
                f"字段 {field} 缺失率 {rates[field]:.1%} 超过容差 {tolerance:.1%}（选择器可能已失效）"
            )

    bad = [repo.get("url", "") for repo in repos if not _REPO_URL_RE.match(repo.get("url") or "")]
    if bad:
        problems.append(f"{len(bad)} 条 url 格式异常，例如 {bad[0]!r}")

    return problems


def diagnose_html(html: str, repos=None) -> dict:
    """供 online-smoke 输出结构化诊断：命中的选择器、条数、字段缺失率。"""
    soup = BeautifulSoup(html, "html.parser")
    parsed = parse_trending_html(html) if repos is None else repos
    return {
        "articles_found": len(soup.select("article.Box-row")),
        "parsed": len(parsed),
        "has_stargazers_link": bool(soup.select('a[href$="/stargazers"]')),
        "has_forks_link": bool(soup.select('a[href$="/forks"]')),
        "has_language_itemprop": bool(soup.select('span[itemprop="programmingLanguage"]')),
        "has_period_badge": bool(soup.select("span.float-sm-right")),
        "field_miss_rates": field_miss_rates(parsed),
    }
