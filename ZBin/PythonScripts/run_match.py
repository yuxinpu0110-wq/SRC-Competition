"""本地仿真执行入口：接收视觉/裁判 → 调用战术 → 执行官方技能。

只想读“为什么传球、谁去拿球”，先看 match_tactics.py 的 Planner.plan()。
想读“车如何移动、什么时候真正允许踢球”，看本文件 targeted_shot() 和主循环。
本入口不是正式 Normalplay.py / PenaltyKick.py 提交模板，且仍有原生崩溃待定位。
距离单位为毫米，角度为弧度；power 是框架力度参数，不是已测定的球速。
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

ROOT = Path(__file__).resolve().parents[2]
ZBIN = ROOT / 'ZBin'


def load_config(path):
    """读取并检查 match.json；有明显参数错误就拒绝启动，而不是盲目驱动车辆。"""
    c = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if c['profile'] not in ('qualifier', 'full', 'goalie'):
        raise ValueError('profile must be qualifier, full or goalie')
    if c['team'] not in ('blue', 'yellow') or c['side'] not in ('left', 'right'):
        raise ValueError('Invalid team / side')
    ids = c['attackers'] + [c['goalie']]
    if len(c['attackers']) != 2 or len(set(ids)) != 3 or any(type(n) is not int or not 0 <= n < 16 for n in ids):
        raise ValueError('Use two different attacker IDs and a different goalie ID (0..15)')
    if c['shoot_skill'] not in ('targeted', 'baseline') or not 1000 <= c['power_cap'] <= 12700:
        raise ValueError('Invalid shoot_skill / power_cap')
    if c.get('task_dispatch', 'state_machine') not in ('state_machine', 'direct'):
        raise ValueError('Invalid task_dispatch')
    for name in ('enable_pass', 'advance_before_shoot', 'enable_dss', 'qt_application'):
        if name in c and type(c[name]) is not bool:
            raise ValueError(name+' must be true or false (not a string)')
    for name, low, high in [('pass_setup_timeout', .5, 10), ('pass_flight_timeout', .5, 6), ('pass_kick_timeout', .3, 3),
                            ('pass_score_gain', 0, 3000), ('pass_shot_threshold', -1000, 3000)]:
        if name in c and (type(c[name]) not in (int, float) or not math.isfinite(c[name]) or not low <= c[name] <= high):
            raise ValueError('Invalid pass parameter: '+name)
    for name in ('field_length', 'field_width', 'goal_width', 'defense_depth', 'defense_width'):
        if not isinstance(c[name], (int, float)) or not math.isfinite(c[name]) or c[name] <= 0:
            raise ValueError('Invalid field dimension: ' + name)
    if c['defense_depth'] >= c['field_length']/2 or c['defense_width'] >= c['field_width'] or c['goal_width'] >= c['field_width']:
        raise ValueError('Inconsistent field dimensions')
    return c


def main():
    # --check 只检查配置与导入，不连接仿真；--frames 供限帧诊断，0 表示持续运行。
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=str(ROOT / 'match.json'))
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--frames', type=int, default=0)
    args = parser.parse_args()
    c = load_config(args.config)
    if not args.check:
        from match_processes import check_duplicates
        check_duplicates()
    os.chdir(ZBIN)
    dll_handle = os.add_dll_directory(str(ZBIN))
    # 官方库部分单例在导入时就读取配置，所以要先准备颜色、方向和仿真设置。
    ini = configparser.ConfigParser(interpolation=None)
    ini.optionxform = str
    ini.read(ZBIN / 'zss.ini', encoding='utf-8')
    ini['Alert']['IsSimulation'] = 'true'  # 强制本机仿真；这个版本没有实车验收。
    ini['ZAlert']['IsYellow'] = str(c['team'] == 'yellow').lower()
    ini['ZAlert']['IsRight'] = str(c['side'] == 'right').lower()
    native_config = ROOT / f"runtime-{c['team']}.ini"
    os.environ['SRC_MATCH_RUNTIME_CONFIG'] = str(native_config)
    # 只写本包生成的 runtime-颜色.ini，不覆盖官方 zss.ini；--check 不写文件。
    if not args.check:
        with native_config.open('w', encoding='utf-8') as f:
            ini.write(f)
    qt_app = None
    # 仅用于诊断的 Qt 初始化对照，默认关闭；不能视为已修复原生崩溃。
    if c.get('qt_application', False) and not args.check:
        from PySide6.QtCore import QCoreApplication, qVersion
        qt_app = QCoreApplication.instance() or QCoreApplication(sys.argv)
        print('[MATCH] Diagnostic Qt application:', qVersion(), flush=True)
    import CppPackage as C
    from match_tactics import Planner, Point, referee_mode, distance
    print(f"[MATCH] Python={sys.executable}\n[MATCH] config={Path(args.config).resolve()}\n[MATCH] {c}", flush=True)
    if args.check:
        from RoleMatch_LuaStyle.Skills import Stop, RushTo, NormalShoot, Goalie2022
        print('[CHECK] Config, Python dependencies and native skills import OK. No motion sent.', flush=True)
        return
    # 同色端口锁：第二份本入口无法占用相同端口，防止两个策略互相抢控制。
    # 这不能阻止未使用该锁的旧 demo / Medusa，仍需手动关闭它们。
    lock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    lock.bind(('127.0.0.1', 45990 if c['team'] == 'blue' else 45991))
    runner = C.SystemRunner.Instance()
    print('[MATCH] Waiting for Client processed vision. Start simulator and CONNECT Vision.', flush=True)
    runner.init(str(native_config))
    import InitAllModules
    # 初始化顺序跟随官方入口：先初始化 native，再导入缓存其单例的模块。
    from RoleMatch_LuaStyle.Skills import Stop, RushTo, NormalShoot, Goalie2022
    import Global
    from Vision import Player, Ball, Enemy, vision
    from WorldModel import kickStatus, worldModel
    from WorldModel.PowerCalculator import evalPower, P, reloadFromGlobal
    from RoleMatch_LuaStyle import State, StateMachine, Task, declare_state_machine
    from sim_tools import stop_robots
    Global.read_config_file(str(native_config))
    from WorldModel import Params
    expected = {'field_length': Params.pitchLength, 'field_width': Params.pitchWidth,
                'goal_width': Params.goalWidth, 'defense_depth': Params.penaltyDepth,
                'defense_width': Params.penaltyWidth}
    for key, value in expected.items():
        if c[key] != value:
            raise ValueError(f'{key}={c[key]} differs from native/Python Params={value}; update the whole field configuration first')
    # 不开“忽略裁判”的测试模式；JSON 场地尺寸也必须匹配官方参数。
    Global.isTestMode = False
    Global.powerMax = c['power_cap']
    reloadFromGlobal()
    # qualifier=只控制两辆进攻车；goalie=单独运行对练门将；full=实验三车模式。
    ids = c['attackers'] if c['profile'] == 'qualifier' else ([c['goalie']] if c['profile'] == 'goalie' else c['attackers']+[c['goalie']])
    Global.controlledRobotNumbers = tuple(ids)
    Global.goalieNumber = c['goalie']
    worldModel.SPlayFSMSwitchClearAll(True)
    planner = Planner(c)
    def stop(n):
        C.makeItCalibrationSpeed(n, 0, 0, 0)
    commands = {}
    # commands 保存“车号 → 执行函数”。闭包和 C++ 几何对象需要跨 native 调用保活。
    native_keepalive = []

    class Execute(State):
        """官方状态机适配器：把已确定的车号/动作封装成 Task，不负责战术切换。

        task_dispatch=state_machine 时使用它；direct 时直接执行相同技能。
        传球状态机仍在 Planner 中，不是这里的单状态 Execute。
        """
        def getTasks(self):
            return {('G' if n == c['goalie'] else 'A' if n == c['attackers'][0] else 'B'):
                    Task((commands.get(n, stop), lambda executor: Player.Pos(executor)), fixedNumber=n)
                    for n in ids if Player.isValid(n)}

        def getMatchString(self):
            return ''

        def transFunction(self):
            return ''

    @declare_state_machine(Execute)
    class MatchExecution(StateMachine):
        pass

    execution = MatchExecution()

    def xy(p):
        # 官方点使用 p.x()/p.y() 方法；战术层使用纯 Python 的 Point.x/Point.y 属性。
        return Point(p.x(), p.y())

    def move(n, target, ball, slow=False, receive=False):
        """走向目标并朝向球；接球时允许靠近球并开启吸球。"""
        p = C.CGeoPoint(target.x, target.y)
        angle = math.atan2(ball.y-Player.Pos(n).y(), ball.x-Player.Pos(n).x())
        flags = (0x08000000 if c.get('enable_dss', True) else 0) | 0x200 | (0 if receive else 0x10)
        # flags 是位掩码，| 表示同时启用多个标志；具体含义见 WorldModel/Flags.py。
        # 当前组合：可选 DSS 避碰、避让己方禁区；非接球动作另外避球。
        if slow:
            flags |= 1 | 0x100000
        skill = RushTo(p, angle=angle, maxAcc=1800 if slow or receive else 3000,
                       flag=flags, needDribble=receive)
        native_keepalive.append((p, skill))
        skill[0](n)

    def targeted_shot(n, target, ball, power, carry=False, can_kick=True, passing=False):
        """先绕到球后、对齐，再接触球；满足条件才踢球。

        carry=True：带球推进，不踢；can_kick=False：只瞄准等待；
        passing=True：发出踢球请求时通知传球状态机，等待视觉确认实际出球。
        返回的是便于看日志的执行阶段名称，不是完整战术状态。
        """
        angle = math.atan2(target.y-ball.y, target.x-ball.x)
        # (ux, uy) 是球指向目标的单位向量；沿这个方向找球后站位。
        ux, uy = math.cos(angle), math.sin(angle)
        robot = xy(Player.Pos(n))
        # along>0：球在车的前方；lateral：车到踢球直线的横向偏差。
        # err 归一到 [-π, π] 再取绝对值，避免 179° 与 -179° 被误判相差 358°。
        along = (ball.x-robot.x)*ux + (ball.y-robot.y)*uy
        lateral = abs((ball.x-robot.x)*uy - (ball.y-robot.y)*ux)
        err = abs(math.atan2(math.sin(Player.direction(n)-angle), math.cos(Player.direction(n)-angle)))
        aligned = along > 0 and lateral < 35 and err < 0.08
        # 35 mm 横向误差、0.08 rad（约 4.6°）朝向误差、150 mm 距球门槛。
        close = distance(robot, ball) < 150
        offset = 65 if aligned else 240
        # 未对齐先到球后 240 mm；对齐后靠到 65 mm，准备接触球。
        dest = planner.legal(Point(ball.x-offset*ux, ball.y-offset*uy))
        if carry and aligned and close:
            # 带球时目标放到球前 160 mm，让吸球中的车继续推进，而不是停在球后。
            dest = planner.legal(Point(ball.x+160*ux, ball.y+160*uy))
        flags = (0x08000000 if c.get('enable_dss', True) else 0) | 0x200 | (0 if aligned else 0x10)
        native_dest = C.CGeoPoint(dest.x, dest.y)
        skill = RushTo(native_dest, angle=angle, maxAcc=1800, flag=flags, needDribble=aligned)
        native_keepalive.append((native_dest, skill))
        skill[0](n)
        if aligned and close and not carry and can_kick:
            # 只有这里真正请求踢球。ALIGN/CONTACT 之类日志不代表已经踢出去。
            kickStatus.setKick(n, power)
            if passing:
                planner.note_kick(time.monotonic(), n, ball)
        return ('CARRY' if carry and aligned and close else
                'KICK' if aligned and close and can_kick else
                'HOLD_BALL' if aligned and close else 'CONTACT' if aligned else 'ALIGN')

    log_dir = ROOT / 'logs'
    log_dir.mkdir(exist_ok=True)
    log_path = log_dir / (time.strftime('%Y%m%d-%H%M%S') + f"-{c['team']}.jsonl")
    frames, last_label, last_log = 0, None, 0.0
    armed = False
    # 启动保险：新进程必须先观察到一次停赛/准备状态，不能继承旧开赛状态就跑。
    print('[MATCH] READY. HALT -> STOP GAME -> FORCE START. Ctrl+C to exit. Do not start Medusa.', flush=True)
    try:
        with log_path.open('w', encoding='utf-8') as log:
            while not args.frames or frames < args.frames:
                # 一帧的流程：读视觉 → 检查裁判 → 算战术 → 生成技能 → 下发 → 记日志。
                runner.preDecision()
                # 上一帧对象保留至本次 preDecision 后再释放，是生命周期防护；
                # 这并没有证明已经修复所有原生堆错误。
                native_keepalive.clear()
                # 初次运行先等待连续 20 帧至少一辆受控车有效，避免空视觉直接规划。
                if frames == 0:
                    ready_frames = 0
                    while ready_frames < 20:
                        ready_frames = ready_frames + 1 if any(Player.isValid(n) for n in ids) else 0
                        runner.preDecision()
                if qt_app is not None:
                    qt_app.processEvents()
                now = time.monotonic()
                # 每帧先清旧踢球/吸球请求，有需要时在本帧技能里重新开启。
                kickStatus.clearAll()
                assigned = set()
                # assigned 是本帧有运动任务的车号集合；未分配的本机车会收到零速度。
                for n in ids:
                    C.setDribbleCommand(n, 0)
                message = vision.getCurrentRefereeMsg()
                mode = referee_mode(message, vision.isGameOn(), vision.canEitherKickBall())
                if mode in ('HALT', 'STOP', 'PREPARE', 'DEFEND_RESTART'):
                    armed = True
                if not armed:
                    mode = 'WAIT_INITIAL_STOP'
                valid_ball = Ball.valid()
                ball = xy(Ball.pos())
                phase = mode
                if mode not in ('PLAY', 'OUR_RESTART') or not valid_ball:
                    # 裁判打断或球不可见时，不能保留半途传球并在之后突然继续踢。
                    planner.cancel_pass(now, 'REFEREE_OR_VISION')
                if not valid_ball or not all(math.isfinite(v) for v in (ball.x, ball.y)):
                    phase = 'NO_BALL'
                elif mode in ('STOP', 'DEFEND_RESTART', 'PREPARE'):
                    # STOP 是暂停/退球，不是开始游戏；距球不足 650 mm 才向外退。
                    # HALT 不进入这个分支，所以不会为“准备站位”移动。
                    for i, n in enumerate(ids):
                        if Player.isValid(n):
                            p = xy(Player.Pos(n))
                            if distance(p, ball) < 650:
                                angle = math.atan2(p.y-ball.y, p.x-ball.x) if distance(p, ball) > 1 else math.pi+i
                                target = planner.legal(Point(ball.x+750*math.cos(angle), ball.y+750*math.sin(angle)))
                                if distance(target, ball) >= 650:
                                    commands[n] = lambda executor, target=target, ball=ball: move(executor, target, ball, True)
                                    assigned.add(n)
                elif mode in ('PLAY', 'OUR_RESTART'):
                    # 仅在允许进攻时调用战术；战术输出动作后，这里翻译成官方技能。
                    players = {n: xy(Player.Pos(n)) for n in c['attackers'] if Player.isValid(n)}
                    enemies = [xy(Enemy.pos(n)) for n in range(16) if Enemy.valid(n)]
                    actions, phase = planner.plan(now, ball, players, enemies)
                    if c['profile'] != 'goalie':
                        for n, (action, target) in actions.items():
                            assigned.add(n)
                            if action in ('move', 'receive'):
                                # lambda 先保存函数，下面统一调用；默认参数用于冻结
                                # 本次循环的 target/ball，避免所有车都使用最后一次的目标。
                                commands[n] = lambda executor, target=target, ball=ball, receive=action == 'receive': move(executor, target, ball, receive=receive)
                            else:
                                p = C.CGeoPoint(target.x, target.y)
                                # 传球用 P.NORMAL，其他动作用 P.HIGH 计算力度。
                                # 即使算出了力度，carry/aim 也不会因此自动踢球。
                                power = evalPower(Ball.pos(), p, P.NORMAL if action == 'pass' else P.HIGH)
                                if c['shoot_skill'] == 'baseline' and action == 'shoot':
                                    commands[n] = NormalShoot(power)[0]
                                else:
                                    def shoot(executor, target=target, ball=ball, power=power, carry=action == 'carry', can_kick=action != 'aim', passing=action == 'pass'):
                                        nonlocal phase
                                        phase = targeted_shot(executor, target, ball, power, carry=carry, can_kick=can_kick, passing=passing)
                                    commands[n] = shoot
                    if c['profile'] != 'qualifier' and Player.isValid(c['goalie']):
                        # 门将由官方 Goalie2022 技能控制，不用进攻车的 legal() 站位。
                        commands[c['goalie']] = Goalie2022('normal')[0]
                        assigned.add(c['goalie'])
                        if c['profile'] == 'goalie':
                            phase = 'GOALIE2022'
                # 无运动任务时不调用原生运动规划，改给本机仿真发送明确的零速度。
                # 这条本机停车通道不能未经适配就用于远程比赛服务器或实车。
                strategy_phase = phase
                # strategy_phase 保存 Planner 的决定（如 PASS_SETUP）；phase 可能被
                # 下面的执行函数改为 ALIGN/KICK 等。多车时 phase 只是最后一次写入值。
                if assigned:
                    native_keepalive.extend(commands.values())
                    if c.get('task_dispatch', 'state_machine') == 'direct':
                        for n in ids:
                            if Player.isValid(n):
                                commands.get(n, stop)(n)
                    else:
                        execution.planTasks()
                    runner.postDecision()
                commands.clear()
                idle_ids = [n for n in ids if n not in assigned]
                if idle_ids:
                    stop_robots(c['team'], idle_ids)
                frames += 1
                label = (mode, phase, planner.striker)
                if label != last_label:
                    # 只在模式/阶段/主攻变化时打印，避免每帧刷屏拖慢实时控制。
                    print(f'[MATCH] mode={mode} phase={phase} striker={planner.striker} referee={message!r}', flush=True)
                    last_label = label
                if now-last_log >= c.get('log_interval', 0.1):
                    # JSONL 每行是一条观察记录；heartbeat 供启动面板发现数据停滞。
                    row = {'time': time.time(), 'frame': frames, 'mode': mode, 'phase': phase,
                           'strategy_phase': strategy_phase, 'pass_state': planner.pass_state,
                           'pass_attempts': planner.pass_attempts, 'pass_completed': planner.pass_completed,
                           'pass_aborted': planner.pass_aborted, 'pass_reason': planner.pass_reason,
                           'referee': message, 'ball': [ball.x, ball.y], 'ball_valid': valid_ball,
                           'striker': planner.striker, 'our_goals': vision.getOurGoal(), 'their_goals': vision.getTheirGoal(),
                           'angles': {str(n): Player.direction(n) for n in ids if Player.isValid(n)},
                           'robots': {str(n): [Player.Pos(n).x(), Player.Pos(n).y()] for n in ids if Player.isValid(n)}}
                    log.write(json.dumps(row)+'\n')
                    log.flush()
                    (ROOT / f'heartbeat-{c["team"]}.txt').write_text(str(time.time()), encoding='ascii')
                    last_log = now
    except KeyboardInterrupt:
        print('[MATCH] Ctrl+C received.', flush=True)
    finally:
        # 普通异常或 Ctrl+C 时尝试停车。原生崩溃/强制杀进程不保证执行 finally，
        # 因此这不能替代外部急停，也不能当成稳定性已经验收的证据。
        kickStatus.clearAll()
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
    # SDK 未暴露线程 shutdown/join；这里在正常清理、刷新输出后尝试绕开
    # Python 析构阶段。此前仍观察到退出异常，os._exit 不是运行时崩溃的修复。
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
