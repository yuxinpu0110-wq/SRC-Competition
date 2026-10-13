"""比赛候选策略：官方 State → Task → Skill 结构。

阅读顺序：Shoot → PassSetup → PassKick → PassFlight → Receive。不再存在带球Advance。
给刚接触框架的同学：
  Play = 总教练，保存本场观察和战术；State = 当前阶段，例如“准备传球”。
  Task = 给某辆车的任务单；Skill = 执行任务的动作，例如 RushTo（跑到某点）。
  self 是当前对象；State 里的 self.play 指向总教练 NormalPlay 对象。
  return 'PassKick' 是向框架申请切换状态，不是调用 PassKick 函数。
  return '' 表示保持当前状态；None 表示“没有这个目标/没有这个队友”。
  所有位置/距离用毫米，角度用弧度，己方坐标中朝 +X 方向进攻。
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
from shot_accuracy import ShotGate
from Vision import Player
from ball_control import BallControl, wrap
from competition_rules import ContactGuard, RestartGuard, retreat_target


@dataclass
class Frame:
    """一帧观察的快照。@dataclass 自动生成初始化函数，方便集中传递数据。"""
    # now 是单调时钟秒数，只用来计算时间差，不是比赛剩余时间。
    now: float
    ball: Point
    # players 如 {0: Point(...), 1: Point(...)}，键是真实车号；包含可见受控车。
    players: dict
    # opponents 只存对手坐标；visible 是当前观察到且坐标有效的受控车号集合。
    opponents: list
    visible: set
    mode: str
    ball_valid: bool
    referee: str = ''


@dataclass
class PassSession:
    """传球参数/计时数据；没有独立 state 字段，状态只属于官方 StateMachine。"""
    sender: int       # 这次传球的发球车号，传球中途不随意换人。
    receiver: int     # 这次传球的接球车号。
    target: Point     # 约定的接应点，不是接球车每帧的当前位置。
    origin: Point     # 发球位置，判断球是否真的向接应点移动时使用。
    started: float    # 开始准备传球的时刻，用于超时保护。
    kick_started: float = 0.0
    flight_started: float = 0.0
    kick_requested: bool = False
    fake_aim: Point | None = None
    feint_started: float = 0.0
    receive_since: float | None = None


class MatchState(State):
    """所有状态共享 Play 的观察和辅助函数；车号通过 Task.fixedNumber 绑定。"""
    def getMatchString(self):
        # 当前所有 Task 都指定 fixedNumber，所以不需要 Munkres 自动分配车号。
        # 空字符串本身不是“禁用匹配”；真正跳过匹配的原因是任务都固定了车号。
        return ''

    def transFunction(self):
        return ''


class Hold(MatchState):
    """停车状态。正常开赛后申请转入进攻；HALT 时一直停留在这里。"""
    def getTasks(self):
        return self.play.tasks({})  # 显式给有效受控车分配 Skill.Stop。

    def transFunction(self):
        return self.play.normal_state() if self.play.can_attack else ''


class Prepare(MatchState):
    """STOP/定位球准备：禁止踢球，只让距球过近的车退开。"""
    def getTasks(self):
        p, f = self.play, self.play.frame
        # p/f 是为了缩短表达式的别名，分别代表整个策略和当前帧观察。
        # actions 是临时任务单：{真实车号: ('动作名称', 目标Point)}。
        actions = {}
        for index, number in enumerate(p.ids):
            pos = f.players.get(number)
            if pos is not None and distance(pos, f.ball) < 650:
                # 从球指向车的方向继续向外退；恰好重合时给不同车辆不同方向。
                angle = math.atan2(pos.y-f.ball.y, pos.x-f.ball.x) if distance(pos, f.ball) > 1 else math.pi+index
                target = p.tactics.legal(Point(f.ball.x+750*math.cos(angle), f.ball.y+750*math.sin(angle)))
                if distance(target, f.ball) >= 650:
                    # legal() 可能把目标挪回场内，必须再次检查是否仍离球足够远。
                    actions[number] = ('retreat', target)
        return p.tasks(actions)

    def transFunction(self):
        return self.play.normal_state() if self.play.can_attack else ''


class Shoot(MatchState):
    """瞄准选出的门内空隙射门；准备传球时本帧只瞄准、不抢先射门。"""
    def getTasks(self):
        return self.play.attacking_tasks()

    def transFunction(self):
        return self.play.attack_transition()


class WaitOutsideDefense(MatchState):
    """球进禁区后，不让进攻车硬闯；legal() 会把目标修正到禁区外。"""
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
        return p.tasks({session.sender: ('aim', session.fake_aim or session.target),
                        session.receiver: ('receive', p.receiver_position())}, goalie=True)

    def transFunction(self):
        p, session = self.play, self.play.session
        if p.receiver_ready():
            # 队友已到接应点附近。下一帧才能执行 PassKick，而不是此处立刻踢。
            if session.fake_aim is not None:
                # 只有持球且朝门站好，才开始计时；不会为了假动作原地无限旋转。
                desired = math.atan2(session.fake_aim.y-p.frame.ball.y, session.fake_aim.x-p.frame.ball.x)
                if not p.ball_controls[session.sender].held or abs(wrap(Player.direction(session.sender)-desired)) > .12:
                    return ''
                session.feint_started = p.frame.now
                return 'FakeShot'
            # 不再仅凭队友就位就启动踢球超时；发球车也要先完成转向/贴球。
            if p.sender_ready():
                session.kick_started = p.frame.now
                return 'PassKick'
        return ''


class FakeShot(MatchState):
    """佯装射门，不发踢球请求；队友保持接应，短暂停顿后真正传球。

    并不保证门将会被骗。0.35秒后退出，不反复晃动，也不掉头带球。
    HALT、丢球、队友离位、传球线路被挡都由安全钩子抢先处理。
    """
    def getTasks(self):
        p, s = self.play, self.play.session
        return p.tasks({s.sender: ('stop', None),
                        s.receiver: ('receive', p.receiver_position())}, goalie=True)

    def transFunction(self):
        p, s = self.play, self.play.session
        if p.frame.now-s.feint_started >= .35:
            # 回到准备阶段真正瞄准侧传，再进入短时出球阶段；不重复佯射。
            s.fake_aim, s.started = None, p.frame.now
            return 'PassSetup'
        return ''


class PassKick(MatchState):
    """请求出球后保持接触，看到球确实离开发球车才转入飞行阶段。"""
    def getTasks(self):
        p, session = self.play, self.play.session
        return p.tasks({session.sender: ('pass', session.target),
                        session.receiver: ('receive', p.receiver_position())}, goalie=True)

    def transFunction(self):
        p, session = self.play, self.play.session
        dx, dy = session.target.x-session.origin.x, session.target.y-session.origin.y
        # hypot(dx, dy) 是传球线路长度；max(1, ...) 防止两点重合时除以零。
        length = max(1, math.hypot(dx, dy))
        # 点积/线路长度 = 球沿传球方向前进的距离。只横着滚动不算出球成功。
        forward = ((p.frame.ball.x-session.origin.x)*dx+(p.frame.ball.y-session.origin.y)*dy)/length
        # 同时要求：请求过踢球、沿线路前进超过200mm、离发球车超过210mm。
        # “下发踢球指令”不保证真的碰到球，因此不能只看 kick_requested。
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
    """球已接近接应点：接球车可以小范围追球，靠近后交接主攻权。"""
    def getTasks(self):
        return self.play.receiving_tasks(chase=True)

    def transFunction(self):
        return self.play.normal_state() if self.play.finish_pass_if_near() else ''


class Goalkeeping(MatchState):
    """单独运行门将的模式。full 模式则由各进攻状态同时附带门将任务。"""
    def getTasks(self):
        return self.play.tasks({}, goalie=True)

    def transFunction(self):
        return self.play.normal_state()


@declare_state_machine(Hold, Prepare, Shoot, WaitOutsideDefense,
                       PassSetup, FakeShot, PassKick, PassFlight, Receive, Goalkeeping)
class NormalPlay(StateMachine):
    """唯一的战术状态机；runner 每帧只更新观察并调用 planTasks()。"""
    def __init__(self, config):
        super().__init__()
        self.c = config
        # config 来自 match.json。这里只保存配置，不在构造函数里驱动车辆。
        self.tactics = Tactics(config)
        self.ids = (config['attackers'] if config['profile'] == 'qualifier' else
                    [config['goalie']] if config['profile'] == 'goalie' else config['attackers']+[config['goalie']])
        # qualifier 只控制两名进攻车；goalie 只控制门将；full 同时控制三车。
        self.frame = self.choice = self.session = None
        # frame=观察；choice=几何算法给的战术建议；session=进行中的一次传球。
        self.last_pass = -100.0
        self.pass_attempts = self.pass_completed = self.pass_aborted = 0
        self.pass_reason = ''
        self.retained_tasks = []
        self.motion_ids = set()
        # 稳定计时必须跨帧保存；每辆车独立，避免换主攻后复用另一辆车的计时。
        self.shot_gates = {number: ShotGate() for number in self.ids}
        self.ball_controls = {number: BallControl() for number in self.ids}
        self.kick_events = []  # 请求日志，不冒充实际出球/进球；保留最近32次。
        self.ball_sample, self.ball_speed = None, math.inf
        self.contact_guard = ContactGuard(config.get('contact_budget_mm',300))
        self.restart_guard = RestartGuard()
        self.infrared = {}
        self.phase = self.strategy_phase = self.executed_state = 'Hold'

    @property
    def pass_state(self):
        # 遥测兼容旧字段，但数据来自真正的官方 current_state，不再维护另一套状态。
        if self.session is None:
            return None
        return {'PassSetup':'SETUP', 'FakeShot':'FEINT', 'PassKick':'KICK', 'PassFlight':'FLIGHT', 'Receive':'RECEIVE'}.get(self.current_state.state_name)

    @property
    def can_attack(self):
        # @property 让它像属性一样读：if self.can_attack，不用加括号。
        # 开赛许可和球的位置都要有效；“能进攻”不代表每一帧都要踢球。
        return self.frame.mode in ('PLAY', 'OUR_RESTART', 'OUR_INDIRECT') and self.frame.ball_valid and finite(self.frame.ball)

    def update_frame(self, now, ball, players, opponents, visible, mode, ball_valid, referee=''):
        # 丢弃 NaN/无穷大坐标，避免把无效位置传入 C++ 运动规划。
        old = self.ball_sample
        if not ball_valid or not finite(ball):
            self.ball_speed, self.ball_sample = math.inf, None
        elif old is None or not 0 <= now-old[0] <= .25:
            self.ball_speed, self.ball_sample = math.inf, (now,ball)
        elif now-old[0] >= .001:
            self.ball_speed = distance(ball,old[1])/(now-old[0])
            self.ball_sample = (now,ball)
        clean_players = {n:p for n,p in players.items() if finite(p)}
        self.frame = Frame(now, ball, clean_players, [p for p in opponents if finite(p)],
                           set(visible) & set(clean_players), mode, ball_valid, referee)

    def normal_state(self):
        """普通情况下应该进入哪个状态？只返回名字，不在这里执行任务。"""
        if not self.can_attack or self.choice.phase in ('NO_BALL', 'BALL_OUT'):
            return 'Hold'
        if self.c['profile'] == 'goalie':
            return 'Goalkeeping'
        if self.choice.phase == 'NO_ATTACKER':
            return 'Goalkeeping' if self.c['profile'] == 'full' else 'Hold'
        if self.choice.phase == 'WAIT_OUTSIDE_DEFENSE':
            return 'WaitOutsideDefense'
        return 'Shoot'

    def cancel_pass(self, reason):
        # 中断后记录冷却时刻，避免下一帧又马上发起同一条失败的传球。
        if self.session is not None:
            self.pass_aborted += 1
            self.last_pass, self.pass_reason = self.frame.now, reason
        self.session = None

    def hookBeforePlanTasks(self):
        """官方框架每帧在 getTasks() 前调用：观察→建议→安全检查。

        正常转换交给 transFunction；但 HALT/超时不能等旧踢球任务执行完，
        所以在这里抢先切换到安全状态。这不是让你每帧手动切所有状态。
        """
        if self.frame is None:
            raise RuntimeError('Call update_frame before planTasks')
        # 官方装饰器在 __init__ 返回后才创建 State 实例，在这里绑定共享 Play。
        for state in self.states.values():
            state.play = self
        f = self.frame
        # 近球累计路径保护与重启二次触球保护在生成任务之前运行。
        self.infrared = {n:getattr(Player,'infraredCount',lambda _:0)(n) for n in f.visible}
        if f.mode in ('STOP','PREPARE','DEFEND_RESTART','WAIT_START'):
            self.restart_guard.reset()
            self.contact_guard.episodes.clear()  # 裁判新死球；重新摆球不计活球连续控制。
        elif f.mode in ('OUR_RESTART','OUR_INDIRECT'):
            self.restart_guard.begin(f.referee or ('OurIndirectKick' if f.mode == 'OUR_INDIRECT' else 'OurKickOff'))
        if self.can_attack:
            for n in self.c['attackers']:
                if n in f.visible:
                    self.contact_guard.observe(n,f.now,f.players[n],f.ball)
            near_ids = {n for n in f.visible if distance(f.players[n],f.ball) < 125}
            self.restart_guard.observe(f.ball,self.infrared,near_ids)
        if self.c.get('shoot_control', 'precision') == 'precision':
            for number, gate in self.shot_gates.items():
                if number in f.visible:
                    gate.observe(f.now, f.players[number], Player.direction(number), f.ball)
                else:
                    # 失去视觉后下次看到车时必须重新积累稳定样本。
                    gate.previous = None
                    gate.stable_since = None
                if not self.can_attack:
                    gate.stable_since = None
        self.motion_ids = set()
        # 传球途中锁住主攻权；球只是靠近接球车还不够，必须由完成条件交接。
        self.choice = self.tactics.choose(f.now, f.ball, f.players, f.opponents, self.last_pass,
                                          freeze_striker=self.session is not None)
        if self.restart_guard.requires_pass and self.session is None:
            # 间接任意球只能先传给队友；无合法接应点则保守等待。
            self.choice.pass_target = (self.tactics.receiving_point(f.ball,f.opponents)
                                       if self.choice.other is not None else None)
        forced, reason = None, ''
        if not f.ball_valid or not finite(f.ball):
            forced, reason = 'Hold', 'NO_BALL'
        elif f.mode not in ('PLAY', 'OUR_RESTART', 'OUR_INDIRECT'):
            forced = 'Prepare' if f.mode in ('STOP', 'PREPARE', 'DEFEND_RESTART') else 'Hold'
            reason = f.mode
        elif self.choice.phase in ('BALL_OUT', 'WAIT_OUTSIDE_DEFENSE'):
            forced, reason = self.normal_state(), self.choice.phase
        elif Global.isPlaySwitched:
            forced, reason = 'Hold', 'PLAY_SWITCHED'
        if forced is not None:
            self.cancel_pass(reason)
            for control in self.ball_controls.values():
                control.reset()
        elif self.session is not None:
            # 只要传球还没结束，每帧检查人员、通道和计时。任一失败就取消。
            s = self.session
            state = self.current_state.state_name
            if s.sender not in f.players or s.receiver not in f.players:
                reason = 'ROBOT_LOST'
            elif self.contact_guard.blocked(s.sender) or self.contact_guard.blocked(s.receiver):
                reason = 'CONTACT_BUDGET'
            elif state in ('PassSetup', 'FakeShot', 'PassKick') and clearance(f.ball, s.target, f.opponents) < 180:
                reason = 'LANE_BLOCKED'
            elif state == 'PassSetup' and f.now-s.started > self.c.get('pass_setup_timeout', 5.0):
                reason = 'SETUP_TIMEOUT'
            elif state == 'FakeShot' and (not self.ball_controls[s.sender].held or not self.receiver_ready()):
                reason = 'FEINT_CONTROL_LOST'
            elif state == 'PassKick' and not s.kick_requested and not self.receiver_ready():
                reason = 'RECEIVER_NOT_READY'
            elif state == 'PassKick' and f.now-s.kick_started > self.c.get('pass_kick_timeout', 2.5):
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
        # 状态对象负责转换；Tactics 只判断“有没有值得尝试的传球目标”。
        d, f = self.choice, self.frame
        if d.pass_target is not None:
            self.session = PassSession(d.striker, d.other, d.pass_target, f.ball, f.now)
            if self.c.get('enable_feint', False) and f.opponents:
                # 只在对手封堵前方时佯攻；空门无需多等一次。
                if clearance(f.ball, d.aim, f.opponents) < 250:
                    # 瞄接应侧的反侧门角，尝试把门将注意力引离接应车。
                    # 这只是佯攻意图；是否真能牵动门将，必须由仿真观测验证。
                    half_goal = self.c['goal_width']/2-self.c.get('goal_margin',160)
                    self.session.fake_aim = Point(self.c['field_length']/2,
                        -math.copysign(half_goal, d.pass_target.y))
            self.pass_reason = 'BETTER_SHOT_ANGLE'
            return 'PassSetup'
        return self.normal_state()

    def note_kick(self, number, ball):
        # 技能执行时调用的通知函数。重复发送请求只计一次尝试。
        # 不在这里切换到 PassFlight：要等视觉证明确实出球。
        s = self.session
        if s is not None and self.current_state.state_name == 'PassKick' and number == s.sender and not s.kick_requested:
            s.kick_requested, s.origin, s.kick_started = True, ball, self.frame.now
            self.pass_attempts += 1

    def record_kick(self, number, ball, target, power, passing):
        """精确记录请求当帧方向，后续用球轨迹验证，而不是看截图猜补偿角。"""
        self.restart_guard.requested(number,ball,self.infrared)
        self.kick_events.append(dict(now=self.frame.now, frame_ball=[ball.x, ball.y],
            target=[target.x, target.y], robot=number, heading=Player.direction(number),
            power=power, passing=passing))
        self.kick_events[:] = self.kick_events[-32:]
        if passing:
            self.note_kick(number, ball)

    def receiver_position(self, ball=None):
        """target 是球的落点；车心应在落点后105mm，车头朝来球方向。"""
        s = self.session
        angle = math.atan2(s.target.y-s.origin.y, s.target.x-s.origin.x)
        point = s.target if ball is None else ball
        return self.tactics.legal(Point(point.x+105*math.cos(angle), point.y+105*math.sin(angle)))

    def receiver_ready(self):
        s = self.session
        angle = math.atan2(s.origin.y-s.target.y, s.origin.x-s.target.x)
        return (distance(self.frame.players[s.receiver], self.receiver_position()) < 140
                and abs(wrap(Player.direction(s.receiver)-angle)) < .2)

    def sender_ready(self):
        s, f = self.session, self.frame
        robot = f.players[s.sender]
        desired = math.atan2(s.target.y-f.ball.y,s.target.x-f.ball.x)
        dx,dy = f.ball.x-robot.x,f.ball.y-robot.y
        along = dx*math.cos(desired)+dy*math.sin(desired)
        side = abs(-dx*math.sin(desired)+dy*math.cos(desired))
        return (50 < along < 150 and side < 20
                and abs(wrap(Player.direction(s.sender)-desired)) < .05)

    def finish_pass_if_near(self):
        # “球离发球点够远 + 接球车离球够近”是当前的近似接球判据。
        # 它不是硬件控球传感器确认；成功后接球车成为新主攻。
        s, f = self.session, self.frame
        robot = f.players[s.receiver]
        heading = Player.direction(s.receiver)
        dx, dy = f.ball.x-robot.x, f.ball.y-robot.y
        forward = dx*math.cos(heading)+dy*math.sin(heading)
        lateral = abs(-dx*math.sin(heading)+dy*math.cos(heading))
        # 不能只凭“200mm内擦肩而过”就交接；球要在接球车的正前方。
        if (distance(f.ball, s.origin) > 350 and 50 < forward < 170 and lateral < 50
                and self.ball_speed < 1200):
            if s.receive_since is None:
                s.receive_since = f.now
            # 连续80ms近球且球已减速，排除高速擦过车头就误计完成。
            if f.now-s.receive_since < .08:
                return False
            self.tactics.striker, self.tactics.changed, self.tactics.target = s.receiver, f.now, None
            self.last_pass, self.pass_reason = f.now, 'RECEIVER_NEAR_BALL'
            self.pass_completed += 1  # 视觉近球，不是红外控球认证。
            self.session = None
            return True
        s.receive_since = None
        return False

    def attacking_tasks(self):
        # d 是 AttackChoice：striker=主攻车号，other=另一辆进攻车号。
        # 这两个身份可以互换；A/B 角色标签则绑定配置中的车号，不代表永久主攻。
        d = self.choice
        actions = {}
        if d.other is not None:
            actions[d.other] = ('move', d.support)
        if d.striker is not None:
            # 无带球推进；原地球远时也只射/传。间接球未有他车触球不能直接射。
            actions[d.striker] = ('aim' if d.pass_target is not None or self.restart_guard.requires_pass else 'shoot', d.aim)
        return self.tasks(actions, goalie=True)

    def receiving_tasks(self, chase):
        # chase=False：守在约定接应点；True：球靠近后跟随球的小范围位置。
        s, f = self.session, self.frame
        retreat = self.tactics.legal(Point(s.origin.x-450, s.origin.y))
        target = self.receiver_position()
        if chase and self.ball_speed < 1200:
            # 高速来球时守住接应点，不能提前冲向来球；慢球才小范围追接。
            target = self.receiver_position(f.ball)
        return self.tasks({s.sender:('move', retreat), s.receiver:('receive', target)}, goalie=True)

    def tasks(self, actions, goalie=False):
        """把简易任务单转成官方 Task，集中处理车号、门将、技能和停车。

        actions 示例：{0: ('shoot', Point(4500, 0)), 1: ('move', Point(0, 1100))}。
        move/receive=无球走位与接应，retreat=退离球，aim=对准不踢，shoot/pass=射/传。
        carry被明确拒绝；所有进攻移动关闭吸球。
        未列出的有效受控车默认 Stop；goalie=True 时门将改用官方守门技能。
        本函数只创建任务，不执行；执行由官方 State.run → Task.run 完成。
        """
        result, f = {}, self.frame
        for number in self.ids:
            if number not in f.visible:
                continue
            role = 'G' if number == self.c['goalie'] else 'A' if number == self.c['attackers'][0] else 'B'
            # A/B/G 是框架任务字典的角色名，不是模拟器显示的真实数字车号。
            action, target = actions.get(number, ('stop', None))
            if action == 'carry':
                raise ValueError('Carrying is forbidden in this competition candidate')
            if self.can_attack and number in self.c['attackers'] and self.tactics.in_defense(f.players[number]):
                target,action = self.tactics.legal(f.players[number]),'move'
                self.phase = 'RULE_DEFENSE_RELEASE'
            if self.can_attack and number in self.c['attackers'] and (
                    self.contact_guard.blocked(number) or self.restart_guard.blocked(number)):
                target = retreat_target(f.players[number],f.ball,self.tactics)
                action = 'move' if target is not None else 'stop'
                self.phase = 'RULE_RELEASE'
            if goalie and number == self.c['goalie'] and self.c['profile'] != 'qualifier':
                # 赛事方指定更强的官方接口；签名不同，不传入'normal'字符串。
                skill = Skill.Goalie()
                self.motion_ids.add(number)
            elif action == 'stop':
                skill = Skill.Stop()
            elif action in ('move', 'retreat', 'receive'):
                # 接球固定面朝传球起点，球越过车心时也不会突然转180度。
                facing = (math.atan2(self.session.origin.y-self.session.target.y,
                                    self.session.origin.x-self.session.target.x)
                          if action == 'receive' and self.session is not None else None)
                skill = CompetitionSkills.MoveTo(target, f.ball, slow=action == 'retreat', receive=action == 'receive',
                                                 enable_dss=self.c.get('enable_dss', True), facing=facing)
                self.motion_ids.add(number)
            else:
                ball_point, target_point = C.CGeoPoint(f.ball.x, f.ball.y), C.CGeoPoint(target.x, target.y)
                power = evalPower(ball_point, target_point, P.NORMAL if action == 'pass' else P.HIGH)
                # 使用统一力度映射，不能把这个参数直接当作物理球速。
                skill = CompetitionSkills.TargetedKick(target, f.ball, power, self.tactics,
                        can_kick=action != 'aim', enable_dss=self.c.get('enable_dss', True),
                        on_kick=lambda n, b, t=target, power=power, passing=action == 'pass': self.record_kick(n, b, t, power, passing),
                        report_phase=lambda phase: setattr(self, 'phase', phase),
                        gate=self.shot_gates[number],
                        now=f.now, shooting=action != 'pass', control=self.ball_controls[number])
                self.motion_ids.add(number)
            task = Task(skill, fixedNumber=number, description=action)
            # fixedNumber：明确指定谁做这个任务，官方角色分配会跳过它。
            # 因而当前并不运行 Munkres 求分配；主攻选择在 Tactics.choose 中完成。
            # retained_tasks 保留闭包及其原生几何对象到下一帧，不能随手删除。
            self.retained_tasks.append(task)
            result[role] = task
        return result
