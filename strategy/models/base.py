from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import pandas as pd
from config import Config


@dataclass
class ModelRiskProfile:
    min_risk_ticks: int = 100
    max_risk_ticks: int = 80
    min_rr: float = 2.0
    be_trigger_rr: float = 1.5
    partial_rr: float = 1.5
    partial_pct: float = 0.5
    time_stop_minutes: int = 45
    max_daily: int = 3
    trail_pct: float = 0.0


@dataclass
class Signal:
    idx: int
    ts: pd.Timestamp
    model: str
    direction: str
    entry: float
    stop: float
    target: float
    risk_ticks: float
    reward_ticks: float
    rr: float
    tag: str = ''
    priority: int = 50
    risk_profile: ModelRiskProfile | None = None


GLOBAL_MIN_RISK_TICKS = 40  # 10 points — hard floor, no model can go below
GLOBAL_MAX_RISK_TICKS = 80  # 20 points — hard ceiling, no model can go above


class BaseModel(ABC):
    name: str = 'base'
    priority: int = 50

    def __init__(self, cfg: Config, risk_profile: ModelRiskProfile | None = None):
        self.cfg = cfg
        self.tick = cfg.instrument.tick_size
        self.risk_profile = risk_profile or ModelRiskProfile()

    @abstractmethod
    def generate(self, df: pd.DataFrame, daily: pd.DataFrame,
                 context: dict) -> list[Signal]:
        ...

    def _risk_ok(self, risk: float, reward: float) -> bool:
        rp = self.risk_profile
        ticks = risk / self.tick
        floor = max(rp.min_risk_ticks, GLOBAL_MIN_RISK_TICKS)
        ceiling = min(rp.max_risk_ticks, GLOBAL_MAX_RISK_TICKS)
        if not (floor <= ticks <= ceiling):
            return False
        if risk <= 0:
            return False
        return (reward / risk) >= rp.min_rr

    def _make_signal(self, idx, bar, direction, entry, stop, target, tag) -> Signal:
        risk = abs(entry - stop)
        reward = abs(target - entry)
        entry_r = self._round(entry)
        stop_r = self._round(stop)
        target_r = self._round(target)
        return Signal(
            idx=idx, ts=bar['datetime'], model=self.name,
            direction=direction, entry=entry_r, stop=stop_r,
            target=target_r, risk_ticks=risk / self.tick,
            reward_ticks=reward / self.tick,
            rr=reward / risk if risk > 0 else 0,
            tag=tag, priority=self.priority,
            risk_profile=self.risk_profile,
        )

    def _round(self, p: float) -> float:
        return round(p / self.tick) * self.tick
