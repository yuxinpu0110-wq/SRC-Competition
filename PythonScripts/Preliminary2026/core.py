"""初赛两车策略的纯 Python 决策层。

这里不连接视觉、不下发机器人命令，只把“这一帧看到了什么”转换为“每辆车应当
停止、移动或踢球”。因此新手可以先读本文件和运行 tests/test_core.py；真正与
ZBin 接口相连的代码在 medusa.py。坐标/距离单位是 mm，角度单位是 rad。
"""

# dataclass 让场地、球车、动作变成清晰且不可随意改写的数据对象。
from dataclasses import dataclass
# 只引入策略几何计算实际需要的数学函数。
from math import atan2, cos, hypot, isfinite, pi, sin
# Munkres（匈牙利）算法为“车 -> 角色”寻找总代价最小的一对一分配。
# 使用本包内置实现，ZBin 环境无需另装 PyPI 依赖也能运行。
from .munkres import Munkres

# 全部可调参数都来自 settings.py；核心策略不散落难追踪的“魔法数字”。
from .settings import Settings


@dataclass(frozen=True)
class Point:
    x: float  # 场地纵向坐标；本策略默认对方球门位于 +X。
    y: float  # 场地横向坐标。

    def distance(self, other):
        """返回两点直线距离，单位 mm。"""
        return hypot(self.x - other.x, self.y - other.y)


def angle(a, b):
    """返回从 a 指向 b 的朝向，单位 rad。"""
    return atan2(b.y - a.y, b.x - a.x)


def angle_error(a, b):
    """将角度误差限制在 [-π, π)，避免正负 π 边界出现 358° 的假大误差。"""
    return (a - b + pi) % (2 * pi) - pi


def offset(point, distance, direction):
    """从 point 沿 direction 前进 distance，得到新点。"""
    return Point(point.x + distance * cos(direction), point.y + distance * sin(direction))


def segment_clearance(point, start, end):
    """返回 point 到线段 start-end 的最短距离。

    射门/传球时它近似表示门将或障碍物离线路有多近。它不是未来运动预测，
    所以“间隙最大”不等于保证进球。
    """
    # 线段的两个分量和长度平方；使用平方可少一次开方。
    dx, dy = end.x - start.x, end.y - start.y
    length2 = dx * dx + dy * dy
    # 将点投影到直线，再裁剪到 [0,1]，确保比较对象是有限线段。
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, ((point.x - start.x) * dx + (point.y - start.y) * dy) / length2))
    # 根据投影点计算最终距离。
    return point.distance(Point(start.x + t * dx, start.y + t * dy))


@dataclass(frozen=True)
class Field:
    length: float = 9000.0  # 场地总长。
    width: float = 6000.0  # 场地总宽。
    goal_width: float = 1000.0  # 球门内宽。
    penalty_depth: float = 1000.0  # 禁区向场内伸出的深度。
    penalty_width: float = 2000.0  # 禁区横向宽度。
    robot_radius: float = 90.0  # 机器人中心到车壳的近似半径。

    def forbidden(self, p, margin):
        """判断点是否进入外扩禁区。

        规则看车体任意部分是否进禁区，所以不只检查车中心：先加车体半径，
        再加策略余量。保守的代价是禁区边球会放弃争抢。
        """
        return (abs(p.x) >= self.length / 2 - self.penalty_depth - margin
                and abs(p.y) <= self.penalty_width / 2 + margin)

    def safe(self, p, settings):
        """将目标点裁到场内且禁区外，作为底层移动命令的保守终点。"""
        # 先将目标裁剪在边线安全距离以内。
        x = max(-self.length / 2 + settings.field_margin, min(p.x, self.length / 2 - settings.field_margin))
        y = max(-self.width / 2 + settings.field_margin, min(p.y, self.width / 2 - settings.field_margin))
        # 禁区外扩量 = 车体半径 + 额外安全距离。
        pad = self.robot_radius + settings.penalty_extra
        # 如果仍在禁区，退到同侧禁区前沿以外。
        if self.forbidden(Point(x, y), pad):
            x = (1 if x >= 0 else -1) * (self.length / 2 - self.penalty_depth - pad - 20)
        return Point(x, y)

    def center_is_safe(self, p, settings):
        """safe 不会移动该点，才说明车中心本身已经在允许区域。"""
        return self.safe(p, settings).distance(p) < 0.01

    def waypoint(self, start, end, settings):
        """选择安全终点；直线会穿禁区时先退到禁区前沿。

        这不是完整的路径规划。底层 DSS 仍负责动态避障；本函数只阻止明显
        直穿禁区的目标线。
        """
        # 目标点先安全裁剪。
        end = self.safe(end, settings)
        pad = self.robot_radius + settings.penalty_extra
        # 用 40 个样本检查从起点到终点的直线。
        for i in range(1, 41):
            p = Point(start.x + (end.x - start.x) * i / 40, start.y + (end.y - start.y) * i / 40)
            if self.forbidden(p, pad):
                # 发现会穿禁区：返回禁区前沿的临时目标，而不是继续直穿。
                front = self.length / 2 - self.penalty_depth - pad - 50
                return self.safe(Point((1 if p.x >= 0 else -1) * front, start.y), settings)
        # 直线安全：直接采用裁剪后的终点。
        return end


