"""Liquidity Sweep Reversal — fade stop hunts at key levels.

Based on ICT / Tempo Trades concepts:
- Price sweeps beyond a key level (PDH, PDL, session extreme) taking out stops
- Price closes back inside the level (the sweep / stop hunt)
- Strong reversal candle confirms institutional direction change
- Enter on confirmation with stop beyond the sweep extreme

Time windows: 9:45-11:00 AM and 14:00-15:00 (Silver Bullet windows)
"""
from __future__ import annotations
import pandas as pd
import numpy as np
from datetime import time as dt_time
from strategy.models.base import BaseModel, ModelRiskProfile, Signal
from config import Config


class SweepReversalModel(BaseModel):
    name = 'sweep'
    priority = 35

    def __init__(self, cfg: Config):
        rp = ModelRiskProfile(
            min_risk_ticks=40, max_risk_ticks=80, min_rr=2.0,
            be_trigger_rr=0.6, partial_rr=0.5, partial_pct=0.0,
            time_stop_minutes=30, max_daily=2,
            trail_pct=0.001,
        )
        super().__init__(cfg, rp)

    # Max bars a *live* rolling buffer is ever trimmed to (see
    # live/executor_multi.py `_merge_bars`, which caps self.buf at 30000 bars
    # ~= 20-21 RTH sessions). A backtest df is the full multi-year history and
    # is far larger, so this acts as a reliable "am I a live tail?" guard.
    _LIVE_BUFFER_MAX_BARS = 30000

    def generate(self, df: pd.DataFrame, daily: pd.DataFrame,
                 context: dict) -> list[Signal]:
        daily_map = context['daily_map']
        regime_map = context['regime_map']

        signals: list[Signal] = []
        cur_date = None
        used = 0
        session_high = None
        session_low = None
        session_high_idx = 0
        session_low_idx = 0

        for idx in range(2, len(df)):
            bar = df.iloc[idx]
            dt = bar['datetime']
            d = dt.date()
            t = dt.time()

            if d != cur_date:
                cur_date = d
                used = 0
                session_high = None
                session_low = None

            if d not in daily_map or d not in regime_map:
                continue

            if t >= dt_time(9, 30):
                if session_high is None:
                    session_high = bar['high']
                    session_low = bar['low']
                    session_high_idx = idx
                    session_low_idx = idx
                else:
                    if bar['high'] > session_high:
                        session_high = bar['high']
                        session_high_idx = idx
                    if bar['low'] < session_low:
                        session_low = bar['low']
                        session_low_idx = idx

            if used >= self.risk_profile.max_daily:
                continue

            in_window = (dt_time(9, 45) <= t < dt_time(11, 0) or
                         dt_time(14, 0) <= t < dt_time(15, 0))
            if not in_window:
                continue

            pdh = daily_map[d]['pdh']
            pdl = daily_map[d]['pdl']
            regime = regime_map[d]
            vwap = bar.get('vwap')
            if pd.isna(vwap):
                continue

            prev1 = df.iloc[idx - 1]

            sig = self._check_bearish_sweep(idx, bar, prev1, pdh, session_high,
                                            session_high_idx, vwap, regime, daily_map[d])
            if sig:
                signals.append(sig)
                used += 1
                continue

            sig = self._check_bullish_sweep(idx, bar, prev1, pdl, session_low,
                                            session_low_idx, vwap, regime, daily_map[d])
            if sig:
                signals.append(sig)
                used += 1

        return self._anchor_to_current_bar(signals, df)

    def _anchor_to_current_bar(self, signals: list[Signal],
                               df: pd.DataFrame) -> list[Signal]:
        """Drop signals that are not anchored to the buffer's current day.

        Root-cause fix for the live stale-signal bug: the live executor
        (live/executor_multi.py) re-scans a multi-DAY rolling buffer every tick
        and acts on the chronologically-last signal (`signals[-1]`). Because the
        sweep model can only fire inside two intraday windows (9:45-11:00 /
        14:00-15:00), its newest-possible signal is structurally clamped to a
        past in-window bar. Once the live clock advances past that window/day,
        no newer sweep signal supersedes it, so the SAME historical bar is
        re-emitted as the tail and rejected as stale on every tick forever
        (observed: a 2026-05-22 14:53 signal rejected ~357x over ~3 days).

        Fix: when running against a live rolling buffer, only return signals
        whose confirmation bar falls on the most-recent trading day in the
        buffer — i.e. anchored to the current/just-closed bar's session rather
        than a stale prior day. The model's entry/stop/target/window logic (its
        edge) is untouched; this only suppresses cross-day-stale emissions.

        No-op for backtests: a backtest passes the full multi-year history in a
        single call (far more than `_LIVE_BUFFER_MAX_BARS` bars), where signals
        are consumed by the simulator at their own `ts`; the guard below leaves
        that path's signal list byte-for-byte identical.
        """
        if not signals or df is None or len(df) == 0:
            return signals
        # Only anchor when this looks like a live rolling buffer, never for a
        # large historical backtest dataframe.
        if len(df) > self._LIVE_BUFFER_MAX_BARS:
            return signals
        last_dt = df.iloc[-1]['datetime']
        if pd.isna(last_dt):
            return signals
        last_date = last_dt.date()
        return [s for s in signals if s.ts.date() == last_date]

    def _check_bearish_sweep(self, idx, bar, prev1, pdh, session_high,
                             sh_idx, vwap, regime, levels):
        if regime == 'bull':
            return None

        swept_pdh = (prev1['high'] > pdh + self.tick
                     and prev1['close'] < pdh)
        swept_session = (session_high is not None
                         and idx - sh_idx > 3
                         and prev1['high'] > session_high
                         and prev1['close'] < session_high)

        if not (swept_pdh or swept_session):
            return None

        body = bar['open'] - bar['close']
        if body < 6 * self.tick:
            return None
        if bar['close'] >= bar['open']:
            return None
        if bar['close'] > prev1['close']:
            return None
        if bar['close'] >= vwap:
            return None

        entry = bar['close']
        sweep_extreme = max(prev1['high'], bar['high'])
        stop = sweep_extreme + 4 * self.tick
        risk = stop - entry

        target_level = vwap
        pdl = levels['pdl']
        if pdl < entry:
            target_level = min(target_level, (entry + pdl) / 2)
        target = min(target_level, entry - risk * 2.5)
        reward = entry - target

        if reward > 0 and self._risk_ok(risk, reward):
            tag = 'sweep_pdh_short' if swept_pdh else 'sweep_sh_short'
            return self._make_signal(idx, bar, 'short', entry, stop, target, tag)
        return None

    def _check_bullish_sweep(self, idx, bar, prev1, pdl, session_low,
                             sl_idx, vwap, regime, levels):
        if regime == 'bear':
            return None

        swept_pdl = (prev1['low'] < pdl - self.tick
                     and prev1['close'] > pdl)
        swept_session = (session_low is not None
                         and idx - sl_idx > 3
                         and prev1['low'] < session_low
                         and prev1['close'] > session_low)

        if not (swept_pdl or swept_session):
            return None

        body = bar['close'] - bar['open']
        if body < 6 * self.tick:
            return None
        if bar['close'] <= bar['open']:
            return None
        if bar['close'] < prev1['close']:
            return None
        if bar['close'] <= vwap:
            return None

        entry = bar['close']
        sweep_extreme = min(prev1['low'], bar['low'])
        stop = sweep_extreme - 4 * self.tick
        risk = entry - stop

        target = max(vwap + (vwap - sweep_extreme), entry + risk * 2.5)
        reward = target - entry

        if reward > 0 and self._risk_ok(risk, reward):
            return self._make_signal(idx, bar, 'long', entry, stop, target,
                                     'sweep_pdl_long')
        return None
