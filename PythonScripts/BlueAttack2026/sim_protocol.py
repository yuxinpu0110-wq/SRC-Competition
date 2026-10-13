"""Local grSim test transport, using schemas embedded in the shipped executable.

No dependency on protoc or protobuf is needed. Importing this module never opens
a socket. The public replacement API accepts mm and radians; grSim replacement
messages encode metres and degrees. Classic robot commands use m/s and rad/s.
This is a simulation helper, separate from the match program's CppPackage path.
"""

from __future__ import annotations

import math
import socket
import struct
import time
from collections.abc import Mapping

VISION_GROUP = "224.5.23.2"
VISION_PORT = 10020
COMMAND_PORT = 20011
REFEREE_PORT = 39991
BLUE_IDS = (5, 6)
YELLOW_GOALIE = 0

REFEREE_COMMANDS = {
    "HALT": 0, "STOP": 1, "NORMAL_START": 2, "FORCE_START": 3,
    "PREPARE_KICKOFF_YELLOW": 4, "PREPARE_KICKOFF_BLUE": 5,
    "PREPARE_PENALTY_YELLOW": 6, "PREPARE_PENALTY_BLUE": 7,
    "DIRECT_FREE_YELLOW": 8, "DIRECT_FREE_BLUE": 9,
    "INDIRECT_FREE_YELLOW": 10, "INDIRECT_FREE_BLUE": 11,
    "TIMEOUT_YELLOW": 12, "TIMEOUT_BLUE": 13,
    "GOAL_YELLOW": 14, "GOAL_BLUE": 15,
    "BALL_PLACEMENT_YELLOW": 16, "BALL_PLACEMENT_BLUE": 17,
}


def _varint(value: int) -> bytes:
    if value < 0:
        value &= (1 << 64) - 1
    result = bytearray()
    while value > 127:
        result.append((value & 127) | 128)
        value >>= 7
    result.append(value)
    return bytes(result)


def _int(number: int, value: int | bool) -> bytes:
    return _varint(number << 3) + _varint(int(value))


def _float(number: int, value: float) -> bytes:
    if not math.isfinite(value):
        raise ValueError("non-finite simulator value")
    return _varint((number << 3) | 5) + struct.pack("<f", value)


def _double(number: int, value: float) -> bytes:
    if not math.isfinite(value):
        raise ValueError("non-finite simulator value")
    return _varint((number << 3) | 1) + struct.pack("<d", value)


def _message(number: int, payload: bytes) -> bytes:
    return _varint((number << 3) | 2) + _varint(len(payload)) + payload


def _text(number: int, value: str) -> bytes:
    return _message(number, value.encode("utf-8"))


def _robot_id(number: int) -> int:
    if not isinstance(number, int) or not 0 <= number <= 15:
        raise ValueError("grSim robot ID must be an integer from 0 through 15")
    return number


def replacement_packet(
    *,
    ball: tuple[float, float, float, float] | None = None,
    blue: Mapping[int, tuple[float, float, float]] | None = None,
    yellow: Mapping[int, tuple[float, float, float]] | None = None,
    disable_others: bool = True,
    robot_count: int = 8,
) -> bytes:
    """Encode a full scenario. Ball is x/y/vx/vy in mm and mm/s.

    Team values are x/y in mm, orientation in radians. Explicit positions are
    turned on. Other IDs in [0, robot_count) are turned off when requested.
    The old/new grSim replacement schemas share this wire representation.
    """
    if not 1 <= robot_count <= 16:
        raise ValueError("robot_count must be between 1 and 16")
    body = bytearray()
    if ball is not None:
        if len(ball) != 4:
            raise ValueError("ball must contain x, y, vx and vy")
        body.extend(_message(1, b"".join(_double(i, value / 1000.0)
                                       for i, value in enumerate(ball, 1))))
    for is_yellow, positions in ((False, blue or {}), (True, yellow or {})):
        for number in positions:
            _robot_id(number)
            if number >= robot_count:
                raise ValueError("replacement ID exceeds configured robot_count")
        ids = range(robot_count) if disable_others else positions
        for number in ids:
            enabled = number in positions
            x, y, angle = positions.get(number, (0.0, 0.0, 0.0))
            robot = (_double(1, x / 1000.0) + _double(2, y / 1000.0)
                     + _double(3, math.degrees(angle)) + _int(4, number)
                     + _int(5, is_yellow) + _int(6, enabled))
            body.extend(_message(2, robot))
    return _message(2, bytes(body))


