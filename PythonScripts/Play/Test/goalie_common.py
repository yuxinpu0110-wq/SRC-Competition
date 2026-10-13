"""供三个练习守门员共用的坐标、移动和威胁判断工具。

这些策略只用于单独测试 ``Global.goalieNumber``。它们不应接入当前初赛的
``Preliminary2026`` 正式配置：该赛制由组委会提供官方守门员，参赛队仅控制两辆
进攻车。

坐标单位是 mm。ZBin 的归一化坐标中，我方球门在 X 负方向；所以门前左右移动
对应 Y 轴变化。
"""

from __future__ import annotations

import math
import random

import CppPackage
from Geometry import CGeoPoint, CVector
from Vision import Ball, Enemy, Player
from WorldModel import Params, Positions, taskMediator


# 所有目标点都在球门线前（场内）而不是球门后，避免直接撞入球门框。
GOAL_LINE_OFFSET = 220
# 门柱内再留出的余量；球门宽度会随小场/大场参数自动变化。
POST_MARGIN = Params.playerRadius + 55
# RushTo 使用此加速度上限，数值偏保守，便于观察三种行为差异。
DEFAULT_MAX_ACC = 1800


def clamp(value: float, lower: float, upper: float) -> float:
    """将数值夹到闭区间中。"""
    return max(lower, min(value, upper))


def guard_x() -> float:
    """守门员活动线的 X 坐标（己方球门线向场内偏移）。"""
    return -Params.pitchLength / 2.0 + GOAL_LINE_OFFSET


def guard_y_limit(extra_margin: float = 0) -> float:
    """不碰门柱时，守门员中心可到达的最大 |Y|。"""
    return max(0.0, Params.goalWidth / 2.0 - POST_MARGIN - extra_margin)


def guard_point(y: float, *, x_offset: float = GOAL_LINE_OFFSET,
                y_margin: float = 0) -> CGeoPoint:
    """生成始终位于己方门前安全横向范围内的目标点。"""
    limit = guard_y_limit(y_margin)
    return CGeoPoint(-Params.pitchLength / 2.0 + x_offset,
                     clamp(y, -limit, limit))


def goalie_rush_to(target: CGeoPoint, face_at: CGeoPoint, *,
                    max_acc: float = DEFAULT_MAX_ACC,
                    expected_velocity: CVector | None = None):
    """返回一个可交给 ``Task`` 的守门员移动 Skill。

    不能直接复用 ``RushTo``：它会把 C++ 侧角色注册成 ``fetchBall``。这里保留
    RushTo 的底层路径规划接口，但先后两层都登记为 ``goalie``，使机器人仍能在
    己方禁区中按守门员身份移动。

    ``expected_velocity`` 是传入 C++ RushTo 的期望末速度；它不是底盘速度的硬件
    上限。实际加减速、避障和轮子控制仍由底层规划器负责。
    """
    if expected_velocity is None:
        expected_velocity = CVector(0, 0)

    def skill_cpp(executor: int):
        # 视觉失效时只下停止任务；不要用旧位置继续移动。
        if not Player.valid(executor):
            return CppPackage.makeItStop(executor)

        taskMediator.registerRole(executor, "goalie")
        CppPackage.TaskMediator.Instance().registerRole(executor, "goalie")

        current = Player.Pos(executor)
        # 球/进攻车恰与守门员重合时，保留当前朝向，避免对零向量取角度。
        angle = Player.direction(executor) if current.dist(face_at) < 1 else (face_at - current).dir()
        return CppPackage.makeItRushTo(
            executor, target, angle, 0, 0, max_acc, False, expected_velocity
        )

    def match_pos(executor: int) -> CGeoPoint:
        return target

    return skill_cpp, match_pos


class PatrolController:
    """在门前 Y 轴两端之间往返的控制器。"""

    def __init__(self, *, cruise_speed: float = 650, turn_lead: float = 120):
        self.cruise_speed = cruise_speed
        self.turn_lead = turn_lead
        self.direction = 1

    def reset(self) -> None:
        self.direction = 1

    def command(self, goalie_y: float) -> tuple[CGeoPoint, CVector]:
        """根据当前位置决定下一个端点和所需横向巡航速度。"""
        limit = guard_y_limit()
        # 预留反向距离；否则带有期望速度的路径规划会在门柱附近才开始减速。
        if self.direction > 0 and goalie_y >= limit - self.turn_lead:
            self.direction = -1
        elif self.direction < 0 and goalie_y <= -limit + self.turn_lead:
            self.direction = 1

        endpoint = guard_point(self.direction * limit)
        return endpoint, CVector(0, self.direction * self.cruise_speed)


