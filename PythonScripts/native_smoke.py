"""LOCAL PRACTICE ONLY: minimal native RushTo reproduction, no competition Planner.

Moves blue #0 only 200mm from the reset position. Requires connected Client + grSim.
"""
import argparse
import os
from pathlib import Path
import socket
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-practice', action='store_true', required=True)
    parser.add_argument('--frames', type=int, default=180)
    parser.add_argument('--qt-app', action='store_true', help='Diagnostic explicit Qt application initialization')
    parser.add_argument('--skill', choices=('rush', 'simple', 'speed'), default='rush')
    args = parser.parse_args()
    if not 1 <= args.frames <= 1200:
        parser.error('frames must be 1..1200')
    root = Path(__file__).resolve().parents[2]
    zbin = root/'ZBin'
    from match_processes import check_duplicates, process_count
    check_duplicates()
    if process_count('Client.exe') != 1 or process_count('grSim.exe') != 1:
        raise RuntimeError('Need exactly one connected Client and grSim')
    lock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    lock.bind(('127.0.0.1', 45990))
    os.chdir(zbin)
    dll = os.add_dll_directory(str(zbin))
    import CppPackage as C
    qt_app = None
    if args.qt_app:
        from PySide6.QtCore import QCoreApplication, qVersion
        qt_app = QCoreApplication.instance() or QCoreApplication(sys.argv)
        print('[NATIVE_SMOKE] Qt application:', qVersion(), flush=True)
    runner = C.SystemRunner.Instance()
    runner.init()
    import InitAllModules
    import Global
    if Global.isYellow or Global.isRight or not Global.isSimulation:
        raise RuntimeError('Minimal test requires native zss.ini blue / left / simulation')
    from RoleMatch_LuaStyle.Skills import RushTo, SimpleGoTo
    from Vision import Player
    from WorldModel import kickStatus
    from sim_tools import referee, reset_scene, stop_robots
    referee(0)
    reset_scene()
    target = C.CGeoPoint(-1200, -700)
    skill = (RushTo(target, angle=0, maxAcc=1800, flag=0) if args.skill == 'rush'
             else SimpleGoTo(target, angle=0, flag=0) if args.skill == 'simple'
             else (lambda n: C.makeItCalibrationSpeed(n, 0, 0, 0), None))
    try:
        for frame in range(args.frames):
            runner.preDecision()
            if qt_app is not None:
                qt_app.processEvents()
            kickStatus.clearAll()
            C.setDribbleCommand(0, 0)
            if Player.isValid(0):
                skill[0](0)
                runner.postDecision()
            if frame % 30 == 0:
                pos = Player.Pos(0)
                print(f'[NATIVE_SMOKE] frame={frame} valid={Player.isValid(0)} '
                      f'position_mm=({pos.x():.1f},{pos.y():.1f}) heading={Player.direction(0):.4f}', flush=True)
    finally:
        referee(0)
        for _ in range(3):
            stop_robots('blue', [0, 1])
        lock.close()
    print('[NATIVE_SMOKE] Finished all frames', flush=True)


if __name__ == '__main__':
    code = 0
    try:
        main()
    except Exception:
        import traceback
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
