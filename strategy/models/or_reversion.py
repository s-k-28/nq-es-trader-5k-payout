"""Opening Range Reversion — fade extended moves beyond the 15-min opening range."""
from __future__ import annotations
import pandas as pd
from datetime import time as dt_time
from strategy.models.base import BaseModel, ModelRiskProfile, Signal
from config import Config


class ORReversionModel(BaseModel):
    name = 'or_rev'
    priority = 28

    def __init__(self, cfg: Config):
        rp = ModelRiskProfile(
            min_risk_ticks=40, max_risk_ticks=60, min_rr=1.5,
            be_trigger_rr=0.6, partial_rr=0.5, partial_pct=0.0,
            time_stop_minutes=35, max_daily=1,
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
        or_high = None
        or_low = None
        or_mid = None

        for idx in range(60, len(df)):
            bar = df.iloc[idx]
            dt = bar['datetime']
            d = dt.date()
            t = dt.time()

            if d != cur_date:
                cur_date = d
                used = 0
                or_high = None
                or_low = None
                or_mid = None

            if d not in daily_map or d not in regime_map:
                continue

            orh = bar.get('or_high')
            orl = bar.get('or_low')
            if (not pd.isna(orh) and not pd.isna(orl)
                    and or_high is None and t >= dt_time(9, 45)):
                or_high = orh
                or_low = orl
                or_mid = (orh + orl) / 2

            if or_high is None or used >= self.risk_profile.max_daily:
                continue
            if not (dt_time(10, 0) <= t < dt_time(12, 0)):
                continue

            or_range = or_high - or_low
            if or_range < 10 * self.tick:
                continue

            regime = regime_map[d]
            prev = df.iloc[idx - 1]

            if (bar['close'] < or_low
                    and bar['close'] > bar['open']
                    and prev['low'] < or_low - 3 * self.tick
                    and regime != 'bear'):
                entry = bar['close']
                stop = min(bar['low'], prev['low']) - 4 * self.tick
                risk = entry - stop
                target = or_mid
                reward = target - entry

                if reward > 0 and risk > 0 and self._risk_ok(risk, reward):
                    signals.append(self._make_signal(
                        idx, bar, 'long', entry, stop, target, 'or_rev_long'))
                    used += 1

            elif (bar['close'] > or_high
                    and bar['close'] < bar['open']
                    and prev['high'] > or_high + 3 * self.tick
                    and regime != 'bull'):
                entry = bar['close']
                stop = max(bar['high'], prev['high']) + 4 * self.tick
                risk = stop - entry
                target = or_mid
                reward = entry - target

                if reward > 0 and risk > 0 and self._risk_ok(risk, reward):
                    signals.append(self._make_signal(
                        idx, bar, 'short', entry, stop, target, 'or_rev_short'))
                    used += 1

        return signals
