"""入门演示：0、1 号机器人在两组目标点之间往返，坐标单位为毫米。"""

from CppPackage import CGeoPoint
from RoleMatch_LuaStyle import State, StateMachine, Task, declare_state_machine
from RoleMatch_LuaStyle.Skills import SimpleGoTo
from Vision import Player


class MoveLeft(State):
    target_x = -1000
    next_state = "MoveRight"

    def getTasks(self):
        # A、B 是角色名；fixedNumber 指定实际机器人编号。
        return {
            "A": Task(SimpleGoTo(CGeoPoint(self.target_x, -700)), fixedNumber=0),
            "B": Task(SimpleGoTo(CGeoPoint(self.target_x, 700)), fixedNumber=1),
        }

    def getMatchString(self):
        return ""

    def transFunction(self):
        # 两辆车都到达目标附近（距离小于 100 mm）后切换状态。
        for number, y in ((0, -700), (1, 700)):
            if not Player.isValid(number):
                return ""
            if Player.Pos(number).dist(CGeoPoint(self.target_x, y)) > 100:
                return ""
        print(f"[DEMO] Reached x={self.target_x}; next: {self.next_state}", flush=True)
        return self.next_state


class MoveRight(MoveLeft):
    target_x = 1000
    next_state = "MoveLeft"


@declare_state_machine(MoveLeft, MoveRight)
class DemoMove(StateMachine):
    pass
