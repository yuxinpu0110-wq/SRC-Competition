"""V2 视觉采样门：区分控制循环和真正的新视觉帧。

同一张融合画面可能被 Python 读取多次。规则计数、速度估计和稳定计时只能在
新画面到来时更新；裁判命令仍由每次控制循环处理。单位为毫米、秒。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SampleStatus:
    cycle: int | None
    fresh: bool
    age: float
    reset: bool = False


class ObservationClock:
    def __init__(self, stale_after=.15):
        self.stale_after = stale_after
        self.last_cycle = None
        self.last_fresh_at = None

    def observe(self, now, cycle=None):
        """返回这一控制循环是否包含新视觉数据。

        cycle=None 用于旧测试或没有 cycle 接口的 SDK，此时每次调用都作为新样本。
        cycle 回退通常表示视觉重连；它是新样本，同时要求上层清空速度历史。
        """
        reset = cycle is not None and self.last_cycle is not None and cycle < self.last_cycle
        fresh = cycle is None or self.last_cycle is None or cycle != self.last_cycle
        if fresh:
            self.last_cycle = cycle
            self.last_fresh_at = now
        age = 0.0 if self.last_fresh_at is None else max(0.0, now-self.last_fresh_at)
        return SampleStatus(cycle, fresh, age, reset)

    def stale(self, now):
        return self.last_fresh_at is None or now-self.last_fresh_at > self.stale_after

