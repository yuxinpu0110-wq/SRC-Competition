"""跨帧控球记忆。单位 mm / rad / s；不绕过官方 Skill 发送速度。

关键：『球在车头』与『已经瞄准目标』是两件事。转向传球时不能因为
暂时没有瞄准，就突然关掉吸球、把目标跳回球后 240mm。
"""
from dataclasses import dataclass
import math
from match_tactics import Point, distance


def wrap(angle):
    return math.atan2(math.sin(angle), math.cos(angle))


@dataclass
class BallControl:
    held: bool = False
    direction: float | None = None
    updated: float | None = None

    def reset(self):
        self.held, self.direction, self.updated = False, None, None

    def plan(self, now, robot, heading, ball, target, *, carry=False, contact=95):
        desired = math.atan2(target.y-ball.y, target.x-ball.x)
        dx, dy = ball.x-robot.x, ball.y-robot.y
        forward = dx*math.cos(heading)+dy*math.sin(heading)
        side = -dx*math.sin(heading)+dy*math.cos(heading)
        gap = None if self.updated is None else now-self.updated
        if gap is None or not 0 <= gap <= .25:
            self.reset()
        # 进入条件严格、退出条件宽松（滞回），防止视觉噪声造成反复绕球。
        if self.held:
            self.held = 30 < forward < 210 and abs(side) < 80
        elif 65 < forward < 155 and abs(side) < 30:
            self.held = True
        angle = desired
        if self.held:
            # 带球时限转向速率，不能瞬间掉头；首次从实际车头开始。
            previous = heading if self.direction is None else self.direction
            step = 1.0*min(.05, max(0, gap or 0))
            # 指令不能远远超前于实际车头，否则底层转速仍会过大。
            previous = heading+max(-.15, min(.15, wrap(previous-heading)))
            angle = previous+max(-step, min(step, wrap(desired-previous)))
        self.direction, self.updated = angle, now
        ux, uy = math.cos(angle), math.sin(angle)
        aligned = abs(wrap(heading-desired)) < .08 and forward > 0 and abs(side) < 35
        if carry and self.held:
            # 使用长前视点，不再每帧给一个 160mm 的短终点让规划器刹停。
            destination = Point(robot.x+650*ux, robot.y+650*uy)
            phase = 'CARRY'
        else:
            offset = contact if self.held or aligned else 240
            destination = Point(ball.x-offset*math.cos(desired), ball.y-offset*math.sin(desired))
            # 已贴球但需大角度变向：沿球后接触圆分小步绕行，不穿过球。
            if self.held and abs(wrap(heading-desired)) > .25:
                radial = math.atan2(robot.y-ball.y, robot.x-ball.x)
                turn = wrap(desired+math.pi-radial)
                radial += max(-.3, min(.3, turn))
                destination = Point(ball.x+contact*math.cos(radial), ball.y+contact*math.sin(radial))
            phase = 'CONTACT' if self.held or aligned else 'ALIGN'
        return destination, angle, aligned, self.held, phase
