"""不带球的静态接触计划：先离球、空车绕后，最后短距离贴球出球。

没有球前推进点，不开吸球，不携球转向。held只表示车头近球的几何观察，
不表示占有球、更不许可带球。旧带球版本保留在Git标签before-rules-v4。
"""
from dataclasses import dataclass
import math
from match_tactics import Point, distance


def wrap(angle):
    return math.atan2(math.sin(angle),math.cos(angle))


class ActionBudget:
    """有效距离/角误差必须下降；远处接近的总期限按路径长度放宽。"""
    def __init__(self):
        self.origin = self.target = None
        self.started = self.progress_at = None
        self.best = math.inf
        self.deadline = math.inf
        self.reason = None

    def update(self, now, robot, heading, ball, target, config):
        desired = math.atan2(target.y-ball.y, target.x-ball.x)
        gap = distance(robot, ball)
        radial = abs(wrap(desired+math.pi-math.atan2(robot.y-ball.y, robot.x-ball.x)))
        angle = abs(wrap(heading-desired))
        remaining = max(0, gap-config.get('contact_distance', 85))+320*radial+150*angle
        if self.origin is None or distance(ball, self.origin) > 100 or distance(target, self.target) > 50:
            self.origin, self.target = ball, target
            self.started = self.progress_at = now
            self.best = remaining
            self.deadline = now+config.get('action_align_timeout_s', 1.)+remaining/250
            self.reason = None
        if remaining < self.best-15:
            self.best, self.progress_at = remaining, now
        if now > self.deadline:
            self.reason = 'ACTION_DEADLINE'
        elif now-self.progress_at > config.get('action_no_progress_s', .65):
            self.reason = 'ACTION_NO_PROGRESS'
        return self.reason



@dataclass
class BallControl:
    budget: ActionBudget | None = None
    held: bool = False
    direction: float | None = None
    updated: float | None = None
    approaching: bool = False
    releasing: bool = False
    orbit_sign: int = 0
    stage: str = 'RELEASE'
    orbit_remaining: float | None = None

    def reset(self):
        self.budget = None
        self.held,self.direction,self.updated = False,None,None
        self.approaching = False
        self.releasing = False
        self.orbit_sign = 0
        self.stage = 'RELEASE'
        self.orbit_remaining = None

    def plan(self, now, robot, heading, ball, target, *, carry=False, contact=85, trim=False):
        if carry:
            raise ValueError('No-carry ruleset: carrying is disabled, including compatibility calls')
        desired = math.atan2(target.y-ball.y,target.x-ball.x)
        dx,dy = ball.x-robot.x,ball.y-robot.y
        along = dx*math.cos(desired)+dy*math.sin(desired)
        lateral = -dx*math.sin(desired)+dy*math.cos(desired)
        error = abs(wrap(heading-desired))
        self.held = 50 < along < 165 and abs(lateral) < 30 and error < .12
        # 进球前最后几厘米不能用同一阈值反复切换“靠近/退320mm”。
        # 首次进入仍严格；进入后只允许小幅横向修正。大角度换目标仍先离球。
        fresh = self.updated is not None and 0 <= now-self.updated <= .15
        same_direction = self.direction is not None and abs(wrap(desired-self.direction)) < .06
        aligned = (along > 0 and abs(lateral) < 20 and error < .06) or (
            fresh and self.approaching and same_direction and along > 75
            and abs(lateral) < 35 and error < .09)
        gap = distance(robot,ball)
        # 一旦决定离球，先完成离球再回来；不能下一帧角度刚恢复就掉头贴球。
        # 否则退几毫米又前进，会把球推走并触发近球路径预算。
        if self.releasing and gap >= 260:
            self.releasing = False
        if not aligned and gap < 260:
            self.releasing = True
        aligned = aligned and not self.releasing
        self.approaching = aligned
        self.direction,self.updated = desired,now
        radial = math.atan2(robot.y-ball.y,robot.x-ball.x)
        radial_error = wrap(desired+math.pi-radial)
        # 固定绕行方向不能把“刚越过终点”解释为还要绕近一整圈。
        # 上一帧剩余角小于半圈、这一帧有向角突然接近2π，说明已经越过。
        directed = (radial_error*self.orbit_sign) % (2*math.pi) if self.orbit_sign else None
        crossed = (directed is not None and self.orbit_remaining is not None
                   and self.orbit_remaining < math.pi and directed-self.orbit_remaining > math.pi)
        if crossed or abs(radial_error) < .20:
            self.orbit_sign = 0
            self.orbit_remaining = None
            # 已经在球后，先在320mm处做横向精调，避免再次绕圈。
            radial_error = 0.
        if not aligned:
            # 大角度变向先径向退到320mm，再走避球圆；不能贴球绕转。
            if self.releasing:
                self.stage = 'RELEASE'
                destination = Point(ball.x+320*math.cos(radial),ball.y+320*math.sin(radial))
            elif abs(radial_error) > .12:
                # 一次绕后动作锁住顺/逆时针方向，避免视觉噪声令方向逐帧翻转。
                if not self.orbit_sign:
                    self.orbit_sign = 1 if radial_error > 0 else -1
                self.orbit_remaining = (radial_error*self.orbit_sign) % (2*math.pi)
                self.stage = 'ORBIT'
                waypoint = radial+self.orbit_sign*min(.45,self.orbit_remaining)
                destination = Point(ball.x+320*math.cos(waypoint),ball.y+320*math.sin(waypoint))
            else:
                self.orbit_sign = 0
                self.stage = 'ALIGN'
                destination = Point(ball.x-320*math.cos(desired),ball.y-320*math.sin(desired))
            return destination,desired,False,self.held,'RELEASE_ALIGN'
        self.orbit_sign = 0
        self.orbit_remaining = None
        self.stage = 'CONTACT'
        destination = Point(ball.x-contact*math.cos(desired),ball.y-contact*math.sin(desired))
        if trim and abs(lateral) > 6:
            correction = max(-20,min(20,lateral))
            destination = Point(destination.x-correction*math.sin(desired),
                                destination.y+correction*math.cos(desired))
        return destination,desired,True,self.held,'CONTACT'
