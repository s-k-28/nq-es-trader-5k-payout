"""Opening Drive Momentum — capture the 9:35-10:15 opening push.

If price moves 5+ points from the 9:30 open in one direction within
the first 5 minutes, enters on a pullback to VWAP in the push direction.
High win rate (49%), strong profit factor (2.39), short hold time.
"""
from __future__ import annotations
import pandas as pd
from datetime import time as dt_time
from strategy.models.base import BaseModel, ModelRiskProfile, Signal
from config import Config


class OpeningDriveModel(BaseModel):
    name = 'open_drive'
    priority = 12

    def __init__(self, cfg: Config):
        rp = ModelRiskProfile(
            min_risk_ticks=40, max_risk_ticks=60, min_rr=1.5,
            be_trigger_rr=0.5, partial_rr=0.5, partial_pct=0.0,
            time_stop_minutes=30, max_daily=2,
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
        day_open = None
        day_high_930 = None
        day_low_930 = None
        drive_dir = None

        for idx in range(30, len(df)):
            bar = df.iloc[idx]
            dt = bar['datetime']
            d = dt.date()
            t = dt.time()

            if d != cur_date:
                cur_date = d
                used = 0
                day_open = None
                day_high_930 = None
                day_low_930 = None
                drive_dir = None

            if d not in daily_map or d not in regime_map:
                continue

            if t == dt_time(9, 30) and day_open is None:
                day_open = bar['open']
                day_high_930 = bar['high']
                day_low_930 = bar['low']
                continue

            if day_open is None:
                continue

            if dt_time(9, 30) < t <= dt_time(9, 35):
                day_high_930 = max(day_high_930, bar['high'])
                day_low_930 = min(day_low_930, bar['low'])
                if day_high_930 - day_open >= 5.0:
                    drive_dir = 'long'
                elif day_open - day_low_930 >= 5.0:
                    drive_dir = 'short'
                continue

            if drive_dir is None:
                continue
            if used >= self.risk_profile.max_daily:
                continue
            if not (dt_time(9, 36) <= t <= dt_time(10, 15)):
                continue

            vwap = bar.get('vwap')
            if pd.isna(vwap):
                continue

            regime = regime_map[d]
            prev1 = df.iloc[idx - 1]
            slope = bar.get('kalman_slope')

            if drive_dir == 'long' and regime != 'bear':
                if (bar['low'] <= vwap + 3 * self.tick
                        and bar['close'] > vwap
                        and bar['close'] > bar['open']
                        and (pd.isna(slope) or slope > -0.1)):
                    entry = bar['close']
                    stop = min(bar['low'], day_low_930) - 4 * self.tick
                    risk = entry - stop
                    target = entry + risk * 2.0
                    reward = target - entry

                    if risk > 0 and self._risk_ok(risk, reward):
                        signals.append(self._make_signal(
                            idx, bar, 'long', entry, stop, target,
                            'open_drive_long'))
                        used += 1

            elif drive_dir == 'short' and regime != 'bull':
                if (bar['high'] >= vwap - 3 * self.tick
                        and bar['close'] < vwap
                        and bar['close'] < bar['open']
                        and (pd.isna(slope) or slope < 0.1)):
                    entry = bar['close']
                    stop = max(bar['high'], day_high_930) + 4 * self.tick
                    risk = stop - entry
                    target = entry - risk * 2.0
                    reward = entry - target

                    if risk > 0 and self._risk_ok(risk, reward):
                        signals.append(self._make_signal(
                            idx, bar, 'short', entry, stop, target,
                            'open_drive_short'))
                        used += 1

        return signals
