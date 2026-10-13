"""离线核查踢球请求后的初段球路；不会启动/控制仿真。

用法：python shot_audit.py ../../logs/某次-blue.jsonl
只有连续两段高速轨迹才估算偏角。球不动、裁判中断、异常跳点，均不当作出球。
这仍无法完全排除人工摆球/碰撞；做标定时必须固定场景，不拖动球。
"""
import argparse
import json
import math
from pathlib import Path


def audit(rows):
    results = []
    for row in rows:
        for event in row.get('kick_events', []):
            result = dict(event, observed=False, reason='NO_CLEAN_FLIGHT')
            samples = [r for r in rows if event['now'] <= r.get('monotonic', -1) <= event['now']+.65]
            if samples and samples[0]['monotonic'] > event['now']:
                samples.insert(0,dict(monotonic=event['now'],mode='PLAY',ball_valid=True,ball=event['frame_ball']))
            segments = []
            for a, b in zip(samples, samples[1:]):
                if a['mode'] != 'PLAY' or b['mode'] != 'PLAY' or not a['ball_valid'] or not b['ball_valid']:
                    break
                dt = b['monotonic']-a['monotonic']
                dx, dy = b['ball'][0]-a['ball'][0], b['ball'][1]-a['ball'][1]
                speed = math.hypot(dx,dy)/dt if dt > 0 else math.inf
                if not .001 < dt < .2 or speed > 12000:
                    break
                if speed < 1500:
                    if segments:
                        break
                    continue
                if segments and abs(math.atan2(math.sin(math.atan2(dy,dx)-segments[0][2]),
                                               math.cos(math.atan2(dy,dx)-segments[0][2]))) > .08:
                    break  # 有明显转折，可能碰撞，不把反弹方向当固定偏角。
                segments.append((dx,dy,math.atan2(dy,dx)))
                if len(segments) == 2:
                    angle = math.atan2(sum(s[1] for s in segments),sum(s[0] for s in segments))
                    desired = math.atan2(event['target'][1]-event['frame_ball'][1],
                                         event['target'][0]-event['frame_ball'][0])
                    delta = lambda a,b: math.degrees(math.atan2(math.sin(a-b),math.cos(a-b)))
                    result.update(observed=True, reason='TWO_STRAIGHT_FAST_SEGMENTS',
                                  flight_angle_rad=angle, heading_error_deg=delta(angle,event['heading']),
                                  target_error_deg=delta(angle,desired))
                    break
            results.append(result)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('telemetry', type=Path)
    args = parser.parse_args()
    with args.telemetry.open(encoding='utf-8') as stream:
        rows = [json.loads(line) for line in stream if line.endswith('\n')]
    result = audit(rows)
    print(json.dumps(dict(requests=len(result), clean_flights=sum(x['observed'] for x in result),
                          events=result, warning='Not an official score or automatic calibration.'),
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
