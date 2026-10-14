# HZK_v3 评审与更新建议

评审日期：2026-10-14。对照目录：`../HZK_v3` 与当前 `Competition`。本次执行代码检查、离线测试、接口导入、只读端口探测及一次受控仿真尝试，未合入或替换比赛策略。

## 1. 结论

HZK_v3 有值得吸收的基础工程改进，但尚不能认定其比赛表现优于当前版本，不建议整包替换。

队友主要实现 V2.1 的 P0/P1 和部分 P2/P3：真实源码备份与版本入口、视觉证据、一次出球事务、动作期限、标定工具。文档也明确 P4/P5/P6 尚未完成。一脚接射、动态门将评分和高进球率验证均未交付。

当前首先要解决三个集成阻碍：原始视觉入口在本机不可用、所有队员必须同相机可见的要求过强、重复帧直接发 Stop。后两项已用纯 Python/官方状态机夹具复现。还需把我们较新的射门与接近修正保留下来。

## 2. 实际核查结果

活动代码是 `HZK_v3/PythonScripts`。`SRC-Competition-v2.1` 是早期开发副本，不应拿它代表当前代码。评审包括队友尚未提交的改动；仓库 HEAD 为 `2b7d0f8`，工作树另有 observation/run_match/benchmark/config 等修改及 vision_mirror 工具。不能只拿 HEAD 或漏掉未跟踪文件进行集成。

| 检查 | 本轮结果 |
|---|---|
| `unittest test_match test_v21 -q` | 157 项通过 |
| `unittest discover -s tests -q` | 7 项通过；一条解析失败日志来自错误曲线输入测试，最终 OK |
| `run_match.py --config ../configs/v2.1-foundation.json --check` | 配置及官方 Play/State/Task/Skill 原生导入通过 |
| SDK / 程序哈希 | 双方 MedusaCore.dll、CppPackage.pyd、Client.exe、grSim.exe 相同，未发现二进制修复 |
| 原始视觉 10020 | Client PID 39868 已占用，探测绑定报 WinError 10013 |
| 原始视觉 10021 | 可绑定，约 2 秒探测收到 0 帧；当前运行的模拟器配置仍向 10020 发包 |
| 强门将受控尝试 | 预检失败，门将进程在首个原生 postDecision 阶段发生 `0xc0000374`；没有取得有效进攻回合 |
| 本次攻击遥测 | 46 条，全部 NO_CAPTURE_EVIDENCE，fresh 为 0，未产生物理出球事件 |
| 队友已有标定 | 检查到四份 summary，全部 complete=false，没有可用实测标定数据 |

本轮强门将命令，在 HZK_v3 工作目录执行：

```powershell
& 'E:/Yuxin/Projects/Vex Robotics/SRC/Competition/ZBin/PythonScripts/.venv/Scripts/python.exe' -X utf8 PythonScripts/benchmark_match.py --config configs/v2.1-foundation.json --seconds 5 --goalie official --starts contact --passing off --frame-cycle paired
```

失败证据归档到 `Competition/验收记录/hzk-v3-review-20261014/`：报告 `strong-keeper-attempt-failed.json` 及对应攻守 JSONL、控制台。报告有原目录绝对路径，归档中保留了同名文件。

本次失败发生于预检，不能记作 0/3 射门成绩，也不能据此说其战术比旧版更差。可以确认原生崩溃未解决，当前本机视觉契约不能满足。测试结束已退出自身子进程并 HALT，保留用户 Client/grSim；结束检查未检测到 Python 策略进程。

## 3. 应当吸收的内容

