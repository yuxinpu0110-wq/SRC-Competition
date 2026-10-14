"""纯 Python 战术计算，不再保存或切换传球状态。

真正的状态机在 Play/Normal/Normalplay.py，使用官方 State/Task/Skill。
这里只保留选人、站位、射门评分等可离线检查的辅助算法。
距离为毫米；己方归一化坐标中朝正 X 进攻；now 为单调时钟秒数。
"""
from strategy_contract import uses_verified_control
from dataclasses import dataclass
import math
from pass_planner import evaluate_pass


@dataclass(frozen=True)
class Point:
    """纯 Python 坐标，便于离线测试；实际交给官方技能前再转成 CGeoPoint。"""
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
        # t 是障碍物投影在 a→b 线段上的比例：0在起点，1在终点。
        # 只看线路附近的障碍，不把远在球后/球门后的车也当作线路阻挡。
        t = ((p.x-a.x)*dx+(p.y-a.y)*dy)/max(1, length2)
        if 0.02 < t < 1.05:
            t = min(1, t)
            result = min(result, distance(p, Point(a.x+t*dx, a.y+t*dy))-130)
    return result


@dataclass
class AttackChoice:
    """一次计算的结果；不是另一套状态机，也不直接驱动车辆。"""
    phase: str                       # 本次计算的建议/球状态，不是官方 current_state。
    striker: int | None = None        # 主攻车号；int | None 表示整数或“没有”。
    other: int | None = None          # 另一辆可见进攻车号。
    aim: Point | None = None          # 射门目标（门线上）。
    support: Point | None = None      # 队友普通接应位置。
    pass_target: Point | None = None  # 值得尝试传球时才设置，否则为 None。


