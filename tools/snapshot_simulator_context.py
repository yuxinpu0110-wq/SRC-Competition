"""Read saved grSim physical settings for the upcoming R2 calibration.

This is not physical acceptance and does not modify the simulator or its XML.
"""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'ZBin/PythonScripts'))
from simulator_context import capture_context,settings_path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=ROOT/'configs/v2.2-development.json')
    parser.add_argument('--output',type=Path,default=ROOT/'tmp/v22-simulator-context.json')
    args=parser.parse_args()
    config=json.loads(args.config.read_text(encoding='utf-8-sig'))
    result=capture_context(settings_path(config),ROOT/'ZBin')
    result.update(physical_acceptance_passed=False,usable_for_planning=False)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('Saved settings snapshot: '+str(args.output.resolve()))
    print('Physical fingerprint: '+result['simulator_context']['physical_settings_sha256'])
    print('LIVE SETTINGS NOT VERIFIED; no model has been calibrated or enabled.')
    if result['missing_profile_files']:
        print('Missing profile files (verify actual selection/defaults before calibration): '+', '.join(result['missing_profile_files']))


if __name__=='__main__':main()
