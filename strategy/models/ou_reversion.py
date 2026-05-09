"""Ornstein-Uhlenbeck Mean Reversion — both directions with full OU filtering.

Entries require:
1. Z-score beyond +/-2.0 sigma (statistically extreme deviation from VWAP)
2. Half-life < 25 bars (reversion expected within time horizon)
3. OU theta > 0 (mean reversion process is active)
4. Hurst < 0.45 (market in mean-reverting regime)
"""
from __future__ import annotations
import pandas as pd
from datetime import time as dt_time
from strategy.models.base import BaseModel, ModelRiskProfile, Signal
from config import Config


class OUReversionModel(BaseModel):
    name = 'ou_rev'
    priority = 15

    def __init__(self, cfg: Config):
        rp = ModelRiskProfile(
            min_risk_ticks=12, max_risk_ticks=100, min_rr=1.5,
            be_trigger_rr=0.6, partial_rr=0.5, partial_pct=0.0,
            time_stop_minutes=35, max_daily=4,
            trail_pct=0.0015,
        )
        super().__init__(cfg, rp)

    def generate(self, df: pd.DataFrame, daily: pd.DataFrame,
                 context: dict) -> list[Signal]:
        regime_map = context['regime_map']
        daily_map = context['daily_map']

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
            if not (dt_time(9, 50) <= t < dt_time(15, 0)):
                continue
            if dt_time(11, 30) <= t < dt_time(13, 30):
                continue

            ou_theta = bar.get('ou_theta')
            ou_hl = bar.get('ou_half_life')
            ou_z = bar.get('ou_zscore')
            hurst = bar.get('hurst')

            if (pd.isna(ou_theta) or pd.isna(ou_hl)
                    or pd.isna(ou_z) or pd.isna(hurst)):
                continue

            if hurst > 0.45:
                continue
            if ou_theta <= 0:
                continue
            if ou_hl > 25 or ou_hl < 2:
                continue

            vwap = bar.get('vwap')
            if pd.isna(vwap):
                continue

            regime = regime_map[d]
            prev1 = df.iloc[idx - 1]

            # Long: price statistically extended below VWAP
            if (ou_z < -2.0
                    and bar['close'] > bar['open']
                    and bar['close'] > prev1['low']):

                if regime == 'bear':
                    continue

                entry = bar['close']
                extreme_low = min(bar['low'], prev1['low'])
                stop = extreme_low - 2 * self.tick
                risk = entry - stop
                target = vwap
                reward = target - entry

                if reward > 0 and self._risk_ok(risk, reward):
                    signals.append(self._make_signal(
                        idx, bar, 'long', entry, stop, target,
                        'ou_rev_long'))
                    used += 1

            # Short: price statistically extended above VWAP
            elif (ou_z > 2.0
                    and bar['close'] < bar['open']
                    and bar['close'] < prev1['high']):

                if regime == 'bull':
                    continue

                entry = bar['close']
                extreme_high = max(bar['high'], prev1['high'])
                stop = extreme_high + 2 * self.tick
                risk = stop - entry
                target = vwap
                reward = entry - target

                if reward > 0 and self._risk_ok(risk, reward):
                    signals.append(self._make_signal(
                        idx, bar, 'short', entry, stop, target,
                        'ou_rev_short'))
                    used += 1

        return signals
