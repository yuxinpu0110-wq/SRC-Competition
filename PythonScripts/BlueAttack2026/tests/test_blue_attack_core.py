"""纯决策层回归验证；不导入 C++，不连接真实机器人。"""
import math
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from BlueAttack2026 import Action, Field, Frame, Planner, Point, Robot, Settings
from BlueAttack2026.core import angle


def settings(**kwargs):
    return Settings(robot_ids=(1, 2), **kwargs)


def frame(cycle, ball=Point(-1600, 0), infrared=True, second=Point(-600, 1100),
          direction=0.0, phase="normal", game_on=True, can_kick=False, token=1,
          speed=0.0, valid=True, first_pos=None, second_ir=False, second_dir=0.0,
          enemies=(), velocity=Point(0, 0)):
    first_pos = first_pos or Point(ball.x - 100, ball.y)
    return Frame(cycle, ball, {1: Robot(first_pos, direction, True, infrared),
                              2: Robot(second, second_dir, True, second_ir)},
                 enemies=enemies, ball_valid=valid, phase=phase, game_on=game_on,
                 can_kick=can_kick, restart_token=token, ball_speed=speed, ball_velocity=velocity)


class ControlDistanceTests(unittest.TestCase):
    def test_loop_counts_path_instead_of_displacement(self):
        p = Planner(settings(tactic="direct"))
        points = [Point(-1600, 0), Point(-1400, 0), Point(-1400, 200),
                  Point(-1600, 200), Point(-1600, 0)]
        for cycle, ball in enumerate(points):
            actions = p.step(frame(cycle, ball))
        self.assertAlmostEqual(points[0].distance(points[-1]), 0)
        self.assertAlmostEqual(p.dribble_distance[1], 800)
        self.assertAlmostEqual(p.metrics["ball_path_mm"][1], 800)
        self.assertFalse(actions[1].dribble)
        self.assertIn("dribble limit", actions[1].reason)

    def test_infrared_dropout_keeps_budget(self):
        p = Planner(settings(tactic="direct"))
        for cycle, (x, ir) in enumerate([(-1600, True), (-1450, True),
                                         (-1300, False), (-1150, True)]):
            p.step(frame(cycle, Point(x, 0), infrared=ir))
        self.assertAlmostEqual(p.dribble_distance[1], 450)

    def test_no_sensor_simulator_still_accounts_frontal_slow_ball_path(self):
        p = Planner(settings(tactic="direct"))
        p.step(frame(0, infrared=False))
        p.step(frame(1, Point(-1300, 0), infrared=False))
        self.assertTrue(p.control[1].active)
        self.assertEqual(p.dribble_distance[1], 300)

    def test_halt_and_role_switch_do_not_reset_budget(self):
        p = Planner(settings(tactic="direct"))
        p.step(frame(0))
        p.step(frame(1, Point(-1450, 0), phase="halt"))
        p.striker = 2
        p.step(frame(2, Point(-1300, 0), infrared=False, phase="normal"))
        self.assertAlmostEqual(p.dribble_distance[1], 300)

    def test_only_confirmed_clear_separation_resets_budget(self):
        p = Planner(settings(tactic="direct"))
        p.step(frame(0))
        p.step(frame(1, Point(-1300, 0)))
        for cycle in (2, 3):
            p.step(frame(cycle, Point(-500, 0), first_pos=Point(-1400, 0), infrared=False))
            self.assertGreaterEqual(p.dribble_distance[1], 300)
        p.step(frame(4, Point(-500, 0), first_pos=Point(-1400, 0), infrared=False))
        self.assertEqual(p.dribble_distance[1], 0)
        self.assertFalse(p.control[1].active)

    def test_unconfirmed_one_frame_separation_does_not_refill(self):
        p = Planner(settings(tactic="direct"))
        p.step(frame(0))
        p.step(frame(1, Point(-1300, 0)))
        p.step(frame(2, Point(-500, 0), first_pos=Point(-1400, 0), infrared=False))
        p.step(frame(3, Point(-1300, 0)))
        self.assertGreaterEqual(p.dribble_distance[1], 1100)

    def test_uncertain_190_to_300_mm_band_cannot_lose_path_segments(self):
        p = Planner(settings(tactic="direct"))
        p.step(frame(0))
        p.step(frame(1, Point(-1400, 0), first_pos=Point(-1650, 0), infrared=False))
        p.step(frame(2, Point(-1200, 0), first_pos=Point(-1450, 0), infrared=False))
        self.assertEqual(p.dribble_distance[1], 400)
        self.assertEqual(p.metrics["ball_path_mm"][1], 400)
        self.assertTrue(p.control[1].active)

    def test_carrier_movement_is_conservative_when_ball_vision_is_stationary(self):
        p = Planner(settings(tactic="direct"))
        p.step(frame(0))
        p.step(frame(1, first_pos=Point(-1650, 0), infrared=False))
        self.assertEqual(p.dribble_distance[1], 50)
        self.assertEqual(p.metrics["ball_path_mm"][1], 0)

    def test_sampling_gap_exhausts_budget(self):
        p = Planner(settings(tactic="direct"))
        p.step(frame(0))
        result = p.step(frame(5, Point(-1580, 0)))
        self.assertGreaterEqual(p.dribble_distance[1], 800)
        self.assertFalse(result[1].dribble)

    def test_lookahead_stops_before_budget(self):
        p = Planner(settings(tactic="direct"))
        p.step(frame(0))
        result = p.step(frame(1, Point(-970, 0), speed=1000))
        self.assertEqual(p.dribble_distance[1], 630)
        self.assertLessEqual(p.remaining_budget(frame(1, Point(-970, 0), speed=1000), 1), 0)
        self.assertFalse(result[1].dribble)

    def test_exhausted_bad_alignment_releases_instead_of_carrying(self):
        p = Planner(settings(tactic="direct"))
        p.step(frame(0))
        result = p.step(frame(1, Point(-800, 0), direction=.9))
        self.assertFalse(result[1].dribble)
        self.assertTrue(result[1].avoid_ball)
        self.assertGreater(result[1].target.distance(Point(-900, 0)), 0)

    def test_carry_command_cannot_consume_more_than_remaining(self):
        p = Planner(settings(tactic="direct"))
        p.step(frame(0))
        f = frame(1, Point(-930, 0))
        result = p.step(f)
        self.assertTrue(result[1].dribble)
        self.assertLessEqual(result[1].target.distance(f.robots[1].pos), p.remaining_budget(f, 1) + 1e-6)


