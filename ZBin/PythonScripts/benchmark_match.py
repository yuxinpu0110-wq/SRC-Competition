"""Repeatable LOCAL simulation smoke benchmark; never an official win-rate measurement.

Requires one connected Client + one grSim. Stops only the child strategy it starts.
Starts official Goalie (NOT Goalie2022) via run_match --config opponent.json.
Logs no-carry guards and filtered SDK ball speed; none replaces the official referee.
"""
from strategy_contract import uses_verified_control
import argparse
import hashlib
import math
import itertools
import json
import os
import uuid
from pathlib import Path
import subprocess
import sys
import time
from run_match import ROOT, ZBIN, load_config
from sim_tools import PracticeReferee, reset_scene, stop_robots


def scene_error(row, ball, poses, tolerance=100):
    """必须从视觉确认摆车成功；发了UDP包不代表已经看到新位置。"""
    if row is None or not row.get('ball_valid') or not all(math.isfinite(v) for v in row['ball']):
        return 'NO_VALID_VISION'
    if math.dist(row['ball'],[ball[0]*1000,ball[1]*1000]) > tolerance:
        return 'BALL_NOT_AT_START'
    for number,pose in poses.items():
        actual = row.get('robots',{}).get(str(number))
        if actual is None or not all(math.isfinite(v) for v in actual) or math.dist(actual,[pose[0]*1000,pose[1]*1000]) > tolerance:
            return f'ROBOT_{number}_NOT_AT_START'
        heading = row.get('angles',{}).get(str(number))
        if heading is None or not math.isfinite(heading) or abs(math.atan2(math.sin(heading-math.radians(pose[2])),
                                             math.cos(heading-math.radians(pose[2])))) > .2:
            return f'ROBOT_{number}_HEADING_NOT_AT_START'
    return ''


def kill_child(p):
    if p is not None and p.poll() is None:
        subprocess.run(['taskkill', '/PID', str(p.pid), '/T', '/F'], capture_output=True)
        p.wait(timeout=5)


def opponents_match(row, expected, tolerance=150):
    """两队融合视觉必须看到同一个摆位，排除刚瞬移后仍残留的对手旧坐标。"""
    if not row:
        return False
    seen = row.get('opponents', [])
    if len(seen) != len(expected):
        return False
    return any(all(len(p) == 2 and all(math.isfinite(v) for v in p)
                       and math.dist(p,q) <= tolerance for p,q in zip(order,expected))
               for order in itertools.permutations(seen))


def complete_rows(path):
    """Ignore a writer's unfinished trailing JSON line, never conceal other errors."""
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if not line.endswith('\n'):
                break
            yield json.loads(line)


def launch(config, label):
    f = (ROOT/'logs'/f'benchmark-{label}-{time.time_ns()}-console.txt').open('w', encoding='utf-8')
    run_id=uuid.uuid4().hex
    env=dict(os.environ,SRC_MATCH_RUN_ID=run_id)
    p = subprocess.Popen([sys.executable, '-X', 'utf8', '-X', 'faulthandler', '-u', str(ZBIN/'PythonScripts'/'run_match.py'), '--config', str(config)],
                         cwd=ZBIN, stdout=f, stderr=subprocess.STDOUT,env=env)
    p.run_id=run_id
    return p, f


