"""读取 data/（经 cache.py），生成 dist/ 下的静态站点与前端数据。

渲染阶段只读 data/、只写 dist/（§2.9 硬约束 1），因此可重复执行而不污染数据。
build_site_data() 与榜单键的拼解是纯函数，可离线单测；render_dist() 只做拷贝与
落盘，不做任何编译或打包（E10）。
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import cache
import config


class RenderError(RuntimeError):
    """渲染前置条件不满足。"""


def _site_entry(repo: dict, cached) -> dict:
    entry = {
        "rank": repo.get("rank"),
        "name": repo.get("name", ""),
        "url": repo.get("url", ""),
        "description": repo.get("description", ""),
        "language": repo.get("language", ""),
        "language_color": repo.get("language_color", ""),
        "stars": repo.get("stars"),
        "forks": repo.get("forks"),
        "add_stars": repo.get("add_stars"),
        "analysis": None,
        "analyzed_at": None,
        "readme_available": None,
    }
    if isinstance(cached, dict) and isinstance(cached.get("analysis"), dict):
        entry["analysis"] = cached["analysis"]
        entry["analyzed_at"] = cached.get("analyzed_at")
        entry["readme_available"] = cached.get("readme_available")
    return entry


def build_site_data(boards_doc, analysis_cache, generated_at: str) -> dict:
    """把榜单与 AI 分析合并成前端消费的数据结构。

    按 config 中声明的 21 个视图逐一填充：即使某个榜单在 boards.json 中缺失，也会
    产出空列表而非省略键，这样前端切到该视图时不会因键不存在而报错。
    """
    boards = (boards_doc or {}).get("boards") or {}
    analyses = analysis_cache or {}

    views: dict[str, list] = {}
    entries = 0
    analyzed = 0

    for window in config.TIME_WINDOWS:
        for language in config.LANGUAGES:
            key = config.board_key(window, language)
            rows = []
            for repo in boards.get(key) or []:
                entry = _site_entry(repo, analyses.get(cache.repo_key(repo.get("name", ""))))
                entries += 1
                if entry["analysis"] is not None:
                    analyzed += 1
                rows.append(entry)
            views[key] = rows

    return {
        "generated_at": generated_at,
        "prompt_version": config.PROMPT_VERSION,
        "model": config.DEEPSEEK_MODEL,
        "windows": [
            {"key": window, "label": config.WINDOW_LABELS.get(window, window)}
            for window in config.TIME_WINDOWS
        ],
        "languages": [
            {"key": language, "label": label} for language, label in config.LANGUAGES.items()
        ],
        "stats": {"boards": len(views), "entries": entries, "analyzed": analyzed},
        "boards": views,
    }


def write_site_data(site_data: dict, dist_dir) -> Path:
    """写出前端数据。用紧凑分隔符以压低体积（E7）。"""
    target = Path(dist_dir)
    data_dir = target / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "trending.json"
    payload = json.dumps(site_data, ensure_ascii=False, separators=(",", ":"))
    path.write_text(payload, encoding="utf-8", newline="\n")
    return path


def render_dist(site_data: dict, web_dir=None, dist_dir=None) -> Path:
    """把 web/ 原样拷到 dist/，再写入前端数据。

    先清空 dist/ 是为了让重复渲染结果确定，不会残留上一次的旧文件。dist/ 是构建
    产物、已在 .gitignore 中，删除它不会影响任何入库内容。
    """
    source = Path(web_dir if web_dir is not None else config.WEB_DIR)
    target = Path(dist_dir if dist_dir is not None else config.DIST_DIR)

    if not source.is_dir():
        raise RenderError(f"前端源码目录不存在：{source}")
    if not (source / "index.html").is_file():
        raise RenderError(f"前端缺少入口文件：{source / 'index.html'}")

    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)

    write_site_data(site_data, target)
    return target


def render_now(generated_at: str | None = None, web_dir=None, dist_dir=None) -> Path:
    """正式入口：读 data/（经 cache.py），渲染 dist/。"""
    stamp = generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    site_data = build_site_data(cache.load_boards(), cache.load_analysis_cache(), stamp)
    return render_dist(site_data, web_dir, dist_dir)
