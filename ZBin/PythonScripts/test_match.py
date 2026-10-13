import json
from pathlib import Path
import unittest
import threading
import tempfile
from unittest.mock import patch
from match_tactics import Planner, Point, distance, clearance, referee_mode
from run_match import load_config


class MatchTests(unittest.TestCase):
    def test_telemetry_partial_line_is_retried(self):
        from benchmark_match import complete_rows
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'telemetry.jsonl'
            path.write_text('{"frame":1}\n{"frame":', encoding='utf-8')
            self.assertEqual(list(complete_rows(path)), [{'frame':1}])
            with path.open('a', encoding='utf-8') as stream:
                stream.write('2}\n')
            self.assertEqual(list(complete_rows(path)), [{'frame':1}, {'frame':2}])

    def test_malformed_complete_telemetry_is_not_hidden(self):
        from benchmark_match import complete_rows
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'telemetry.jsonl'
            path.write_text('not json\n', encoding='utf-8')
            with self.assertRaises(json.JSONDecodeError):
                list(complete_rows(path))

    def setUp(self):
        self.c = load_config(Path(__file__).resolve().parents[2] / 'match.json')
        self.p = Planner(self.c)

    def test_halt_overrides_game_on(self):
        self.assertEqual(referee_mode('GameHalt', True, True), 'HALT')

    def test_stop_overrides_game_on(self):
        self.assertEqual(referee_mode('GameStop', True, True), 'STOP')

    def test_no_auto_start(self):
        self.assertEqual(referee_mode('', False, False), 'WAIT_START')

    def test_start(self):
        self.assertEqual(referee_mode('', True, True), 'PLAY')

    def test_unknown_fails_closed(self):
        self.assertEqual(referee_mode('unexpected', True, True), 'UNKNOWN_REF')

    def test_restart_prepare(self):
        self.assertEqual(referee_mode('OurKickOff', False, False), 'PREPARE')
        self.assertEqual(referee_mode('OurKickOff', False, True), 'OUR_RESTART')

    def test_shoot_away_from_keeper(self):
        point, _ = self.p.shot(Point(1000, 0), [Point(4250, 250)])
        self.assertLess(point.y, 0)

    def test_segment_clearance(self):
        self.assertLess(clearance(Point(0, 0), Point(4000, 0), [Point(2000, 0)]), 0)

    def test_legal_positions(self):
        for x in (-6000, -4200, 0, 4200, 6000):
            for y in (-4000, -500, 0, 500, 4000):
                p = self.p.legal(Point(x, y))
                self.assertLess(abs(p.x), self.c['field_length']/2)
                self.assertLess(abs(p.y), self.c['field_width']/2)
                self.assertFalse(self.p.in_defense(p))

    def test_fixed_ids_and_single_shooter(self):
        self.c['advance_before_shoot'] = False
        actions, _ = self.p.plan(0, Point(0, 0), {0: Point(-300, 0), 1: Point(-800, 700)}, [])
        self.assertEqual(set(actions), {0, 1})
        self.assertEqual(sum(a[0] == 'shoot' for a in actions.values()), 1)

    def test_role_hysteresis(self):
        self.p.plan(0, Point(0, 0), {0: Point(-300, 0), 1: Point(-800, 0)}, [])
        self.p.plan(0.1, Point(0, 0), {0: Point(-700, 0), 1: Point(-200, 0)}, [])
        self.assertEqual(self.p.striker, 0)
        self.p.plan(2, Point(0, 0), {0: Point(-1000, 0), 1: Point(-200, 0)}, [])
        self.assertEqual(self.p.striker, 1)

    def test_lost_striker_switches(self):
        self.p.striker = 0
        actions, _ = self.p.plan(0, Point(0, 0), {1: Point(-800, 0)}, [])
        self.assertEqual(set(actions), {1})
        self.assertEqual(self.p.striker, 1)

    def test_no_robot_no_action(self):
        self.assertEqual(self.p.plan(0, Point(0, 0), {}, [])[0], {})

    def test_no_chase_into_defense(self):
        actions, phase = self.p.plan(0, Point(4200, 0), {0: Point(2500, 0)}, [])
        self.assertEqual(phase, 'WAIT_OUTSIDE_DEFENSE')
        self.assertEqual(actions[0][0], 'move')

    def test_out_of_bounds(self):
        actions, phase = self.p.plan(0, Point(0, 4000), {0: Point(2500, 0)}, [])
        self.assertEqual(actions, {})

    def test_advance_before_shoot(self):
        self.c['advance_before_shoot'] = True
        self.c['shoot_from_x'] = 2650
        actions, phase = self.p.plan(0, Point(800, 1000), {0: Point(500, 1000)}, [])
        self.assertEqual(actions[0][0], 'carry')
        self.assertLess(actions[0][1].x, self.c['field_length']/2-self.c['defense_depth'])
        actions, phase = self.p.plan(1, Point(2700, 0), {0: Point(2600, 0)}, [])
        self.assertEqual(actions[0][0], 'shoot')

    def test_advance_never_enters_penalty(self):
        self.c['advance_before_shoot'] = True
        actions, phase = self.p.plan(0, Point(4200, 0), {0: Point(2800, 0)}, [])
        self.assertEqual(actions[0][0], 'move')
        self.assertFalse(self.p.in_defense(actions[0][1]))

    def pass_setup(self):
        self.c['enable_pass'] = True
        ball = Point(2700, 0)
        players = {0: Point(2460, 0), 1: Point(2200, -1400)}
        opponents = [Point(3400, 0)]
        actions, phase = self.p.plan(0, ball, players, opponents)
        self.assertEqual(phase, 'PASS_SETUP')
        return ball, players, opponents

    def test_pass_waits_for_receiver(self):
        ball, players, opponents = self.pass_setup()
        actions, phase = self.p.plan(.1, ball, players, opponents)
        self.assertEqual(actions[0][0], 'aim')
        self.assertEqual(actions[1][0], 'receive')
        players[1] = self.p.pass_target
        actions, phase = self.p.plan(.2, ball, players, opponents)
        self.assertEqual(actions[0][0], 'pass')

    def test_pass_changes_role_only_after_ball_arrives(self):
        ball, players, opponents = self.pass_setup()
        players[1] = self.p.pass_target
        self.p.note_kick(.3, 0, ball)
        actions, phase = self.p.plan(.4, ball, players, opponents)
        self.assertEqual(phase, 'PASS_KICK')
        self.assertEqual(actions[0][0], 'pass')
        middle = Point((ball.x+self.p.pass_target.x)/2, (ball.y+self.p.pass_target.y)/2)
        actions, phase = self.p.plan(.6, middle, players, opponents)
        self.assertEqual(phase, 'PASS_FLIGHT')
        self.assertEqual(actions[0][0], 'move')
        self.assertEqual(self.p.striker, 0)
        self.p.plan(.8, self.p.pass_target, players, opponents)
        self.assertEqual(self.p.striker, 1)
        self.assertEqual(self.p.pass_completed, 1)
        self.assertIsNone(self.p.pass_state)

    def test_wrong_robot_cannot_mark_pass_kicked(self):
        ball, players, opponents = self.pass_setup()
        self.p.note_kick(.1, 1, ball)
        self.assertEqual(self.p.pass_state, 'SETUP')
        self.assertEqual(self.p.pass_attempts, 0)

    def test_pass_setup_timeout(self):
        ball, players, opponents = self.pass_setup()
        self.p.plan(6, ball, players, opponents)
        self.assertIsNone(self.p.pass_state)
        self.assertEqual(self.p.pass_reason, 'SETUP_TIMEOUT')

    def test_pass_flight_timeout(self):
        ball, players, opponents = self.pass_setup()
        self.p.note_kick(.1, 0, ball)
        self.p.plan(4, ball, players, opponents)
        self.assertIsNone(self.p.pass_state)
        self.assertEqual(self.p.pass_completed, 0)

    def test_pass_receiver_lost(self):
        ball, players, opponents = self.pass_setup()
        self.p.plan(.1, ball, {0: players[0]}, opponents)
        self.assertIsNone(self.p.pass_state)
        self.assertEqual(self.p.pass_reason, 'ROBOT_LOST')

    def test_pass_lane_becomes_blocked(self):
        ball, players, opponents = self.pass_setup()
        target = self.p.pass_target
        opponents.append(Point((ball.x+target.x)/2, (ball.y+target.y)/2))
        self.p.plan(.1, ball, players, opponents)
        self.assertIsNone(self.p.pass_state)
        self.assertEqual(self.p.pass_reason, 'LANE_BLOCKED')

    def test_referee_cancels_pass(self):
        self.pass_setup()
        self.p.cancel_pass(.1, 'REFEREE_OR_VISION')
        self.assertIsNone(self.p.pass_state)
        self.assertEqual(self.p.pass_reason, 'REFEREE_OR_VISION')

    def test_out_ball_cancels_pass(self):
        ball, players, opponents = self.pass_setup()
        actions, phase = self.p.plan(.1, Point(4700, 0), players, opponents)
        self.assertEqual(actions, {})
        self.assertIsNone(self.p.pass_state)

    def test_no_pass_when_disabled(self):
        self.c['enable_pass'] = False
        actions, phase = self.p.plan(0, Point(2700, 0), {0: Point(2460, 0), 1: Point(2200, -1400)}, [Point(3400, 0)])
        self.assertIsNone(self.p.pass_state)
        self.assertEqual(actions[0][0], 'shoot')

    def test_pass_cancel_has_cooldown(self):
        ball, players, opponents = self.pass_setup()
        self.p.cancel_pass(.1, 'REFEREE_OR_VISION')
        self.p.plan(.2, ball, players, opponents)
        self.assertIsNone(self.p.pass_state)
        self.p.plan(7, ball, players, opponents)
        self.assertEqual(self.p.pass_state, 'SETUP')

    def test_repeated_kick_does_not_double_count(self):
        ball, players, opponents = self.pass_setup()
        self.p.note_kick(.1, 0, ball)
        self.p.note_kick(.2, 0, ball)
        self.assertEqual(self.p.pass_attempts, 1)

    def test_pass_requires_observed_launch(self):
        ball, players, opponents = self.pass_setup()
        self.p.note_kick(.1, 0, ball)
        self.p.plan(.2, ball, players, opponents)
        self.assertEqual(self.p.pass_state, 'KICK')
        self.p.plan(2, ball, players, opponents)
        self.assertIsNone(self.p.pass_state)
        self.assertEqual(self.p.pass_reason, 'KICK_NOT_OBSERVED')

    def test_bad_pass_config_rejected(self):
        path = Path(__file__).resolve().parents[2] / 'match.json'
        for name, value in [('enable_pass', 'false'), ('pass_flight_timeout', -1),
                            ('pass_setup_timeout', float('nan')), ('enable_dss', 1)]:
            with self.subTest(name=name), patch('run_match.json.loads', return_value=dict(self.c, **{name:value})):
                with self.assertRaises(ValueError):
                    load_config(path)

    def test_practice_referee_repeats_same_counter(self):
        from sim_tools import PracticeReferee
        seen = threading.Event()
        packets = []
        def capture(command, counter):
            packets.append((command, counter))
            if len(packets) >= 3:
                seen.set()
        with patch('sim_tools.referee', side_effect=capture):
            sender = PracticeReferee()
            try:
                sender.set(3)
                self.assertTrue(seen.wait(1), 'Periodic referee did not repeat')
                self.assertEqual(set(packets), {(3, packets[0][1])})
                sender.set(0)
                self.assertNotEqual(packets[-1][1], packets[0][1])
            finally:
                sender.close()
            self.assertFalse(sender._thread.is_alive())

    def test_practice_referee_rejects_unknown_command(self):
        from sim_tools import PracticeReferee
        sender = PracticeReferee()
        try:
            with self.assertRaises(ValueError):
                sender.set(99)
        finally:
            sender.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
