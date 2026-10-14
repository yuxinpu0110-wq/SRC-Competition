"""每次实验记录全部源码、配置、SDK 与官方运行时哈希。"""
import hashlib
import platform
from pathlib import Path
from runtime_paths import ROOT, RUNTIME_ZBIN, SOURCE_ZBIN

RUNTIME_DIRS = ('bearer','data','defence_config','gamepads','generic','iconengines','imageformats',
    'networkinformation','platforms','qml','qmltooling','Qt','QtGamepad','QtGraphicalEffects',
    'QtQml','QtQuick','styles','tls','translations')


def runtime_files(root=RUNTIME_ZBIN):
    files=list(root.glob('*.dll'))+list(root.glob('*.exe'))+list(root.glob('*.ini'))
    for name in RUNTIME_DIRS:
        files += [p for p in (root/name).rglob('*') if p.is_file()]
    return files


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def manifest(config_path):
    scripts=SOURCE_ZBIN/'PythonScripts'
    files=[p for p in scripts.rglob('*') if p.is_file() and p.suffix in ('.py','.pyi','.pyd')
           and not any(part in ('.venv','__pycache__','.git') for part in p.relative_to(scripts).parts)]
    sources={p.relative_to(ROOT).as_posix():sha(p) for p in files}
    for directory in ('data','defence_config'):
        for p in (SOURCE_ZBIN/directory).rglob('*'):
            if p.is_file():
                sources[p.relative_to(ROOT).as_posix()]=sha(p)
    for p in SOURCE_ZBIN.glob('*.ini'):
        sources[p.relative_to(ROOT).as_posix()]=sha(p)
    runtime=runtime_files()
    return dict(python=platform.python_version(),source_root=str(ROOT),runtime_root=str(RUNTIME_ZBIN),
                config_path=str(Path(config_path).resolve()),config_sha256=sha(Path(config_path)),
                sources=sources,runtime={p.relative_to(RUNTIME_ZBIN).as_posix():sha(p) for p in runtime})
