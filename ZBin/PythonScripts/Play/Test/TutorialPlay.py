"""你的练习入口：0 号车到指定位置后停止；1 号车始终停止。

在 VS Code 中选择 SRC: tutorial，启动 run_demo.py --play tutorial。
先读工作区根目录的《从零写策略与仿真指南.md》。
"""

from CppPackage import CGeoPoint
from RoleMatch_LuaStyle import State, StateMachine, Task, declare_state_machine
from RoleMatch_LuaStyle.Skills import SimpleGoTo, Stop
from Vision import Player
from Vision import Ball;
import math;

# 改这些参数，保存并重新运行，就能观察效果。单位：毫米。
TARGET_X = -500
TARGET_Y = -500
ARRIVAL_DISTANCE = 100

A_X = -500
A_Y = -500
B_X = -1000
B_Y = -1000
C_X = -200
C_Y = -1000

class GoToA(State):
    def getTasks(self):
        return {
            "A": Task(SimpleGoTo(CGeoPoint(A_X, A_Y), angle=math.pi / 2), fixedNumber=0),
            "B": Task(Stop(), fixedNumber=1),
        }

    def getMatchString(self):
        # 本练习固定车号，不需要额外的角色匹配规则。
        return ""

    def transFunction(self):
        if not Player.isValid(0):
            return ""  # 没看到 0 号车时，继续等待。
        distance = Player.Pos(0).dist(CGeoPoint(A_X, A_Y))
        if distance < ARRIVAL_DISTANCE:
            print("[TUTORIAL] Arrived; switching to GoToB.", flush=True)
            return "GoToB"  # 必须与下面的类名完全相同。
        return ""  # 没到达时，保持当前状态。

class GoToB(State):
    def getTasks(self):
        return {
            "A": Task(SimpleGoTo(CGeoPoint(B_X, B_Y), angle=math.pi / 2), fixedNumber=0),
            "B": Task(Stop(), fixedNumber=1),
        }

    def getMatchString(self):
        # 本练习固定车号，不需要额外的角色匹配规则。
        return ""

    def transFunction(self):
        if not Player.isValid(0):
            return ""  # 没看到 0 号车时，继续等待。
        distance = Player.Pos(0).dist(CGeoPoint(B_X, B_Y))
        if distance < ARRIVAL_DISTANCE:
            print("[TUTORIAL] Arrived; switching to GoToC.", flush=True)
            return "GoToC"  # 必须与下面的类名完全相同。
        return ""  # 没到达时，保持当前状态。

class GoToC(State):
    def getTasks(self):
        return {
            "A": Task(SimpleGoTo(CGeoPoint(C_X, C_Y), angle=math.pi / 2), fixedNumber=0),
            "B": Task(Stop(), fixedNumber=1),
        }

    def getMatchString(self):
        # 本练习固定车号，不需要额外的角色匹配规则。
        return ""

    def transFunction(self):
        if not Player.isValid(0):
            return ""  # 没看到 0 号车时，继续等待。
        distance = Player.Pos(0).dist(CGeoPoint(C_X, C_Y))
        if distance < ARRIVAL_DISTANCE:
            print("[TUTORIAL] Arrived; switching to GoToA.", flush=True)
            return "GoToA"  # 必须与下面的类名完全相同。
        return ""  # 没到达时，保持当前状态。

class Hold(State):
    def getTasks(self):
        return {
            "A": Task(Stop(), fixedNumber=0),
            "B": Task(Stop(), fixedNumber=1),
        }

    def getMatchString(self):
        return ""

    def transFunction(self):
        return ""  # 永久停在这个状态，直到策略重新开始。

class FollowBall(State):
    def getTasks(self):
        if not Ball.valid():
            return {
                "A": Task(Stop(), fixedNumber=0),
                "B": Task(Stop(), fixedNumber=1),
            }

        ball = Ball.pos()
        target = CGeoPoint(ball.x() - 500, ball.y())

        return {
            "A": Task(SimpleGoTo(target), fixedNumber=0),
            "B": Task(Stop(), fixedNumber=1),
        }

    def getMatchString(self):
        return ""

    def transFunction(self):
        return ""  # 留在跟随球状态，每帧更新目标

class ShootBall(State):
    def getTasks(self):
        

# 列出全部状态；第一个 GoToPoint 是初始状态。
@declare_state_machine(FollowBall)
class TutorialPlay(StateMachine):
    pass
