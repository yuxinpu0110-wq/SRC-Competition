"""在已启动的 grSim + Client 中运行有限时长的真实 Medusa 仿真。

从 ZBin 根目录运行。摆球、裁判指令和黄色陪练门将仅发送到本机 UDP，蓝队
动作完整经过 SelectPlay -> Task -> CppPackage -> C++ 规划 -> Client -> grSim。
结束时发送 HALT 并给两辆蓝车、陪练门将下停止命令。
"""

import argparse
import json
import math
import os
from pathlib import Path
import sys
import socket
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tactic", choices=("automatic", "direct", "wing", "wall_pass"), default="automatic")
    parser.add_argument("--scenario", choices=("open", "wing", "wall", "kickoff", "indirect"), default="open")
    parser.add_argument("--seconds", type=float, default=35)
    parser.add_argument("--goalie-protocol", choices=("classic", "new"), default="classic")
    args = parser.parse_args()
    zbin = Path(__file__).resolve().parents[2]
    if Path.cwd().resolve() != zbin or not (zbin / "grSim.exe").exists():
        raise RuntimeError("请在 ZBin 根目录运行")
    sys.path.insert(0, str(zbin / "PythonScripts"))
    os.environ["BLUE_ATTACK_TACTIC"] = args.tactic
    output = zbin / "PythonScripts" / "BlueAttack2026" / "simulation_results"
    output.mkdir(exist_ok=True)
    log_path = output / f"{args.scenario}_{args.tactic}.jsonl"
    # 每个仿真案例使用独立新日志，避免上次摆球的滤波瞬态混入本次统计。
    log_path.write_text("", encoding="utf-8")
    os.environ["BLUE_ATTACK_LOG"] = str(log_path)

    from BlueAttack2026.sim_protocol import (
        referee_packet, replacement_packet, robot_command_packet, send_local_packet,
        decode_status_packet, status_forward_packets,
    )
    import CppPackage as C
    import Global
    Global.read_config_file("zss.ini")
    if not Global.isSimulation or Global.isYellow or Global.isRight:
        raise RuntimeError("此仿真入口要求 IsSimulation=true，IsYellow=false，IsRight=false")

    poses = {
        "open": ((1200, 0, 0, 0), {5: (930, 0, 0), 6: (1450, -1150, 0)}),
        "wing": ((1800, 850, 0, 0), {5: (1550, 850, 0), 6: (2100, -950, 0)}),
        "wall": ((1000, 100, 0, 0), {5: (730, 100, 0), 6: (1400, -950, 0)}),
        "kickoff": ((0, 0, 0, 0), {5: (-300, 0, 0), 6: (-700, -1100, 0)}),
        "indirect": ((1700, 300, 0, 0), {5: (1400, 300, 0), 6: (1950, -900, 0)}),
    }
    ball, blue = poses[args.scenario]
    counter = int(time.time()) % 1000000
    current_ref = ["HALT", counter]
    command_lock = threading.Lock()
    done = threading.Event()
    feedback = {"packets": 0, "contacts": {5: 0, 6: 0}, "error": None}

    def legacy_feedback_bridge():
        # 随程序一起退出的本地兼容桥：旧 grSim 给 bool，Client 本版本期待
        # float，直接将真实状态转交给 Medusa。只转换真实反馈，不伪造触球。
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            receiver.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            receiver.bind(("0.0.0.0", 30011))
            receiver.settimeout(0.2)
            while not done.is_set():
                try:
                    payload, _ = receiver.recvfrom(65535)
                except socket.timeout:
                    continue
                statuses = decode_status_packet(payload)
                feedback["packets"] += 1
                for n in (5, 6):
                    feedback["contacts"][n] += int(statuses.get(n, {}).get("infrared", False))
                for packet in status_forward_packets(payload).values():
                    sender.sendto(packet, ("127.0.0.1", 60101))
        except Exception as error:
            feedback["error"] = f"{type(error).__name__}: {error}"
        finally:
            receiver.close()
            sender.close()

    def ref_command(command):
        with command_lock:
            current_ref[0], current_ref[1] = command, current_ref[1] + 1

    def repeat_referee():
        while not done.is_set():
            with command_lock:
                payload = referee_packet(current_ref[0], counter=current_ref[1])
            send_local_packet(payload, 39991)
            done.wait(0.08)

    ref_thread = threading.Thread(target=repeat_referee, daemon=True)
    feedback_thread = threading.Thread(target=legacy_feedback_bridge, daemon=True)
    ref_thread.start()
    feedback_thread.start()
    send_local_packet(replacement_packet(ball=ball, blue=blue, yellow={0: (4280, 0, math.pi)}))
    system = C.SystemRunner.Instance()
    print("开始真实 grSim 仿真，初始化 Medusa，等待 Client 视觉...", flush=True)
    system.init()
    import InitAllModules
    import SelectPlay
    from BlueAttack2026 import medusa
    from Vision import Ball, Player

    started = time.monotonic()
    switched_prepare = switched_start = False
    seeded_again = False
    stats = {"scenario": args.scenario, "tactic": args.tactic,
             "seconds": args.seconds, "frames": 0, "kick_requests": [],
             "max_ball_speed_mm_s": 0, "max_control_path_mm": {5: 0, 6: 0},
             "max_ball_control_path_mm": {5: 0, 6: 0}, "dribble_frames": 0,
             "second_touch_unlocked": False, "goal_line_crossings": 0,
             "ref_messages": [], "errors": []}
    last_message = None
    last_stats_cycle = -1
    goal_seen = False
    try:
        while time.monotonic() - started < args.seconds:
            elapsed = time.monotonic() - started
            if args.scenario in ("kickoff", "indirect"):
                if elapsed >= 2 and not switched_prepare:
                    ref_command("PREPARE_KICKOFF_BLUE" if args.scenario == "kickoff" else "INDIRECT_FREE_BLUE")
                    switched_prepare = True
                if elapsed >= 4 and not switched_start:
                    ref_command("NORMAL_START")
                    switched_start = True
            elif elapsed >= 2 and not switched_start:
                ref_command("FORCE_START")
                switched_start = True
            system.preDecision()
            if not switched_prepare and not switched_start:
                medusa.disable_ball_actions()
                for n in (5, 6):
                    C.makeItStop(n)
            else:
                SelectPlay.SelectPlay()
            system.postDecision()
            # 先收到 HALT 并停下旧命令，再摆球；避免初始化期间排队的旧视觉或
            # 上一个案例的末速度污染新场景。重复少量设置包并给滤波器留出时间。
            if 0.35 <= elapsed <= 0.85:
                for n in (5, 6):
                    send_local_packet(robot_command_packet(n, yellow=False, protocol="classic"))
                send_local_packet(replacement_packet(ball=ball, blue=blue, yellow={0: (4280, 0, math.pi)}))
                seeded_again = True
            stats["frames"] += 1
            f, actions = medusa.LAST_FRAME, medusa.LAST_ACTIONS
            if f is not None and f.cycle != last_stats_cycle:
                last_stats_cycle = f.cycle
                if math.isfinite(f.ball_speed):
                    stats["max_ball_speed_mm_s"] = max(stats["max_ball_speed_mm_s"], f.ball_speed)
                for n, distance in medusa.PLANNER.dribble_distance.items():
                    stats["max_control_path_mm"][n] = max(stats["max_control_path_mm"][n], distance)
                    ball_path = medusa.PLANNER.metrics["ball_path_mm"][n]
                    stats["max_ball_control_path_mm"][n] = max(stats["max_ball_control_path_mm"][n], ball_path)
                stats["second_touch_unlocked"] |= medusa.PLANNER.restart_done
                stats["dribble_frames"] += any(a.dribble for a in actions.values())
                for n, a in actions.items():
                    if a.kind == "kick":
                        stats["kick_requests"].append({"time": round(elapsed, 3), "number": n,
                            "pass": a.pass_ball, "reason": a.reason,
                            "ball": [f.ball.x, f.ball.y], "lock": medusa.PLANNER.locked_kicker,
                            "power": medusa.LAST_POWER.get(n)})
                crossing = f.ball.x > medusa.PLANNER.field.length / 2 + 21 and abs(f.ball.y) < medusa.PLANNER.field.goal_width / 2 - 21
                if crossing and not goal_seen:
                    stats["goal_line_crossings"] += 1
                    goal_seen = True
                    ref_command("HALT")
                # 黄色陪练门将只在门前横向跟随，物理运动仍由 grSim 执行。
                if not goal_seen and elapsed > 1 and C.VisionModule.Instance().theirPlayer(0).Valid():
                    goalie = C.VisionModule.Instance().theirPlayer(0)
                    target_y = max(-320, min(320, f.ball.y * 0.12))
                    world_vy = max(-0.45, min(0.45, (target_y - goalie.Y()) * 0.003))
                    send_local_packet(robot_command_packet(0, yellow=True, vn=-world_vy,
                                      protocol=args.goalie_protocol))
            message = C.VisionModule.Instance().getCurrentRefereeMsg()
            if message != last_message:
                stats["ref_messages"].append({"time": round(elapsed, 3), "message": message,
                    "game_on": C.VisionModule.Instance().isGameOn(),
                    "can_kick": C.VisionModule.Instance().canEitherKickBall()})
                print("裁判:", stats["ref_messages"][-1], flush=True)
                last_message = message
    except Exception as error:
        stats["errors"].append(f"{type(error).__name__}: {error}")
        raise
    finally:
        ref_command("HALT")
        for _ in range(5):
            send_local_packet(referee_packet("HALT", counter=current_ref[1]), 39991)
            for n in (5, 6):
                send_local_packet(robot_command_packet(n, yellow=False, protocol=args.goalie_protocol))
            send_local_packet(robot_command_packet(0, yellow=True, protocol=args.goalie_protocol))
        medusa.disable_ball_actions()
        done.set()
        ref_thread.join(timeout=1)
        feedback_thread.join(timeout=1)
        stats["infrared_feedback"] = feedback
        stats["elapsed_seconds"] = round(time.monotonic() - started, 3)
        stats["log"] = str(log_path)
        (output / f"{args.scenario}_{args.tactic}_summary.json").write_text(
            json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(stats, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