class RandomWalkController:
    """在门前矩形区域内选点；到点后才换目标，避免每帧随机造成抖动。"""

    def __init__(self, *, seed: int = 2026, x_min: float = 180,
                 x_max: float = 620, arrival_distance: float = 100,
                 minimum_step: float = 180):
        self.seed = seed
        self.x_min = x_min
        self.x_max = x_max
        self.arrival_distance = arrival_distance
        self.minimum_step = minimum_step
        self._rng = random.Random(seed)
        self.target: CGeoPoint | None = None

    def reset(self) -> None:
        # 固定种子使每次重新进入测试模式的轨迹可复现，便于调参比较。
        self._rng = random.Random(self.seed)
        self.target = None

    def choose_target(self, goalie_pos: CGeoPoint) -> CGeoPoint:
        if self.target is not None and goalie_pos.dist(self.target) > self.arrival_distance:
            return self.target

        y_limit = guard_y_limit(35)
        # 拒绝过近的新点，避免到达后只做很小的来回抖动。
        for _ in range(20):
            candidate = guard_point(
                self._rng.uniform(-y_limit, y_limit),
                x_offset=self._rng.uniform(self.x_min, self.x_max),
                y_margin=35,
            )
            if candidate.dist(goalie_pos) >= self.minimum_step:
                self.target = candidate
                return candidate

        # 极少数情况下所有采样点都过近，仍保证返回一个安全目标点。
        self.target = guard_point(0, x_offset=(self.x_min + self.x_max) / 2,
                                  y_margin=35)
        return self.target


def _enemy_motion_direction(number: int) -> float:
    """优先取实际速度方向；静止时退回车头朝向。"""
    velocity = Enemy.vel(number)
    return velocity.dir() if velocity.mod() >= 150 else Enemy.dir(number)


def select_threat() -> int | None:
    """选出最可能冲向我方球门的敌方机器人。

    离我方球门近、车头或运动方向朝向我方球门的敌车具有更高威胁。若无人朝门，
    仍会退化为离球门最近的可见敌车，保证策略不因短暂转身而失效。
    """
    best_number: int | None = None
    best_score = float("inf")
    for number in range(Params.maxPlayer):
        if not Enemy.valid(number):
            continue

        enemy_pos = Enemy.pos(number)
        heading = _enemy_motion_direction(number)
        toward_goal = math.cos((Positions.ourGoal - enemy_pos).dir() - heading)
        # 朝门移动时最多得到 900 mm 的优先级；同距离时优先盯住该车。
        score = enemy_pos.dist(Positions.ourGoal) - 900 * max(0.0, toward_goal)
        if Ball.valid():
            # 与球更接近的进攻车略优先，避免只盯一个远处但朝向恰巧合适的车。
            score += 0.12 * enemy_pos.dist(Ball.pos())
        if score < best_score:
            best_score = score
            best_number = number
    return best_number


def attacker_intercept_target(number: int) -> tuple[CGeoPoint, CGeoPoint]:
    """把进攻者的运动射线投影到守门横线，返回 ``(站位, 面向点)``。

    只有进攻者实际向 X 负方向（我方球门）移动时才做直线预测；其它朝向用它
    当前的 Y 位置退化处理。最后统一限幅，故不会跟着假预测撞门柱。
    """
    attacker = Enemy.pos(number)
    heading = _enemy_motion_direction(number)
    cos_heading = math.cos(heading)
    predicted_y = attacker.y()

    if cos_heading < -0.15:
        # t 是进攻者沿当前方向走到守门横线所需的距离参数。
        t = (guard_x() - attacker.x()) / cos_heading
        if 0 <= t <= Params.pitchLength * 1.5:
            predicted_y = attacker.y() + t * math.sin(heading)

    return guard_point(predicted_y), attacker


def fallback_tracking_target() -> tuple[CGeoPoint, CGeoPoint]:
    """暂未看到敌人时盯球；连球也失效时回到门中央。"""
    if Ball.valid():
        ball_pos = Ball.pos()
        return guard_point(ball_pos.y()), ball_pos
    center = guard_point(0)
    return center, CGeoPoint(0, 0)
