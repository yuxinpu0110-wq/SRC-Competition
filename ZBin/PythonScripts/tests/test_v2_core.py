import math
from pathlib import Path
import sys
import unittest

# unittest discover 把 tests 目录放在 sys.path 首位；显式加入被测模块目录。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from keeper_model import KeeperModel
from match_events import MatchEvents
from match_tactics import Point
from observation import ObservationClock
from pass_planner import evaluate_pass, reachable_time, rolling_time


class ObservationTests(unittest.TestCase):
    def test_duplicate_cycle_is_not_fresh(self):
        clock = ObservationClock(.15)
        self.assertTrue(clock.observe(1.0, 10).fresh)
        duplicate = clock.observe(1.1, 10)
        self.assertFalse(duplicate.fresh)
        self.assertAlmostEqual(duplicate.age, .1)

    def test_cycle_rollback_requires_reset(self):
        clock = ObservationClock()
        clock.observe(1.0, 100)
        status = clock.observe(1.1, 2)
        self.assertTrue(status.fresh)
        self.assertTrue(status.reset)

    def test_legacy_call_is_always_fresh(self):
        clock = ObservationClock()
        self.assertTrue(clock.observe(1.0).fresh)
        self.assertTrue(clock.observe(1.0).fresh)


class EventTests(unittest.TestCase):
    def test_request_is_not_departure(self):
        events = MatchEvents()
        sequence = events.request_kick(1.0, 0, Point(0, 0), Point(1000, 0), True)
        self.assertFalse(events.departed(sequence))
        events.observe(1.1, Point(250, 0), fresh=False)
        self.assertFalse(events.departed(sequence))

    def test_fresh_forward_motion_confirms_departure(self):
        events = MatchEvents()
        sequence = events.request_kick(1.0, 0, Point(0, 0), Point(1000, 0), True)
        events.observe(1.1, Point(250, 0), fresh=True)
        self.assertTrue(events.departed(sequence))

    def test_sideways_motion_does_not_confirm_departure(self):
        events = MatchEvents()
        sequence = events.request_kick(1.0, 0, Point(0, 0), Point(1000, 0), False)
        events.observe(1.1, Point(0, 300), fresh=True)
        self.assertFalse(events.departed(sequence))


class PassPlannerTests(unittest.TestCase):
    def test_ball_that_stops_before_target_is_rejected(self):
        self.assertIsNone(rolling_time(3000, initial_speed=1000, deceleration=350))

    def test_blocked_lane_is_rejected(self):
        plan = evaluate_pass(Point(0, 0), Point(900, 500), Point(1200, 0), [Point(600, 0)])
        self.assertIsNone(plan)

    def test_reachable_open_pass_is_rankable(self):
        plan = evaluate_pass(Point(0, 0), Point(1050, 200), Point(1200, 200), [])
        self.assertIsNotNone(plan)
        self.assertGreater(plan.lane_clearance, 200)
        self.assertLessEqual(plan.receiver_seconds, plan.flight_seconds+.35)

    def test_reachable_time_grows_with_distance(self):
        self.assertLess(reachable_time(Point(0, 0), Point(500, 0)),
                        reachable_time(Point(0, 0), Point(2000, 0)))


class KeeperModelTests(unittest.TestCase):
    FIELD = {'goal_width': 1000}

    def test_missing_keeper_is_unknown(self):
        self.assertIsNone(KeeperModel().predict(.2, self.FIELD))

    def test_short_prediction_follows_motion(self):
        model = KeeperModel()
        model.observe(1.0, Point(4300, 0))
        model.observe(1.1, Point(4300, 100))
        prediction = model.predict(.2, self.FIELD)
        self.assertGreater(prediction.position.y, 100)
        self.assertGreater(prediction.uncertainty, 100)

    def test_reset_jump_does_not_create_extreme_velocity(self):
        model = KeeperModel()
        model.observe(1.0, Point(4300, -300))
        model.observe(1.02, Point(4300, 300))
        self.assertAlmostEqual(model.velocity.y, 0)


if __name__ == '__main__':
    unittest.main()
