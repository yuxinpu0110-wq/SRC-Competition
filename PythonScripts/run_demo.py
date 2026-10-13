"""独立仿真 demo 入口；不会修改 Config.py 或原有比赛策略。"""

import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description="蓝方 0、1 号机器人往返移动 demo")
    parser.add_argument("--play", choices=("demo", "tutorial"), default="demo",
                        help="demo 为双车往返；tutorial 为自己编写策略的练习入口")
    parser.add_argument("--check", action="store_true", help="只检查依赖和本地接口，不连接仿真")
    parser.add_argument("--frames", type=int, default=0, help="执行指定帧数后退出；0 为持续运行")
    args = parser.parse_args()
    if args.frames < 0:
        parser.error("--frames must be >= 0")

    # 本项目的 DLL、配置和数据都依赖 ZBin 工作目录。
    zbin = Path(__file__).resolve().parent.parent
    os.chdir(zbin)
    # 保持句柄存活，确保 Windows 可以一直找到本地 DLL。
    dll_directory = os.add_dll_directory(str(zbin)) if hasattr(os, "add_dll_directory") else None

    import debugpy
    import munkres
    import PySide6
    import pybind11
    import CppPackage as C
    if args.play == "tutorial":
        from Play.Test.TutorialPlay import TutorialPlay as SelectedPlay
    else:
        from Play.Test.DemoMove import DemoMove as SelectedPlay

    print(f"[OK] Python: {sys.version.split()[0]}", flush=True)
    print(f"[OK] Interpreter: {sys.executable}", flush=True)
    print(f"[OK] Working directory: {zbin}", flush=True)
    print(f"[OK] Dependencies and CppPackage loaded (PySide6 {PySide6.__version__})", flush=True)
    print(f"[OK] Selected strategy: {SelectedPlay.__name__}", flush=True)
    if args.check:
        return

    # 使用官方入口的配置方法：仿真、蓝队、左半场。
    from main import _update_config_file
    _update_config_file(argparse.Namespace(simulation=True, team="blue", positive_side="left"))
    runner = C.SystemRunner.Instance()
    print("[DEMO] Waiting for vision. Connect Simulation vision in Client and start Simulator.", flush=True)
    runner.init()

    import InitAllModules
    import Config
    import Global
    import SelectPlay

    # 仅在这个进程里替换常规策略；官方 HALT/STOP 仍然生效。
    Global.isTestMode = False
    Global.controlledRobotNumbers = (0, 1)
    Global.gameStrategies["NormalPlay"] = Config.GameStrategy(SelectedPlay())
    print("[DEMO] Ready. In Client Referee Box: Send, then FORCE START.", flush=True)
    print(f"[DEMO] Running {SelectedPlay.__name__} on blue #0/#1. HALT to stop. Ctrl+C to exit.", flush=True)
    frames = 0
    try:
        while not args.frames or frames < args.frames:
            runner.preDecision()
            SelectPlay.SelectPlay()
            runner.postDecision()
            frames += 1
    except KeyboardInterrupt:
        print("\n[DEMO] Stopped by Ctrl+C.", flush=True)
    finally:
        # 明确给可见的两辆车下发停止指令，再退出进程。
        from RoleMatch_LuaStyle.Skills import Stop
        from Vision import Player
        stop, _ = Stop()
        for number in (0, 1):
            if Player.isValid(number):
                C.setDribbleCommand(number, 0)
                stop(number)
        runner.postDecision()
        print(f"[DEMO] Finished: {frames} frames.", flush=True)


if __name__ == "__main__":
    main()
