"""比赛候选策略：官方 State → Task → Skill 结构。

阅读顺序：Advance/Shoot → PassSetup → PassKick → PassFlight → Receive。
getTasks() 决定本帧任务，transFunction() 返回下一状态的类名。
官方框架是“先执行任务，再切换状态”，新状态任务在下一帧执行。
只有停赛/视觉丢失等安全中断在 hookBeforePlanTasks 中立即抢占，避免多踢一帧。

这是按当前本地 SDK 编写的 Normalplay，最终提交路径/接口仍需正式模板确认。
不包含正式点球、间接任意球或实车验收；不宣称修复 Client 原生崩溃。
"""
from dataclasses import dataclass
import math
import Global
import CppPackage as C
from RoleMatch_LuaStyle import State, StateMachine, Task, declare_state_machine
from RoleMatch_LuaStyle.Skills import Skill
from RoleMatch_LuaStyle.Skills import CompetitionSkills
from WorldModel.PowerCalculator import evalPower, P
from match_tactics import Point, Tactics, distance, clearance, finite


@dataclass
class Frame:
    now: float
    ball: Point
    players: dict
    opponents: list
    visible: set
    mode: str
    ball_valid: bool


@dataclass
class PassSession:
    """传球参数/计时数据；没有独立 state 字段，状态只属于官方 StateMachine。"""
    sender: int
    receiver: int
    target: Point
    origin: Point
    started: float
    kick_started: float = 0.0
    flight_started: float = 0.0
    kick_requested: bool = False


class MatchState(State):
    """所有状态共享 Play 的观察和辅助函数；车号通过 Task.fixedNumber 绑定。"""
    def getMatchString(self):
        return ''

    def transFunction(self):
        return ''


class Hold(MatchState):
    def getTasks(self):
        return self.play.tasks({})  # 显式给有效受控车分配 Skill.Stop。

    def transFunction(self):
        return self.play.normal_state() if self.play.can_attack else ''


class Prepare(MatchState):
    """STOP/定位球准备：禁止踢球，只让距球过近的车退开。"""
    def getTasks(self):
        p, f = self.play, self.play.frame
        actions = {}
        for index, number in enumerate(p.ids):
            pos = f.players.get(number)
            if pos is not None and distance(pos, f.ball) < 650:
                angle = math.atan2(pos.y-f.ball.y, pos.x-f.ball.x) if distance(pos, f.ball) > 1 else math.pi+index
                target = p.tactics.legal(Point(f.ball.x+750*math.cos(angle), f.ball.y+750*math.sin(angle)))
                if distance(target, f.ball) >= 650:
                    actions[number] = ('retreat', target)
        return p.tasks(actions)

    def transFunction(self):
        return self.play.normal_state() if self.play.can_attack else ''


class Advance(MatchState):
    """带到进攻区域；没有足够的推进距离时才交给 Shoot。"""
    def getTasks(self):
        return self.play.attacking_tasks(carry=True)

    def transFunction(self):
        return self.play.attack_transition()


class Shoot(MatchState):
    """瞄准选出的门内空隙射门；准备传球时本帧只瞄准、不抢先射门。"""
    def getTasks(self):
        return self.play.attacking_tasks(carry=False)

    def transFunction(self):
        return self.play.attack_transition()


class WaitOutsideDefense(MatchState):
    def getTasks(self):
        p, d = self.play, self.play.choice
        actions = {}
        if d.striker is not None:
            actions[d.striker] = ('move', p.tactics.legal(p.frame.ball))
        if d.other is not None:
            actions[d.other] = ('move', d.support)
        return p.tasks(actions, goalie=True)

    def transFunction(self):
        return self.play.normal_state()


class PassSetup(MatchState):
    """接球车先就位；传球车只瞄准。这里不发踢球请求。"""
    def getTasks(self):
        p, session = self.play, self.play.session
        return p.tasks({session.sender: ('aim', session.target),
                        session.receiver: ('receive', session.target)}, goalie=True)

    def transFunction(self):
        p, session = self.play, self.play.session
        if distance(p.frame.players[session.receiver], session.target) < 180:
            session.kick_started = p.frame.now
            return 'PassKick'
        return ''


class PassKick(MatchState):
    """请求出球后保持接触，看到球确实离开发球车才转入飞行阶段。"""
    def getTasks(self):
        p, session = self.play, self.play.session
        return p.tasks({session.sender: ('pass', session.target),
                        session.receiver: ('receive', session.target)}, goalie=True)

    def transFunction(self):
        p, session = self.play, self.play.session
        dx, dy = session.target.x-session.origin.x, session.target.y-session.origin.y
        length = max(1, math.hypot(dx, dy))
        forward = ((p.frame.ball.x-session.origin.x)*dx+(p.frame.ball.y-session.origin.y)*dy)/length
        if session.kick_requested and forward > 200 and distance(p.frame.ball, p.frame.players[session.sender]) > 210:
            session.flight_started = p.frame.now
            return 'PassFlight'
        return ''


