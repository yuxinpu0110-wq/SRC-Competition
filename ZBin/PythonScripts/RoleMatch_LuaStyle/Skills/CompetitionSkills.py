"""官方Task可执行的组合Skill。进攻技能全部关闭吸球；拒绝旧carry参数。

执行链仍是State→Task→Skill.RushTo/kickStatus，不绕过SDK直接遥控仿真。
"""
from strategy_contract import uses_verified_control, uses_range_gate
import math
import CppPackage as C
from RoleMatch_LuaStyle.Skills import Skill
from Vision import Player
from WorldModel import kickStatus
from match_tactics import Point, distance
from ball_control import ActionBudget


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
                 shooting=True, control=None, authorize_kick=None, validate_plan=None):
    """静态接触后射/传；瞄准改变时先脱离球再绕后，不做携球假动作。"""
    if carry:
        raise ValueError('No-carry ruleset forbids TargetedKick(carry=True)')
    if gate is None or control is None:
        raise ValueError('Ruleset requires precision gate and static contact planner')
    if uses_range_gate(geometry.c) and validate_plan is None:
        raise ValueError('V2.2 requires an execution-time calibrated kick plan gate')
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
        if uses_verified_control(geometry.c):
            if control.budget is None:
                control.budget = ActionBudget()
            failure = control.budget.update(now, robot, heading, ball, target, geometry.c)
            if failure:
                # 不贴球换目标；本帧先停车，下一帧由官方 AbortRelease 状态退离。
                skill = Skill.Stop()
                resources.append(skill)
                skill[0](executor)
                if report_phase is not None:
                    report_phase(failure)
                return
        dest = geometry.legal(dest)
        if uses_verified_control(geometry.c):
            dest = geometry.safe_waypoint(robot, dest)
            if dest is None:
                skill = Skill.Stop()
                resources.append(skill)
                skill[0](executor)
                if report_phase is not None:
                    report_phase('ILLEGAL_APPROACH_PATH')
                return
        point = C.CGeoPoint(dest.x,dest.y)
        flags = (0x08000000 if enable_dss else 0) | 0x200 | (0 if aligned else 0x10)
        forward = (ball.x-robot.x)*math.cos(heading)+(ball.y-robot.y)*math.sin(heading)
        lateral = abs(-(ball.x-robot.x)*math.sin(heading)+(ball.y-robot.y)*math.cos(heading))
        # 本机静态测量：原始球心距车心110mm可踢，120mm不能踢。
        # 不能套用理论94.5mm：本机模型在这个距离已发生碰撞推球。
        # SDK近车球心还有数厘米修正，所以接触判断用已验证原始球心。
        # 原生规划存在约数毫米位置死区，运动目标100mm，进入110mm即锁位置。
        # 111.6mm曾出现空发，不能把接触上限放宽代替实际碰到踢球器。
        # 110mm仅是停车带；换方向仍须先退离球，不能携球转向。
        settle_forward = min(110.,geometry.c.get('contact_forward_max',110))
        ready_geometry = (75 <= forward <= settle_forward
                          and lateral <= 10 and abs(math.atan2(math.sin(heading-angle),math.cos(heading-angle))) <= .018)
        # 出脚阶段保持位置，但继续使用可出球的运动技能。原生Stop是停车任务，
        # 不能假定它与踢球请求组合后仍会正常发出球；也不继续向球内挤压。
        if geometry.c.get('settle_on_contact',False) and aligned and ready_geometry:
            if control.contact_anchor is None:
                control.contact_anchor = robot
            anchor=C.CGeoPoint(control.contact_anchor.x,control.contact_anchor.y)
            resources.append(anchor)
            skill = Skill.RushTo(anchor,angle=angle,maxAcc=1200,flag=flags,needDribble=False)
        else:
            control.contact_anchor = None
            skill = Skill.RushTo(point,angle=angle,maxAcc=1200 if aligned else 2200,
                                 flag=flags,needDribble=False)
        resources.extend((point,skill))
        skill[0](executor)
        allowed,reason = gate.allow(now,robot,heading,ball,target,geometry.c,shooting=shooting)
        actual_power = min(power, geometry.c['power_cap'], 6000)
        if aligned and can_kick and allowed and validate_plan is not None:
            if not validate_plan(executor, actual_power, robot, heading):
                allowed, reason = False, 'SHOT_OPPORTUNITY_REJECTED'
        if (aligned and distance(robot,ball) < 150 and can_kick and allowed
                and (authorize_kick is None or authorize_kick(executor))):
            # 力度不是实测球速；这里再限幅，物理6.5m/s还必须独立验证。
            kickStatus.setKick(executor,actual_power)
            gate.fired(now,geometry.c.get('kick_pulse_seconds',.18))
            phase = 'KICK'
            if on_kick is not None:
                on_kick(executor,ball)
        elif aligned and can_kick:
            phase = 'WAIT_'+reason
        if report_phase is not None:
            report_phase(phase)
    return execute,lambda executor:match_point
