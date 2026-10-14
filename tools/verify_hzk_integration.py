"""离线回归与只读视觉诊断；不启动比赛策略、不发送机器人运动命令。"""
import hashlib
import json
from pathlib import Path
import subprocess
import socket
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'ZBin/PythonScripts'
sys.path.insert(0,str(SCRIPTS))


def main():
    checks=[]
    for command in ([sys.executable,'-X','utf8','-m','unittest','test_match','test_v21','-q'],
                    [sys.executable,'-X','utf8','-m','unittest','discover','-s','tests','-q'],
                    [sys.executable,'-X','utf8','run_match.py','--config',str(ROOT/'configs/v2.1-foundation.json'),'--check']):
        r=subprocess.run(command,cwd=SCRIPTS,capture_output=True,text=True,encoding='utf-8',errors='replace')
        checks.append(dict(command=command,exit_code=r.returncode,output=r.stdout+r.stderr))
    from observation import RelayVision
    from vision_relay import status
    # 回归 Windows 短命 UDP 查询触发 ICMP 10054 的真实场景；只查询，不发运动。
    for _ in range(40):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as query:
            query.sendto(b'status',('127.0.0.1',45993))
    raw=RelayVision()
    frames=set()
    until=time.perf_counter()+2
    while time.perf_counter()<until:
        raw.poll()
        frames.update((c.camera,c.frame_id) for c in raw.captures.values())
        time.sleep(.01)
    paths=[p for p in SCRIPTS.rglob('*.py') if '.venv' not in p.parts and '__pycache__' not in p.parts]
    result=dict(offline_passed=all(c['exit_code']==0 for c in checks),checks=checks,
        vision=dict(distinct_frames_2s=len(frames),cameras=sorted({c for c,_ in frames}),
                    error=raw.error,relay=status(),short_lived_queries=40),
        source_sha256={p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        physical_acceptance_passed=False,
        limitation='Native failures persist; two completed open-goal diagnostic trials scored zero; not a validated match release.')
    out=ROOT/'验收记录/hzk-v3-integration-20261014/verification.json'
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(offline_passed=result['offline_passed'],vision=result['vision'],report=str(out)),ensure_ascii=False))
    for check in checks:
        if check['exit_code']: print(check['output'])
    return 0 if result['offline_passed'] else 1


if __name__=='__main__':
    sys.exit(main())
