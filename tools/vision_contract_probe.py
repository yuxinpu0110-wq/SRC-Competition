"""只读查看原始 camera/帧/时间；不初始化 SDK、不发控制命令。"""
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str((ROOT/'PythonScripts' if (ROOT/'PythonScripts').is_dir() else ROOT/'ZBin/PythonScripts')))
from observation import RawVision


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--camera', type=int, default=0)
    parser.add_argument('--seconds', type=float, default=3)
    args = parser.parse_args()
    if not 0 <= args.camera <= 15 or not 0 < args.seconds <= 30:
        parser.error('invalid camera or seconds')
    raw = RawVision(camera=args.camera)
    samples, last = [], None
    try:
        until = time.perf_counter()+args.seconds
        while time.perf_counter() < until and not raw.error:
            c = raw.latest
            if c is not None and c.frame_id != last:
                samples.append(dict(frame_id=c.frame_id, capture_time=c.capture_time,
                    receive_time=c.receive_time, ball=[c.ball.x,c.ball.y] if c.ball else None,
                    robots={f'{team}:{n}':[p.x,p.y] for (team,n),p in c.robots.items()}))
                last = c.frame_id
            time.sleep(.005)
    finally:
        raw.close()
    result = dict(camera=args.camera, samples=samples, error=raw.error,
                  fused_timestamp='UNKNOWN', motion_commands_sent=False)
    out = ROOT/'验收记录/v21-vision-probe.json'
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(camera=args.camera, samples=len(samples), error=raw.error, report=str(out)),ensure_ascii=False))
    return 0 if samples and not raw.error else 1


if __name__ == '__main__':
    sys.exit(main())
