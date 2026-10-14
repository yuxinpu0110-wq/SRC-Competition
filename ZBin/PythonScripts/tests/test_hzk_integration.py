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

    def test_short_history_associates_lagged_fused_snapshot(self):
        old=self.captures()[0]
        latest=Capture(0,2,100.04,self.now+.04,Point(3000,0),{('blue',0):Point(2905,0)})
        # 最新原始球已经前进200mm，融合位置仍处于40ms前；使用真实历史帧匹配。
        e=self.check([old,latest,self.captures()[1]],self.now+.04)
        self.assertTrue(e.fresh)
        self.assertEqual(e.frame_id,(0,1))

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

    def test_ready_shot_holds_position_with_kick_capable_skill(self):
        f=self.fixture
        f.players={0:Point(2606,0)}
        f.opponents=[]
        f.angles[0]=0
        for now in (0,.02,.04,.14): f.tick(now)
        self.assertTrue(any(c[0]=='kick' for c in f.calls))
        movement=next(c for c in f.calls if c[0]=='RushTo' and c[1]==0)
        self.assertEqual(movement[2:4],(2606,0))
        self.assertFalse(any(c[0]=='Stop' for c in f.calls))


class KeeperSamplingTests(unittest.TestCase):
    def test_fast_callbacks_accumulate_velocity_baseline(self):
        model=KeeperModel()
        for i in range(12): model.observe(i*.005,Point(4200,i*5))
        self.assertGreater(model.velocity.y,100)


class EmptyGoalRegressionTests(unittest.TestCase):
    def test_contact_uses_verified_detection_not_corrected_fused_ball(self):
        from observation import control_ball, Evidence
        fused=Point(3432,0)
        capture=Capture(0,1,1,1,Point(3477,0),{})
        self.assertEqual(control_ball(fused,capture,Evidence(fresh=True),'left'),Point(3477,0))
        self.assertEqual(control_ball(fused,capture,Evidence(fresh=True),'right'),Point(-3477,0))
        self.assertEqual(control_ball(fused,capture,Evidence(fresh=False),'left'),fused)
        self.assertEqual(control_ball(fused,None,Evidence(fresh=True),'left'),fused)

    def setUp(self):
        import json
        from match_tactics import Tactics
        config=json.loads((Path(__file__).resolve().parents[3]/'configs/v2.1-foundation.json').read_text())
        self.tactics=Tactics(config)

    def test_ball_before_real_defense_is_attackable(self):
        for x in (3381,3428,3470):
            self.assertEqual(self.tactics.ball_status(Point(x,0)),'')
        self.assertEqual(self.tactics.ball_status(Point(3501,0)),'WAIT_OUTSIDE_DEFENSE')

    def test_just_outside_physical_contact_cannot_consume_kick_attempts(self):
        from shot_accuracy import ShotGate
        gate=ShotGate()
        allowed,reason=gate.allow(1,Point(-111.6,0),0,Point(0,0),Point(4500,0),self.tactics.c)
        self.assertFalse(allowed)
        self.assertEqual(reason,'CONTACT')

    def test_legal_contact_point_is_not_pushed_away(self):
        ball=Point(3428,0)
        contact=Point(ball.x-95,0)
        self.assertEqual(self.tactics.legal(contact),contact)
        self.assertTrue(self.tactics.segment_legal(Point(3200,0),contact))
        self.assertFalse(self.tactics.in_defense(contact))

    def test_robot_still_cannot_enter_expanded_defense(self):
        self.assertTrue(self.tactics.in_defense(Point(3450,0)))
        safe=self.tactics.legal(Point(3450,0))
        self.assertFalse(self.tactics.in_defense(safe))

    def test_slow_simulation_clock_does_not_expire_new_packets(self):
        from observation import FreshnessGuard
        guard=FreshnessGuard(.2,60,'simulation')
        p=Point(0,0);robot={('blue',0):p}
        self.assertTrue(guard.evaluate(Capture(0,1,1,10,p,robot),10,p,{0:p},'blue','left').fresh)
        self.assertTrue(guard.evaluate(Capture(0,2,1.016,11,p,robot),11,p,{0:p},'blue','left').fresh)
        # 重复帧仍不能靠更新接收时间无限延长有效期。
        self.assertFalse(guard.evaluate(Capture(0,2,1.016,11.3,p,robot),11.3,p,{0:p},'blue','left').fresh)

    def test_watchdog_stays_alive_through_stale_vision_and_recovers(self):
        from unittest.mock import patch,MagicMock
        import safety_watchdog
        clock=[0.]
        sock=MagicMock()
        messages=iter([(4.,b'alive'),(4.1,b'fresh'),(4.2,b'close')])
        def receive(_):
            clock[0],message=next(messages)
            return message,('127.0.0.1',12345)
        sock.recvfrom.side_effect=receive
        with patch.object(safety_watchdog.socket,'socket') as factory, \
             patch.object(safety_watchdog.time,'perf_counter',side_effect=lambda:clock[0]), \
             patch.object(safety_watchdog,'stop_robots') as stop:
            factory.return_value.__enter__.return_value=sock
            safety_watchdog.worker('blue',[0,1],.2)
            self.assertEqual(sock.recvfrom.call_count,3)
            self.assertEqual(stop.call_count,2)  # 视觉缺失停车；最后退出停车，中途恢复。

    def test_empty_kick_recovery_requires_release_and_cooldown(self):
        from match_events import KickTransactions
        m=KickTransactions({})
        ball,target,robot=Point(0,0),Point(4500,0),Point(-95,0)
        for start in (0,1):
            m.request(start,0,'shot',ball,target,robot)
            m.observe(start+.46,start+.46,round(start*100),ball,{0:robot})
            m.finish()
        self.assertIsNone(m.request(1.5,0,'shot',ball,target,robot))
        self.assertFalse(m.rearm_after_release(1.5,ball,{0:Point(-300,0)}))
        self.assertFalse(m.rearm_after_release(4,ball,{0:robot}))
        self.assertTrue(m.rearm_after_release(4,ball,{0:Point(-300,0)}))
        self.assertIsNotNone(m.request(4.1,0,'shot',ball,target,robot))
