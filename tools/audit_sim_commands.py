"""透明诊断转发：临时改grSim接收端口20012后，转发20011→20012。

仅用于有时限本机诊断，结束必须将模拟器端口恢复20011。
不修改命令字段、模型或门将参数。
"""
import argparse
import json
from pathlib import Path
import socket
import sys
import time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ZBin/PythonScripts'))
from simulation_probe import fields


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--seconds',type=float,default=90)
    parser.add_argument('--listen',type=int,default=20011)
    parser.add_argument('--forward',type=int,default=20012)
    args=parser.parse_args()
    if not 1<=args.seconds<=180: parser.error('Bounded duration required')
    if not (1<=args.listen<=65535 and 1<=args.forward<=65535) or args.listen==args.forward:
        parser.error('Distinct valid UDP ports required')
    path=ROOT/'logs'/('command-audit-'+time.strftime('%Y%m%d-%H%M%S')+'.jsonl')
    total=kicks=0
    started=time.perf_counter()
    with socket.socket(socket.AF_INET6,socket.SOCK_DGRAM) as receiver, socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sender, path.open('w',encoding='utf-8') as out:
        receiver.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_V6ONLY,0)
        receiver.bind(('::',args.listen))
        receiver.settimeout(.05)
        print(f'READY: command audit {args.listen} -> {args.forward}; '+str(path),flush=True)
        while time.perf_counter()-started<args.seconds:
            try: packet,_=receiver.recvfrom(65535)
            except socket.timeout: continue
            sender.sendto(packet,('127.0.0.1',args.forward))
            total+=1
            try:
                for tag,commands in fields(packet):
                    if tag!=1 or not isinstance(commands,bytes): continue
                    for number,value in fields(commands):
                        if number!=3 or not isinstance(value,bytes): continue
                        robot=dict(fields(value))
                        if robot.get(2,0)>0 or robot.get(3,0)>0:
                            kicks+=1
                            out.write(json.dumps(dict(now=time.perf_counter(),robot_fields=robot))+'\n');out.flush()
            except (ValueError,TypeError): pass
    print(json.dumps(dict(total_packets=total,kick_packets=kicks,log=str(path))),flush=True)


if __name__=='__main__': main()
