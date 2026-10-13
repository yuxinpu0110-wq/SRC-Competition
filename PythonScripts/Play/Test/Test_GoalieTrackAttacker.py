"""测试守门员三：根据进攻球员来向预测并调整门前站位和朝向。"""

import Global
from RoleMatch_LuaStyle import State, StateMachine, Task, declare_state_machine

from .goalie_common import (
    attacker_intercept_target,
    fallback_tracking_target,
    goalie_rush_to,
    select_threat,
)


class GoalieTrackAttackerState(State):
    def getTasks(self):
        goalie = Global.goalieNumber
        threat = select_threat()
        if threat is None:
            target, face_at = fallback_tracking_target()
        else:
            # 目标是进攻者运动射线与门前横线的交点；face_at 使车头也持续转向威胁。
            target, face_at = attacker_intercept_target(threat)

        return {
            "G": Task(
                goalie_rush_to(target, face_at),
                fixedNumber=goalie,
                description="track the most dangerous attacking opponent",
            )
        }

    def getMatchString(self):
        return ""

    def transFunction(self):
        return ""


@declare_state_machine(GoalieTrackAttackerState)
class Test_GoalieTrackAttacker(StateMachine):
    pass
