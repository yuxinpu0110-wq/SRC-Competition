"""验证新证据/事务与真实官方状态机的连接；不发实际控制命令。"""
import math
import time
import unittest
from unittest.mock import patch
from observation import Capture, Evidence, FreshnessGuard, decode_detection
from match_tactics import Point, Tactics
from match_events import KickTransactions
from ball_control import ActionBudget
from shot_accuracy import ShotGate
from run_match import ROOT, load_config
from test_match import OfficialPlayTests
from sim_tools import field
from kick_calibration import analyze, fit_window, rolling_time


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.wall = time.time()
        self.guard = FreshnessGuard(.2, 60)
        self.ball = Point(2700,0)
        self.players = {0:Point(2600,0)}

    def capture(self, n=1, t=0, receive=1, camera=0):
        return Capture(camera,n,self.wall+t,receive,self.ball,{('blue',0):self.players[0]})

    def evaluate(self, capture, now=1):
        with patch('observation.time.time', return_value=self.wall+now-1):
            return self.guard.evaluate(capture,now,self.ball,self.players,'blue','left',999)

    def test_repeated_capture_does_not_refresh_age(self):
        self.assertTrue(self.evaluate(self.capture()).new_capture)
        for i in range(100):
            ev = self.evaluate(self.capture(receive=1+i*.001),1+i*.001)
            self.assertFalse(ev.new_capture)
        self.assertFalse(self.evaluate(self.capture(receive=1.3),1.3).fresh)

    def test_loop_cycle_is_not_evidence(self):
        self.assertFalse(self.evaluate(None).fresh)

    def test_multi_camera_and_out_of_order_rejected(self):
        self.evaluate(self.capture())
        self.assertEqual(self.evaluate(self.capture(2,.02,1.02,1),1.02).reason,'CAMERA_CHANGED')
        self.assertFalse(self.evaluate(self.capture(3,-.01,1.03),1.03).new_capture)

    def test_mirror_matches_team_and_side(self):
        raw = Capture(0,1,self.wall,1,Point(-2700,0),{('yellow',0):Point(-2600,0)})
        self.assertTrue(self.guard.evaluate(raw,1,self.ball,self.players,'yellow','right').new_capture)

    def test_raw_fusion_mismatch_holds(self):
        c = self.capture()
        self.assertFalse(self.guard.evaluate(c,1,Point(0,0),self.players,'blue','left').fresh)

    def test_official_detection_fields_keep_camera_timestamp(self):
        ball = field(1,1.,5)+field(3,2700.,5)+field(4,0.,5)
        robot = field(1,1.,5)+field(2,0)+field(3,2600.,5)+field(4,0.,5)
        frame = field(1,7)+field(2,self.wall,1)+field(4,2)+field(5,ball,2)+field(7,robot,2)
        c = list(decode_detection(field(1,frame,2),1))[0]
        self.assertEqual((c.camera,c.frame_id,c.capture_time),(2,7,self.wall))
        self.assertEqual(c.robots[('blue',0)],self.players[0])

    def test_gate_cannot_settle_by_waiting_on_one_capture(self):
        gate = ShotGate()
        config = load_config(ROOT/'match.json')
        robot,ball,target = Point(2605,0),self.ball,Point(4500,0)
        gate.observe(0,robot,0,ball,frame_id=(0,1))
        gate.allow(0,robot,0,ball,target,config)
        gate.observe(.02,robot,0,ball,frame_id=(0,2))
        gate.allow(.02,robot,0,ball,target,config)
        for i in range(100):
            gate.observe(.02,robot,0,ball,frame_id=(0,2))
            self.assertFalse(gate.allow(.02+i*.01,robot,0,ball,target,config)[0])
        self.assertEqual(gate.stable_since,.02)


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.manager = KickTransactions({})
        self.ball,self.target,self.robot = Point(0,0),Point(4500,0),Point(-95,0)

    def request(self,t=0):
        return self.manager.request(t,0,'shot',self.ball,self.target,self.robot)

    def observe(self,t,x,new=True,reset=False):
        self.manager.observe(t,t,round(t*1000),Point(x,0),{0:self.robot},new=new,reset=reset)

    def test_fifty_pulses_one_departure(self):
        ids = [self.request(i*.003) for i in range(50)]
        self.assertEqual(set(ids),{1})
        for t,x in ((.15,0),(.2,100),(.23,160),(.26,220),(.3,300)):
            self.observe(t,x)
        self.assertEqual([e['kind'] for e in self.manager.events].count('KICK_REQUESTED'),1)
        self.assertEqual([e['kind'] for e in self.manager.events].count('BALL_DEPARTED'),1)
        self.assertEqual(self.manager.active.pulse_count,50)
        self.assertIsNone(self.request(.31))

    def test_no_departure_hard_deadline_and_retry_limit(self):
        for start in (0,1):
            self.request(start)
            self.observe(start+.46,0)
            self.assertEqual(self.manager.active.state,'NO_DEPARTURE')
            self.manager.finish()
        self.assertIsNone(self.request(2))
        self.assertTrue(self.manager.diagnostics()['retry_exhausted'])

    def test_reset_halt_stale_do_not_confirm_pending(self):
        self.request()
        self.observe(.02,0)
        self.observe(.05,200,reset=True)
        self.assertFalse(any(e['kind']=='BALL_DEPARTED' for e in self.manager.events))
        self.assertIsNone(self.manager.active)

    def test_fifty_mm_jitter_is_not_departure(self):
        self.request()
        for t,x in ((.02,0),(.05,20),(.08,50),(.12,50)):
            self.observe(t,x)
        self.assertFalse(any(e['kind']=='BALL_DEPARTED' for e in self.manager.events))

    def test_duplicate_capture_cannot_confirm(self):
        self.request()
        self.observe(.02,0)
        for i in range(100):
            self.observe(.05,100,new=False)
        self.assertEqual(self.manager.active.departure_samples,0)

    def test_teleport_is_cancelled(self):
        self.request()
        self.observe(.02,0)
        self.observe(.03,2000)
        self.assertIsNone(self.manager.active)


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.c = load_config(ROOT/'configs/v2.1-foundation.json')

    def test_progress_timeout_and_long_approach(self):
        b=ActionBudget()
        ball,target=Point(0,0),Point(4500,0)
        self.assertIsNone(b.update(0,Point(-3000,0),0,ball,target,self.c))
        self.assertGreater(b.deadline,10)
        self.assertIsNone(b.update(.4,Point(-2800,0),0,ball,target,self.c))
        self.assertEqual(b.update(1.1,Point(-2800,0),0,ball,target,self.c),'ACTION_NO_PROGRESS')

    def test_angle_wrap_does_not_create_full_circle(self):
        b=ActionBudget()
        c=dict(self.c,contact_distance=95)
        b.update(0,Point(95,0),-math.pi+.001,Point(0,0),Point(-4500,0),c)
        self.assertLess(b.best,1)

    def test_entity_segment_crossing_defense_is_rejected(self):
        t=Tactics(self.c)
        a,b=Point(3900,1400),Point(3900,-1400)
        self.assertFalse(t.in_defense(a))
        self.assertFalse(t.in_defense(b))
        self.assertFalse(t.segment_legal(a,b))
        waypoint=t.safe_waypoint(a,b)
        self.assertIsNotNone(waypoint)
        self.assertTrue(t.segment_legal(a,waypoint))

    def test_commit_is_bounded_and_material_ball_change_replans(self):
        tactics=Tactics(self.c)
        ball,players=Point(2700,0),{0:Point(2605,0)}
        tactics.choose(0,ball,players,[Point(4200,250)])
        old=tactics.target
        tactics.choose(.1,ball,players,[Point(4200,240)])
        self.assertEqual(tactics.target,old)
        tactics.choose(3,ball,players,[Point(4200,250)])
        self.assertEqual(tactics.target_time,3)
        tactics.choose(3.1,Point(2801,0),players,[Point(4200,250)])
        self.assertEqual(tactics.target_time,3.1)


