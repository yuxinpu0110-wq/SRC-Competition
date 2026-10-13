"""Repeatable LOCAL simulation smoke benchmark; never an official win-rate measurement.

Requires one connected Client + one grSim. Stops only the child strategy it starts.
Starts an official Goalie2022 via run_match --config opponent.json. Resets the scene.
"""
import argparse
import hashlib
import math
import json
from pathlib import Path
import subprocess
import sys
import time
from run_match import ROOT, ZBIN, load_config
from sim_tools import PracticeReferee, reset_scene, stop_robots


def kill_child(p):
    if p is not None and p.poll() is None:
        subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'], capture_output=True)
        p.wait(timeout=5)


def complete_rows(path):
    """Ignore a writer's unfinished trailing JSON line, never conceal other errors."""
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if not line.endswith('\n'):
                break
            yield json.loads(line)


def launch(config, label):
    f = (ROOT/'logs'/f'benchmark-{label}-{time.time_ns()}-console.txt').open('w', encoding='utf-8')
    p = subprocess.Popen([sys.executable, '-X', 'utf8', '-X', 'faulthandler', '-u', str(ZBIN/'PythonScripts'/'run_match.py'), '--config', str(config)],
                         cwd=ZBIN, stdout=f, stderr=subprocess.STDOUT)
    return p, f


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seconds', type=float, default=20)
    parser.add_argument('--skills', nargs='+', choices=['targeted', 'baseline'], default=['targeted', 'baseline'])
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--passing', choices=('on', 'off'), help='Explicit passing A/B; otherwise uses match.json')
    parser.add_argument('--feint', choices=('on', 'off'), default='off', help='Requires --passing on; candidate feature')
    parser.add_argument('--dss', choices=('on', 'off'), help='Diagnostic native DSS A/B; default unchanged')
    parser.add_argument('--dispatch', choices=('state_machine',), help='Only official state_machine dispatch is supported')
    parser.add_argument('--qt-app', action='store_true', help='Diagnostic explicit Qt application in both controllers')
    parser.add_argument('--shoot-control', choices=('legacy', 'precision'), default='legacy',
                        help='Same-scene A/B of old/new targeted contact control')
    parser.add_argument('--goalie', choices=('official', 'stationary'), default='official',
                        help='stationary isolates aiming; never count it as a moving-goalie test')
    parser.add_argument('--starts', choices=('approach','contact'), default='approach',
                        help='contact pre-aligns the striker near ball: aiming-only diagnostic, NOT full attack')
    parser.add_argument('--frame-cycle', choices=('paired','legacy'), default='paired',
                        help='paired always completes pre/post; legacy reproduces old skipped idle postDecision')
    args = parser.parse_args()
    if args.repeats < 1 or args.seconds <= 0:
        parser.error('repeats and seconds must be positive')
    from match_processes import check_duplicates, process_count
    check_duplicates()
    if process_count('Client.exe') != 1 or process_count('grSim.exe') != 1:
        raise RuntimeError('Benchmark requires ONE connected Client and ONE simulator')
    (ROOT/'logs').mkdir(exist_ok=True)
    c = load_config(ROOT/'match.json')
    c['shoot_control'] = args.shoot_control
    c['complete_frame'] = args.frame_cycle == 'paired'
    if args.passing:
        c['enable_pass'] = args.passing == 'on'
    c['enable_feint'] = args.feint == 'on'
    if c['enable_feint'] and not c.get('enable_pass', False):
        parser.error('--feint on requires passing enabled')
    if args.dss:
        c['enable_dss'] = args.dss == 'on'
    if args.dispatch:
        c['task_dispatch'] = args.dispatch
    if args.qt_app:
        c['qt_application'] = True
    if c['team'] != 'blue' or c['side'] != 'left' or c['profile'] != 'qualifier':
        raise ValueError('This reproducible benchmark requires blue / left / qualifier')
    report = {'description': 'Local practice, three fixed ball starts. Not official score/win rate.',
              'goalie_mode': args.goalie, 'shoot_control': args.shoot_control,
              'start_mode': args.starts,
              'complete': False, 'started_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'config': c,
              'source_sha256': {name: hashlib.sha256((ZBIN/'PythonScripts'/name).read_bytes()).hexdigest()
                                for name in ['run_match.py', 'match_tactics.py', 'Play/Normal/Normalplay.py',
                                             'RoleMatch_LuaStyle/Skills/CompetitionSkills.py', 'shot_accuracy.py',
                                             'ball_control.py', 'sim_tools.py', 'benchmark_match.py']}, 'trials': []}
    report_path = ROOT/'logs'/'benchmark-report.json'
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    practice = PracticeReferee()
    referee = practice.set
    referee(0)
    goalie_path = ROOT/'opponent.json'
    if args.dispatch or args.qt_app or args.frame_cycle:
        goalie_config = load_config(goalie_path)
        goalie_config['complete_frame'] = args.frame_cycle == 'paired'
        if args.dispatch:
            goalie_config['task_dispatch'] = args.dispatch
        if args.qt_app:
            goalie_config['qt_application'] = True
        goalie_path = ROOT/'benchmark-goalie.json'
        goalie_path.write_text(json.dumps(goalie_config, indent=2), encoding='utf-8')
    goalie, goalie_log = launch(goalie_path, 'goalie') if args.goalie == 'official' else (None, None)
    report['goalie_config'] = load_config(goalie_path)
    report['goalie_console'] = goalie_log.name if goalie_log else None
    try:
        time.sleep(2)
        for skill in args.skills:
            cfg = dict(c, shoot_skill=skill, log_interval=0.02)
            path = ROOT/f'benchmark-{skill}.json'
            path.write_text(json.dumps(cfg, indent=2), encoding='utf-8')
            p, output = launch(path, skill)
            report.setdefault('attacker_consoles', []).append(output.name)
            try:
                time.sleep(2)
                if p.poll() is not None or (goalie is not None and goalie.poll() is not None):
                    raise RuntimeError('A strategy failed; inspect logs/benchmark-*-console.txt')
                logs = sorted((ROOT/'logs').glob('*-blue.jsonl'), key=lambda p:p.stat().st_mtime)
                if not logs:
                    raise RuntimeError('No telemetry: connect Client Vision first')
                telemetry = logs[-1]
                yellow_logs = sorted((ROOT/'logs').glob('*-yellow.jsonl'), key=lambda p:p.stat().st_mtime)
                goalie_telemetry = yellow_logs[-1] if yellow_logs and goalie is not None else None
                starts = [(0,0),(.8,1),(.8,-1)] if args.starts == 'approach' else [(2.8,0),(2.8,.8),(2.8,-.8)]
                for trial, ball in enumerate(starts * args.repeats):
                    referee(0)
                    time.sleep(.25)
                    poses = None
                    if args.starts == 'contact':
                        from match_tactics import Point, Tactics
                        aim,_ = Tactics(c).shot(Point(ball[0]*1000,ball[1]*1000),[Point(4200,0)])
                        angle = math.atan2(aim.y-ball[1]*1000,aim.x-ball[0]*1000)
                        poses = {c['attackers'][0]:(ball[0]-.095*math.cos(angle),ball[1]-.095*math.sin(angle),math.degrees(angle)),
                                 c['attackers'][1]:(1.5,1.5,0)}
                    reset_scene(ball=ball, attackers=c['attackers'], goalie=c['goalie'],attacker_poses=poses)
                    time.sleep(.6)
                    referee(1)
                    time.sleep(.25)
                    previous_rows = list(complete_rows(telemetry))
                    baseline_counts = previous_rows[-1] if previous_rows else {}
                    offset = telemetry.stat().st_size
                    referee(3)
                    start = time.monotonic()
                    trial_wall_time = time.time()
                    result = {'trial': trial+1, 'skill': skill, 'ball_start_m': ball, 'seconds': args.seconds, 'goal_crossing': False}
                    # 试验开始即保存；即便中途崩溃，也要留下一次无效尝试，不能悄悄消失。
                    result['valid'] = False
                    report['trials'].append(result)
                    prev = None
                    samples = 0
                    last_sample_at = time.monotonic()
                    initial_robots = None
                    max_robot_drift = 0
                    with telemetry.open(encoding='utf-8') as stream:
                        stream.seek(offset)
                        while time.monotonic()-start < args.seconds:
                            if p.poll() is not None or (goalie is not None and goalie.poll() is not None):
                                raise RuntimeError('Strategy process exited during trial')
                            if goalie is not None:
                                heartbeat = ROOT/'heartbeat-yellow.txt'
                                if not heartbeat.exists() or time.time()-heartbeat.stat().st_mtime > 2.5:
                                    raise RuntimeError('Goalkeeper heartbeat stalled; moving-goalie trial INVALID')
                            line_offset = stream.tell()
                            line = stream.readline()
                            if not line or not line.endswith('\n'):
                                stream.seek(line_offset)
                                if time.monotonic()-last_sample_at > 2.5:
                                    raise RuntimeError('Processed vision/decision heartbeat stalled for 2.5 seconds; trial INVALID')
                                time.sleep(.025)
                                continue
                            try:
                                row = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            x, y = row['ball']
                            if row['mode'] in ('HALT','STOP') and time.monotonic()-start > .5:
                                raise RuntimeError('Unexpected HALT/STOP during FORCE START trial: another referee sender may be active. Trial INVALID.')
                            last_sample_at = time.monotonic()
                            result.update(pass_attempts=row.get('pass_attempts', 0)-baseline_counts.get('pass_attempts', 0),
                                          pass_completed_proximity=row.get('pass_completed', 0)-baseline_counts.get('pass_completed', 0),
                                          pass_aborted=row.get('pass_aborted', 0)-baseline_counts.get('pass_aborted', 0))
                            samples += 1
                            if initial_robots is None:
                                initial_robots = row.get('robots', {})
                            for number, pos in row.get('robots', {}).items():
                                if number in initial_robots:
                                    old = initial_robots[number]
                                    max_robot_drift = max(max_robot_drift, math.hypot(pos[0]-old[0], pos[1]-old[1]))
                            if args.starts == 'approach' and time.monotonic()-start > 6 and max_robot_drift < 20:
                                raise RuntimeError('Vision arrives but robots do not move: check Client simulation command forwarding. Trial INVALID.')
                            if not row['ball_valid']:
                                continue
                            # Entire ball crosses the goal line; interpolate crossing Y between adjacent telemetry samples.
                            plane = c['field_length']/2 + 21.5
                            if prev and prev[0] <= plane < x:
                                crossing_y = prev[1]+(y-prev[1])*(plane-prev[0])/(x-prev[0])
                                if abs(crossing_y) < c['goal_width']/2-21.5:
                                    result.update(goal_crossing=True, crossing_y_mm=crossing_y, seconds=round(time.monotonic()-start, 3))
                                    break
                            prev = (x,y)
                            result['last_ball_mm'] = [x,y]
                            result['last_phase'] = row['phase']
                            result['last_shot_gate'] = row.get('shot_gate', {})
                    referee(0)
                    result['samples'] = samples
                    if goalie_telemetry:
                        poses = []
                        for row in complete_rows(goalie_telemetry):
                            pose = row.get('robots', {}).get(str(c['goalie']))
                            if row['time'] >= trial_wall_time and pose:
                                poses.append(pose)
                        result['goalie_samples'] = len(poses)
                        result['goalie_max_displacement_mm'] = max((math.dist(poses[0], q) for q in poses), default=0)
                        result['moving_goalie_observed'] = len(poses) >= 10 and result['goalie_max_displacement_mm'] > 20
                    if samples < 10:
                        raise RuntimeError('Insufficient telemetry: trial is INVALID, not a failed shot')
                    result['valid'] = True
                    (ROOT/'logs'/'benchmark-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
                    print(json.dumps(result), flush=True)
            finally:
                kill_child(p)
                output.close()
                stop_robots('blue', c['attackers'])
    except Exception as exc:
        report['error'] = str(exc)
        report['finished_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        data = json.dumps(report, indent=2)
        report_path.write_text(data, encoding='utf-8')
        (ROOT/'logs'/('benchmark-failed-'+time.strftime('%Y%m%d-%H%M%S')+'.json')).write_text(data, encoding='utf-8')
        raise
    finally:
        referee(0)
        kill_child(goalie)
        if goalie_log is not None:
            goalie_log.close()
        stop_robots('yellow', [c['goalie']])
        practice.close()
    report['complete'] = True
    report['finished_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
    data = json.dumps(report, indent=2)
    (ROOT/'logs'/'benchmark-report.json').write_text(data, encoding='utf-8')
    (ROOT/'logs'/('benchmark-'+time.strftime('%Y%m%d-%H%M%S')+'.json')).write_text(data, encoding='utf-8')
    print('Report: '+str(ROOT/'logs'/'benchmark-report.json'), flush=True)


if __name__ == '__main__':
    main()