def wait_for_telemetry(process, team, started_at, timeout=15):
    """等本次子进程真正发布遥测；不以固定2秒或旧日志作为启动成功。"""
    from match_launcher import latest_complete_row
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        code=process.poll()
        if code is not None:
            raise RuntimeError(f'Strategy exited before telemetry: pid={process.pid}, code={code}')
        paths=sorted((ROOT/'logs').glob(f'*-{team}.jsonl'),key=lambda p:p.stat().st_mtime,reverse=True)
        for path in paths:
            row=latest_complete_row(path)
            if (row and row.get('run_id')==getattr(process,'run_id',None) and row.get('run_id') is not None and row.get('time',0)>=started_at
                    and time.time()-row['time']<.5):
                return path
        time.sleep(.05)
    raise RuntimeError(f'No telemetry from this strategy within {timeout}s; Client vision or native init stalled')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=ROOT/'configs/v2.1-foundation.json')
    parser.add_argument('--seconds', type=float, default=20)
    parser.add_argument('--contact-distance',type=float, help='Diagnostic target contact distance in mm (75..110)')
    parser.add_argument('--contact-trim',action='store_true',help='Bounded lateral contact correction diagnostic')
    parser.add_argument('--skills', nargs='+', choices=['targeted'], default=['targeted'])
    parser.add_argument('--repeats', type=int, default=1)
    parser.add_argument('--passing', choices=('on', 'off'), help='Explicit passing A/B; otherwise uses match.json')
    parser.add_argument('--feint', choices=('on', 'off'), default='off', help='Requires --passing on; candidate feature')
    parser.add_argument('--dss', choices=('on', 'off'), help='Diagnostic native DSS A/B; default unchanged')
    parser.add_argument('--dispatch', choices=('state_machine',), help='Only official state_machine dispatch is supported')
    parser.add_argument('--qt-app', action='store_true', help='Diagnostic explicit Qt application in both controllers')
    parser.add_argument('--shoot-control', choices=('precision',), default='precision',
                        help='Same-scene A/B of old/new targeted contact control')
    parser.add_argument('--goalie', choices=('official', 'stationary', 'none'), default='official',
                        help='stationary isolates aiming; never count it as a moving-goalie test')
    parser.add_argument('--starts', choices=('approach','contact','front','rolling'), default='approach',
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
    c = load_config(args.config)
    if args.starts == 'rolling' and (not c.get('calibration_mode') or args.goalie != 'none'):
        parser.error('rolling is calibration-only and requires --goalie none')
    if args.contact_trim:
        c['contact_trim'] = True
    if args.contact_distance is not None:
        if not 75 <= args.contact_distance <= 110:
            parser.error('contact-distance must be 75..110 mm')
        c['contact_distance'] = args.contact_distance
        c['contact_forward_max'] = min(130,args.contact_distance+20)
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
              'goalie_skill':'Goalie', 'ruleset':c['ruleset'],
              'start_mode': args.starts,
              'complete': False, 'started_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'config': c,
              'source_sha256': {name: hashlib.sha256((ZBIN/'PythonScripts'/name).read_bytes()).hexdigest()
                                for name in ['run_match.py', 'match_tactics.py', 'Play/Normal/Normalplay.py',
                                             'RoleMatch_LuaStyle/Skills/CompetitionSkills.py', 'shot_accuracy.py',
                                             'ball_control.py', 'competition_rules.py', 'sim_tools.py', 'benchmark_match.py']}, 'trials': []}
    report_path = ROOT/'logs'/'benchmark-report.json'
    from runtime_manifest import manifest
    report['runtime_manifest'] = manifest(args.config)
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    practice = PracticeReferee()
    referee = practice.set
    referee(0)
    goalie_path = ROOT/'opponent.json'
    if args.dispatch or args.qt_app or args.frame_cycle:
        goalie_config = load_config(goalie_path)
        goalie_config['complete_frame'] = args.frame_cycle == 'paired'
        # 只提高记录频率；一秒内进球时，默认10Hz不足以满足10个门将样本验收。
        # 不改Goalie()算法、力量、位置或任何物理参数。
        goalie_config['log_interval'] = .02
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
            launched_at=time.time()
            report.setdefault('attacker_consoles', []).append(output.name)
            try:
                telemetry=wait_for_telemetry(p,c['team'],launched_at)
                if p.poll() is not None or (goalie is not None and goalie.poll() is not None):
                    raise RuntimeError('A strategy failed; inspect logs/benchmark-*-console.txt')
                report.setdefault('attacker_telemetries',[]).append(str(telemetry))
                yellow_logs = sorted((ROOT/'logs').glob('*-yellow.jsonl'), key=lambda p:p.stat().st_mtime)
                goalie_telemetry = yellow_logs[-1] if yellow_logs and goalie is not None else None
                report['goalie_telemetry'] = str(goalie_telemetry) if goalie_telemetry else None
                starts = ([(0,0),(.8,1),(.8,-1)] if args.starts == 'approach' else
                          [(3.38,0),(3.38,.6),(3.38,-.6)] if args.starts == 'front' else
                          [(0,0),(0,.8),(0,-.8)] if args.starts == 'rolling' else
                          [(2.8,0),(2.8,.8),(2.8,-.8)])
                for trial, ball in enumerate(starts * args.repeats):
                    result = {'trial':trial+1, 'skill':skill, 'ball_start_m':ball,
                              'seconds':args.seconds, 'goal_crossing':False, 'valid':False,
                              'rule_compliance_verified':False, 'physical_departures':0}
                    report['trials'].append(result)
                    # 先STOP再HALT，后摆车；不能贴球摆好后再让Prepare退球破坏初态。
                    referee(1)
                    time.sleep(.25)
                    referee(0)
                    time.sleep(.25)
                    poses = None
                    if args.starts in ('contact','front','rolling'):
                        from match_tactics import Point, Tactics
                        aim,_ = Tactics(c).shot(Point(ball[0]*1000,ball[1]*1000),[] if args.goalie == 'none' else [Point(4200,0)])
                        angle = math.atan2(aim.y-ball[1]*1000,aim.x-ball[0]*1000)
                        # 摆位不能穿入接触实体；运动目标可略近以克服原生位置死区。
                        placement = c.get('calibration_placement_distance_mm', 110)/1000
                        poses = {c['attackers'][0]:(ball[0]-placement*math.cos(angle),ball[1]-placement*math.sin(angle),math.degrees(angle)),
                                 c['attackers'][1]:(1.5,1.5,0)}
                        if c.get('calibration_mode'):
                            poses.pop(c['attackers'][1])  # 空门单车，排除其他触球者。
                    reset_scene(ball=ball, attackers=c['attackers'][:1] if c.get('calibration_mode') else c['attackers'],
                                goalie=None if args.goalie == 'none' else c['goalie'],attacker_poses=poses)
                    expected_poses = poses or {c['attackers'][0]:(-1,-.7,0),c['attackers'][1]:(-1.5,1.2,0)}
                    from match_launcher import latest_complete_row
                    deadline = time.monotonic()+8
                    confirmed_since = None
                    last = None
                    while time.monotonic() < deadline:
                        if p.poll() is not None:
                            result['attacker_exit_code'] = p.returncode
                            raise RuntimeError('Strategy exited during reset preflight')
                        if goalie is not None and goalie.poll() is not None:
                            result['goalie_exit_code'] = goalie.returncode
                            raise RuntimeError('Goalkeeper exited during reset preflight; trial INVALID. Inspect goalie console.')
                        last = latest_complete_row(telemetry)
                        goalie_start = latest_complete_row(goalie_telemetry) if goalie_telemetry else None
                        goalie_ready = (goalie is None or (goalie_start and time.time()-goalie_start['time'] < .3
                            and not scene_error(goalie_start,(-ball[0],-ball[1]),{c['goalie']:(-4.2,0,0)},150)
                            and opponents_match(goalie_start,[[-p[0]*1000,-p[1]*1000] for p in expected_poses.values()])))
                        if (last and time.time()-last['time'] < .3 and not scene_error(last,ball,expected_poses)
                                and (not uses_verified_control(c) or (last.get('observation') or {}).get('fresh'))
                                and (not last.get('opponents') if args.goalie == 'none' else opponents_match(last,[[4200,0]]))
                                and (not c.get('calibration_mode') or math.dist(last['ball'],[ball[0]*1000,ball[1]*1000]) <= 8)
                                and goalie_ready):
                            if confirmed_since is None:
                                confirmed_since = time.monotonic()
                            elif time.monotonic()-confirmed_since >= .2:
                                break
                        else:
                            confirmed_since = None
                        time.sleep(.05)
                    else:
                        details = dict(
                            scene_error=scene_error(last,ball,expected_poses),
                            observation=(last or {}).get('observation'),
                            opponents=(last or {}).get('opponents'),
                            expected_opponents=[] if args.goalie == 'none' else [[4200,0]],
                            ball_placement_error_mm=math.dist(last['ball'],[ball[0]*1000,ball[1]*1000]) if last else None,
                            calibration_placement_tolerance_mm=8 if c.get('calibration_mode') else None,
                            goalie_required=args.goalie == 'official', goalie_ready=bool(goalie_ready))
                        result.update(reset_error='PREFLIGHT_CONDITIONS_NOT_MET', preflight_details=details,
                                      observed_start=last,observed_goalie_start=goalie_start)
                        raise RuntimeError('Reset not confirmed by vision: '+result['reset_error']+'. Trial INVALID.')
                    result['verified_start'] = dict(ball=last['ball'],robots=last['robots'],angles=last['angles'])
                    result['verified_goalie_start'] = goalie_start
                    previous_rows = list(complete_rows(telemetry))
                    baseline_counts = previous_rows[-1] if previous_rows else {}
                    offset = telemetry.stat().st_size
                    referee(3)
                    start = time.monotonic()
                    trial_wall_time = time.time()
                    # 试验开始即保存；即便中途崩溃，也要留下一次无效尝试，不能悄悄消失。
                    prev = None
                    samples = 0
                    last_sample_at = time.monotonic()
                    initial_robots = None
                    max_robot_drift = 0
                    speed_samples = []
                    max_near_ball_path = 0
                    rule_releases = 0
                    phase_samples = {}
                    kick_requests = {'shot':0,'pass':0}
                    physical_ids = set()
                    transaction_ids = set()
                    fresh_capture_ids = set()
                    stale_since = None
                    max_stale_seconds = 0.
                    stale_samples = 0
                    from raw_trial_recorder import RawTrialRecorder
                    raw_path=ROOT/'logs'/f'raw-calibration-{p.run_id}-{trial+1}.jsonl' if c.get('calibration_mode') else None
                    if raw_path is not None:
                        report.setdefault('raw_capture_logs',[]).append(str(raw_path))
                        result['raw_capture_log']=str(raw_path)
                    with telemetry.open(encoding='utf-8') as stream, RawTrialRecorder(raw_path) as raw_audit:
                        stream.seek(offset)
                        while time.monotonic()-start < args.seconds:
                            raw_audit.poll()
                            if p.poll() is not None or (goalie is not None and goalie.poll() is not None):
                                result.update(attacker_exit_code=p.poll(),goalie_exit_code=goalie.poll() if goalie else None)
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
                            ev = row.get('observation') or {}
                            if ev.get('fresh') and ev.get('new_capture') and ev.get('frame_id'):
                                fresh_capture_ids.add(tuple(ev['frame_id']))
                            phase_samples[row['phase']] = phase_samples.get(row['phase'],0)+1
                            for event in row.get('kick_events',[]):
                                kick_requests['pass' if event.get('passing') else 'shot'] += 1
                            for event in row.get('physical_events', []):
                                if event.get('kind') == 'KICK_REQUESTED':
                                    transaction_ids.add(event['transaction_id'])
                                elif event.get('kind') == 'BALL_DEPARTED':
                                    physical_ids.add(event['transaction_id'])
                                elif event.get('kind') == 'KICK_CANCELLED' and event.get('reason') == 'RESET_OR_SPEED_LIMIT':
                                    raise RuntimeError('Unobserved jump or speed limit violation; trial INVALID')
                            result['physical_departures'] = len(physical_ids)
                            result['kick_transactions'] = len(transaction_ids)
                            speed = row.get('ball_speed_sdk_mm_s')
                            if speed is not None and math.isfinite(speed):
                                speed_samples.append(speed)
                                if c.get('calibration_mode') and speed > 6500:
                                    raise RuntimeError('Calibration speed limit exceeded; stop this combination')
                            for guard in row.get('contact_guard',{}).values():
                                max_near_ball_path = max(max_near_ball_path,guard['robot_path_mm'],guard['ball_path_mm'])
                            rule_releases += row['phase'].startswith('RULE_')
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
                            if uses_verified_control(c):
                                if not (row.get('observation') or {}).get('fresh'):
                                    stale_samples += 1
                                    if stale_since is None: stale_since = row['monotonic']
                                    duration=row['monotonic']-stale_since
                                    max_stale_seconds=max(max_stale_seconds,duration)
                                    if duration > .6:
                                        raise RuntimeError('Raw-associated vision missing continuously >0.6s; trial INVALID')
                                else:
                                    stale_since=None
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
                    result['independent_raw_samples']=raw_audit.samples
                    result['raw_poll_failures']=raw_audit.poll_failures
                    referee(0)
                    result['samples'] = samples
                    result['fresh_capture_samples'] = len(fresh_capture_ids)
                    result['stale_samples'] = stale_samples
                    result['max_stale_seconds'] = max_stale_seconds
                    result['phase_samples'] = phase_samples
                    result['kick_requests'] = kick_requests  # 请求数；短脉冲会重复请求，不代表实际踢了多次。
                    result.update(max_ball_speed_sdk_mm_s=max(speed_samples,default=None),
                                  sdk_speed_over_limit=any(v > 6500 for v in speed_samples),
                                  max_near_ball_path_mm=max_near_ball_path,rule_release_samples=rule_releases,
                                  rule_compliance_verified=False)
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
                    if uses_verified_control(c) and len(fresh_capture_ids) < 10:
                        raise RuntimeError('Insufficient distinct captures; trial INVALID')
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
