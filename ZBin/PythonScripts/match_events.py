"""V2 比赛事件证据：请求出脚与球真正离开必须分开记录。"""
from dataclasses import dataclass
from match_tactics import Point, distance, finite


@dataclass
class KickAttempt:
    sequence: int
    robot: int
    origin: Point
    target: Point
    requested_at: float
    passing: bool
    departed_at: float | None = None


class MatchEvents:
    def __init__(self):
        self.sequence = 0
        self.pending = []
        self.events = []

    def request_kick(self, now, robot, origin, target, passing):
        self.sequence += 1
        attempt = KickAttempt(self.sequence, robot, origin, target, now, passing)
        self.pending.append(attempt)
        self.events.append(dict(kind='kick_requested', sequence=attempt.sequence,
                                now=now, robot=robot, passing=passing))
        return attempt.sequence

    def observe(self, now, ball, fresh=True):
        """只在新视觉帧上认定离球；使用沿目标方向位移而不是单纯距离。"""
        if not fresh or not finite(ball):
            return []
        emitted = []
        for attempt in list(self.pending):
            dx, dy = attempt.target.x-attempt.origin.x, attempt.target.y-attempt.origin.y
            length = max(1.0, (dx*dx+dy*dy)**.5)
            forward = ((ball.x-attempt.origin.x)*dx+(ball.y-attempt.origin.y)*dy)/length
            if forward > 180 and distance(ball, attempt.origin) > 200:
                attempt.departed_at = now
                event = dict(kind='ball_departed', sequence=attempt.sequence, now=now,
                             robot=attempt.robot, passing=attempt.passing,
                             forward_mm=forward)
                self.events.append(event)
                emitted.append(event)
                self.pending.remove(attempt)
            elif now-attempt.requested_at > 1.0:
                self.events.append(dict(kind='kick_not_observed', sequence=attempt.sequence,
                                        now=now, robot=attempt.robot, passing=attempt.passing))
                self.pending.remove(attempt)
        self.events[:] = self.events[-128:]
        return emitted

    def departed(self, sequence):
        return any(e['kind'] == 'ball_departed' and e['sequence'] == sequence for e in self.events)



"""一次事务至多确认一次离脚；脉冲次数与物理事件分开计数。"""
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import uuid
from match_tactics import Point, distance


@dataclass
class KickTransaction:
    id: int
    robot: int
    purpose: str
    start_ball: Point
    target: Point
    request_time: float
    pulse_end: float
    deadline: float
    config_hash: str
    start_gap: float
    initial_speed: float
    run_id: str = ''
    reset_epoch: int = 0
    plan_id: str = ''
    leg_id: str = '0'
    state: str = 'REQUESTED'
    pulse_count: int = 0
    departed_at: float | None = None
    departure_samples: int = 0
    departure_since: float | None = None


