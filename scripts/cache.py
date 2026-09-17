"""data/ 与 tests/fixtures/ 的唯一读写入口，以及缓存有效性的纯判定。

任何其他模块不得自行拼接数据路径或调用 open()（E13）。未来把 data/ 换成
数据库或远端存储时，只需替换本模块。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config


# --------------------------------------------------------------------------
# 路径与底层读写
# --------------------------------------------------------------------------

def _resolve(override, default: Path) -> Path:
    return Path(override) if override is not None else Path(default)


def read_json(name: str, data_dir=None, default=None):
    path = _resolve(data_dir, config.DATA_DIR) / name
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def write_json(name: str, payload, data_dir=None) -> Path:
    directory = _resolve(data_dir, config.DATA_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


# --------------------------------------------------------------------------
# 数据集：榜单原始数据、分析缓存、配额状态
# --------------------------------------------------------------------------

def load_boards(data_dir=None, default=None):
    return read_json(config.BOARDS_FILE, data_dir, default if default is not None else {})


def save_boards(boards, data_dir=None) -> Path:
    return write_json(config.BOARDS_FILE, boards, data_dir)


def load_analysis_cache(data_dir=None) -> dict:
    return read_json(config.ANALYSIS_CACHE_FILE, data_dir, {}) or {}


def save_analysis_cache(cache, data_dir=None) -> Path:
    return write_json(config.ANALYSIS_CACHE_FILE, cache, data_dir)


def load_quota_state(data_dir=None) -> dict:
    return read_json(config.QUOTA_FILE, data_dir, {}) or {}


def save_quota_state(state, data_dir=None) -> Path:
    return write_json(config.QUOTA_FILE, state, data_dir)


# --------------------------------------------------------------------------
# fixture：唯一出入口（§2.9 硬约束 3）
# --------------------------------------------------------------------------

def _fixture_path(relative: str, fixture_dir=None) -> Path:
    base = _resolve(fixture_dir, config.FIXTURE_DIR)
    return base / relative


def fixture_exists(relative: str, fixture_dir=None) -> bool:
    return _fixture_path(relative, fixture_dir).exists()


def read_fixture(relative: str, fixture_dir=None) -> str:
    path = _fixture_path(relative, fixture_dir)
    with path.open("r", encoding="utf-8") as fh:
        return fh.read()


def write_fixture(relative: str, text: str, fixture_dir=None) -> Path:
    path = _fixture_path(relative, fixture_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return path


def append_step_summary(text: str) -> bool:
    """把一段 Markdown 追加到 GitHub Actions 的 Step Summary。

    这是平台提供的产物文件，不是数据存储，因此不受 §2.9 第 2 条的范围限制——放在本
    模块只是因为这里集中负责文件 I/O，避免每个入口各写一遍 open()。未设置该环境
    变量时（本地运行）静默跳过。
    """
    path = os.environ.get(config.ENV_STEP_SUMMARY)
    if not path:
        return False
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(text + "\n")
    return True


# --------------------------------------------------------------------------
# 缓存有效性判定（纯函数）
# --------------------------------------------------------------------------

def _parse_timestamp(value: str):
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_expired(entry: dict, now=None, ttl_days: int | None = None) -> bool:
    """无法解析时间戳时视为已过期——宁可重跑，也不要永久沿用坏数据。"""
    now = now or datetime.now(timezone.utc)
    ttl = config.CACHE_TTL_DAYS if ttl_days is None else ttl_days
    analyzed_at = _parse_timestamp(entry.get("analyzed_at", ""))
    if analyzed_at is None:
        return True
    return now - analyzed_at > timedelta(days=ttl)


def is_star_shifted(entry: dict, stars_now: int) -> bool:
    """绝对增量与比例增量取或，任一超阈值即认为数据已过时。"""
    base = entry.get("stars_at_analysis")
    if not isinstance(base, (int, float)):
        return True
    if abs(stars_now - base) > config.STAR_DELTA_ABSOLUTE:
        return True
    if base > 0 and abs(stars_now - base) / base > config.STAR_DELTA_RATIO:
        return True
    return False


def needs_analysis(
    entry: dict | None,
    stars_now: int,
    prompt_version: str | None = None,
    now=None,
) -> tuple[bool, str]:
    """判定单个仓库是否需要（重新）分析，返回 (是否, 原因)。

    每日配额不在本函数内判定——它属于 quota.py。二者结果行为等价：先在此判
    TTL／Star 再按配额截断，与先截断配额再判定，产出完全一致（配额耗尽时未
    入选的候选一律沿用旧缓存）。
    """
    version = config.PROMPT_VERSION if prompt_version is None else prompt_version
    if not entry:
        return True, "missing"
    if entry.get("prompt_version") != version:
        return True, "prompt_version"
    if is_expired(entry, now):
        return True, "ttl"
    if is_star_shifted(entry, stars_now):
        return True, "stars"
    return False, "fresh"


def analyze_targets(candidates, cache: dict, prompt_version: str | None = None, now=None):
    """从候选仓库中筛出需要分析的子集，保留原因供日志与测试断言。"""
    targets = []
    for candidate in candidates:
        key = candidate["repo_key"]
        entry = cache.get(key)
        required, reason = needs_analysis(entry, candidate["stars"], prompt_version, now)
        if required:
            targets.append({**candidate, "reason": reason})
    return targets


def valid_cache_keys(candidates, cache: dict, prompt_version: str | None = None, now=None) -> set[str]:
    """命中率的分子：缓存有效（存在、版本匹配、未超期、Star 未越阈值）的仓库。"""
    valid = set()
    for candidate in candidates:
        key = candidate["repo_key"]
        entry = cache.get(key)
        required, _ = needs_analysis(entry, candidate["stars"], prompt_version, now)
        if not required:
            valid.add(key)
    return valid
