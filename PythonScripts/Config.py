"""蓝队两进攻车策略的 ZBin 配置入口。

只控制本队两辆进攻车，不能控制组委会官方门将。正式比赛不可使用 --test，
因为测试模式会绕过裁判策略；安装脚本会在替换本文件前备份旧配置。
"""
import Global
from BlueAttack2026.settings import SETTINGS

# 正式模式必须关闭测试模式，SelectPlay 才会先处理裁判消息。
Global.isTestMode = False
# 关闭框架海量调试输出；BLUE 的战术与规则保护提示仍会显示。
Global.needDetailedRuntimeDebugInfo = False
# 告诉官方停止策略哪些车受控；核心层同样用 fixedNumber 双重限制。
Global.controlledRobotNumbers = SETTINGS.robot_ids

from WorldModel import Params
from BlueAttack2026.medusa import BlueNormal, BlueRestart, BlueDefend
from Play.RefPlay.GameHalt import GameHalt
from Play.RefPlay.GameStop import GameStop
from Play.RefPlay.OurTimeout import OurTimeout

# 拒绝布尔、负数和超出本地最大编号范围的车号，避免误控机器人。
if any(type(n) is not int or not 0 <= n < Params.maxPlayer for n in SETTINGS.robot_ids):
    raise ValueError("请在 BlueAttack2026/settings.py 填有效机器人编号")


class GameStrategy:
    """根据 Global.isYellow 选择策略对象，并在每帧调用 planTasks。"""
    def __init__(self, blueTeam, yellowTeam=None):
        self.blueTeam = blueTeam  # 蓝队应运行的策略。
        self.yellowTeam = yellowTeam if yellowTeam is not None else blueTeam  # 黄队应运行的策略。

    def runStrategy(self):
        # 颜色由 zss.ini/main.py 设置；车号不等于颜色。
        (self.yellowTeam if Global.isYellow else self.blueTeam).planTasks()


# 普通活球时的两车进攻策略。
normal = GameStrategy(BlueNormal(), GameHalt())
# 我方开球、直接任意球、间接任意球策略。
restart = GameStrategy(BlueRestart(), GameHalt())
# 对方定位球时的保守停止策略。
defend = GameStrategy(BlueDefend(), GameHalt())
# HALT、点球、人工摆球等暂未实现流程的停止策略。
halt = GameStrategy(GameHalt())

# 保留本地官方的所有分发键；点球与人工摆球维持官方 Halt 行为。
Global.gameStrategies = {
    "NormalPlay": normal,
    "KickOff": restart,
    "KickOffDef": defend,
    "FreeKick": restart,
    "FreeKickDef": defend,
    "ourDirectKick": restart,
    "theirDirectKick": defend,
    "TimeOut": GameStrategy(OurTimeout()),
    "GameStop": GameStrategy(GameStop()),
    "GameHalt": halt,
    "PenaltyKick": halt,
    "theirPenaltyKick": halt,
    "BallPlace": halt,
    "TheirBallPlace": halt,
    "IfHalfField": False,
    "USE_ZPASS": False,
}

# 即使误开 --test，也只停；正式比赛仍应禁止 --test。
testStrategy = halt
