"""Compact local experiment receipts, including native failures; no win-rate claim."""
import argparse
import hashlib
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('directories',type=Path,nargs='+')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    batches=[]
    for folder in a.directories:
        summary=json.loads((folder/'summary.json').read_text(encoding='utf8'))
        records=[]
        for attempt in summary['attempts']:
            path=folder/f"attempt-{attempt['number']:02}"/'report.json'
            item=dict(attempt)
            if path.is_file():
                r=json.loads(path.read_text(encoding='utf8'));m=r.get('runtime_manifest',{})
                item.update(report_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    config=r.get('config'),config_sha256=m.get('config_sha256'),
                    source_sha256={k:v for k,v in m.get('sources',{}).items() if k.endswith(('.py','.pyd'))
                        and any(s in k for s in ('reactive_policy','Normalplay','CompetitionSkills','match_tactics',
                            'run_match','ball_control','shot_accuracy','CppPackage','benchmark_match'))},
                    trials=[{k:t.get(k) for k in ('trial','valid','goal_crossing','seconds','ball_start_m',
                        'physical_departures','pass_attempts','pass_completed_proximity','pass_aborted',
                        'last_ball_mm','last_phase','moving_goalie_observed','goalie_max_displacement_mm')}
                        for t in r.get('trials',[])])
            records.append(item)
        batches.append(dict(directory=str(folder.resolve()),attempts=records))
    result=dict(status='REACTIVE_LOCAL_EXPERIMENT',competition_accepted=False,
        physical_rule_compliance_verified=False,high_win_rate_verified=False,
        proximity_receive_is_not_hardware_possession=True,batches=batches)
    a.output.parent.mkdir(parents=True,exist_ok=True)
    a.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(a.output.resolve())


if __name__=='__main__':main()
