import unittest
from ball_flight import BallFlightObserver, FlightIdentity
from match_events import KickTransactions
from match_tactics import Point
from observation import Capture


class FlightTests(unittest.TestCase):
    def setUp(self):
        self.config = dict(side='left', team='blue', field_length=9000, goal_width=1000,
                           vision_max_age_s=.2, flight_observe_seconds=1.5)
        self.observer = BallFlightObserver(self.config)
        self.identity = FlightIdentity('run-a', 0, 'kick-1', '0', 1)
        self.arm()

    def arm(self, origin=Point(2700, 0)):
        self.observer.arm(self.identity, 10., 100., 0, 0, origin,
                          Point(4500, 0), {0: Point(origin.x-100, 0)})

    def sample(self, dt, x, camera=0, ball=True):
        return Capture(camera, round(dt*1000), 100+dt, 10+dt,
                       Point(x, 0) if ball else None, {})

    def consume(self, *captures, now=None, epoch=0, halted=False):
        self.observer.consume(captures, now or max(c.receive_time for c in captures),
                              epoch, halted=halted)

    def kinds(self):
        return [e['kind'] for e in self.observer.events]

    def test_duplicate_pulses_and_frames_count_one_departure(self):
        a, b = self.sample(.03, 2820), self.sample(.06, 2940)
        self.consume(a)
        for _ in range(50):
            self.arm()
            self.consume(a, b)
        self.assertEqual(self.kinds().count('BALL_DEPARTED'), 1)
        self.assertEqual(self.kinds().count('FLIGHT_SAMPLE'), 2)
        self.assertTrue(all(e['control_authority'] is False for e in self.observer.events))

    def test_halt_or_epoch_cannot_complete_old_request(self):
        for kwargs in ({'halted': True}, {'epoch': 1}):
            self.arm()
            self.consume(self.sample(.03, 2820), self.sample(.06, 2940), **kwargs)
            self.assertNotIn('BALL_DEPARTED', self.kinds())
            self.assertIsNone(self.observer.active)

    def test_jump_warns_without_departure(self):
        self.consume(self.sample(.03, 3000))
        self.assertIn('SPEED_LIMIT_SUSPECT', self.kinds())
        self.assertNotIn('BALL_DEPARTED', self.kinds())
        self.assertEqual(self.kinds()[-1], 'UNKNOWN')

    def test_stale_packets_cannot_create_evidence(self):
        self.consume(self.sample(.03, 2820), self.sample(.06, 2940), now=10.5)
        self.assertNotIn('BALL_DEPARTED', self.kinds())

    def test_ambiguous_gap_and_other_camera_are_unknown(self):
        for captures in ((self.sample(.03, 2820, ball=False),),
                         (self.sample(.2, 2820),),
                         (self.sample(.03, 2820, camera=1), self.sample(.2, 2900, camera=1))):
            self.setUp()
            self.consume(*captures)
            self.assertNotIn('BALL_DEPARTED', self.kinds())
            self.assertIsNone(self.observer.active)

    def test_other_toucher_prevents_departure(self):
        c = self.sample(.03, 2820)
        c.robots[('yellow', 2)] = Point(2820, 100)
        self.consume(c)
        self.assertEqual(self.observer.events[-1]['reason'], 'POSSIBLE_OTHER_TOUCHER')

    def test_multiple_cameras_same_time_never_make_speed(self):
        self.consume(self.sample(.03, 2820), self.sample(.03, 4000, camera=1))
        self.assertNotIn('BALL_DEPARTED', self.kinds())
        self.assertNotIn('SPEED_LIMIT_SUSPECT', self.kinds())

    def test_goal_proxy_needs_continuity_and_is_not_legality(self):
        self.observer = BallFlightObserver(self.config)
        self.arm(Point(4200, 0))
        self.consume(self.sample(.03, 4320), self.sample(.06, 4440), self.sample(.09, 4560))
        event = next(e for e in self.observer.events if e['kind'] == 'GOAL_LINE_CROSSED')
        self.assertFalse(event['rule_compliance_verified'])

    def test_mirror_uses_same_attack_coordinate_frame(self):
        self.config['side'] = 'right'
        self.observer = BallFlightObserver(self.config)
        self.arm()
        self.consume(self.sample(.03, -2820), self.sample(.06, -2940))
        self.assertIn('BALL_DEPARTED', self.kinds())

    def test_distinct_runs_do_not_suppress_same_transaction_number(self):
        self.consume(self.sample(.03, 2820), self.sample(.06, 2940))
        self.identity = FlightIdentity('run-b', 0, 'kick-1', '0', 1)
        self.arm()
        # New run resets frame chronology; observer instances are per process.
        other = BallFlightObserver(self.config)
        other.arm(self.identity, 10., 100., 0, 0, Point(2700, 0), Point(4500, 0), {0: Point(2600, 0)})
        other.consume([self.sample(.03, 2820), self.sample(.06, 2940)], 10.06, 0)
        self.assertEqual(other.events[-1]['run_id'], 'run-b')

    def test_command_cancel_does_not_revoke_read_only_audit(self):
        control = KickTransactions(self.config, 'run-a')
        control.request(10., 0, 'shot', Point(2700, 0), Point(4500, 0), Point(2600, 0))
        control.cancel(10.01, 'REFEREE_OR_STALE')
        self.consume(self.sample(.03, 2820), self.sample(.06, 2940))
        self.assertIsNone(control.active)
        self.assertIn('BALL_DEPARTED', self.kinds())

    def test_cancel_preserves_requested_epoch(self):
        control = KickTransactions(self.config, 'run-a')
        control.request(10., 0, 'shot', Point(2700, 0), Point(4500, 0), Point(2600, 0))
        control.reset_epoch = 1
        control.cancel(10.1, 'RESET')
        self.assertEqual(control.events[-1]['reset_epoch'], 0)
