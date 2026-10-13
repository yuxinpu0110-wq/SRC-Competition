# 三个练习守门员

这些文件是对 `Test_Goalie2022.py` 的 Python 层接口做的替代测试策略：

| 文件 | 行为 |
| --- | --- |
| `Test_GoaliePatrol.py` | 在门前安全横线两端往返，向 `RushTo` 传入固定的 Y 方向期望速度。 |
| `Test_GoalieRandomWalk.py` | 在门前的安全矩形内随机选点，到达后才换目标。 |
| `Test_GoalieTrackAttacker.py` | 选择最危险的敌方车，按其速度方向（静止时用车头方向）预测到门前横线的交点，并始终面向它。 |

共同参数和“守门员身份 + RushTo”适配在 `goalie_common.py`；所有长度均为 mm，速度为 mm/s。`GOAL_LINE_OFFSET`、`POST_MARGIN`、`DEFAULT_MAX_ACC` 和各控制器构造参数是主要调参点。

## 安全边界

当前 `Config.py` 是初赛两进攻车的正式配置。根据校赛规则，初赛对阵的是组委会提供、只承担守门职责的官方守门员；所以**不要把这些练习策略接到正式 `Preliminary2026` 配置**，也不要改动 `SETTINGS.robot_ids` 去控制官方门将。

这三份文件仅适合在具有明确授权的仿真/训练环境中使用。它们不踢球、不吸球，也不尝试清球；视觉中守门车、球或敌方车失效时由各分支保守停止或回中。

## 单独测试

先停止正在运行的策略进程，在测试用的 `Config.py`（不要直接提交为正式配置）中临时加入一个策略并打开测试模式。例如测试“按进攻方向防守”时：

```python
import Global
from Play.Test.Test_GoalieTrackAttacker import Test_GoalieTrackAttacker

Global.isTestMode = True
testStrategy = GameStrategy(Test_GoalieTrackAttacker())
```

随后在 `ZBin` 根目录启动：

```powershell
& '.\PythonScripts\.venv\Scripts\python.exe' -X utf8 -B '.\PythonScripts\main.py' --sim --team blue --side left --test
```

启动前确认 `Global.goalieNumber` 是测试场景中**已授权**的守门车号。依次测试：无球/无敌车回中、低速斜向逼近、快速直线射门、门柱附近来球和视觉短暂丢失。真实场地前还要重新标定速度与加速度，且任何场上裁判指令优先。
