"""把新版 Competition 完整映射到现有 ZBin；先备份、再导入基线、再覆盖改进。"""
import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

SOURCE=Path(__file__).resolve().parents[1]
TARGET=SOURCE.parent
ARCHIVE=Path('D:/下载/SRC-Competition-main.zip')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def git(*args):
    subprocess.run(['git','-c','core.autocrlf=false',*args],cwd=TARGET,check=True,
                   stdout=subprocess.PIPE,stderr=subprocess.PIPE)


def destination(relative):
    path=(TARGET/relative).resolve()
    if not path.is_relative_to(TARGET) or path==TARGET:
        raise ValueError(f'Unsafe deployment destination: {path}')
    return path


def main():
    if TARGET != Path('D:/桌面/ZBin') or not (TARGET/'Client.exe').is_file():
        raise RuntimeError('Unexpected runtime directory')
    if (TARGET/'deployment-manifest.json').exists():
        raise RuntimeError('Deployment already exists; refusing to overwrite its backup')
    stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
    backup=TARGET/'.local-backups'/f'pre-new-competition-{stamp}.zip'
    backup.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(ARCHIVE) as z:
        entries=[]
        for info in z.infolist():
            if info.is_dir():
                continue
            rel=Path(info.filename).relative_to('SRC-Competition-main')
            mapped=Path(*rel.parts[1:]) if rel.parts[0]=='ZBin' else rel
            destination(mapped)
            entries.append((info,mapped))
        previous={}
        candidates=[p for p in (TARGET/'PythonScripts').rglob('*') if p.is_file()
            and not any(part in ('.venv','__pycache__','.git') for part in p.relative_to(TARGET).parts)]
        candidates += [destination(rel) for _,rel in entries if destination(rel).is_file()]
        for name in ('data','defence_config','configs','tools'):
            candidates += [p for p in (TARGET/name).rglob('*') if p.is_file()]
        candidates=sorted(set(candidates))
        with zipfile.ZipFile(backup,'w',zipfile.ZIP_DEFLATED) as saved:
            for p in candidates:
                rel=p.relative_to(TARGET).as_posix()
                saved.write(p,rel)
                previous[rel]=digest(p)
        print(f'Pre-merge backup saved: {backup} ({len(candidates)} files)',flush=True)
        # 主目录是扁平布局，显式忽略第三方运行时及旧备份；不把 Qt/DLL/虚拟环境全入库。
        ignore='''/*
!/.gitignore
!/.gitattributes
!/PythonScripts/
!/configs/
!/data/
!/defence_config/
!/tools/
!/验收记录/
!/*.md
!/*.json
!/*.ini
!/*.cmd
!/*.pdf
!/*.code-workspace
**/__pycache__/
**/.venv/
**/*.dll
**/*.exe
**/*.pdb
**/*.pyd
!PythonScripts/CppPackage/CppPackage.pyd
runtime-*.ini
heartbeat-*.txt
benchmark-*.json
match-selected.json
opponent-selected.json
'''
        (TARGET/'.gitignore').write_text(ignore,encoding='utf-8')
        if not (TARGET/'.git').exists():
            git('init')
            git('add','.')
            git('-c','user.name=Codex','-c','user.email=codex@local.invalid','commit','--quiet','-m','保存 ZBin 主目录合入新版前的源码与配置')
            git('tag','zbin-before-new-competition-20261014')
            git('switch','-c','upgrade/competition-v2.1')
        # 每个 upstream 文件均有主目录映射。只对主目录 ignore 做必要的扁平布局适配。
        original=[]
        for info,rel in entries:
            path=destination(rel)
            original.append(dict(archive_path=info.filename,target=rel.as_posix(),
                upstream_sha256=hashlib.sha256(z.read(info)).hexdigest()))
            if rel==Path('.gitignore'):
                continue
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(z.read(info))
        git('add','.')
        git('-c','user.name=Codex','-c','user.email=codex@local.invalid','commit','--quiet','-m','完整合入新 Competition 压缩包源码配置资源和文档')
        git('tag','new-competition-input-20261014')
    # 覆盖经过离线验证的改进，不覆盖现有 .venv 或官方 Client/grSim/DLL。
    for directory,mapped in ((SOURCE/'ZBin',TARGET), (SOURCE/'tools',TARGET/'tools'),
            (SOURCE/'configs',TARGET/'configs'),(SOURCE/'验收记录',TARGET/'验收记录')):
        for p in directory.rglob('*'):
            if not p.is_file() or any(part in ('.venv','__pycache__','.git') for part in p.relative_to(directory).parts):
                continue
            dest=destination((mapped/p.relative_to(directory)).relative_to(TARGET))
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(p,dest)
    for name in ('baseline-manifest.json','V2.1升级参考.md','V2.1诊断工作日志与队友交接.md'):
        shutil.copy2(SOURCE/name,TARGET/name)
    for name in ('v2-original.zip','restored-source.zip'):
        shutil.copy2(SOURCE/'.local-backups'/name,TARGET/'.local-backups'/name)
    old_source=TARGET/'.local-backups/v2'
    if old_source.exists():
        raise RuntimeError('Old-version baseline destination already exists')
    shutil.copytree(SOURCE/'.local-backups/v2',old_source)
    (TARGET/'competition-layout.json').write_text('{"layout":"flat","version":"2.1-foundation"}\n',encoding='utf-8')
    for name,body in {
        'start-v2.1.cmd':'"PythonScripts\\.venv\\Scripts\\python.exe" -X utf8 tools\\version_entry.py v2.1',
        'start-v2-baseline.cmd':'"PythonScripts\\.venv\\Scripts\\python.exe" -X utf8 tools\\version_entry.py v2 --cli',
        'start-match.cmd':'call start-v2.1.cmd',
        'start-baseline.cmd':'call start-v2-baseline.cmd',
        'start-accuracy-v2.cmd':'call start-v2-baseline.cmd',
        'run-opponent.cmd':'"PythonScripts\\.venv\\Scripts\\python.exe" -X utf8 PythonScripts\\run_match.py --config opponent.json',
        'check.cmd':'"PythonScripts\\.venv\\Scripts\\python.exe" -X utf8 tools\\verify_v21.py'
    }.items():
        (TARGET/name).write_text('@echo off\ncd /d "%~dp0"\n'+body+'\nif errorlevel 1 pause\n',encoding='ascii')
    for name in ('run-demo.cmd','run-tutorial.cmd'):
        path=TARGET/name
        if path.is_file():
            path.write_text(path.read_text(encoding='utf-8').replace('ZBin\\PythonScripts','PythonScripts'),encoding='utf-8')
    workspace=TARGET/'Competition.code-workspace'
    workspace.write_text(workspace.read_text(encoding='utf-8').replace('/ZBin/PythonScripts','/PythonScripts').replace('/ZBin"','"'),encoding='utf-8')
    for item in original:
        p=destination(item['target'])
        item.update(present=p.is_file(),deployed_sha256=digest(p),
                    changed_by_upgrade=digest(p)!=item['upstream_sha256'])
    result=dict(layout='flat',source_archive=str(ARCHIVE),archive_sha256=digest(ARCHIVE),
        runtime_root=str(TARGET),source_root=str(TARGET/'PythonScripts'),premerge_backup=str(backup),
        premerge_backup_sha256=digest(backup),previous_files=previous,upstream_files=original,
        upstream_file_count=len(original),all_upstream_files_present=all(i['present'] for i in original),
        primary_entry='start-match.cmd -> start-v2.1.cmd -> tools/version_entry.py -> PythonScripts/match_launcher.py',
        physical_acceptance_passed=False)
    (TARGET/'deployment-manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(dict(all_upstream_files_present=result['all_upstream_files_present'],
                         upstream_files=len(original),source_root=result['source_root']),ensure_ascii=False),flush=True)


if __name__=='__main__':
    main()
