"""V2 门将短时运动模型。评分是启发式，不是进球概率。"""
from dataclasses import dataclass
import math
from match_tactics import Point, distance, finite


@dataclass
class KeeperPrediction:
    position: Point
    uncertainty: float


class KeeperModel:
    def __init__(self):
        self.sample = None
        self.velocity = Point(0, 0)

    def observe(self, now, position):
        if position is None or not finite(position):
            self.sample = None
            self.velocity = Point(0, 0)
            return
        if self.sample is not None:
            old_time, old = self.sample
            dt = now-old_time
            if .015 <= dt <= .4:
                velocity = Point((position.x-old.x)/dt, (position.y-old.y)/dt)
                speed = math.hypot(velocity.x, velocity.y)
                if speed <= 3000:
                    self.velocity = Point(.55*self.velocity.x+.45*velocity.x,
                                          .55*self.velocity.y+.45*velocity.y)
                else:
                    self.velocity = Point(0, 0)  # 摆位/换身份不能成为“高速门将”。
            elif dt > .4:
                self.velocity = Point(0, 0)
        self.sample = (now, position)

    def predict(self, seconds, field):
        if self.sample is None:
            return None
        seconds = max(0.0, min(.8, seconds))
        p = self.sample[1]
        y_limit = field['goal_width']/2+350
        predicted = Point(p.x+min(seconds,.25)*self.velocity.x,
                          max(-y_limit, min(y_limit, p.y+min(seconds,.25)*self.velocity.y)))
        return KeeperPrediction(predicted, 100+350*seconds)

    def shot_clearance(self, ball, target, ball_speed, field):
        if self.sample is None or ball_speed <= 0:
            return None
        flight = distance(ball,target)/ball_speed
        prediction = self.predict(flight, field)
        return distance(target,prediction.position)-prediction.uncertainty-180