class PassFlight(MatchState):
    """球在途中：发球车后撤，接球车留在落点，不要两辆车一起追球。"""
    def getTasks(self):
        return self.play.receiving_tasks(chase=False)

    def transFunction(self):
        p, session = self.play, self.play.session
        if p.finish_pass_if_near():
            return p.normal_state()
        if distance(p.frame.ball, session.target) < 550 and distance(p.frame.ball, session.origin) > 350:
            return 'Receive'
        return ''


class Receive(MatchState):
    def getTasks(self):
        return self.play.receiving_tasks(chase=True)

    def transFunction(self):
        return self.play.normal_state() if self.play.finish_pass_if_near() else ''


class Goalkeeping(MatchState):
    def getTasks(self):
        return self.play.tasks({}, goalie=True)

    def transFunction(self):
        return self.play.normal_state()


@declare_state_machine(Hold, Prepare, Advance, Shoot, WaitOutsideDefense,
                       PassSetup, PassKick, PassFlight, Receive, Goalkeeping)
class NormalPlay(StateMachine):
    """唯一的战术状态机；runner 每帧只更新观察并调用 planTasks()。"""
    def __init__(self, config):
        super().__init__()
        self.c = config
        self.tactics = Tactics(config)
        self.ids = (config['attackers'] if config['profile'] == 'qualifier' else
                    [config['goalie']] if config['profile'] == 'goalie' else config['attackers']+[config['goalie']])
        self.frame = self.choice = self.session = None
        self.last_pass = -100.0
        self.pass_attempts = self.pass_completed = self.pass_aborted = 0
        self.pass_reason = ''
        self.retained_tasks = []
        self.motion_ids = set()
        self.phase = self.strategy_phase = self.executed_state = 'Hold'

    @property
    def pass_state(self):
        # 遥测兼容旧字段，但数据来自真正的官方 current_state，不再维护另一套状态。
        if self.session is None:
            return None
        return {'PassSetup':'SETUP', 'PassKick':'KICK', 'PassFlight':'FLIGHT', 'Receive':'RECEIVE'}.get(self.current_state.state_name)

    @property
    def can_attack(self):
        return self.frame.mode in ('PLAY', 'OUR_RESTART') and self.frame.ball_valid and finite(self.frame.ball)

    def update_frame(self, now, ball, players, opponents, visible, mode, ball_valid):
        clean_players = {n:p for n,p in players.items() if finite(p)}
        self.frame = Frame(now, ball, clean_players, [p for p in opponents if finite(p)],
                           set(visible) & set(clean_players), mode, ball_valid)

    def normal_state(self):
        if not self.can_attack or self.choice.phase in ('NO_BALL', 'BALL_OUT'):
            return 'Hold'
        if self.c['profile'] == 'goalie':
            return 'Goalkeeping'
        if self.choice.phase == 'NO_ATTACKER':
            return 'Goalkeeping' if self.c['profile'] == 'full' else 'Hold'
        if self.choice.phase == 'WAIT_OUTSIDE_DEFENSE':
            return 'WaitOutsideDefense'
        return 'Advance' if self.choice.phase == 'ADVANCE' else 'Shoot'

    def cancel_pass(self, reason):
        if self.session is not None:
            self.pass_aborted += 1
            self.last_pass, self.pass_reason = self.frame.now, reason
        self.session = None

    def hookBeforePlanTasks(self):
        if self.frame is None:
            raise RuntimeError('Call update_frame before planTasks')
        # 官方装饰器在 __init__ 返回后才创建 State 实例，在这里绑定共享 Play。
        for state in self.states.values():
            state.play = self
        f = self.frame
        self.motion_ids = set()
        # 传球途中锁住主攻权；球只是靠近接球车还不够，必须由完成条件交接。
        self.choice = self.tactics.choose(f.now, f.ball, f.players, f.opponents, self.last_pass,
                                          freeze_striker=self.session is not None)
        forced, reason = None, ''
        if not f.ball_valid or not finite(f.ball):
            forced, reason = 'Hold', 'NO_BALL'
        elif f.mode not in ('PLAY', 'OUR_RESTART'):
            forced = 'Prepare' if f.mode in ('STOP', 'PREPARE', 'DEFEND_RESTART') else 'Hold'
            reason = f.mode
        elif self.choice.phase in ('BALL_OUT', 'WAIT_OUTSIDE_DEFENSE'):
            forced, reason = self.normal_state(), self.choice.phase
        elif Global.isPlaySwitched:
            forced, reason = 'Hold', 'PLAY_SWITCHED'
        if forced is not None:
            self.cancel_pass(reason)
        elif self.session is not None:
            s = self.session
            state = self.current_state.state_name
            if s.sender not in f.players or s.receiver not in f.players:
                reason = 'ROBOT_LOST'
            elif state in ('PassSetup', 'PassKick') and clearance(f.ball, s.target, f.opponents) < 180:
                reason = 'LANE_BLOCKED'
            elif state == 'PassSetup' and f.now-s.started > self.c.get('pass_setup_timeout', 5.0):
                reason = 'SETUP_TIMEOUT'
            elif state == 'PassKick' and f.now-s.kick_started > self.c.get('pass_kick_timeout', 1.5):
                reason = 'KICK_NOT_OBSERVED'
            elif state in ('PassFlight', 'Receive') and f.now-s.flight_started > self.c.get('pass_flight_timeout', 3.5):
                reason = 'FLIGHT_TIMEOUT'
            if reason:
                self.cancel_pass(reason)
                forced = 'Hold'  # 本帧先停止，下一帧重新进入普通进攻。
        if forced is not None and self.current_state.state_name != forced:
            # 安全抢占是唯一直接改 current_state 的地方；普通战术均由 transFunction 切换。
            self.current_state, self.isStateSwitched = self.states[forced], True
        # 框架随后会处理 Global.isPlaySwitched，重置到起始 Hold；保持日志一致。
        if Global.isPlaySwitched:
            self.current_state, self.isStateSwitched = self.start_state, True
        self.executed_state = self.current_state.state_name
        self.strategy_phase = reason or self.executed_state
        self.phase = self.strategy_phase

    def attack_transition(self):
        d, f = self.choice, self.frame
        if d.pass_target is not None:
            self.session = PassSession(d.striker, d.other, d.pass_target, f.ball, f.now)
            self.pass_reason = 'BETTER_SHOT_ANGLE'
            return 'PassSetup'
        return self.normal_state()

    def note_kick(self, number, ball):
        s = self.session
        if s is not None and self.current_state.state_name == 'PassKick' and number == s.sender and not s.kick_requested:
            s.kick_requested, s.origin, s.kick_started = True, ball, self.frame.now
            self.pass_attempts += 1

    def finish_pass_if_near(self):
        s, f = self.session, self.frame
        if distance(f.ball, s.origin) > 350 and distance(f.ball, f.players[s.receiver]) < 200:
            self.tactics.striker, self.tactics.changed, self.tactics.target = s.receiver, f.now, None
            self.last_pass, self.pass_reason = f.now, 'RECEIVER_NEAR_BALL'
            self.pass_completed += 1  # 视觉近球，不是红外控球认证。
            self.session = None
            return True
        return False

    def attacking_tasks(self, carry):
        d = self.choice
        actions = {}
        if d.other is not None:
            actions[d.other] = ('move', d.support)
        if d.striker is not None:
            if carry and d.carry_target is not None:
                actions[d.striker] = ('carry', d.carry_target)
            else:
                # 状态转换要到下一帧：准备转入传球或 Shoot 时，本帧不能误踢球。
                actions[d.striker] = ('aim' if carry or d.pass_target is not None or d.phase == 'ADVANCE' else 'shoot', d.aim)
        return self.tasks(actions, goalie=True)

    def receiving_tasks(self, chase):
        s, f = self.session, self.frame
        retreat = self.tactics.legal(Point(s.origin.x-450, s.origin.y))
        target = s.target
        if chase:
            dx, dy = s.target.x-s.origin.x, s.target.y-s.origin.y
            length = max(1, math.hypot(dx, dy))
            target = self.tactics.legal(Point(f.ball.x+90*dx/length, f.ball.y+90*dy/length))
        return self.tasks({s.sender:('move', retreat), s.receiver:('receive', target)}, goalie=True)

    def tasks(self, actions, goalie=False):
        """每帧返回官方 Task；不在 Play 内手动调用技能执行函数。"""
        result, f = {}, self.frame
        for number in self.ids:
            if number not in f.visible:
                continue
            role = 'G' if number == self.c['goalie'] else 'A' if number == self.c['attackers'][0] else 'B'
            action, target = actions.get(number, ('stop', None))
            if goalie and number == self.c['goalie'] and self.c['profile'] != 'qualifier':
                skill = Skill.Goalie2022('normal')
                self.motion_ids.add(number)
            elif action == 'stop':
                skill = Skill.Stop()
            elif action in ('move', 'retreat', 'receive'):
                skill = CompetitionSkills.MoveTo(target, f.ball, slow=action == 'retreat', receive=action == 'receive',
                                                 enable_dss=self.c.get('enable_dss', True))
                self.motion_ids.add(number)
            else:
                ball_point, target_point = C.CGeoPoint(f.ball.x, f.ball.y), C.CGeoPoint(target.x, target.y)
                power = evalPower(ball_point, target_point, P.NORMAL if action == 'pass' else P.HIGH)
                if action == 'shoot' and self.c['shoot_skill'] == 'baseline':
                    skill = Skill.NormalShoot(power)
                else:
                    skill = CompetitionSkills.TargetedKick(target, f.ball, power, self.tactics,
                        carry=action == 'carry', can_kick=action != 'aim', enable_dss=self.c.get('enable_dss', True),
                        on_kick=self.note_kick if action == 'pass' else None,
                        report_phase=lambda phase: setattr(self, 'phase', phase))
                self.motion_ids.add(number)
            task = Task(skill, fixedNumber=number, description=action)
            self.retained_tasks.append(task)
            result[role] = task
        return result