class RestartLockTests(unittest.TestCase):
    def make_first_kick(self, phase="indirect"):
        p = Planner(settings(tactic="direct", possession_settle_frames=1))
        ball, receiver = Point(0, 0), Point(1200, 0)
        result = p.step(frame(0, ball, second=receiver, phase=phase))
        self.assertEqual(result[1].kind, "kick")
        self.assertTrue(result[1].pass_ball)
        self.assertEqual(p.locked_kicker, 1)
        return p

    def test_no_first_kicker_retry_after_failed_restart_even_after_long_wait(self):
        p = self.make_first_kick()
        for cycle in range(1, 130):
            result = p.step(frame(cycle, Point(0, 0), second=Point(1200, 0), phase="normal"))
            self.assertNotEqual(result[1].kind, "kick")
            self.assertFalse(result[1].dribble)
        self.assertEqual(p.locked_kicker, 1)

    def test_ball_motion_49_and_50_do_not_start_play_51_does(self):
        p = self.make_first_kick()
        for cycle, distance in enumerate((49, 50), start=1):
            result = p.step(frame(cycle, Point(distance, 0), second=Point(1200, 0), infrared=False))
            self.assertIn("waiting >50", result[2].reason)
            self.assertEqual(result[2].kind, "stop")
        result = p.step(frame(3, Point(51, 0), second=Point(1200, 0), infrared=False))
        self.assertNotIn("waiting >50", result[2].reason)
        self.assertEqual(p.locked_kicker, 1)

    def test_teammate_near_without_infrared_does_not_unlock(self):
        p = self.make_first_kick()
        for cycle in range(1, 7):
            p.step(frame(cycle, Point(600, 0), first_pos=Point(-500, 0), infrared=False,
                         second=Point(500, 0), second_ir=False))
        self.assertEqual(p.locked_kicker, 1)

    def test_confirmation_requires_consecutive_frontal_teammate_contacts(self):
        p = self.make_first_kick()
        for cycle, ir in enumerate((True, True, False, True, True), start=1):
            p.step(frame(cycle, Point(600, 0), first_pos=Point(-500, 0), infrared=False,
                         second=Point(500, 0), second_ir=ir))
            self.assertEqual(p.locked_kicker, 1)
        p.step(frame(6, Point(600, 0), first_pos=Point(-500, 0), infrared=False,
                     second=Point(500, 0), second_ir=True))
        self.assertIsNone(p.locked_kicker)
        self.assertTrue(p.restart_done)

    def test_near_enemy_and_timeout_are_not_touch_evidence(self):
        p = self.make_first_kick()
        for cycle in range(1, 100):
            p.step(frame(cycle, Point(600, 0), first_pos=Point(-500, 0), infrared=False,
                         second=Point(1500, 1000), enemies=(Point(610, 0),)))
        self.assertEqual(p.locked_kicker, 1)

    def test_new_restart_token_resets_old_lock_without_refilling_control_budget(self):
        p = self.make_first_kick()
        p.step(frame(1, Point(100, 0), second=Point(1200, 0), phase="halt"))
        distance = p.dribble_distance[1]
        result = p.step(frame(2, Point(100, 0), second=Point(1200, 0), phase="direct",
                              game_on=False, token=2))
        self.assertIsNone(p.locked_kicker)
        self.assertEqual(p.restart_key, ("direct", 2))
        self.assertEqual(p.dribble_distance[1], distance)
        self.assertTrue(all(a.kind == "stop" for a in result.values()))

    def test_no_restart_command_without_permission(self):
        p = Planner(settings())
        result = p.step(frame(0, Point(0, 0), phase="kickoff", game_on=False))
        self.assertTrue(all(a.kind == "stop" for a in result.values()))
        self.assertIsNone(p.locked_kicker)

    def test_pending_indirect_first_pass_survives_normal_phase_transition(self):
        p = Planner(settings(tactic="direct", possession_settle_frames=1))
        p.step(frame(0, Point(1200, 0), second=Point(2300, 0),
                     phase="indirect", game_on=False))
        result = p.step(frame(1, Point(1200, 0), second=Point(2300, 0), phase="normal"))
        self.assertEqual(result[1].kind, "kick")
        self.assertTrue(result[1].pass_ball)
        self.assertEqual(p.locked_kicker, 1)

    def test_locked_first_kicker_never_gets_a_normal_phase_shot(self):
        p = self.make_first_kick()
        # 首踢者仍靠球且最适合射门，另一车较远；换到 normal 也必须退开。
        result = p.step(frame(1, Point(1200, 0), second=Point(2700, 900), phase="normal"))
        self.assertEqual(p.striker, 2)
        self.assertNotEqual(result[1].kind, "kick")
        self.assertFalse(result[1].dribble)
        self.assertIn("first kicker", result[1].reason)

    def test_invalid_restart_vision_does_not_forget_first_pass_requirement(self):
        p = Planner(settings(tactic="direct", possession_settle_frames=1))
        p.step(frame(0, Point(float("nan"), 0), valid=False,
                     phase="indirect", game_on=False, token=2))
        self.assertEqual(p.restart_key, ("indirect", 2))
        self.assertIsNone(p.restart_origin)
        result = p.step(frame(1, Point(1200, 0), second=Point(2300, 0), phase="normal"))
        self.assertEqual(result[1].kind, "kick")
        self.assertTrue(result[1].pass_ball)
        self.assertEqual(p.locked_kicker, 1)

    def test_referee_restart_seen_on_duplicate_frame_remains_pending(self):
        p = Planner(settings(tactic="direct", possession_settle_frames=1))
        p.step(frame(0, game_on=False))
        p.step(frame(0, phase="indirect", game_on=False, token=2))
        result = p.step(frame(1, Point(1200, 0), second=Point(2300, 0)))
        self.assertEqual(result[1].kind, "kick")
        self.assertTrue(result[1].pass_ball)

    def test_kickoff_support_stays_in_own_half_after_early_normal_transition(self):
        p = Planner(settings(tactic="direct"))
        p.step(frame(0, Point(0, 0), second=Point(-100, -300), phase="kickoff", game_on=False))
        # 接应车距球过近，不能立即出球；应继续在己方半场准备，而不是提前前插。
        result = p.step(frame(1, Point(0, 0), second=Point(-100, -300), phase="normal"))
        self.assertLessEqual(result[2].target.x, -550)
        self.assertNotEqual(result[1].kind, "kick")
        self.assertEqual(p.restart_key[0], "kickoff")


