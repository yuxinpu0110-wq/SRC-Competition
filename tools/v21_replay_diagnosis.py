r"""只读复核 V2.1 文档中的三段球路，不控制仿真、不修改策略。

用法（工作目录 Competition）：
  .\ZBin\PythonScripts\.venv\Scripts\python.exe tools/v21_replay_diagnosis.py

这些固定时间点是人工选出的可辨识出球段，不是自动判定的全部物理射门。
0.10～0.40 秒差分是融合轨迹速度，不是踢球瞬间的真实初速度。
"""
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / '验收记录' / 'v21-diagnosis-20261013'


def load_rows(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip()]


def wrap(angle):
    return math.atan2(math.sin(angle), math.cos(angle))


def measure(rows, event_time):
    events = [event for row in rows for event in row.get('kick_events', [])]
    event = min(events, key=lambda value: abs(value['now'] - event_time))
    if abs(event['now'] - event_time) > .02:
        raise ValueError('Selected event not found; use the archived run, not another run.')
    selected = [row for row in rows
                if event['now'] <= row['monotonic'] <= event['now'] + 2
                and row['mode'] == 'PLAY' and row.get('ball_valid')]
    flight = [row for row in selected
              if .10 < row['monotonic'] - event['now'] < .40]
    if len(flight) < 2:
        raise ValueError('Not enough flight samples')
    a, b = flight[0], flight[-1]
    dx, dy = b['ball'][0] - a['ball'][0], b['ball'][1] - a['ball'][1]
    dt = b['monotonic'] - a['monotonic']
    if dt <= 0 or math.hypot(dx, dy) < 50:
        raise ValueError('Selected flight interval is not a measurable departure')
    desired = math.atan2(event['target'][1] - event['frame_ball'][1],
                         event['target'][0] - event['frame_ball'][0])
    with_keeper = [row for row in selected if '2' in row.get('opponent_robots', {})]
    nearest = min(with_keeper,
                  key=lambda row: math.dist(row['ball'], row['opponent_robots']['2']))
    return {
        'request_monotonic': event['now'],
        'requested_power': event['power'],
        'flight_interval_s': [a['monotonic'] - event['now'], b['monotonic'] - event['now']],
        'fused_segment_speed_mm_s': math.hypot(dx, dy) / dt,
        'target_angle_error_deg': math.degrees(wrap(math.atan2(dy, dx) - desired)),
        'nearest_keeper_after_request_s': nearest['monotonic'] - event['now'],
        'nearest_sampled_keeper_distance_mm': math.dist(nearest['ball'], nearest['opponent_robots']['2']),
        'warning': 'Selected fused trajectory segment; not raw initial speed or automatic save proof.',
    }


def main():
    rows = load_rows(ARCHIVE / 'benchmark-targeted-1791906533953309900.jsonl')
    report = json.loads((ARCHIVE / 'strong-keeper-contact-report.json').read_text(encoding='utf-8'))
    result = {
        'complete': report['complete'],
        'trials': len(report['trials']),
        'goal_crossings': sum(trial['goal_crossing'] for trial in report['trials']),
        'selected_flights': [measure(rows, now) for now in (13804.341, 13822.511, 13841.317)],
        'scope': 'Read-only replay of the archived three-scene diagnostic; not win-rate validation.',
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
