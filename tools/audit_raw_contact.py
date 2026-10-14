"""只读原始接触诊断摘要；不会把位姿可用当作动态出脚认证。"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path


def audit(path):
    observations=Counter(); contacts=Counter(); phases=Counter(); poses={}; ready=[]
    rows=0
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if not line.endswith('\n'): break
            row=json.loads(line); rows+=1
            observations[(row.get('observation') or {}).get('reason','MISSING')]+=1
            phases[row.get('phase','MISSING')]+=1
            for number,pose in row.get('robots',{}).items():
                if pose and all(math.isfinite(v) for v in pose):poses.setdefault(number,[]).append(pose)
            for number,diagnostic in row.get('raw_contact',{}).items():
                reason=diagnostic.get('reason','MISSING'); contacts[reason]+=1
                if diagnostic.get('ready') is True and len(ready)<6:
                    ready.append(dict(time=row.get('time'),robot_id=number,
                                      diagnostic=diagnostic,raw_capture=row.get('raw_capture')))
    return dict(telemetry_name=path.name,telemetry_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                rows=rows,observation_reasons=dict(observations),raw_contact_reasons=dict(contacts),
                phases=dict(phases),displacement_from_first_mm={n:max(math.dist(ps[0],p) for p in ps)
                    for n,ps in poses.items()},ready_examples=ready,
                control_authority=False,limitation='Diagnostic readiness is not a measured redirect support profile.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('telemetry',type=Path,nargs='+')
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    result=[audit(p) for p in args.telemetry]
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:args.output.write_text(text,encoding='utf-8')
    else:print(text)


if __name__=='__main__':main()
