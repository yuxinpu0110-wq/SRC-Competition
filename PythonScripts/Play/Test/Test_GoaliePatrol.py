"""测试守门员一：门前左右（Y 轴）匀速往返。"""

import Global
from RoleMatch_LuaStyle import State, StateMachine, Task, declare_state_machine
from Vision import Ball, Player

from .goalie_common import PatrolController, goalie_rush_to


# mm/s；这是给 RushTo 的期望横向速度，不是绕过底层规划器的直接电机命令。
CRUISE_SPEED = 650


class GoaliePatrolState(State):
    def __init__(self):
        super().__init__()
        self.controller = PatrolController(cruise_speed=CRUISE_SPEED)

    def getTasks(self):
        if Global.isPlaySwitched:
            self.controller.reset()

        goalie = Global.goalieNumber
        target, desired_velocity = self.controller.command(Player.posY(goalie))
        face_at = Ball.pos() if Ball.valid() else target
        return {
            "G": Task(
                goalie_rush_to(target, face_at, expected_velocity=desired_velocity),
                fixedNumber=goalie,
                description="fixed-speed goal-mouth patrol",
            )
        }

    def getMatchString(self):
        return ""

    def transFunction(self):
        return ""


@declare_state_machine(GoaliePatrolState)
class Test_GoaliePatrol(StateMachine):
    pass
