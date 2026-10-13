"""组合技能采用官方 (执行函数, 匹配位置函数) 接口，交给 Task 执行。

移动仍调用官方 Skill.RushTo，踢球使用 kickStatus；这里不切换战术状态。
"""
import math
import CppPackage as C
from RoleMatch_LuaStyle.Skills import Skill
from Vision import Player
from WorldModel import kickStatus
from match_tactics import Point, distance


def MoveTo(target, ball, *, slow=False, receive=False, enable_dss=True):
    """执行时读取车位置；接应时允许接触球，并开启吸球。"""
    point = C.CGeoPoint(target.x, target.y)
    resources = [point]  # 随 Task 保活，避免在 native 执行前释放参数。
    def execute(executor):
        pos = Player.Pos(executor)
        angle = math.atan2(ball.y-pos.y(), ball.x-pos.x())
        flags = (0x08000000 if enable_dss else 0) | 0x200 | (0 if receive else 0x10)
        if slow:
            flags |= 1 | 0x100000
        skill = Skill.RushTo(point, angle=angle, maxAcc=1800 if slow or receive else 3000,
                             flag=flags, needDribble=receive)
        resources.append(skill)
        skill[0](executor)
    return execute, lambda executor: point


def TargetedKick(target, ball, power, geometry, *, carry=False, can_kick=True,
                 enable_dss=True, on_kick=None, report_phase=None):
    """绕后→对齐→接触；carry 不踢，can_kick=False 只瞄准。

    自定义接触/发球条件，实际轨迹交给官方 RushTo。
    on_kick 只通知“请求已发出”，不证明物理传球完成。
    """
    resources = []
    match_point = C.CGeoPoint(ball.x, ball.y)
    def execute(executor):
        angle = math.atan2(target.y-ball.y, target.x-ball.x)
        ux, uy = math.cos(angle), math.sin(angle)
        pos = Player.Pos(executor)
        robot = Point(pos.x(), pos.y())
        along = (ball.x-robot.x)*ux+(ball.y-robot.y)*uy
        lateral = abs((ball.x-robot.x)*uy-(ball.y-robot.y)*ux)
        err = abs(math.atan2(math.sin(Player.direction(executor)-angle), math.cos(Player.direction(executor)-angle)))
        aligned, close = along > 0 and lateral < 35 and err < .08, distance(robot, ball) < 150
        offset = 65 if aligned else 240
        dest = geometry.legal(Point(ball.x-offset*ux, ball.y-offset*uy))
        if carry and aligned and close:
            dest = geometry.legal(Point(ball.x+160*ux, ball.y+160*uy))
        point = C.CGeoPoint(dest.x, dest.y)
        flags = (0x08000000 if enable_dss else 0) | 0x200 | (0 if aligned else 0x10)
        skill = Skill.RushTo(point, angle=angle, maxAcc=1800, flag=flags, needDribble=aligned)
        resources.extend((point, skill))
        skill[0](executor)
        if aligned and close and not carry and can_kick:
            kickStatus.setKick(executor, power)
            if on_kick is not None:
                on_kick(executor, ball)
        phase = ('CARRY' if carry and aligned and close else
                 'KICK' if aligned and close and can_kick else
                 'HOLD_BALL' if aligned and close else 'CONTACT' if aligned else 'ALIGN')
        if report_phase is not None:
            report_phase(phase)
    return execute, lambda executor: match_point
