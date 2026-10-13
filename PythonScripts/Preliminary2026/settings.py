"""初学者最常修改的参数。

距离是 mm，角度是 rad，时间是视觉帧。每次训练只改一项，并记录旧值、新值和结果。
力度数值不是球速；实测球速合格前，不能把更大力度带到正式比赛。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    robot_ids: tuple[int, int] = (5, 6)  # 仿真当前可见的两辆蓝队进攻车；实物赛前改为现场分配编号。
    # 此处仅为本地 grSim 演示临时开启；实物赛前恢复 False，先实测裁判语义。
    enable_restart_kicks: bool = True
    # 力度是底层命令量，不是 mm/s；上限 4000 不证明球速 <= 6.5 m/s。
    shot_power_cap: float = 4000.0  # 射门命令量上限；必须由实测球速校准。
    pass_power_cap: float = 2600.0  # 传球命令量上限，通常小于射门。
    # 与当前 ZBin 默认 MNormalAcc=4500 对齐；这是本地仿真基准，实车应重新标定。
    max_acc: float = 4500.0  # 底层移动最大加速度，不是球速上限。
    field_margin: float = 150.0  # 目标点到边线的安全距离。
    penalty_extra: float = 60.0  # 车体半径外额外扩大的禁区距离。
    # Munkres 主攻列的代价权重；大于 1 表示“尽快处理球”比少走一点接应路更重要。
    striker_match_weight: float = 2.0
    # Munkres 候选分配的总代价必须至少优于当前分配这么多，才允许换主攻。
    role_hysteresis: float = 400.0
    role_min_frames: int = 60  # 两次换进攻车之间的最少帧数。
    approach_distance: float = 220.0  # 进攻车的球后等待距离。
    contact_center_distance: float = 98.0  # 最后接近时车中心距球的距离。
    kick_ball_distance: float = 128.0  # 认为车已靠近球、可踢的距离。
    kick_angle_tolerance: float = 0.10  # 允许踢球的最大朝向误差，约 5.7 度。
    # 车接近球后先开吸球并向前带一小段，再请求传球/射门，避免“碰球即踢”。
    possession_settle_frames: int = 8
    possession_carry_distance: float = 260.0
    kick_cooldown_frames: int = 75  # 两次踢球请求间的最少帧数。
    pass_wait_frames: int = 100  # 传球后优先交给接球车的帧数。
    restart_contact_frames: int = 3  # 第二触须连续红外近球的帧数。
    support_spacing: float = 1150.0  # 接应车横向拉开距离。
    ball_progress_mm: float = 150.0  # 移动超过此距离视为明显推进。
    # 约 6 秒时换射门角度/优先横传；不保证裁判不会判僵局。
    stuck_frames: int = 450


# 其它文件均从这里导入同一个不可变设置对象。
SETTINGS = Settings()