| 内容 | 价值 | 合入要求 |
|---|---|---|
| `match_events.py::KickTransactions` | 请求、脉冲、离脚分开；一次事务最多一次确认；无离脚期限与重试预算 | 连同授权、状态转换及日志字段原子集成，不能只替换事件类 |
| `shot_accuracy.py` 的新帧稳定证据 | 重复捕获不累计稳定时间，减少假稳定 | 接入可信帧时钟；保留当前速度交叉核验，补重复帧控制测试 |
| `ball_control.py::ActionBudget` | 接近/对准有进展与期限，失败可退出 | 并入我们现有绕球规划器，避免覆盖较新修正；增加真实路径进展测试 |
| Normalplay 的 `AbortRelease/ObserveFlight` | 出脚失败先离球；确认离脚后不再追着球踢 | 与当前传球会话、重开球保护一起回归；支持提前终止观察而非死等 |
| `match_tactics.py::segment_legal/safe_waypoint` | 目标点合法之外，增加线段与机器人实体余量检查 | 扩展到所有 MoveTo/接应/退球任务，并验证原生实际轨迹 |
| `runtime_manifest.py`、run_id/PID 日志匹配 | 运行清单更完整，减少挑到其他进程旧日志 | 使用我们的目录结构及独立归档路径，不覆盖旧记录 |
| `kick_calibration.py` 和标定/分析工具 | 失败进入分母，区分测量窗口速度与瞬时初速度，保留不能用于规划的状态 | 修好视觉和预检后实际标定；不要把工具存在当作已经标定 |
| 真实旧版源码入口 | 对照确实执行旧代码，而不仅换一个配置 | 我们建立自己的当前 V2 基线，不直接把队友的旧快照当我们的最新版 |

## 4. 必须先修的具体问题

### 4.1 本机原始视觉链路未打通

配置要分清：`v2.1-foundation.json` 没有 raw_vision_port 字段，走代码默认 10020，实际绑定失败；`v2.1-calibration.json` 显式设为 10021，本机该端口没有数据。前面沟通中把基础配置也概括为默认 10021 不够准确，以本段核查结果为准。

现有 `tools/vision_mirror.py` 只转发 10021→10020，不能自动让仍向 10020 发包的 grSim 改发 10021。队友归档的 mirror 为 180 秒 0 包，也未证明链路成功。

建议设计明确的单入口分流：模拟器发送到可共享/专用输入端口，一份接收器把原包分发给 Client 与只读证据接收器。所有端口和启动顺序显式配置，并实际验证收包、Client FPS、两队数据一致性。或者使用经验证的融合帧接口。禁止靠放宽 freshness 或回退 Python 循环编号绕过证据检查。

这项需要可恢复地配置实际运行的模拟器和转发服务；本轮评审没有擅自改其端口或关闭用户窗口。

### 4.2 同一相机必须同时看到球和所有受控车

位置：`observation.py::RawVision.associated_capture`、`FreshnessGuard.evaluate`。

代码对全部 players 使用 all 检查。构造 camera 0 有球和 0 号、camera 1 有 1 号，融合视觉两车与球均有效，函数仍返回 None。这不是时间过期，而是正常跨相机站位被判成没有证据。传球拉开阵型或 full 模式加入远端门将后风险更大。

修复应分别关联对象的时间与所属相机，或采用可信融合快照；发球只要求关键对象具有时间一致的证据，接应者证据单独维护。缺少支持车证据应停其任务/取消配合，不应在主攻车与球证据有效时永久停掉整个队。不能简单取消所有关联检查。

### 4.3 重复但仍新鲜的帧会反复制动

位置：`Normalplay.py::hookBeforePlanTasks` 中 `safety_stop or not f.new_capture` 分支，以及 `run_match.py` 的 idle 零命令。

官方状态机夹具实测：

```text
t=0.020，新捕获：Shoot，给 0/1 号 RushTo，motion_ids=[0,1]
t=0.021，重复捕获但 fresh=True：Hold，给两车 Stop，motion_ids=[]
t=0.022，重复捕获但 fresh=True：Hold，再给两车 Stop
t=0.040，新捕获：Shoot，再给两车 RushTo
```

