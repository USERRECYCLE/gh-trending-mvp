"""配额与候选选取的纯策略：每日上限、Bootstrap 判定、滞回、熔断。

全部函数为纯函数，不含 IO，也不判断运行模式（E16）——状态由 main.py 通过
cache.py 读写后传入传出。「每日只计算一次配额」的时序同样由 main.py 负责：
它只在 roll_to_day() 报告进入新的一天时调用 update_day_policy()。
"""

from __future__ import annotations

from datetime import datetime, timezone

import config

MODE_STEADY = "steady"
MODE_BOOTSTRAP = "bootstrap"


def today_utc(now=None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")


def new_state(date_str: str) -> dict:
    return {
        "date": date_str,
        "used_today": 0,
        "hit_rate": 0.0,
        "mode": MODE_STEADY,
        "cap": config.STEADY_DAILY_CAP,
        "consecutive_bootstrap_days": 0,
        "warnings": [],
    }


def daily_cap(mode: str) -> int:
    return config.BOOTSTRAP_DAILY_CAP if mode == MODE_BOOTSTRAP else config.STEADY_DAILY_CAP


def per_run_cap(mode: str) -> int:
    if mode == MODE_BOOTSTRAP:
        return config.BOOTSTRAP_PER_RUN_CAP
    return config.STEADY_DAILY_CAP


def roll_to_day(state: dict, today: str) -> tuple[dict, bool]:
    """跨到新的 UTC 日则重置当日用量，但保留熔断计数。返回 (状态, 是否新的一天)。"""
    state = dict(state or {})
    if state.get("date") == today:
        return state, False
    rolled = new_state(today)
    rolled["consecutive_bootstrap_days"] = state.get("consecutive_bootstrap_days", 0)
    return rolled, True


def compute_hit_rate(candidates, valid_keys) -> float:
    """去重后的候选中缓存有效的比例。候选为空时返回 1.0——保守退到稳态配额，
    避免因取数为空而误开 Bootstrap；取数为空本身由 A4 断言拦截。"""
    total = {c["repo_key"] for c in candidates}
    if not total:
        return 1.0
    return len(total & set(valid_keys)) / len(total)


def update_day_policy(state: dict, hit_rate: float) -> dict:
    """按当日命中率决定生效配额。调用方须保证每天只调用一次。"""
    state = dict(state or {})

    if hit_rate < config.HIT_RATE_BOOTSTRAP_ENTER:
        streak = state.get("consecutive_bootstrap_days", 0) + 1
        desired = MODE_BOOTSTRAP
    else:
        streak = 0
        if hit_rate >= config.HIT_RATE_STEADY_EXIT:
            desired = MODE_STEADY
        else:
            # 滞回区间：维持上一轮状态，避免命中率在阈值附近抖动导致配额反复跳变
            desired = state.get("mode", MODE_STEADY)

    warnings = []
    if desired == MODE_BOOTSTRAP and streak > config.BOOTSTRAP_FUSE_DAYS:
        # 熔断：连续多日命中率过低说明存在异常（缓存丢失、上游改版），此时
        # Bootstrap 关不掉，必须强制回落并告警。计数器不重置，防止反复开关。
        warnings.append(
            f"熔断：连续 {streak} 日命中率低于 {config.HIT_RATE_BOOTSTRAP_ENTER:.0%}，"
            f"已强制回落至稳态配额 {config.STEADY_DAILY_CAP}/日，请排查缓存与解析链路"
        )
        mode = MODE_STEADY
    else:
        mode = desired

    state.update(
        {
            "hit_rate": hit_rate,
            "mode": mode,
            "cap": daily_cap(mode),
            "consecutive_bootstrap_days": streak,
            "warnings": warnings,
        }
    )
    return state


def effective_run_cap(state: dict) -> int:
    """本轮可用次数：当日剩余额度与单轮上限取小。"""
    remaining = max(0, state.get("cap", config.STEADY_DAILY_CAP) - state.get("used_today", 0))
    return min(remaining, per_run_cap(state.get("mode", MODE_STEADY)))


def consume(state: dict, count: int) -> dict:
    state = dict(state or {})
    state["used_today"] = state.get("used_today", 0) + max(0, count)
    return state


def select_within_cap(candidates, cap: int) -> tuple[list, int]:
    """按「榜单权重 → 榜内排名」排序并截断。同一仓库命中多个榜单时只保留优先级
    最高的一次，避免配额被重复消耗。返回 (入选列表, 被丢弃数量)。"""
    best: dict[str, tuple] = {}
    for candidate in candidates:
        key = candidate["repo_key"]
        order = (config.WINDOW_PRIORITY.get(candidate.get("window"), 99), candidate.get("rank", 99))
        if key not in best or order < best[key][0]:
            best[key] = (order, candidate)

    ordered = [item[1] for item in sorted(best.values(), key=lambda pair: pair[0])]
    if cap < 0:
        cap = 0
    selected = ordered[:cap]
    return selected, len(ordered) - len(selected)