def robot_command_packet(
    robot_id: int = YELLOW_GOALIE, *, yellow: bool = True,
    vt: float = 0.0, vn: float = 0.0, vw: float = 0.0,
    kick_speed: float = 0.0, spinner: bool = False,
    protocol: str = "classic", timestamp: float = 0.0,
) -> bytes:
    """Encode a robot-local velocity command; vt/vn m/s, vw rad/s.

    ``classic`` sends grSim_Robot_Command. ``new`` sends ZSS.New.Robot_Command
    through grSim_Packet_new with CMD_VEL (2), KICK (1), and mm/s velocity.
    The selected protocol must be verified against the live grSim build.
    """
    _robot_id(robot_id)
    if kick_speed < 0:
        raise ValueError("kick_speed must be nonnegative")
    if protocol == "classic":
        robot = (_int(1, robot_id) + _float(2, kick_speed) + _float(3, 0)
                 + _float(4, vt) + _float(5, vn) + _float(6, vw)
                 + _int(7, spinner) + _int(8, False))
        commands = (_double(1, timestamp) + _int(2, yellow)
                    + _message(3, robot))
    elif protocol == "new":
        velocity = (_float(1, vt * 1000) + _float(2, vn * 1000)
                    + _int(3, False) + _float(4, vw))
        robot = (_int(1, robot_id) + _int(2, 1 if kick_speed else 0)
                 + _float(3, kick_speed * 1000) + _float(4, 0)
                 + _float(5, 3 if spinner else 0) + _int(6, True)
                 + _int(7, 2) + _message(8, velocity))
        commands = (_double(1, timestamp) + _int(2, yellow)
                    + _message(3, _message(1, robot)))
    else:
        raise ValueError("protocol must be 'classic' or 'new'")
    return _message(1, commands)


goalie_command_packet = robot_command_packet


def referee_packet(
    command: str | int, *, counter: int = 1, packet_timestamp: int | None = None,
    command_timestamp: int | None = None, yellow_goalie: int = YELLOW_GOALIE,
    blue_goalie: int = 0, blue_positive_half: bool = False,
) -> bytes:
    """Encode an initialized SSL Referee packet, including required team fields.

    For a kickoff send PREPARE_KICKOFF_BLUE then NORMAL_START with incremented
    command counters. Keep repeating the current packet while a case runs.
    """
    if isinstance(command, str):
        command = REFEREE_COMMANDS[command.upper()]
    if command not in REFEREE_COMMANDS.values() or counter < 0:
        raise ValueError("invalid referee command or counter")
    packet_timestamp = (time.time_ns() // 1000 if packet_timestamp is None
                        else packet_timestamp)
    command_timestamp = (packet_timestamp if command_timestamp is None
                         else command_timestamp)

    def team(name: str, goalie: int, count: int) -> bytes:
        _robot_id(goalie)
        return (_text(1, name) + _int(2, 0) + _int(3, 0) + _int(5, 0)
                + _int(6, 0) + _int(7, 0) + _int(8, goalie) + _int(13, count))

    return (_int(1, packet_timestamp) + _int(2, 1) + _int(4, command)
            + _int(5, counter) + _int(6, command_timestamp)
            + _message(7, team("TrainingGoalie", yellow_goalie, 1))
            + _message(8, team("BlueAttack2026", blue_goalie, 2))
            + _int(10, blue_positive_half))


def send_local_packet(payload: bytes, port: int = COMMAND_PORT) -> int:
    """Send to the local simulator/referee only; never the physical radio path."""
    if port not in (COMMAND_PORT, REFEREE_PORT):
        raise ValueError("only local simulation command/referee ports are allowed")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        return sender.sendto(payload, ("127.0.0.1", port))


def _read_varint(payload: bytes, position: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 70, 7):
        if position >= len(payload):
            raise ValueError("truncated varint")
        byte = payload[position]
        position += 1
        value |= (byte & 127) << shift
        if not byte & 128:
            return value, position
    raise ValueError("oversized varint")


