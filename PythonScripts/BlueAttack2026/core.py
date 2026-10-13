"""蓝队两车的逐帧决策，完全独立于 CppPackage。

规则的两个记忆不能由状态切换清零：定位球首踢者锁要等另一车确认触球；带球
预算要等球和持球车明确分离。红外闪断、换主攻、旋转或沿圈走均不重置预算。
本层按视觉采样累计路径，并预留制动距离；采样缺口时主动结束控球。
"""
from dataclasses import dataclass
from math import atan2, cos, hypot, isfinite, pi, sin

from .settings import Settings


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def distance(self, other):
        return hypot(self.x - other.x, self.y - other.y)


def angle(a, b):
    return atan2(b.y - a.y, b.x - a.x)


def angle_error(a, b):
    return (a - b + pi) % (2 * pi) - pi


def offset(p, distance, direction):
    return Point(p.x + distance * cos(direction), p.y + distance * sin(direction))


def segment_clearance(p, start, end):
    dx, dy = end.x - start.x, end.y - start.y
    length2 = dx * dx + dy * dy
    t = 0 if length2 == 0 else max(0, min(1, ((p.x - start.x) * dx + (p.y - start.y) * dy) / length2))
    return p.distance(Point(start.x + t * dx, start.y + t * dy))


@dataclass(frozen=True)
class Field:
    length: float = 9000.0
    width: float = 6000.0
    goal_width: float = 1000.0
    penalty_depth: float = 1000.0
    penalty_width: float = 2000.0
    robot_radius: float = 90.0

    def forbidden(self, p, margin):
        return (abs(p.x) >= self.length / 2 - self.penalty_depth - margin
                and abs(p.y) <= self.penalty_width / 2 + margin)

    def safe(self, p, settings):
        x = max(-self.length / 2 + settings.field_margin,
                min(p.x, self.length / 2 - settings.field_margin))
        y = max(-self.width / 2 + settings.field_margin,
                min(p.y, self.width / 2 - settings.field_margin))
        pad = self.robot_radius + settings.penalty_extra
        if self.forbidden(Point(x, y), pad):
            x = (1 if x >= 0 else -1) * (self.length / 2 - self.penalty_depth - pad - 25)
        return Point(x, y)

    def center_is_safe(self, p, settings):
        return self.safe(p, settings).distance(p) < 0.01

    def waypoint(self, start, end, settings):
        """两个外扩禁区都是矩形，精确求线段相交，避免离散采样漏掉角点。"""
        end = self.safe(end, settings)
        pad = self.robot_radius + settings.penalty_extra
        front = self.length / 2 - self.penalty_depth - pad
        for side in (-1, 1):
            xmin, xmax = ((-self.length, -front) if side < 0 else (front, self.length))
            if _segment_intersects_rect(start, end, xmin, xmax,
                                        -self.penalty_width / 2 - pad,
                                        self.penalty_width / 2 + pad):
                return self.safe(Point(side * (front - 55), start.y), settings)
        return end


def _segment_intersects_rect(a, b, xmin, xmax, ymin, ymax):
    enter, leave = 0.0, 1.0
    for pos, delta, low, high in ((a.x, b.x - a.x, xmin, xmax), (a.y, b.y - a.y, ymin, ymax)):
        if abs(delta) < 1e-9:
            if pos < low or pos > high:
                return False
        else:
            t1, t2 = (low - pos) / delta, (high - pos) / delta
            enter, leave = max(enter, min(t1, t2)), min(leave, max(t1, t2))
            if enter > leave:
                return False
    return True


@dataclass(frozen=True)
class Robot:
    pos: Point
    direction: float = 0.0
    valid: bool = True
    infrared: bool = False


@dataclass(frozen=True)
class Frame:
    cycle: int
    ball: Point
    robots: dict[int, Robot]
    enemies: tuple[Point, ...] = ()
    ball_valid: bool = True
    game_on: bool = True
    can_kick: bool = False
    phase: str = "normal"
    ball_speed: float = 0.0
    restart_token: int = 0
    ball_velocity: Point = Point(0.0, 0.0)


@dataclass(frozen=True)
class Action:
    kind: str = "stop"
    target: Point | None = None
    direction: float = 0.0
    kick_target: Point | None = None
    pass_ball: bool = False
    avoid_ball: bool = True
    dribble: bool = False
    reason: str = "hold"


