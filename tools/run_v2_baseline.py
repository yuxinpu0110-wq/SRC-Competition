"""使用独立 Git 工作树旧源码和当前官方 DLL，不与新版本混用源码。"""
import os
from pathlib import Path
import runpy
import sys

root=Path(__file__).resolve().parents[1]
old=root.parent/'Competition-before-hzk-v3-20261014'
scripts=old/'ZBin/PythonScripts'
if not scripts.is_dir():
    raise RuntimeError('Restore baseline with: git worktree add --detach ../Competition-before-hzk-v3-20261014 before-hzk-v3-integration-20261014')
handles=[os.add_dll_directory(str(root/'ZBin')),os.add_dll_directory(str(old/'ZBin'))]
sys.path.insert(0,str(scripts))
sys.argv=[str(scripts/'run_match.py'),'--config',str(old/'configs/v2-foundation.json')]+sys.argv[1:]
print('[BASELINE] source='+str(scripts),flush=True)
runpy.run_path(str(scripts/'run_match.py'),run_name='__main__')
