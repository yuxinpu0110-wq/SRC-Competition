"""V2 视觉采样门：区分控制循环和真正的新视觉帧。

同一张融合画面可能被 Python 读取多次。规则计数、速度估计和稳定计时只能在
新画面到来时更新；裁判命令仍由每次控制循环处理。单位为毫米、秒。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SampleStatus:
    cycle: int | None
    fresh: bool
    age: float
    reset: bool = False


class ObservationClock:
    def __init__(self, stale_after=.15):
        self.stale_after = stale_after
        self.last_cycle = None
        self.last_fresh_at = None

    def observe(self, now, cycle=None):
        """返回这一控制循环是否包含新视觉数据。

        cycle=None 用于旧测试或没有 cycle 接口的 SDK，此时每次调用都作为新样本。
        cycle 回退通常表示视觉重连；它是新样本，同时要求上层清空速度历史。
        """
        reset = cycle is not None and self.last_cycle is not None and cycle < self.last_cycle
        fresh = cycle is None or self.last_cycle is None or cycle != self.last_cycle
        if fresh:
            self.last_cycle = cycle
            self.last_fresh_at = now
        age = 0.0 if self.last_fresh_at is None else max(0.0, now-self.last_fresh_at)
        return SampleStatus(cycle, fresh, age, reset)

    def stale(self, now):
        return self.last_fresh_at is None or now-self.last_fresh_at > self.stale_after



"""视觉证据契约：循环编号不是视觉帧号。长度 mm，捕获/接收时间 s。

