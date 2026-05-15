"""VWAP Mean Reversion — fade extended moves beyond VWAP std bands back toward VWAP."""
from __future__ import annotations
import pandas as pd
from datetime import time as dt_time
from strategy.models.base import BaseModel, ModelRiskProfile, Signal
from config import Config


class VWAPReversionModel(BaseModel):
    name = 'vwap_rev'
    priority = 25

    def __init__(self, cfg: Config):
        rp = ModelRiskProfile(
            min_risk_ticks=40, max_risk_ticks=50, min_rr=1.3,
            be_trigger_rr=0.6, partial_rr=0.5, partial_pct=0.0,
            time_stop_minutes=40, max_daily=2,
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
            if not (dt_time(10, 0) <= t < dt_time(14, 30)):
                continue

            vwap = bar.get('vwap')
            vwap_std = bar.get('vwap_std')
            if pd.isna(vwap) or pd.isna(vwap_std) or vwap_std < 2 * self.tick:
                continue

            dist = (bar['close'] - vwap) / vwap_std
            regime = regime_map[d]
            prev = df.iloc[idx - 1]

            if (dist < -2.0
                    and bar['close'] > bar['open']
                    and bar['close'] > prev['low']
                    and regime != 'bear'):
                entry = bar['close']
                stop = min(bar['low'], prev['low']) - 4 * self.tick
                risk = entry - stop
                target = vwap
                reward = target - entry

                if reward > 0 and risk > 0 and self._risk_ok(risk, reward):
                    signals.append(self._make_signal(
                        idx, bar, 'long', entry, stop, target, 'vwap_rev_long'))
                    used += 1

            elif (dist > 2.0
                    and bar['close'] < bar['open']
                    and bar['close'] < prev['high']
                    and regime != 'bull'):
                entry = bar['close']
                stop = max(bar['high'], prev['high']) + 4 * self.tick
                risk = stop - entry
                target = vwap
                reward = entry - target

                if reward > 0 and risk > 0 and self._risk_ok(risk, reward):
                    signals.append(self._make_signal(
                        idx, bar, 'short', entry, stop, target, 'vwap_rev_short'))
                    used += 1

        return signals
