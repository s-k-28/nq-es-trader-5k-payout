#!/usr/bin/env python3
"""Paper trading simulator — runs the 9-model bot on live NQ data with fake money.

No brokerage account needed. Pulls free delayed NQ data from Yahoo Finance,
runs signals in real-time, simulates fills, and shows P&L.

Usage:
  python3 run_paper.py                  # live mode (polls every 60s during market hours)
  python3 run_paper.py --speed 10       # replay today's data at 10x speed
  python3 run_paper.py --replay 2025-05-09  # replay a specific date
"""
import argparse
import logging
import os
import sys
import time
from datetime import datetime, timedelta, time as dt_time
from zoneinfo import ZoneInfo

import pandas as pd
import numpy as np

from config import Config
from data.loader import load_csv, build_daily_bars
from strategy.vwap import compute_vwap, compute_opening_range
from strategy.quant import compute_all_quant_features
from strategy.multi import MultiModelGenerator
from strategy.quality import filter_by_quality
from strategy.models.base import Signal
from live.broker_paper import PaperBroker

ET = ZoneInfo('US/Eastern')
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger(__name__)

MNQ_TICK = 0.50
MAX_CONTRACTS = 20
TICK_SIZE = 0.25


def fetch_nq_bars(period: str = '5d', interval: str = '1m') -> pd.DataFrame:
    """Fetch NQ futures 1-min bars from Yahoo Finance (free, ~15 min delayed)."""
    import yfinance as yf
    ticker = yf.Ticker('NQ=F')
    df = ticker.history(period=period, interval=interval)
    if df.empty:
        return pd.DataFrame()

    df = df.reset_index()
    col_map = {}
    for c in df.columns:
        cl = str(c).lower().strip()
        if cl in ('datetime', 'date', 'index'):
            col_map[c] = 'datetime'
        elif cl == 'open':
            col_map[c] = 'open'
        elif cl == 'high':
            col_map[c] = 'high'
        elif cl == 'low':
            col_map[c] = 'low'
        elif cl == 'close':
            col_map[c] = 'close'
        elif cl == 'volume':
            col_map[c] = 'volume'
    df = df.rename(columns=col_map)

    if 'datetime' not in df.columns:
        df['datetime'] = df.index

    df['datetime'] = pd.to_datetime(df['datetime'])
    if df['datetime'].dt.tz is not None:
        df['datetime'] = df['datetime'].dt.tz_convert('US/Eastern').dt.tz_localize(None)

    df = df[['datetime', 'open', 'high', 'low', 'close', 'volume']].copy()
    df['volume'] = df['volume'].fillna(0).astype(int)
    df.loc[df['volume'] == 0, 'volume'] = 1
    df = df.sort_values('datetime').drop_duplicates(subset='datetime').reset_index(drop=True)
    df['symbol'] = 'NQ'
    return df