def fields(payload: bytes) -> dict[int, list[object]]:
    """Decode protobuf scalar fields and retain nested messages as bytes."""
    result: dict[int, list[object]] = {}
    position = 0
    while position < len(payload):
        key, position = _read_varint(payload, position)
        number, wire = key >> 3, key & 7
        if number == 0:
            raise ValueError("zero protobuf field number")
        if wire == 0:
            value, position = _read_varint(payload, position)
        elif wire in (1, 5):
            width = 8 if wire == 1 else 4
            if position + width > len(payload):
                raise ValueError("truncated numeric field")
            value = struct.unpack_from("<d" if wire == 1 else "<f", payload,
                                       position)[0]
            position += width
        elif wire == 2:
            length, position = _read_varint(payload, position)
            if position + length > len(payload):
                raise ValueError("truncated message field")
            value = payload[position:position + length]
            position += length
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        result.setdefault(number, []).append(value)
    return result


def _one(values: dict[int, list[object]], number: int, default=0):
    return values.get(number, [default])[0]


def decode_vision_packet(payload: bytes) -> dict | None:
    """Decode raw SSL_WrapperPacket multicast; coordinates remain in mm.

    A geometry-only packet returns None. Camera packets are independent; callers
    should merge current robot IDs across cameras, not treat one camera as a
    complete world frame.
    """
    packet = fields(payload)
    if 1 not in packet:
        return None
    detection = fields(_one(packet, 1))
    result = {"frame_number": _one(detection, 1),
              "t_capture": _one(detection, 2),
              "t_sent": _one(detection, 3),
              "camera_id": _one(detection, 4), "balls": [],
              "blue": {}, "yellow": {}}
    for ball_payload in detection.get(5, []):
        ball = fields(ball_payload)
        result["balls"].append({"confidence": _one(ball, 1),
                                "x": _one(ball, 3), "y": _one(ball, 4),
                                "z": _one(ball, 5)})
    for number, name in ((6, "yellow"), (7, "blue")):
        for robot_payload in detection.get(number, []):
            robot = fields(robot_payload)
            robot_id = int(_one(robot, 2))
            result[name][robot_id] = {"confidence": _one(robot, 1),
                                     "x": _one(robot, 3),
                                     "y": _one(robot, 4),
                                     "orientation": _one(robot, 5)}
    return result


def decode_status_packet(payload: bytes) -> dict[int, dict]:
    """Decode both old bool status and new float status by robot ID.

    grSim's configured blue/yellow status ports are 30011/30012. The enclosing
    Robots_Status uses repeated field 1, and each robot has ID 1, infrared 2,
    flat_kick 3, chip_kick 4. Nonzero float infrared is treated as detected.
    """
    status = fields(payload)
    result = {}
    for robot_payload in status.get(1, []):
        if not isinstance(robot_payload, bytes):
            continue
        robot = fields(robot_payload)
        result[int(_one(robot, 1))] = {
            "infrared": bool(_one(robot, 2)),
            "flat_kick": bool(_one(robot, 3)),
            "chip_kick": bool(_one(robot, 4)),
        }
    return result


def status_forward_packets(payload: bytes) -> dict[int, bytes]:
    """Convert actual grSim feedback to Medusa's standalone new status packets.

    The shipped grSim emits boolean IR/kick fields. The shipped Client expects
    float fields and silently loses those booleans. This pure converter supports
    an optional simulation-only feedback bridge: receive original grSim status,
    then send each returned payload to Medusa blue 60101 or yellow 60102.
    It preserves feedback reported by grSim; it never invents a ball contact.
    """
    return {
        number: (_int(1, number) + _float(2, float(status["infrared"]))
                 + _float(3, float(status["flat_kick"]))
                 + _float(4, float(status["chip_kick"])))
        for number, status in decode_status_packet(payload).items()
    }


class VisionReceiver:
    """Optional raw vision listener; construct only for an authorized live run."""

    def __init__(self, timeout: float = 1.0, interface: str = "0.0.0.0"):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM,
                                    socket.IPPROTO_UDP)
        try:
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.socket.bind(("", VISION_PORT))
            membership = socket.inet_aton(VISION_GROUP) + socket.inet_aton(interface)
            self.socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                   membership)
            self.socket.settimeout(timeout)
        except BaseException:
            self.socket.close()
            raise

    def receive(self) -> dict | None:
        payload, _ = self.socket.recvfrom(65535)
        return decode_vision_packet(payload)

    def close(self):
        self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
