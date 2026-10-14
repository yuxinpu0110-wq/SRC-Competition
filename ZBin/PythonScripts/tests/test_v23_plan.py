import json
import math
from pathlib import Path
import tempfile
import unittest
from run_match import ROOT,load_config
from match_tactics import Point,Tactics,distance
from attack_plan import EpisodeBudget,pass_plan
from strategy_contract import uses_verified_control,uses_range_gate
import test_v21 as fixtures


class PlanTests(unittest.TestCase):
    def setUp(self):self.c=load_config(ROOT/'configs/v2.3-development.json')
    def test_v23_inherits_safety(self):
        self.assertTrue(uses_verified_control(self.c));self.assertTrue(uses_range_gate(self.c))
    def test_replans_do_not_extend_budget(self):
        b=EpisodeBudget(10)
        self.assertFalse(b.observe(0,Point(0,0),1))
        self.assertFalse(b.observe(7,Point(100,0),1))
        self.assertEqual(b.deadline,10)
        self.assertTrue(b.observe(10,Point(100,0),1))
    def test_new_referee_epoch_starts_new_budget(self):
        b=EpisodeBudget(10);b.observe(0,Point(0,0),1)
        self.assertFalse(b.observe(12,Point(0,0),2));self.assertEqual(b.deadline,22)
    def test_near_slow_cooperation_does_not_replace_direct_shot(self):
        t=Tactics(self.c)
        c=t.choose(0,Point(2800,0),{0:Point(2695,0),1:Point(1500,1500)},[Point(4200,0)])
        self.assertIsNone(c.pass_target);self.assertEqual(t.proposed_plan.kind,'DIRECT_SHOT')
    def test_midfield_can_choose_one_longer_advance_leg(self):
        t=Tactics(self.c)
        c=t.choose(0,Point(0,0),{0:Point(-100,0),1:Point(-800,1000)},[])
        self.assertIsNotNone(c.pass_target)
        self.assertGreater(distance(Point(0,0),c.pass_target),1400)
        self.assertEqual(t.proposed_plan.legs[0].sdk_power,6000)
        self.assertEqual(t.proposed_plan.evidence,'HEURISTIC_NOT_CALIBRATED')
    def test_fast_finish_flag_not_silently_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'c.json';p.write_text(json.dumps(dict(self.c,enable_fast_finish=True)),encoding='utf8')
            with self.assertRaises(ValueError):load_config(p)
    def test_bool_is_not_plan_contract_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'c.json';p.write_text(json.dumps(dict(self.c,attack_plan_contract_version=True)),encoding='utf8')
            with self.assertRaises(ValueError):load_config(p)


class V23PipelineTests(fixtures.V21IntegrationTests):
    def setUp(self):
        super().setUp();self.c=load_config(ROOT/'configs/v2.3-development.json')
        self.play=self.NormalPlay(self.c);self.opponents=[]
    def receiving_fixture(self):
        from Play.Normal.Normalplay import PassSession
        origin=Point(2000,0);contact=Point(2700,0)
        self.ball=contact;self.players={0:Point(1905,0),1:Point(2810,0)}
        self.angles[0]=0;self.angles[1]=math.pi
        plan=pass_plan(origin,contact,0,1,self.players,Point(4500,0),self.play.tactics,self.c)
        self.play.active_plan=plan.freeze('plan-fixture',0,0,10)
        self.play.session=PassSession(0,1,contact,origin,0)
        self.play.tactics.striker=0
        self.play.current_state=self.play.states['PassFlight']
        self.play.transactions.request(-.2,0,'pass',origin,contact,self.players[0],
            plan_id='plan-fixture',leg_id='pass-0')
        self.play.transactions.active.state='IN_FLIGHT';self.play.transactions.active.departed_at=-.1
        self.play.ball_sample=(-.1,contact)
    def test_receive_closes_old_control_tx_and_enters_terminal_state(self):
        self.receiving_fixture();self.tick(0);self.tick(.1)
        self.assertIsNone(self.play.transactions.active)
        self.assertEqual(self.play.current_state.state_name,'TerminalPrepare')
        self.assertEqual(self.play.plan_leg,1)
    def test_terminal_request_has_same_plan_new_leg(self):
        self.receiving_fixture();self.tick(0);self.tick(.1)
        self.players[1]=Point(2605,0);self.angles[1]=0
        for t in (.12,.14,.16,.3):self.tick(t)
        tx=self.play.transactions.active
        self.assertIsNotNone(tx)
        self.assertEqual((tx.plan_id,tx.leg_id,tx.purpose),('plan-fixture','shot-1','shot'))
        self.assertIn(('kick',1,6000),self.calls)
    def test_tactical_receive_does_not_unlock_restart_touch(self):
        self.receiving_fixture()
        self.play.restart_guard.begin('OurKickOff')
        self.play.restart_guard.requested(0,Point(2000,0),{})
        self.tick(0);self.tick(.1)
        self.assertTrue(self.play.restart_guard.blocked(0))
    def test_halt_preempts_terminal(self):
        self.receiving_fixture();self.tick(0);self.tick(.1);self.tick(.12,mode='HALT')
        self.assertEqual(self.play.executed_state,'Hold')
        self.assertFalse(any(c[0]=='kick' for c in self.calls))
    def test_failed_pass_clears_frozen_plan_but_not_episode_budget(self):
        self.receiving_fixture();self.tick(0)
        deadline=self.play.episode_budget.deadline
        self.play.cancel_pass('FLIGHT_TIMEOUT')
        self.assertIsNone(self.play.active_plan)
        self.assertEqual(self.play.episode_budget.deadline,deadline)
    def test_expired_episode_does_not_reselect_every_frame(self):
        self.players={0:Point(2605,0)};self.angles[0]=0
        self.tick(0);sequence=self.play.plan_sequence
        self.tick(11);self.tick(12)
        self.assertEqual(self.play.current_state.state_name,'Hold')
        self.assertEqual(self.play.plan_sequence,sequence)
    def test_terminal_timeout_clears_receipt_timing(self):
        self.receiving_fixture();self.tick(0);self.tick(.1);self.tick(8)
        self.assertIsNone(self.play.terminal_started)
        self.assertIsNone(self.play.terminal_plan_id)


for name in dir(fixtures.V21IntegrationTests):
    if name.startswith('test_') and name not in V23PipelineTests.__dict__:setattr(V23PipelineTests,name,None)

if __name__=='__main__':unittest.main()
