"""本机原始视觉分流：grSim 10021 → Client 10020 + 观察器 10022。

原包不改写，不接触机器人命令。控制端口仅绑定回环；只允许本项目启动/停止。
模拟器配置不匹配时显式报错，绝不以控制循环编号替代视觉帧。
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

GROUP = '224.5.23.2'
CONTROL_PORT = 45993
INPUT_PORT = 10021
OUTPUT_PORTS = (10020, 10022)


def status(timeout=.15, command=b'status'):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(command, ('127.0.0.1', CONTROL_PORT))
            data, _ = sock.recvfrom(65535)
            reply = json.loads(data)
            return reply if reply.get('service') == 'src-vision-relay-v1' else None
        except (OSError, ValueError):
            return None


def ensure_relay():
    found = status()
    if found:
        return found
    root = Path(__file__).resolve().parents[2]
    (root/'logs').mkdir(exist_ok=True)
    with (root/'logs/vision-relay-console.txt').open('a',encoding='utf-8') as output:
        subprocess.Popen([sys.executable,'-u',str(Path(__file__).resolve()),'--serve'],
            cwd=root, stdout=output, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    until = time.perf_counter()+3
    while time.perf_counter() < until:
        found = status()
        if found:
            return found
        time.sleep(.05)
    raise RuntimeError('Vision relay failed; see logs/vision-relay-console.txt')


def serve():
    # 单实例控制端口不共享；两队进程同时请求启动时，只有一个能取得所有权。
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as control:
        control.bind(('127.0.0.1',CONTROL_PORT))
        if hasattr(socket,'SIO_UDP_CONNRESET'):
            control.ioctl(socket.SIO_UDP_CONNRESET,False)
        control.setblocking(False)
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_UDP) as receiver:
            receiver.bind(('',INPUT_PORT))
            receiver.setsockopt(socket.IPPROTO_IP,socket.IP_ADD_MEMBERSHIP,
                socket.inet_aton(GROUP)+socket.inet_aton('0.0.0.0'))
            receiver.settimeout(.02)
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_UDP) as sender:
                sender.setsockopt(socket.IPPROTO_IP,socket.IP_MULTICAST_TTL,1)
                packets = 0
                captures = {}
                from observation import decode_detection
                last_packet = None
                started = time.perf_counter()
                print('READY: vision 10021 -> 10020/10022',flush=True)
                while True:
                    try:
                        data, address = control.recvfrom(256)
                        reply = dict(service='src-vision-relay-v1',pid=os.getpid(),packets=packets,
                            input_port=INPUT_PORT,output_ports=OUTPUT_PORTS,
                            packet_age_s=None if last_packet is None else time.perf_counter()-last_packet)
                        if data == b'captures':
                            reply['captures'] = [dict(camera=c.camera,frame_id=c.frame_id,
                                capture_time=c.capture_time,receive_time=c.receive_time,
                                ball=None if c.ball is None else [c.ball.x,c.ball.y],
                                robots=[[team,n,p.x,p.y] for (team,n),p in c.robots.items()])
                                for c in captures.values()]
                        control.sendto(json.dumps(reply).encode(),address)
                        if data == b'stop':
                            return
                    except (BlockingIOError,ConnectionResetError):
                        # Windows 会把短命查询客户端关闭后的 ICMP 映射为下一次
                        # recvfrom 的10054；这不能导致整个 Client 视觉服务退出。
                        pass
                    try:
                        packet, _ = receiver.recvfrom(65535)
                    except socket.timeout:
                        # 未开模拟器时不会永远遗留服务；下次启动策略自动重建。
                        if time.perf_counter()-(last_packet or started) > 60:
                            return
                        continue
                    for port in OUTPUT_PORTS:
                        sender.sendto(packet,(GROUP,port))
                    last_packet = time.perf_counter()
                    try:
                        for capture in decode_detection(packet,last_packet):
                            captures[capture.camera] = capture
                    except (ValueError,TypeError):
                        pass
                    packets += 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve',action='store_true')
    parser.add_argument('--stop',action='store_true')
    args = parser.parse_args()
    if args.serve:
        serve()
    elif args.stop:
        print(json.dumps(status(command=b'stop')))
    else:
        print(json.dumps(ensure_relay()))
