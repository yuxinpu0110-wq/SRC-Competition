"""本机仿真入口：初始化 SDK、更新观察、调用官方 NormalPlay.planTasks()。

战术状态及任务定义在 Play/Normal/Normalplay.py；这里不再直接执行技能回调。
HALT/无任务时保留本机 grSim 零速度保护；不适用于实车或远程比赛服务器。
"""
import argparse
import configparser
import json
import math
import os
from pathlib import Path
import socket
import sys
import time
import traceback

from runtime_paths import ROOT, SOURCE_ZBIN as ZBIN


def load_config(path):
    """配置不合法就拒绝启动；旧 direct 调度已移除，不允许静默回退。"""
    c = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if c['profile'] not in ('qualifier', 'full', 'goalie'):
        raise ValueError('profile must be qualifier, full or goalie')
    if c['team'] not in ('blue', 'yellow') or c['side'] not in ('left', 'right'):
        raise ValueError('Invalid team / side')
    ids = c['attackers']+[c['goalie']]
    if len(c['attackers']) != 2 or len(set(ids)) != 3 or any(type(n) is not int or not 0 <= n < 16 for n in ids):
        raise ValueError('Use two different attacker IDs and a different goalie ID (0..15)')
    if c['shoot_skill'] != 'targeted' or not 1000 <= c['power_cap'] <= 6000:
        raise ValueError('Invalid shoot_skill / power_cap')
    if c.get('task_dispatch', 'state_machine') != 'state_machine':
        raise ValueError('Only official state_machine dispatch is supported; remove old direct setting')
    if c.get('shoot_control', 'precision') != 'precision':
        raise ValueError('Ruleset requires precision; use the old Git version for legacy comparisons')
    from competition_rules import RULESET
    if c.get('ruleset') != RULESET or c.get('advance_before_shoot',False):
        raise ValueError('Use ruleset src2026-v1-no-carry and disable advance_before_shoot')
    if c.get('goalie_skill','Goalie') != 'Goalie':
        raise ValueError('This ruleset tests the official Goalie skill, not Goalie2022')
    for name in ('enable_pass', 'enable_feint', 'contact_trim', 'advance_before_shoot', 'enable_dss', 'qt_application', 'complete_frame',
                 'calibration_mode', 'enable_one_touch', 'enable_dynamic_keeper'):
        if name in c and type(c[name]) is not bool:
            raise ValueError(name+' must be true or false (not a string)')
    for name, low, high in [('pass_setup_timeout', .5, 10), ('pass_flight_timeout', .5, 6), ('pass_kick_timeout', .3, 3),
                            ('pass_score_gain', 0, 3000), ('pass_shot_threshold', -1000, 3000),
                            ('contact_distance',75,110), ('contact_forward_max',90,130)]:
        if name in c and (type(c[name]) not in (int, float) or not math.isfinite(c[name]) or not low <= c[name] <= high):
            raise ValueError('Invalid pass parameter: '+name)
    if type(c.get('contact_budget_mm',300)) not in (int,float) or not 50 <= c.get('contact_budget_mm',300) <= 400:
        raise ValueError('contact_budget_mm must be 50..400, below the 1m rule limit')
    if 'goal_margin' in c and (type(c['goal_margin']) not in (int, float)
            or not math.isfinite(c['goal_margin']) or not 85 <= c['goal_margin'] < c['goal_width']/2):
        raise ValueError('goal_margin must be >=85 and smaller than half goal_width')
    for name in ('field_length', 'field_width', 'goal_width', 'defense_depth', 'defense_width'):
        if not isinstance(c[name], (int, float)) or not math.isfinite(c[name]) or c[name] <= 0:
            raise ValueError('Invalid field dimension: '+name)
    if c.get('upgrade_version') == '2.1':
        if c.get('raw_capture_clock','unix') not in ('unix','relative'):
            raise ValueError('raw_capture_clock must be unix or relative')
        if not c.get('complete_frame', True):
            raise ValueError('V2.1 requires paired native pre/post decisions')
        for name, low, high in [('vision_max_age_s', .05, .5), ('vision_match_tolerance_mm', 5, 100),
                ('kick_pulse_seconds', .02, .2), ('kick_departure_timeout', .2, .5),
                ('flight_observe_seconds', .3, 3), ('action_no_progress_s', .3, 2),
                ('action_align_timeout_s', .5, 3), ('target_commit_timeout_s', .5, 5)]:
            value = c.get(name)
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError('Invalid V2.1 parameter: '+name)
        if type(c.get('raw_camera_id')) is not int or not -1 <= c['raw_camera_id'] <= 15:
            raise ValueError('raw_camera_id must be -1 (safe camera selection) or 0..15')
        if type(c.get('kick_max_attempts')) is not int or not 1 <= c['kick_max_attempts'] <= 2:
            raise ValueError('V2.1 permits at most one reapproach after no departure')
        if c.get('enable_feint') or c.get('enable_one_touch') or c.get('enable_dynamic_keeper'):
            raise ValueError('V2.1 foundation has not passed physical calibration for advanced features')
        if c.get('calibration_mode'):
            if type(c.get('calibration_power')) is not int or not 1000 <= c['calibration_power'] <= c['power_cap']:
                raise ValueError('Invalid calibration SDK power input')
            if c.get('enable_pass') or c.get('profile') != 'qualifier':
                raise ValueError('Calibration isolates two attackers, without passing')
    if c['defense_depth'] >= c['field_length']/2 or c['defense_width'] >= c['field_width'] or c['goal_width'] >= c['field_width']:
        raise ValueError('Inconsistent field dimensions')
    return c


