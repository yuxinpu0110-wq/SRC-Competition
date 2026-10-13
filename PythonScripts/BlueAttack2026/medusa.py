"""蓝队两进攻车与真实 ZBin/Medusa 的接口。"""

import json
import math
import os
from dataclasses import asdict, replace
from pathlib import Path

import CppPackage
import Global
from Geometry import CGeoPoint
from RoleMatch_LuaStyle import State, StateMachine, Task, declare_state_machine
from RoleMatch_LuaStyle.Skills import RushTo, Stop
from Vision import Ball, Enemy, Player, vision
from WorldModel import Conditions, Flags, Params, kickStatus
from WorldModel.PowerCalculator import P, evalPower

from .core import Field, Frame, Planner, Point, Robot
from .settings import SETTINGS as DEFAULT_SETTINGS


SETTINGS = replace(DEFAULT_SETTINGS, tactic=os.environ.get("BLUE_ATTACK_TACTIC", DEFAULT_SETTINGS.tactic))
PLANNER = Planner(SETTINGS, Field(Params.pitchLength, Params.pitchWidth,
                  Params.goalWidth, Params.penaltyDepth, Params.penaltyWidth,
                  Params.playerRadius))
OUR_RESTARTS = {"OurKickOff": "kickoff", "OurDirectKick": "direct", "OurIndirectKick": "indirect"}
SAFE_FLAGS = Flags.allow_dss | Flags.dodge_our_defense_box
LAST_FRAME = None
LAST_ACTIONS = {}
LAST_POWER = {}
_last_sample_key = None
_log_path = os.environ.get("BLUE_ATTACK_LOG")
_log_stream = None


def point(p):
    return Point(float(p.x()), float(p.y()))


def disable_ball_actions():
    kickStatus.clearAll()
    for number in SETTINGS.robot_ids:
        CppPackage.setDribbleCommand(number, 0)


def stop_tasks():
    return {f"B{n}": Task(Stop(), fixedNumber=n) for n in SETTINGS.robot_ids}


def snapshot(mode="normal"):
    """保留 Ready 的活球语义，空裁判消息本身不授权启动。"""
    message = vision.getCurrentRefereeMsg()
    ready = message == "Ready"
    game_on = bool(Conditions.isGameOn()) or ready
    token = int(Ball.gRefMsg["lastCycle"])
    if message in OUR_RESTARTS:
        phase = OUR_RESTARTS[message]
    elif mode == "restart" and message in ("", "Ready") and PLANNER.restart_key:
        phase, token = PLANNER.restart_key
    elif message in ("", "Ready"):
        phase = "normal"
    else:
        phase = "halt" if mode != "defend" else "defend"
    robots = {}
    for n in SETTINGS.robot_ids:
        valid = bool(Player.valid(n))
        robots[n] = Robot(point(Player.Pos(n)), float(Player.direction(n)), valid,
                          bool(Player.isInfraredOn(n)) if valid else False)
    return Frame(cycle=int(vision.getCycle()), ball=point(Ball.pos()), robots=robots,
                 enemies=tuple(point(Enemy.pos(n)) for n in range(Params.maxPlayer) if Enemy.valid(n)),
                 ball_valid=bool(Ball.valid()), game_on=game_on,
                 can_kick=bool(vision.canEitherKickBall()) and phase in ("kickoff", "direct", "indirect"),
                 phase=phase, ball_speed=float(Ball.velMod()), restart_token=token,
                 ball_velocity=point(Ball.vel()))


