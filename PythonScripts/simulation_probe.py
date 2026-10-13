"""本机链路诊断，非比赛策略：观察原始视觉，或低速直行后停车。

默认不初始化 C++ SDK；--processed 仅从 SDK 读取融合视觉，不执行原生运动规划。
用于区分“模拟器没走”和“规划/转发链路没有正确驱动车辆”。
协议来源：RoboCup-SSL/ssl-vision messages_robocup_ssl_detection.proto；
RoboCup-SSL/grSim grSim_Commands.proto。只支持这两个协议所用的基础 wire 类型。
"""
import argparse
import json
import math
import os
from pathlib import Path
import socket
import struct
import threading
import time
from sim_tools import field, send, stop_robots


def fields(data):
    pos = 0
    def read_varint():
        nonlocal pos
        value = shift = 0
        while pos < len(data) and shift < 70:
            byte = data[pos]
            pos += 1
            value |= (byte & 127) << shift
            if not byte & 128:
                return value
            shift += 7
        raise ValueError('truncated/invalid varint')
    while pos < len(data):
        key = read_varint()
        number, wire = key >> 3, key & 7
        if number == 0:
            raise ValueError('invalid field number')
        if wire == 0:
            value = read_varint()
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            if pos+size > len(data): raise ValueError('truncated fixed value')
            value = struct.unpack('<d' if wire == 1 else '<f', data[pos:pos+size])[0]
            pos += size
        elif wire == 2:
            size = read_varint()
            if pos+size > len(data): raise ValueError('truncated message')
            value = data[pos:pos+size]
            pos += size
        else:
            raise ValueError('unsupported wire type')
        yield number, value


def robot_detections(packet, team, number):
    for tag, frame in fields(packet):
        if tag != 1 or not isinstance(frame, bytes): continue
        camera, robots = None, []
        for key, value in fields(frame):
            if key == 4: camera = value
            elif key == (6 if team == 'yellow' else 7): robots.append(value)
        for robot in robots:
            values = dict(fields(robot))
            if values.get(2) == number and 3 in values and 4 in values:
                yield camera, (values[3], values[4], values.get(5, 0))


def velocity(team, number, speed):
    robot = field(1, number)
    for n in range(2, 7):
        robot += field(n, speed if n == 4 else 0., 5)
    robot += field(7, 0)+field(8, 0)
    commands = field(1, time.time(), 1)+field(2, team == 'yellow')+field(3, robot, 2)
    send(field(1, commands, 2))


def drive_pulse(team, number, seconds, cancelled):
    """发送有时限的低速脉冲；即使主线程堵在 SDK 收帧，也独立到时停车。"""
    end = time.monotonic()+seconds
    try:
        while time.monotonic() < end and not cancelled.is_set():
            velocity(team,number,.2)
            cancelled.wait(.02)
    finally:
        for _ in range(3): stop_robots(team,[number])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-practice', action='store_true', required=True)
    parser.add_argument('--drive', action='store_true', help='Drive at 0.2m/s for at most 3 seconds, then stop')
    parser.add_argument('--processed', action='store_true', help='Read Client processed vision instead of raw multicast (blue only)')
    parser.add_argument('--team', choices=('blue','yellow'), default='blue')
    parser.add_argument('--robot', type=int, default=0)
    parser.add_argument('--seconds', type=float, default=3)
    args = parser.parse_args()
    if not 0 <= args.robot < 16 or not .5 <= args.seconds <= 10 or (args.drive and args.seconds > 3):
        parser.error('invalid robot/duration')
    lock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    if args.drive:
        lock.bind(('127.0.0.1',45990 if args.team == 'blue' else 45991))
    receiver = None
    if args.processed:
        if args.team != 'blue': parser.error('processed probe is blue-only')
        zbin = Path(__file__).resolve().parents[1]
        os.chdir(zbin)
        dll_handle = os.add_dll_directory(str(zbin))
        import CppPackage as C
        runner = C.SystemRunner.Instance()
        runner.init()
        from Vision import Player
    else:
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        receiver.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        receiver.bind(('',10020))
        receiver.setsockopt(socket.IPPROTO_IP,socket.IP_ADD_MEMBERSHIP,
                            socket.inet_aton('224.5.23.2')+socket.inet_aton('0.0.0.0'))
        receiver.settimeout(.05)
    poses = {}
    start = time.monotonic()
    cancelled = threading.Event()
    pulse = threading.Thread(target=drive_pulse,args=(args.team,args.robot,args.seconds,cancelled),daemon=True) if args.drive else None
    if pulse is not None: pulse.start()
    try:
        while time.monotonic()-start < args.seconds:
            if args.processed:
                runner.preDecision()
                if Player.isValid(args.robot):
                    point = Player.Pos(args.robot)
                    poses.setdefault('processed',[]).append((point.x(),point.y(),Player.direction(args.robot)))
                continue
            try:
                packet,_ = receiver.recvfrom(65535)
            except socket.timeout:
                continue
            for camera, pose in robot_detections(packet,args.team,args.robot):
                poses.setdefault(camera,[]).append(pose)
    finally:
        cancelled.set()
        if pulse is not None: pulse.join(timeout=1)
        if args.drive:
            for _ in range(3): stop_robots(args.team,[args.robot])
        if receiver is not None: receiver.close()
        lock.close()
    result = {'team':args.team,'robot':args.robot,'drive':args.drive,'processed':args.processed,'seconds':args.seconds,'cameras':{}}
    for camera, samples in poses.items():
        result['cameras'][camera] = {'samples':len(samples),'first':samples[0],'last':samples[-1],
            'displacement_mm':math.dist(samples[0][:2],samples[-1][:2]),
            'max_displacement_mm':max(math.dist(samples[0][:2],p[:2]) for p in samples)}
    print(json.dumps(result,ensure_ascii=False),flush=True)
    if not poses: raise RuntimeError('No raw vision samples received; no conclusion about motion')


if __name__ == '__main__':
    if '--processed' not in __import__('sys').argv:
        main()
    else:
        # SDK 没有 shutdown；与 runner 相同绕过原生析构，异常仍返回失败。
        import sys
        try:
            main()
        except Exception:
            import traceback
            traceback.print_exc()
            sys.stdout.flush(); sys.stderr.flush()
            os._exit(1)
        sys.stdout.flush(); sys.stderr.flush()
        os._exit(0)
