"""诊断用原始视觉转发：输入10021→Client10020，原包不改写；不发送机器人命令。"""
import argparse
import json
from pathlib import Path
import socket
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds',type=float,default=120)
    args=parser.parse_args()
    if not 1<=args.seconds<=180:
        parser.error('duration must be 1..180 seconds')
    receiver=socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_UDP)
    receiver.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    receiver.bind(('',10021))
    receiver.setsockopt(socket.IPPROTO_IP,socket.IP_ADD_MEMBERSHIP,
                       socket.inet_aton('224.5.23.2')+socket.inet_aton('0.0.0.0'))
    receiver.settimeout(.1)
    sender=socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_UDP)
    sender.setsockopt(socket.IPPROTO_IP,socket.IP_MULTICAST_TTL,1)
    packets=0
    started=time.perf_counter()
    print('Vision mirror READY: multicast10021 -> multicast10020; no robot commands',flush=True)
    try:
        while time.perf_counter()-started<args.seconds:
            try:
                data,_=receiver.recvfrom(65535)
            except socket.timeout:
                continue
            sender.sendto(data,('224.5.23.2',10020))
            packets+=1
    except KeyboardInterrupt:
        pass
    finally:
        receiver.close(); sender.close()
        root=Path(__file__).resolve().parents[1]
        (root/'验收记录/v21-vision-mirror.json').write_text(json.dumps(dict(packets=packets,
            duration_s=time.perf_counter()-started,input_port=10021,output_port=10020,
            payload_modified=False,robot_commands_sent=False),indent=2),encoding='utf-8')
        print(f'Vision mirror stopped: {packets} packets',flush=True)


if __name__=='__main__':
    main()
