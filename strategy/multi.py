"""Multi-model signal orchestrator — generates, filters, and resolves signals."""
from __future__ import annotations
import pandas as pd
import numpy as np
from datetime import time as dt_time
from config import Config
from strategy.vwap import compute_vwap, compute_opening_range
from strategy.models.base import Signal, GLOBAL_MIN_RISK_TICKS
from strategy.models import ALL_MODELS
from strategy.quality import filter_by_quality


class MultiModelGenerator:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tick = cfg.instrument.tick_size
        self.models = [ModelClass(cfg) for ModelClass in ALL_MODELS]

    def generate(self, df: pd.DataFrame, daily: pd.DataFrame,
                 df_es: pd.DataFrame | None = None) -> list[Signal]:
        df = compute_vwap(df)
        df = compute_opening_range(df, minutes=15)
        df = df.reset_index(drop=True)

        from strategy.quant import compute_all_quant_features
        df = compute_all_quant_features(df)

        context = self._build_context(daily)

        all_signals: list[Signal] = []
        for model in self.models:
            sigs = model.generate(df, daily, context)
            all_signals.extend(sigs)

        all_signals = self._apply_atr_hybrid_wider(all_signals, df)

        filtered = [s for s in all_signals if s.ts.time() < dt_time(15, 30)]
        filtered.sort(key=lambda s: s.idx)
        resolved = self._resolve_conflicts(filtered)
        return filter_by_quality(resolved, df)

    def _apply_atr_hybrid_wider(self, signals: list[Signal],
                                df: pd.DataFrame) -> list[Signal]:
        atr_len = self.cfg.strategy.atr_stop_length
        factor = self.cfg.strategy.atr_stop_factor
        atr_col = f'atr_{atr_len}'
        if atr_col not in df.columns:
            return signals

        result = []
        for sig in signals:
            if sig.idx >= len(df):
                result.append(sig)
                continue

            atr_val = df.iloc[sig.idx].get(atr_col)
            if pd.isna(atr_val) or atr_val <= 0:
                result.append(sig)
                continue

            atr_stop_dist = atr_val * factor
            model_stop_dist = abs(sig.entry - sig.stop)

            if atr_stop_dist > model_stop_dist:
                if sig.direction == 'long':
                    new_stop = sig.entry - atr_stop_dist
                else:
                    new_stop = sig.entry + atr_stop_dist

                new_stop = round(new_stop / self.tick) * self.tick
                new_risk = abs(sig.entry - new_stop)
                new_risk_ticks = new_risk / self.tick

                if new_risk_ticks < GLOBAL_MIN_RISK_TICKS:
                    result.append(sig)
                    continue

                rp = sig.risk_profile
                max_ticks = rp.max_risk_ticks if rp else 200
                if new_risk_ticks > max_ticks:
                    result.append(sig)
                    continue

                new_reward = abs(sig.target - sig.entry)
                new_rr = new_reward / new_risk if new_risk > 0 else 0

                if rp and new_rr < rp.min_rr:
                    continue

                sig.stop = new_stop
                sig.risk_ticks = new_risk_ticks
                sig.reward_ticks = new_reward / self.tick
                sig.rr = new_rr

            result.append(sig)
        return result

    def _build_context(self, daily: pd.DataFrame) -> dict:
        daily_map = {}
        regime_map = {}
        if daily.empty:
            return {'daily_map': daily_map, 'regime_map': regime_map}
        daily_s = daily.sort_values('date').reset_index(drop=True)
        closes = daily_s['close'].values
        n = len(closes)
        ema20 = self._ema(closes, 20) if n >= 2 else closes.copy()
        ema50 = self._ema(closes, 50) if n >= 2 else closes.copy()

        for i in range(1, len(daily_s)):
            d = pd.Timestamp(daily_s.iloc[i]['date']).date()
            prev = daily_s.iloc[i - 1]
            daily_map[d] = {
                'pdh': prev['high'], 'pdl': prev['low'],
                'prev_close': prev['close'],
            }
            c = prev['close']
            above20 = c > ema20[i - 1]
            if i >= 50:
                above50 = c > ema50[i - 1]
                if above20 and above50:
                    regime_map[d] = 'bull'
                elif not above20 and not above50:
                    regime_map[d] = 'bear'
                else:
                    regime_map[d] = 'chop'
            elif i >= 20:
                regime_map[d] = 'bull' if above20 else 'bear'
            else:
                regime_map[d] = 'chop'

        return {'daily_map': daily_map, 'regime_map': regime_map}

    @staticmethod
    def _ema(data, span):
        out = np.empty_like(data, dtype=float)
        out[0] = data[0]
        k = 2.0 / (span + 1)
        for i in range(1, len(data)):
            out[i] = data[i] * k + out[i - 1] * (1 - k)
        return out

    @staticmethod
    def _resolve_conflicts(signals: list[Signal], cooldown_bars: int = 3) -> list[Signal]:
        if not signals:
            return []

        resolved = [signals[0]]
        for sig in signals[1:]:
            last = resolved[-1]
            if sig.idx - last.idx < cooldown_bars:
                if sig.priority < last.priority:
                    resolved[-1] = sig
                elif sig.priority == last.priority and sig.rr > last.rr:
                    resolved[-1] = sig
            else:
                resolved.append(sig)
        return resolved
