"""入口：把采集、缓存、配额、分析、渲染串成一轮完整流程（§2.10.3）。

全部网络与文件取数都在这里完成，纯逻辑模块收到的永远是同样类型的入参。走网络还是
读 fixture 只在本文件按 INPUT_SOURCE 决定（E16）。纯函数内不得出现模式判断。
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import analyze
import cache
import config
import fetch
import net
import quota
import render


class RunError(RuntimeError):
    """整轮无法继续。单个仓库分析失败不属于此类，那只会跳过该仓库。"""


def input_source() -> str:
    value = (os.environ.get(config.ENV_INPUT_SOURCE) or config.INPUT_SOURCE_NETWORK).strip().lower()
    if value not in (config.INPUT_SOURCE_NETWORK, config.INPUT_SOURCE_FIXTURE):
        raise RunError(f"INPUT_SOURCE 只能是 network 或 fixture，实际为 {value!r}")
    return value


def acquire_boards(source: str, *, transport=None, sleep=None, now=None) -> dict:
    """取得 21 个榜单。任一榜解析异常即整轮失败（A4／A5），绝不静默产出空榜。"""
    parsed = {}
    problems = []
    for window in config.TIME_WINDOWS:
        for language in config.LANGUAGES:
            key = config.board_key(window, language)
            if source == config.INPUT_SOURCE_FIXTURE:
                html = cache.read_fixture(fetch.fixture_relative_path(window, language))
            else:
                html = net.fetch_trending_html(window, language, transport=transport, sleep=sleep)
            repos = fetch.parse_trending_html(html)
            issues = fetch.invariant_failures(repos)
            if issues:
                problems.append(f"{key}: {'；'.join(issues)}")
            parsed[key] = repos

    if problems:
        raise RunError("榜单解析异常，已中止本轮：\n  " + "\n  ".join(problems))
    stamp = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return fetch.build_boards(parsed, stamp)


def acquire_readme(source: str, candidate: dict, *, transport=None, sleep=None):
    """取 README。取不到返回 None——那是正常情况，不应导致该仓库被跳过。"""
    if source == config.INPUT_SOURCE_FIXTURE:
        return cache.read_fixture(config.FIXTURE_README_SAMPLE)
    owner, _, repo = candidate["name"].partition("/")
    if not owner or not repo:
        return None
    return net.fetch_readme(owner, repo, transport=transport, sleep=sleep)


def acquire_analysis(source: str, messages, *, transport=None, sleep=None, api_key=""):
    """取模型输出文本。内容是否合规由 analyze.py 校验，本函数不管。"""
    if source == config.INPUT_SOURCE_FIXTURE:
        return cache.read_fixture(config.FIXTURE_DEEPSEEK_RESPONSE)
    return net.call_deepseek(messages, api_key, transport=transport, sleep=sleep)


def run(*, source=None, transport=None, sleep=None, api_key=None, now=None, log=print) -> dict:
    source = source or input_source()
    key = os.environ.get(config.ENV_DEEPSEEK_API_KEY, "") if api_key is None else api_key
    stamp_dt = now or datetime.now(timezone.utc)
    stamp = stamp_dt.isoformat(timespec="seconds")

    boards_doc = acquire_boards(source, transport=transport, sleep=sleep, now=stamp_dt)
    cache.save_boards(boards_doc)
    candidates = fetch.collect_candidates(boards_doc)

    analysis_cache = cache.load_analysis_cache()
    state, is_new_day = quota.roll_to_day(cache.load_quota_state(), quota.today_utc(stamp_dt))
    if is_new_day:
        valid = cache.valid_cache_keys(candidates, analysis_cache, now=stamp_dt)
        state = quota.update_day_policy(state, quota.compute_hit_rate(candidates, valid))
        log(f"新的一天：命中率 {state['hit_rate']:.1%}，生效配额 {state['cap']}/日（{state['mode']}）")
    for warning in state.get("warnings", []):
        log(f"告警：{warning}")

    targets = cache.analyze_targets(candidates, analysis_cache, now=stamp_dt)
    cap = quota.effective_run_cap(state)
    selected, dropped = quota.select_within_cap(targets, cap)
    log(f"候选 {len(candidates)} 个，需分析 {len(targets)} 个，本轮额度 {cap}，实际取 {len(selected)} 个")

    done, failed = 0, 0
    for candidate in selected:
        try:
            readme = acquire_readme(source, candidate, transport=transport, sleep=sleep)
            messages = [
                {"role": "system", "content": analyze.SYSTEM_PROMPT},
                {"role": "user", "content": analyze.build_prompt(candidate, readme)},
            ]
            raw = acquire_analysis(source, messages, transport=transport, sleep=sleep, api_key=key)
            analysis = analyze.parse_analysis_response(raw)
        except (analyze.AnalysisError, net.FetchError, net.TransportError) as exc:
            # 不写缓存、不计配额——避免把失败结果固化下来污染后续轮次（C10）
            failed += 1
            log(f"  跳过 {candidate['name']}：{exc}")
            continue
        analysis_cache[candidate["repo_key"]] = {
            "analysis": analysis,
            "stars_at_analysis": candidate["stars"],
            "prompt_version": config.PROMPT_VERSION,
            "model": config.DEEPSEEK_MODEL,
            "analyzed_at": stamp,
            "readme_available": readme is not None,
        }
        done += 1

    state = quota.consume(state, done)
    cache.save_analysis_cache(analysis_cache)
    cache.save_quota_state(state)
    render.render_now(stamp)

    summary = {
        "input_source": source,
        "boards": len(boards_doc.get("boards", {})),
        "candidates": len(candidates),
        "needed": len(targets),
        "quota_cap": cap,
        "selected": len(selected),
        "analyzed": done,
        "failed": failed,
        "dropped_by_cap": dropped,
        "cache_size": len(analysis_cache),
        "quota": state,
    }
    _publish(summary, log)
    return summary


def _publish(summary: dict, log) -> None:
    state = summary["quota"]
    lines = [
        "## 榜单与 AI 解读更新",
        "",
        f"- 数据来源：`{summary['input_source']}`",
        f"- 榜单：{summary['boards']} 个，候选仓库 {summary['candidates']} 个（去重后）",
        f"- 需分析：{summary['needed']} 个；本轮额度 {summary['quota_cap']}，实际分析 {summary['selected']} 个",
        f"- 成功 {summary['analyzed']} 个，失败 {summary['failed']} 个，因额度延后 {summary['dropped_by_cap']} 个",
        f"- 缓存总量：{summary['cache_size']} 条",
        "",
        f"配额状态：`{state.get('date')}` 已用 {state.get('used_today')}，"
        f"命中率 {state.get('hit_rate', 0):.1%}，模式 `{state.get('mode')}`，"
        f"连续 Bootstrap {state.get('consecutive_bootstrap_days')} 天",
    ]
    for warning in state.get("warnings", []):
        lines.append(f"- ⚠️ {warning}")
    text = "\n".join(lines)
    log(text)
    cache.append_step_summary(text)


def main() -> int:
    try:
        run()
    except RunError as exc:
        print(f"本轮中止：{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
