"""策略与官方运行时分开存放；不复制/改写用户的 DLL。"""
import os
import json
from pathlib import Path

SOURCE_ZBIN = Path(__file__).resolve().parents[1]
_marker = SOURCE_ZBIN.parent/'competition-layout.json'
_flat_marker = SOURCE_ZBIN/'competition-layout.json'
_flat = _flat_marker.is_file() and json.loads(_flat_marker.read_text(encoding='utf-8')).get('layout') == 'flat'
_nested = not _flat and _marker.is_file() and json.loads(_marker.read_text(encoding='utf-8')).get('layout') == 'nested'
ROOT = SOURCE_ZBIN.parent if _nested else SOURCE_ZBIN
RUNTIME_ZBIN = Path(os.environ.get('SRC_RUNTIME_ZBIN', str(ROOT.parent if _nested else SOURCE_ZBIN))).resolve()


def dll_directories():
    if not hasattr(os, 'add_dll_directory'):
        return []
    # 句柄必须保持到进程退出；旧 CppPackage __init__ 丢弃了自己的句柄。
    return [os.add_dll_directory(str(p)) for p in dict.fromkeys((RUNTIME_ZBIN, SOURCE_ZBIN))]
