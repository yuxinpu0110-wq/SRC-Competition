"""ZBin 接口适配层：将 core.Action 翻译成 RoleMatch 的 Task。

core.py 只决定两辆车“应该做什么”；本文件才读取 ZBin 的视觉/裁判对象，并把
停止、移动、踢球交给已有 CppPackage。策略细节请先读 core.py，避免把接口调用
误认为战术本身。
"""

# math.isfinite 用于拒绝异常的踢球力度。
import math

import CppPackage
import Global
from Geometry import CGeoPoint
from RoleMatch_LuaStyle import State, StateMachine, Task, declare_state_machine
from RoleMatch_LuaStyle.Skills import RushTo, Stop
from Vision import Ball, Enemy, Player, vision
from WorldModel import Conditions, Flags, Params, kickStatus
from WorldModel.PowerCalculator import P, evalPower

from .core import Field, Frame, Planner, Point, Robot
from .settings import SETTINGS


# 用当前 ZBin 实际场地参数而非 core.py 的默认数值创建策略。
PLANNER = Planner(SETTINGS, Field(Params.pitchLength, Params.pitchWidth,
                  Params.goalWidth, Params.penaltyDepth, Params.penaltyWidth,
                  Params.playerRadius))
# 仅将“我方有球”的三种裁判消息映射到核心层定位球阶段。
OUR_RESTARTS = {"OurKickOff": "kickoff", "OurDirectKick": "direct", "OurIndirectKick": "indirect"}
# Falcon 在 Normal Start 后保留 Ready 作为当前裁判状态；它和空字符串都表示活球。
LIVE_PLAY_MESSAGES = {"", "Ready"}
# 移动时始终允许底层动态避障，并阻止车进入己方禁区。
SAFE_FLAGS = Flags.allow_dss | Flags.dodge_our_defense_box


def point(p):
    """将 CppPackage.CGeoPoint 复制为不依赖 ZBin 的 core.Point。"""
    return Point(float(p.x()), float(p.y()))


def stop_actions():
    """清除已挂起的踢球请求，并关闭两辆受控车的吸球命令。"""
    kickStatus.clearAll()
    for number in SETTINGS.robot_ids:
        CppPackage.setDribbleCommand(number, 0)


def stop_tasks():
    """返回两辆固定车号的 Stop Task；固定车号防止匹配到官方门将。"""
    return {f"P{n}": Task(Stop(), fixedNumber=n) for n in SETTINGS.robot_ids}


def snapshot(mode):
    """从 ZBin 读出一帧信息并转换为 core.Frame。

    mode 由当前 State 传入：normal 是普通进攻，restart 是我方定位球，defend
    是对方定位球。核心层只看统一的 phase 字符串，不直接接触 ZBin API。
    """
    raw_message = vision.getCurrentRefereeMsg()
    if mode == "restart":
        # 空消息时，SelectPlay 可能仍保持未 exit 的原定位球策略。
        phase = OUR_RESTARTS.get(raw_message)
        if phase is None and raw_message in LIVE_PLAY_MESSAGES and PLANNER.restart_key:
            phase = PLANNER.restart_key[0]
        if phase is None:
            phase = "halt"
    elif mode == "defend":
        phase = "normal" if Conditions.isGameOn() and raw_message in LIVE_PLAY_MESSAGES else "defend"
    else:
        # testStrategy 被意外调用时也再次核验裁判，防止测试模式绕过 HALT。
        phase = "normal" if raw_message in LIVE_PLAY_MESSAGES else "halt"
    # 逐辆读取两辆受控车；视觉无效时仍保留位置，但 valid=False 会使 core 安全停止。
    robots = {}
    for n in SETTINGS.robot_ids:
        valid = bool(Player.valid(n))
        robots[n] = Robot(point(Player.Pos(n)), float(Player.direction(n)), valid,
                          bool(Player.isInfraredOn(n)) if valid else False)
    # 收集可见敌方车/官方门将，供核心层选射门、传球空隙。
    enemies = tuple(point(Enemy.pos(n)) for n in range(Params.maxPlayer) if Enemy.valid(n))
    # SelectPlay 在裁判消息变化时记录 lastCycle；它识别一次新的定位球。
    token = int(Ball.gRefMsg["lastCycle"])
    # raw message 清空后保持原token，不制造第二次定位球。
    if mode == "restart" and raw_message in LIVE_PLAY_MESSAGES and PLANNER.restart_key:
        token = PLANNER.restart_key[1]
    # 将所有数据一次性装入纯 Python Frame，之后交给 core.Planner.step。
    # 这个 Falcon 版本在 Normal Start 后仍把底层 gameOn 置为假，但裁判消息
    # Ready 已明确表示比赛进入活球；两者任一成立都允许普通进攻策略执行。
    game_on = bool(Conditions.isGameOn()) or raw_message in LIVE_PLAY_MESSAGES
    return Frame(cycle=int(vision.getCycle()), ball=point(Ball.pos()), robots=robots,
                 enemies=enemies, ball_valid=bool(Ball.valid()),
                 game_on=game_on, can_kick=bool(vision.canEitherKickBall()),
                 phase=phase, ball_speed=float(Ball.velMod()), restart_token=token)