class CalibrationTests(unittest.TestCase):
    def test_operator_panel_does_not_call_a_request_a_departure(self):
        from match_launcher import v21_status
        row=dict(observation=dict(fresh=True),kick_transaction=dict(active=dict(state='REQUESTED',pulse_count=50)))
        self.assertIn('等待离脚证据',v21_status(row))
        self.assertNotIn('已确认',v21_status(row))
        row['observation']=dict(fresh=False,reason='STALE_CAPTURE')
        self.assertIn('进攻暂停',v21_status(row))

    def test_gui_entry_uses_explicit_v21_config(self):
        import match_launcher
        config=ROOT/'configs/v2.1-foundation.json'
        with patch('match_launcher.tk.Tk') as window, patch('match_launcher.App') as app:
            match_launcher.main(['--config',str(config)])
            app.assert_called_once_with(window.return_value,config_path=config)

    def test_power_is_not_a_speed_and_unreachable_ball_stops(self):
        self.assertIsNone(rolling_time(3000,1500,600))
        self.assertAlmostEqual(rolling_time(1000,2000,0),.5)
        self.assertGreater(rolling_time(1000,2000,500),.5)
        self.assertIsNone(rolling_time(1000,float('nan'),0))

    def test_linear_raw_fit_and_mirror_heading(self):
        samples=[dict(camera=0,frame_id=i,capture_time=i*.02,ball=[-2000*i*.02,0]) for i in range(8)]
        result=fit_window(samples,math.pi)
        self.assertTrue(result['valid'])
        self.assertAlmostEqual(result['observed_speed_median_mm_s'],2000)
        self.assertAlmostEqual(result['target_error_deg'],0)
        samples[-1]['camera']=1
        self.assertEqual(fit_window(samples,math.pi)['reason'],'CAMERA_CHANGED')

    def test_no_samples_cannot_become_a_calibration(self):
        self.assertEqual(analyze([])['status'],'NO_SAMPLES')
        self.assertFalse(analyze([])['usable_for_planning'])

    def test_failed_requests_are_in_denominator(self):
        event=dict(kind='KICK_REQUESTED',transaction_id=1,robot=0,now=0,
                   start_ball={'x':0,'y':0},target={'x':4500,'y':0})
        row=dict(physical_events=[event],kick_events=[dict(transaction_id=1,power=3500)])
        report=analyze([row])
        self.assertEqual(report['groups']['robot-0/power-3500']['attempts'],1)
        self.assertEqual(report['groups']['robot-0/power-3500']['departure_rate'],0)


