"""测试守门员二：在门前限定矩形区域内随机游走。"""

import Global
from RoleMatch_LuaStyle import State, StateMachine, Task, declare_state_machine
from Vision import Ball, Player

from .goalie_common import RandomWalkController, goalie_rush_to


class GoalieRandomWalkState(State):
    def __init__(self):
        super().__init__()
        # 固定随机种子便于复现实验；想要每次路径不同可把 seed 改为 None 并调整控制器。
        self.controller = RandomWalkController(seed=2026)

    def getTasks(self):
        if Global.isPlaySwitched:
            self.controller.reset()

        goalie = Global.goalieNumber
        target = self.controller.choose_target(Player.Pos(goalie))
        face_at = Ball.pos() if Ball.valid() else target
        return {
            "G": Task(
                goalie_rush_to(target, face_at),
                fixedNumber=goalie,
                description="random walk in the goal-mouth rectangle",
            )
        }

    def getMatchString(self):
        return ""

    def transFunction(self):
        return ""


@declare_state_machine(GoalieRandomWalkState)
class Test_GoalieRandomWalk(StateMachine):
    pass
