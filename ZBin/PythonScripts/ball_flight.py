"""Read-only raw flight audit. Capture seconds/mm; never grants control rights.

Continuous evidence is conservative: ambiguous detections, gaps and camera
handoffs terminate the audit as UNKNOWN. Goal crossings are proxy observations.
"""
from collections import deque
from dataclasses import asdict, dataclass
import math

from match_tactics import Point, distance, finite
from observation import Capture


@dataclass(frozen=True)
class FlightIdentity:
    run_id: str
    reset_epoch: int
    plan_id: str
    leg_id: str
    transaction_id: int


class BallFlightObserver:
    def __init__(self, config):
        self.c = config
        self.active = None
        self.events = []
        self.seen = deque(maxlen=256)
        self.emitted = deque(maxlen=256)
        # An UNKNOWN/finished identity is terminal. Subsequent pulses with that
        # identity cannot create a fresh observation window or shorten delay.
        self.closed = set()

    def emit(self, kind, now, **details):
        identity = self.active['identity'] if self.active else None
        key = (identity, kind)
        if kind != 'FLIGHT_SAMPLE' and key in self.emitted:
            return
        if kind != 'FLIGHT_SAMPLE':
            self.emitted.append(key)
        self.events.append(dict(kind=kind, now=now,
                                **(asdict(identity) if identity else {}),
                                source='independent_raw_flight_audit',
                                control_authority=False, **details))

    def arm(self, identity, now, capture_time, camera, robot, origin, target, players):
        if identity in self.closed:
            return
        if self.active and self.active['identity'] == identity:
            return  # Repeated SDK pulses cannot reset the trajectory or deadline.
        if self.active:
            self.invalidate(now, 'SUPERSEDED_REQUEST')
        self.active = dict(identity=identity, now=now, capture_time=capture_time,
                           camera=camera, robot=robot, origin=origin, target=target,
                           players=dict(players), previous=None, confirmations=0,
                           departure_since=None, departed=False)
        if (capture_time is None or camera is None or robot not in players
                or not finite(origin) or not finite(target)):
            self.invalidate(now, 'NO_REQUEST_CAPTURE')
        elif any(n != robot and distance(p, origin) < 220 for n, p in players.items()):
            self.invalidate(now, 'POSSIBLE_OTHER_TOUCHER')
        else:
            sign = -1 if self.c.get('side') == 'right' else 1
            self.active['previous'] = Capture(camera, -1, capture_time, now,
                                              Point(sign*origin.x, sign*origin.y), {})

    def invalidate(self, now, reason):
        if self.active:
            self.emit('UNKNOWN', now, reason=reason, evidence_grade='unknown')
            self.closed.add(self.active['identity'])
        self.active = None

    def consume(self, captures, now, epoch, *, halted=False):
        tx = self.active
        if not tx:
            return
        if halted or epoch != tx['identity'].reset_epoch:
            self.invalidate(now, 'REFEREE_RESET_OR_EPOCH_CHANGED')
            return
        if now-tx['now'] > self.c.get('flight_observe_seconds', 1.5):
            self.invalidate(now, 'OBSERVATION_ENDED_UNCLASSIFIED')
            return
        sign = -1 if self.c.get('side') == 'right' else 1
        ordered = sorted(captures, key=lambda c: (c.capture_time, c.camera, c.frame_id))
        own_camera = [c for c in ordered if c.camera == tx['camera']]
        for capture in own_camera:
            key = (capture.camera, capture.frame_id, capture.capture_time)
            if (key in self.seen or capture.capture_time <= tx['capture_time']
                    or capture.receive_time < tx['now']):
                continue
            self.seen.append(key)
            if not 0 <= now-capture.receive_time <= self.c.get('vision_max_age_s', .2):
                continue
            if capture.ball is None or not finite(capture.ball):
                self.invalidate(now, 'MISSING_OR_AMBIGUOUS_RAW_BALL')
                return
            ball = Point(sign*capture.ball.x, sign*capture.ball.y)
            old = tx['previous']
            if old and capture.capture_time <= old.capture_time:
                continue
            if capture.capture_time-tx['capture_time'] > .2 and not old:
                self.invalidate(now, 'NO_CONTINUOUS_REQUEST_TRACK')
                return
            if old:
                dt = capture.capture_time-old.capture_time
                if not .001 <= dt <= .1:
                    self.invalidate(now, 'RAW_TRACK_GAP')
                    return
                old_ball = Point(sign*old.ball.x, sign*old.ball.y)
                speed = distance(ball, old_ball)/dt
                if speed > 6500:
                    self.emit('SPEED_LIMIT_SUSPECT', now, speed_mm_s=speed,
                              frames=[old.frame_id, capture.frame_id],
                              capture_interval=[old.capture_time, capture.capture_time],
                              positions=[[old_ball.x, old_ball.y], [ball.x, ball.y]],
                              evidence_grade='suspect_not_adjudicated')
                    self.invalidate(now, 'SPEED_LIMIT_OR_RESET_SUSPECT')
                    return
                dx, dy = tx['target'].x-tx['origin'].x, tx['target'].y-tx['origin'].y
                length = max(1., math.hypot(dx, dy))
                forward = ((ball.x-tx['origin'].x)*dx+(ball.y-tx['origin'].y)*dy)/length
                lateral = abs((ball.x-tx['origin'].x)*dy-(ball.y-tx['origin'].y)*dx)/length
                forward_speed = ((ball.x-old_ball.x)*dx+(ball.y-old_ball.y)*dy)/(length*dt)
                if lateral > max(50, forward*.25) or forward_speed < -100:
                    self.invalidate(now, 'TRAJECTORY_TURN_OR_OTHER_TOUCH')
                    return
                candidates = {key: Point(sign*p.x, sign*p.y) for key, p in capture.robots.items()}
                shooter_key = (self.c.get('team', 'blue'), tx['robot'])
                if any(key != shooter_key and distance(p, ball) < 125 for key, p in candidates.items()):
                    self.invalidate(now, 'POSSIBLE_OTHER_TOUCHER')
                    return
                shooter = candidates.get(shooter_key, tx['players'][tx['robot']])
                valid = (forward >= 70 and forward_speed > 500
                         and distance(ball, shooter) > 180)
                tx['confirmations'] = tx['confirmations']+1 if valid else 0
                if valid and tx['departure_since'] is None:
                    tx['departure_since'] = capture.capture_time
                elif not valid:
                    tx['departure_since'] = None
                if (tx['confirmations'] >= 2 and not tx['departed']
                        and capture.capture_time-tx['departure_since'] >= .025):
                    tx['departed'] = True
                    self.emit('BALL_DEPARTED', now, speed_mm_s=speed,
                              capture_interval=[old.capture_time, capture.capture_time],
                              frame_id=[capture.camera, capture.frame_id],
                              evidence_grade='continuous_raw_directional_proxy',
                              delay_s=now-tx['now'], delay_semantics='confirmation_delay_not_release_delay')
                goal = self.c['field_length']/2
                radius = self.c.get('ball_radius_mm', 21.5)
                line = goal+radius
                if tx['departed'] and old_ball.x < line <= ball.x:
                    y = old_ball.y+(ball.y-old_ball.y)*(line-old_ball.x)/(ball.x-old_ball.x)
                    if abs(y) < self.c['goal_width']/2-radius:
                        self.emit('GOAL_LINE_CROSSED', now, goal_y_mm=y,
                                  evidence_grade='raw_goal_proxy', rule_compliance_verified=False)
            tx['previous'] = capture
            self.emit('FLIGHT_SAMPLE', now, camera=capture.camera, frame_id=capture.frame_id,
                      capture_time=capture.capture_time, receive_time=capture.receive_time,
                      ball=[ball.x, ball.y], coordinate_frame='attack_positive_x')
        # Camera switching is explicitly unknown until overlap handoffs are validated.
        previous = tx['previous']
        if previous and now-previous.receive_time > .1:
            self.invalidate(now, 'RAW_TRACK_GAP_OR_CAMERA_HANDOFF')

    def diagnostics(self):
        return dict(active=asdict(self.active['identity']) if self.active else None,
                    departed=bool(self.active and self.active['departed']), control_authority=False)
