"""反应式策略离线验收：真实官方状态/任务，原生动作由夹具替代。"""
import math
import unittest
from match_tactics import Point,Tactics,distance
from run_match import ROOT,load_config
from shot_opportunity import KickPlan
import reactive_policy as policy
import test_v21 as fixtures


class PolicyTests(unittest.TestCase):
    def setUp(self):self.c=load_config(ROOT/'configs/v2.2-reactive-development.json')
    def kick(self,origin,target,purpose='shot',power=6000):
        heading=math.atan2(target.y-origin.y,target.x-origin.x)
        plan=KickPlan(0,purpose,origin,target,power,0,0,policy.MODEL_ID)
        return policy.evaluate(plan,origin,heading,power,0,0,self.c)
    def test_midfield_shot_rejected(self):
        self.assertFalse(self.kick(Point(0,0),Point(4500,0)).reachable)
    def test_near_shot_is_explicitly_uncalibrated(self):
        result=self.kick(Point(2800,0),Point(4500,0))
        self.assertTrue(result.reachable)
        self.assertIn('NOT_CALIBRATED',result.reason)
        self.assertIsNone(result.time_high_s)
    def test_long_pass_rejected(self):
        self.assertFalse(self.kick(Point(0,0),Point(2000,0),'pass',4500).reachable)
    def test_short_pass_allowed(self):
        self.assertTrue(self.kick(Point(0,0),Point(1100,300),'pass',4500).reachable)
    def test_far_ball_selects_short_pass_without_waiting_for_bad_score(self):
        t=Tactics(self.c)
        choice=t.choose(1,Point(0,0),{0:Point(-100,0),1:Point(-800,1000)},[])
        self.assertIsNotNone(choice.pass_target)
        self.assertLessEqual(distance(choice.pass_target,Point(0,0)),1400)
    def test_alone_far_ball_waits_instead_of_shooting(self):
        choice=Tactics(self.c).choose(1,Point(0,0),{0:Point(-100,0)},[])
        self.assertEqual(choice.phase,'WAIT_SHORT_PASS')
    def test_front_pass_has_legal_sender_and_receiver_body_pose(self):
        tactics=Tactics(self.c);ball=Point(3380,0)
        target=tactics.receiving_point(ball,[Point(4200,0)],Point(1500,1500))
        self.assertIsNotNone(target)
        ux,uy=(target.x-ball.x)/distance(ball,target),(target.y-ball.y)/distance(ball,target)
        for pose in (Point(ball.x-110*ux,ball.y-110*uy),Point(target.x+110*ux,target.y+110*uy)):
            self.assertLess(distance(pose,tactics.legal(pose)),1)
    def test_near_goal_prefers_layoff_over_180_degree_receiver_turn(self):
        ball=Point(2800,0)
        target=Tactics(self.c).receiving_point(ball,[Point(4200,0)],Point(2200,1000))
        self.assertIsNotNone(target)
        self.assertLess(target.x,ball.x)
    def test_receive_commitment_does_not_immediately_pass_again(self):
        tactics=Tactics(self.c);tactics.shot_after_receive_until=5
        choice=tactics.choose(3,Point(2600,500),{0:Point(2495,500),1:Point(2000,1200)},[Point(4200,0)],last_pass=1)
        self.assertIsNone(choice.pass_target)
    def test_planned_release_is_progress_not_approach_failure(self):
        from ball_control import ActionBudget
        budget=ActionBudget();ball=Point(0,0);goal=Point(4500,0)
        for now,x in ((0,95),(.4,145),(.8,195),(1.2,260)):
            self.assertIsNone(budget.update(now,Point(x,0),math.pi,ball,goal,self.c,stage='RELEASE'))
    def test_stage_switch_cannot_extend_total_deadline(self):
        from ball_control import ActionBudget
        budget=ActionBudget();ball=Point(0,0);goal=Point(4500,0)
        budget.update(0,Point(95,0),math.pi,ball,goal,self.c,stage='RELEASE')
        deadline=budget.deadline
        self.assertEqual(budget.update(deadline+.1,Point(320,0),0,ball,goal,self.c,stage='ORBIT'),'ACTION_DEADLINE')


class ReactivePipelineTests(fixtures.V21IntegrationTests):
    def setUp(self):
        super().setUp()
        self.c=load_config(ROOT/'configs/v2.2-reactive-development.json')
        self.play=self.NormalPlay(self.c)
        self.opponents=[]
        self.players={0:Point(2605,0)}
        self.angles[0]=0
    def ready(self):
        for now in (0,.02,.04,.14):self.tick(now)
    def test_actual_sdk_uses_explicit_shot_power(self):
        self.ready()
        self.assertIn(('kick',0,6000),self.calls)
        self.assertFalse(self.play.travel_model.data['usable_for_planning'])
    def test_far_alone_never_sends_kick(self):
        self.players={0:Point(-100,0)}
        for now in (0,.02,.04,.14,.5,1):
            self.tick(now,ball=Point(0,0))
            self.assertFalse(any(c[0]=='kick' for c in self.calls))
    def test_halt_and_stale_preempt_reactive_shot(self):
        self.ready()
        self.tick(.16,mode='HALT')
        self.assertFalse(any(c[0]=='kick' for c in self.calls))
        self.tick(.18,fresh=False)
        self.assertFalse(any(c[0]=='kick' for c in self.calls))
    def test_far_ball_enters_official_pass_setup(self):
        self.players={0:Point(-300,0),1:Point(-500,1000)}
        self.angles[1]=math.pi
        self.tick(0,ball=Point(0,0));self.tick(.02)
        self.assertEqual(self.play.current_state.state_name,'PassSetup')
        self.assertIsNotNone(self.play.session)
        self.assertFalse(any(c[0]=='kick' for c in self.calls))
    def pass_fixture(self,state):
        from Play.Normal.Normalplay import PassSession
        self.ball=Point(0,0)
        self.players={0:Point(-95,0),1:Point(1205,0)}
        self.angles[0]=0;self.angles[1]=math.pi
        self.play.session=PassSession(0,1,Point(1100,0),self.ball,0,kick_started=0)
        self.play.current_state=self.play.states[state]
    def test_ready_short_pass_reaches_sdk_with_pass_power(self):
        self.pass_fixture('PassKick')
        for now in (0,.02,.04,.14):self.tick(now)
        self.assertIn(('kick',0,4500),self.calls)
        self.assertEqual(self.play.transactions.active.purpose,'pass')
    def test_waiting_sender_not_failed_as_no_progress_and_has_timeout(self):
        self.pass_fixture('PassSetup');self.players[1]=Point(1205,500)
        for now in (0,.1,.3,.6,.9,1.2):
            self.tick(now)
            self.assertEqual(self.play.executed_state,'PassSetup')
            self.assertFalse(any(c[0]=='kick' for c in self.calls))
        self.tick(4.2)
        self.assertIsNone(self.play.session)


for name in dir(fixtures.V21IntegrationTests):
    if name.startswith('test_') and name not in ReactivePipelineTests.__dict__:
        setattr(ReactivePipelineTests,name,None)

if __name__=='__main__':unittest.main()
