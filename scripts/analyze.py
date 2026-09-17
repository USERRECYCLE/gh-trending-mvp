"""AI 分析的纯逻辑：构建 prompt 与解析模型响应（§2.10.3）。

不联网、不读文件、不判断运行模式（E16）。调用 DeepSeek 属于 net.py 的职责，持久化
属于 cache.py，本模块只做「数据进、数据出」，因此可用 fixture 完全离线测试。
"""

from __future__ import annotations

import json
import re

import config

SCORE_KEYS = ("innovation", "practicality", "learning_value")
FEATURE_MIN = 3
FEATURE_MAX = 5
ONE_LINER_MAX_CHARS = 60

READMISSING_PLACEHOLDER = "（未获取到 README，请仅依据以上元数据谨慎推断，不要编造细节）"

SYSTEM_PROMPT = (
    "你是一名资深软件工程师，擅长快速判断开源项目的价值。"
    "你只输出 JSON，不输出任何解释、前后缀或 Markdown 代码块。"
)

PROMPT_TEMPLATE = """请分析下面的开源项目，并输出一个 JSON 对象。

## 输出结构（严格遵守）
{{
  "one_liner": "一句话总结项目是做什么的，不超过 {one_liner_max} 字",
  "core_features": ["核心功能点，{feature_min}-{feature_max} 条"],
  "tech_stack": ["主要框架与工具"],
  "highlights": "最值得关注的地方",
  "target_audience": "适合哪些开发者",
  "scores": {{
    "innovation": 1到5的整数,
    "practicality": 1到5的整数,
    "learning_value": 1到5的整数
  }}
}}

## 评分口径
- innovation：创新性
- practicality：实用性
- learning_value：学习价值
三者均为 1-5 的整数，不要写成字符串或小数。

## 项目信息
- 仓库：{name}
- 官方描述：{description}
- 主要语言：{language}
- 总 Star 数：{stars}
- 周期内新增 Star：{add_stars}
- Fork 数：{forks}

## README
{readme}

## 约束
1. 所有文本字段一律使用简体中文。
2. 只依据上述信息作答，不要编造 README 与描述中没有依据的内容。
3. 不要在任何字段中插入 URL。
4. 除 JSON 外不要输出任何字符。

prompt_version={prompt_version}"""


class AnalysisError(RuntimeError):
    """模型输出无法通过校验。

    调用方应据此跳过该仓库：不写缓存、不计入配额消耗（C10）。
    """


def _field(value) -> str:
    if value is None or value == "":
        return "未知"
    return str(value)


def build_prompt(repo: dict, readme: str | None = None, prompt_version: str | None = None) -> str:
    """构建分析 prompt。同一输入必须产出逐字节相同的结果（B5）。

    因此本函数不得引入时间戳、随机数或依赖字典遍历顺序的内容。
    """
    version = config.PROMPT_VERSION if prompt_version is None else prompt_version
    readme_text = (readme or "").strip()
    if readme_text:
        readme_block = readme_text[: config.README_MAX_CHARS]
    else:
        readme_block = READMISSING_PLACEHOLDER

    return PROMPT_TEMPLATE.format(
        one_liner_max=ONE_LINER_MAX_CHARS,
        feature_min=FEATURE_MIN,
        feature_max=FEATURE_MAX,
        name=_field(repo.get("name")),
        description=_field(repo.get("description")),
        language=_field(repo.get("language")),
        stars=_field(repo.get("stars")),
        add_stars=_field(repo.get("add_stars")),
        forks=_field(repo.get("forks")),
        readme=readme_block,
        prompt_version=version,
    )


def extract_json(text: str | None) -> str:
    """从模型响应中取出 JSON 对象文本。

    模型常把 JSON 包在 Markdown 围栏里，或在前后加一句客套话，因此先剥围栏、
    再取最外层大括号区间。取错时由 json.loads 报错，不静默兜底。
    """
    if text is None:
        raise AnalysisError("响应为空")
    stripped = text.strip()
    if not stripped:
        raise AnalysisError("响应为空")

    fence = re.search(r"```(?:json)?\s*(.+?)```", stripped, re.DOTALL | re.IGNORECASE)
    if fence:
        stripped = fence.group(1).strip()

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end <= start:
        raise AnalysisError("响应中找不到完整的 JSON 对象")
    return stripped[start : end + 1]


