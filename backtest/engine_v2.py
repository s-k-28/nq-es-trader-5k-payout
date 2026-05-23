"""Backtester v2 — funded-account aware, per-signal risk profiles.
No overlapping trades, daily R-based loss limit, consecutive loss cooldown."""
from __future__ import annotations
import pandas as pd
from dataclasses import dataclass
from config import Config
from strategy.models.base import Signal


@dataclass
class Trade:
    signal: Signal
    entry_time: pd.Timestamp
    entry_price: float
    direction: str
    stop_price: float
    target_price: float
    risk: float

    exit_time: pd.Timestamp | None = None
    exit_price: float = 0.0
    exit_reason: str = ''
    partial_r: float = 0.0
    remainder_r: float = 0.0
    total_r: float = 0.0
    moved_be: bool = False
    partial_taken: bool = False
    risk_ticks: float = 0.0
    model: str = ''
    tag: str = ''


class BacktestEngineV2:
    def __init__(self, cfg: Config, daily_win_cap: float = 2.0,
                 consec_cooldown: int = 10):
        self.cfg = cfg
        self.tick = cfg.instrument.tick_size
        self.daily_win_cap = daily_win_cap
        self.consec_cooldown = consec_cooldown

    def run(self, df: pd.DataFrame, signals: list[Signal]) -> list[Trade]:
        trades = []
        daily_r: dict = {}
        daily_model_count: dict = {}
        open_trade_exit: pd.Timestamp | None = None
        consec_losses = 0

        for sig in signals:
            d = sig.ts.date()

            if daily_r.get(d, 0.0) <= -self.cfg.risk.max_daily_loss_r:
                continue

            if daily_r.get(d, 0.0) >= self.daily_win_cap:
                continue

            if open_trade_exit is not None and sig.ts < open_trade_exit:
                continue

            if consec_losses >= self.consec_cooldown:
                consec_losses = 0
                continue

            model_key = (d, sig.model)
            rp = sig.risk_profile
            if rp and daily_model_count.get(model_key, 0) >= rp.max_daily:
                continue

            t = self._sim(df, sig)
            if t is not None:
                trades.append(t)
                daily_r[d] = daily_r.get(d, 0.0) + t.total_r
                daily_model_count[model_key] = daily_model_count.get(model_key, 0) + 1
                open_trade_exit = t.exit_time
                if t.total_r <= -0.5:
                    consec_losses += 1
                else:
                    consec_losses = 0
        return trades

    def _sim(self, df: pd.DataFrame, sig: Signal) -> Trade | None:
        start_mask = df.index[df['datetime'] >= sig.ts]
        if len(start_mask) == 0:
            return None
        fill_idx = start_mask[0]

        risk = abs(sig.entry - sig.stop)
        trade = Trade(
            signal=sig, entry_time=sig.ts, entry_price=sig.entry,
            direction=sig.direction, stop_price=sig.stop,
            target_price=sig.target, risk=risk,
            risk_ticks=sig.risk_ticks, model=sig.model, tag=sig.tag,
        )

        rp = sig.risk_profile
        be_trigger = rp.be_trigger_rr if rp else self.cfg.risk.be_trigger_rr
        partial_rr = rp.partial_rr if rp else self.cfg.risk.partial_rr
        partial_pct = rp.partial_pct if rp else self.cfg.risk.partial_pct
        time_stop_min = rp.time_stop_minutes if rp else self.cfg.strategy.time_stop_minutes

        trail_pct = rp.trail_pct if rp else 0.0
        trail_dist = trail_pct * risk if trail_pct > 0 and risk > 0 else 0.0
        trailing = False

        cur_stop = sig.stop
        be = partial = False
        partial_r = 0.0
        mfe = 0.0
        time_limit = sig.ts + pd.Timedelta(minutes=time_stop_min)
        is_long = sig.direction == 'long'

        for i in range(fill_idx + 1, len(df)):
            b = df.iloc[i]

            if is_long:
                best = b['high'] - sig.entry
                if best > mfe:
                    mfe = best
                    if trailing and trail_dist > 0:
                        new_trail = sig.entry + mfe - trail_dist
                        if new_trail > cur_stop:
                            cur_stop = self._round(new_trail)
                hit_stop = b['low'] <= cur_stop
                hit_target = (not trailing) and b['high'] >= sig.target
            else:
                best = sig.entry - b['low']
                if best > mfe:
                    mfe = best
                    if trailing and trail_dist > 0:
                        new_trail = sig.entry - mfe + trail_dist
                        if new_trail < cur_stop:
                            cur_stop = self._round(new_trail)
                hit_stop = b['high'] >= cur_stop
                hit_target = (not trailing) and b['low'] <= sig.target

            if hit_stop and hit_target:
                if is_long:
                    dist_to_stop = abs(b['open'] - cur_stop)
                    dist_to_target = abs(b['open'] - sig.target)
                else:
                    dist_to_stop = abs(b['open'] - cur_stop)
                    dist_to_target = abs(b['open'] - sig.target)
                if dist_to_stop <= dist_to_target:
                    self._close(trade, b, cur_stop, 'stop_ambiguous',
                                is_long, partial_r, partial, partial_pct)
                else:
                    self._close(trade, b, sig.target, 'target_ambiguous',
                                is_long, partial_r, partial, partial_pct)
                break

            if hit_stop:
                reason = 'trail' if trailing else ('breakeven' if be else 'stop')
                self._close(trade, b, cur_stop, reason,
                            is_long, partial_r, partial, partial_pct)
                break

            if hit_target:
                self._close(trade, b, sig.target, 'target',
                            is_long, partial_r, partial, partial_pct)
                break

            if not partial and risk > 0 and best >= risk * partial_rr:
                partial = True
                partial_r = partial_pct * partial_rr
                trade.partial_taken = True
                if trail_dist > 0:
                    trailing = True

            if not be and risk > 0 and best >= risk * be_trigger:
                cur_stop = sig.entry
                be = True
                trade.moved_be = True

            if b['datetime'] >= time_limit and not be:
                self._close(trade, b, b['close'], 'time_stop',
                            is_long, partial_r, partial, partial_pct)
                break

            if b['datetime'].time() >= self.cfg.sessions.session_close:
                self._close(trade, b, b['close'], 'session_close',
                            is_long, partial_r, partial, partial_pct)
                break
        else:
            last = df.iloc[-1]
            self._close(trade, last, last['close'], 'end_of_data',
                        is_long, partial_r, partial, partial_pct)
        return trade

    def _round(self, p: float) -> float:
        return round(p / self.tick) * self.tick

    def _close(self, trade, bar, exit_price, reason, is_long,
               partial_r, partial_taken, partial_pct):
        trade.exit_time = bar['datetime']
        trade.exit_reason = reason
        risk = trade.risk
        if risk == 0:
            trade.exit_price = exit_price
            trade.total_r = 0
            return
        slip = self.tick * 0.25
        if reason in ('stop', 'breakeven', 'trail', 'stop_ambiguous',
                       'target_ambiguous', 'time_stop', 'session_close',
                       'end_of_data'):
            exit_price = exit_price - slip if is_long else exit_price + slip
        trade.exit_price = exit_price
        raw_r = ((exit_price - trade.entry_price) / risk if is_long
                 else (trade.entry_price - exit_price) / risk)
        if partial_taken:
            trade.partial_r = partial_r
            trade.remainder_r = (1 - partial_pct) * raw_r
            trade.total_r = trade.partial_r + trade.remainder_r
        else:
            trade.remainder_r = raw_r
            trade.total_r = raw_r
