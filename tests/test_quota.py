"""quota.py 的离线单元测试（C7–C9、C12–C16）。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import config  # noqa: E402
import quota  # noqa: E402

TODAY = "2026-09-17"
TOMORROW = "2026-09-18"


def candidates(count, window="daily"):
    return [
        {"repo_key": f"o/r{i:03d}", "window": window, "rank": i + 1, "stars": 100 + i}
        for i in range(count)
    ]


class DailyCapTest(unittest.TestCase):
    """C7：稳态配额上限。"""

    def test_high_hit_rate_selects_steady_cap(self):
        state = quota.update_day_policy(quota.new_state(TODAY), 0.95)
        self.assertEqual(state["mode"], quota.MODE_STEADY)
        self.assertEqual(state["cap"], config.STEADY_DAILY_CAP)

    def test_selected_count_never_exceeds_cap(self):
        state = quota.update_day_policy(quota.new_state(TODAY), 0.95)
        selected, dropped = quota.select_within_cap(candidates(60), quota.effective_run_cap(state))
        self.assertEqual(len(selected), config.STEADY_DAILY_CAP)
        self.assertEqual(dropped, 10)


class CrossRoundAccumulationTest(unittest.TestCase):
    """C8：同日多轮共享同一份每日额度。"""

    def test_four_rounds_of_fifteen_stop_at_fifty(self):
        state = quota.update_day_policy(quota.new_state(TODAY), 0.95)
        total = 0
        for _ in range(4):
            cap = quota.effective_run_cap(state)
            selected, _ = quota.select_within_cap(candidates(15), cap)
            total += len(selected)
            state = quota.consume(state, len(selected))
        self.assertEqual(total, config.STEADY_DAILY_CAP)
        self.assertEqual(state["used_today"], config.STEADY_DAILY_CAP)

    def test_exhausted_cap_yields_nothing(self):
        state = quota.consume(quota.update_day_policy(quota.new_state(TODAY), 0.95), 50)
        self.assertEqual(quota.effective_run_cap(state), 0)
        selected, _ = quota.select_within_cap(candidates(15), quota.effective_run_cap(state))
        self.assertEqual(selected, [])


class PriorityOrderTest(unittest.TestCase):
    """C9：额度不足时按榜单权重与榜内排名选取。"""

    def test_surplus_selects_best_daily_ranks(self):
        pool = [
            {"repo_key": "w/x", "window": "weekly", "rank": 1, "stars": 10},
            {"repo_key": "d/3", "window": "daily", "rank": 3, "stars": 30},
            {"repo_key": "d/1", "window": "daily", "rank": 1, "stars": 10},
            {"repo_key": "m/y", "window": "monthly", "rank": 1, "stars": 10},
            {"repo_key": "d/2", "window": "daily", "rank": 2, "stars": 20},
        ]
        selected, dropped = quota.select_within_cap(pool, 3)
        self.assertEqual([c["repo_key"] for c in selected], ["d/1", "d/2", "d/3"])
        self.assertEqual(dropped, 2)

    def test_same_repo_across_boards_counted_once(self):
        pool = [
            {"repo_key": "a/one", "window": "weekly", "rank": 2, "stars": 10},
            {"repo_key": "a/one", "window": "daily", "rank": 9, "stars": 10},
        ]
        selected, dropped = quota.select_within_cap(pool, 5)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["window"], "daily")
        self.assertEqual(dropped, 0)


class BootstrapTest(unittest.TestCase):
    """C12：命中率过低时自动开 Bootstrap，无需人工开关。"""

    def test_low_hit_rate_opens_bootstrap(self):
        state = quota.update_day_policy(quota.new_state(TODAY), 0.30)
        self.assertEqual(state["mode"], quota.MODE_BOOTSTRAP)
        self.assertEqual(state["cap"], config.BOOTSTRAP_DAILY_CAP)

    def test_single_run_is_capped_below_daily_limit(self):
        state = quota.update_day_policy(quota.new_state(TODAY), 0.30)
        self.assertEqual(quota.effective_run_cap(state), config.BOOTSTRAP_PER_RUN_CAP)

    def test_two_runs_fill_the_bootstrap_allowance(self):
        state = quota.update_day_policy(quota.new_state(TODAY), 0.30)
        first = quota.effective_run_cap(state)
        state = quota.consume(state, first)
        second = quota.effective_run_cap(state)
        self.assertEqual(first + second, config.BOOTSTRAP_DAILY_CAP)


class HysteresisTest(unittest.TestCase):
    """C13：命中率落在中间区间时维持上一轮状态，不跳变。"""

    def test_middle_band_keeps_bootstrap(self):
        state = quota.new_state(TODAY)
        state["mode"] = quota.MODE_BOOTSTRAP
        state["consecutive_bootstrap_days"] = 1
        resolved = quota.update_day_policy(state, 0.75)
        self.assertEqual(resolved["mode"], quota.MODE_BOOTSTRAP)

    def test_middle_band_keeps_steady(self):
        state = quota.new_state(TODAY)
        resolved = quota.update_day_policy(state, 0.75)
        self.assertEqual(resolved["mode"], quota.MODE_STEADY)

    def test_enter_threshold_is_strict(self):
        self.assertEqual(
            quota.update_day_policy(quota.new_state(TODAY), config.HIT_RATE_BOOTSTRAP_ENTER)["mode"],
            quota.MODE_STEADY,
        )


class AutoFallbackTest(unittest.TestCase):
    """C14：命中率恢复后自动回落，无需人工关闭。"""

    def test_high_hit_rate_returns_to_steady(self):
        state = quota.new_state(TODAY)
        state["mode"] = quota.MODE_BOOTSTRAP
        state["consecutive_bootstrap_days"] = 2
        resolved = quota.update_day_policy(state, 0.95)
        self.assertEqual(resolved["mode"], quota.MODE_STEADY)
        self.assertEqual(resolved["cap"], config.STEADY_DAILY_CAP)
        self.assertEqual(resolved["consecutive_bootstrap_days"], 0)
        self.assertEqual(resolved["warnings"], [])


class FuseTest(unittest.TestCase):
    """C15：连续多日低命中率触发熔断，避免 Bootstrap 关不掉。"""

    def _state_after_days(self, days):
        state = quota.new_state(TODAY)
        for index in range(days):
            state, _ = quota.roll_to_day(state, f"2026-09-{10 + index:02d}")
            state = quota.update_day_policy(state, 0.10)
        return state

    def test_first_three_days_stay_in_bootstrap(self):
        state = self._state_after_days(3)
        self.assertEqual(state["mode"], quota.MODE_BOOTSTRAP)
        self.assertEqual(state["consecutive_bootstrap_days"], 3)

    def test_fourth_day_is_fused_back_to_steady(self):
        state = self._state_after_days(3)
        state, _ = quota.roll_to_day(state, "2026-09-13")
        state = quota.update_day_policy(state, 0.10)
        self.assertEqual(state["mode"], quota.MODE_STEADY)
        self.assertEqual(state["cap"], config.STEADY_DAILY_CAP)
        self.assertTrue(state["warnings"])

    def test_fuse_does_not_oscillate_back(self):
        state = self._state_after_days(4)
        state, is_new_day = quota.roll_to_day(state, "2026-09-14")
        self.assertTrue(is_new_day)
        state = quota.update_day_policy(state, 0.05)
        self.assertEqual(state["mode"], quota.MODE_STEADY)


class StateShapeTest(unittest.TestCase):
    """C16：配额状态可审计。"""

    REQUIRED = (
        "date",
        "used_today",
        "hit_rate",
        "cap",
        "consecutive_bootstrap_days",
        "warnings",
    )

    def test_state_carries_all_audit_fields(self):
        state = quota.update_day_policy(quota.new_state(TODAY), 0.30)
        for field in self.REQUIRED:
            self.assertIn(field, state)
            self.assertIsNotNone(state[field])


class DayRolloverTest(unittest.TestCase):
    def test_same_day_does_not_reset_usage(self):
        state = quota.consume(quota.new_state(TODAY), 20)
        rolled, is_new_day = quota.roll_to_day(state, TODAY)
        self.assertFalse(is_new_day)
        self.assertEqual(rolled["used_today"], 20)

    def test_new_day_resets_usage_but_keeps_streak(self):
        state = quota.consume(quota.new_state(TODAY), 20)
        state["consecutive_bootstrap_days"] = 2
        rolled, is_new_day = quota.roll_to_day(state, TOMORROW)
        self.assertTrue(is_new_day)
        self.assertEqual(rolled["used_today"], 0)
        self.assertEqual(rolled["consecutive_bootstrap_days"], 2)

    def test_new_day_starts_from_conservative_defaults(self):
        rolled, _ = quota.roll_to_day(quota.new_state(TODAY), TOMORROW)
        self.assertEqual(rolled["mode"], quota.MODE_STEADY)
        self.assertEqual(rolled["cap"], config.STEADY_DAILY_CAP)


class HitRateTest(unittest.TestCase):
    def test_ratio_over_deduplicated_candidates(self):
        pool = candidates(4)
        rate = quota.compute_hit_rate(pool, {"o/r000", "o/r001"})
        self.assertAlmostEqual(rate, 0.5)

    def test_duplicate_boards_do_not_inflate_denominator(self):
        pool = candidates(2) + [
            {"repo_key": "o/r000", "window": "weekly", "rank": 5, "stars": 100}
        ]
        self.assertAlmostEqual(quota.compute_hit_rate(pool, {"o/r000"}), 0.5)

    def test_empty_candidates_fall_back_conservatively(self):
        self.assertEqual(quota.compute_hit_rate([], set()), 1.0)


if __name__ == "__main__":
    unittest.main()