SDK 未暴露融合时间戳；原始帧只作为关联证据，绝不标成融合帧时间。
选定单个 camera 的序列，避免把多相机同一时刻当作连续运动样本。
"""
from dataclasses import asdict, dataclass
import math
import socket
import threading
import time
from match_tactics import Point, distance
from simulation_probe import fields


@dataclass(frozen=True)
class Capture:
    camera: int
    frame_id: int
    capture_time: float
    receive_time: float
    ball: Point | None
    robots: dict


@dataclass(frozen=True)
class Evidence:
    new_capture: bool = False
    fresh: bool = False
    reason: str = 'NO_CAPTURE_EVIDENCE'
    capture_time: float | None = None
    receive_time: float | None = None
    frame_id: tuple | None = None
    sdk_cycle: int | None = None
    source: str = 'raw_associated_fused_timestamp_unknown'
    capture_clock: str = 'unix'
    capture_age_s: float | None = None
    # None 供旧接口使用；新多相机接口明确列出已验证的车，未验证车不得执行任务。
    trusted_robots: tuple | None = None

    def diagnostics(self):
        return asdict(self)


def control_ball(fused, capture, evidence, side):
    """接触几何用已关联的真实检测点，不把SDK近车修正点当物理球心。

    仍先执行融合/原始关联；不能用未验证、过期或丢失的原始球绕过保护。
    side=right 时与SDK保持同一攻向坐标系，不修改原始包或模拟器物理。
    """
    if capture is None or capture.ball is None or evidence is None or not evidence.fresh:
        return fused
    sign = -1 if side == 'right' else 1
    return Point(sign*capture.ball.x, sign*capture.ball.y)


def decode_detection(packet, received):
    """SSL 官方 detection.proto；confidence 低/数据非法的对象不参与关联。"""
    for tag, data in fields(packet):
        if tag != 1 or not isinstance(data, bytes):
            continue
        values, balls, robots = {}, [], {}
        for number, value in fields(data):
            if number == 5:
                obj = dict(fields(value))
                if obj.get(1, 0) > .5 and all(math.isfinite(obj.get(i, math.nan)) for i in (3, 4)):
                    balls.append(Point(obj[3], obj[4]))
            elif number in (6, 7):
                obj = dict(fields(value))
                if obj.get(1, 0) > .5 and 2 in obj and all(math.isfinite(obj.get(i, math.nan)) for i in (3, 4)):
                    robots[('yellow' if number == 6 else 'blue', obj[2])] = Point(obj[3], obj[4])
            else:
                values[number] = value
        if all(n in values for n in (1, 2, 4)) and math.isfinite(values[2]):
            yield Capture(values[4], values[1], values[2], received,
                          balls[0] if len(balls) == 1 else None, robots)


class FreshnessGuard:
    def __init__(self, max_age=.2, tolerance=60, capture_clock='unix'):
        if capture_clock not in ('unix','relative','simulation'):
            raise ValueError('Unknown raw capture clock domain')
        self.max_age, self.tolerance = max_age, tolerance
        self.capture_clock = capture_clock
        self.last_key = None
        self.last_capture = None
        self.last_advance = None
        self.camera_times = {}
        self.clock_offsets = {}

    def evaluate(self, capture, now, ball, players, team, side, sdk_cycle=None):
        age = None
        def result(reason, fresh=False, new=False):
            return Evidence(new, fresh, reason,
                            capture.capture_time if capture else None,
                            capture.receive_time if capture else None,
                            (capture.camera, capture.frame_id) if capture else None, sdk_cycle,
                            capture_clock=self.capture_clock, capture_age_s=age)
        if capture is None:
            return result('NO_CAPTURE_EVIDENCE')
        if not 0 <= now-capture.receive_time <= self.max_age:
            return result('STALE_RECEIVE')
        if self.capture_clock == 'simulation':
            # grSim捕获时间是物理仿真时钟，暂停/低FPS时不保证与宿主同速。
            # 本机链路以收到新包的年龄和捕获序列前进为依据，不能伪造Unix年龄。
            age = now-capture.receive_time
        elif self.capture_clock == 'unix':
            age = time.time()-capture.capture_time
        else:
            # 本机已测 grSim 使用启动后的相对秒数。按相机建立单调时钟关联，
            # 最小接收偏移只向较小值更新；重复/滞后包不能重设锚点来洗掉年龄。
            if not math.isfinite(capture.capture_time) or capture.capture_time < 0:
                return result('INVALID_CAPTURE_TIME')
            offset = capture.receive_time-capture.capture_time
            previous_offset = self.clock_offsets.get(capture.camera)
            self.clock_offsets[capture.camera] = offset if previous_offset is None else min(offset,previous_offset)
            age = now-(capture.capture_time+self.clock_offsets[capture.camera])
        if not -.1 <= age <= self.max_age:
            return result('STALE_CAPTURE')
        if capture.ball is None:
            return result('RAW_OBJECTS_MISSING')
        sign = -1 if side == 'right' else 1
        def normalized(point):
            return Point(sign*point.x, sign*point.y)
        if distance(normalized(capture.ball), ball) > self.tolerance:
            return result('FUSION_RAW_MISMATCH')
        for n, p in players.items():
            raw = capture.robots.get((team, n))
            if raw is None or distance(normalized(raw), p) > self.tolerance:
                return result('FUSION_RAW_MISMATCH')
        key = (capture.camera, capture.frame_id)
        if self.last_key != key and capture.capture_time <= self.camera_times.get(capture.camera, -math.inf):
            return result('CAPTURE_NOT_ADVANCING')
        if self.last_key is not None and capture.camera != self.last_key[0]:
            # 换相机先取消本帧，下一帧重新建速度/事务历史，禁止拼接两相机差分。
            self.last_key = self.last_capture = self.last_advance = None
            return result('CAMERA_CHANGED')
        if self.last_key == key:
            return result('DUPLICATE_CAPTURE', fresh=self.last_advance is not None
                          and now-self.last_advance <= self.max_age)
        if self.last_capture is not None and capture.capture_time <= self.last_capture:
            return result('CAPTURE_NOT_ADVANCING')
        self.last_key, self.last_capture, self.last_advance = key, capture.capture_time, now
        self.camera_times[capture.camera] = capture.capture_time
        return result('RAW_ASSOCIATED', fresh=True, new=True)


class RawVision:
    """只读监听；失败保留原因，不回退为 Python 循环计时。"""
    def __init__(self, group='224.5.23.2', port=10020, camera=0):
        self.camera = camera
        self.latest = None
        self.captures = {}
        self.error = None
        self.closed = threading.Event()
        self.socket = None
        self.thread = None
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(('', port))
            self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                  socket.inet_aton(group)+socket.inet_aton('0.0.0.0'))
            self.socket.settimeout(.05)
            self.thread = threading.Thread(target=self._receive, daemon=True)
            self.thread.start()
        except OSError as exc:
            self.error = str(exc)
            if self.socket is not None:
                self.socket.close()
            self.socket = None

    def _receive(self):
        while not self.closed.is_set():
            try:
                packet, _ = self.socket.recvfrom(65535)
                for capture in decode_detection(packet, time.perf_counter()):
                    previous=self.captures.get(capture.camera)
                    if previous is not None and capture.capture_time <= previous.capture_time:
                        if not (capture.frame_id < previous.frame_id and
                                capture.capture_time < previous.capture_time-.2):
                            continue  # 重发/轻微乱序不覆盖接收年龄；模拟器重启另行重建契约。
                    self.captures[capture.camera] = capture
                    if capture.camera == self.camera or self.camera == -1:
                        self.latest = capture
            except socket.timeout:
                continue
            except (OSError, ValueError) as exc:
                self.error = str(exc)
                if isinstance(exc, OSError):
                    break

    def associated_capture(self, ball, players, team, side, guard):
        if self.camera != -1:
            return self.latest
        sign = -1 if side == 'right' else 1
        candidates = []
        for c in tuple(self.captures.values()):
            if c.ball is None or distance(Point(sign*c.ball.x,sign*c.ball.y),ball) > guard.tolerance:
                continue
            if all((team,n) in c.robots and distance(Point(sign*c.robots[team,n].x,sign*c.robots[team,n].y),p)
                   <= guard.tolerance for n,p in players.items()):
                candidates.append(c)
        if not candidates:
            return None
        # 同一条轨迹优先保持原相机；相机交接必经一帧安全取消。
        now = time.perf_counter()
        current = [c for c in candidates if guard.last_key and c.camera == guard.last_key[0]
                   and 0 <= now-c.receive_time <= guard.max_age]
        return max(current or candidates, key=lambda c:c.capture_time)

    def close(self):
        self.closed.set()
        if self.thread is not None:
            self.thread.join(timeout=.2)
        if self.socket is not None:
            self.socket.close()


class RelayVision:
    """SDK 只读取独立分流进程快照，不在原生 SDK 旁创建接收线程。"""
    def __init__(self):
        self.captures = {}
        self.error = None
        self.polled_at = -math.inf

    def poll(self):
        from vision_relay import status
        now = time.perf_counter()
        if now-self.polled_at < .003: return
        self.polled_at = now
        response = status(timeout=.03,command=b'captures')
        if response is None:
            self.error = 'VISION_RELAY_NOT_RESPONDING'
            return
        self.error = None
        self.captures = {(c['camera'],c['frame_id']):Capture(c['camera'],c['frame_id'],c['capture_time'],
            c['receive_time'],None if c['ball'] is None else Point(*c['ball']),
            {(team,n):Point(x,y) for team,n,x,y in c['robots']})
            for c in response.get('captures',[])}

    def close(self):
        pass  # 停止某队策略不能停止服务于 Client 的分流进程。


class MultiCameraGuard:
    """球与每辆车各自关联原始相机；不伪造一个全场相机快照。

    原始捕获时间仍不是融合时间。仅接受位置相符、时差≤50ms的对象；队友缺证据
    时只撤销该车任务。相机切换取消一帧，使速度/稳定历史能重新建立。
    """
    def __init__(self, max_age=.2, tolerance=60, capture_clock='relative'):
        self.max_age, self.tolerance = max_age, tolerance
        self.capture_clock = capture_clock
        self.guards = {}
        self.last_camera = None
        self.robot_offsets = {}
        self.camera_times = {}

    def _age(self, c, now):
        if not math.isfinite(c.capture_time) or c.capture_time < 0:
            return math.inf
        if self.capture_clock == 'simulation':
            return now-c.receive_time
        if self.capture_clock == 'unix':
            return time.time()-c.capture_time
        offset = c.receive_time-c.capture_time
        previous = self.robot_offsets.get(c.camera)
        self.robot_offsets[c.camera] = offset if previous is None else min(offset, previous)
        return now-c.capture_time-self.robot_offsets[c.camera]

    def evaluate_all(self, captures, now, ball, players, team, side, sdk_cycle=None):
        from dataclasses import replace
        sign = -1 if side == 'right' else 1
        def normalized(p): return Point(sign*p.x, sign*p.y)
        if any(c.capture_time < self.camera_times.get(c.camera,-math.inf)-.2 for c in captures):
            self.guards.clear()
            self.robot_offsets.clear()
            self.camera_times = {c.camera:c.capture_time for c in captures}
            self.last_camera = None
            return Evidence(reason='CAPTURE_CLOCK_RESET',trusted_robots=()), None
        self.camera_times.update({c.camera:c.capture_time for c in captures})
        available = [c for c in captures if 0 <= now-c.receive_time <= self.max_age
                     and -.1 <= self._age(c, now) <= self.max_age]
        candidates = [c for c in available if c.ball is not None
                      and distance(normalized(c.ball), ball) <= self.tolerance]
        if not candidates:
            return Evidence(reason='NO_FRESH_BALL_ASSOCIATION', trusted_robots=()), None
        current = [c for c in candidates if c.camera == self.last_camera]
        capture = max(current or candidates, key=lambda c:c.capture_time)
        if self.last_camera is not None and self.last_camera != capture.camera:
            self.last_camera = capture.camera
            self.guards.pop(capture.camera, None)
            return Evidence(reason='CAMERA_CHANGED', trusted_robots=()), capture
        self.last_camera = capture.camera
        guard = self.guards.setdefault(capture.camera,
            FreshnessGuard(self.max_age, self.tolerance, self.capture_clock))
        evidence = guard.evaluate(capture, now, ball, {}, team, side, sdk_cycle)
        trusted = []
        for number, p in players.items():
            if any((team, number) in c.robots
                   and abs(c.capture_time-capture.capture_time) <= .05
                   and distance(normalized(c.robots[team, number]),p) <= self.tolerance
                   for c in available):
                trusted.append(number)
        if not trusted:
            return replace(evidence, fresh=False, new_capture=False,
                           reason='NO_FRESH_ROBOT_ASSOCIATION', trusted_robots=()), capture
        return replace(evidence, trusted_robots=tuple(sorted(trusted))), capture