class Tactics:
    """只记住主攻和射门目标的滞回信息，传球过程由官方 State 管理。"""
    def __init__(self, config):
        self.c = config
        self.striker = None
        self.changed = -100.0
        self.target = None
        self.target_time = -100.0
        self.last_pass_plans = []
        self.committed_ball = None
        self.shot_after_receive_until = -math.inf
        self.proposed_plan = None

    def legal(self, p):
        """将进攻车目标限制在场内/禁区外；不保证整段轨迹或所有规则合法。"""
        c = self.c
        x = max(-c['field_length']/2+180, min(p.x, c['field_length']/2-180))
        y = max(-c['field_width']/2+180, min(p.y, c['field_width']/2-180))
        # 与实体路径检查采用同一120mm膨胀量，额外2mm避免停在矩形边界上。
        # 旧220mm目标限制会把球前3428mm的接触点3333mm错误推回3280mm。
        edge = c['field_length']/2-c['defense_depth']-122
        if abs(x) > edge and abs(y) < c['defense_width']/2+122:
            x = math.copysign(edge, x)
        return Point(x, y)

    def in_defense(self, p):
        return (abs(p.x) > self.c['field_length']/2-self.c['defense_depth']-120
                and abs(p.y) < self.c['defense_width']/2+120)

    def segment_legal(self, a, b):
        """检查机器人实体膨胀后的禁区矩形；目标合法不代表线段合法。"""
        if not finite(a) or not finite(b):
            return False
        if max(abs(a.x), abs(b.x)) > self.c['field_length']/2-120 or max(abs(a.y), abs(b.y)) > self.c['field_width']/2-120:
            return False
        edge = self.c['field_length']/2-self.c['defense_depth']-120
        half = self.c['defense_width']/2+120
        for low_x, high_x in ((edge, self.c['field_length']/2+120),
                              (-self.c['field_length']/2-120, -edge)):
            low_t, high_t = 0., 1.
            for origin, delta, low, high in ((a.x,b.x-a.x,low_x,high_x), (a.y,b.y-a.y,-half,half)):
                if abs(delta) < 1e-9:
                    if not low <= origin <= high:
                        low_t, high_t = 1., 0.
                        break
                else:
                    t1, t2 = (low-origin)/delta, (high-origin)/delta
                    low_t, high_t = max(low_t,min(t1,t2)), min(high_t,max(t1,t2))
            if low_t <= high_t:
                return False
        return True

    def safe_waypoint(self, robot, target):
        """无禁区穿越的下一段；已有非法位置只允许朝禁区外释放。"""
        if self.in_defense(robot):
            return self.legal(robot)
        if self.c.get('strategy_version')=='2.3':
            from legal_path import route
            path=route(self,robot,target)
            return path[1] if path is not None else None
        if self.segment_legal(robot, target):
            return target
        edge = self.c['field_length']/2-self.c['defense_depth']-122
        candidates = [Point(math.copysign(edge, robot.x), robot.y),
                      Point(0, robot.y), Point(target.x, robot.y)]
        candidates = [self.legal(p) for p in candidates]
        candidates = [p for p in candidates if distance(p,robot) > 20 and self.segment_legal(robot,p)]
        return min(candidates, key=lambda p:distance(robot,p)+distance(p,target)) if candidates else None

    def ball_status(self, ball):
        if not finite(ball):
            return 'NO_BALL'
        if abs(ball.x) > self.c['field_length']/2 or abs(ball.y) > self.c['field_width']/2:
            return 'BALL_OUT'
        # 球的位置用真实禁区边界，机器人中心才需要按实体半径膨胀。
        # 否则球在3380..3500的合法前沿就被直接转入永久等待状态。
        inside = (abs(ball.x) >= self.c['field_length']/2-self.c['defense_depth']
                  and abs(ball.y) <= self.c['defense_width']/2)
        return 'WAIT_OUTSIDE_DEFENSE' if inside else ''

    def shot(self, ball, obstacles):
        """在门线上选 9 点，优先净空，略惩罚远距离；返回 (点, 分数)。"""
        # 保留更宽门柱余量：远距离的微小偏角也会变成几十毫米落点误差。
        # 这是保守瞄点，不是未经标定的固定偏角补偿。
        half_goal = self.c['goal_width']/2-self.c.get('goal_margin', 160)
        points = [Point(self.c['field_length']/2, half_goal*i/4) for i in range(-4, 5)]
        from reactive_policy import enabled
        if enabled(self.c):
            # 大斜角时整球越线的Y会继续偏移，不能只看门线上瞄点。
            safe=[]
            for p in points:
                angle=math.atan2(p.y-ball.y,p.x-ball.x)
                if math.cos(angle)>.05 and all(abs(ball.y+(p.x+21.5-ball.x)*math.tan(a))
                        <self.c['goal_width']/2-70 for a in (angle-.025,angle+.025)):
                    safe.append(p)
            points=safe or [Point(self.c['field_length']/2,0)]
        def score(p):
            # 净空越大越好，线路越长略扣分。分数是启发式评分，不是成功率。
            return min(600, clearance(ball, p, obstacles))-distance(ball, p)*0.015
        best = max(points, key=score)
        # max(..., key=score) 返回评分最高的点，不是最高的分数本身。
        return best, score(best)

    def receiving_point(self, ball, opponents, receiver=None):
        """选择队友来得及到达且球能滚到的接应点。

        receiver=None 保留给旧调用和定位球缺少完整观察时的保守几何筛选。
        评分是启发式，不是传球成功概率；模型参数后续需要仿真标定。
        """
        # 不带球时依靠真实前传推进；不把队友只派到球侧后方。
        front = self.c['field_length']/2-self.c['defense_depth']-300
        candidates = [self.legal(Point(min(front,ball.x+dx), ball.y+dy))
                      for dx in (650, 1600, 2200, 2800) for dy in (-1500, -1100, -900, -600, 600, 900, 1100, 1500)]
        from reactive_policy import enabled
        if enabled(self.c):
            # 短传推进/横向转移。只用粗距离，不依赖预估飞行时间；先等接应车就位。
            near_goal=distance(ball,Point(self.c['field_length']/2,0))<self.c['reactive_shot_max_mm']
            offsets=(-650,-350,0,350,650) if near_goal else (0,350,650,1000,1200)
            v23=self.c.get('strategy_version')=='2.3'
            if v23 and not near_goal:
                offsets=(650,1200,1600,2050)
            lateral=(-1200,-900,-500,-250,0,250,500,900,1200) if v23 else (-1200,-900,-500,-250,250,500,900,1200)
            candidates=[self.legal(Point(ball.x+dx,ball.y+dy)) for dx in offsets for dy in lateral]
            candidates=[p for p in candidates if (650 if v23 else 700)<=distance(ball,p)<=(self.c['v23_advance_pass_max_mm'] if v23 else self.c['reactive_pass_max_mm'])
                and clearance(ball,p,opponents)>220]
            # 接触位/接应车实体也必须合法。门前不能选一个根本绕不到球后的侧传。
            legal_candidates=[]
            for p in candidates:
                length=distance(ball,p);ux,uy=(p.x-ball.x)/length,(p.y-ball.y)/length
                sender_pose=Point(ball.x-110*ux,ball.y-110*uy)
                receiver_pose=Point(p.x+110*ux,p.y+110*uy)
                if distance(sender_pose,self.legal(sender_pose))<1 and distance(receiver_pose,self.legal(receiver_pose))<1:
                    legal_candidates.append(p)
            candidates=legal_candidates
            self.last_pass_plans=[]
            if v23 and getattr(self,'planning_players',None):
                from attack_plan import pass_plan
                other=next((n for n in self.c['attackers'] if n!=self.striker and n in self.planning_players),None)
                plans=[pass_plan(ball,p,self.striker,other,self.planning_players,self.shot(p,opponents)[0],self,self.c)
                       for p in candidates] if other is not None else []
                plans=[p for p in plans if p is not None]
                if not plans:return None
                # 远处按真实推进收益/完整成本选；近门按终结完整成本选，不只看门口角度。
                chosen=max(plans,key=lambda p: ((p.legs[0].contact_point.x-ball.x)/max(.1,p.time_high_s)
                    if not near_goal else -p.time_high_s))
                self.proposed_plan=chosen
                return chosen.legs[0].contact_point
            def reactive_score(p):
                incoming=math.atan2(ball.y-p.y,ball.x-p.x)
                shot_angle=math.atan2(-p.y,self.c['field_length']/2-p.x)
                turn=abs(math.atan2(math.sin(incoming-shot_angle),math.cos(incoming-shot_angle)))
                # 近门优先回做/斜传，接球队员已经大致面向球门；远处才优先前传推进。
                score=(0 if near_goal else (p.x-ball.x)*.35)+(500 if near_goal else 1000)*self.goal_opening(p)
                score-=turn*(350 if near_goal else 50)
                score+=min(400,clearance(ball,p,opponents))*.2
                score-=distance(receiver,p)*.08 if receiver is not None else 0
                if near_goal and distance(p,Point(self.c['field_length']/2,0))>self.c['reactive_shot_max_mm']-25:
                    score-=2000
                return score
            return max(candidates,key=reactive_score) if candidates else None
        candidates = [p for p in candidates if 850 < distance(ball, p) < 3000
                      and clearance(ball, p, opponents) > 200]
        if receiver is not None and self.c.get('enable_reachable_pass', False):
            plans = [evaluate_pass(ball, receiver, p, opponents,
                                   initial_speed=self.c.get('pass_ball_speed_mm_s', 3000),
                                   wait_for_receiver=True)
                     for p in candidates]
            plans = [plan for plan in plans if plan is not None]
            self.last_pass_plans = plans
            return (max(plans, key=lambda plan: plan.score+1000*self.goal_opening(plan.target)).target
                    if plans else None)
        self.last_pass_plans = []
        # 接球时门将早已移动，不能用“现在门将离接应点射线很远”打高分。
        # 优先更近、更正的射门位置：从该点看见的门口角度越大，越容易打两侧。
        return max(candidates, key=lambda p: 1000*self.goal_opening(p)-distance(ball,p)*.025) if candidates else None

    def goal_opening(self, ball):
        """球到两侧门内安全点的夹角，单位弧度；不是进球概率。"""
        dx = self.c['field_length']/2-ball.x
        half = self.c['goal_width']/2-self.c.get('goal_margin',160)
        if dx <= 0:
            return 0.
        return math.atan2(half-ball.y,dx)-math.atan2(-half-ball.y,dx)

    def choose(self, now, ball, players, opponents, last_pass=-100.0, freeze_striker=False):
        """给官方状态机提供选人/目标建议；没有 SETUP/KICK 等字符串状态赋值。"""
        players = {n:p for n,p in players.items() if finite(p)}
        self.planning_players=players
        self.proposed_plan=None
        opponents = [p for p in opponents if finite(p)]
        status = self.ball_status(ball)
        if status in ('NO_BALL', 'BALL_OUT'):
            return AttackChoice(status)
        ids = [n for n in self.c['attackers'] if n in players]
        # 只让配置中的进攻车参加选人，不把门将拉出去抢球。
        if not ids:
            return AttackChoice('NO_ATTACKER')
        cost = {n:distance(players[n], ball)+max(0, players[n].x-ball.x)*0.35 for n in ids}
        # 成本越低越好：离球距离 + 站在球前面（需要绕后）的附加成本。
        # 这里只选一个主攻，不是 Munkres 的“多任务与多车整体匹配”。
        best = min(ids, key=cost.get)
        # 换主攻需至少 1.2 秒、450 mm 成本优势；当前车丢失时立即换人。
        if self.striker not in ids or (not freeze_striker and now-self.changed > 1.2 and cost[best]+450 < cost[self.striker]):
            self.striker, self.changed = best, now
        other = next((n for n in ids if n != self.striker), None)
        # next(..., None) 找到另一辆车；没有另一辆时返回 None，而不是报错。
        obstacles = opponents+([players[other]] if other is not None else [])
        aim, _ = self.shot(ball, obstacles)
        old_clearance = clearance(ball, self.target, obstacles) if self.target is not None else -math.inf
        new_clearance = clearance(ball, aim, obstacles)
        near_contact = distance(players[self.striker],ball) < 500
        if not near_contact or (self.committed_ball is not None and distance(ball,self.committed_ball) > 180):
            self.committed_ball = None
        if self.c.get('lock_contact_aim',False) and near_contact and self.committed_ball is None:
            self.committed_ball = ball
        committed = self.c.get('lock_contact_aim',False) and self.committed_ball is not None
        expired = uses_verified_control(self.c) and (
            (not near_contact and now-self.target_time > self.c.get('target_commit_timeout_s',2.5))
            or (hasattr(self,'target_ball') and distance(ball,self.target_ball) > 100))
        # 最后500mm内不能仅因计时器翻转门角。ActionBudget仍限制接近时长；
        # 真正失败由AbortRelease离球后清空target再选，避免贴球左右重规划。
        if expired:
            committed = False
        if (self.target is None or expired or (not committed and now-self.target_time > .65
                and (old_clearance < 0 or new_clearance > old_clearance+80))):
            # 短时间保留射门目标，避免视觉小抖动让车头每帧左右摇摆。
            self.target, self.target_time = aim, now
            self.target_ball = ball
        offer = (self.receiving_point(ball, opponents, players[other])
                 if other is not None and self.c.get('enable_pass', False) else None)
        support = offer or self.legal(Point(ball.x-650, -1100 if ball.y >= 0 else 1100))
        if distance(support, ball) < 800:
            support = self.legal(Point(ball.x-900, support.y))
        choice = AttackChoice(status or 'ATTACK', self.striker, other, self.target, support)
        if status:
            return choice
        own_score = self.shot(ball, opponents)[1]
        from reactive_policy import enabled
        if enabled(self.c):
            far=distance(ball,self.target)>self.c['reactive_shot_max_mm']-25
            blocked=clearance(ball,self.target,opponents)<self.c['reactive_shot_lane_margin_mm']
            if self.c.get('strategy_version')=='2.3':
                from attack_plan import direct_plan
                direct=direct_plan(ball,self.target,self.striker,players,self.c)
                cooperative=self.proposed_plan
                # 普通停球+转身比可执行直射更慢时，不因门将附近就强制传球。
                worth_pass=far or (blocked and cooperative is not None
                    and cooperative.kind=='TRAP_SHOT' and cooperative.time_high_s<direct.time_high_s+.75)
                if offer is not None and worth_pass and now-last_pass>self.c['reactive_pass_cooldown_s']:
                    choice.pass_target=offer
                elif far:
                    choice.phase='WAIT_SHORT_PASS';self.proposed_plan=None
                else:
                    self.proposed_plan=direct
                return choice
            if (offer is not None and now-last_pass>self.c['reactive_pass_cooldown_s']
                    and (far or (blocked and now>=self.shot_after_receive_until))):
                choice.pass_target=offer
            if far and choice.pass_target is None:
                choice.phase='WAIT_SHORT_PASS'
            return choice
        if (offer is not None and now-last_pass > 6
                and distance(players[self.striker], ball) < 280
                and own_score < self.c.get('pass_shot_threshold', 230)
                and (self.shot(offer, opponents)[1] > own_score+self.c.get('pass_score_gain', 60)
                     or (ball.x < 2400 and self.goal_opening(offer) > 1.25*self.goal_opening(ball)))):
            choice.pass_target = offer
            # 此处只是提出传球建议，NormalPlay 才决定切到 PassSetup。
        # 不再建议ADVANCE：推进靠真正出球给前方队友，而不是携球跑动。
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
        return 'OUR_INDIRECT' if can_kick else 'PREPARE'
    if message in ('OurKickOff', 'OurDirectKick'):
        return 'OUR_RESTART' if can_kick else 'PREPARE'
    if message not in ('', 'NormalPlay'):
        return 'UNKNOWN_REF'
    return 'PLAY' if game_on else 'WAIT_START'
