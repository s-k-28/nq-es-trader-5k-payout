"""Previous Day Level Reversion — fade at previous day high/low with confirmation."""
from __future__ import annotations
import pandas as pd
from datetime import time as dt_time
from strategy.models.base import BaseModel, ModelRiskProfile, Signal
from config import Config


class PDLevelReversionModel(BaseModel):
    name = 'pd_rev'
    priority = 22

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
            if not (dt_time(9, 50) <= t < dt_time(14, 0)):
                continue

            pdh = daily_map[d]['pdh']
            pdl = daily_map[d]['pdl']
            regime = regime_map[d]
            prev = df.iloc[idx - 1]

            vwap = bar.get('vwap')
            if pd.isna(vwap):
                continue

            if (regime != 'bull'
                    and prev['high'] >= pdh - 2 * self.tick
                    and bar['close'] < pdh
                    and bar['close'] < bar['open']):
                if abs(bar['close'] - pdh) < 20 * self.tick:
                    entry = bar['close']
                    stop = max(prev['high'], bar['high']) + 4 * self.tick
                    risk = stop - entry
                    target = min(vwap, entry - risk * 2.0)
                    reward = entry - target

                    if reward > 0 and risk > 0 and self._risk_ok(risk, reward):
                        signals.append(self._make_signal(
                            idx, bar, 'short', entry, stop, target, 'pd_pdh_short'))
                        used += 1

            elif (regime != 'bear'
                    and prev['low'] <= pdl + 2 * self.tick
                    and bar['close'] > pdl
                    and bar['close'] > bar['open']):
                if abs(bar['close'] - pdl) < 20 * self.tick:
                    entry = bar['close']
                    stop = min(prev['low'], bar['low']) - 4 * self.tick
                    risk = entry - stop
                    target = max(vwap, entry + risk * 2.0)
                    reward = target - entry

                    if reward > 0 and risk > 0 and self._risk_ok(risk, reward):
                        signals.append(self._make_signal(
                            idx, bar, 'long', entry, stop, target, 'pd_pdl_long'))
                        used += 1

        return signals
