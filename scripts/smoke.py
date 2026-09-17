"""online-smoke 入口：引导写入 fixture（仅当缺失或显式 refresh），此后只验证。

fixture 是引导产物而非自动同步产物（§4.8.1）——一旦写入即冻结，此后每轮只做校验。
上游漂移因此表现为 smoke 失败，而不会被悄悄吸收进本地测试（§4.8.2 断言的是结构
不变量，不是内容一致：榜单内容每小时都在变）。
"""

from __future__ import annotations

import os
import sys

import cache
import config
import fetch
import net

SUMMARY_FIELDS = ("name", "url", "stars", "language")

# 失败时用来定位「上游改了哪一处」的选择器探针
SELECTOR_PROBES = (
    ("star 链接", "has_stargazers_link"),
    ("fork 链接", "has_forks_link"),
    ("语言 itemprop", "has_language_itemprop"),
    ("周期徽章", "has_period_badge"),
)


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def board_label(window: str, language: str) -> str:
    return f"{window}/{language or 'all'}"


def run(transport=None, sleep=None, refresh=None, fixture_dir=None, log=print):
    """遍历全部榜单。返回 (退出码, 每条榜单的结果)。"""
    refresh = _env_flag(config.ENV_REFRESH_FIXTURES) if refresh is None else refresh
    results = []
    failures = []

    for window in config.TIME_WINDOWS:
        for language in config.LANGUAGES:
            board = board_label(window, language)
            relative = fetch.fixture_relative_path(window, language)
            existed = cache.fixture_exists(relative, fixture_dir)
            entry = {
                "board": board,
                "existed": existed,
                "action": "",
                "count": 0,
                "miss_rates": {},
                "problems": [],
            }

            try:
                html = net.fetch_trending_html(window, language, transport=transport, sleep=sleep)
            except net.FetchError as exc:
                entry["action"] = "fetch-failed"
                entry["problems"] = [str(exc)]
                failures.append((board, entry["problems"]))
                results.append(entry)
                continue

            repos = fetch.parse_trending_html(html)
            problems = fetch.invariant_failures(repos)
            entry["count"] = len(repos)
            entry["miss_rates"] = fetch.field_miss_rates(repos)

            if (not existed) or refresh:
                if problems:
                    # 绝不把不合格的快照冻结下来——那会让漂移从此不可见
                    entry["action"] = "write-skipped"
                else:
                    cache.write_fixture(relative, html, fixture_dir)
                    entry["action"] = "bootstrap" if not existed else "refresh"
            else:
                entry["action"] = "verify"

            if problems:
                entry["problems"] = problems
                # 只在失败时才算选择器诊断：正常情况下零开销，出问题时才需要它定位
                entry["diagnostics"] = fetch.diagnose_html(html, repos)
                failures.append((board, problems))
            results.append(entry)

    _publish(results, failures, refresh, log)
    return (1 if failures else 0), results


def _publish(results, failures, refresh, log) -> None:
    writes = sum(1 for entry in results if entry["action"] in {"bootstrap", "refresh"})
    mode = "refresh（显式覆盖）" if refresh else "引导/校验（fixture 缺失才写入）"

    lines = [
        f"## online-smoke — {mode}",
        "",
        f"- 榜单数：{len(results)}",
        f"- fixture 写入：{writes}",
        f"- 失败：{len(failures)}",
        "",
        "| 榜单 | 动作 | 条数 | " + " | ".join(SUMMARY_FIELDS) + " 缺失率 | 问题 |",
        "|---|---|---|" + "---|" * len(SUMMARY_FIELDS) + "---|",
    ]
    for entry in results:
        rates = entry["miss_rates"]
        cells = " | ".join(
            f"{rates.get(field, 0.0):.1%}"
            if entry["count"] and field in rates
            else "-"
            for field in SUMMARY_FIELDS
        )
        problem = "；".join(entry["problems"]) or "-"
        lines.append(f"| {entry['board']} | {entry['action']} | {entry['count']} | {cells} | {problem} |")

    if failures:
        by_board = {entry["board"]: entry for entry in results}
        lines += ["", "### 失败明细", ""]
        for board, problems in failures:
            for problem in problems:
                lines.append(f"- **{board}**：{problem}")
            diag = by_board.get(board, {}).get("diagnostics") or {}
            if diag:
                hit = [label for label, key in SELECTOR_PROBES if diag.get(key)]
                miss = [label for label, key in SELECTOR_PROBES if not diag.get(key)]
                lines.append(f"  - 选择器命中：{'、'.join(hit) or '（无）'}")
                lines.append(f"  - 选择器未命中：{'、'.join(miss) or '（无）'}")
                lines.append(
                    f"  - HTML 中 article 标签 {diag.get('articles_found')} 个，解析出 {diag.get('parsed')} 条"
                )

    text = "\n".join(lines)
    log(text)
    cache.append_step_summary(text)


def main() -> int:
    exit_code, _ = run()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
