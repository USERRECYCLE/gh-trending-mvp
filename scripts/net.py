"""全部出网调用：集中超时、重试与指数退避。

本模块不判断运行模式（E16）——走网络还是读 fixture 由 main.py 决定，它只负责
「给定请求，返回响应体」。传输层与 sleep 均可注入（E18），使重试逻辑可脱离真实
网络做单元测试。

GET 与 POST 共用同一个 Request 形状与同一套重试逻辑，避免出现两套互不一致的注入口。
"""

from __future__ import annotations

import json
import time
from typing import NamedTuple

import requests

import config


class RetryableError(RuntimeError):
    """瞬时故障：网络异常、429 限流、5xx。"""


class NotFoundError(RuntimeError):
    """404。对榜单是不可恢复的错误；对 README 则是「这个仓库没有该文件」的正常答复。"""


class FetchError(RuntimeError):
    """请求最终失败（重试耗尽，或遇到不该重试的状态码）。"""


class TransportError(RuntimeError):
    """响应本身取到了，但内容不符合期望结构。"""


RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# 按常见程度排序：绝大多数仓库用 README.md，逐个尝试直到命中
README_CANDIDATES = ("README.md", "readme.md", "README.rst", "README.MD", "README.txt", "README")


class Request(NamedTuple):
    method: str
    url: str
    headers: dict
    body: str | None
    timeout: int


def _default_transport(request: Request) -> str:
    try:
        response = requests.request(
            request.method,
            request.url,
            headers=request.headers,
            data=(request.body.encode("utf-8") if request.body is not None else None),
            timeout=request.timeout,
        )
    except requests.RequestException as exc:
        raise RetryableError(str(exc)) from exc

    if response.status_code in RETRYABLE_STATUS:
        raise RetryableError(f"HTTP {response.status_code}")
    if response.status_code == 404:
        raise NotFoundError(request.url)
    response.raise_for_status()
    return response.text


def send(request: Request, *, transport=None, retries=None, backoff: float = 2.0, sleep=None,
         missing_ok: bool = False):
    """执行请求。``missing_ok=True`` 时 404 返回 None 而非抛错。"""
    transport = transport or _default_transport
    sleeper = sleep or time.sleep
    attempts = (config.HTTP_RETRIES if retries is None else retries) + 1
    last_error = None

    for attempt in range(attempts):
        try:
            return transport(request)
        except RetryableError as exc:
            last_error = exc
            if attempt < attempts - 1:
                sleeper(backoff**attempt)
        except NotFoundError as exc:
            if missing_ok:
                return None
            raise FetchError(f"{request.url} 返回 404") from exc
        except Exception as exc:
            raise FetchError(f"{request.url} 请求失败且不重试: {exc}") from exc

    raise FetchError(f"{request.url} 重试 {attempts} 次后仍失败: {last_error}") from last_error


def get(url: str, *, headers=None, timeout=None, **kwargs):
    request = Request(
        "GET",
        url,
        headers or {"User-Agent": config.USER_AGENT},
        None,
        timeout or config.TRENDING_TIMEOUT_SECONDS,
    )
    return send(request, **kwargs)


def post_json(url: str, payload: dict, *, headers=None, timeout=None, **kwargs) -> str:
    merged = {"User-Agent": config.USER_AGENT, "Content-Type": "application/json"}
    merged.update(headers or {})
    request = Request(
        "POST",
        url,
        merged,
        json.dumps(payload, ensure_ascii=False),
        timeout or config.DEEPSEEK_TIMEOUT_SECONDS,
    )
    return send(request, **kwargs)


def trending_url(window: str, language: str) -> str:
    suffix = f"/{language}" if language else ""
    return f"https://github.com/trending{suffix}?since={window}"


def fetch_trending_html(window: str, language: str, **kwargs) -> str:
    return get(trending_url(window, language), timeout=config.TRENDING_TIMEOUT_SECONDS, **kwargs)


def readme_url(owner: str, repo: str, filename: str = "README.md") -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/HEAD/{filename}"


def fetch_readme(owner: str, repo: str, *, filenames=None, **kwargs) -> str | None:
    """按常见文件名依次尝试，取第一个非空结果。

    全部候选都 404 时返回 None——仓库没有 README 是正常情况，不是故障，调用方凭此
    标记 readme_available=False 而非跳过该仓库。
    """
    for filename in filenames or README_CANDIDATES:
        text = get(
            readme_url(owner, repo, filename),
            timeout=config.README_TIMEOUT_SECONDS,
            missing_ok=True,
            **kwargs,
        )
        if text and text.strip():
            return text[: config.README_MAX_CHARS]
    return None


def deepseek_url(path: str = "chat/completions") -> str:
    return f"{config.DEEPSEEK_BASE_URL.rstrip('/')}/{path}"


def deepseek_payload(messages, model: str | None = None) -> dict:
    """请求体。用 response_format 要求 JSON 输出，降低「模型多写一句客套话」的概率。"""
    return {
        "model": model or config.DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": config.DEEPSEEK_TEMPERATURE,
        "max_tokens": config.DEEPSEEK_MAX_TOKENS,
        "response_format": {"type": "json_object"},
        "stream": False,
    }


def extract_assistant_content(payload) -> str:
    """从响应信封里取出模型输出文本。结构不符一律抛错，不返回半成品。"""
    if not isinstance(payload, dict):
        raise TransportError("DeepSeek 响应不是 JSON 对象")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise TransportError(f"DeepSeek 响应缺少 choices：{str(payload)[:200]}")
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise TransportError("DeepSeek 响应没有可用的 message.content")
    return content


def call_deepseek(messages, api_key: str, *, transport=None, sleep=None, model=None,
                  retries=None) -> str:
    """调用 DeepSeek 并返回模型输出的文本内容。

    注意本函数只负责「取到内容」；内容是否符合分析 schema 由 analyze.py 校验。
    """
    if not api_key:
        raise FetchError("缺少 DeepSeek API Key")
    raw = post_json(
        deepseek_url(),
        deepseek_payload(messages, model),
        headers={"Authorization": f"Bearer {api_key}"},
        transport=transport,
        sleep=sleep,
        retries=retries,
    )
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise TransportError(f"DeepSeek 响应不是合法 JSON：{exc}") from exc
    return extract_assistant_content(payload)
