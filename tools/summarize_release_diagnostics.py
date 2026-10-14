"""Compact evidence index; never exports an accepted ball-travel model."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def summarize(folder):
    summary=folder/'summary.json'
    batch=json.loads(summary.read_text(encoding='utf8'))
    result=dict(directory=str(folder.resolve()),summary_sha256=digest(summary),
                stage=batch.get('stage'),complete=batch['complete'],attempts=[])
    for attempt in batch['attempts']:
        item=dict(robot=attempt['robot'],sdk_power=attempt['power'],exit_code=attempt['exit_code'],
                  timed_out=attempt.get('timed_out',False),benchmark_complete=attempt.get('benchmark_complete',False))
        path=folder/f"robot-{attempt['robot']}-power-{attempt['power']}-report.json"
        if path.is_file():
            report=json.loads(path.read_text(encoding='utf8'))
            manifest=report['runtime_manifest']
            item.update(report_sha256=digest(path),error=report.get('error'),
                        config_sha256=manifest['config_sha256'],source_sha256=report['source_sha256'],
                        runtime_sha256={k:v for k,v in manifest['runtime'].items()
                            if k in ('MedusaCore.dll','grSim.exe','ode_double.dll','zss_simulator.ini')},
                        pyd_sha256=manifest['sources'].get('ZBin/PythonScripts/CppPackage/CppPackage.pyd'),trials=[])
            for trial in report['trials']:
                t={k:trial.get(k) for k in ('trial','valid','goal_crossing','seconds','ball_start_m',
                    'physical_departures','kick_requests','last_ball_mm','max_ball_speed_sdk_mm_s',
                    'independent_raw_samples','raw_poll_failures')}
                t['sdk_speed_is_release_calibration']=False
                t['single_kick_raw_diagnostic']=bool(t['valid'] and t['physical_departures']==1
                    and (t['independent_raw_samples'] or 0)>=10 and t['raw_poll_failures']==0)
                if (t['physical_departures'] or 0)>1:
                    t['range_rejection_reason']='MULTIPLE_KICKS_IN_ONE_SCENE'
                if trial.get('raw_capture_log'):
                    raw=folder/Path(trial['raw_capture_log']).name
                    if raw.is_file():
                        t['raw_evidence']=dict(path=raw.name,sha256=digest(raw),bytes=raw.stat().st_size)
                if t['last_ball_mm'] and trial.get('verified_start'):
                    t['telemetry_end_displacement_mm']=math.dist(trial['verified_start']['ball'],t['last_ball_mm'])
                    t['end_displacement_is_certified_range']=False
                item['trials'].append(t)
        result['attempts'].append(item)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('directories',type=Path,nargs='+')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    result=dict(schema_version=1,status='DIAGNOSTIC_ONLY',physical_acceptance_passed=False,
                usable_for_planning=False,holdout_validated=False,strong_goalkeeper_tested=False,
                batches=[summarize(path) for path in args.directories])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(args.output.resolve())


if __name__=='__main__': main()