def _clean_text(value) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _clean_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _coerce_score(value):
    """接受整数；也接受 4.0 这类取值为整的浮点——模型偶尔会这么写，为此丢弃一次
    调用不划算。布尔值是 int 的子类，必须显式排除。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 1 <= value <= 5 else None
    if isinstance(value, float) and value.is_integer():
        return int(value) if 1 <= value <= 5 else None
    return None


def validate_analysis(payload) -> dict:
    """校验并归一化模型输出。任何不合规都抛 AnalysisError 并列出全部问题。"""
    if not isinstance(payload, dict):
        raise AnalysisError("顶层不是 JSON 对象")

    problems: list[str] = []

    one_liner = _clean_text(payload.get("one_liner"))
    if not one_liner:
        problems.append("one_liner 缺失或为空")
    elif len(one_liner) > ONE_LINER_MAX_CHARS:
        problems.append(f"one_liner 超长（{len(one_liner)} > {ONE_LINER_MAX_CHARS} 字）")

    features = _clean_list(payload.get("core_features"))
    if not FEATURE_MIN <= len(features) <= FEATURE_MAX:
        problems.append(
            f"core_features 应为 {FEATURE_MIN}-{FEATURE_MAX} 条，实际 {len(features)} 条"
        )

    tech_stack = _clean_list(payload.get("tech_stack"))
    if not tech_stack:
        problems.append("tech_stack 缺失或为空")

    highlights = _clean_text(payload.get("highlights"))
    if not highlights:
        problems.append("highlights 缺失或为空")

    audience = _clean_text(payload.get("target_audience"))
    if not audience:
        problems.append("target_audience 缺失或为空")

    scores_raw = payload.get("scores")
    scores: dict[str, int] = {}
    if not isinstance(scores_raw, dict):
        problems.append("scores 缺失或不是对象")
    else:
        for key in SCORE_KEYS:
            coerced = _coerce_score(scores_raw.get(key))
            if coerced is None:
                problems.append(f"scores.{key} 必须是 1-5 的整数，实际 {scores_raw.get(key)!r}")
            else:
                scores[key] = coerced

    if problems:
        raise AnalysisError("；".join(problems))

    return {
        "one_liner": one_liner,
        "core_features": features,
        "tech_stack": tech_stack,
        "highlights": highlights,
        "target_audience": audience,
        "scores": scores,
    }


def parse_analysis_response(text: str | None) -> dict:
    """解析并校验模型响应。失败一律抛 AnalysisError，绝不返回半成品。"""
    raw = extract_json(text)
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise AnalysisError(f"JSON 解析失败：{exc}") from exc
    return validate_analysis(payload)


# 承载叙述的字段。tech_stack 被排除在语言检查之外——它装的是框架与工具名
# （Python、DuckDB 之类），本来就几乎全是拉丁字符，对它算中文占比没有意义。
PROSE_FIELDS = ("one_liner", "highlights", "target_audience")


def iter_prose(analysis: dict):
    """只遍历叙述性字段，供 B3 的语言检查使用。"""
    for field in PROSE_FIELDS:
        yield analysis.get(field, "")


def iter_texts(analysis: dict):
    """遍历一份分析结果中的所有文本片段，供 URL 检查复用（URL 可能出现在任何字段）。"""
    yield analysis.get("one_liner", "")
    yield analysis.get("highlights", "")
    yield analysis.get("target_audience", "")
    for item in analysis.get("core_features", []):
        yield item
    for item in analysis.get("tech_stack", []):
        yield item


def chinese_ratio(text: str | None) -> float:
    """中文字符占非空白字符的比例。"""
    if not text:
        return 0.0
    chars = [char for char in text if not char.isspace()]
    if not chars:
        return 0.0
    han = sum(1 for char in chars if CJK_START <= char <= CJK_END)
    return han / len(chars)


# CJK 统一表意文字区间（U+4E00-U+9FFF），用码点表达以保证源码为纯 ASCII
CJK_START, CJK_END = chr(0x4E00), chr(0x9FFF)

# 用「允许出现的 URL 字符」正向匹配，而不是排除空白。中文常紧贴 URL 出现且无空格
# （如「参考 https://x.test/a，以及…」），若用排除法会把后面的中文一并吞进结果。
_URL_RE = re.compile(r"https?://[A-Za-z0-9\-._~:/?#@!$&'()*+,;=%\[\]]+")
_TRAILING_PUNCTUATION = ".,;:)"


def find_urls(text: str | None) -> list[str]:
    return [url.rstrip(_TRAILING_PUNCTUATION) for url in _URL_RE.findall(text or "")]
