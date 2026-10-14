"""本机空门分层标定；复用比赛 State→Task→Skill 链路，失败尝试也归档。"""
import argparse
import itertools
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str((ROOT/'PythonScripts' if (ROOT/'PythonScripts').is_dir() else ROOT/'ZBin/PythonScripts')))
from run_match import load_config, ZBIN


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-practice',action='store_true',required=True)
    parser.add_argument('--powers',type=int,nargs='+',default=[2500,3500,4500,5500,6000])
    parser.add_argument('--robots',type=int,nargs='+',choices=(0,1),default=[0,1])
    parser.add_argument('--repeats',type=int,default=10)
    parser.add_argument('--seconds',type=float,default=3)
    args=parser.parse_args()
    if not 1 <= args.repeats <= 30 or not .5 <= args.seconds <= 10 or any(not 1000<=p<=6000 for p in args.powers):
        parser.error('invalid bounded trial parameters')
    out=ROOT/'验收记录'/('v21-calibration-'+time.strftime('%Y%m%d-%H%M%S'))
    out.mkdir()
    report=dict(complete=False,stage='static_open_goal',attempts=[],physical_acceptance_passed=False)
    summary=out/'summary.json'
    for robot,power in itertools.product(args.robots,args.powers):
        c=load_config(ROOT/'configs/v2.1-calibration.json')
        c.update(attackers=[robot,1-robot],calibration_power=power)
        cfg=out/f'robot-{robot}-power-{power}.json'
        cfg.write_text(json.dumps(c,indent=2),encoding='utf-8')
        console=out/f'robot-{robot}-power-{power}-console.txt'
        attempt=dict(robot=robot,power=power,config=str(cfg),console=str(console),exit_code=None)
        report['attempts'].append(attempt)
        summary.write_text(json.dumps(report,indent=2),encoding='utf-8')
        with console.open('w',encoding='utf-8') as stream:
            result=subprocess.run([sys.executable,'-X','utf8',str(ZBIN/'PythonScripts/benchmark_match.py'),
                '--config',str(cfg),'--goalie','none','--starts','contact','--passing','off',
                '--repeats',str(args.repeats),'--seconds',str(args.seconds)],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
        attempt['exit_code']=result.returncode
        benchmark=ROOT/'logs/benchmark-report.json'
        if benchmark.exists():
            data=json.loads(benchmark.read_text(encoding='utf-8'))
            if data.get('runtime_manifest',{}).get('config_path')==str(cfg.resolve()):
                (out/f'robot-{robot}-power-{power}-report.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
                attempt['benchmark_complete']=data.get('complete',False)
                attempt['valid_trials']=sum(t.get('valid',False) for t in data.get('trials',[]))
        summary.write_text(json.dumps(report,indent=2),encoding='utf-8')
        if result.returncode:
            # 崩溃、视觉缺失或超速后停批次，不静默自动重启刷成功率。
            print(f'Calibration stopped on failure; full attempt saved: {summary}')
            return result.returncode
    report['complete']=True
    summary.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(f'Calibration trials complete; physical acceptance still requires analysis: {summary}')
    return 0


if __name__=='__main__':
    sys.exit(main())
