"""EMA Mean Reversion — fade extended moves beyond 20-EMA back toward the mean."""
from __future__ import annotations
import pandas as pd
from datetime import time as dt_time
from strategy.models.base import BaseModel, ModelRiskProfile, Signal
from config import Config


class EMAReversionModel(BaseModel):
    name = 'ema_rev'
    priority = 30

    def __init__(self, cfg: Config):
        rp = ModelRiskProfile(
            min_risk_ticks=40, max_risk_ticks=60, min_rr=1.3,
            be_trigger_rr=0.6, partial_rr=0.5, partial_pct=0.0,
            time_stop_minutes=35, max_daily=1,
            trail_pct=0.001,
        )
        super().__init__(cfg, rp)

    def generate(self, df: pd.DataFrame, daily: pd.DataFrame,
                 context: dict) -> list[Signal]:
        regime_map = context['regime_map']
        daily_map = context['daily_map']

        ema = df['close'].ewm(span=20, adjust=False).mean()
        dev = (df['close'] - ema).rolling(20, min_periods=10).std()

        signals: list[Signal] = []
        cur_date = None
        used = 0

        for idx in range(60, len(df)):
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
            if not (dt_time(9, 50) <= t < dt_time(14, 30)):
                continue

            e = ema.iloc[idx]
            dv = dev.iloc[idx]
            if pd.isna(e) or pd.isna(dv) or dv < 2 * self.tick:
                continue

            z = (bar['close'] - e) / dv
            prev = df.iloc[idx - 1]
            regime = regime_map[d]

            if (z < -2.5
                    and bar['close'] > bar['open']
                    and bar['close'] > prev['low']
                    and regime != 'bear'):
                entry = bar['close']
                stop = min(bar['low'], prev['low']) - 4 * self.tick
                risk = entry - stop
                target = e
                reward = target - entry

                if reward > 0 and self._risk_ok(risk, reward):
                    signals.append(self._make_signal(
                        idx, bar, 'long', entry, stop, target, 'ema_rev_long'))
                    used += 1

            elif (z > 2.5
                    and bar['close'] < bar['open']
                    and bar['close'] < prev['high']
                    and regime != 'bull'):
                entry = bar['close']
                stop = max(bar['high'], prev['high']) + 4 * self.tick
                risk = stop - entry
                target = e
                reward = entry - target

                if reward > 0 and self._risk_ok(risk, reward):
                    signals.append(self._make_signal(
                        idx, bar, 'short', entry, stop, target, 'ema_rev_short'))
                    used += 1

        return signals
