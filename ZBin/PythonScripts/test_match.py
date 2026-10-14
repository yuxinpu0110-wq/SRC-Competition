"""离线验证：实际执行官方 State/StateMachine/Task，仅替换视觉和 native 技能。

不会导入 CppPackage.pyd、连接 Client、启动仿真或给真实机器人发指令。
测试不能替代 DLL 稳定性、真实轨迹和正式规则验收。
"""
import copy
import importlib
import json
import math
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from match_tactics import Tactics, Point, clearance, distance, referee_mode
from run_match import ROOT, load_config


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.c = load_config(ROOT/'match.json')
        self.p = Tactics(self.c)

    def test_no_direct_dispatch(self):
        with patch('run_match.json.loads', return_value=dict(self.c, task_dispatch='direct')):
            with self.assertRaises(ValueError):
                load_config(ROOT/'match.json')

    def test_bad_pass_config_rejected(self):
        for name, value in [('enable_pass','false'), ('pass_flight_timeout',-1),
                            ('pass_setup_timeout',float('nan')), ('enable_dss',1), ('qt_application',1)]:
            with self.subTest(name=name), patch('run_match.json.loads', return_value=dict(self.c, **{name:value})):
                with self.assertRaises(ValueError):
                    load_config(ROOT/'match.json')

    def test_referee_gates(self):
        for message, on, kick, expected in [('GameHalt',True,True,'HALT'), ('GameStop',True,True,'STOP'),
                ('',False,False,'WAIT_START'), ('',True,True,'PLAY'), ('unexpected',True,True,'UNKNOWN_REF'),
                ('OurKickOff',False,False,'PREPARE'), ('OurKickOff',False,True,'OUR_RESTART'),
                ('OurIndirectKick',True,True,'OUR_INDIRECT'), ('OurPenaltyKick',True,True,'HALT')]:
            with self.subTest(message=message):
                self.assertEqual(referee_mode(message,on,kick), expected)

    def test_shoot_away_from_keeper(self):
        self.assertLess(self.p.shot(Point(1000,0), [Point(4250,250)])[0].y, 0)

    def test_forward_receiver_has_wider_goal_view(self):
        ball = Point(0,0)
        receiver = self.p.receiving_point(ball,[Point(4200,0)])
        self.assertGreater(receiver.x,2500)
        self.assertLessEqual(abs(receiver.y),900)
        self.assertGreater(self.p.goal_opening(receiver),1.5*self.p.goal_opening(ball))
        self.assertFalse(self.p.in_defense(receiver))

    def test_goal_opening_does_not_reward_a_wide_sideline(self):
        self.assertGreater(self.p.goal_opening(Point(2800,600)),self.p.goal_opening(Point(2200,1500)))

    def test_segment_clearance(self):
        self.assertLess(clearance(Point(0,0),Point(4000,0),[Point(2000,0)]),0)

    def test_legal_positions(self):
        for x in (-6000,-4200,0,4200,6000):
            for y in (-4000,-500,0,500,4000):
                p = self.p.legal(Point(x,y))
                self.assertLess(abs(p.x),self.c['field_length']/2)
                self.assertLess(abs(p.y),self.c['field_width']/2)
                self.assertFalse(self.p.in_defense(p))

    def test_role_hysteresis(self):
        self.p.choose(0,Point(0,0),{0:Point(-300,0),1:Point(-800,0)},[])
        self.p.choose(.1,Point(0,0),{0:Point(-700,0),1:Point(-200,0)},[])
        self.assertEqual(self.p.striker,0)
        self.p.choose(2,Point(0,0),{0:Point(-1000,0),1:Point(-200,0)},[])
        self.assertEqual(self.p.striker,1)

    def test_lost_striker_switches(self):
        self.p.striker = 0
        self.assertEqual(self.p.choose(0,Point(0,0),{1:Point(-800,0)},[]).striker,1)

    def test_no_robot(self):
        self.assertEqual(self.p.choose(0,Point(0,0),{},[]).phase,'NO_ATTACKER')

    def test_out_ball(self):
        self.assertEqual(self.p.choose(0,Point(0,4000),{0:Point(0,0)},[]).phase,'BALL_OUT')

    def test_invalid_coordinates(self):
        self.assertEqual(self.p.choose(0,Point(float('nan'),0),{},[]).phase,'NO_BALL')
        self.assertEqual(self.p.choose(0,Point(0,0),{0:Point(float('inf'),0)},[]).phase,'NO_ATTACKER')

    def test_defense_wait(self):
        self.assertEqual(self.p.choose(0,Point(4200,0),{0:Point(2500,0)},[]).phase,'WAIT_OUTSIDE_DEFENSE')

    def test_no_advance_target_exists(self):
        choice = self.p.choose(0,Point(0,0),{0:Point(-300,0)},[])
        self.assertEqual(choice.phase,'ATTACK')
        self.assertFalse(hasattr(choice,'carry_target'))

    def test_pass_disabled(self):
        self.assertIsNone(self.p.choose(0,Point(2700,0),{0:Point(2460,0),1:Point(2200,-1400)},[Point(3400,0)]).pass_target)

    def test_no_second_state_machine(self):
        self.assertFalse(hasattr(self.p,'pass_state'))
        self.assertFalse(hasattr(self.p,'pass_actions'))

    def test_telemetry_partial_line_is_retried(self):
        from benchmark_match import complete_rows
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'telemetry.jsonl'
            path.write_text('{"frame":1}\n{"frame":',encoding='utf-8')
            self.assertEqual(list(complete_rows(path)),[{'frame':1}])
            with path.open('a',encoding='utf-8') as stream:
                stream.write('2}\n')
            self.assertEqual(list(complete_rows(path)),[{'frame':1},{'frame':2}])

    def test_malformed_complete_telemetry_is_not_hidden(self):
        from benchmark_match import complete_rows
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'telemetry.jsonl'
            path.write_text('not json\n',encoding='utf-8')
            with self.assertRaises(json.JSONDecodeError):
                list(complete_rows(path))

    def test_practice_referee_repeats_same_counter(self):
        from sim_tools import PracticeReferee
        seen, packets = threading.Event(), []
        def capture(command,counter):
            packets.append((command,counter))
            if len(packets) >= 3:
                seen.set()
        with patch('sim_tools.referee',side_effect=capture):
            sender = PracticeReferee()
            try:
                sender.set(3)
                self.assertTrue(seen.wait(1))
                self.assertEqual(set(packets),{(3,packets[0][1])})
                sender.set(0)
                self.assertNotEqual(packets[-1][1],packets[0][1])
            finally:
                sender.close()
            self.assertFalse(sender._thread.is_alive())

    def test_practice_referee_rejects_unknown(self):
        from sim_tools import PracticeReferee
        sender = PracticeReferee()
        try:
            with self.assertRaises(ValueError):
                sender.set(99)
        finally:
            sender.close()


