"""Kalman Momentum — trade in direction of Kalman filter slope when trending."""
from __future__ import annotations
import pandas as pd
from datetime import time as dt_time
from strategy.models.base import BaseModel, ModelRiskProfile, Signal
from config import Config


class KalmanMomentumModel(BaseModel):
    name = 'kalman_mom'
    priority = 40

    def __init__(self, cfg: Config):
        rp = ModelRiskProfile(
            min_risk_ticks=40, max_risk_ticks=50, min_rr=1.5,
            be_trigger_rr=0.6, partial_rr=0.5, partial_pct=0.0,
            time_stop_minutes=40, max_daily=1,
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
            if not (dt_time(10, 15) <= t < dt_time(14, 0)):
                continue

            hurst = bar.get('hurst')
            if pd.isna(hurst) or hurst < 0.50:
                continue

            slope = bar.get('kalman_slope')
            level = bar.get('kalman_level')
            if pd.isna(slope) or pd.isna(level) or abs(slope) < 0.3:
                continue

            regime = regime_map[d]

            consistent = all(
                not pd.isna(df.iloc[idx - j].get('kalman_slope'))
                and (df.iloc[idx - j]['kalman_slope'] > 0) == (slope > 0)
                for j in range(5)
            )
            if not consistent:
                continue

            if slope > 0.3 and regime != 'bear':
                entry = bar['close']
                stop = bar['low'] - 4 * self.tick
                risk = entry - stop
                target = entry + risk * 2.5
                reward = target - entry

                if risk > 0 and self._risk_ok(risk, reward):
                    signals.append(self._make_signal(
                        idx, bar, 'long', entry, stop, target, 'kalman_mom_long'))
                    used += 1

            elif slope < -0.3 and regime != 'bull':
                entry = bar['close']
                stop = bar['high'] + 4 * self.tick
                risk = stop - entry
                target = entry - risk * 2.5
                reward = entry - target

                if risk > 0 and self._risk_ok(risk, reward):
                    signals.append(self._make_signal(
                        idx, bar, 'short', entry, stop, target, 'kalman_mom_short'))
                    used += 1

        return signals
