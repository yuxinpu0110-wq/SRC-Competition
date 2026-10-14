"""本机集成回归：跨相机证据、重复帧运动、过期抢占与旧功能保留。"""
from pathlib import Path
import sys
import time
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from observation import Capture, MultiCameraGuard
from match_tactics import Point
from keeper_model import KeeperModel
import test_v21 as fixtures


class MultiCameraTests(unittest.TestCase):
    def setUp(self):
        self.now = time.perf_counter()
        self.ball = Point(2800,0)
        self.players = {0:Point(2705,0),1:Point(-1500,1200)}
        self.guard = MultiCameraGuard(.2,60,'relative')

    def captures(self, frame=1, dt=0):
        return [Capture(0,frame,100+dt,self.now+dt,self.ball,{('blue',0):self.players[0]}),
                Capture(1,frame,100+dt,self.now+dt,None,{('blue',1):self.players[1]})]

    def check(self, captures, now=None, **kwargs):
        return self.guard.evaluate_all(captures,now or self.now,self.ball,self.players,
                                      kwargs.get('team','blue'),kwargs.get('side','left'))[0]

    def test_cross_camera_support_does_not_stop_attack(self):
        e=self.check(self.captures())
        self.assertTrue(e.fresh)
        self.assertEqual(e.trusted_robots,(0,1))

    def test_missing_support_is_excluded_not_global_hold(self):
        e=self.check(self.captures()[:1])
        self.assertTrue(e.fresh)
        self.assertEqual(e.trusted_robots,(0,))

    def test_missing_all_robots_blocks_motion(self):
        c=self.captures()[0]
        e=self.check([Capture(c.camera,c.frame_id,c.capture_time,c.receive_time,c.ball,{})])
        self.assertFalse(e.fresh)

    def test_duplicate_does_not_refresh_captured_age(self):
        caps=self.captures()
        self.assertTrue(self.check(caps).new_capture)
        self.assertFalse(self.check(caps,self.now+.02).new_capture)
        self.assertFalse(self.check(caps,self.now+.3).fresh)

    def test_temporally_unrelated_support_rejected(self):
        caps=self.captures()
        c=caps[1]
        caps[1]=Capture(c.camera,c.frame_id,99.9,c.receive_time,None,c.robots)
        self.assertEqual(self.check(caps).trusted_robots,(0,))

    def test_wrong_raw_ball_does_not_use_fused_loop_as_fallback(self):
        c=self.captures()[0]
        e=self.check([Capture(c.camera,c.frame_id,c.capture_time,c.receive_time,Point(0,0),c.robots)])
        self.assertFalse(e.fresh)

    def test_yellow_right_mirror(self):
        caps=[Capture(0,1,100,self.now,Point(-2800,0),{('yellow',0):Point(-2705,0)}),
              Capture(1,1,100,self.now,None,{('yellow',1):Point(1500,-1200)})]
        self.assertEqual(self.check(caps,team='yellow',side='right').trusted_robots,(0,1))

    def test_simulator_restart_invalidates_once_then_recovers(self):
        self.assertTrue(self.check(self.captures()).fresh)
        reset=[Capture(c.camera,0,1,self.now+.02,c.ball,c.robots) for c in self.captures()]
        self.assertEqual(self.check(reset,self.now+.02).reason,'CAPTURE_CLOCK_RESET')
        next_frame=[Capture(c.camera,1,1.02,self.now+.04,c.ball,c.robots) for c in reset]
        self.assertTrue(self.check(next_frame,self.now+.04).fresh)


class MotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixtures.V21IntegrationTests.setUpClass()
    @classmethod
    def tearDownClass(cls): fixtures.V21IntegrationTests.tearDownClass()
    def setUp(self):
        self.fixture=fixtures.V21IntegrationTests()
        self.fixture.setUp()
    def tearDown(self): self.fixture.tearDown()

    def test_duplicate_fresh_frame_keeps_moving_without_kick(self):
        f=self.fixture
        f.tick(0); f.tick(.02)
        for now in (.021,.022,.03):
            f.tick(now,new=False)
            self.assertEqual(f.play.executed_state,'Shoot')
            self.assertTrue(f.play.motion_ids)
            self.assertFalse(any(c[0] in ('Stop','kick') for c in f.calls))

    def test_stale_still_stops_immediately(self):
        f=self.fixture
        f.tick(0);f.tick(.02);f.tick(.3,new=False,fresh=False)
        self.assertEqual(f.play.executed_state,'Hold')
        self.assertFalse(f.play.motion_ids)

    def test_halt_on_duplicate_preempts_immediately(self):
        f=self.fixture
        f.tick(0);f.tick(.02);f.tick(.021,mode='HALT',new=False)
        self.assertEqual(f.play.executed_state,'Hold')
        self.assertFalse(f.play.motion_ids)

    def test_existing_settle_and_contact_lock_remain_enabled(self):
        f=self.fixture
        self.assertTrue(f.c['settle_on_contact'])
        self.assertTrue(f.c['lock_contact_aim'])

    def test_untrusted_visible_robot_gets_explicit_stop(self):
        from observation import Evidence
        f=self.fixture
        f.positions.clear()
        f.positions.update(f.players)
        f.play.update_frame(0,f.ball,f.players,f.opponents,set(f.players),'PLAY',True,
            observation=Evidence(reason='NO_CAPTURE',trusted_robots=()))
        f.calls.clear()
        f.play.planTasks()
        self.assertFalse(f.play.motion_ids)
        self.assertTrue(f.calls)
        self.assertTrue(all(c[0]=='Stop' for c in f.calls))


class KeeperSamplingTests(unittest.TestCase):
    def test_fast_callbacks_accumulate_velocity_baseline(self):
        model=KeeperModel()
        for i in range(12): model.observe(i*.005,Point(4200,i*5))
        self.assertGreater(model.velocity.y,100)
