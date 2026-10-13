"""Local grSim tools; never sends to a physical robot or a remote IP.

Wire layout: official grSim_{Packet,Replacement,Commands}.proto and SSL Referee.proto.
Replacement/referee commands are for practice only. The competition runner also
uses zero-velocity commands to stop its own robots in LOCAL simulation.
"""
import argparse
import socket
import struct
import time
import threading


def varint(value):
    data = bytearray()
    while value > 127:
        data.append((value & 127) | 128)
        value >>= 7
    data.append(value)
    return bytes(data)


def field(n, value, wire=0):
    key = varint((n << 3) | wire)
    if wire == 0:
        return key + varint(int(value))
    if wire == 1:
        return key + struct.pack('<d', value)
    if wire == 5:
        return key + struct.pack('<f', value)
    return key + varint(len(value)) + value


def send(data, port=20011):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(data, ('127.0.0.1', port))


def stop_robots(team, ids):
    commands = field(1, time.time(), 1) + field(2, team == 'yellow')
    for n in ids:
        robot = field(1, n)
        for f in range(2, 7):
            robot += field(f, 0.0, 5)
        robot += field(7, 0) + field(8, 0)
        commands += field(3, robot, 2)
    send(field(1, commands, 2))


def reset_scene(ball=(0, 0), attackers=(0, 1), goalie=2, team='blue', side='left', attacker_poses=None):
    """Replace practice scene: TWO attacking robots + ONE opposite goalkeeper."""
    sign = 1 if side == 'left' else -1
    b = field(1, ball[0], 1)+field(2, ball[1], 1)+field(3, 0.0, 1)+field(4, 0.0, 1)
    replacement = field(1, b, 2)
    for yellow in (False, True):
        ours = yellow == (team == 'yellow')
        for n in range(16):
            active = n in attackers if ours else n == goalie
            x, y, direction = -4.0, 2.7, 0.0
            if ours and n in attackers:
                x, y = (-1.0, -0.7) if n == attackers[0] else (-1.5, 1.2)
                if attacker_poses and n in attacker_poses:
                    # 仅供练习的接触射门诊断；坐标单位米，朝向单位度。
                    x, y, direction = attacker_poses[n]
            elif not ours and n == goalie:
                x, y, direction = 4.2, 0.0, 180.0
            robot = (field(1, sign*x, 1)+field(2, sign*y, 1)+field(3, direction+(180 if sign < 0 else 0), 1)
                     +field(4, n)+field(5, yellow)+field(6, active))
            replacement += field(2, robot, 2)
    send(field(2, replacement, 2))


def referee(command, counter=None):
    stamp = int(time.time()*1_000_000)
    counter = (stamp // 1000) & 0xffffffff if counter is None else counter
    team = field(1, b'Practice', 2)
    for n in (2, 3, 5, 6, 7, 8):
        team += field(n, 0)
    packet = (field(1, stamp)+field(2, 1)+field(4, command)+field(5, counter)
              +field(6, stamp)+field(7, team, 2)+field(8, team, 2))
    send(packet, 39991)
    send(packet, 39992)


class PracticeReferee:
    """Repeat the current LOCAL practice command, like a real referee transmitter.

    One UDP datagram is not reliable delivery. Counter stays constant for repeats,
    and changes only when the operator issues a new command.
    """
    def __init__(self):
        # 同一电脑只能有一个本工具裁判发送者，避免两个面板相互抢 HALT/START。
        # Client/官方裁判不使用此锁，正式比赛仍须手动关闭练习发送。
        self._owner = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._owner.bind(('127.0.0.1',45992))
        except OSError as exc:
            self._owner.close()
            raise RuntimeError('已有练习裁判发送器，请先停止/关闭另一个比赛面板') from exc
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._command = None
        self._counter = int(time.time()*1000) & 0xffffffff
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def set(self, command):
        if command not in (0, 1, 3):
            raise ValueError('Practice referee only supports HALT, STOP, FORCE START')
        with self._lock:
            self._command = command
            self._counter = (self._counter+1) & 0xffffffff
            referee(command, self._counter)

    def _run(self):
        while not self._closed.wait(.1):
            with self._lock:
                if self._command is not None:
                    referee(self._command, self._counter)

    def close(self):
        self._closed.set()
        self._thread.join(timeout=.5)
        self._owner.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='LOCAL PRACTICE ONLY; do not run during official matches')
    parser.add_argument('action', choices=('halt', 'stop', 'start', 'reset'))
    args = parser.parse_args()
    if args.action == 'reset':
        reset_scene()
    else:
        referee({'halt': 0, 'stop': 1, 'start': 3}[args.action])
