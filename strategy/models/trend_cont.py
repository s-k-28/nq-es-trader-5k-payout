"""Trend Continuation — enter on pullback to fast EMA in established intraday trend."""
from __future__ import annotations
import pandas as pd
import numpy as np
from datetime import time as dt_time
from strategy.models.base import BaseModel, ModelRiskProfile, Signal
from config import Config


class TrendContinuationModel(BaseModel):
    name = 'trend'
    priority = 40

    def __init__(self, cfg: Config):
        rp = ModelRiskProfile(
            min_risk_ticks=40, max_risk_ticks=120, min_rr=2.0,
            be_trigger_rr=0.6, partial_rr=0.5, partial_pct=0.0,
            time_stop_minutes=45, max_daily=5,
            trail_pct=0.001,
        )
        super().__init__(cfg, rp)

    def generate(self, df: pd.DataFrame, daily: pd.DataFrame,
                 context: dict) -> list[Signal]:
        regime_map = context['regime_map']
        daily_map = context['daily_map']

        ema9 = df['close'].ewm(span=9, adjust=False).mean()
        ema21 = df['close'].ewm(span=21, adjust=False).mean()

        signals: list[Signal] = []
        cur_date = None
        used = 0

        for idx in range(30, len(df)):
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
            if dt.weekday() in (0, 4):
                continue
            if not ((dt_time(10, 0) <= t < dt_time(12, 0))
                    or (dt_time(13, 30) <= t < dt_time(15, 0))):
                continue

            regime = regime_map[d]
            if regime == 'chop':
                continue

            hurst = bar.get('hurst')
            if not pd.isna(hurst) and hurst < 0.50:
                continue

            e9 = ema9.iloc[idx]
            e21 = ema21.iloc[idx]
            sep = abs(e9 - e21) / self.tick

            if sep < 5:
                continue

            emas_aligned = all(
                (ema9.iloc[idx - j] > ema21.iloc[idx - j]) == (e9 > e21)
                for j in range(10)
            ) if idx >= 39 else False

            if not emas_aligned:
                continue

            prev1 = df.iloc[idx - 1]

            # Uptrend: EMA9 > EMA21, pullback to EMA9, resume
            if (e9 > e21
                    and sep >= 10
                    and prev1['low'] <= e9 + 3 * self.tick
                    and bar['close'] > e9
                    and bar['close'] > bar['open']
                    and prev1['low'] > e21
                    and regime == 'bull'):

                entry = bar['close']
                pullback_low = min(bar['low'], prev1['low'])
                stop = pullback_low - 4 * self.tick
                risk = entry - stop

                recent_high = df.iloc[max(0, idx - 20):idx]['high'].max()
                target = max(entry + risk * 2.0, recent_high)
                reward = target - entry

                if self._risk_ok(risk, reward):
                    signals.append(self._make_signal(
                        idx, bar, 'long', entry, stop, target,
                        'trend_cont_long'))
                    used += 1

            # Downtrend: EMA9 < EMA21, pullback to EMA9, resume
            elif (e9 < e21
                    and prev1['high'] >= e9 - 3 * self.tick
                    and bar['close'] < e9
                    and bar['close'] < bar['open']
                    and prev1['high'] < e21):

                entry = bar['close']
                pullback_high = max(bar['high'], prev1['high'])
                stop = pullback_high + 4 * self.tick
                risk = stop - entry

                recent_low = df.iloc[max(0, idx - 20):idx]['low'].min()
                target = min(entry - risk * 2.0, recent_low)
                reward = entry - target

                if self._risk_ok(risk, reward):
                    signals.append(self._make_signal(
                        idx, bar, 'short', entry, stop, target,
                        'trend_cont_short'))
                    used += 1

        return signals
