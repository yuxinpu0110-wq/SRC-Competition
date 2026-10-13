"""2026校内赛V1.0的保守运行保护，不是自动裁判。距离mm，时间s。

不主动带球；近球路径预算仅300mm，留出距规则1m的安全余量。
定位球出球后锁住该车，不把球走50mm误当作允许原发球车再次触球。
"""
from dataclasses import dataclass
import math
from match_tactics import Point, distance, finite

RULESET = 'src2026-v1-no-carry'


@dataclass
class ContactEpisode:
    robot: Point
    ball: Point
    travel_robot: float = 0
    travel_ball: float = 0
    separated_since: float | None = None
    blocked: bool = False


class ContactGuard:
    def __init__(self, budget=300):
        if not 50 <= budget <= 400:
            raise ValueError('Conservative contact budget must be 50..400 mm')
        self.budget = budget
        self.episodes = {}

    def observe(self, number, now, robot, ball):
        if not finite(robot) or not finite(ball):
            return
        gap = distance(robot,ball)
        e = self.episodes.get(number)
        if e is None:
            if gap < 180:
                self.episodes[number] = ContactEpisode(robot,ball)
            return
        if gap >= 260:
            if e.separated_since is None:
                e.separated_since = now
            elif now-e.separated_since >= .12:
                del self.episodes[number]
                return
        else:
            e.separated_since = None
        # 出球后球飞离车头不算带球路程，不能把一次合法传球记成控球超限。
        if gap < 180 and distance(e.robot,e.ball) < 180:
            e.travel_robot += distance(robot,e.robot)
            e.travel_ball += distance(ball,e.ball)
        e.robot,e.ball = robot,ball
        e.blocked = e.blocked or max(e.travel_robot,e.travel_ball) >= self.budget

    def blocked(self, number):
        return number in self.episodes and self.episodes[number].blocked

    def diagnostics(self):
        return {str(n):dict(robot_path_mm=e.travel_robot,ball_path_mm=e.travel_ball,
                           blocked=e.blocked,budget_mm=self.budget) for n,e in self.episodes.items()}


class RestartGuard:
    def __init__(self):
        self.kind = None
        self.kicker = None
        self.origin = None
        self.started = False
        self.satisfied = False
        self.baseline_ir = {}

    def reset(self):
        self.__init__()

    def begin(self, kind):
        if self.kind != kind:
            self.reset()
            self.kind = kind

    def requested(self, number, ball, infrared):
        if self.kind is not None and self.kicker is None:
            self.kicker,self.origin = number,ball
            self.baseline_ir = dict(infrared)

    def observe(self, ball, infrared, near_ids):
        if self.kicker is None or self.satisfied:
            return
        self.started = self.started or distance(ball,self.origin) > 50
        # 只根据己方队友新增红外帧数解锁；不可读的敌方触球不凭距离猜测。
        if self.started and any(n != self.kicker and infrared.get(n,0)-self.baseline_ir.get(n,0) >= 3
                                for n in near_ids):
            self.satisfied = True

    def blocked(self, number):
        return self.kicker == number and not self.satisfied

    @property
    def requires_pass(self):
        return self.kind == 'OurIndirectKick' and not self.satisfied

    def diagnostics(self):
        return dict(kind=self.kind,kicker=self.kicker,ball_moved_50mm=self.started,
                    other_touch_confirmed=self.satisfied)


def retreat_target(robot, ball, geometry):
    angle = math.atan2(robot.y-ball.y,robot.x-ball.x) if distance(robot,ball) > 1 else math.pi
    target = geometry.legal(Point(ball.x+380*math.cos(angle),ball.y+380*math.sin(angle)))
    return target if distance(target,ball) >= 270 else None