def action_skill(action, number):
    if action.kind == "stop":
        return Stop()
    target = CGeoPoint(action.target.x, action.target.y)
    flag = SAFE_FLAGS | (Flags.dodge_ball if action.avoid_ball else 0)
    if action.kind == "move":
        return RushTo(target, angle=action.direction, maxAcc=SETTINGS.max_acc,
                      flag=flag, needDribble=action.dribble)

    def execute(executor):
        # 只在当前帧视觉与裁判仍有效时下令；复用已有力度曲线。
        CppPackage.setDribbleCommand(executor, 0)
        kick_target = CGeoPoint(action.kick_target.x, action.kick_target.y)
        calculated = evalPower(Ball.pos(), kick_target, P.LOW if action.pass_ball else P.NORMAL)
        cap = SETTINGS.pass_power_cap if action.pass_ball else SETTINGS.shot_power_cap
        power = min(cap, max(0.0, calculated)) if math.isfinite(calculated) else 0.0
        if power > 0 and Player.valid(executor) and Ball.valid():
            kickStatus.setKick(executor, power)
            LAST_POWER[executor] = power
        return CppPackage.makeItRushTo(executor, target, action.direction, flag=flag,
                                      maxAcc=SETTINGS.max_acc, needDribble=False)
    return execute, lambda executor: target


def log_frame(f, actions):
    global _log_stream
    if not _log_path:
        return
    if _log_stream is None:
        destination = Path(_log_path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        _log_stream = destination.open("a", encoding="utf-8", buffering=1)
    _log_stream.write(json.dumps({"frame": asdict(f), "actions": {n: asdict(a) for n, a in actions.items()},
                                 "metrics": PLANNER.metrics, "tactic": PLANNER.current_tactic,
                                 "status": PLANNER.status, "lock": PLANNER.locked_kicker,
                                 "power": LAST_POWER}, ensure_ascii=False) + "\n")


class BaseState(State):
    mode = "normal"

    def getTasks(self):
        global LAST_FRAME, LAST_ACTIONS, _last_sample_key
        disable_ball_actions()
        LAST_POWER.clear()
        try:
            LAST_FRAME = snapshot(self.mode)
            sample_key = (LAST_FRAME.cycle, LAST_FRAME.phase, LAST_FRAME.restart_token,
                          LAST_FRAME.game_on, LAST_FRAME.can_kick)
            fresh = sample_key != _last_sample_key
            if Global.isYellow:
                LAST_ACTIONS = PLANNER.stop_all("blue strategy only")
            elif not fresh:
                # Client 不同相机可能重复送出同一融合帧。持续移动任务可重复登记，
                # 但本帧的踢球命令只能执行一次，不让重复帧打断吸球或重复踢球。
                LAST_ACTIONS = {n: replace(a, kind="move", kick_target=None, dribble=False)
                                if a.kind == "kick" else a for n, a in LAST_ACTIONS.items()}
            else:
                LAST_ACTIONS = PLANNER.step(LAST_FRAME)
            _last_sample_key = sample_key
            Global.debugEngine.gui_debug_msg(CGeoPoint(-2800, 2600),
                f"BLUE: {PLANNER.current_tactic} | {PLANNER.status}")
            Global.debugEngine.gui_debug_msg(CGeoPoint(-2800, 2400),
                f"lock={PLANNER.locked_kicker} carry={PLANNER.dribble_distance}")
            if fresh:
                log_frame(LAST_FRAME, LAST_ACTIONS)
            return {f"B{n}": Task(action_skill(LAST_ACTIONS[n], n), fixedNumber=n)
                    for n in SETTINGS.robot_ids}
        except Exception as error:
            print(f"[BlueAttack2026] stopped: {type(error).__name__}: {error}", flush=True)
            disable_ball_actions()
            return stop_tasks()

    def getMatchString(self):
        return ""

    def transFunction(self):
        if self.mode == "restart":
            return "exit" if PLANNER.restart_done else ""
        if self.mode == "defend":
            return "exit" if vision.getCurrentRefereeMsg() == "Ready" or Conditions.isGameOn() else ""
        return ""


class NormalState(BaseState):
    pass


class RestartState(BaseState):
    mode = "restart"


class DefendState(BaseState):
    mode = "defend"


@declare_state_machine(NormalState)
class BlueNormal(StateMachine):
    pass


@declare_state_machine(RestartState)
class BlueRestart(StateMachine):
    def hookBeforePlanTasks(self):
        Global.hasFinishedRefPlay = False


@declare_state_machine(DefendState)
class BlueDefend(StateMachine):
    def hookBeforePlanTasks(self):
        Global.hasFinishedRefPlay = False
