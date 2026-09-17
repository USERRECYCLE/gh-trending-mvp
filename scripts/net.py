"""全部出网调用：集中超时、重试与指数退避。

本模块不判断运行模式（E16）——走网络还是读 fixture 由 main.py 决定，它只负责
「给定请求，返回响应」。传输层与 sleep 均可注入（E18），使重试逻辑可脱离真实
网络做单元测试。
"""

from __future__ import annotations

import time

import requests

import config


class RetryableError(RuntimeError):
    """瞬时故障：网络异常、429 限流、5xx。"""


class FetchError(RuntimeError):
    """请求最终失败（重试耗尽，或遇到不该重试的状态码）。"""


# 429 与 5xx 值得重试；4xx 重试只会浪费时间和配额
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def trending_url(window: str, language: str) -> str:
    suffix = f"/{language}" if language else ""
    return f"https://github.com/trending{suffix}?since={window}"


def _default_transport(url: str, headers: dict, timeout: int) -> str:
    try:
        response = requests.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        raise RetryableError(str(exc)) from exc
    if response.status_code in RETRYABLE_STATUS:
        raise RetryableError(f"HTTP {response.status_code}")
    response.raise_for_status()
    return response.text


def fetch_text(
    url: str,
    *,
    transport=None,
    headers=None,
    timeout=None,
    retries=None,
    backoff: float = 2.0,
    sleep=None,
) -> str:
    """带重试的 GET。``transport`` 签名为 (url, headers, timeout) -> str。"""
    transport = transport or _default_transport
    sleeper = sleep or time.sleep
    attempts = (config.HTTP_RETRIES if retries is None else retries) + 1
    request_headers = headers or {"User-Agent": config.USER_AGENT}
    request_timeout = timeout or config.TRENDING_TIMEOUT_SECONDS

    for attempt in range(attempts):
        try:
            return transport(url, request_headers, request_timeout)
        except RetryableError as exc:
            last_error = exc
            if attempt < attempts - 1:
                sleeper(backoff**attempt)
        except Exception as exc:
            raise FetchError(f"{url} 请求失败且不重试: {exc}") from exc

    raise FetchError(f"{url} 重试 {attempts} 次后仍失败: {last_error}") from last_error


def fetch_trending_html(window: str, language: str, **kwargs) -> str:
    return fetch_text(trending_url(window, language), **kwargs)
