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

    def diagnostics(self):
        return asdict(self)


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
    def __init__(self, max_age=.2, tolerance=60):
        self.max_age, self.tolerance = max_age, tolerance
        self.last_key = None
        self.last_capture = None
        self.last_advance = None
        self.camera_times = {}

    def evaluate(self, capture, now, ball, players, team, side, sdk_cycle=None):
        def result(reason, fresh=False, new=False):
            return Evidence(new, fresh, reason,
                            capture.capture_time if capture else None,
                            capture.receive_time if capture else None,
                            (capture.camera, capture.frame_id) if capture else None, sdk_cycle)
        if capture is None:
            return result('NO_CAPTURE_EVIDENCE')
        if not 0 <= now-capture.receive_time <= self.max_age:
            return result('STALE_RECEIVE')
        # t_capture 是 Unix 秒；旧包重发不能用新的接收时间洗掉捕获年龄。
        age = time.time()-capture.capture_time
        if not -.1 <= age <= self.max_age:
            return result('STALE_CAPTURE')
        if capture.ball is None or not players:
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
