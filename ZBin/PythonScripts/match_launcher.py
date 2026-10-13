"""Small local operator panel. No global process killing, no auto-start on load."""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import tkinter as tk
from tkinter import messagebox
from run_match import load_config, ROOT, ZBIN
from sim_tools import stop_robots, reset_scene, referee, PracticeReferee


def opponent_config(main, template):
    """练习门将必须在对面；颜色/方向/车号跟随当前练习场景，不硬编码黄队。"""
    return dict(template, profile='goalie', team='yellow' if main['team'] == 'blue' else 'blue',
                side='right' if main['side'] == 'left' else 'left', goalie=main['goalie'],
                attackers=list(main['attackers']))


def latest_complete_row(path):
    """只读文件末尾，避开正在写的一半 JSON 行；不靠进程存在来证明车在运动。"""
    with path.open('rb') as stream:
        stream.seek(0,2)
        offset = max(0,stream.tell()-16384)
        stream.seek(offset)
        lines = stream.read().splitlines(keepends=True)
    if offset and lines: lines = lines[1:]
    for line in reversed(lines):
        if line.endswith(b'\n'):
            return json.loads(line)
    return None


class App:
    def __init__(self, window, config_path=None):
        self.window = window
        self.config_path = Path(config_path) if config_path is not None else ROOT/'match.json'
        self.process = None
        self.output = None
        self.c = None
        self.started_at = 0
        self.saw_heartbeat = False
        self.gui = {}
        self.practice_sender = None
        self.opponent_process = self.opponent_output = self.opponent_c = None
        self.opponent_started = 0
        self.opponent_saw_heartbeat = False
        self.opponent_origin = None
        self.opponent_motion_max = 0.
        self.opponent_telemetry = None
        window.title('SRC 比赛策略 · 本机仿真')
        window.geometry('720x830')
        tk.Label(window, text='比赛策略控制台（独立 Competition 环境）', font=('Microsoft YaHei', 16)).pack(pady=12)
        tk.Label(window, text='默认：初赛 2 攻，蓝队 0 / 1 号；原练习目录不受影响。\n先关闭旧 Client / grSim / demo / Medusa，避免端口和控制权冲突。', justify='left').pack()
        self.pass_enabled = tk.BooleanVar(value=load_config(self.config_path).get('enable_pass', False))
        tk.Checkbutton(window, text='实验传球（重新启动策略后生效，尚未完成移动门将验收）',
                       variable=self.pass_enabled).pack(pady=4)
        self.feint_enabled = tk.BooleanVar(value=load_config(self.config_path).get('enable_feint', False))
        tk.Checkbutton(window, text='佯射后传球（需同时开启传球；候选功能，重启生效）',
                       variable=self.feint_enabled).pack(pady=4)
        self.shoot_control = tk.StringVar(value=load_config(self.config_path).get('shoot_control', 'legacy'))
        row = tk.Frame(window)
        row.pack()
        tk.Label(row, text='出球控制（重启策略生效）：').pack(side='left')
        tk.Radiobutton(row, text='旧出球逻辑 legacy', variable=self.shoot_control, value='legacy').pack(side='left')
        tk.Radiobutton(row, text='新版 precision（待验收）', variable=self.shoot_control, value='precision').pack(side='left')
        for text, callback in [
            ('1. 打开 Client（在其中启动 Simulator）', self.open_gui),
            ('2. 启动策略（不会自动 Force Start）', self.start),
            ('本机练习：重置为 2 攻 + 1 门将（需另启动门将）', self.reset),
            ('本机练习：启动对面官方移动门将', self.start_opponent),
            ('本机练习：停止对面门将', self.stop_opponent),
            ('本机练习：STOP GAME', lambda: self.practice_ref(1)),
            ('本机练习：FORCE START', lambda: self.practice_ref(3)),
            ('停止本面板进攻和门将，并发送零速度', self.stop),
            ('打开策略参数', lambda: os.startfile(self.config_path)),
            ('打开使用说明', lambda: os.startfile(ROOT / '使用指南.md')),
            ('打开日志目录', self.logs),
        ]:
            tk.Button(window, text=text, command=callback, height=1, width=55).pack(pady=4)
        self.status = tk.StringVar(value='未启动。Client：Simulation，前4个摄像头，Vision 插头；ZSimulator 选 Show 后播放。')
        tk.Label(window, textvariable=self.status, wraplength=610, fg='#155599').pack(pady=10)
        self.opponent_status = tk.StringVar(value='门将：未启动。重置只摆车，不会自动守门。')
        tk.Label(window, textvariable=self.opponent_status, wraplength=680, fg='#7b4215').pack()
        tk.Label(window, text='正式比赛：裁判由官方控制，不要使用这里的练习裁判/重置按钮。\n本版本仅启用仿真；没有经过实车或完整 3v3 规则验收。', fg='#9b4215').pack()
        window.protocol('WM_DELETE_WINDOW', self.close)
        window.after(500, self.poll)

    def open_gui(self):
        # Windows GUI tools are intentionally visible for the operator.
        for name in ('Client.exe',):
            # Do not spawn duplicates from this panel. An existing external instance is operator-owned.
            running = subprocess.run(['tasklist', '/FI', f'IMAGENAME eq {name}', '/FO', 'CSV', '/NH'],
                                     capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if name.lower().encode() not in running.stdout.lower():
                self.gui[name] = subprocess.Popen([str(ZBIN / name)], cwd=ZBIN)
        self.status.set('Client 中 ZSimulator 选 Show 后播放；连接 Simulation 视觉，勾前4个摄像头。只启动一份 grSim，不点 Medusa。')

    def start(self):
        if self.process and self.process.poll() is None:
            messagebox.showinfo('已启动', '已有本面板启动的策略，请先停止。')
            return
        try:
            self.c = load_config(self.config_path)
            self.c['enable_pass'] = self.pass_enabled.get()
            self.c['enable_feint'] = self.feint_enabled.get()
            self.c['shoot_control'] = self.shoot_control.get()
            if self.opponent_process and self.opponent_process.poll() is None:
                expected = opponent_config(self.c, self.opponent_c)
                if any(expected[k] != self.opponent_c[k] for k in ('team', 'side', 'goalie', 'attackers')):
                    raise ValueError('场景配置已改变，请先停止并重新启动门将')
            selected = ROOT/'match-selected.json'
            selected.write_text(json.dumps(self.c, indent=2), encoding='utf-8')
            (ROOT / 'logs').mkdir(exist_ok=True)
            self.logfile = ROOT / 'logs' / (time.strftime('%Y%m%d-%H%M%S')+'-console.txt')
            self.output = self.logfile.open('w', encoding='utf-8')
            self.started_at = time.time()
            self.saw_heartbeat = False
            self.process = subprocess.Popen([sys.executable, '-X', 'utf8', '-X', 'faulthandler', '-u', str(ZBIN/'PythonScripts'/'run_match.py'), '--config', str(selected)],
                cwd=ZBIN, stdout=self.output, stderr=subprocess.STDOUT)
            self.status.set('策略启动中；如果一直 Waiting for vision，请在 Client 连接视觉。日志：'+self.logfile.name)
        except Exception as e:
            messagebox.showerror('启动失败', str(e))

    def practice_ref(self, cmd):
        if not messagebox.askyesno('仅限本机练习', '确认这是自己的本机仿真，且 Client 的裁判 Send 已关闭？\n不要与正式裁判或其他裁判发送器同时使用。'):
            return
        try:
            if self.practice_sender is None:
                self.practice_sender = PracticeReferee()
            self.practice_sender.set(cmd)
        except RuntimeError as exc:
            messagebox.showerror('练习裁判未启动',str(exc))
            return

    def start_opponent(self):
        """仅启动一份本面板拥有的官方门将，不自动开赛、不改变裁判。"""
        if self.opponent_process and self.opponent_process.poll() is None:
            self.opponent_status.set('门将进程已启动；要确认实际运动还需看仿真/遥测。')
            return
        try:
            self.stop_opponent()
            main = self.c or load_config(self.config_path)
            if main['profile'] != 'qualifier':
                raise ValueError('练习对面门将按钮只用于 qualifier 场景')
            self.opponent_c = opponent_config(main, load_config(ROOT/'opponent.json'))
            path = ROOT/'opponent-selected.json'
            path.write_text(json.dumps(self.opponent_c, indent=2), encoding='utf-8')
            (ROOT/'logs').mkdir(exist_ok=True)
            self.opponent_logfile = ROOT/'logs'/(time.strftime('%Y%m%d-%H%M%S')+'-goalie-console.txt')
            self.opponent_output = self.opponent_logfile.open('w', encoding='utf-8')
            self.opponent_started = time.time()
            self.opponent_saw_heartbeat = False
            self.opponent_origin = self.opponent_telemetry = None
            self.opponent_motion_max = 0.
            self.opponent_process = subprocess.Popen([sys.executable, '-X', 'utf8', '-X', 'faulthandler', '-u',
                str(ZBIN/'PythonScripts'/'run_match.py'), '--config', str(path)], cwd=ZBIN,
                stdout=self.opponent_output, stderr=subprocess.STDOUT)
            self.opponent_status.set('官方门将启动中，等待视觉和开赛；启动进程不等于已验证运动。')
        except Exception as exc:
            self.stop_opponent()
            self.opponent_status.set('门将启动失败：'+str(exc))

    def stop_opponent(self):
        if self.opponent_process and self.opponent_process.poll() is None:
            subprocess.run(['taskkill', '/PID', str(self.opponent_process.pid), '/T', '/F'],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            self.opponent_process.wait(timeout=5)
        if self.opponent_c:
            for _ in range(3):
                stop_robots(self.opponent_c['team'], [self.opponent_c['goalie']])
        if self.opponent_output:
            self.opponent_output.close()
        self.opponent_process = self.opponent_output = self.opponent_c = None
        self.opponent_status.set('门将：已停止。')

    def poll_opponent(self):
        if self.opponent_process is None:
            return
        code = self.opponent_process.poll()
        if code is not None:
            self.stop_opponent()
            self.opponent_status.set(f'门将进程退出，返回码 {code}；查看 logs/*-goalie-console.txt。')
            return
        heartbeat = ROOT/f'heartbeat-{self.opponent_c["team"]}.txt'
        stamp = heartbeat.stat().st_mtime if heartbeat.exists() else 0
        self.opponent_saw_heartbeat |= stamp > self.opponent_started
        timeout = ((self.opponent_saw_heartbeat and time.time()-stamp > 2.5) or
                   (not self.opponent_saw_heartbeat and time.time()-self.opponent_started > 30))
        if timeout:
            self.stop_opponent()
            self.opponent_status.set('门将心跳停止，已结束本面板门将并发零速度；检查原生报错/视觉。')
        elif self.opponent_saw_heartbeat:
            if self.opponent_telemetry is None:
                paths = [p for p in (ROOT/'logs').glob('*-'+self.opponent_c['team']+'.jsonl')
                         if p.stat().st_mtime > self.opponent_started]
                if paths: self.opponent_telemetry = max(paths,key=lambda p:p.stat().st_mtime)
            row = latest_complete_row(self.opponent_telemetry) if self.opponent_telemetry else None
            if row and row.get('time',0) > self.opponent_started and row.get('mode') == 'PLAY':
                pose = row.get('robots',{}).get(str(self.opponent_c['goalie']))
                if pose and all(math.isfinite(v) for v in pose):
                    if self.opponent_origin is None: self.opponent_origin = pose
                    self.opponent_motion_max = max(self.opponent_motion_max,math.dist(self.opponent_origin,pose))
            self.opponent_status.set(f'门将心跳正常，开赛后已观测最大位移 {self.opponent_motion_max:.1f}mm；'
                                     '位移小不等于已完成移动门将验收。')

    def reset(self):
        if not messagebox.askyesno('重置练习场景', '这会停止本面板策略、重摆球和车辆、移除其他仿真车辆。仅限自己的本机练习，继续？'):
            return
        self.stop()
        c = load_config(ROOT/'match.json')
        referee(0)
        reset_scene(attackers=c['attackers'], goalie=c['goalie'], team=c['team'], side=c['side'])
        self.status.set('已重置。先启动对面官方移动门将，再启动进攻策略；STOP GAME → FORCE START。')

    def stop(self):
        self.stop_opponent()
        if self.practice_sender is not None:
            self.practice_sender.set(0)
            self.practice_sender.close()
            self.practice_sender = None
        if self.process and self.process.poll() is None:
            # Terminate only our exact child PID. Native receive may block Ctrl+C.
            subprocess.run(['taskkill', '/PID', str(self.process.pid), '/T', '/F'],
                           capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            self.process.wait(timeout=5)
        if self.c:
            ids = self.c['attackers'] if self.c['profile']=='qualifier' else [self.c['goalie']] if self.c['profile']=='goalie' else self.c['attackers']+[self.c['goalie']]
            for _ in range(3):
                stop_robots(self.c['team'], ids)
        if self.output:
            self.output.close()
            self.output = None
        self.process = None
        self.c = None
        self.status.set('策略已停止，练习裁判发送已关闭，本机仿真已发送零速度。Client/grSim 保留打开。')

    def logs(self):
        (ROOT/'logs').mkdir(exist_ok=True)
        os.startfile(ROOT/'logs')

    def poll(self):
        try:
            self.poll_opponent()
        except OSError as exc:
            self.stop_opponent()
            self.opponent_status.set('门将监控失败：'+str(exc))
        if self.process:
            if self.process.poll() is not None:
                code = self.process.returncode
                self.stop()
                self.status.set(f'策略已退出，返回码 {code}。请看 logs 控制台日志。')
            else:
                try:
                    heartbeat = ROOT/f'heartbeat-{self.c["team"]}.txt'
                    if heartbeat.exists():
                        stamp = heartbeat.stat().st_mtime
                        self.saw_heartbeat |= stamp > self.started_at
                        if self.saw_heartbeat and time.time()-stamp > 2.5:
                            self.stop()
                            self.status.set('视觉/策略心跳中断超过 2.5 秒，已结束本策略并发送本机零速度。检查 Client FPS 后重新启动。')
                            self.window.after(500, self.poll)
                            return
                    if not self.saw_heartbeat and time.time()-self.started_at > 30:
                        self.stop()
                        self.status.set('启动 30 秒未获得有效视觉，已停止。确认 Simulator 正在运行、Client FPS 持续大于 0 后重试。')
                        self.window.after(500, self.poll)
                        return
                    lines = self.logfile.read_text(encoding='utf-8', errors='replace').splitlines()
                    useful = [s for s in lines[-30:] if '[MATCH]' in s or 'Error' in s]
                    if useful:
                        self.status.set(useful[-1])
                except OSError:
                    pass
        self.window.after(500, self.poll)

    def close(self):
        self.stop()
        if self.practice_sender is not None:
            self.practice_sender.close()
        self.window.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    app = App(root)
    root.mainloop()
