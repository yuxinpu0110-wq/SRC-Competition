"""组合技能采用官方 (执行函数, 匹配位置函数) 接口，交给 Task 执行。

移动仍调用官方 Skill.RushTo，踢球使用 kickStatus；这里不切换战术状态。

读法：MoveTo(...) / TargetedKick(...) 是“制作动作”的工厂，不会当场开车。
它们返回两个函数：(execute, matchPos)。官方 Task.run 之后才调用 execute(车号)。
内部函数会记住外层 target/ball 等变量，Python 把这种写法叫“闭包”。
lambda executor: point 是简写函数：接收车号，返回任务位置；不是运动命令。
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
        # executor 是官方任务分配后给出的真实车号，不是 A/B 角色字符串。
        pos = Player.Pos(executor)
        angle = math.atan2(ball.y-pos.y(), ball.x-pos.x())
        # atan2(纵向差, 横向差) 得到车朝球看的角度，单位弧度。
        # flag 是官方标志位的组合（用 | 拼起来），不是速度数值：
        # 0x08000000 开启 DSS，0x200 避开己方禁区，0x10 避球。
        # 接球需要接触球，所以 receive=True 时不加避球标志。
        flags = (0x08000000 if enable_dss else 0) | 0x200 | (0 if receive else 0x10)
        if slow:
            flags |= 1 | 0x100000
        skill = Skill.RushTo(point, angle=angle, maxAcc=1800 if slow or receive else 3000,
                             flag=flags, needDribble=receive)
        resources.append(skill)
        # 官方 Skill 返回元组，第0项是执行函数，第1项是匹配位置函数。
        skill[0](executor)
    return execute, lambda executor: point


def TargetedKick(target, ball, power, geometry, *, carry=False, can_kick=True,
                 enable_dss=True, on_kick=None, report_phase=None, gate=None, now=0,
                 shooting=True):
    """绕后→对齐→接触；carry 不踢，can_kick=False 只瞄准。

    自定义接触/发球条件，实际轨迹交给官方 RushTo。
    on_kick 只通知“请求已发出”，不证明物理传球完成。
    """
    resources = []
    match_point = C.CGeoPoint(ball.x, ball.y)
    def execute(executor):
        angle = math.atan2(target.y-ball.y, target.x-ball.x)
        # (ux, uy) 是从球指向目标的单位方向向量，长度为1。
        ux, uy = math.cos(angle), math.sin(angle)
        pos = Player.Pos(executor)
        robot = Point(pos.x(), pos.y())
        # along：车到球在射门方向上的距离，>0 才说明车在球后。
        # lateral：车偏离“球→目标”直线的横向距离。
        along = (ball.x-robot.x)*ux+(ball.y-robot.y)*uy
        lateral = abs((ball.x-robot.x)*uy-(ball.y-robot.y)*ux)
        err = abs(math.atan2(math.sin(Player.direction(executor)-angle), math.cos(Player.direction(executor)-angle)))
        # sin/cos + atan2 把角度差折回 [-π, π]，避免 179° 与 -179° 被算差358°。
        # aligned 要求：站在球后、横向偏差<35mm、朝向偏差<0.08rad（约4.6°）。
        aligned, close = along > 0 and lateral < 35 and err < .08, distance(robot, ball) < 150
        # precision 模式对齐后停在约95mm：官方车前沿76mm + 球半径21.5mm。
        # 旧65mm目标会要求车身继续挤球。带球仍向前推进，不在这里停车。
        offset = (95 if gate is not None and not carry else 65) if aligned else 240
        # 未对齐先去球后240mm绕好位置；对齐后靠到65mm，准备接触球。
        dest = geometry.legal(Point(ball.x-offset*ux, ball.y-offset*uy))
        if carry and aligned and close:
            # 带球时目标移到球前160mm，吸球并向前推进，不发送踢球请求。
            dest = geometry.legal(Point(ball.x+160*ux, ball.y+160*uy))
        point = C.CGeoPoint(dest.x, dest.y)
        flags = (0x08000000 if enable_dss else 0) | 0x200 | (0 if aligned else 0x10)
        skill = Skill.RushTo(point, angle=angle, maxAcc=900 if gate is not None and aligned else 1800,
                             flag=flags, needDribble=aligned)
        resources.extend((point, skill))
        skill[0](executor)
        allowed, reason = True, ''
        if gate is not None and carry:
            gate.stable_since = None
        if gate is not None and not carry:
            allowed, reason = gate.allow(now, robot, Player.direction(executor), ball, target,
                                         geometry.c, shooting=shooting)
        if aligned and close and not carry and can_kick and allowed:
            # “状态允许踢”还不够：车也必须真正靠近并对准。
            kickStatus.setKick(executor, power)
            if gate is not None:
                gate.fired(now)
            if on_kick is not None:
                on_kick(executor, ball)
        phase = ('CARRY' if carry and aligned and close else
                 'KICK' if aligned and close and can_kick and allowed else
                 'HOLD_BALL' if aligned and close else 'CONTACT' if aligned else 'ALIGN')
        if gate is not None and aligned and close and not carry and can_kick and not allowed:
            phase = 'WAIT_'+reason
        if report_phase is not None:
            # 只更新日志动作标签，不改变战术状态；不要在这里切换 State。
            report_phase(phase)
    return execute, lambda executor: match_point
