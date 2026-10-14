"""Run offline regression suites; export a compact, explicitly non-physical report."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=ROOT/'ZBin/PythonScripts'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'tmp/v22-offline-report.json')
    args=parser.parse_args()
    temporary=ROOT/'tmp/v22-tests'
    temporary.mkdir(parents=True,exist_ok=True)
    environment=dict(os.environ,TEMP=str(temporary),TMP=str(temporary))
    suites=[]
    commands=[['-m','unittest','test_match','test_v21'],['-m','unittest','discover','-s','tests'],
              ['run_match.py','--config',str(ROOT/'configs/v2.2-development.json'),'--check-config']]
    for index,arguments in enumerate(commands):
        command=[sys.executable,'-X','utf8',*arguments]
        result=subprocess.run(command,cwd=SCRIPTS,env=environment,text=True,encoding='utf-8',
                              stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=60)
        (temporary/f'suite-{index}.txt').write_text(result.stdout,encoding='utf-8')
        match=re.search(r'Ran (\d+) tests',result.stdout)
        suites.append(dict(arguments=arguments,exit_code=result.returncode,
                           tests=int(match[1]) if match else None,console=f'tmp/v22-tests/suite-{index}.txt'))
        print(result.stdout.strip())
    files=[p for p in SCRIPTS.rglob('*.py') if not any(part in ('__pycache__','.venv') for part in p.parts)]
    files += list((ROOT/'configs').rglob('*.json'))+list((ROOT/'tools').glob('*.py'))
    git=subprocess.run(['git','rev-parse','HEAD'],cwd=ROOT,capture_output=True,text=True,check=True)
    report=dict(schema_version=1,scope='OFFLINE_ONLY',date_utc=datetime.now(timezone.utc).isoformat(),
                base_or_current_commit=git.stdout.strip(),python=sys.version, suites=suites,
                all_passed=all(s['exit_code']==0 for s in suites),
                total_tests=sum(s['tests'] or 0 for s in suites), physical_trials_run=0,
                physical_acceptance_passed=False,production_ready=False,
                synthetic_models_are_test_only=True,
                source_and_config_sha256={p.relative_to(ROOT).as_posix():sha(p) for p in sorted(files)})
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return 0 if report['all_passed'] else 1


if __name__=='__main__':
    sys.exit(main())
