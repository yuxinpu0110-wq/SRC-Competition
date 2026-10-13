"""一次完成必要离线回归、原生导入与旧版代码完整性检查；不发运动命令。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str((ROOT/'PythonScripts' if (ROOT/'PythonScripts').is_dir() else ROOT/'ZBin/PythonScripts')))
from runtime_manifest import sha, runtime_files
from runtime_paths import RUNTIME_ZBIN
import hashlib


def run(command,cwd=ROOT):
    result=subprocess.run(command,cwd=cwd,capture_output=True,text=True,encoding='utf-8',errors='replace')
    return dict(command=command,exit_code=result.returncode,output=result.stdout+result.stderr)


def main():
    tmp=ROOT/'tmp'
    tmp.mkdir(exist_ok=True)
    os.environ['TMP']=os.environ['TEMP']=str(tmp)
    records=[]
    records.append(run([sys.executable,'-X','utf8','-m','unittest','test_match','test_v21','-q'],(ROOT/'PythonScripts' if (ROOT/'PythonScripts').is_dir() else ROOT/'ZBin/PythonScripts')))
    records.append(run([sys.executable,'-X','utf8','-m','unittest','discover','-s','tests','-q'],(ROOT/'PythonScripts' if (ROOT/'PythonScripts').is_dir() else ROOT/'ZBin/PythonScripts')))
    for version in ('v2.1','v2'):
        records.append(run([sys.executable,'-X','utf8',str(ROOT/'tools/version_entry.py'),version,'--check']))
    records.append(run(['git','-c','core.autocrlf=false','diff','--check']))
    baseline=ROOT/'.local-backups/v2/SRC-Competition-main'
    differences=[]
    with zipfile.ZipFile(ROOT/'.local-backups/v2-original.zip') as archive:
        for entry in archive.infolist():
            if entry.is_dir():
                continue
            rel=Path(entry.filename).relative_to('SRC-Competition-main')
            if (baseline/rel).read_bytes()!=archive.read(entry):
                differences.append(str(rel))
    old_manifest=json.loads((ROOT/'baseline-manifest.json').read_text(encoding='utf-8'))
    with zipfile.ZipFile(ROOT/'.local-backups/restored-source.zip') as restored:
        restored_differences=[name for name,expected in old_manifest['restored_sources'].items()
            if hashlib.sha256(restored.read(name.replace('\\','/'))).hexdigest()!=expected]
    runtime=runtime_files(RUNTIME_ZBIN)
    runtime += list((RUNTIME_ZBIN/'PythonScripts/CppPackage').glob('*.pyd'))
    result=dict(offline_passed=all(r['exit_code']==0 for r in records) and not differences and not restored_differences,
        checks=records,baseline_source_differences=differences,restored_source_differences=restored_differences,
        runtime_manifest_files=len(runtime),physical_acceptance_passed=False,
        live_test_status='Not part of this verifier; see separate live experiment reports')
    out=ROOT/'验收记录/v21-implementation-verification.json'
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(offline_passed=result['offline_passed'],runtime_files=len(runtime),report=str(out)),ensure_ascii=False))
    for r in records:
        if r['exit_code']:
            print(r['output'])
    return 0 if result['offline_passed'] else 1


if __name__=='__main__':
    sys.exit(main())