def action_skill(action, number):
    """将 core.Action 变成 RoleMatch 可以运行的 Skill。"""
    if action.kind == "stop":
        # Stop Skill 最终下发明确停止任务。
        return Stop()
    # 把纯 Python 坐标转回 ZBin 所需的 CGeoPoint。
    p = CGeoPoint(action.target.x, action.target.y)
    # 当核心层要求避球时叠加 dodge_ball；其它安全标志始终存在。
    flag = SAFE_FLAGS | (Flags.dodge_ball if action.avoid_ball else 0)
    if action.kind == "move":
        # 只有核心层进入近球控球阶段才开启吸球；接应站位等移动仍不带球。
        return RushTo(p, angle=action.direction, maxAcc=SETTINGS.max_acc,
                      flag=flag, needDribble=action.dribble)

    def execute(executor):
        """RoleMatch 已确定真实执行车号时，才实际计算并写入踢球命令。"""
        target = CGeoPoint(action.kick_target.x, action.kick_target.y)
        # 传球和射门各有独立命令量上限，但上限并不是球速单位。
        cap = SETTINGS.pass_power_cap if action.pass_ball else SETTINGS.shot_power_cap
        # 先使用当前 ZBin 已校准的 PowerCalculator 曲线按距离计算力度。
        calculated = evalPower(Ball.pos(), target, P.LOW if action.pass_ball else P.NORMAL)
        # 曲线出错/给出非有限数时把力度置零，绝不把 NaN 下发。
        power = min(cap, calculated) if math.isfinite(calculated) else 0.0
        # 执行前再确认视觉，防止读取后状态异常；不用 Shoot/JustKick 的隐式行为。
        if power > 0 and Ball.valid() and Player.valid(executor):
            kickStatus.setKick(executor, power)
        CppPackage.setDribbleCommand(executor, 0)  # 每次踢球前也明确关闭吸球。
        # 使用 RushTo 保持朝向；踢球命令本身不再吸球，避免出脚后仍拖住球。
        return CppPackage.makeItRushTo(executor, p, action.direction, flag=SAFE_FLAGS,
                                      maxAcc=SETTINGS.max_acc, needDribble=False)

    # Task 需要 (执行函数, 匹配位置)；车号固定后匹配位置只为接口完整性保留。
    return execute, lambda executor: p


class BaseState(State):
    """三种比赛 State 的公共骨架：清命令 -> 读快照 -> 策略 -> Task。"""
    mode = "normal"

    def getTasks(self):
        # 每帧先清掉旧的 kick/dribble，避免异常时残留上一帧命令。
        stop_actions()
        try:
            # 将当前 ZBin 数据变成 core 可测试的 Frame。
            f = snapshot(self.mode)
            # 纯 Python 决策器同时给两辆车生成 Action。
            actions = PLANNER.step(f)
            # 在 Client GUI 留下人能读懂的原因和关键裁判开关。
            Global.debugEngine.gui_debug_msg(CGeoPoint(-2500, 2600), "P2026: " + PLANNER.status)
            Global.debugEngine.gui_debug_msg(CGeoPoint(-2500, 2400),
                f"phase={f.phase} gameOn={f.game_on} ready={f.can_kick} restartEnabled={SETTINGS.enable_restart_kicks}")
            # 两个 Task 都绑定 settings.py 的真实车号，禁止框架随意换车。
            return {f"P{n}": Task(action_skill(actions[n], n), fixedNumber=n)
                    for n in SETTINGS.robot_ids}
        except Exception as error:
            # 正常有视觉时不应进入这里；每次异常都明确下停止任务。
            print(f"[Preliminary2026] stopped: {type(error).__name__}: {error}")
            stop_actions()
            return stop_tasks()

    def getMatchString(self):
        # 培训中 [] / () / {} 可让 RoleMatch 以不同频率自动分配车号。
        # 本初赛基线明确只控制 settings.py 给出的两辆车，所以 Task 已 fixedNumber；
        # 此处返回空字符串，避免自动匹配到官方门将或未授权车辆。
        return ""

    def transFunction(self):
        # NORMAL_START 可能先于首脚触球；不能此时退出而丢掉定位球两触约束。
        if self.mode == "restart":
            return "exit" if PLANNER.restart_done and Conditions.isGameOn() else ""
        if self.mode == "defend" and Conditions.isGameOn():
            return "exit"
        return ""


class NormalState(BaseState):
    """普通活球进攻状态。"""


class RestartState(BaseState):
    """我方开球、直接任意球、间接任意球状态。"""
    mode = "restart"


class DefendState(BaseState):
    """对方定位球状态：核心层只返回停止动作。"""
    mode = "defend"


@declare_state_machine(NormalState)
class PreliminaryNormal(StateMachine):
    """普通进攻的 StateMachine，只有一个长期运行状态。"""


@declare_state_machine(RestartState)
class PreliminaryRestart(StateMachine):
    """我方定位球 StateMachine；首触锁未解除前不允许退出。"""
    def hookBeforePlanTasks(self):
        # 告诉 SelectPlay：裁判策略尚未结束，继续让 RestartState 运行。
        Global.hasFinishedRefPlay = False


@declare_state_machine(DefendState)
class PreliminaryDefend(StateMachine):
    """对方定位球 StateMachine；保持策略占用以确保停止。"""
    def hookBeforePlanTasks(self):
        # 直到对方定位球结束前，禁止 SelectPlay 提前回普通进攻。
        Global.hasFinishedRefPlay = False
