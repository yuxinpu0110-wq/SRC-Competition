"""固定嵌套工作区；运行时可由 SRC_RUNTIME_ZBIN 显式指定。"""
import os
from pathlib import Path
SOURCE_ZBIN=Path(__file__).resolve().parents[1]
ROOT=SOURCE_ZBIN.parent
RUNTIME_ZBIN=Path(os.environ.get("SRC_RUNTIME_ZBIN",str(SOURCE_ZBIN))).resolve()
def dll_directories():
    if not hasattr(os,"add_dll_directory"): return []
    return [os.add_dll_directory(str(p)) for p in dict.fromkeys((RUNTIME_ZBIN,SOURCE_ZBIN))]
