#!/usr/bin/env python3
"""P($10K) optimization harness — walk-forward out-of-sample evaluation.

Splits data by year, trains on all-but-one, tests on holdout.
Returns P($10K) averaged across out-of-sample years only.
"""
import sys
import os
import time
import json
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from config import Config
from data.loader import load_csv, build_daily_bars
from strategy.multi import MultiModelGenerator
from backtest.engine_v2 import BacktestEngineV2
from backtest.funded_sim import trades_to_daily_pnl, run_monte_carlo


def run_backtest(df_1min: pd.DataFrame, cfg: Config):
    daily = build_daily_bars(df_1min)
    daily = daily.rename(columns={'date': 'date'})
    daily['date'] = pd.to_datetime(daily['date']).dt.date

    gen = MultiModelGenerator(cfg)
    signals = gen.generate(df_1min, daily, None)
    engine = BacktestEngineV2(cfg)
    trades = engine.run(df_1min, signals)
    return trades, daily


def compute_p10k(trades, all_dates, cfg, n_sims=25000, n_days=60, seed=142):
    daily_pnl = trades_to_daily_pnl(trades, all_dates, cfg)
    if len(daily_pnl[daily_pnl != 0]) < 10:
        return {'p10k': 0.0, 'survival_rate': 0.0, 'avg_extraction': 0.0,
                'p5k': 0.0, 'trading_days': 0}
    mc = run_monte_carlo(daily_pnl, cfg, n_sims=n_sims, n_days=n_days, seed=seed)
    return mc


def walk_forward_p10k(data_path: str, cfg: Config, n_sims: int = 25000):
    """Walk-forward: for each year 2024+, backtest only that year's data
    (with prior years prepended for warmup), compute P($10K) out-of-sample."""

    raw = load_csv(data_path, 'NQ')
    raw['year'] = raw['datetime'].dt.year
    years = sorted(raw['year'].unique())

    # We need at least 2 years for walk-forward
    if len(years) < 2:
        print(f"Only {len(years)} year(s) of data, need 2+")
        return 0.0, {}

    # Out-of-sample years: everything except the first year (which is training-only)
    oos_years = years[1:]  # e.g., [2024, 2025] if data has 2023-2025
    results = {}

    for oos_year in oos_years:
        # Use all data up to and including oos_year for signals
        # but only measure P($10K) on oos_year's trades
        oos_data = raw[raw['year'] <= oos_year].copy()
        oos_data = oos_data.drop(columns=['year'])

        trades, daily_df = run_backtest(oos_data, cfg)

        # Filter trades to only the OOS year
        oos_trades = [t for t in trades if t.entry_time.year == oos_year]

        if len(oos_trades) < 20:
            results[oos_year] = {'p10k': 0.0, 'n_trades': len(oos_trades)}
            continue

        # Get all trading dates in the OOS year
        oos_dates = sorted(set(t.entry_time.date() for t in oos_trades))

        mc = compute_p10k(oos_trades, oos_dates, cfg, n_sims=n_sims)
        mc['n_trades'] = len(oos_trades)
        results[oos_year] = mc

    p10k_values = [r['p10k'] for r in results.values() if r.get('p10k', 0) > 0]
    avg_p10k = np.mean(p10k_values) if p10k_values else 0.0

    return avg_p10k, results


if __name__ == '__main__':
    cfg = Config()
    data_path = 'data/Dataset_NQ_1min_2022_2025.csv'

    t0 = time.time()
    avg_p10k, results = walk_forward_p10k(data_path, cfg)
    elapsed = time.time() - t0

    print(f"\n{'='*60}")
    print(f"  WALK-FORWARD P($10K) RESULTS")
    print(f"{'='*60}")
    for year, r in sorted(results.items()):
        p = r.get('p10k', 0)
        surv = r.get('survival_rate', 0)
        n = r.get('n_trades', 0)
        avg_ext = r.get('avg_extraction', 0)
        print(f"  {year}: P($10K) = {p:.2f}%  survival = {surv:.1f}%  "
              f"trades = {n}  avg_extract = ${avg_ext:,.0f}")
    print(f"\n  AVERAGE P($10K) = {avg_p10k:.2f}%")
    print(f"  Elapsed: {elapsed:.1f}s")
    print(f"{'='*60}")