def main():
    parser = argparse.ArgumentParser()
    default_config = ROOT/'configs/v2.1-foundation.json'
    parser.add_argument('--config', default=str(default_config if default_config.is_file() else ROOT/'match.json'))
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--frames', type=int, default=0)
    args = parser.parse_args()
    args.config = str(Path(args.config).resolve())
    if args.frames < 0:
        parser.error('frames must be non-negative')
    c = load_config(args.config)
    if not args.check:
        from match_processes import check_duplicates, process_count
        check_duplicates()
        if c.get('upgrade_version') == '2.1' and process_count('Medusa.exe'):
            raise RuntimeError('Another native Medusa controller is running; stop it before V2.1')
    os.chdir(ZBIN)
    from runtime_paths import dll_directories
    dll_handles = dll_directories()
    ini = configparser.ConfigParser(interpolation=None)
    ini.optionxform = str
    ini.read(ZBIN/'zss.ini', encoding='utf-8')
    ini['Alert']['IsSimulation'] = 'true'
    ini['ZAlert']['IsYellow'] = str(c['team'] == 'yellow').lower()
    ini['ZAlert']['IsRight'] = str(c['side'] == 'right').lower()
    native_config = ROOT/f"runtime-{c['team']}.ini"
    os.environ['SRC_MATCH_RUNTIME_CONFIG'] = str(native_config)
    if not args.check:
        with native_config.open('w', encoding='utf-8') as stream:
            ini.write(stream)
    qt_app = None
    if c.get('qt_application', False) and not args.check:
        from PySide6.QtCore import QCoreApplication, qVersion
        qt_app = QCoreApplication.instance() or QCoreApplication(sys.argv)
        print('[MATCH] Diagnostic Qt application:', qVersion(), flush=True)
    import CppPackage as C
    print(f'[MATCH] Python={sys.executable}\n[MATCH] config={Path(args.config).resolve()}\n[MATCH] {c}', flush=True)
    if args.check:
        from Play.Normal.Normalplay import NormalPlay
        print('[CHECK] Config and official Play/State/Task/Skill imports OK. No motion sent.', flush=True)
        return
    # 只防止两份本入口抢同色控制权；不能阻止旧 demo/Medusa。
    lock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    lock.bind(('127.0.0.1', 45990 if c['team'] == 'blue' else 45991))
    runner = C.SystemRunner.Instance()
    print('[MATCH] Waiting for Client processed vision. Start simulator and CONNECT Vision.', flush=True)
    runner.init(str(native_config))
    import InitAllModules
    import Global
    from Vision import Player, Ball, Enemy, vision
    from WorldModel import Params, kickStatus, worldModel
    from WorldModel.PowerCalculator import reloadFromGlobal
    from Play.Normal.Normalplay import NormalPlay
    from match_tactics import Point, referee_mode
    from sim_tools import stop_robots
    Global.read_config_file(str(native_config))
    expected = {'field_length':Params.pitchLength, 'field_width':Params.pitchWidth,
                'goal_width':Params.goalWidth, 'defense_depth':Params.penaltyDepth, 'defense_width':Params.penaltyWidth}
    for key, value in expected.items():
        if c[key] != value:
            raise ValueError(f'{key}={c[key]} differs from native/Python Params={value}')
    Global.isTestMode = False
    Global.powerMax = c['power_cap']
    reloadFromGlobal()
    play = NormalPlay(c)
    ids = play.ids
    Global.controlledRobotNumbers = tuple(ids)
    Global.goalieNumber = c['goalie']
    worldModel.SPlayFSMSwitchClearAll(True)
    log_dir = ROOT/'logs'
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir/(time.strftime('%Y%m%d-%H%M%S')+f"-{os.getpid()}-{c['team']}.jsonl")
    from runtime_manifest import manifest
    log_path.with_suffix('.manifest.json').write_text(json.dumps(manifest(args.config),indent=2),encoding='utf-8')
    frames, last_label, last_log, armed = 0, None, 0.0, False
    ready_frames = 0
    def xy(point):
        return Point(point.x(), point.y())
    print('[MATCH] READY. Official NormalPlay states. HALT -> STOP GAME -> FORCE START.', flush=True)
    raw = watchdog = None
    if c.get('upgrade_version') == '2.1':
        from observation import RawVision, FreshnessGuard
        from safety_watchdog import DecisionWatchdog
        raw = RawVision(camera=c['raw_camera_id'],port=c.get('raw_vision_port',10020))
        freshness = FreshnessGuard(c['vision_max_age_s'], c['vision_match_tolerance_mm'], c.get('raw_capture_clock','unix'))
        watchdog = DecisionWatchdog(c['team'], ids, c['vision_max_age_s'])
        if raw.error:
            print('[MATCH] Raw vision unavailable; V2.1 will hold safely: '+raw.error, flush=True)
    try:
        with log_path.open('w', encoding='utf-8') as log:
            while not args.frames or frames < args.frames:
                runner.preDecision()
                # 上一帧 Task/技能对象在 native preDecision 后才释放；不宣称能根治崩溃。
                play.retained_tasks.clear()
                if frames == 0 and not c.get('complete_frame', True):
                    ready = 0
                    while ready < 20:
                        ready = ready+1 if any(Player.isValid(n) for n in ids) else 0
                        runner.preDecision()
                if qt_app is not None:
                    qt_app.processEvents()
                # Python3.12/Windows的monotonic可能只有约15.6ms分辨率。
                # 多次回调时间相同会让速度估计/稳定计时反复重置，改用高精度单调计时。
                now = time.perf_counter()
                for number in ids:
                    C.setDribbleCommand(number, 0)
                message = vision.getCurrentRefereeMsg()
                mode = referee_mode(message, vision.isGameOn(), vision.canEitherKickBall())
                if mode in ('HALT', 'STOP', 'PREPARE', 'DEFEND_RESTART'):
                    armed = True
                if not armed:
                    mode = 'WAIT_INITIAL_STOP'
                if c.get('complete_frame', True):
                    # 官方要求 pre/post 配对：预热期间也规划 Stop 并完成后半帧。
                    # 旧实现连续 preDecision 预热、HALT 时跳过 post，保留开关供对照。
                    ready_frames = ready_frames+1 if any(Player.isValid(n) for n in ids) else 0
                    if ready_frames < 20:
                        mode = 'WAIT_VISION_STABLE'
                ball, valid_ball = xy(Ball.pos()), Ball.valid()
                # players 含受控门将位置，供 STOP 退球；选主攻时只看配置里的 attackers。
                visible = {n for n in ids if Player.isValid(n)}
                players = {n:xy(Player.Pos(n)) for n in visible}
                opponents = [xy(Enemy.pos(n)) for n in range(16) if Enemy.valid(n)]
                evidence = None
                associated = None
                if raw is not None:
                    # getCycle 仅供诊断；关联时间来自单独原始视觉，融合时间仍未知。
                    associated = raw.associated_capture(ball, players, c['team'], c['side'], freshness)
                    evidence = freshness.evaluate(associated, now, ball, players, c['team'], c['side'], vision.getCycle())
                    if watchdog.error:
                        from observation import Evidence
                        evidence = Evidence(reason='SAFE_STOP_CHANNEL_FAILED')
                    if evidence.fresh and evidence.new_capture:
                        watchdog.touch(now)
                play.update_frame(now, ball, players, opponents, visible, mode, valid_ball, referee=message,
                                  observation=evidence)
                play.planTasks()  # 唯一调度入口：官方 State.run → Task.run → Skill。
                Global.isPlaySwitched = False
                if play.motion_ids or c.get('complete_frame', True):
                    if frames < 3:
                        print(f'[MATCH] native frame={frames} mode={mode} visible={sorted(visible)} tasks={len(play.retained_tasks)}',flush=True)
                    runner.postDecision()
                # 完成 native 后半帧后，再给无运动任务的受控车追加本机零速度保护。
                idle = [n for n in ids if n not in play.motion_ids]
                if idle:
                    stop_robots(c['team'], idle)
                frames += 1
                label = (mode, play.executed_state, play.phase, play.tactics.striker)
                if label != last_label:
                    print(f'[MATCH] mode={mode} state={play.executed_state} next={play.current_state.state_name} phase={play.phase}', flush=True)
                    last_label = label
                if now-last_log >= c.get('log_interval', .1):
                    row = {'time':time.time(), 'frame':frames, 'mode':mode, 'phase':play.phase,
                           'process_id':os.getpid(),
                           'run_id':os.environ.get('SRC_MATCH_RUN_ID'),
                           'state':play.executed_state, 'next_state':play.current_state.state_name,
                           'strategy_phase':play.strategy_phase, 'pass_state':play.pass_state,
                           'pass_attempts':play.pass_attempts, 'pass_completed':play.pass_completed,
                           'pass_aborted':play.pass_aborted, 'pass_reason':play.pass_reason,
                           'referee':message, 'ball':[ball.x, ball.y], 'ball_valid':valid_ball,
                           'striker':play.tactics.striker, 'our_goals':vision.getOurGoal(), 'their_goals':vision.getTheirGoal(),
                           'shoot_control':c.get('shoot_control', 'precision'),
                           'ruleset':c['ruleset'], 'goalie_skill':'Goalie',
                           'contact_guard':play.contact_guard.diagnostics(),
                           'restart_guard':play.restart_guard.diagnostics(),
                           'ball_speed_sdk_mm_s':Ball.velMod(),
                           'monotonic':now, 'kick_events':list(play.kick_events),
                           'version':c.get('upgrade_version', 'v2-input'),
                           'observation':evidence.diagnostics() if evidence else None,
                           'raw_vision_error':raw.error if raw else None,
                           'safety_watchdog_error':watchdog.error if watchdog else None,
                           'raw_capture':dict(camera=associated.camera, frame_id=associated.frame_id,
                                capture_time=associated.capture_time, receive_time=associated.receive_time,
                                ball=[associated.ball.x,associated.ball.y] if associated.ball else None)
                                if associated else None,
                           'physical_events':list(play.transactions.events),
                           'kick_transaction':play.transactions.diagnostics(),
                           'aim':[play.choice.aim.x, play.choice.aim.y] if play.choice is not None and play.choice.aim is not None else None,
                           'shot_gate':{str(n):g.diagnostics for n,g in play.shot_gates.items()},
                           'angles':{str(n):Player.direction(n) for n in visible},
                           'opponents':[[p.x,p.y] for p in opponents],
                           'robots':{str(n):[players[n].x, players[n].y] for n in visible}}
                    log.write(json.dumps(row)+'\n')
                    log.flush()
                    play.kick_events.clear()  # 每次请求只记录一次；高频请求保存在两次日志之间。
                    play.transactions.events.clear()
                    (ROOT/f'heartbeat-{c["team"]}.txt').write_text(str(time.time()), encoding='ascii')
                    last_log = now
    except KeyboardInterrupt:
        print('[MATCH] Ctrl+C received.', flush=True)
    finally:
        # 原生崩溃不保证执行 finally，故这不是经过认证的急停机制。
        kickStatus.clearAll()
        if watchdog is not None:
            watchdog.close()
        if raw is not None:
            raw.close()
        for _ in range(3):
            stop_robots(c['team'], ids)
        lock.close()
        print(f'[MATCH] Exit after {frames} frames; log={log_path}', flush=True)


if __name__ == '__main__':
    code = 0
    try:
        main()
    except Exception:
        traceback.print_exc()
        code = 1
    # SDK 没有 shutdown/join；尝试避开析构，不是原生崩溃的修复。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
