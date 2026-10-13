"""纯 Python 战术计算，不再保存或切换传球状态。

真正的状态机在 Play/Normal/Normalplay.py，使用官方 State/Task/Skill。
这里只保留选人、站位、射门评分等可离线检查的辅助算法。
距离为毫米；己方归一化坐标中朝正 X 进攻；now 为单调时钟秒数。
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Point:
    x: float
    y: float


def distance(a, b):
    return math.hypot(a.x-b.x, a.y-b.y)


def finite(p):
    return math.isfinite(p.x) and math.isfinite(p.y)


def clearance(a, b, obstacles):
    """障碍物到路径的最小距离，扣除 130 mm 余量；不是进球概率。"""
    dx, dy = b.x-a.x, b.y-a.y
    length2 = dx*dx+dy*dy
    result = 3000.0
    for p in obstacles:
        t = ((p.x-a.x)*dx+(p.y-a.y)*dy)/max(1, length2)
        if 0.02 < t < 1.05:
            t = min(1, t)
            result = min(result, distance(p, Point(a.x+t*dx, a.y+t*dy))-130)
    return result


@dataclass
class AttackChoice:
    """一次计算的结果；不是另一套状态机，也不直接驱动车辆。"""
    phase: str
    striker: int | None = None
    other: int | None = None
    aim: Point | None = None
    support: Point | None = None
    carry_target: Point | None = None
    pass_target: Point | None = None


class Tactics:
    """只记住主攻和射门目标的滞回信息，传球过程由官方 State 管理。"""
    def __init__(self, config):
        self.c = config
        self.striker = None
        self.changed = -100.0
        self.target = None
        self.target_time = -100.0

    def legal(self, p):
        """将进攻车目标限制在场内/禁区外；不保证整段轨迹或所有规则合法。"""
        c = self.c
        x = max(-c['field_length']/2+180, min(p.x, c['field_length']/2-180))
        y = max(-c['field_width']/2+180, min(p.y, c['field_width']/2-180))
        edge = c['field_length']/2-c['defense_depth']-220
        if abs(x) > edge and abs(y) < c['defense_width']/2+220:
            x = math.copysign(edge, x)
        return Point(x, y)

    def in_defense(self, p):
        return (abs(p.x) > self.c['field_length']/2-self.c['defense_depth']-120
                and abs(p.y) < self.c['defense_width']/2+120)

    def ball_status(self, ball):
        if not finite(ball):
            return 'NO_BALL'
        if abs(ball.x) > self.c['field_length']/2 or abs(ball.y) > self.c['field_width']/2:
            return 'BALL_OUT'
        return 'WAIT_OUTSIDE_DEFENSE' if self.in_defense(ball) else ''

    def shot(self, ball, obstacles):
        """在门线上选 9 点，优先净空，略惩罚远距离；返回 (点, 分数)。"""
        half_goal = self.c['goal_width']/2-95
        points = [Point(self.c['field_length']/2, half_goal*i/4) for i in range(-4, 5)]
        def score(p):
            return min(600, clearance(ball, p, obstacles))-distance(ball, p)*0.015
        best = max(points, key=score)
        return best, score(best)

    def receiving_point(self, ball, opponents):
        """侧翼候选点必须有传球净空，避免把队友派入禁区。"""
        candidates = [self.legal(Point(ball.x+dx, ball.y+dy))
                      for dx in (250, 650) for dy in (-1500, -1100, 1100, 1500)]
        candidates = [p for p in candidates if 850 < distance(ball, p) < 2400
                      and clearance(ball, p, opponents) > 200]
        return max(candidates, key=lambda p: self.shot(p, opponents)[1]-distance(ball, p)*0.02) if candidates else None

    def choose(self, now, ball, players, opponents, last_pass=-100.0, freeze_striker=False):
        """给官方状态机提供选人/目标建议；没有 SETUP/KICK 等字符串状态赋值。"""
        players = {n:p for n,p in players.items() if finite(p)}
        opponents = [p for p in opponents if finite(p)]
        status = self.ball_status(ball)
        if status in ('NO_BALL', 'BALL_OUT'):
            return AttackChoice(status)
        ids = [n for n in self.c['attackers'] if n in players]
        if not ids:
            return AttackChoice('NO_ATTACKER')
        cost = {n:distance(players[n], ball)+max(0, players[n].x-ball.x)*0.35 for n in ids}
        best = min(ids, key=cost.get)
        # 换主攻需至少 1.2 秒、450 mm 成本优势；当前车丢失时立即换人。
        if self.striker not in ids or (not freeze_striker and now-self.changed > 1.2 and cost[best]+450 < cost[self.striker]):
            self.striker, self.changed = best, now
        other = next((n for n in ids if n != self.striker), None)
        obstacles = opponents+([players[other]] if other is not None else [])
        aim, _ = self.shot(ball, obstacles)
        if self.target is None or now-self.target_time > .65:
            self.target, self.target_time = aim, now
        offer = self.receiving_point(ball, opponents) if other is not None and self.c.get('enable_pass', False) else None
        support = offer or self.legal(Point(ball.x-650, -1100 if ball.y >= 0 else 1100))
        if distance(support, ball) < 800:
            support = self.legal(Point(ball.x-900, support.y))
        choice = AttackChoice(status or 'ATTACK', self.striker, other, self.target, support)
        if status:
            return choice
        own_score = self.shot(ball, opponents)[1]
        if (offer is not None and ball.x > 1500 and now-last_pass > 6
                and distance(players[self.striker], ball) < 280
                and own_score < self.c.get('pass_shot_threshold', 230)
                and self.shot(offer, opponents)[1] > own_score+self.c.get('pass_score_gain', 60)):
            choice.pass_target = offer
        if self.c.get('advance_before_shoot', False) and ball.x < self.c.get('shoot_from_x', 2650):
            choice.phase = 'ADVANCE'
            choice.carry_target = self.legal(Point(self.c.get('shoot_from_x', 2650), ball.y*.45))
        return choice


def referee_mode(message, game_on, can_kick):
    """HALT=不动；STOP=退球准备；未知/未实现的定位球保守停止。"""
    if message in ('GameHalt', 'Ref_Halt', 'GameOver', 'OurTimeout', 'TheirTimeout',
                   'OurBallPlacement', 'TheirBallPlacement', 'OurPenaltyKick', 'TheirPenaltyKick'):
        return 'HALT'
    if message == 'GameStop':
        return 'STOP'
    if message in ('TheirKickOff', 'TheirDirectKick', 'TheirIndirectKick'):
        return 'DEFEND_RESTART'
    if message == 'OurIndirectKick':
        return 'PREPARE'  # 普通实验传球不等于已实现间接任意球规则。
    if message in ('OurKickOff', 'OurDirectKick'):
        return 'OUR_RESTART' if can_kick else 'PREPARE'
    if message not in ('', 'NormalPlay'):
        return 'UNKNOWN_REF'
    return 'PLAY' if game_on else 'WAIT_START'