class KickTransactions:
    def __init__(self, config, run_id=None):
        self.c = config
        self.run_id = run_id or uuid.uuid4().hex
        self.reset_epoch = 0
        self.config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
        self.active = None
        self.sequence = 0
        self.events = []
        self.previous = None
        self.failed_origin = None
        self.failures = 0
        self.failed_at = None
        self.failed_robot = None
        self.recovery_episodes = 0

    def emit(self, kind, now, **extra):
        tx = self.active
        self.events.append(dict(kind=kind, now=now, transaction_id=tx.id if tx else None,
                                robot=tx.robot if tx else None, purpose=tx.purpose if tx else None,
                                run_id=tx.run_id if tx else self.run_id,
                                reset_epoch=tx.reset_epoch if tx else self.reset_epoch,
                                plan_id=tx.plan_id if tx else None, leg_id=tx.leg_id if tx else '0',
                                **extra))

    def request(self, now, robot, purpose, ball, target, robot_pos, initial_speed=0, *, plan_id=None,leg_id=None):
        tx = self.active
        if tx is None:
            if self.failed_origin is not None and distance(ball, self.failed_origin) > 300:
                self.failed_origin, self.failures = None, 0
                self.failed_at,self.failed_robot,self.recovery_episodes = None,None,0
            if self.failures >= self.c.get('kick_max_attempts', 2):
                return None
            self.sequence += 1
            tx = self.active = KickTransaction(self.sequence, robot, purpose, ball, target, now,
                now+self.c.get('kick_pulse_seconds', .18),
                now+self.c.get('kick_departure_timeout', .45), self.config_hash,
                distance(ball, robot_pos), initial_speed, run_id=self.run_id,
                reset_epoch=self.reset_epoch, plan_id=f'kick-{self.sequence}')
            self.previous = None
            if plan_id is not None:tx.plan_id=plan_id
            if leg_id is not None:tx.leg_id=leg_id
            self.emit('KICK_REQUESTED', now, power_units='sdk_input', start_ball=asdict(ball),
                      target=asdict(target), config_hash=tx.config_hash)
        if tx.state != 'REQUESTED' or tx.robot != robot or tx.purpose != purpose or distance(target, tx.target) > 40:
            return None
        if now > tx.pulse_end or now > tx.deadline:
            return None
        tx.pulse_count += 1
        return tx.id

    def observe(self, now, capture_time, frame_id, ball, players, *, fresh=True, new=True, reset=False):
        tx = self.active
        if tx is None:
            return
        if reset or not fresh or tx.robot not in players:
            self.cancel(now, 'RESET' if reset else 'VISION_OR_ROBOT_LOST')
            return
        if tx.state == 'REQUESTED' and now > tx.deadline:
            tx.state = 'NO_DEPARTURE'
            self.failed_origin, self.failures = tx.start_ball, self.failures+1
            self.failed_at,self.failed_robot = now,tx.robot
            self.emit('KICK_NO_DEPARTURE', now, pulse_count=tx.pulse_count)
            return
        if not new or tx.state not in ('REQUESTED', 'IN_FLIGHT'):
            return
        old = self.previous
        if old and frame_id == old[0]:
            return
        self.previous = (frame_id, capture_time, ball)
        if not old or not .001 <= capture_time-old[1] <= .2:
            tx.departure_samples, tx.departure_since = 0, None
            return
        dt = capture_time-old[1]
        step = distance(ball, old[2])
        speed = step/dt
        # 快于规则上限的跳变不能用作可信离脚证据；场景摆位也不能补确认。
        if speed > 6500 or step > 450:
            if speed > 6500:
                self.emit('SPEED_LIMIT_SUSPECT', now, speed_mm_s=speed,
                          capture_interval=[old[1], capture_time],
                          evidence='raw_associated_suspect_not_adjudicated')
            self.cancel(now, 'RESET_OR_SPEED_LIMIT')
            return
        if tx.state == 'IN_FLIGHT':
            if now-tx.departed_at > self.c.get('flight_observe_seconds', 1.5):
                tx.state = 'FINISHED'
                self.emit('FLIGHT_OBSERVATION_ENDED', now, outcome='UNCLASSIFIED')
            return
        dx, dy = tx.target.x-tx.start_ball.x, tx.target.y-tx.start_ball.y
        length = max(1, math.hypot(dx, dy))
        forward = ((ball.x-tx.start_ball.x)*dx+(ball.y-tx.start_ball.y)*dy)/length
        lateral = abs((ball.x-tx.start_ball.x)*dy-(ball.y-tx.start_ball.y)*dx)/length
        forward_speed = ((ball.x-old[2].x)*dx+(ball.y-old[2].y)*dy)/(length*dt)
        gap = distance(ball, players[tx.robot])
        valid = (forward >= self.c.get('departure_min_distance_mm', 70)
                 and gap > max(180, tx.start_gap+80) and lateral < max(40, forward*.25)
                 and forward_speed > self.c.get('departure_min_speed_mm_s', 500)
                 and speed > tx.initial_speed+200)
        if valid:
            tx.departure_samples += 1
            if tx.departure_since is None:
                tx.departure_since = capture_time
            if tx.departure_samples >= 2 and capture_time-tx.departure_since >= .025:
                tx.state, tx.departed_at = 'IN_FLIGHT', now
                self.failures, self.failed_origin = 0, None
                self.failed_at,self.failed_robot,self.recovery_episodes = None,None,0
                self.emit('BALL_DEPARTED', now, speed_mm_s=speed, pulse_count=tx.pulse_count,
                          delay_s=now-tx.request_time, evidence='continuous_raw_associated_geometry')
        else:
            tx.departure_samples, tx.departure_since = 0, None

    def cancel(self, now, reason):
        if self.active is not None:
            self.emit('KICK_CANCELLED', now, reason=reason)
        self.active, self.previous = None, None

    def reset(self, now, reason):
        self.cancel(now, reason)
        self.failures, self.failed_origin = 0, None
        self.failed_at,self.failed_robot,self.recovery_episodes = None,None,0

    def rearm_after_release(self, now, ball, players):
        """两次空发后先完成离球和冷却，再给有限的新接近机会，不静默永久Hold。"""
        if (self.active is not None or self.failed_at is None or now-self.failed_at < 2
                or self.recovery_episodes >= 2 or self.failed_robot not in players
                or distance(players[self.failed_robot],ball) < 280):
            return False
        self.recovery_episodes += 1
        self.failures,self.failed_origin = 0,None
        self.failed_at = None
        self.emit('KICK_REARMED_AFTER_RELEASE',now,episode=self.recovery_episodes)
        return True

    def finish(self):
        self.active, self.previous = None, None

    def diagnostics(self):
        return dict(active=asdict(self.active) if self.active else None,
                    no_departure_attempts=self.failures,
                    recovery_episodes=self.recovery_episodes,
                    retry_exhausted=self.failures >= self.c.get('kick_max_attempts', 2))
