"""VWAP Band Scalper — tighter VWAP z-score fade with faster exits.

Uses OU z-score band 1.5-2.5 (lower threshold than main OU's 2.0),
targets 50% back to VWAP instead of full VWAP for quicker exits.
Higher frequency, faster trail, higher win rate.
"""
from __future__ import annotations
import pandas as pd
from datetime import time as dt_time
from strategy.models.base import BaseModel, ModelRiskProfile, Signal
from config import Config


class VWAPBandScalperModel(BaseModel):
    name = 'vwap_scalp'
    priority = 25

    def __init__(self, cfg: Config):
        rp = ModelRiskProfile(
            min_risk_ticks=40, max_risk_ticks=50, min_rr=1.3,
            be_trigger_rr=0.5, partial_rr=0.4, partial_pct=0.0,
            time_stop_minutes=20, max_daily=3,
            trail_pct=0.001,
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
            if not (dt_time(10, 0) <= t < dt_time(14, 0)):
                continue

            vwap = bar.get('vwap')
            ou_z = bar.get('ou_zscore')
            hurst = bar.get('hurst')

            if pd.isna(vwap) or pd.isna(ou_z) or pd.isna(hurst):
                continue
            if hurst > 0.48:
                continue

            regime = regime_map[d]
            prev1 = df.iloc[idx - 1]

            if (-2.5 < ou_z < -1.5
                    and bar['close'] > bar['open']
                    and bar['close'] > prev1['low']):

                if regime == 'bear':
                    continue

                entry = bar['close']
                stop = min(bar['low'], prev1['low']) - 4 * self.tick
                risk = entry - stop
                target = entry + (vwap - entry) * 0.5
                reward = target - entry

                if reward > 0 and self._risk_ok(risk, reward):
                    signals.append(self._make_signal(
                        idx, bar, 'long', entry, stop, target,
                        'vwap_scalp_long'))
                    used += 1

            elif (1.5 < ou_z < 2.5
                    and bar['close'] < bar['open']
                    and bar['close'] < prev1['high']):

                if regime == 'bull':
                    continue

                entry = bar['close']
                stop = max(bar['high'], prev1['high']) + 4 * self.tick
                risk = stop - entry
                target = entry - (entry - vwap) * 0.5
                reward = entry - target

                if reward > 0 and self._risk_ok(risk, reward):
                    signals.append(self._make_signal(
                        idx, bar, 'short', entry, stop, target,
                        'vwap_scalp_short'))
                    used += 1

        return signals
