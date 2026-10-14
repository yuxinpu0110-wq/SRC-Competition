"""Execution-time range gate. Geometry uses the complete ball crossing line.

Dynamic goalkeeper evaluation is a separate unfinished stage; REACHABLE is
not a positive goalkeeper window or a goal probability.
"""
from dataclasses import dataclass
import math
from ball_travel import number
from match_tactics import Point, distance, finite


@dataclass(frozen=True)
class KickPlan:
    robot: int
    purpose: str
    origin: Point
    target: Point
    power: float
    reset_epoch: int
    created_at: float
    model_id: str | None


def evaluate_kick(plan, ball, heading, actual_power, epoch, now, model, config, *, contact=None):
    reject = model.reject
    if epoch != plan.reset_epoch or not number(now) or not 0 <= now-plan.created_at <= .2:
        return reject('PLAN_EXPIRED')
    if not finite(ball) or not finite(plan.target) or not math.isfinite(heading):
        return reject('INVALID_SHOT_GEOMETRY')
    if distance(ball, plan.origin) > 10 or plan.model_id != model.data.get('model_id'):
        return reject('PLAN_MODEL_OR_BALL_CHANGED')
    if (not number(actual_power) or actual_power != plan.power
            or not 1000 <= actual_power <= min(config['power_cap'], 6000)):
        return reject('PLAN_POWER_MISMATCH')
    dx, dy = plan.target.x-ball.x, plan.target.y-ball.y
    if math.hypot(dx, dy) < 1:
        return reject('INVALID_SHOT_GEOMETRY')
    if plan.purpose != 'shot':
        # Ordinary V2.1 passes lack calibrated receiver poses/terminal legs.
        return reject('ADVANCE_PLAN_NOT_VALIDATED')
    goal_x = config['field_length']/2
    radius = config.get('ball_radius_mm', 21.5)
    if dx <= 0 or ball.x >= goal_x or abs(plan.target.x-goal_x) > 1:
        return reject('AWAY_FROM_GOAL')
    desired = math.atan2(dy, dx)
    error = abs(math.atan2(math.sin(heading-desired), math.cos(heading-desired)))
    if error > .018:
        return reject('SHOT_HEADING_CHANGED')
    full_length = (goal_x+radius-ball.x)/math.cos(desired)
    travel = model.evaluate(plan.robot, actual_power, full_length, 'static', contact=contact)
    if not travel.reachable:
        return travel
    aperture = config['goal_width']/2-radius
    conservative_length = full_length
    for angle in (heading-travel.angle_error_high_rad, heading+travel.angle_error_high_rad):
        if math.cos(angle) <= .05:
            return reject('GOAL_APERTURE_UNSAFE')
        y = ball.y+(goal_x+radius-ball.x)*math.tan(angle)
        if abs(y) >= aperture:
            return reject('GOAL_APERTURE_UNSAFE')
        conservative_length = max(conservative_length, (goal_x+radius-ball.x)/math.cos(angle))
    if conservative_length > full_length:
        travel = model.evaluate(plan.robot, actual_power, conservative_length, 'static', contact=contact)
        if not travel.reachable:
            return travel
    if travel.arrival_speed_low_mm_s < travel.min_goal_arrival_speed_mm_s:
        return reject('ARRIVAL_TOO_SLOW')
    return travel
