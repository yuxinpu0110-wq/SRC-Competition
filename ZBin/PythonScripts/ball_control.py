"""不带球的静态接触计划：先离球、空车绕后，最后短距离贴球出球。

没有球前推进点，不开吸球，不携球转向。held只表示车头近球的几何观察，
不表示占有球、更不许可带球。旧带球版本保留在Git标签before-rules-v4。
"""
from dataclasses import dataclass
import math
from match_tactics import Point, distance


def wrap(angle):
    return math.atan2(math.sin(angle),math.cos(angle))


@dataclass
class BallControl:
    held: bool = False
    direction: float | None = None
    updated: float | None = None

    def reset(self):
        self.held,self.direction,self.updated = False,None,None

    def plan(self, now, robot, heading, ball, target, *, carry=False, contact=85, trim=False):
        if carry:
            raise ValueError('No-carry ruleset: carrying is disabled, including compatibility calls')
        desired = math.atan2(target.y-ball.y,target.x-ball.x)
        dx,dy = ball.x-robot.x,ball.y-robot.y
        along = dx*math.cos(desired)+dy*math.sin(desired)
        lateral = -dx*math.sin(desired)+dy*math.cos(desired)
        error = abs(wrap(heading-desired))
        self.held = 50 < along < 165 and abs(lateral) < 30 and error < .12
        aligned = along > 0 and abs(lateral) < 20 and error < .06
        self.direction,self.updated = desired,now
        radial = math.atan2(robot.y-ball.y,robot.x-ball.x)
        radial_error = wrap(desired+math.pi-radial)
        if not aligned:
            # 大角度变向先径向退到320mm，再走避球圆；不能贴球绕转。
            if distance(robot,ball) < 260:
                destination = Point(ball.x+320*math.cos(radial),ball.y+320*math.sin(radial))
            elif abs(radial_error) > .12:
                waypoint = radial+max(-.45,min(.45,radial_error))
                destination = Point(ball.x+320*math.cos(waypoint),ball.y+320*math.sin(waypoint))
            else:
                destination = Point(ball.x-320*math.cos(desired),ball.y-320*math.sin(desired))
            return destination,desired,False,self.held,'RELEASE_ALIGN'
        destination = Point(ball.x-contact*math.cos(desired),ball.y-contact*math.sin(desired))
        if trim and abs(lateral) > 6:
            correction = max(-20,min(20,lateral))
            destination = Point(destination.x-correction*math.sin(desired),
                                destination.y+correction*math.cos(desired))
        return destination,desired,True,self.held,'CONTACT'
