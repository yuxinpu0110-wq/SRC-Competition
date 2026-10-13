"""V2 无带球传球候选评估。纯 Python，只产生建议，不发送动作。"""
from dataclasses import dataclass
import math


def distance(a, b):
    return math.hypot(a.x-b.x, a.y-b.y)


def clearance(a, b, obstacles):
    dx, dy = b.x-a.x, b.y-a.y
    length2 = max(1.0, dx*dx+dy*dy)
    result = 3000.0
    for obstacle in obstacles:
        t = ((obstacle.x-a.x)*dx+(obstacle.y-a.y)*dy)/length2
        if .02 < t < 1.05:
            t = min(1.0, t)
            closest_x, closest_y = a.x+t*dx, a.y+t*dy
            result = min(result, math.hypot(obstacle.x-closest_x, obstacle.y-closest_y)-130)
    return result


@dataclass(frozen=True)
class PassPlan:
    target: object
    flight_seconds: float
    receiver_seconds: float
    lane_clearance: float
    score: float


def reachable_time(start, target, max_speed=1800.0, acceleration=1800.0):
    """从静止出发的保守一维时间界；路径长度由调用方给定。"""
    d = distance(start, target)
    ramp = max_speed*max_speed/acceleration
    if d <= ramp:
        return 2*math.sqrt(d/acceleration)
    return 2*max_speed/acceleration+(d-ramp)/max_speed


def rolling_time(length, initial_speed=3000.0, deceleration=350.0):
    """匀减速滚动模型；球在目标前停下时返回 None。参数必须以后由仿真标定。"""
    disc = initial_speed*initial_speed-2*deceleration*length
    if disc < 0:
        return None
    return (initial_speed-math.sqrt(disc))/deceleration


def evaluate_pass(ball, receiver, target, opponents, *, initial_speed=3000.0, wait_for_receiver=False):
    length = distance(ball, target)
    flight = rolling_time(length, initial_speed)
    if flight is None or not 700 <= length <= 3200:
        return None
    lane = clearance(ball, target, opponents)
    if lane < 200:
        return None
    arrival = reachable_time(receiver, target)+.12  # 转向/停稳余量，后续用实测修正。
    # 我们的PassSetup会先等队友就位；不能用“立刻出球时赶不上”否决所有前场
    # 接应点，否则队友永远留在球后，永远没有机会就位。动态传球才用飞行期限。
    deadline = 3.0 if wait_for_receiver else flight+.35
    if arrival > deadline:
        return None
    score = lane*.55-distance(receiver, target)*.08-length*.025
    return PassPlan(target, flight, arrival, lane, score)

