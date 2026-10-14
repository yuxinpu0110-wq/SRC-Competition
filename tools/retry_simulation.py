"""受控仿真重试；保留每次启动失败，不把重启后的成绩冒充全部尝试成功率。"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'ZBin/PythonScripts'
sys.path.insert(0,str(SCRIPTS))
from sim_tools import referee,stop_robots
from vision_relay import ensure_relay


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--goalie',choices=('official','none'),default='official')
    parser.add_argument('--attempts',type=int,default=6)
    parser.add_argument('--seconds',type=float,default=12)
    parser.add_argument('--config',type=Path,default=ROOT/'configs/v2.1-foundation.json')
    parser.add_argument('--starts',choices=('contact','approach','front'),default='contact')
    parser.add_argument('--passing',choices=('on','off'),default='off')
    args=parser.parse_args()
    if not 1<=args.attempts<=12 or not 2<=args.seconds<=30: parser.error('Bounded attempts/duration required')
    output=ROOT/'验收记录'/('empty-goal-fix-'+time.strftime('%Y%m%d-%H%M%S'))
    # 稀疏检出不含历史验收目录；首次测试要能自行创建父目录。
    output.mkdir(parents=True)
    summary=dict(goalie=args.goalie,attempts=[],complete=False)
    for number in range(1,args.attempts+1):
        # 分流器由测试父进程启动，不挂在稍后会被回收的原生策略子树下。
        # 否则一次原生失败回收子树后，连视觉链路也会丢失。
        ensure_relay()
        folder=output/f'attempt-{number:02}'
        folder.mkdir()
        command=[sys.executable,'-X','utf8',str(SCRIPTS/'benchmark_match.py'),
                 '--config',str(args.config.resolve()),'--seconds',str(args.seconds),
                 '--goalie',args.goalie,'--starts',args.starts,'--passing',args.passing,'--frame-cycle','paired']
        timed_out=False
        attempt_started=time.time()
        with (folder/'console.txt').open('w',encoding='utf-8') as log:
            process=subprocess.Popen(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
            try:
                code=process.wait(timeout=args.seconds*3+55)
            except subprocess.TimeoutExpired:
                timed_out=True
                subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True)
                code=process.wait(timeout=5)
                referee(0)
                stop_robots('blue',[0,1]);stop_robots('yellow',[2])
        report_path=ROOT/'logs/benchmark-report.json'
        own_report=report_path.exists() and report_path.stat().st_mtime>=attempt_started-.05
        report=json.loads(report_path.read_text(encoding='utf-8')) if own_report else {'error':'No report produced by this attempt'}
        if own_report: shutil.copy2(report_path,folder/'report.json')
        paths=[report.get('goalie_console'),report.get('goalie_telemetry')]
        paths+=report.get('attacker_consoles',[])+report.get('attacker_telemetries',[])
        for path in paths:
            if path and Path(path).exists(): shutil.copy2(path,folder/Path(path).name)
        record=dict(number=number,exit_code=code,timed_out=timed_out,complete=report.get('complete',False),
            valid_trials=sum(bool(t.get('valid')) for t in report.get('trials',[])),
            goals=sum(bool(t.get('goal_crossing')) for t in report.get('trials',[]) if t.get('valid')),
            error=report.get('error') or ('BOUNDED_ATTEMPT_TIMEOUT' if timed_out else None),
            elapsed_s=time.time()-attempt_started,report=str(folder/'report.json'))
        summary['attempts'].append(record)
        summary['complete']=bool(code==0 and report.get('complete'))
        (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps(record,ensure_ascii=False),flush=True)
        if summary['complete']: break
        # 独立看门狗在父进程丢失后最多3秒退出；等它释放端口再重启。
        time.sleep(3.2)
    print('Summary: '+str(output/'summary.json'),flush=True)
    return 0 if summary['complete'] else 1


if __name__=='__main__': sys.exit(main())