def load_history_for_warmup(cfg: Config) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load historical data for feature warmup."""
    hist_path = 'data/Dataset_NQ_1min_2022_2025.csv'
    if not os.path.exists(hist_path):
        log.warning(f"No historical data at {hist_path} — features will have limited warmup")
        return pd.DataFrame(), pd.DataFrame()

    hist = load_csv(hist_path, 'NQ')
    cutoff = hist['datetime'].max() - pd.Timedelta(days=90)
    hist = hist[hist['datetime'] >= cutoff].copy()
    daily = build_daily_bars(hist)
    daily['date'] = pd.to_datetime(daily['date']).dt.date
    return hist, daily


class PaperTrader:
    def __init__(self, cfg: Config, broker: PaperBroker, speed: float = 1.0):
        self.cfg = cfg
        self.broker = broker
        self.speed = speed
        self.gen = MultiModelGenerator(cfg)
        self.risk_map = cfg.funded.model_risk_dollars
        self.dlc = cfg.funded.dollar_loss_cap

        self.active_trade = None
        self.daily_r = 0.0
        self.daily_pnl_usd = 0.0
        self.trade_count = 0
        self.win_count = 0
        self.total_pnl = 0.0
        self.consec_losses = 0
        self.current_date = None

        # Load history for warmup
        log.info("Loading historical data for feature warmup...")
        self.hist_bars, self.hist_daily = load_history_for_warmup(cfg)
        if not self.hist_bars.empty:
            log.info(f"  Warmup: {len(self.hist_bars):,} bars from "
                     f"{self.hist_bars['datetime'].min().date()} to "
                     f"{self.hist_bars['datetime'].max().date()}")

    def process_bars(self, new_bars: pd.DataFrame):
        """Process incoming bars through the full signal pipeline."""
        if new_bars.empty:
            return

        # Combine with history for feature computation
        if not self.hist_bars.empty:
            combined = pd.concat([self.hist_bars, new_bars], ignore_index=True)
            combined = combined.sort_values('datetime').drop_duplicates(subset='datetime').reset_index(drop=True)
        else:
            combined = new_bars.copy()

        # Compute features
        df = compute_vwap(combined)
        df = compute_opening_range(df, minutes=15)
        df = df.reset_index(drop=True)
        df = compute_all_quant_features(df)

        # Build context
        all_daily = build_daily_bars(combined)
        all_daily['date'] = pd.to_datetime(all_daily['date']).dt.date

        if not self.hist_daily.empty:
            combined_daily = pd.concat([self.hist_daily, all_daily], ignore_index=True)
            combined_daily = combined_daily.drop_duplicates(subset='date').sort_values('date').reset_index(drop=True)
        else:
            combined_daily = all_daily

        context = self.gen._build_context(combined_daily)

        # Generate signals from all models
        all_signals = []
        for model in self.gen.models:
            sigs = model.generate(df, combined_daily, context)
            all_signals.extend(sigs)

        # Filter
        filtered = [s for s in all_signals if s.ts.time() < dt_time(14, 30)]
        filtered.sort(key=lambda s: s.idx)
        resolved = MultiModelGenerator._resolve_conflicts(filtered)
        final = filter_by_quality(resolved, df)

        # Only look at signals from new bars
        if not self.hist_bars.empty:
            hist_end = self.hist_bars['datetime'].max()
            final = [s for s in final if s.ts > hist_end]

        return final, df

    def run_live(self):
        """Poll Yahoo Finance every 60s during market hours."""
        self.broker.connect()
        self._print_banner('LIVE')

        try:
            while True:
                now_et = datetime.now(ET)

                # Check if market hours (futures: Sun 6pm - Fri 5pm, RTH 9:30-4:00)
                if now_et.weekday() >= 5 and not (now_et.weekday() == 6 and now_et.hour >= 18):
                    log.info("Markets closed (weekend). Waiting...")
                    time.sleep(300)
                    continue

                if now_et.time() < dt_time(9, 30) or now_et.time() > dt_time(16, 30):
                    log.info(f"Outside RTH ({now_et.strftime('%H:%M')} ET). Waiting for 9:30...")
                    time.sleep(60)
                    continue

                # Reset daily on new day
                d = now_et.date()
                if d != self.current_date:
                    self.current_date = d
                    self.daily_r = 0.0
                    self.daily_pnl_usd = 0.0
                    self.broker.reset_daily()
                    log.info(f"\n{'='*60}")
                    log.info(f"  NEW TRADING DAY: {d}")
                    log.info(f"  Balance: ${self.broker.balance:,.2f} | Total P&L: ${self.total_pnl:+,.2f}")
                    log.info(f"{'='*60}\n")

                # Fetch latest data
                log.info("Fetching NQ data...")
                bars = fetch_nq_bars(period='5d', interval='1m')
                if bars.empty:
                    log.warning("No data received. Retrying in 60s...")
                    time.sleep(60)
                    continue

                latest = bars.iloc[-1]
                self.broker.update_price(latest['close'])
                log.info(f"  NQ @ {latest['close']:.2f} | {latest['datetime']} | {len(bars)} bars")

                # Check active trade management
                if self.active_trade:
                    self._manage_active_trade(latest)

                # Generate signals if no active trade
                if not self.active_trade:
                    result = self.process_bars(bars)
                    if result:
                        signals, df = result
                        today_signals = [s for s in signals
                                         if s.ts.date() == d]
                        if today_signals:
                            best = today_signals[-1]
                            self._evaluate_signal(best, latest)

                # Status
                pos = self.broker.get_position()
                if pos:
                    log.info(f"  Position: {pos['side']} {pos['qty']} @ {pos['avg_price']:.2f} "
                             f"| Unrealized: ${pos['unrealized_pnl']:+,.2f}")

                wait = 60 / self.speed
                log.info(f"  Waiting {wait:.0f}s for next bar...")
                time.sleep(wait)

        except KeyboardInterrupt:
            log.info("\n\nShutting down paper trader...")
            if self.active_trade:
                self.broker.flatten()
            self._print_summary()

    def run_replay(self, date_str: str = None):
        """Replay a day's data bar by bar."""
        self.broker.connect()
        self._print_banner('REPLAY')

        bars = fetch_nq_bars(period='5d', interval='1m')
        if bars.empty:
            log.error("No data to replay")
            return

        if date_str:
            target_date = pd.Timestamp(date_str).date()
        else:
            target_date = bars['datetime'].dt.date.max()

        day_bars = bars[bars['datetime'].dt.date == target_date].reset_index(drop=True)
        rth_bars = day_bars[
            (day_bars['datetime'].dt.time >= dt_time(9, 30)) &
            (day_bars['datetime'].dt.time < dt_time(16, 0))
        ].reset_index(drop=True)

        if rth_bars.empty:
            log.error(f"No RTH bars for {target_date}")
            available = sorted(bars['datetime'].dt.date.unique())
            log.info(f"Available dates: {available}")
            return

        self.current_date = target_date
        log.info(f"\n  Replaying {target_date} — {len(rth_bars)} RTH bars")
        log.info(f"  Speed: {self.speed}x (1 bar every {1/self.speed:.1f}s)\n")

        # Build incremental bar window
        warmup_bars = bars[bars['datetime'].dt.date < target_date].copy()

        for i in range(len(rth_bars)):
            bar = rth_bars.iloc[i]
            t = bar['datetime']

            # Build running window up to this bar
            bars_so_far = pd.concat([
                warmup_bars,
                rth_bars.iloc[:i+1]
            ], ignore_index=True)

            self.broker.update_price(bar['close'])

            # Manage active trade
            if self.active_trade:
                self._check_exits(bar)

            # Generate new signals if flat
            if not self.active_trade and i >= 5:
                result = self.process_bars(bars_so_far)
                if result:
                    signals, df = result
                    current_signals = [s for s in signals if s.ts == t]
                    if current_signals:
                        best = current_signals[0]
                        self._evaluate_signal(best, bar)

            # Progress display every 30 bars
            if i % 30 == 0 or self.active_trade:
                pos_str = ""
                pos = self.broker.get_position()
                if pos:
                    pos_str = f" | {pos['side']} {pos['qty']} @ {pos['avg_price']:.2f}"
                print(f"\r  [{t.strftime('%H:%M')}] NQ {bar['close']:.2f} "
                      f"| Day P&L: ${self.daily_pnl_usd:+,.2f} "
                      f"| Trades: {self.trade_count}{pos_str}    ", end='', flush=True)

            if self.speed > 0:
                time.sleep(1.0 / self.speed)

        # End of day
        if self.active_trade:
            log.info("\n  Session close — flattening position")
            self._close_trade(rth_bars.iloc[-1], 'session_close')

        print()
        self._print_summary()

    def _evaluate_signal(self, sig: Signal, bar):
        """Decide whether to take a signal."""
        # Daily win cap
        if self.daily_r >= 2.0:
            return
        # DLC
        if self.dlc and self.daily_pnl_usd <= -self.dlc:
            return
        # Consec cooldown
        if self.consec_losses >= 10:
            self.consec_losses = 0
            return
        # Min risk ticks
        if sig.risk_ticks < self.cfg.risk.min_risk_ticks:
            return

        # Size the trade
        model_risk = self.risk_map.get(sig.model, 400)
        risk_per_contract = sig.risk_ticks * MNQ_TICK
        contracts = min(MAX_CONTRACTS, int(model_risk / risk_per_contract))
        if contracts <= 0:
            return

        # Enter!
        side = 'buy' if sig.direction == 'long' else 'sell'
        result = self.broker.place_market_order(self.broker.symbol, side, contracts)

        self.active_trade = {
            'signal': sig,
            'contracts': contracts,
            'entry_price': bar['close'],
            'stop': sig.stop,
            'target': sig.target,
            'be_moved': False,
            'trailing': False,
            'trail_stop': sig.stop,
            'mfe': 0.0,
            'entry_time': bar['datetime'],
        }

        log.info(f"\n  >>> ENTRY: {sig.model} {sig.direction.upper()} {contracts} MNQ "
                 f"@ {bar['close']:.2f}")
        log.info(f"      Stop: {sig.stop:.2f} | Target: {sig.target:.2f} | "
                 f"Risk: {sig.risk_ticks:.0f} ticks | RR: {sig.rr:.1f}")

    def _check_exits(self, bar):
        """Check if active trade hit stop/target/time on this bar."""
        if not self.active_trade:
            return

        t = self.active_trade
        sig = t['signal']
        is_long = sig.direction == 'long'
        rp = sig.risk_profile
        risk = abs(sig.entry - sig.stop)

        if risk <= 0:
            return

        # Update MFE
        if is_long:
            best = bar['high'] - t['entry_price']
        else:
            best = t['entry_price'] - bar['low']

        if best > t['mfe']:
            t['mfe'] = best
            # Update trail stop
            if t['trailing'] and rp and rp.trail_pct > 0:
                trail_dist = rp.trail_pct * risk
                if is_long:
                    new_trail = t['entry_price'] + t['mfe'] - trail_dist
                    if new_trail > t['trail_stop']:
                        t['trail_stop'] = round(new_trail / TICK_SIZE) * TICK_SIZE
                else:
                    new_trail = t['entry_price'] - t['mfe'] + trail_dist
                    if new_trail < t['trail_stop']:
                        t['trail_stop'] = round(new_trail / TICK_SIZE) * TICK_SIZE

        # Breakeven
        be_trigger = rp.be_trigger_rr if rp else 0.6
        if not t['be_moved'] and t['mfe'] >= risk * be_trigger:
            t['trail_stop'] = t['entry_price']
            t['be_moved'] = True
            log.info(f"      BE moved to {t['entry_price']:.2f}")

        # Trail activation
        partial_rr = rp.partial_rr if rp else 0.5
        if not t['trailing'] and t['mfe'] >= risk * partial_rr:
            t['trailing'] = True
            if rp and rp.trail_pct > 0:
                log.info(f"      Trail activated")

        cur_stop = t['trail_stop']

        # Check stop
        if is_long and bar['low'] <= cur_stop:
            reason = 'trail' if t['trailing'] else ('breakeven' if t['be_moved'] else 'stop')
            self._close_trade(bar, reason, exit_price=cur_stop)
            return
        if not is_long and bar['high'] >= cur_stop:
            reason = 'trail' if t['trailing'] else ('breakeven' if t['be_moved'] else 'stop')
            self._close_trade(bar, reason, exit_price=cur_stop)
            return

        # Check target (only if not trailing)
        if not t['trailing']:
            if is_long and bar['high'] >= sig.target:
                self._close_trade(bar, 'target', exit_price=sig.target)
                return
            if not is_long and bar['low'] <= sig.target:
                self._close_trade(bar, 'target', exit_price=sig.target)
                return

        # Time stop
        time_limit = rp.time_stop_minutes if rp else 45
        elapsed = (bar['datetime'] - t['entry_time']).total_seconds() / 60
        if elapsed >= time_limit and not t['be_moved']:
            self._close_trade(bar, 'time_stop')
            return

        # Session close
        if bar['datetime'].time() >= dt_time(16, 55):
            self._close_trade(bar, 'session_close')

    def _manage_active_trade(self, bar):
        """Manage active trade on live data."""
        self._check_exits(bar)

    def _close_trade(self, bar, reason: str, exit_price: float = None):
        if not self.active_trade:
            return

        t = self.active_trade
        sig = t['signal']
        is_long = sig.direction == 'long'

        if exit_price is None:
            exit_price = bar['close']

        # Apply slippage
        if reason in ('stop', 'breakeven', 'trail', 'time_stop', 'session_close'):
            exit_price = exit_price - TICK_SIZE * 0.25 if is_long else exit_price + TICK_SIZE * 0.25

        # Calculate R
        risk = abs(sig.entry - sig.stop)
        if risk > 0:
            if is_long:
                r = (exit_price - t['entry_price']) / risk
            else:
                r = (t['entry_price'] - exit_price) / risk
        else:
            r = 0

        # Dollar P&L
        risk_per_contract = sig.risk_ticks * MNQ_TICK
        dollar_pnl = r * risk_per_contract * t['contracts']

        self.daily_r += r
        self.daily_pnl_usd += dollar_pnl
        self.total_pnl += dollar_pnl
        self.trade_count += 1
        if r > 0:
            self.win_count += 1

        if r <= -0.5:
            self.consec_losses += 1
        else:
            self.consec_losses = 0

        # Close position
        side = 'sell' if is_long else 'buy'
        self.broker.place_market_order(self.broker.symbol, side, t['contracts'])

        emoji = '+' if r > 0 else '-'
        log.info(f"\n  <<< EXIT [{reason}]: {sig.model} {sig.direction.upper()} "
                 f"@ {exit_price:.2f} | {r:+.2f}R | ${dollar_pnl:+,.2f}")
        log.info(f"      Day: {self.daily_r:+.2f}R (${self.daily_pnl_usd:+,.2f}) | "
                 f"Session: {self.win_count}/{self.trade_count} wins")

        self.active_trade = None

    def _print_banner(self, mode: str):
        print(f"""
+============================================================+
|          NQ PAPER TRADING SIMULATOR                         |
+============================================================+
|  Models:     9 (Config E — OU, PD, VWAP, OR, OU-Lunch,     |
|              VWAP-Scalp, Open-Drive, Trend, PM Mom)         |
|  Mode:       {mode:<47}|
|  Balance:    ${self.broker.balance:,.0f} (simulated)              |
|  Risk tiers: OU $2,500 | PD $900 | rest $400               |
|  Max MNQ:    20 contracts | Min stop: 25 pts               |
|  Data:       Yahoo Finance NQ=F (~15 min delayed)           |
+------------------------------------------------------------+
|  Press Ctrl+C to stop                                       |
+============================================================+
""")

    def _print_summary(self):
        wr = self.win_count / self.trade_count * 100 if self.trade_count else 0
        print(f"""
+============================================================+
|  SESSION SUMMARY                                            |
+============================================================+
|  Trades:    {self.trade_count:<47}|
|  Wins:      {self.win_count} ({wr:.0f}%){' ' * (43 - len(f'{self.win_count} ({wr:.0f}%)'))  }|
|  Total P&L: ${self.total_pnl:+,.2f}{' ' * (44 - len(f'${self.total_pnl:+,.2f}'))  }|
|  Balance:   ${self.broker.balance:,.2f}{' ' * (44 - len(f'${self.broker.balance:,.2f}'))  }|
+============================================================+
""")