@dataclass(frozen=True)
class Robot:
    pos: Point  # 机器人中心位置。
    direction: float = 0.0  # 车头方向，单位 rad。
    valid: bool = True  # 视觉是否可靠识别到这辆车。
    infrared: bool = False  # 红外球传感器是否检测到近球。


@dataclass(frozen=True)
class Frame:
    cycle: int  # 视觉帧号，用于拒绝重复/倒退帧。
    ball: Point  # 球的位置。
    robots: dict[int, Robot]  # 两辆我方车；字典 key 为真实车号。
    enemies: tuple[Point, ...] = ()  # 可见的对手和官方门将位置。
    ball_valid: bool = True  # 球视觉是否可信。
    game_on: bool = True  # 裁判是否允许正常比赛。
    can_kick: bool = False  # 本地接口报告是否允许首脚出球。
    phase: str = "normal"  # normal / kickoff / direct / indirect / defend / halt。
    ball_speed: float = 0.0  # 视觉估计球速，单位 mm/s。
    restart_token: int = 0  # 这一次定位球的标识。


@dataclass(frozen=True)
class Action:
    kind: str = "stop"  # stop / move / kick 三种基础动作。
    target: Point | None = None  # 机器人本体应靠近的目标。
    direction: float = 0.0  # 希望车头面向的方向。
    kick_target: Point | None = None  # kick 时球应前往的位置。
    pass_ball: bool = False  # True 表示这是传球，接口层使用较保守力度曲线。
    avoid_ball: bool = True  # 移动中是否请求底层绕开球。
    dribble: bool = False  # True 表示打开吸球并允许带球移动。
    reason: str = "hold"  # 用于 GUI/日志的可读解释。


