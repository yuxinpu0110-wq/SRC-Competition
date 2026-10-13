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