可确认重复帧会发 Stop；对真实运动速度的影响还需链路打通后测。若 SDK 回调频率高于视觉帧率，这种处理可能造成走停交替。现有测试只断言重复帧不踢球，未检查是否不必要地打断运动。

修复方向：同一新鲜快照不重复累计稳定时间、不新建踢球事务，但在明确短 TTL 内维持已验证的无球运动任务，或把运动决策调度到真实新帧节奏。裁判变化/数据过期/非法路径仍应立即抢占停车。必须尊重 SDK pre/post 调用契约，不能直接跳过 post。补测“重复帧维持有界运动、过期立即停车”。

### 4.4 基于较旧快照，覆盖会丢失现有改进

当前 Competition 有而 HZK_v3 活动代码没有的内容包括：临球 `lock_contact_aim`、接触区 `settle_on_contact`、`observe_native_velocity`、接应可达性 `pass_planner` 及短时门将模型。它的近球目标在同球位、主攻距离 100mm、门将从 y=+250 变到 −250 的测试里，于 t=2s 从门角 −340 切到 +340，仍会触发大角度重瞄。

两者也有不同绕球方案：队友使用每次取较短角方向，我们当前有固定方向和终点越过处理。本轮终点越过简单用例在队友版本返回约 `(-320,0)`，因此不能笼统断言其绕行必然更差；需要同时测噪声导致换向及终点越过。

采用逐功能合入：保留临球短期承诺，并吸收有界失效；保留接触停车/速度观察作为可对照实验，按真实帧时间调整；保留接应评估模块。当前门将预测本身也未验收，不因“保留”就默认启用。

### 4.5 标定预检原因不准确，已有失败不能归因于门将

位置：`benchmark_match.py` 摆位条件及超时错误。

`--goalie none` 时 goalie_ready 为 true，但若对手旧视觉未清除或球偏移超过 8mm，仍可落入通用 `GOALIE_START_NOT_CONFIRMED`。队友最后一份空门报告正是 goalie_mode=none 却得到这个错误，说明错误文字无法解释实际阻断条件。

应输出每项布尔条件、观察值与期望值：我方位置/方向、球偏移、对手清空、视觉 freshness、门将存活及位置。当前预检也没有把 V2.1 observation.fresh 列为开始比赛的必要条件；应加入，避免启动无视觉的空跑。

### 4.6 目录与环境不能照搬

队友使用 flat 布局，当前我们是 Competition/ZBin 嵌套布局。runtime_paths、version_entry、备份入口和文档中的 `D:/桌面/ZBin` 均需适配。尤其 runtime_paths 的 nested 默认运行时位置是 ROOT.parent，不能直接认为那就是我们的 Competition/ZBin；集成时显式配置并打印正确运行时路径。

同样二进制无需复制。保留我们现有虚拟环境与用户源码，先建立基线再引入依赖。

## 5. 建议更新顺序与验收

1. 保存双方源码与未提交改动清单，建立独立集成分支和真实旧版入口。
2. 先修视觉输入及跨相机证据；独立测试至少覆盖不同半场/不同相机的主攻和支持车。
3. 修重复帧制动，再测每秒新捕获数、决策次数、Stop 次数与真实运动速度，确认不会无故反复停车。
4. 合入 KickTransactions、状态退出和运行清单；保留最新接触/目标承诺改动。测试一次离脚只记一次、空发有界、HALT/stale/reset 不补确认。
5. 修标定预检，完成空门单车有效标定，定位低速；真实球速/角偏差/延迟数据到位后再改力度和稳定阈值。
6. 在强门将存活且完整运行下做新旧同初态对照；原生崩溃留作运行失败，不记成未进球或剔除。
7. 继续按 V2.1 执行文档做接应出射姿态、动态 Gate 与门将拦截时间评分。假动作待响应实验有证据再启用。

本轮授权是评估，因此只新增评审文件和失败证据，没有执行上述策略合并。队友工程成果可减少基础开发工作，但“运动快、射得准、快接快射、强门将高进球率”仍需实际完成与验收。
