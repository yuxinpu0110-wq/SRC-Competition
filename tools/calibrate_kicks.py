"""本机空门分层标定；复用比赛 State→Task→Skill 链路，失败尝试也归档。"""
import argparse
import itertools
import json
from pathlib import Path
import subprocess
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str((ROOT/'PythonScripts' if (ROOT/'PythonScripts').is_dir() else ROOT/'ZBin/PythonScripts')))
from run_match import load_config, ZBIN


def diagnostic_config(base, robot, power):
    """Static placement avoids pushing the ball before the measured release."""
    c=dict(base)
    c.update(attackers=[robot,1-robot],calibration_power=power,
             calibration_placement_distance_mm=110,settle_on_contact=True,
             enable_pass=False,enable_feint=False,kick_max_attempts=1)
    return c


def own_report(path, config, started):
    """Never count an earlier successful run as a new failed run's result."""
    if not path.exists() or path.stat().st_mtime < started-.05:
        return None
    data=json.loads(path.read_text(encoding='utf-8'))
    if data.get('runtime_manifest',{}).get('config_path') != str(config.resolve()):
        return None
    return data


def archive_logs(data, folder):
    paths=[data.get('goalie_console'),data.get('goalie_telemetry')]
    paths+=data.get('attacker_consoles',[])+data.get('attacker_telemetries',[])+data.get('raw_capture_logs',[])
    for value in paths:
        if value:
            path=Path(value).resolve()
            if path.is_relative_to((ROOT/'logs').resolve()) and path.is_file():
                shutil.copy2(path,folder/path.name)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-practice',action='store_true')
    parser.add_argument('--plan-only',action='store_true',help='No subprocesses, file writes or robot commands')
    parser.add_argument('--scene',choices=('contact','rolling'),default='contact',help='rolling starts at midfield; no goalkeeper')
    parser.add_argument('--powers',type=int,nargs='+',default=[2500,3500,4500,5500,6000])
    parser.add_argument('--robots',type=int,nargs='+',choices=(0,1),default=[0,1])
    parser.add_argument('--repeats',type=int,default=10)
    parser.add_argument('--seconds',type=float,default=3)
    args=parser.parse_args()
    if not 1 <= args.repeats <= 30 or not .5 <= args.seconds <= 15 or any(not 1000<=p<=6000 for p in args.powers):
        parser.error('invalid bounded trial parameters')
    if args.plan_only:
        print(json.dumps(dict(stage='static_'+args.scene+'_diagnostic',robots=args.robots,powers=args.powers,
            trials_per_batch=3*args.repeats,placement_mm=110,settle_on_contact=True,
            rolling_range_acceptance=False,physical_acceptance_passed=False,usable_for_planning=False),indent=2))
        return 0
    if not args.local_practice:
        parser.error('Execution requires --local-practice; use --plan-only for offline inspection')
    out=ROOT/'验收记录'/('v21-calibration-'+time.strftime('%Y%m%d-%H%M%S'))
    out.mkdir()
    report=dict(complete=False,stage='static_'+args.scene+'_diagnostic',attempts=[],
                rolling_range_acceptance=False,physical_acceptance_passed=False,usable_for_planning=False)
    summary=out/'summary.json'
    from simulator_context import capture_context,settings_path
    try:
        report['saved_context']=capture_context(settings_path(dict(simulator_settings_path='@user-grsim')),ZBIN)
    except (OSError,ValueError,SyntaxError) as exc:
        report['saved_context_error']=str(exc)
    for robot,power in itertools.product(args.robots,args.powers):
        c=load_config(ROOT/'configs/v2.1-calibration.json')
        c=diagnostic_config(c,robot,power)
        cfg=out/f'robot-{robot}-power-{power}.json'
        cfg.write_text(json.dumps(c,indent=2),encoding='utf-8')
        console=out/f'robot-{robot}-power-{power}-console.txt'
        attempt=dict(robot=robot,power=power,config=str(cfg),console=str(console),exit_code=None)
        report['attempts'].append(attempt)
        summary.write_text(json.dumps(report,indent=2),encoding='utf-8')
        # Keep relay outside the SDK child tree so native failure cannot take it down.
        from vision_relay import ensure_relay
        ensure_relay()
        started=time.time()
        attempt['timed_out']=False
        with console.open('w',encoding='utf-8') as stream:
            child=subprocess.Popen([sys.executable,'-X','utf8','-X','faulthandler',str(ZBIN/'PythonScripts/benchmark_match.py'),
                '--config',str(cfg),'--goalie','none','--starts',args.scene,'--passing','off',
                '--repeats',str(args.repeats),'--seconds',str(args.seconds)],cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
            try:
                attempt['exit_code']=child.wait(timeout=55+3*args.repeats*(args.seconds+10))
            except subprocess.TimeoutExpired:
                attempt['timed_out']=True
                # Only terminate this test's child tree; never kill Client/grSim.
                subprocess.run(['taskkill','/PID',str(child.pid),'/T','/F'],capture_output=True)
                attempt['exit_code']=child.wait(timeout=5)
                from sim_tools import referee,stop_robots
                referee(0);stop_robots('blue',[robot,1-robot])
        benchmark=ROOT/'logs/benchmark-report.json'
        data=own_report(benchmark,cfg,started)
        if data is not None:
            (out/f'robot-{robot}-power-{power}-report.json').write_text(json.dumps(data,indent=2),encoding='utf-8')
            attempt['benchmark_complete']=data.get('complete',False)
            attempt['valid_trials']=sum(t.get('valid',False) for t in data.get('trials',[]))
            archive_logs(data,out)
        summary.write_text(json.dumps(report,indent=2),encoding='utf-8')
        if attempt['exit_code'] or not attempt.get('benchmark_complete'):
            # 崩溃、视觉缺失或超速后停批次，不静默自动重启刷成功率。
            print(f'Calibration stopped on failure; full attempt saved: {summary}')
            return attempt['exit_code'] or 1
    report['complete']=True
    summary.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(f'Calibration trials complete; physical acceptance still requires analysis: {summary}')
    return 0


if __name__=='__main__':
    sys.exit(main())
