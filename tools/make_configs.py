"""生成实验配置；不改默认比赛入口。"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
c = json.loads((ROOT/'match.json').read_text(encoding='utf-8'))
c.update(upgrade_version='2.1', vision_max_age_s=.2, vision_match_tolerance_mm=60, raw_camera_id=-1,
    kick_pulse_seconds=.18, kick_departure_timeout=.45, kick_max_attempts=2, flight_observe_seconds=1.5,
    action_no_progress_s=.65, action_align_timeout_s=1., target_commit_timeout_s=2.5,
    enable_one_touch=False, enable_dynamic_keeper=False, log_interval=.02)
(ROOT/'configs').mkdir(exist_ok=True)
(ROOT/'configs/v2.1-foundation.json').write_text(json.dumps(c,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
d = dict(c, calibration_mode=True,calibration_power=3500,kick_max_attempts=1,
    contact_distance=100,contact_forward_max=110,calibration_placement_distance_mm=100)
(ROOT/'configs/v2.1-calibration.json').write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
