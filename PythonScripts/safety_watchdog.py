"""SDK preDecision 阻塞时仍发送本机仿真零命令；仅用于既有本机链路。"""
import threading
import time
from sim_tools import stop_robots


class DecisionWatchdog:
    def __init__(self, team, ids, timeout=.2):
        self.team, self.ids, self.timeout = team, ids, timeout
        self.last_fresh = time.perf_counter()
        self.error = None
        self.closed = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def touch(self, now):
        self.last_fresh = now

    def _run(self):
        while not self.closed.wait(.025):
            if time.perf_counter()-self.last_fresh > self.timeout:
                try:
                    stop_robots(self.team, self.ids)
                except OSError as exc:
                    self.error = str(exc)
                    return

    def close(self):
        self.closed.set()
        self.thread.join(timeout=.2)
