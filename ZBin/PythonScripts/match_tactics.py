"""战术决策层：只决定“谁做什么、目标在哪”，不直接驱动机器人。

阅读顺序：Planner.plan() → shot() / receiving_point() → pass_actions()。
本文件没有官方 C++ 库依赖，可以用 test_match.py 离线检查逻辑。

坐标约定：距离单位为毫米；己方球门在负 X，向正 X 进攻。
这里使用的是框架提供的己方归一化坐标，不是随队伍方向改变的屏幕坐标。
now 使用单调时钟，单位为秒；比赛有效计时和日志日期不是这个时钟。
这是本地联调策略，尚未接入正式 Normalplay.py / PenaltyKick.py 模板。
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Point:
    """不可修改的二维位置；x、y 单位均为毫米。"""
    x: float
    y: float


def distance(a, b):
    """两点的直线距离，例如 distance(机器人位置, 球位置)。"""
    return math.hypot(a.x - b.x, a.y - b.y)


def clearance(a, b, obstacles):
    """估计从 a 到 b 的通道净空，越大越不容易被挡住。

把障碍物中心投影到路径上，取最近距离，再扣除 130 mm 保守余量。
负数意味着路径靠障碍太近；这只是几何估计，不预测对手运动。
"""
    dx, dy = b.x - a.x, b.y - a.y
    length2 = dx * dx + dy * dy
    result = 3000.0
    for p in obstacles:
        # t=0 是起点，t=1 是终点；路径之外的障碍通常不计入。
        t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / max(1, length2)
        if 0.02 < t < 1.05:
            t = min(1, t)
            result = min(result, distance(p, Point(a.x + t * dx, a.y + t * dy)) - 130)
    return result


class Planner:
    """跨帧保存战术记忆：主攻车、射门目标和进行中的传球。"""

    def __init__(self, config):
        self.c = config
        # striker 是机器人车号，不是 A/B 角色名；changed 记录最近换人的时间。
        self.striker = None
        self.changed = -100.0
        self.target = None
        self.target_time = -100.0
        # 传球状态：None（未传球）→ SETUP → KICK → FLIGHT → RECEIVE → None。
        # 接球可能在 FLIGHT 阶段就满足完成条件，不一定经过每一个阶段。
        self.pass_state = None
        self.pass_sender = self.pass_receiver = None
        self.pass_target = self.pass_origin = None
        self.pass_started = 0.0
        self.last_pass = -100.0
        # completed 仅表示视觉中球已接近接球车，不证明已经牢牢控住球。
        self.pass_attempts = self.pass_completed = self.pass_aborted = 0
        self.pass_reason = ''

    def cancel_pass(self, now, reason):
        """取消正在进行的传球，并开始冷却，避免下一帧立即重试。"""
        if self.pass_state is not None:
            self.pass_aborted += 1
            self.last_pass = now
            self.pass_reason = reason
        self.pass_state = None

    def note_kick(self, now, number, ball):
        """执行层通知“已请求踢球”；这不证明已经接触球或完成传球。

        只接受当前传球车在 SETUP 阶段的首次通知，避免每帧重复增加次数。
        """
        if self.pass_state == 'SETUP' and number == self.pass_sender:
            self.pass_state = 'KICK'
            self.pass_origin = ball
            self.pass_started = now
            self.pass_attempts += 1

    def receiving_point(self, ball, opponents):
        """在球的前方侧翼找接球点；返回 Point，无合适点则返回 None。"""
        # 前移 250/650 mm、横向错开 1100/1500 mm，尝试换一个射门角度。
        # legal() 把候选点限制在场内、禁区外；再排除太近、太远和被堵的通道。
        candidates = [self.legal(Point(ball.x+dx, ball.y+dy))
                      for dx in (250, 650) for dy in (-1500, -1100, 1100, 1500)]
        candidates = [p for p in candidates if 850 < distance(ball, p) < 2400
                      and clearance(ball, p, opponents) > 200]
        if not candidates:
            return None
        # 接球后的射门评分越高越好，略微惩罚长传；评分不是进球概率。
        return max(candidates, key=lambda p: self.shot(p, opponents)[1]
                   - distance(ball, p)*0.02)

    def pass_actions(self, now, ball, players, opponents):
        """推进已有的传球状态机，而不是每帧重新开始一次传球。

        返回 (动作字典, 阶段名称)；返回 None 表示本轮传球结束/取消，
        调用者 plan() 应继续决定普通进攻动作。状态切换来自赋值，不来自 print。
        """
        sender, receiver = self.pass_sender, self.pass_receiver
        if sender not in players or receiver not in players:
            self.cancel_pass(now, 'ROBOT_LOST')
            return None
        if self.pass_state == 'SETUP':
            # 准备阶段：先等接球车就位。aim 允许瞄准但禁止踢球，pass 才允许踢。
            if now-self.pass_started > self.c.get('pass_setup_timeout', 5.0):
                self.cancel_pass(now, 'SETUP_TIMEOUT')
                return None
            if clearance(ball, self.pass_target, opponents) < 180:
                self.cancel_pass(now, 'LANE_BLOCKED')
                return None
            ready = distance(players[receiver], self.pass_target) < 180
            return {sender: ('pass' if ready else 'aim', self.pass_target),
                    receiver: ('receive', self.pass_target)}, 'PASS_SETUP'
        if self.pass_state == 'KICK':
            # 请求踢球后仍保持接触；只有球沿传球方向前进且离开发球车，
            # 才认为真正出球。否则一发指令就撤退，可能根本没有碰到球。
            dx, dy = self.pass_target.x-self.pass_origin.x, self.pass_target.y-self.pass_origin.y
            length = max(1, math.hypot(dx, dy))
            forward = ((ball.x-self.pass_origin.x)*dx+(ball.y-self.pass_origin.y)*dy)/length
            if forward > 200 and distance(ball, players[sender]) > 210:
                self.pass_state, self.pass_started = 'FLIGHT', now
            elif now-self.pass_started > self.c.get('pass_kick_timeout', 1.5):
                self.cancel_pass(now, 'KICK_NOT_OBSERVED')
                return None
            else:
                # 尚未看到实际出球：继续踢球动作，接球车留在接应位置。
                return {sender: ('pass', self.pass_target), receiver: ('receive', self.pass_target)}, 'PASS_KICK'
        if now-self.pass_started > self.c.get('pass_flight_timeout', 3.5):
            self.cancel_pass(now, 'FLIGHT_TIMEOUT')
            return None
        traveled = distance(ball, self.pass_origin)
        # 球离起点足够远并接近接球车后，把主攻权交给接球车。
        # 清空旧射门目标，下方普通进攻会从新球位重新选射门方向。
        if traveled > 350 and distance(ball, players[receiver]) < 200:
            self.pass_completed += 1
            self.pass_state = None
            self.pass_reason = 'RECEIVER_NEAR_BALL'  # proximity, not an infrared possession assertion
            self.striker, self.changed, self.last_pass = receiver, now, now
            self.target = None
            return None
        # 确认出球后，发球车后撤让路，只让接球车追球，避免两车一起抢。
        retreat = self.legal(Point(self.pass_origin.x-450, self.pass_origin.y))
        if distance(ball, self.pass_target) < 550 and traveled > 350:
            # 球快到预定落点才小幅追踪球，减少接球车过早追球造成的偏离。
            self.pass_state = 'RECEIVE'
            dx, dy = self.pass_target.x-self.pass_origin.x, self.pass_target.y-self.pass_origin.y
            length = max(1, math.hypot(dx, dy))
            receive = self.legal(Point(ball.x+90*dx/length, ball.y+90*dy/length))
        else:
            receive = self.pass_target
        return {sender: ('move', retreat), receiver: ('receive', receive)}, 'PASS_'+self.pass_state

    def legal(self, p):
        """把进攻车目标点推回场内/禁区外；并非完整的比赛规则检查器。

        180/220 mm 是保守位置余量。限制的是目标点，不保证整条行走轨迹合法。
        门将使用独立的官方技能，不由这个函数决定禁区内站位。
        """
        c = self.c
        x = max(-c['field_length']/2 + 180, min(p.x, c['field_length']/2 - 180))
        y = max(-c['field_width']/2 + 180, min(p.y, c['field_width']/2 - 180))
        edge = c['field_length']/2 - c['defense_depth'] - 220
        if abs(x) > edge and abs(y) < c['defense_width']/2 + 220:
            x = math.copysign(edge, x)
        return Point(x, y)

    def in_defense(self, p):
        """球是否落在任一侧的保守矩形禁区范围内。"""
        return abs(p.x) > self.c['field_length']/2 - self.c['defense_depth'] - 120 and abs(p.y) < self.c['defense_width']/2 + 120

    def shot(self, ball, obstacles):
        """在对方门线上选 9 个候选点，返回 (最佳目标点, 几何评分)。"""
        # 门柱附近留 95 mm 余量；i=-4..4 对应球门从一端到另一端。
        half_goal = self.c['goal_width']/2 - 95
        points = [Point(self.c['field_length']/2, half_goal * i / 4) for i in range(-4, 5)]
        def score(p):
            # 优先选择净空大的射门通道；净空最高计 600，长距离斜射略扣分。
            return min(600, clearance(ball, p, obstacles)) - distance(ball, p)*0.015
        best = max(points, key=score)
        return best, score(best)

    def plan(self, now, ball, players, opponents):
        """每帧战术主入口，建议从这里开始阅读。

        输入：ball 是球位置；players 是 {己方车号: Point}；opponents 是对手位置列表。
        输出：(actions, phase)，例如 {0: ('shoot', Point(...)), 1: ('move', Point(...))}。
        phase 是观察用的阶段名称；真正执行动作的是 run_match.py。

        动作含义：move=走位；receive=接应并吸球；aim=瞄准但不踢；
        pass=允许传球；carry=带球推进不踢；shoot=允许射门。
        裁判是否允许执行，由执行层先检查，本函数不自行忽略停赛命令。
        """
        # 第一步：过滤异常坐标。球出界就取消传球、返回空动作，等待重摆。
        players = {n: p for n, p in players.items()
                   if math.isfinite(p.x) and math.isfinite(p.y)}
        opponents = [p for p in opponents if math.isfinite(p.x) and math.isfinite(p.y)]
        if not all(math.isfinite(v) for v in (ball.x, ball.y)):
            self.cancel_pass(now, 'INVALID_BALL')
            return {}, 'NO_BALL'
        if abs(ball.x) > self.c['field_length']/2 or abs(ball.y) > self.c['field_width']/2:
            self.cancel_pass(now, 'BALL_OUT')
            return {}, 'BALL_OUT'
        if self.in_defense(ball):
            self.cancel_pass(now, 'BALL_IN_DEFENSE')
        elif self.pass_state is not None:
            # 第二步：先完成已有传球，不要被普通选人逻辑每帧打断。
            result = self.pass_actions(now, ball, players, opponents)
            if result is not None:
                return result
        ids = [n for n in self.c['attackers'] if n in players]
        if not ids:
            return {}, 'NO_ATTACKER'
        # 第三步：选主攻。离球近优先；站在球前面需要绕后，额外增加成本。
        cost = {n: distance(players[n], ball) + max(0, players[n].x-ball.x)*0.35 for n in ids}
        best = min(ids, key=cost.get)
        # 滞回：至少间隔 1.2 秒，且另一辆车成本低 450 以上才主动换人。
        # 当前主攻不可见时立即换人，不受这个等待限制。
        if self.striker not in ids or (now-self.changed > 1.2 and cost[best]+450 < cost[self.striker]):
            self.striker, self.changed = best, now
        striker = self.striker
        other = next((n for n in ids if n != striker), None)
        obstacles = list(opponents)
        if other is not None:
            obstacles.append(players[other])
        aim, _ = self.shot(ball, obstacles)
        # 射门目标也保留 0.65 秒，避免视觉小幅变化导致朝向反复左右摆动。
        if self.target is None or now-self.target_time > 0.65:
            self.target, self.target_time = aim, now
        actions = {}
        if other is not None:
            # 第四步：安排队友接应。未开传球时站球后侧翼，给主攻留出通道。
            offer = self.receiving_point(ball, opponents) if self.c.get('enable_pass', False) else None
            support = offer or self.legal(Point(ball.x-650, -1100 if ball.y >= 0 else 1100))
            if distance(support, ball) < 800:
                support = self.legal(Point(ball.x-900, support.y))
            actions[other] = ('move', support)
        if abs(ball.x) > self.c['field_length']/2 or abs(ball.y) > self.c['field_width']/2:
            return {}, 'BALL_OUT'
        if self.in_defense(ball):
            # 球在禁区内时，不让进攻车直接冲进去；只去合法边缘等待。
            actions[striker] = ('move', self.legal(ball))
            return actions, 'WAIT_OUTSIDE_DEFENSE'
        if self.c.get('enable_pass', False) and other is not None and ball.x > 1500 and now-self.last_pass > 6:
            # 第五步：只有启用传球、存在队友、进入前场且冷却结束才考虑传球。
            # 还必须：主攻靠近球、当前射门受阻、接球点的射门评分明显更好。
            # .get(..., 默认值) 仅在 JSON 没写该参数时使用；以实际配置为准。
            offer = self.receiving_point(ball, opponents)
            own_score = self.shot(ball, opponents)[1]
            if (offer is not None and distance(players[striker], ball) < 280
                    and own_score < self.c.get('pass_shot_threshold', 230)
                    and self.shot(offer, opponents)[1] > own_score+self.c.get('pass_score_gain', 60)):
                self.pass_state, self.pass_sender, self.pass_receiver = 'SETUP', striker, other
                self.pass_target, self.pass_started = offer, now
                self.pass_reason = 'BETTER_SHOT_ANGLE'
                return self.pass_actions(now, ball, players, opponents)
        if self.c.get('advance_before_shoot', False) and ball.x < self.c.get('shoot_from_x', 2650):
            # 第六步：没有传球机会时，先带到设定的 X，再射门；Y 向中路收拢。
            actions[striker] = ('carry', self.legal(Point(self.c.get('shoot_from_x', 2650), ball.y * 0.45)))
            return actions, 'ADVANCE'
        # 第七步：到达射门区域后，主攻瞄准选好的门内空隙射门。
        actions[striker] = ('shoot', self.target)
        return actions, 'ATTACK'


def referee_mode(message, game_on, can_kick):
    """把官方裁判信息转换成执行层模式；不支持的情形保守停止。

    HALT=不动；STOP=禁止踢球、必要时退球；PLAY=允许常规进攻。
    PREPARE=准备，不能直接开踢；UNKNOWN_REF=未知命令，不能盲跑。
    当前点球/摆球采用保守停止，尚未实现正式 PenaltyKick 策略。
    """
    if message in ('GameHalt', 'Ref_Halt', 'GameOver', 'OurTimeout', 'TheirTimeout',
                   'OurBallPlacement', 'TheirBallPlacement', 'OurPenaltyKick', 'TheirPenaltyKick'):
        return 'HALT'
    if message == 'GameStop':
        return 'STOP'
    if message in ('TheirKickOff', 'TheirDirectKick', 'TheirIndirectKick'):
        return 'DEFEND_RESTART'
    if message == 'OurIndirectKick':
        # 间接任意球需要专门的触球配合；实验性普通传球不等于已实现该规则。
        return 'PREPARE'
    if message in ('OurKickOff', 'OurDirectKick'):
        return 'OUR_RESTART' if can_kick else 'PREPARE'
    if message not in ('', 'NormalPlay'):
        return 'UNKNOWN_REF'
    return 'PLAY' if game_on else 'WAIT_START'
