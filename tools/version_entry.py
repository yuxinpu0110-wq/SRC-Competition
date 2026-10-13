"""真正绑定两份代码；旧版始终执行备份包中的脚本和配置。"""
import argparse
import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('version', choices=('v2', 'v2.1'))
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--cli', action='store_true')
    args = parser.parse_args()
    base = ROOT if args.version == 'v2.1' else ROOT/'.local-backups/v2/SRC-Competition-main'
    source_zbin = base if args.version == 'v2.1' and (base/'PythonScripts').is_dir() else base/'ZBin'
    scripts = source_zbin/'PythonScripts'
    config = ROOT/'configs/v2.1-foundation.json' if args.version == 'v2.1' else base/'match.json'
    if not scripts.is_dir() or not config.is_file():
        raise RuntimeError('Selected version source/config missing')
    runtime = ROOT if (ROOT/'PythonScripts').is_dir() else ROOT.parent
    os.environ['SRC_RUNTIME_ZBIN'] = str(runtime)
    handles = [os.add_dll_directory(str(runtime)), os.add_dll_directory(str(source_zbin))]
    sys.path.insert(0, str(scripts))
    # 旧版只能从这里直接启动策略；Client 使用原目录现有运行时。
    script = scripts/('run_match.py' if args.check or args.cli or args.version == 'v2' else 'match_launcher.py')
    sys.argv = [str(script), '--config', str(config)] + (['--check'] if args.check else [])
    print(f'[VERSION] {args.version} source={scripts} config={config}', flush=True)
    runpy.run_path(str(script), run_name='__main__')


if __name__ == '__main__':
    main()
