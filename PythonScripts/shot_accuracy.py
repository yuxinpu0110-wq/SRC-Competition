"""精确出球的纯 Python 安全门：先站稳、贴正球，再允许发球。

这不是物理球路预测器；heading 延长线只用于拒绝明显会偏出门框的请求。
每辆车持有一个 ShotGate，不能每帧重新创建，否则无法积累稳定时间。
"""
import math
from match_tactics import distance


def angle_difference(a, b):
    return math.atan2(math.sin(a-b), math.cos(a-b))


class ShotGate:
    def __init__(self):
        self.previous = None
        self.speed = self.spin = self.ball_speed = math.inf
        self.stable_since = None
        self.previous_target = None
        self.last_kick = -math.inf
        self.pulse_until = -math.inf
        self.diagnostics = {'reason': 'NO_SAMPLE'}
        self.capture_id = None
        self.new_observation = False
        self.capture_time = None

    def reset(self):
        self.__init__()

    def observe(self, now, robot, heading, ball, *, frame_id=None):
        """由相邻视觉帧估算速度；断帧后重新站稳，不沿用之前的稳定计时。"""
        if not all(math.isfinite(v) for v in (now, robot.x, robot.y, heading, ball.x, ball.y)):
            self.previous = None
            self.speed = self.spin = self.ball_speed = math.inf
            self.stable_since = None
            return
        self.new_observation = False
        if frame_id is not None and frame_id == self.capture_id:
            return
        old = self.previous
        # Windows低分辨率时钟/重复回调可能得到相同时间；不是视觉断帧。
        # 不覆盖上一个有效样本，也不把已经积累的稳定时间清零。
        if old is not None and 0 <= now-old[0] < .001:
            return
        self.capture_id, self.capture_time, self.new_observation = frame_id, now, True
        self.previous = (now, robot, heading, ball)
        if old is None or not 0.001 <= now-old[0] <= .25:
            self.speed = self.spin = self.ball_speed = math.inf
            self.stable_since = None
            return
        dt = now-old[0]
        self.speed = distance(robot, old[1])/dt
        self.spin = abs(angle_difference(heading, old[2]))/dt
        self.ball_speed = distance(ball, old[3])/dt

    def allow(self, now, robot, heading, ball, target, config, *, shooting=True):
        """返回 (是否允许踢, 原因)。阈值是待仿真验证的初始值，不是成功率保证。"""
        if not all(math.isfinite(v) for v in (now, robot.x, robot.y, heading, ball.x, ball.y, target.x, target.y)):
            self.stable_since = None
            self.diagnostics = {'reason': 'INVALID_OBSERVATION'}
            return False, 'INVALID_OBSERVATION'
        desired = math.atan2(target.y-ball.y, target.x-ball.x)
        error = abs(angle_difference(heading, desired))
        # 在车头方向上计算球的位置：真正出球方向跟车头有关，不只跟目标点有关。
        dx, dy = ball.x-robot.x, ball.y-robot.y
        forward = dx*math.cos(heading)+dy*math.sin(heading)
        lateral = abs(-dx*math.sin(heading)+dy*math.cos(heading))
        predicted_y = None
        reason = 'READY'
        if not 75 <= forward <= config.get('contact_forward_max',130) or lateral > 10:
            reason = 'CONTACT'
        elif error > .018:
            reason = 'AIM'
        elif self.speed > 220 or self.spin > .18 or self.ball_speed > 350:
            reason = 'MOVING'
        if shooting:
            goal_x = config['field_length']/2
            if math.cos(heading) <= .05 or ball.x >= goal_x:
                reason = 'AWAY_FROM_GOAL'
            else:
                predicted_y = ball.y+(goal_x-ball.x)*math.tan(heading)
                # 留出球半径及误差余量，拒绝朝门柱外侧打的指令。
                if abs(predicted_y) > config['goal_width']/2-85:
                    reason = 'OUTSIDE_GOAL'
        if self.previous_target is not None and distance(target, self.previous_target) > 40:
            self.stable_since = None
        self.previous_target = target
        evidence_time = self.capture_time if self.capture_time is not None else now
        if reason != 'READY':
            self.stable_since = None
        elif not self.new_observation:
            reason = 'NO_NEW_CAPTURE'
        else:
            if self.stable_since is None:
                self.stable_since = evidence_time
            if evidence_time-self.stable_since < .08:
                reason = 'SETTLE'
            elif now > self.pulse_until and now-self.last_kick < .3:
                reason = 'COOLDOWN'
        # 不把 inf 写到 JSON 里，缺少速度样本时用 None 表示。
        self.diagnostics = dict(reason=reason, heading_error_rad=error, lateral_mm=lateral,
            forward_mm=forward, predicted_goal_y_mm=predicted_y,
            speed_mm_s=self.speed if math.isfinite(self.speed) else None,
            spin_rad_s=self.spin if math.isfinite(self.spin) else None,
            ball_speed_mm_s=self.ball_speed if math.isfinite(self.ball_speed) else None)
        return reason == 'READY', reason

    def fired(self, now):
        # 官方技能按帧请求出球；一次请求并不等于踢球器已经碰到球。
        # 几何/速度仍逐帧检查，在180ms窗口内重发，避免只发一帧后空等300ms。
        # 球离开、车头偏移或裁判停止都会由门控/状态机立即取消后续请求。
        if now > self.pulse_until:
            self.last_kick = now
            self.pulse_until = now+.18