class Planner:
    """跨帧保存策略记忆的两车决策器。

    它保存“谁是进攻车、定位球是否完成、球是否停滞”等状态，但每帧位置仍只信
    Frame 的最新视觉。step 的顺序是：安全检查 -> 裁判/定位球 -> 角色 -> 传射。
    """

    def __init__(self, settings=None, field=None):
        self.s = settings or Settings()  # 支持测试时传入另一套参数。
        self.field = field or Field()  # 未接入 ZBin 时采用默认小场参数。
        # 初赛只允许两辆进攻车，重复编号会让角色与二次触球安全锁失去意义。
        if len(self.s.robot_ids) != 2 or len(set(self.s.robot_ids)) != 2:
            raise ValueError("robot_ids 必须是两个不同的车号")
        # 同一个 matcher 可跨帧复用；每一帧仍用新位置重新建立代价矩阵。
        self.role_matcher = Munkres()
        self.striker = self.s.robot_ids[0]  # 初始取球/射门车。
        self.last_switch = -100000  # 上次切换进攻车的帧号。
        self.last_kick = -100000  # 上次下踢球命令的帧号。
        self.last_cycle = -1  # 最近已处理的视觉帧号。
        self.restart_key = None  # (定位球类别, 裁判帧号)。
        self.restart_origin = None  # 定位球开始时的球位置。
        self.restart_done = False  # 另一车完成合法第二触后才为 True。
        self.locked_kicker = None  # 首触车号；非 None 时不得再碰球。
        self.touch_frames = 0  # 接应车红外近球连续帧数。
        self.pass_receiver = None  # 已出传球后优先接球的车号。
        self.pass_until = -1  # 优先接球的截止帧号。
        self.progress_ball = None  # 最近明显推进时的球位置。
        self.progress_cycle = 0  # 最近明显推进时的帧号。
        self.possession_since = None  # 当前主攻车第一次进入可控球姿态的帧号。
        self.status = "waiting"  # 给 GUI 显示的当前决定原因。

    def stop_all(self, reason):
        """为两辆车返回停止动作，且把原因留给 GUI。"""
        self.status = reason
        return {n: Action(reason=reason) for n in self.s.robot_ids}

    def move(self, frame, n, target, reason, avoid_ball=True, dribble=False):
        """生成一辆车的移动 Action，并把目标修正为安全 waypoint。"""
        waypoint = self.field.waypoint(frame.robots[n].pos, target, self.s)
        return Action("move", waypoint, angle(frame.robots[n].pos, frame.ball),
                      avoid_ball=avoid_ball, dribble=dribble, reason=reason)

    def munkres_roles(self, f, support_point):
        """用 Munkres 算法给两辆已授权车分配主攻与接应角色。

        矩阵的行是 ``robot_ids`` 中的真实车号，列依次是主攻位（球）与接应位。
        Munkres 保证一辆车只得到一个角色、每个角色也只由一辆车承担。主攻列
        乘以较大的权重：初赛最重要的是有人尽快处理球，不能因为另一辆车刚好
        更靠近接应点，就让明显更近球的车放弃取球。

        注意：这不是 RoleMatch 框架的自动车号匹配。函数输出只会包含
        settings.py 的两辆车，medusa.py 仍用 fixedNumber 下发任务。
        """
        numbers = self.s.robot_ids
        # 每一行 [主攻代价, 接应代价] 都是距离的加权近似，单位仍可理解为 mm。
        costs = [
            [
                f.robots[n].pos.distance(f.ball) * self.s.striker_match_weight,
                f.robots[n].pos.distance(support_point),
            ]
            for n in numbers
        ]
        # compute 返回 (行号, 列号)；2x2 情况仍使用通用算法，便于将来扩到更多角色。
        pairs = self.role_matcher.compute(costs)
        roles = ("striker", "support")
        # 翻译索引，使调用处只需读取 roles["striker"] / roles["support"]。
        return {roles[column]: numbers[row] for row, column in pairs}

    def role_assignment_cost(self, f, support_point, striker):
        """计算指定主攻车的总代价，专供迟滞判断，不直接控制机器人。"""
        support = next(n for n in self.s.robot_ids if n != striker)
        # 与 munkres_roles 使用完全相同的代价定义，比较才有意义。
        return (f.robots[striker].pos.distance(f.ball) * self.s.striker_match_weight
                + f.robots[support].pos.distance(support_point))

    def step(self, f):
        """用一帧比赛信息输出 {车号: Action}。

        早期 return 都是有意的安全停止：信息不可信、裁判不允许或位置不安全时，
        不应该继续复用上一帧“看起来合理”的进攻决定。
        """
        # 不重用上一帧踢球命令；视觉倒退也停止，避免用错缓存。
        if f.cycle <= self.last_cycle:
            return self.stop_all("stale vision cycle")
        # 即使本帧会因其它原因停止，仍标记已经消费，防止同一帧稍后被重用。
        self.last_cycle = f.cycle
        numbers = self.s.robot_ids  # 两辆受控车的真实编号。
        # 统一检查球、我方车、敌车坐标，NaN/Inf 绝不能传给底层规划。
        coords = [f.ball] + [r.pos for r in f.robots.values()] + list(f.enemies)
        if (not f.ball_valid or not all(isfinite(p.x) and isfinite(p.y) for p in coords)
                or not isfinite(f.ball_speed)
                or any(n not in f.robots or not f.robots[n].valid or not isfinite(f.robots[n].direction) for n in numbers)):
            return self.stop_all("invalid ball/robot vision")
        # HALT 或对方定位球时，本策略不主动接球。
        if f.phase in ("halt", "defend"):
            return self.stop_all("referee hold")
        # 球已越界后交给裁判；不让车继续冲到场外。
        if abs(f.ball.x) > self.field.length / 2 or abs(f.ball.y) > self.field.width / 2:
            return self.stop_all("ball outside field")
        # 己方车已经在边线/禁区安全边界外时先停，避免继续违规。
        if any(not self.field.center_is_safe(f.robots[n].pos, self.s) for n in numbers):
            return self.stop_all("robot at field/penalty safety boundary")
        # 球过近禁区时也保守停止，避免为抢球把车带进禁区。
        if self.field.forbidden(f.ball, self.field.robot_radius + self.s.penalty_extra):
            return self.stop_all("ball too close to penalty area")

        # 只有我方开球、直接任意球、间接任意球需要首触/二次触球保护。
        own_restart = f.phase in ("kickoff", "direct", "indirect")
        if own_restart:
            key = (f.phase, f.restart_token)
            # 检测到一轮新定位球，清掉上一轮的首触锁和传球记忆。
            if self.restart_key != key:
                self.restart_key = key
                self.restart_origin = f.ball
                self.restart_done = False
                self.locked_kicker = None
                self.touch_frames = 0
                self.pass_receiver = None
            # 默认不开自动定位球；应先在仿真确认裁判信号的含义。
            if not self.s.enable_restart_kicks:
                return self.stop_all("restart disabled: verify start signal before enabling")
            # 没有开始许可时严禁提前出球。
            if not (f.game_on or f.can_kick):
                return self.stop_all("waiting for referee start permission")
        # 普通活球也必须处于裁判允许的比赛进行状态。
        elif not f.game_on:
            return self.stop_all("game not started")

        # 首踢者锁跨 Play 切换保存。时间到了不能自动解除二次触球限制。
        if self.locked_kicker is not None:
            other = next(n for n in numbers if n != self.locked_kicker)
            robot = f.robots[other]
            # 必须是另一车红外近球，且距离也足够近，才视为可能的第二触。
            contact = robot.infrared and robot.pos.distance(f.ball) <= self.s.kick_ball_distance
            # 连续帧计数过滤偶发红外噪声。
            self.touch_frames = self.touch_frames + 1 if contact else 0
            if self.touch_frames >= self.s.restart_contact_frames:
                # 队友已摸球：首脚车解除锁，定位球流程完成。
                self.locked_kicker = None
                self.restart_done = True
                self.touch_frames = 0
            else:
                # 在解除前，只让另一车承担进攻角色，防止首脚车补踢。
                self.striker = other

        # 球每移动足够远就刷新“有推进”时间，用于识别僵持而不是严格比赛计时。
        if self.progress_ball is None or self.progress_ball.distance(f.ball) > self.s.ball_progress_mm:
            self.progress_ball, self.progress_cycle = f.ball, f.cycle
        stuck = f.cycle - self.progress_cycle > self.s.stuck_frames
        # 首触锁住的车不属于可选取球车。
        available = [n for n in numbers if n != self.locked_kicker]
        # 接应点不依赖“当前谁是主攻”，因此可先用于 Munkres 的角色代价矩阵。
        support_point = self.support_position(f, stuck)
        # 传球刚出脚时，短时间优先让指定接球车接管。
        if self.pass_receiver in available and f.cycle < self.pass_until:
            self.striker = self.pass_receiver
        elif self.locked_kicker is None:
            # Munkres 同时考虑“谁更适合拿球”和“谁更适合接应”，给出候选主攻车。
            matched = self.munkres_roles(f, support_point)
            candidate = matched["striker"]
            # 计算候选分配相对当前分配能节省多少总代价；正数才说明有改进。
            improvement = (self.role_assignment_cost(f, support_point, self.striker)
                           - self.role_assignment_cost(f, support_point, candidate))
            # 算法每帧可以重新匹配，但真正换主攻还要满足迟滞、最短间隔和未持球。
            # 这样既使用全局一对一分配，也不会在临界位置来回抢角色。
            if (candidate != self.striker and f.cycle - self.last_switch >= self.s.role_min_frames
                    and improvement > self.s.role_hysteresis
                    and not f.robots[self.striker].infrared):
                self.striker, self.last_switch = candidate, f.cycle

        striker = self.striker  # 当前取球/传球/射门车。
        support = next(n for n in numbers if n != striker)  # 另一辆为接应车。
        actions = {support: self.move(f, support, support_point, "support")}
        # 若接应车正是首触车，让它离球退开，防止意外二次触球。
        if support == self.locked_kicker:
            retreat = offset(f.ball, 750, angle(f.ball, f.robots[support].pos))
            actions[support] = self.move(f, support, retreat, "first kicker: avoid ball")

        # 先选择球门内相对开阔的射门点；接应车也被视作射线障碍。
        goal = self.goal_target(f, support)
        score_gap = min((segment_clearance(e, f.ball, goal) for e in f.enemies), default=9999)
        teammate = f.robots[support].pos
        # 传球要有合适距离、接应车不能被首触锁住，且所有敌车离传球线足够远。
        can_pass = (support != self.locked_kicker and 650 < teammate.distance(f.ball) < 2600
                    and all(segment_clearance(e, f.ball, teammate) > 260 for e in f.enemies))
        first_restart = own_restart and not self.restart_done and self.locked_kicker is None
        # 所有定位球先传给队友；因此间接球不会首脚直射。活球在受阻/僵持才优先横传。
        should_pass = can_pass and (first_restart or stuck or score_gap < 210)
        if first_restart and not can_pass:
            # 定位球不能安全传出就先站好，不冒险直踢。
            actions[striker] = self.move(f, striker, offset(f.ball, 700, pi), "waiting for pass receiver")
            self.status = "restart receiver positioning"
            return actions
        # 首脚下令后，球尚未移动超过规则 50 mm 前，接应车先不抢球。
        if self.locked_kicker is not None and self.restart_origin is not None:
            if f.ball.distance(self.restart_origin) <= 50:
                # 尚未移过规则阈值，接应车不扑球，避免疑似未开出时纠缠。
                actions[striker] = Action(reason="restart kick requested; waiting >50 mm")
                return actions
        # 决定球要去队友还是球门。
        kick_target = teammate if should_pass else goal
        if should_pass:
            # 接应车保持在被选择的接球点，不同时另跑一个目标。
            actions[support] = self.move(f, support, teammate, "receive pass: hold position")
        action = self.approach_or_kick(f, striker, kick_target, should_pass)
        actions[striker] = action
        if action.kind == "kick":
            # 记录出脚帧，后续帧进入冷却。
            self.last_kick = f.cycle
            if should_pass:
                self.pass_receiver, self.pass_until = support, f.cycle + self.s.pass_wait_frames
            if first_restart:
                # 命令即上锁：即使球没出去，也不由首踢者补踢。
                self.locked_kicker = striker
        self.status = action.reason
        return actions

    def support_position(self, f, stuck):
        """给接应车选球侧前方的安全站位；僵持时换另一侧创造新传球线。"""
        side = -1 if f.ball.y >= 0 else 1
        if stuck:
            side *= -1
        x = min(f.ball.x + 450, self.field.length / 2 - self.field.penalty_depth - 500)
        if f.phase == "kickoff" and self.locked_kicker is None:
            x = min(x, -650)
        return self.field.safe(Point(x, f.ball.y + side * self.s.support_spacing), self.s)

    def goal_target(self, f, support):
        """从五个球门内候选点中选出射线离障碍最远的一个。"""
        # 球门内留 130 mm，不瞄准门柱；比较每条射线和门将/队友的距离。
        usable = self.field.goal_width / 2 - 130
        candidates = [Point(self.field.length / 2, usable * k / 2) for k in (-2, -1, 0, 1, 2)]
        obstacles = list(f.enemies) + [f.robots[support].pos]
        return max(candidates, key=lambda p: min((segment_clearance(e, f.ball, p) for e in obstacles), default=9999))

    def approach_or_kick(self, f, n, target, is_pass):
        """从球后接近、吸球带一小段，再传球或射门。"""
        robot = f.robots[n]
        direction = angle(f.ball, target)
        behind = offset(f.ball, self.s.approach_distance, direction + pi)
        facing = abs(angle_error(robot.direction, direction)) < self.s.kick_angle_tolerance
        on_axis = abs(angle_error(angle(robot.pos, f.ball), direction)) < 0.20
        close = robot.pos.distance(f.ball) <= self.s.kick_ball_distance
        # 四个条件同时满足才允许请求踢球，减少从侧面撞球或追高速球乱踢。
        # 从球的侧方绕到球后，不把球当作普通导航穿越点。
        if robot.pos.distance(f.ball) > self.s.approach_distance + 90 or not facing or not on_axis:
            # 离开控球姿态就重新计时；下一次贴球会再次先吸球稳定下来。
            self.possession_since = None
            if segment_clearance(f.ball, robot.pos, behind) < 170:
                side = 1 if robot.pos.y >= f.ball.y else -1
                behind = offset(f.ball, 320, direction + side * pi / 2)
            p = self.field.waypoint(robot.pos, behind, self.s)
            return Action("move", p, direction, avoid_ball=True, reason="approach behind ball")
        if not close:
            # 车头已对准时打开吸球，接近球后方约 98 mm。
            self.possession_since = None
            contact = offset(f.ball, self.s.contact_center_distance, direction + pi)
            p = self.field.waypoint(robot.pos, contact, self.s)
            return Action("move", p, direction, avoid_ball=False, dribble=True,
                          reason="close with dribbler")

        # 已在球后且车头已对准：先稳定控球数帧，再允许传射，避免“碰球即踢”。
        if self.possession_since is None:
            self.possession_since = f.cycle
        held_frames = f.cycle - self.possession_since
        if held_frames >= self.s.possession_settle_frames and f.ball_speed < 1200:
            if f.cycle - self.last_kick >= self.s.kick_cooldown_frames:
                self.possession_since = None
                return Action("kick", robot.pos, direction, target, is_pass, False, False,
                              "pass after possession" if is_pass else "shoot after possession")
            return Action(reason="kick cooldown")

        carry = offset(f.ball, self.s.possession_carry_distance, direction)
        p = self.field.waypoint(robot.pos, carry, self.s)
        return Action("move", p, direction, avoid_ball=False, dribble=True,
                      reason=f"capture and carry ({held_frames}/{self.s.possession_settle_frames})")