def main():
    p = argparse.ArgumentParser(description='NQ Paper Trading Simulator')
    p.add_argument('--speed', type=float, default=1.0,
                   help='Replay speed multiplier (default: 1.0 = real-time)')
    p.add_argument('--replay', type=str, default=None,
                   help='Replay a specific date (YYYY-MM-DD)')
    p.add_argument('--balance', type=float, default=100000.0,
                   help='Starting paper balance')
    args = p.parse_args()

    cfg = Config()
    cfg.instrument.symbol = 'MNQ'
    cfg.instrument.tick_size = 0.25
    cfg.instrument.tick_value = 0.50
    cfg.instrument.point_value = 2.0

    broker = PaperBroker(starting_balance=args.balance)
    trader = PaperTrader(cfg, broker, speed=args.speed)

    if args.replay:
        trader.run_replay(args.replay)
    else:
        # If market is closed, auto-replay latest available day
        now_et = datetime.now(ET)
        if now_et.time() < dt_time(9, 30) or now_et.time() > dt_time(16, 30) or now_et.weekday() >= 5:
            log.info("Market is closed — switching to replay mode with latest data")
            trader.speed = args.speed if args.speed > 1 else 5.0
            trader.run_replay()
        else:
            trader.run_live()


if __name__ == '__main__':
    main()
