"""Explicit LOCAL live operator smoke test. Resets practice scene; never run in a match."""
import argparse
import json
import math
import time
import tkinter as tk
from match_launcher import App
from run_match import ROOT, load_config
from sim_tools import PracticeReferee, reset_scene
from benchmark_match import complete_rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-practice', action='store_true', required=True)
    parser.add_argument('--qt-app', action='store_true', help='Diagnostic explicit Qt application')
    args = parser.parse_args()
    window = tk.Tk()
    window.withdraw()
    config_path = ROOT/'match.json'
    if args.qt_app:
        config = dict(load_config(config_path), qt_application=True)
        config_path = ROOT/'benchmark-operator.json'
        config_path.write_text(json.dumps(config, indent=2), encoding='utf-8')
    app = App(window, config_path=config_path)
    results = {'started_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'config': load_config(config_path)}
    began = time.monotonic()
    practice = PracticeReferee()
    referee = practice.set

    def latest():
        if app.process is None or app.process.poll() is not None:
            raise RuntimeError('Strategy exited during operator check; inspect console log')
        paths = sorted((ROOT/'logs').glob('*-blue.jsonl'), key=lambda p: p.stat().st_mtime)
        rows = list(complete_rows(paths[-1])) if paths else []
        row = rows[-1] if rows else {}
        if time.time()-row.get('time', 0) > 1:
            raise RuntimeError('Stale telemetry; cannot verify referee or robot movement')
        return row

    def drift(a, b):
        positions = a.get('robots', {})
        return max((math.dist(pos, b['robots'][n]) for n, pos in positions.items()
                    if n in b.get('robots', {})), default=0)

    def finish(error=None):
        if error:
            results['error'] = str(error)
        app.stop()
        referee(0)
        practice.close()
        results['child_stopped'] = app.process is None
        data = json.dumps(results, indent=2)
        (ROOT/'logs'/'operator-check.json').write_text(data, encoding='utf-8')
        (ROOT/'logs'/('operator-check-'+time.strftime('%Y%m%d-%H%M%S')+'.json')).write_text(data, encoding='utf-8')
        print(json.dumps(results), flush=True)
        window.destroy()

    def guarded(fn):
        def call():
            try:
                fn()
            except Exception as exc:
                finish(exc)
        return call

    def wait_ready():
        if app.saw_heartbeat:
            referee(0)
            window.after(1000, guarded(initial_halt))
        elif app.process is None:
            finish(app.status.get())
        elif time.monotonic()-began > 25:
            finish('No strategy heartbeat within 25 seconds')
        else:
            window.after(200, guarded(wait_ready))

    def initial_halt():
        results['halt_before'] = latest()
        window.after(1500, guarded(start_play))

    def start_play():
        row = latest()
        results['initial_halt_drift_mm'] = drift(results.pop('halt_before'), row)
        results['initial_mode'] = row.get('mode')
        results['play_before'] = row
        referee(1)
        window.after(400, lambda: referee(3))
        window.after(8500, guarded(end_play))

    def end_play():
        row = latest()
        results['play_drift_mm'] = drift(results.pop('play_before'), row)
        results['play_mode'] = row.get('mode')
        referee(0)
        window.after(800, guarded(after_halt))

    def after_halt():
        results['halt_before'] = latest()
        window.after(1500, guarded(final_halt))

    def final_halt():
        row = latest()
        results['final_halt_drift_mm'] = drift(results.pop('halt_before'), row)
        results['final_mode'] = row.get('mode')
        results['passed'] = (results['initial_mode'] == results['final_mode'] == 'HALT'
                             and results['play_mode'] == 'PLAY'
                             and results['initial_halt_drift_mm'] < 60
                             and results['final_halt_drift_mm'] < 60
                             and results['play_drift_mm'] > 100)
        finish()

    reset_scene()
    app.start()
    window.after(200, guarded(wait_ready))
    window.mainloop()
    return 0 if results.get('passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