class TacticalTests(unittest.TestCase):
    def test_direct_tactic_shoots_clear_goal_after_settle(self):
        p = Planner(settings(tactic="direct"))
        ball = Point(1400, 0)
        f = frame(0, ball)
        goal = p.goal_target(f, 2)
        heading = angle(ball, goal)
        # 车中心与球的连线也按射门方向，满足前向持球几何。
        position = Point(ball.x - 100 * math.cos(heading), ball.y - 100 * math.sin(heading))
        for cycle in range(5):
            result = p.step(frame(cycle, ball, direction=heading, first_pos=position))
        self.assertEqual(result[1].kind, "kick")
        self.assertFalse(result[1].pass_ball)
        self.assertEqual(result[1].kick_target.x, 4500)

    def test_normal_failed_kick_is_one_request_until_separation(self):
        p = Planner(settings(tactic="direct", possession_settle_frames=1))
        ball = Point(1400, 0)
        heading = angle(ball, p.goal_target(frame(0, ball), 2))
        first = Point(ball.x - 100 * math.cos(heading), ball.y - 100 * math.sin(heading))
        self.assertEqual(p.step(frame(0, ball, direction=heading, first_pos=first))[1].kind, "kick")
        for cycle in range(1, 80):
            result = p.step(frame(cycle, ball, direction=heading, first_pos=first))
            self.assertNotEqual(result[1].kind, "kick")

    def test_wing_dribble_has_lateral_component(self):
        p = Planner(settings(tactic="wing"))
        initial = frame(0)
        p.step(initial)
        goal = p.goal_target(initial, 2)
        target = p.carry_target(initial, goal)
        heading = angle(initial.robots[1].pos, target)
        first = Point(initial.ball.x - 100 * math.cos(heading), initial.ball.y - 100 * math.sin(heading))
        result = p.step(frame(1, direction=heading, first_pos=first))
        self.assertEqual(p.current_tactic, "wing")
        self.assertTrue(result[1].dribble)
        self.assertGreater(abs(target.y), 800)
        self.assertGreater(abs(result[1].target.y - first.y), 10)

    def test_wall_pass_selects_teammate_and_then_return(self):
        p = Planner(settings(tactic="wall_pass", possession_settle_frames=1))
        result = p.step(frame(0, Point(-1600, 0), second=Point(-600, 0)))
        self.assertEqual(result[1].kind, "kick")
        self.assertTrue(result[1].pass_ball)
        self.assertEqual(p.wall_stage, "outbound")
        self.assertEqual(p.pass_receiver, 2)
        # 球到了 2 号车，1 号前插；接球车朝回传方向，连续观察可真实解除请求锁。
        ball = Point(-500, 0)
        return_pos = Point(-1050, 400)
        back = angle(ball, return_pos)
        receiver = Point(ball.x - 100 * math.cos(back), ball.y - 100 * math.sin(back))
        f = frame(30, ball, first_pos=return_pos, infrared=False, second=receiver,
                  second_ir=True, second_dir=back)
        result = p.step(f)
        self.assertEqual(p.striker, 2)
        self.assertEqual(p.wall_stage, "finish")
        self.assertEqual(result[2].kind, "kick")
        self.assertTrue(result[2].pass_ball)
        self.assertEqual(p.pass_receiver, 1)

    def test_automatic_changes_when_all_goal_lanes_blocked(self):
        p = Planner(settings())
        # 使用靠近球的屏障，而非只在门口的一个点，确保所有射线被遮挡。
        p.step(frame(0, enemies=(Point(-1500, -130), Point(-1500, 0), Point(-1500, 130))))
        self.assertEqual(p.current_tactic, "wing")

    def test_wing_creates_width_before_passing_from_center(self):
        p = Planner(settings(tactic="wing", possession_settle_frames=1))
        result = p.step(frame(0, enemies=(Point(-1500, -130), Point(-1500, 0), Point(-1500, 130))))
        self.assertNotEqual(result[1].kind, "kick")
        self.assertTrue(result[1].dribble)

    def test_blocked_pass_is_not_kicked_through_enemy(self):
        p = Planner(settings(tactic="wall_pass", possession_settle_frames=1))
        result = p.step(frame(0, second=Point(-600, 0), enemies=(Point(-1100, 0),)))
        self.assertNotEqual(result[1].kind, "kick")

    def test_moving_pass_receiver_faces_incoming_ball_before_next_shot(self):
        p = Planner(settings(tactic="direct"))
        p.pass_receiver, p.pass_until = 2, 100
        result = p.step(frame(0, Point(-100, 0), first_pos=Point(-1000, 0), infrared=False,
                              second=Point(700, 0), second_dir=math.pi,
                              speed=1000, velocity=Point(1000, 0)))
        receiver = result[2]
        self.assertEqual(p.striker, 2)
        self.assertTrue(receiver.dribble)
        self.assertLess(math.cos(receiver.direction), -.99)
        self.assertLess(receiver.target.x, 700)
        self.assertIn("face incoming", receiver.reason)


