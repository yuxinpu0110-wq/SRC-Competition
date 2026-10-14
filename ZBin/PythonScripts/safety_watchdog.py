"""独立进程看门狗：SDK 阻塞/崩溃后仍向本机仿真发送停车。"""
import argparse
import socket
import subprocess
import sys
import time
from pathlib import Path
from sim_tools import stop_robots


class DecisionWatchdog:
    def __init__(self,team,ids,timeout=.2):
        self.port=45994 if team=='blue' else 45995
        self.error=None
        self.socket=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        self.process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),
            '--worker','--team',team,'--ids',','.join(map(str,ids)),'--timeout',str(timeout)],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

    def touch(self,now):
        if self.process.poll() is not None:
            self.error='WATCHDOG_EXITED'
        else:
            self.socket.sendto(b'fresh',('127.0.0.1',self.port))

    def close(self):
        self.socket.sendto(b'close',('127.0.0.1',self.port))
        try:
            self.process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=1)
        self.socket.close()


def worker(team,ids,timeout):
    port=45994 if team=='blue' else 45995
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1',port))
        sock.settimeout(.025)
        last=time.perf_counter()
        try:
            while True:
                try:
                    data,_=sock.recvfrom(32)
                    if data==b'close': return
                    if data==b'fresh': last=time.perf_counter()
                except socket.timeout:
                    pass
                age=time.perf_counter()-last
                if age>timeout: stop_robots(team,ids)
                if age>3: return
        finally:
            stop_robots(team,ids)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--worker',action='store_true',required=True)
    parser.add_argument('--team',choices=('blue','yellow'),required=True)
    parser.add_argument('--ids',required=True)
    parser.add_argument('--timeout',type=float,default=.2)
    args=parser.parse_args()
    worker(args.team,[int(n) for n in args.ids.split(',')],args.timeout)
