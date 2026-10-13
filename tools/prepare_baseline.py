"""只新增备份，不改写恢复的代码/运行时。路径清单及 SHA256 随备份保存。"""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
import sys

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT if (ROOT/'PythonScripts').is_dir() else ROOT.parent
DEST = ROOT / '.local-backups'
sys.path.insert(0,str((ROOT/'PythonScripts' if (ROOT/'PythonScripts').is_dir() else ROOT/'ZBin/PythonScripts')))


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    DEST.mkdir(exist_ok=True)
    archive = Path('D:/下载/SRC-Competition-main.zip')
    target = DEST / 'v2-original.zip'
    if target.exists():
        raise RuntimeError('Baseline already exists; refusing to overwrite')
    shutil.copy2(archive, target)
    with zipfile.ZipFile(target) as z:
        z.extractall(DEST / 'v2')  # 原包已在导入时校验所有相对路径。
    restored = DEST / 'restored-source.zip'
    files = []
    for source in (RUNTIME / 'PythonScripts', RUNTIME / 'CompetitionCoerce2026'):
        for path in source.rglob('*'):
            rel = path.relative_to(source)
            if not path.is_file() or any(p in ('.venv', '__pycache__', '.git', 'logs') for p in rel.parts):
                continue
            if source.name == 'CompetitionCoerce2026' and not (
                    'PythonScripts' in rel.parts or path.suffix in ('.json', '.md', '.ini', '.cmd')):
                continue
            files.append(path)
    with zipfile.ZipFile(restored, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in files:
            z.write(path, path.relative_to(RUNTIME).as_posix())
    from runtime_manifest import runtime_files as enumerate_runtime
    runtime_files = enumerate_runtime(RUNTIME)
    runtime_files += list((RUNTIME / 'PythonScripts/CppPackage').glob('*.pyd'))
    manifest = {
        'baseline_archive_sha256': digest(target),
        'restored_archive_sha256': digest(restored),
        'restored_sources': {str(p.relative_to(RUNTIME)): digest(p) for p in files},
        'runtime_root': str(RUNTIME),
        'runtime_files': {str(p.relative_to(RUNTIME)): digest(p) for p in runtime_files},
        'runtime_restore': 'Official runtime remains in original ZBin; retain that folder. Python venv is excluded.'
    }
    (ROOT / 'baseline-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Baseline saved; restored sources={len(files)}, runtime resources={len(runtime_files)}')


if __name__ == '__main__':
    main()