class ShotGateTests(unittest.TestCase):
    """纯几何/计时测试；不调用 C++、不把出球请求当成物理进球。"""
    def setUp(self):
        from shot_accuracy import ShotGate
        self.gate = ShotGate()
        self.c = load_config(ROOT/'match.json')
        self.ball, self.robot, self.target = Point(2700,0), Point(2605,0), Point(4500,0)

    def sample(self, now, robot=None, heading=0, ball=None):
        robot, ball = robot or self.robot, ball or self.ball
        self.gate.observe(now,robot,heading,ball)
        return self.gate.allow(now,robot,heading,ball,self.target,self.c)

    def test_first_sample_cannot_kick(self):
        self.assertFalse(self.sample(0)[0])

    def test_stationary_contact_requires_settling(self):
        self.sample(0)
        self.assertEqual(self.sample(.02),(False,'SETTLE'))
        self.assertEqual(self.sample(.12),(True,'READY'))

    def test_duplicate_timestamp_does_not_restart_stability(self):
        self.sample(0)
        self.sample(.02)
        self.sample(.02)
        self.sample(.02001)
        self.assertEqual(self.sample(.12),(True,'READY'))

    def test_old_angle_tolerance_rejected(self):
        self.sample(0,heading=.07)
        self.assertFalse(self.sample(.02,heading=.07)[0])

    def test_off_center_contact_rejected(self):
        self.assertEqual(self.sample(0,robot=Point(2605,18)),(False,'CONTACT'))

    def test_ball_not_at_kicker_rejected(self):
        self.assertEqual(self.sample(0,robot=Point(2550,0)),(False,'CONTACT'))

    def test_moving_robot_rejected(self):
        self.sample(0,robot=Point(2585,0))
        self.assertEqual(self.sample(.02),(False,'MOVING'))

    def test_rotating_robot_rejected(self):
        self.sample(0,heading=-.01)
        self.assertEqual(self.sample(.02),(False,'MOVING'))

    def test_moving_ball_rejected(self):
        self.sample(0,ball=Point(2690,0))
        self.assertEqual(self.sample(.02),(False,'MOVING'))

    def test_heading_ray_outside_goal_rejected(self):
        self.ball,self.robot,self.target = Point(2700,430),Point(2605,430),Point(4500,430)
        self.assertEqual(self.sample(0),(False,'OUTSIDE_GOAL'))

    def test_nonfinite_heading_rejected(self):
        self.assertEqual(self.sample(0,heading=float('nan')),(False,'INVALID_OBSERVATION'))

    def test_gap_requires_new_stable_window(self):
        self.sample(0); self.sample(.02); self.sample(.12)
        self.assertEqual(self.sample(1),(False,'MOVING'))
        self.assertEqual(self.sample(1.02),(False,'SETTLE'))

    def test_shot_cooldown(self):
        self.sample(0); self.sample(.02); self.sample(.12)
        self.gate.fired(.12)
        self.assertEqual(self.sample(.14),(True,'READY'))
        self.gate.fired(.14)
        self.assertEqual(self.sample(.24),(True,'READY'))
        self.assertEqual(self.sample(.32),(False,'COOLDOWN'))
        self.assertTrue(self.sample(.43)[0])

    def test_kick_pulse_stops_when_ball_moves(self):
        self.sample(0); self.sample(.02); self.sample(.12)
        self.gate.fired(.12)
        self.assertFalse(self.sample(.14,ball=Point(2800,0))[0])

    def test_kick_pulse_stops_when_alignment_is_lost(self):
        self.sample(0); self.sample(.02); self.sample(.12)
        self.gate.fired(.12)
        self.assertFalse(self.sample(.14,heading=.1)[0])

    def test_direction_change_restarts_settling(self):
        self.sample(0); self.sample(.02); self.sample(.12)
        self.target = Point(4400,0)
        self.assertEqual(self.sample(.14),(False,'SETTLE'))

    def test_pass_can_face_away_from_opponent_goal(self):
        robot,target = Point(2795,0),Point(1500,0)
        self.gate.observe(0,robot,math.pi,self.ball)
        self.gate.observe(.02,robot,math.pi,self.ball)
        self.gate.allow(.02,robot,math.pi,self.ball,target,self.c,shooting=False)
        self.gate.observe(.12,robot,math.pi,self.ball)
        self.assertTrue(self.gate.allow(.12,robot,math.pi,self.ball,target,self.c,shooting=False)[0])

    def test_opponent_config_follows_scene(self):
        from match_launcher import opponent_config
        c = opponent_config(dict(self.c,team='yellow',side='right',goalie=7),{})
        self.assertEqual((c['team'],c['side'],c['goalie'],c['profile']),('blue','left',7,'goalie'))

    def test_precision_config_checked(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            path = Path(directory)/'invalid.json'
            path.write_text(json.dumps(dict(self.c,shoot_control='typo')),encoding='utf-8')
            with self.assertRaises(ValueError): load_config(path)

    def test_probe_decodes_both_team_packets(self):
        from simulation_probe import robot_detections
        from sim_tools import field
        robot = field(2,3)+field(3,100.,5)+field(4,-20.,5)+field(5,.5,5)
        frame = field(4,2)+field(6,robot,2)
        packet = field(1,frame,2)
        self.assertEqual(list(robot_detections(packet,'yellow',3)),[(2,(100.,-20.,.5))])
        self.assertEqual(list(robot_detections(packet,'blue',3)),[])

    def test_probe_rejects_truncated_packet(self):
        from simulation_probe import fields
        with self.assertRaises(ValueError): list(fields(b'\x0a\x05x'))

    def test_cancelled_probe_stops_without_driving(self):
        import threading
        from simulation_probe import drive_pulse
        event = threading.Event(); event.set()
        with patch('simulation_probe.velocity') as moving, patch('simulation_probe.stop_robots') as stopped:
            drive_pulse('blue',0,1,event)
        moving.assert_not_called()
        self.assertEqual(stopped.call_count,3)

    def test_panel_stops_only_owned_goalie_pid(self):
        from match_launcher import App
        from unittest.mock import MagicMock
        app = App.__new__(App)
        process = MagicMock(pid=12345)
        process.poll.return_value = None
        app.opponent_process = process
        app.opponent_c = {'team':'yellow','goalie':2}
        app.opponent_output = MagicMock()
        app.opponent_status = MagicMock()
        with patch('match_launcher.subprocess.run') as run, patch('match_launcher.stop_robots') as stopped:
            app.stop_opponent()
        self.assertEqual(run.call_args.args[0],['taskkill','/PID','12345','/T','/F'])
        self.assertEqual(stopped.call_count,3)
        self.assertIsNone(app.opponent_process)

    def test_panel_detects_goalie_crash(self):
        from match_launcher import App
        from unittest.mock import MagicMock
        app = App.__new__(App)
        app.opponent_process = MagicMock()
        app.opponent_process.poll.return_value = 1
        app.stop_opponent = MagicMock()
        app.opponent_status = MagicMock()
        app.poll_opponent()
        app.stop_opponent.assert_called_once()
        self.assertIn('退出',app.opponent_status.set.call_args.args[0])

    def test_panel_reads_last_complete_telemetry(self):
        from match_launcher import latest_complete_row
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            path = Path(directory)/'rows.jsonl'
            path.write_bytes(b'{"frame":1}\n{"frame":2}\n{"fra')
            self.assertEqual(latest_complete_row(path),{'frame':2})

    def test_complete_frame_config_requires_bool(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as directory:
            path = Path(directory)/'invalid.json'
            path.write_text(json.dumps(dict(self.c,complete_frame='false')),encoding='utf-8')
            with self.assertRaises(ValueError): load_config(path)


class NativePoint:
    def __init__(self,x=0,y=0):
        self._x,self._y = x,y
    def x(self): return self._x
    def y(self): return self._y
    def __add__(self,other): return NativePoint(self.x()+other.x(),self.y()+other.y())


def fake_sdk():
    """只替换外部依赖；官方框架三个 Python 文件保持真实导入执行。"""
    calls, positions, angles = [], {}, {}
    def module(name,**attrs):
        result = types.ModuleType(name)
        result.__dict__.update(attrs)
        return result
    cpp = module('CppPackage',CGeoPoint=NativePoint,CVector=NativePoint)
    class Role:
        MatchType = types.SimpleNamespace(Auto=0,Fixed=1)
        def __init__(self,kind,number=-1):
            self.matchType,self.currentRoleNumber = kind,number
    defaults = {r:Role(0) for r in 'ABG'}
    global_ = module('Global',isPlaySwitched=False,needDetailedRuntimeDebugInfo=False,
                     hasFinishedRefPlay=False,RoleNumberStruct=Role,roleNumberStructTable={},
                     lastRoleNumberStructTable={},rolePositions={},
                     debugEngine=types.SimpleNamespace(gui_debug_msg=lambda *a,**k:None))
    global_.resetRoleNumbersTableBeforeRoleMatch = lambda: setattr(global_,'roleNumberStructTable',copy.deepcopy(defaults))
    global_.getRoleNumber = lambda role: global_.roleNumberStructTable[role].currentRoleNumber
    player = types.SimpleNamespace(Pos=lambda n:NativePoint(positions[n].x,positions[n].y),
        direction=lambda n:angles.get(n,0), getAllValidNumbers=lambda:list(positions),
        ourPlayer=lambda n:types.SimpleNamespace(Pos=lambda:NativePoint(positions[n].x,positions[n].y)))
    player._infrared = {}
    player.infraredCount = lambda n:player._infrared.get(n,0)
    vision = module('Vision',Player=player,ball=lambda:types.SimpleNamespace(Pos=lambda:NativePoint()))
    kick = types.SimpleNamespace(clearAll=lambda:None,setKick=lambda n,p:calls.append(('kick',n,p)))
    world = module('WorldModel',kickStatus=kick,worldModel=types.SimpleNamespace(SPlayFSMSwitchClearAll=lambda *a:None))
    power = module('WorldModel.PowerCalculator',P=types.SimpleNamespace(NORMAL=1,HIGH=2),evalPower=lambda *a:4000)
    skill = module('RoleMatch_LuaStyle.Skills.Skill')
    def factory(name):
        def create(*args,**kwargs):
            point = args[0] if args and isinstance(args[0],NativePoint) else NativePoint()
            return lambda n:calls.append((name,n,point.x(),point.y(),kwargs)), lambda n:point
        return create
    for name in ('RushTo','Stop','NormalShoot','Goalie2022','Goalie'):
        setattr(skill,name,factory(name))
    def no_auto_match(*a):
        raise AssertionError('Fixed-number Tasks must not fall through to automatic matching')
    modules = {m.__name__:m for m in [cpp,global_,vision,world,power,skill,
        module('Geometry',CGeoPoint=NativePoint,CVector=NativePoint),
        module('Utils',DebugColor=types.SimpleNamespace(Yellow=1)),
        module('Algorithm.munkre',DoMunkresMatch=no_auto_match)]}
    return modules,calls,positions,angles


class OfficialPlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        modules,cls.calls,cls.positions,cls.angles = fake_sdk()
        cls.modules = modules
        cls.patcher = patch.dict(sys.modules,modules)
        cls.patcher.start()
        cls.play_module = importlib.import_module('Play.Normal.Normalplay')
        cls.NormalPlay = cls.play_module.NormalPlay
        cls.Task = importlib.import_module('RoleMatch_LuaStyle.Task').Task

    @classmethod
    def tearDownClass(cls):
        cls.patcher.stop()

    def setUp(self):
        self.c = dict(load_config(ROOT/'match.json'),enable_pass=True,advance_before_shoot=False,
                      pass_shot_threshold=230,pass_score_gain=60,shoot_control='precision')
        self.play = self.NormalPlay(self.c)
        self.ball = Point(2700,0)
        self.players = {0:Point(2460,0),1:Point(2200,-1400)}
        self.opponents = [Point(3400,0)]
        self.positions.clear()
        self.angles.clear()
        self.modules['Vision'].Player._infrared.clear()
        self.modules['Global'].isPlaySwitched = False

    def tick(self,now,mode='PLAY',ball=None,players=None,valid=True,referee='',cycle=None):
        if ball is not None: self.ball = ball
        if players is not None: self.players = players
        self.positions.clear()
        self.positions.update(self.players)
        self.calls.clear()
        self.play.retained_tasks.clear()
        self.play.update_frame(now,self.ball,self.players,self.opponents,set(self.players),mode,valid,
                               referee=referee,cycle=cycle)
        self.play.planTasks()
        return self.play.current_state.state_name

    def setup_pass(self):
        self.assertEqual(self.tick(0),'Shoot')  # Hold 本帧先停车，下一帧才执行 Shoot。
        self.assertEqual(self.tick(.01),'PassSetup')
        self.assertIsNotNone(self.play.session)
        return self.play.session

    def ready_to_kick(self):
        s = self.setup_pass()
        self.prepare_receiver()
        self.prepare_sender()
        self.tick(.05)  # 移动样本不能算站稳；之后再积累稳定窗口。
        self.assertEqual(self.tick(.2),'PassKick')
        self.tick(.3)
        return s

    def prepare_sender(self):
        s = self.play.session
        angle = math.atan2(s.target.y-self.ball.y,s.target.x-self.ball.x)
        # 先给真实状态机一帧“已经退离球”的观察，再模拟完成贴球。
        # 不能从正在退球直接瞬移到球前，绕过离球阶段的锁定。
        self.players[s.sender] = Point(self.ball.x-320*math.cos(angle),self.ball.y-320*math.sin(angle))
        self.angles[s.sender] = angle
        self.tick(.03)
        self.players[s.sender] = Point(self.ball.x-95*math.cos(angle),self.ball.y-95*math.sin(angle))
        self.angles[s.sender] = angle

    def prepare_receiver(self):
        s = self.play.session
        self.players[s.receiver] = self.play.receiver_position()
        self.angles[s.receiver] = math.atan2(s.origin.y-s.target.y,s.origin.x-s.target.x)

    def launched(self):
        s = self.ready_to_kick()
        middle = Point((s.origin.x+s.target.x)/2,(s.origin.y+s.target.y)/2)
        self.assertEqual(self.tick(.5,ball=middle),'PassFlight')
        return s

    def test_actual_official_framework_loaded(self):
        from RoleMatch_LuaStyle import State,StateMachine
        self.assertTrue(issubclass(self.NormalPlay,StateMachine))
        self.assertTrue(all(isinstance(s,State) for s in self.play.states.values()))
        self.assertEqual(len(self.play.states),12)
        self.assertIn('AbortRelease',self.play.states)
        self.assertIn('ObserveFlight',self.play.states)
        self.assertNotIn('Advance',self.play.states)
        self.assertTrue(Path(sys.modules[StateMachine.__module__].__file__).name == 'StateMachine.py')

    def test_duplicate_vision_cycle_does_not_accumulate_contact_path(self):
        self.play = self.NormalPlay(dict(self.c,enable_pass=False))
        self.players[0] = Point(self.ball.x-90,self.ball.y)
        self.tick(0,cycle=12)
        self.ball = Point(self.ball.x+100,self.ball.y)
        self.players[0] = Point(self.players[0].x+100,self.players[0].y)
        self.tick(.02,cycle=12)
        episode = self.play.contact_guard.diagnostics()['0']
        self.assertEqual(episode['robot_path_mm'],0)
        self.assertEqual(episode['ball_path_mm'],0)

    def test_halt_preempts_even_when_vision_cycle_is_duplicated(self):
        self.play = self.NormalPlay(dict(self.c,enable_pass=False))
        self.tick(0,cycle=20)
        self.tick(.01,cycle=20)
        self.assertEqual(self.tick(.02,mode='HALT',cycle=20),'Hold')

    def test_precision_skill_waits_and_then_requests_kick(self):
        self.play = self.NormalPlay(dict(self.c,shoot_control='precision',enable_pass=False))
        self.opponents = []
        self.players[0] = Point(2605,0)
        self.tick(0)
        self.tick(.02)
        self.assertFalse(any(c[0]=='kick' for c in self.calls))
        self.assertEqual(self.play.phase,'WAIT_SETTLE')
        rush = [c for c in self.calls if c[0]=='RushTo' and c[1]==0][0]
        self.assertEqual(rush[2],2700-self.c.get('contact_distance',95))
        self.tick(.12)
        self.assertTrue(any(c[0]=='kick' and c[1]==0 for c in self.calls))

    def test_tasks_use_official_task_and_fixed_ids(self):
        self.tick(0,'HALT')
        tasks = self.play.states['Hold'].tasks
        self.assertEqual({t.fixedNumber for t in tasks.values()},{0,1})
        self.assertTrue(all(isinstance(t,self.Task) for t in tasks.values()))
        self.assertEqual({c[0] for c in self.calls},{'Stop'})

    def test_wait_initial_stop_does_not_move(self):
        self.assertEqual(self.tick(0,'WAIT_INITIAL_STOP'),'Hold')
        self.assertFalse(self.play.motion_ids)

    def test_setup_never_kicks(self):
        self.setup_pass()
        self.assertEqual(self.tick(.1),'PassSetup')
        self.assertFalse(any(c[0]=='kick' for c in self.calls))

    def test_ready_transition_does_not_kick_same_frame(self):
        s = self.setup_pass()
        self.prepare_receiver()
        self.prepare_sender()
        self.assertEqual(self.tick(.2),'PassKick')
        self.assertEqual(self.play.executed_state,'PassSetup')
        self.assertFalse(any(c[0]=='kick' for c in self.calls))

    def test_kick_requires_alignment(self):
        s = self.setup_pass()
        self.prepare_receiver()
        self.tick(.2)
        self.tick(.3)  # 传球车还没朝向侧翼目标。
        self.assertEqual(self.play.current_state.state_name,'PassSetup')
        self.assertEqual(self.play.pass_attempts,0)
        self.assertFalse(any(c[0]=='kick' for c in self.calls))

    def test_kick_waits_for_observed_launch(self):
        self.ready_to_kick()
        self.assertEqual(self.play.pass_attempts,1)
        self.assertEqual(self.tick(.4),'PassKick')
        self.assertEqual(self.play.pass_attempts,1)

    def test_wrong_kicker_ignored(self):
        s = self.setup_pass()
        self.prepare_receiver()
        self.tick(.2)
        self.play.note_kick(s.receiver,self.ball)
        self.assertEqual(self.play.pass_attempts,0)

    def test_actual_launch_changes_official_state(self):
        self.launched()
        self.assertEqual(self.play.pass_state,'FLIGHT')

    def test_proximity_hands_over_striker(self):
        s = self.launched()
        self.tick(.6,ball=s.target)
        self.assertEqual(self.play.pass_completed,0)  # 高速到达的一帧不能算接住。
        self.tick(.7)
        self.tick(.8)
        self.assertEqual(self.play.tactics.striker,s.receiver)
        self.assertEqual(self.play.pass_completed,1)
        self.assertIsNone(self.play.session)

    def test_receive_state_before_completion(self):
        s = self.launched()
        dx,dy = s.target.x-s.origin.x,s.target.y-s.origin.y
        length = math.hypot(dx,dy)
        near = Point(s.target.x-300*dx/length,s.target.y-300*dy/length)
        self.assertEqual(self.tick(.6,ball=near),'Receive')
        self.assertEqual(self.play.pass_completed,0)
        self.tick(.7,ball=s.target)
        self.tick(.8)
        self.tick(.9)
        self.assertEqual(self.play.pass_completed,1)

    def test_halt_preempts_kick_in_same_frame(self):
        self.ready_to_kick()
        self.assertEqual(self.tick(.4,'HALT'),'Hold')
        self.assertEqual(self.play.executed_state,'Hold')
        self.assertFalse(self.play.motion_ids)
        self.assertFalse(any(c[0]=='kick' for c in self.calls))
        self.assertEqual(self.play.pass_aborted,1)

    def test_striker_frozen_until_pass_completed(self):
        s = self.launched()
        dx,dy = s.target.x-s.origin.x,s.target.y-s.origin.y
        length = math.hypot(dx,dy)
        near = Point(s.target.x-300*dx/length,s.target.y-300*dy/length)
        self.tick(2.1,ball=near)
        self.assertEqual(self.play.tactics.striker,s.sender)
        self.assertEqual(self.play.pass_completed,0)
        self.tick(2.2,ball=s.target)
        self.tick(2.3)
        self.tick(2.4)
        self.assertEqual(self.play.tactics.striker,s.receiver)

    def test_nonfinite_robot_is_not_assigned_task(self):
        self.tick(0,'HALT',players={0:Point(float('nan'),0),1:Point(1000,0)})
        self.assertEqual({t.fixedNumber for t in self.play.states['Hold'].tasks.values()},{1})
        self.assertFalse(self.play.motion_ids)

    def test_no_advance_even_with_old_programmatic_flag(self):
        self.play = self.NormalPlay(dict(self.c,enable_pass=False,advance_before_shoot=True))
        self.opponents = []
        self.tick(0,players={0:Point(2635,0),1:Point(2000,1200)})
        self.assertEqual(self.play.current_state.state_name,'Shoot')
        self.angles[0] = 0
        self.tick(.1,ball=Point(0,0),players={0:Point(-65,0),1:Point(-1500,1200)})
        self.assertEqual(self.play.current_state.state_name,'Shoot')
        self.assertFalse(any(c[0]=='kick' for c in self.calls))

    def test_stop_retreats_but_does_not_kick(self):
        self.ready_to_kick()
        self.assertEqual(self.tick(.4,'STOP'),'Prepare')
        self.assertTrue(self.play.motion_ids)
        self.assertFalse(any(c[0]=='kick' for c in self.calls))

    def test_setup_timeout(self):
        self.setup_pass()
        self.tick(6)
        self.assertIsNone(self.play.session)
        self.assertEqual(self.play.pass_reason,'SETUP_TIMEOUT')
        self.assertEqual(self.play.executed_state,'Hold')

    def test_kick_timeout(self):
        self.ready_to_kick()
        self.tick(3)
        self.assertIsNone(self.play.session)
        self.assertEqual(self.play.pass_reason,'KICK_NOT_OBSERVED')

    def test_flight_timeout(self):
        self.launched()
        self.tick(4.1)
        self.assertIsNone(self.play.session)
        self.assertEqual(self.play.pass_reason,'FLIGHT_TIMEOUT')

    def test_lane_blocked(self):
        s = self.setup_pass()
        self.opponents.append(Point((self.ball.x+s.target.x)/2,(self.ball.y+s.target.y)/2))
        self.tick(.1)
        self.assertEqual(self.play.pass_reason,'LANE_BLOCKED')
        self.assertFalse(self.play.motion_ids)

    def test_receiver_lost(self):
        self.setup_pass()
        self.tick(.1,players={0:self.players[0]})
        self.assertEqual(self.play.pass_reason,'ROBOT_LOST')
        self.assertFalse(self.play.motion_ids)

    def test_invalid_ball_preempts(self):
        self.ready_to_kick()
        self.tick(.4,ball=Point(float('nan'),0))
        self.assertEqual(self.play.executed_state,'Hold')
        self.assertFalse(self.play.motion_ids)

    def test_out_ball_cancels(self):
        self.setup_pass()
        self.tick(.1,ball=Point(4700,0))
        self.assertEqual(self.play.executed_state,'Hold')
        self.assertIsNone(self.play.session)

    def test_defense_wait_never_kicks(self):
        self.setup_pass()
        self.tick(.1,ball=Point(4200,0))
        self.assertEqual(self.play.executed_state,'WaitOutsideDefense')
        self.assertIsNone(self.play.session)
        self.assertFalse(any(c[0]=='kick' for c in self.calls))

    def test_cancel_has_cooldown(self):
        self.setup_pass()
        self.tick(.1,'HALT')
        self.tick(.2)
        self.tick(.3)
        self.assertIsNone(self.play.session)
        self.tick(7)
        self.assertIsNotNone(self.play.session)

    def test_goalie_only_uses_official_skill(self):
        self.play = self.NormalPlay(dict(self.c,profile='goalie'))
        self.tick(0,players={2:Point(-4200,0)})
        self.tick(.1)
        self.assertEqual(self.play.executed_state,'Goalkeeping')
        self.assertTrue(any(c[0]=='Goalie' and c[1]==2 for c in self.calls))
        self.assertFalse(any(c[0]=='Goalie2022' for c in self.calls))
        self.assertEqual(self.play.motion_ids,{2})

    def test_qualifier_never_controls_goalie(self):
        self.tick(0,players={**self.players,2:Point(-4200,0)})
        self.tick(.1)
        self.assertFalse(any(c[1]==2 for c in self.calls))

    def test_play_switch_resets_and_cancels(self):
        self.setup_pass()
        self.modules['Global'].isPlaySwitched = True
        self.tick(.1)
        self.assertEqual(self.play.executed_state,'Hold')
        self.assertIsNone(self.play.session)
        self.assertFalse(self.play.motion_ids)


    def setup_feint(self):
        self.play = self.NormalPlay(dict(self.c,enable_feint=True))
        s = self.setup_pass()
        self.assertIsNotNone(s.fake_aim)
        self.assertLess(s.fake_aim.y*s.target.y,0)
        self.prepare_receiver()
        angle = math.atan2(s.fake_aim.y-self.ball.y,s.fake_aim.x-self.ball.x)
        self.players[s.sender] = Point(self.ball.x-95*math.cos(angle),self.ball.y-95*math.sin(angle))
        self.angles[s.sender] = angle
        self.assertEqual(self.tick(.1),'FakeShot')
        return s

    def test_feint_does_not_kick_and_has_bounded_duration(self):
        self.setup_feint()
        self.assertEqual(self.tick(.2),'FakeShot')
        self.assertFalse(any(c[0]=='kick' for c in self.calls))
        self.assertEqual(self.tick(.46),'PassSetup')
        self.assertFalse(any(c[0]=='kick' for c in self.calls))

    def test_feint_halt_preempts(self):
        self.setup_feint()
        self.assertEqual(self.tick(.2,'HALT'),'Hold')
        self.assertFalse(self.play.motion_ids)
        self.assertFalse(any(c[0]=='kick' for c in self.calls))

    def test_feint_lane_blocked_aborts(self):
        s = self.setup_feint()
        self.opponents.append(Point((s.target.x+self.ball.x)/2,(s.target.y+self.ball.y)/2))
        self.tick(.2)
        self.assertEqual(self.play.pass_reason,'LANE_BLOCKED')
        self.assertIsNone(self.play.session)

    def test_receiver_must_face_incoming_pass(self):
        s = self.setup_pass()
        self.players[s.receiver] = self.play.receiver_position()
        self.angles[s.receiver] = math.atan2(s.target.y-s.origin.y,s.target.x-s.origin.x)
        self.assertEqual(self.tick(.2),'PassSetup')
        self.assertFalse(any(c[0]=='kick' for c in self.calls))

    def test_receiver_stops_when_ball_crosses_target(self):
        s = self.launched()
        self.tick(.6,ball=self.players[s.receiver])
        self.assertTrue(any(c[0]=='Stop' and c[1]==s.receiver for c in self.calls))

    def test_ball_passing_behind_receiver_is_not_completion(self):
        s = self.launched()
        self.tick(.6,ball=self.players[s.receiver])
        self.assertEqual(self.play.pass_completed,0)

    def test_kick_request_contains_actual_heading_and_target(self):
        s = self.ready_to_kick()
        event = self.play.kick_events[-1]
        self.assertEqual(event['robot'],s.sender)
        self.assertEqual(event['target'],[s.target.x,s.target.y])
        self.assertEqual(event['heading'],self.angles[s.sender])

    def test_nonfinite_heading_stops_striker(self):
        self.play = self.NormalPlay(dict(self.c,enable_pass=False))
        self.tick(0)
        self.angles[0] = float('nan')
        self.tick(.1)
        self.assertTrue(any(c[0]=='Stop' and c[1]==0 for c in self.calls))

    def test_no_attack_skill_enables_dribbler(self):
        self.setup_pass()
        self.tick(.1)
        rushes = [c for c in self.calls if c[0]=='RushTo']
        self.assertTrue(rushes)
        self.assertTrue(all(c[4]['needDribble'] is False for c in rushes))

    def test_carry_action_is_rejected_at_task_boundary(self):
        self.tick(0)
        with self.assertRaises(ValueError):
            self.play.tasks({0:('carry',Point(3000,0))})

    def test_contact_budget_forces_release_before_one_metre(self):
        self.play = self.NormalPlay(dict(self.c,enable_pass=False))
        self.opponents = []
        for i in range(7):
            self.tick(i*.02,ball=Point(95+i*50,0),players={0:Point(i*50,0),1:Point(-1000,1500)})
        self.assertTrue(self.play.contact_guard.blocked(0))
        self.assertEqual(self.play.phase,'RULE_RELEASE')
        self.assertFalse(any(c[0]=='kick' for c in self.calls))
        self.tick(.15,'HALT')
        self.assertFalse(self.play.motion_ids)

    def test_robot_partly_in_defense_cannot_kick(self):
        self.play = self.NormalPlay(dict(self.c,enable_pass=False))
        self.tick(0,ball=Point(3300,0),players={0:Point(3450,0),1:Point(2000,1500)})
        self.tick(.1)
        self.assertEqual(self.play.phase,'RULE_DEFENSE_RELEASE')
        self.assertFalse(any(c[0]=='kick' for c in self.calls))

    def test_restart_kicker_cannot_touch_again_after_request(self):
        self.play = self.NormalPlay(dict(self.c,enable_pass=False))
        self.opponents = []
        self.players[0] = Point(2605,0)
        for t in (0,.02,.12):
            self.tick(t,'OUR_RESTART',referee='OurKickOff')
        self.assertEqual(self.play.restart_guard.kicker,0)
        self.tick(.14)
        self.assertTrue(self.play.restart_guard.blocked(0))
        self.assertFalse(any(c[0]=='kick' for c in self.calls))
        self.tick(.16,'HALT')
        self.assertTrue(self.play.restart_guard.blocked(0))

    def test_indirect_restart_does_not_shoot_when_no_receiver(self):
        self.play = self.NormalPlay(dict(self.c,enable_pass=False))
        self.opponents = []
        for t in (0,.02,.12,.3):
            self.tick(t,'OUR_INDIRECT',ball=Point(2700,0),players={0:Point(2605,0)},referee='OurIndirectKick')
        self.assertTrue(self.play.restart_guard.requires_pass)
        self.assertFalse(any(c[0]=='kick' for c in self.calls))


class BallControlTests(unittest.TestCase):
    def setUp(self):
        from ball_control import BallControl
        self.control = BallControl()
        self.robot, self.ball, self.target = Point(0,0),Point(95,0),Point(3000,0)

    def plan(self,now,ball=None,target=None,heading=0,carry=False):
        return self.control.plan(now,self.robot,heading,ball or self.ball,target or self.target,carry=carry)

    def test_carry_is_rejected(self):
        with self.assertRaises(ValueError):
            self.plan(0,carry=True)

    def test_small_alignment_noise_does_not_trigger_full_retreat(self):
        self.plan(0)
        dest,_,aligned,_,phase = self.control.plan(.02,Point(0,0),0,Point(95,23),Point(3000,23))
        self.assertTrue(aligned)
        self.assertEqual(phase,'CONTACT')
        self.assertLess(distance(dest,Point(95,23)),100)

    def test_off_axis_entry_cannot_use_contact_hysteresis(self):
        result = self.control.plan(0,Point(0,0),0,Point(95,23),Point(3000,23))
        self.assertFalse(result[2])

    def test_stale_contact_memory_cannot_keep_approaching(self):
        self.plan(0)
        result = self.control.plan(1,Point(0,0),0,Point(95,23),Point(3000,23))
        self.assertFalse(result[2])

    def test_retreat_completes_before_contact_can_restart(self):
        self.plan(0)
        self.plan(.02,ball=Point(95,40))
        self.assertFalse(self.plan(.04)[2])
        result = self.control.plan(.06,Point(-200,0),0,self.ball,self.target)
        self.assertTrue(result[2])

    def test_off_axis_contact_releases_before_realigning(self):
        self.plan(0)
        dest,_,_,held,phase = self.plan(.02,ball=Point(95,40))
        self.assertFalse(held)
        self.assertEqual(phase,'RELEASE_ALIGN')
        self.assertAlmostEqual(distance(dest,Point(95,40)),320)

    def test_lost_ball_releases_contact(self):
        self.plan(0)
        self.assertFalse(self.plan(.02,ball=Point(-95,0))[3])

    def test_large_gap_reacquires_instead_of_using_old_latch(self):
        self.plan(0)
        self.assertFalse(self.plan(1,ball=Point(95,60))[3])

    def test_large_turn_does_not_rotate_while_holding_ball(self):
        self.plan(0)
        result = self.plan(.02,target=Point(95,3000))
        self.assertAlmostEqual(distance(result[0],self.ball),320)
        self.assertEqual(result[4],'RELEASE_ALIGN')

    def test_shoot_turn_is_also_limited(self):
        self.plan(0,carry=False)
        result = self.plan(.02,target=Point(95,3000),carry=False)
        self.assertGreaterEqual(distance(result[0],self.ball),260)
        self.assertFalse(result[2])
        self.assertFalse(result[3])

    def test_reset_clears_all_memory(self):
        self.plan(0)
        self.control.reset()
        self.assertFalse(self.control.held)
        self.assertIsNone(self.control.direction)

    def test_lateral_trim_amplifies_small_error_without_forward_push(self):
        target = Point(3000,15)
        ball = Point(95,15)
        dest,_,_,held,_ = self.control.plan(0,self.robot,0,ball,target,trim=True)
        self.assertTrue(held)
        self.assertAlmostEqual(dest.x,10)
        self.assertAlmostEqual(dest.y,30)

    def test_lateral_trim_does_not_move_an_aligned_contact(self):
        dest,*_ = self.control.plan(0,self.robot,0,self.ball,self.target,trim=True)
        self.assertEqual(dest,Point(10,0))

    def test_reset_preflight_checks_robot_not_only_ball(self):
        from benchmark_match import scene_error
        row = dict(ball=[2800,0],ball_valid=True,robots={'0':[-1500,0]},angles={'0':0})
        self.assertEqual(scene_error(row,(2.8,0),{0:(2.705,0,0)}),'ROBOT_0_NOT_AT_START')

    def test_reset_preflight_checks_heading(self):
        from benchmark_match import scene_error
        row = dict(ball=[2800,0],ball_valid=True,robots={'0':[2705,0]},angles={'0':math.pi})
        self.assertEqual(scene_error(row,(2.8,0),{0:(2.705,0,0)}),'ROBOT_0_HEADING_NOT_AT_START')

    def test_reset_preflight_rejects_nan(self):
        from benchmark_match import scene_error
        row = dict(ball=[float('nan'),0],ball_valid=True,robots={'0':[2705,0]},angles={'0':0})
        self.assertEqual(scene_error(row,(2.8,0),{0:(2.705,0,0)}),'NO_VALID_VISION')

    def test_reset_requires_both_teams_to_observe_same_scene(self):
        from benchmark_match import opponents_match
        expected = [[1000,700],[1500,-1200]]
        self.assertTrue(opponents_match({'opponents':expected[::-1]},expected))
        self.assertFalse(opponents_match({'opponents':[[2100,700],[1500,-1200]]},expected))
        self.assertFalse(opponents_match({'opponents':[[1000,700],[1000,700]]},expected))

    def test_shoot_contact_not_65mm_push(self):
        result = self.plan(0,carry=False)
        self.assertEqual(result[0],Point(10,0))

    def test_shot_margin_is_inside_goal(self):
        c = load_config(ROOT/'match.json')
        point,_ = Tactics(c).shot(Point(2700,0),[Point(3400,0)])
        self.assertLessEqual(abs(point.y),c['goal_width']/2-160)

    def test_old_advance_flag_cannot_reenable_carry_tactics(self):
        c = dict(load_config(ROOT/'match.json'),enable_pass=False,advance_before_shoot=True)
        t = Tactics(c)
        choose = lambda x: t.choose(0,Point(x,0),{0:Point(x-100,0)},[]).phase
        self.assertEqual(choose(2000),'ATTACK')
        self.assertEqual(choose(2651),'ATTACK')
        self.assertEqual(choose(2640),'ATTACK')
        self.assertEqual(choose(2300),'ATTACK')

    def test_audit_requires_real_ball_motion(self):
        from shot_audit import audit
        event = dict(now=0,frame_ball=[0,0],target=[4500,0],heading=0,robot=0)
        rows = [dict(monotonic=i*.1,mode='PLAY',ball_valid=True,ball=[0,0],
                     kick_events=[event] if i==0 else []) for i in range(4)]
        self.assertFalse(audit(rows)[0]['observed'])
        for i,r in enumerate(rows): r['ball'] = [i*400,0]
        self.assertTrue(audit(rows)[0]['observed'])
        self.assertAlmostEqual(audit(rows)[0]['heading_error_deg'],0)

    def test_audit_rejects_halt_interruption(self):
        from shot_audit import audit
        event = dict(now=0,frame_ball=[0,0],target=[4500,0],heading=0,robot=0)
        rows = [dict(monotonic=i*.1,mode='HALT' if i==1 else 'PLAY',ball_valid=True,
                     ball=[i*400,0],kick_events=[event] if i==0 else []) for i in range(4)]
        self.assertFalse(audit(rows)[0]['observed'])

    def test_practice_sender_rejects_second_owner(self):
        from sim_tools import PracticeReferee
        sender = PracticeReferee()
        try:
            with self.assertRaises(RuntimeError):
                PracticeReferee()
        finally:
            sender.close()
        replacement = PracticeReferee()
        replacement.close()

    def test_panel_stop_closes_practice_sender(self):
        from match_launcher import App
        from unittest.mock import Mock
        app = App.__new__(App)
        app.stop_opponent = Mock()
        sender = app.practice_sender = Mock()
        app.process = app.c = app.output = None
        app.status = Mock()
        app.stop()
        sender.set.assert_called_once_with(0)
        sender.close.assert_called_once()
        self.assertIsNone(app.practice_sender)


class CompetitionRuleTests(unittest.TestCase):
    def test_old_unsafe_configurations_fail_closed(self):
        c = load_config(ROOT/'match.json')
        for changes in [dict(advance_before_shoot=True),dict(power_cap=7400),dict(shoot_control='legacy'),
                        dict(shoot_skill='baseline'),dict(goalie_skill='Goalie2022'),dict(ruleset='old'),
                        dict(contact_budget_mm=1000)]:
            with self.subTest(changes=changes),patch('run_match.json.loads',return_value=dict(c,**changes)):
                with self.assertRaises(ValueError):
                    load_config(ROOT/'match.json')

    def test_continuous_contact_path_is_limited_to_300mm(self):
        from competition_rules import ContactGuard
        g = ContactGuard()
        for i in range(7):
            g.observe(0,i*.02,Point(i*50,0),Point(i*50+95,0))
        self.assertTrue(g.blocked(0))
        self.assertEqual(g.diagnostics()['0']['robot_path_mm'],300)

    def test_control_path_is_accumulated_not_net_displacement(self):
        from competition_rules import ContactGuard
        g = ContactGuard()
        for i in range(7):
            x = (i%2)*50
            g.observe(0,i*.02,Point(x,0),Point(x+95,0))
        self.assertTrue(g.blocked(0))

    def test_clear_separation_releases_contact_budget(self):
        from competition_rules import ContactGuard
        g = ContactGuard()
        g.observe(0,0,Point(0,0),Point(95,0))
        g.episodes[0].blocked = True
        g.observe(0,.02,Point(-200,0),Point(95,0))
        self.assertTrue(g.blocked(0))
        g.observe(0,.15,Point(-200,0),Point(95,0))
        self.assertFalse(g.blocked(0))

    def test_brief_visual_separation_does_not_reset_budget(self):
        from competition_rules import ContactGuard
        g = ContactGuard()
        g.observe(0,0,Point(0,0),Point(95,0))
        g.episodes[0].blocked = True
        g.observe(0,.02,Point(-200,0),Point(95,0))
        g.observe(0,.04,Point(0,0),Point(95,0))
        self.assertTrue(g.blocked(0))

    def test_free_ball_flight_is_not_counted_as_dribbling(self):
        from competition_rules import ContactGuard
        g = ContactGuard()
        g.observe(0,0,Point(0,0),Point(95,0))
        g.observe(0,.02,Point(0,0),Point(1500,0))
        self.assertFalse(g.blocked(0))
        self.assertEqual(g.diagnostics()['0']['ball_path_mm'],0)

    def test_contacts_of_different_robots_do_not_share_budget(self):
        from competition_rules import ContactGuard
        g = ContactGuard()
        g.observe(0,0,Point(0,0),Point(95,0))
        g.episodes[0].blocked = True
        g.observe(1,0,Point(95,95),Point(95,0))
        self.assertFalse(g.blocked(1))

    def test_ball_moving_50mm_does_not_unlock_double_touch(self):
        from competition_rules import RestartGuard
        g = RestartGuard()
        g.begin('OurKickOff')
        g.requested(0,Point(0,0),{1:0})
        g.observe(Point(1000,0),{1:0},{1})
        self.assertTrue(g.started)
        self.assertTrue(g.blocked(0))

    def test_other_robot_new_infrared_unlocks_double_touch(self):
        from competition_rules import RestartGuard
        g = RestartGuard()
        g.begin('OurDirectKick')
        g.requested(0,Point(0,0),{1:4})
        g.observe(Point(1000,0),{1:7},{1})
        self.assertFalse(g.blocked(0))

    def test_stale_or_far_infrared_does_not_unlock(self):
        from competition_rules import RestartGuard
        g = RestartGuard()
        g.begin('OurKickOff')
        g.requested(0,Point(0,0),{1:4})
        g.observe(Point(1000,0),{1:4},{1})
        self.assertTrue(g.blocked(0))
        g.observe(Point(1000,0),{1:7},set())
        self.assertTrue(g.blocked(0))

    def test_repeated_restart_command_does_not_unlock_kicker(self):
        from competition_rules import RestartGuard
        g = RestartGuard()
        g.begin('OurKickOff')
        g.requested(0,Point(0,0),{})
        g.begin('OurKickOff')
        self.assertTrue(g.blocked(0))

    def test_indirect_requires_other_touch_even_after_pass_request(self):
        from competition_rules import RestartGuard
        g = RestartGuard()
        g.begin('OurIndirectKick')
        g.requested(0,Point(0,0),{})
        self.assertTrue(g.requires_pass)
        g.observe(Point(1000,0),{1:3},{1})
        self.assertFalse(g.requires_pass)


if __name__ == '__main__':
    unittest.main(verbosity=2)
