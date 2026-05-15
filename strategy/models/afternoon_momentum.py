"""Afternoon Momentum — trade Kalman slope pullbacks in the PM session."""
from __future__ import annotations
import pandas as pd
from datetime import time as dt_time
from strategy.models.base import BaseModel, ModelRiskProfile, Signal
from config import Config


class AfternoonMomentumModel(BaseModel):
    name = 'pm_mom'
    priority = 50

    def __init__(self, cfg: Config):
        rp = ModelRiskProfile(
            min_risk_ticks=40, max_risk_ticks=80, min_rr=1.5,
            be_trigger_rr=0.6, partial_rr=0.5, partial_pct=0.0,
            time_stop_minutes=30, max_daily=1,
            trail_pct=0.001,
        )
        super().__init__(cfg, rp)

    def generate(self, df: pd.DataFrame, daily: pd.DataFrame,
                 context: dict) -> list[Signal]:
        daily_map = context['daily_map']
        regime_map = context['regime_map']

        signals: list[Signal] = []
        cur_date = None
        used = 0

        for idx in range(120, len(df)):
            bar = df.iloc[idx]
            dt = bar['datetime']
            d = dt.date()
            t = dt.time()

            if d != cur_date:
                cur_date = d
                used = 0

            if d not in daily_map or d not in regime_map:
                continue
            if used >= self.risk_profile.max_daily:
                continue
            if not (dt_time(13, 30) <= t < dt_time(15, 0)):
                continue

            slope = bar.get('kalman_slope')
            level = bar.get('kalman_level')
            if pd.isna(slope) or pd.isna(level) or abs(slope) < 0.15:
                continue

            regime = regime_map[d]

            if (slope > 0.15
                    and bar['close'] > bar['open']
                    and regime != 'bear'):
                if bar['low'] <= level + 5 * self.tick and bar['close'] > level:
                    entry = bar['close']
                    stop = min(bar['low'], level) - 4 * self.tick
                    risk = entry - stop
                    target = entry + risk * 2.0
                    reward = target - entry

                    if risk > 0 and self._risk_ok(risk, reward):
                        signals.append(self._make_signal(
                            idx, bar, 'long', entry, stop, target, 'pm_mom_long'))
                        used += 1

            elif (slope < -0.15
                    and bar['close'] < bar['open']
                    and regime != 'bull'):
                if bar['high'] >= level - 5 * self.tick and bar['close'] < level:
                    entry = bar['close']
                    stop = max(bar['high'], level) + 4 * self.tick
                    risk = stop - entry
                    target = entry - risk * 2.0
                    reward = entry - target

                    if risk > 0 and self._risk_ok(risk, reward):
                        signals.append(self._make_signal(
                            idx, bar, 'short', entry, stop, target, 'pm_mom_short'))
                        used += 1

        return signals
