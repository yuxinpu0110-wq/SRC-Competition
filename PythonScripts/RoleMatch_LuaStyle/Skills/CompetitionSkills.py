"""官方Task可执行的组合Skill。进攻技能全部关闭吸球；拒绝旧carry参数。

执行链仍是State→Task→Skill.RushTo/kickStatus，不绕过SDK直接遥控仿真。
"""
import math
import CppPackage as C
from RoleMatch_LuaStyle.Skills import Skill
from Vision import Player
from WorldModel import kickStatus
from match_tactics import Point, distance


def MoveTo(target, ball, *, slow=False, receive=False, enable_dss=True, facing=None):
    """无球移动；接球车近球后停车等球，不边吸球边追球。"""
    point = C.CGeoPoint(target.x,target.y)
    resources = [point]
    def execute(executor):
        pos = Player.Pos(executor)
        robot = Point(pos.x(),pos.y())
        angle = facing if facing is not None else math.atan2(ball.y-robot.y,ball.x-robot.x)
        if receive and distance(robot,ball) < 170:
            skill = Skill.Stop()
        else:
            flags = (0x08000000 if enable_dss else 0) | 0x200 | (0 if receive else 0x10)
            if slow:
                flags |= 1 | 0x100000
            skill = Skill.RushTo(point,angle=angle,maxAcc=1500 if slow or receive else 2600,
                                 flag=flags,needDribble=False)
        resources.append(skill)
        skill[0](executor)
    return execute,lambda executor:point


def TargetedKick(target, ball, power, geometry, *, carry=False, can_kick=True,
                 enable_dss=True, on_kick=None, report_phase=None, gate=None, now=0,
                 shooting=True, control=None):
    """静态接触后射/传；瞄准改变时先脱离球再绕后，不做携球假动作。"""
    if carry:
        raise ValueError('No-carry ruleset forbids TargetedKick(carry=True)')
    if gate is None or control is None:
        raise ValueError('Ruleset requires precision gate and static contact planner')
    match_point = C.CGeoPoint(ball.x,ball.y)
    resources = [match_point]
    def execute(executor):
        heading = Player.direction(executor)
        pos = Player.Pos(executor)
        robot = Point(pos.x(),pos.y())
        if not all(math.isfinite(v) for v in (heading,robot.x,robot.y,target.x,target.y,ball.x,ball.y)):
            control.reset()
            skill = Skill.Stop()
            resources.append(skill)
            skill[0](executor)
            return
        dest,angle,aligned,held,phase = control.plan(now,robot,heading,ball,target,
            contact=geometry.c.get('contact_distance',85),trim=geometry.c.get('contact_trim',False))
        dest = geometry.legal(dest)
        point = C.CGeoPoint(dest.x,dest.y)
        flags = (0x08000000 if enable_dss else 0) | 0x200 | (0 if aligned else 0x10)
        skill = Skill.RushTo(point,angle=angle,maxAcc=1200 if aligned else 2200,
                             flag=flags,needDribble=False)
        resources.extend((point,skill))
        skill[0](executor)
        allowed,reason = gate.allow(now,robot,heading,ball,target,geometry.c,shooting=shooting)
        if aligned and distance(robot,ball) < 150 and can_kick and allowed:
            # 力度不是实测球速；这里再限幅，物理6.5m/s还必须独立验证。
            kickStatus.setKick(executor,min(power,geometry.c['power_cap'],6000))
            gate.fired(now)
            phase = 'KICK'
            if on_kick is not None:
                on_kick(executor,ball)
        elif aligned and can_kick:
            phase = 'WAIT_'+reason
        if report_phase is not None:
            report_phase(phase)
    return execute,lambda executor:match_point