@dataclass
class _Control:
    active: bool = False
    distance: float = 0.0
    ball_path: float = 0.0
    last_ball: Point | None = None
    last_robot: Point | None = None
    last_cycle: int = -1
    separation_frames: int = 0
    contact_frames: int = 0
    kick_latched: bool = False


class Planner:
    def __init__(self, settings=None, field=None):
        self.s = settings or Settings()
        self.field = field or Field()
        if len(self.s.robot_ids) != 2 or len(set(self.s.robot_ids)) != 2:
            raise ValueError("robot_ids 必须为两个不同的编号")
        if self.s.tactic not in ("automatic", "direct", "wing", "wall_pass"):
            raise ValueError("未知进攻战术")
        if not 0 < self.s.dribble_reserve_mm < self.s.dribble_limit_mm < 1000:
            raise ValueError("带球上限须低于规则 1000 mm，且留出正的制动余量")
        if self.s.release_distance <= self.s.control_near_distance:
            raise ValueError("明确分离距离必须大于近球控球距离")
        self.striker = self.s.robot_ids[0]
        self.last_switch = -10000
        self.last_cycle = -1
        self.last_kick = -10000
        self.restart_key = None
        self.restart_origin = None
        self.restart_done = False
        self.locked_kicker = None
        self.touch_frames = 0
        self.pass_receiver = None
        self.pass_until = -1
        self.pass_target = None
        self.wall_stage = "idle"
        self.wall_passer = None
        self.wall_receiver = None
        self.wall_return_point = None
        self.progress_ball = None
        self.progress_cycle = 0
        self.control = {n: _Control() for n in self.s.robot_ids}
        self.current_tactic = "direct"
        self.status = "waiting"
        self.last_actions = {}
        self.metrics = {}
        self._update_metrics()

    @property
    def dribble_distance(self):
        return {n: c.distance for n, c in self.control.items()}

    def _update_metrics(self):
        self.metrics = {"tactic": self.current_tactic, "striker": self.striker,
                        "locked_kicker": self.locked_kicker, "restart_done": self.restart_done,
                        "wall_stage": self.wall_stage, "control_path_mm": self.dribble_distance,
                        "ball_path_mm": {n: c.ball_path for n, c in self.control.items()},
                        "status": self.status}

    def _finish(self, actions, reason=None):
        if reason:
            self.status = reason
        self.last_actions = actions
        self._update_metrics()
        return actions

    def stop_all(self, reason):
        return self._finish({n: Action(reason=reason) for n in self.s.robot_ids}, reason)

    def move(self, f, n, target, reason, avoid_ball=True, dribble=False, direction=None):
        target = self.field.waypoint(f.robots[n].pos, target, self.s)
        if direction is None:
            direction = angle(f.robots[n].pos, f.ball)
        return Action("move", target, direction, avoid_ball=avoid_ball, dribble=dribble, reason=reason)

    def _frontal(self, robot, ball):
        return abs(angle_error(angle(robot.pos, ball), robot.direction)) <= self.s.frontal_ball_tolerance

    def _contact(self, robot, ball):
        return (robot.infrared and robot.pos.distance(ball) <= self.s.kick_ball_distance
                and self._frontal(robot, ball))

    def _update_control(self, f):
        """连续球路逐段相加，并用车路作保守校验。红外灭一帧不能获得新预算。"""
        for n, c in self.control.items():
            r = f.robots[n]
            distance = r.pos.distance(f.ball)
            near = distance <= self.s.control_near_distance
            previous_dribble = bool(self.last_actions.get(n, Action()).dribble)
            apparent_control = (self._contact(r, f.ball)
                                # 某些仿真构建没有可靠红外回传；慢球已在车头的
                                # 控球几何内也提前记账。此条件只用于带球预算，
                                # 绝不会被当作解除定位球首触锁的另一车触球证据。
                                or (distance <= self.s.kick_ball_distance
                                    and self._frontal(r, f.ball) and f.ball_speed < 1400)
                                or (near and self._frontal(r, f.ball)
                                    and (previous_dribble or c.active)))
            if c.active:
                # 球离车超过 300 mm 且持续若干帧才算“明确分离”。即使此时红外
                # 卡在 True，也以视觉几何为准；瞬时闪断则保留旧预算。
                c.separation_frames = c.separation_frames + 1 if distance > self.s.release_distance else 0
                if c.separation_frames >= self.s.release_confirm_frames:
                    self.control[n] = c = _Control()
                else:
                    # 未确认分离前一段都不能丢：190~300 mm 的模糊区、甚至只
                    # 偶发两帧远球，也不能让仍在连续控球的车凭空获得新距离。
                    # 真正传球飞走后的前两帧可能高估预算，这是安全的保守代价。
                    if c.last_ball is not None:
                        ball_step = c.last_ball.distance(f.ball)
                        robot_step = c.last_robot.distance(r.pos)
                        c.ball_path += ball_step
                        c.distance += max(ball_step, robot_step)
                    # 控球期间视觉跳帧无法排除中间绕圈，主动耗尽预算并放球。
                    if c.last_cycle >= 0 and f.cycle - c.last_cycle > self.s.max_control_frame_gap:
                        c.distance = max(c.distance, self.s.dribble_limit_mm)
            if not c.active and apparent_control:
                c.active = True
                c.distance = c.ball_path = 0.0
            c.contact_frames = c.contact_frames + 1 if apparent_control and near else 0
            c.last_ball, c.last_robot, c.last_cycle = f.ball, r.pos, f.cycle

    def _record_restart(self, f):
        """裁判事实独立于视觉有效性：看不清球不能忘掉间接球首脚约束。"""
        own_restart = f.phase in ("kickoff", "direct", "indirect", "penalty")
        if own_restart and self.restart_key != (f.phase, f.restart_token):
            self.restart_key = (f.phase, f.restart_token)
            self.restart_origin = None
            self.restart_done = False
            self.locked_kicker = None
            self.touch_frames = 0
            self.pass_receiver = None
            self.pass_until = -1
            self.wall_stage = "idle"
            # 新一次裁判定位球取消旧踢球请求锁；连续带球距离仍须按分离解除。
            for c in self.control.values():
                c.kick_latched = False
        return own_restart

    def _restart(self, f):
        own_restart = self._record_restart(f)
        # 本方法只在视觉有效后调用；无效定位球帧留下的原点在此安全补全。
        if self.restart_key is not None and self.restart_origin is None:
            self.restart_origin = f.ball
        if self.locked_kicker is not None:
            other = next(n for n in self.s.robot_ids if n != self.locked_kicker)
            in_play = self.restart_origin is not None and f.ball.distance(self.restart_origin) > self.s.restart_in_play_mm
            contact = in_play and self._contact(f.robots[other], f.ball)
            self.touch_frames = self.touch_frames + 1 if contact else 0
            if self.touch_frames >= self.s.restart_contact_frames:
                self.locked_kicker = None
                self.restart_done = True
                self.touch_frames = 0
        return own_restart

    def step(self, f):
        self._record_restart(f)
        if f.cycle <= self.last_cycle:
            return self.stop_all("stale vision cycle")
        self.last_cycle = f.cycle
        coordinates = [f.ball, f.ball_velocity] + [r.pos for r in f.robots.values()] + list(f.enemies)
        if (not f.ball_valid or not all(isfinite(p.x) and isfinite(p.y) for p in coordinates)
                or not isfinite(f.ball_speed) or f.ball_speed < 0
                or any(n not in f.robots or not f.robots[n].valid or not isfinite(f.robots[n].direction)
                       for n in self.s.robot_ids)):
            return self.stop_all("invalid ball/robot vision")
        # 即使裁判暂停或状态切换，也要更新而非清除连续控球账本。
        self._update_control(f)
        own_restart = self._restart(f)
        if f.phase in ("halt", "defend", "stop"):
            return self.stop_all("referee hold")
        if own_restart:
            if not self.s.enable_restart_kicks:
                return self.stop_all("restart disabled")
            if not (f.game_on or f.can_kick):
                return self.stop_all("waiting for referee start permission")
        elif not f.game_on:
            return self.stop_all("game not started")
        if abs(f.ball.x) > self.field.length / 2 or abs(f.ball.y) > self.field.width / 2:
            return self.stop_all("ball outside field")
        if any(not self.field.center_is_safe(f.robots[n].pos, self.s) for n in self.s.robot_ids):
            return self.stop_all("robot at field/penalty safety boundary")
        if self.field.forbidden(f.ball, self.field.robot_radius + self.s.penalty_extra):
            return self.stop_all("ball too close to penalty area")
        if self.progress_ball is None or self.progress_ball.distance(f.ball) > self.s.ball_progress_mm:
            self.progress_ball, self.progress_cycle = f.ball, f.cycle
        stuck = f.cycle - self.progress_cycle > self.s.stuck_frames
        self._choose_striker(f)
        striker = self.striker
        support = next(n for n in self.s.robot_ids if n != striker)
        goal = self.goal_target(f, support)
        shot_gap = self._clearance(f, f.ball, goal, [f.robots[support].pos])
        self.current_tactic = self.choose_tactic(f, shot_gap)
        self._update_wall_stage(f)
        support_point = self.support_position(f, stuck)
        actions = {support: self.move(f, support, support_point, "support: " + self.current_tactic)}
        if support == self.locked_kicker:
            away = angle(f.ball, f.robots[support].pos)
            actions[support] = self.move(f, support, offset(f.ball, 800, away), "first kicker: avoid ball")
        if self.locked_kicker is not None and f.ball.distance(self.restart_origin) <= self.s.restart_in_play_mm:
            actions[striker] = Action(reason="restart kick requested; waiting >50 mm")
            return self._finish(actions, actions[striker].reason)
        # NORMAL_START/Play 切换可以先于首脚。已记录的定位球尚未完成时，
        # 即使本帧被适配为 normal，也继续首脚传球约束，避免间接球首脚射门。
        first_restart = (self.restart_key is not None and not self.restart_done
                         and self.locked_kicker is None)
        pass_target = f.robots[support].pos
        can_pass = (support != self.locked_kicker
                    and self.s.pass_min_distance < f.ball.distance(pass_target) < self.s.pass_max_distance
                    and self.field.center_is_safe(pass_target, self.s)
                    and self._clearance(f, f.ball, pass_target) > self.s.pass_clearance)
        can_shoot = (f.ball.distance(goal) <= self.s.shot_range
                     and shot_gap > self.s.shot_clearance and not first_restart)
        budget_limited = self.control[striker].active and self.remaining_budget(f, striker) <= 0
        is_pass = can_pass and (first_restart or budget_limited or stuck
                               or self.current_tactic == "wall_pass" and self.wall_stage in ("idle", "return")
                               or self.current_tactic == "wing" and abs(f.ball.y) >= self.s.wing_y * .7 and not can_shoot
                               or self.current_tactic == "direct" and shot_gap <= self.s.shot_clearance)
        if first_restart and not can_pass:
            actions[striker] = self.move(f, striker, offset(f.ball, 350, pi), "restart: prepare teammate pass")
            return self._finish(actions, actions[striker].reason)
        # 间接球首脚和其它定位球统一采用两车配合，首脚绝不会直接射门。
        desired_target = pass_target if is_pass else goal
        # 出球条件不足时先推进；超过预算时只能放球/退开，不能继续找朝向带球。
        should_kick = is_pass or can_shoot
        if budget_limited:
            action = self._release_at_limit(f, striker, desired_target, is_pass, should_kick)
        else:
            carry_target = self.carry_target(f, goal)
            action = self.approach_or_kick(f, striker, desired_target, is_pass, should_kick, carry_target)
        actions[striker] = action
        if is_pass:
            actions[support] = self.move(f, support, pass_target, "receive pass: hold position")
        if action.kind == "kick":
            self.last_kick = f.cycle
            self.control[striker].kick_latched = True
            if is_pass:
                self.pass_receiver, self.pass_until = support, f.cycle + self.s.pass_wait_frames
                self.pass_target = pass_target
                if self.current_tactic == "wall_pass":
                    if self.wall_stage == "idle":
                        self.wall_stage = "outbound"
                        self.wall_passer, self.wall_receiver = striker, support
                        self.wall_return_point = self.field.safe(Point(f.ball.x + 650, f.ball.y), self.s)
                    elif self.wall_stage == "return":
                        self.wall_stage = "finish"
            if first_restart:
                self.locked_kicker = striker
            if not is_pass and self.wall_stage == "finish":
                self.wall_stage = "idle"
        return self._finish(actions, action.reason)

    def _choose_striker(self, f):
        if self.locked_kicker is not None:
            self.striker = next(n for n in self.s.robot_ids if n != self.locked_kicker)
            return
        if self.pass_receiver in self.s.robot_ids and f.cycle < self.pass_until:
            self.striker = self.pass_receiver
            return
        # 真正控球的车不因角色匹配变化丢失带球账本。
        holders = [n for n in self.s.robot_ids if self._contact(f.robots[n], f.ball)]
        if holders:
            self.striker = min(holders, key=lambda n: f.robots[n].pos.distance(f.ball))
            return
        candidate = min(self.s.robot_ids, key=lambda n: f.robots[n].pos.distance(f.ball))
        improvement = f.robots[self.striker].pos.distance(f.ball) - f.robots[candidate].pos.distance(f.ball)
        if candidate != self.striker and improvement > self.s.role_hysteresis and f.cycle - self.last_switch >= self.s.role_min_frames:
            self.striker, self.last_switch = candidate, f.cycle

    def _clearance(self, f, start, end, extra=()):
        return min((segment_clearance(p, start, end) for p in tuple(f.enemies) + tuple(extra)), default=99999.0)

    def goal_target(self, f, support):
        usable = max(0, self.field.goal_width / 2 - 130)
        candidates = [Point(self.field.length / 2, usable * k / 2) for k in (-2, -1, 0, 1, 2)]
        return max(candidates, key=lambda p: self._clearance(f, f.ball, p, [f.robots[support].pos]))

    def choose_tactic(self, f, shot_gap):
        if self.s.tactic != "automatic":
            return self.s.tactic
        if self.wall_stage in ("outbound", "return", "finish"):
            return "wall_pass"
        if shot_gap > self.s.shot_clearance:
            return "direct"
        # 球在中路而门将封线，先拉到边路；边路仍封线则进行二过一。
        return "wing" if abs(f.ball.y) < self.s.wing_y * .7 else "wall_pass"

    def _update_wall_stage(self, f):
        if self.wall_stage == "outbound" and self.wall_receiver is not None:
            if self._contact(f.robots[self.wall_receiver], f.ball) and self.locked_kicker is None:
                self.wall_stage = "return"

    def support_position(self, f, stuck=False):
        if self.wall_stage == "return" and self.striker == self.wall_receiver:
            return self.wall_return_point
        if self.pass_receiver is not None and f.cycle < self.pass_until and self.striker != self.pass_receiver:
            return self.pass_target
        side = -1 if f.ball.y >= 0 else 1
        if stuck:
            side *= -1
        x = min(f.ball.x + 550, self.field.length / 2 - self.field.penalty_depth - 450)
        y = f.ball.y + side * self.s.support_spacing
        if self.current_tactic == "wing":
            x += 250
            y = -side * self.s.wing_y * .65
        if (self.restart_key is not None and self.restart_key[0] == "kickoff"
                and self.locked_kicker is None and not self.restart_done):
            x = min(x, -550)
        return self.field.safe(Point(x, y), self.s)

    def carry_target(self, f, goal):
        if self.current_tactic == "wing":
            side = 1 if f.ball.y >= 0 else -1
            # 门将所处的另一侧优先；与球同侧时无需突然跨越球门中线。
            if f.enemies and abs(f.ball.y) < 200:
                side = -1 if min(f.enemies, key=lambda p: p.distance(goal)).y >= 0 else 1
            return self.field.safe(Point(f.ball.x + 700, side * self.s.wing_y), self.s)
        return self.field.safe(Point(f.ball.x + 900, f.ball.y * .65), self.s)

    def remaining_budget(self, f, n):
        c = self.control[n]
        speed = max(f.ball_speed, hypot(f.ball_velocity.x, f.ball_velocity.y))
        lookahead = min(220.0, speed * self.s.dribble_lookahead_seconds)
        return self.s.dribble_limit_mm - c.distance - self.s.dribble_reserve_mm - lookahead

    def _kick_ready(self, f, n, target):
        r = f.robots[n]
        desired = angle(f.ball, target)
        return (r.pos.distance(f.ball) <= self.s.kick_ball_distance
                and self._frontal(r, f.ball)
                and abs(angle_error(r.direction, desired)) < self.s.kick_angle_tolerance
                and f.ball_speed < 1400
                and f.cycle - self.last_kick >= self.s.kick_cooldown_frames
                and not self.control[n].kick_latched)

    def _kick_action(self, f, n, target, is_pass, reason):
        return Action("kick", f.robots[n].pos, angle(f.ball, target), target,
                      pass_ball=is_pass, avoid_ball=False, dribble=False, reason=reason)

    def _release_at_limit(self, f, n, target, is_pass, should_kick):
        if should_kick and self._kick_ready(f, n, target):
            return self._kick_action(f, n, target, is_pass, "dribble limit: release pass" if is_pass else "dribble limit: release shot")
        r = f.robots[n]
        # 未对正时关闭吸球并沿球后方退开，避免在放球阶段继续推球。
        return self.move(f, n, offset(r.pos, 360, angle(f.ball, r.pos)),
                         "dribble limit: release and retreat", avoid_ball=True, dribble=False)

    def approach_or_kick(self, f, n, target, is_pass, should_kick=True, carry_target=None):
        r, c = f.robots[n], self.control[n]
        desired = angle(f.ball, target)
        if c.kick_latched:
            return self.move(f, n, offset(r.pos, 330, angle(f.ball, r.pos)),
                             "kick requested: wait for clear separation", dribble=False)
        # 接高速来球先朝来球方向、开吸球拦截。立即朝下一脚射门方向站到球后，
        # 会让斜向/回传球撞到接球队员的后壳；实际红外确认持球后才恢复传射。
        if (self.pass_receiver == n and f.cycle < self.pass_until and f.ball_speed > 350
                and not self._contact(r, f.ball)):
            incoming = angle(r.pos, f.ball)
            horizon = min(self.s.intercept_horizon_seconds,
                          r.pos.distance(f.ball) / max(f.ball_speed, 1.0))
            intercept = self.field.safe(Point(f.ball.x + f.ball_velocity.x * horizon,
                                             f.ball.y + f.ball_velocity.y * horizon), self.s)
            receive_center = offset(intercept, self.s.contact_center_distance, incoming + pi)
            if (r.pos.distance(f.ball) < self.s.approach_distance
                    and abs(angle_error(r.direction, incoming)) > .3):
                receive_center = r.pos
            return self.move(f, n, receive_center, "receive moving pass: face incoming ball",
                             avoid_ball=r.pos.distance(f.ball) > self.s.approach_distance + 100,
                             dribble=True, direction=incoming)
        close = r.pos.distance(f.ball) <= self.s.kick_ball_distance
        frontal = self._frontal(r, f.ball)
        if close and frontal and c.active:
            if should_kick and c.contact_frames >= self.s.possession_settle_frames and self._kick_ready(f, n, target):
                return self._kick_action(f, n, target, is_pass, "teammate pass: " + self.current_tactic if is_pass else "shoot: " + self.current_tactic)
            # 传射时先原地转向/稳定持球；不得一边为了找角度无限带球。
            if should_kick:
                return self.move(f, n, r.pos, "settle/align for pass" if is_pass else "settle/align for shot",
                                 avoid_ball=False, dribble=True, direction=desired)
            carry_target = carry_target or target
            carry_angle = angle(r.pos, carry_target)
            if abs(angle_error(r.direction, carry_angle)) > .18:
                return self.move(f, n, r.pos, "align controlled dribble", False, True, carry_angle)
            step = max(0, min(self.s.carry_step, self.remaining_budget(f, n)))
            return self.move(f, n, offset(r.pos, step, carry_angle), "controlled dribble: " + self.current_tactic,
                             False, True, carry_angle)
        # 来球有速度时短时预测拦截点；控球车不能通过此分支重置预算。
        intercept = f.ball
        if f.ball_speed > 350 and self.pass_receiver == n:
            intercept = self.field.safe(Point(f.ball.x + f.ball_velocity.x * self.s.intercept_horizon_seconds,
                                             f.ball.y + f.ball_velocity.y * self.s.intercept_horizon_seconds), self.s)
        behind = offset(intercept, self.s.approach_distance, desired + pi)
        on_axis = abs(angle_error(angle(r.pos, f.ball), desired)) < .28
        facing = abs(angle_error(r.direction, desired)) < .25
        if r.pos.distance(f.ball) > self.s.approach_distance + 75 or not on_axis or not facing:
            if segment_clearance(f.ball, r.pos, behind) < 165:
                side = 1 if r.pos.y >= f.ball.y else -1
                behind = offset(intercept, 340, desired + side * pi / 2)
            return self.move(f, n, behind, "approach behind ball", direction=desired)
        contact = offset(f.ball, self.s.contact_center_distance, desired + pi)
        return self.move(f, n, contact, "capture ball with dribbler", False, True, desired)
