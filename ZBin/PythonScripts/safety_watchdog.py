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

    def touch(self,now, fresh=True):
        if self.process.poll() is not None:
            self.error='WATCHDOG_EXITED'
        else:
            # alive证明父进程存活；fresh才许可停止重复停车。视觉中断不退出
            # worker，恢复后可以继续；只有父进程也消失才最终退出。
            self.socket.sendto(b'fresh' if fresh else b'alive',('127.0.0.1',self.port))

    def close(self):
        try:
            self.socket.sendto(b'close',('127.0.0.1',self.port))
        finally:
            self.socket.close()
            # 通知发送失败时仍让独立 worker 按原3秒失联期限停车退出。
            # 不强杀它来掩盖主进程的通信/原生异常。
            self.process.wait(timeout=3.5)


def worker(team,ids,timeout):
    port=45994 if team=='blue' else 45995
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.bind(('127.0.0.1',port))
        sock.settimeout(.025)
        last=alive=time.perf_counter()
        try:
            while True:
                try:
                    data,_=sock.recvfrom(32)
                    if data==b'close': return
                    if data in (b'fresh',b'alive'): alive=time.perf_counter()
                    if data==b'fresh': last=time.perf_counter()
                except socket.timeout:
                    pass
                age=time.perf_counter()-last
                if age>timeout: stop_robots(team,ids)
                if time.perf_counter()-alive>3: return
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