class V21IntegrationTests(OfficialPlayTests):
    """复用真实框架夹具，只运行本类的新用例，不重复继承原测试集合。"""
    def setUp(self):
        super().setUp()
        self.c = dict(load_config(ROOT/'configs/v2.1-foundation.json'),enable_pass=False)
        self.play=self.NormalPlay(self.c)

    def tick(self,now,mode='PLAY',ball=None,players=None,valid=True,referee='',new=True,fresh=True):
        if ball is not None: self.ball=ball
        if players is not None: self.players=players
        self.positions.clear(); self.positions.update(self.players)
        self.calls.clear(); self.play.retained_tasks.clear()
        evidence=Evidence(new,fresh,'TEST_CAPTURE',now,now,(0,round(now*1000)))
        self.play.update_frame(now,self.ball,self.players,self.opponents,set(self.players),mode,valid,referee,observation=evidence)
        self.play.planTasks()
        return self.play.current_state.state_name

    def test_v21_stale_preempts_every_attack_state(self):
        self.tick(0)
        for name in ('Shoot','PassSetup','PassKick','PassFlight','Receive','ObserveFlight'):
            self.play.current_state=self.play.states[name]
            self.tick(.2,fresh=False)
            self.assertEqual(self.play.executed_state,'Hold')
            self.assertFalse(any(c[0]=='kick' for c in self.calls))

    def test_v21_duplicate_does_not_emit_or_advance_stability(self):
        self.tick(0)
        self.tick(.02)
        previous=self.play.shot_gates[0].previous
        for i in range(100):
            self.tick(.02+i*.001,new=False)
            self.assertFalse(any(c[0]=='kick' for c in self.calls))
        self.assertEqual(self.play.shot_gates[0].previous,previous)

    def test_v21_departure_enters_official_observe_flight(self):
        self.tick(0)
        self.play.transactions.request(.02,0,'shot',self.ball,Point(4500,0),self.players[0])
        self.play.transactions.active.state='IN_FLIGHT'
        self.play.transactions.active.departed_at=.04
        self.tick(.05)
        self.assertEqual(self.play.current_state.state_name,'ObserveFlight')
        self.tick(.06)
        self.assertFalse(any(c[0]=='kick' for c in self.calls))

    def test_v21_no_departure_enters_official_release(self):
        self.tick(0)
        self.play.transactions.request(.02,0,'shot',self.ball,Point(4500,0),self.players[0])
        self.tick(.48)
        self.assertEqual(self.play.executed_state,'AbortRelease')
        self.assertFalse(any(c[0]=='kick' for c in self.calls))

    def test_v21_actual_skill_pulses_share_tx_and_confirm_once(self):
        self.opponents=[]
        self.players={0:Point(2605,0)}
        self.angles[0]=0
        self.tick(0)
        self.tick(.02)
        self.tick(.04)
        self.tick(.14)
        self.assertTrue(any(c[0]=='kick' for c in self.calls))
        first=self.play.transactions.active.id
        self.tick(.16)
        self.assertTrue(any(c[0]=='kick' for c in self.calls))
        self.assertEqual(self.play.transactions.active.id,first)
        self.tick(.2,ball=Point(2800,0))
        self.tick(.23,ball=Point(2860,0))
        self.assertEqual(self.play.current_state.state_name,'ObserveFlight')
        self.tick(.26,ball=Point(2920,0))
        self.assertFalse(any(c[0]=='kick' for c in self.calls))
        self.assertEqual(sum(e['kind']=='BALL_DEPARTED' for e in self.play.transactions.events),1)


# 从真实框架夹具继承 setUp/tearDown；旧测试另由 test_match 执行。
for _name in tuple(name for name in dir(OfficialPlayTests) if name.startswith('test_')):
    setattr(V21IntegrationTests,_name,None)
del OfficialPlayTests


if __name__ == '__main__':
    unittest.main()
