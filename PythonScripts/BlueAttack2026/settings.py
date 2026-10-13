"""2026 校内赛蓝队两车参数。距离为 mm，角度为 rad，时间为视觉帧。

800 mm 是主动停止带球的策略上限，低于规则的 1000 mm；预留量和前视量还会
进一步提前结束带球。踢球命令量不是球速，仿真/实车仍须测量 6.5 m/s 上限。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # 当前 grSim 中已有的蓝队两车；实物赛使用现场分配的编号。
    robot_ids: tuple[int, int] = (5, 6)
    tactic: str = "automatic"  # automatic / direct / wing / wall_pass
    enable_restart_kicks: bool = True
    shot_power_cap: float = 3500.0
    pass_power_cap: float = 2400.0
    max_acc: float = 3000.0
    field_margin: float = 160.0
    penalty_extra: float = 70.0
    role_hysteresis: float = 300.0
    role_min_frames: int = 25
    approach_distance: float = 220.0
    contact_center_distance: float = 100.0
    kick_ball_distance: float = 135.0
    kick_angle_tolerance: float = 0.14
    frontal_ball_tolerance: float = 0.38
    possession_settle_frames: int = 5
    kick_cooldown_frames: int = 24
    pass_wait_frames: int = 160
    restart_contact_frames: int = 3
    restart_in_play_mm: float = 50.0
    support_spacing: float = 1050.0
    pass_min_distance: float = 550.0
    pass_max_distance: float = 2700.0
    pass_clearance: float = 250.0
    shot_clearance: float = 205.0
    shot_range: float = 4300.0
    wing_y: float = 950.0
    carry_step: float = 140.0
    # 不能把“起点到当前点”当作带球距离；核心层逐帧累加路径长度。
    dribble_limit_mm: float = 800.0
    dribble_reserve_mm: float = 90.0
    dribble_lookahead_seconds: float = 0.12
    control_near_distance: float = 190.0
    release_distance: float = 300.0
    release_confirm_frames: int = 3
    max_control_frame_gap: int = 3
    intercept_horizon_seconds: float = 0.3
    frames_per_second: float = 60.0
    ball_progress_mm: float = 120.0
    stuck_frames: int = 300


SETTINGS = Settings()
