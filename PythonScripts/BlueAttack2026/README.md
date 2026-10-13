# 蓝队两车进攻程序

2026-10-13 重写，当前蓝队固定车号为 5、6；在 `settings.py` 的 `robot_ids` 修改。
`Config.py` 已接入本包，正式路径使用裁判调度；黄队不会执行这套进攻程序。

## 三套打法与自动选择

| `Settings.tactic` | 行为 |
| --- | --- |
| `direct` | 球后取球、吸球稳定，开阔且进入射程时射门；门将封线时传给另一车。 |
| `wing` | 在中路受阻时短距离带球拉开横向空间，边路横传或选择球门空侧射门。 |
| `wall_pass` | A 传 B，A 向前接应，B 回传 A，再射门。 |
| `automatic` | 按射门线路、球的横向位置和已开始的二过一流程选择上述打法。 |

`core.py` 管逐帧决策，`medusa.py` 将决策转换成已有 Skill/Task 和 C++ 指令。
不用重新编译 C++，停止旧策略进程后重新启动 Python 即可。

## 二次触球与带球过度

规则 5.1/5.3：开球、直接/间接任意球等首触机器人，必须等另一机器人触球后才能
再次触球。球移动**超过** 50 mm 才视为比赛开始。规则 5.5 要求间接任意球另一次
触球后才可得分。程序对所有我方定位球都先传队友：首脚命令一发就锁住该车，
不能靠超时、切换状态、转入 NormalPlay 或首脚踢空自动解锁。锁住的车关闭吸球、
远离球；球没有移过 50 mm 时接球队友保持等待。队友连续 3 个新视觉帧同时满足
真实红外、球在车头前、距离不超过 135 mm，才解除首触锁。敌车仅靠近球不作为
确实触球证据；如果是敌方已经触球，程序可能比规则要求更久地保留锁。

规则 6.2：未与球明显分离时连续控球超过 1 m 违规。程序逐段累加球的路程，并取
球/车各段路程的较大值作为保守预算，**不会只算起点到终点的直线距离**。主动
带球上限设为 800 mm，另扣 90 mm 制动余量和按实测球速计算的前视量。预算不足
时对正就传球/射门，否则关闭吸球并退开。红外闪断、绕圈、换角色、裁判暂停都
不会给新预算；球与车相距超过 300 mm 连续 3 帧才确认分离。控球视觉跳帧超过
3 帧时耗尽预算，主动放球。

程序还限制车体进入两侧禁区，保留官方 STOP/HALT/暂停策略，限制出脚命令量。
命令量并非球速：规则球速上限 6.5 m/s 需要用仿真/现场反馈测量。

## 检查与运行

在 `D:\桌面\ZBin` 根目录运行：

```powershell
$env:PYTHONPATH = '.\PythonScripts'
& '.\PythonScripts\.venv\Scripts\python.exe' -X utf8 -B -m unittest discover -s '.\PythonScripts\BlueAttack2026\tests' -v
& '.\PythonScripts\.venv\Scripts\python.exe' -X utf8 -B '.\PythonScripts\main.py' --sim --team blue --side left
```

单次真实软件仿真：先启动 `grSim.exe` 和 `Client.exe`，在 Client 的 Vision 栏选择
Simulation 并点击连接。然后分别运行下面命令，避免同时运行多个蓝队控制进程。
仿真入口会设置两辆蓝队车和一辆黄色陪练门将、发送真实裁判消息，结束时 HALT。

```powershell
& '.\PythonScripts\.venv\Scripts\python.exe' -X utf8 -B '.\PythonScripts\BlueAttack2026\run_simulation.py' --scenario open --tactic direct --seconds 35
& '.\PythonScripts\.venv\Scripts\python.exe' -X utf8 -B '.\PythonScripts\BlueAttack2026\run_simulation.py' --scenario wing --tactic wing --seconds 45
& '.\PythonScripts\.venv\Scripts\python.exe' -X utf8 -B '.\PythonScripts\BlueAttack2026\run_simulation.py' --scenario wall --tactic wall_pass --seconds 45
& '.\PythonScripts\.venv\Scripts\python.exe' -X utf8 -B '.\PythonScripts\BlueAttack2026\run_simulation.py' --scenario kickoff --tactic automatic --seconds 45
& '.\PythonScripts\.venv\Scripts\python.exe' -X utf8 -B '.\PythonScripts\BlueAttack2026\run_simulation.py' --scenario indirect --tactic automatic --seconds 45
```

仿真入口中的旧红外兼容桥只用于本机 grSim：将实际布尔反馈转换为 Medusa 要求
的浮点反馈，不合成触球。`infrared_feedback` 记录收到的反馈包和触球帧数量，
若没有反馈包或有错误，就不能把定位球解锁当作已验证。

## 本次验证记录

37 项离线规则/策略测试通过，覆盖环形带球、红外闪断、帧缺口、角色/暂停切换、
模糊分离距离、49/50/51 mm 边界、踢空不补踢、来球接收和间接球不能首脚射门。真实 ZBin
绑定与配置已通过只读导入。

已启动 grSim、Client 并接通视觉，实际跑过蓝队直攻。**这次软件仿真尚未完成
传球、射门或合法第二触验收**：第一轮出现摆球滤波瞬态；后续已观察到机器人
移动和吸球命令，但尚未观察到踢球。发现重复融合视觉帧会打断吸球，已修复；
发现本地 grSim 布尔红外与 Client 浮点红外格式不兼容，已补兼容桥，尚未在软件
中重新验证。用户按 Esc 中断软件界面控制后停止继续操作。

`simulation_results` 中日志是诊断记录，不能把出脚请求当作实际触球，也不能把
预算计数器当作裁判犯规判定。后续应先确认摆球后的球/车实际位置和红外反馈，
再运行五种场景，并核对出球、第二触、球速及连续带球的观察证据。

修改前的 `Config.py` 与 `zss.ini` 位于根目录 `backup-blue-attack-20261013`。