class SafetyTests(unittest.TestCase):
    def test_invalid_and_stale_frames_stop(self):
        p = Planner(settings())
        result = p.step(frame(0, valid=False))
        self.assertTrue(all(a.kind == "stop" for a in result.values()))
        result = p.step(frame(0))
        self.assertTrue(all(a.kind == "stop" for a in result.values()))
        result = p.step(frame(1, Point(float("nan"), 0)))
        self.assertTrue(all(a.kind == "stop" for a in result.values()))

    def test_both_penalty_boxes_prohibit_ball_contact(self):
        for x in (-3450, 3450):
            p = Planner(settings())
            result = p.step(frame(0, Point(x, 0), first_pos=Point(x - 100, 0)))
            self.assertTrue(all(a.kind == "stop" for a in result.values()))

    def test_safe_waypoint_does_not_cut_through_penalty_box(self):
        s, field = settings(), Field()
        start, end = Point(3700, -1600), Point(3700, 1600)
        waypoint = field.waypoint(start, end, s)
        self.assertLess(waypoint.x, 4500 - 1000 - field.robot_radius - s.penalty_extra)
        self.assertTrue(field.center_is_safe(waypoint, s))

    def test_only_two_requested_robot_ids_are_controlled(self):
        p = Planner(settings())
        f = frame(0)
        f.robots[9] = Robot(Point(0, 1500))
        self.assertEqual(set(p.step(f)), {1, 2})

    def test_settings_cannot_disable_dribble_safety_limit(self):
        with self.assertRaises(ValueError):
            Planner(settings(dribble_limit_mm=1000))


if __name__ == "__main__":
    unittest.main()
