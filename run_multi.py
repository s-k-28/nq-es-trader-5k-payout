#!/usr/bin/env python3
"""Multi-model NQ day trading strategy backtester.

Usage:
  python3 run_multi.py --nq data/Dataset_NQ_1min_2022_2025.csv
  python3 run_multi.py --nq data/mnq_2026_1min.csv --history data/Dataset_NQ_1min_2022_2025.csv
"""
import argparse
import os
import pandas as pd
from config import Config
from data.loader import load_csv, build_daily_bars
from strategy.multi import MultiModelGenerator
from backtest.engine_v2 import BacktestEngineV2
from backtest.metrics_v2 import MetricsV2
from tiers import apply_tier, VALID_TIERS


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--nq', required=True)
    p.add_argument('--nq-daily', default=None)
    p.add_argument('--history', default=None,
                   help='Historical NQ data to prepend for regime warmup (auto-detected if not given)')
    p.add_argument('--es', default=None)
    p.add_argument('--account', type=float, default=100000)
    p.add_argument('--risk', type=float, default=1.0)
    p.add_argument('--plot', default=None)
    p.add_argument('--csv', default=None)
    p.add_argument('--tier', default=None, choices=VALID_TIERS,
                   help='Apply an account-tier preset (25k/50k/100k/150k)')
    args = p.parse_args()

    cfg = Config()
    cfg.account_size = args.account
    cfg.risk.risk_per_trade_pct = args.risk
    if args.tier:
        apply_tier(cfg, args.tier)
        print(f"Applied tier preset: {args.tier}")

    raw = load_csv(args.nq, 'NQ')
    print(f"NQ: {len(raw):,} bars  "
          f"({raw['datetime'].min()} -> {raw['datetime'].max()})")

    first_date = raw['datetime'].min()
    history_path = args.history
    if not history_path and first_date.year >= 2026:
        auto_path = os.path.join(os.path.dirname(args.nq), 'Dataset_NQ_1min_2022_2025.csv')
        if os.path.exists(auto_path):
            history_path = auto_path
            print(f"Auto-detected history file: {history_path}")

    if history_path:
        hist = load_csv(history_path, 'NQ')
        cutoff = first_date - pd.Timedelta(days=120)
        hist_tail = hist[hist['datetime'] >= cutoff].copy()
        raw = pd.concat([hist_tail, raw], ignore_index=True)
        raw = raw.sort_values('datetime').drop_duplicates(subset='datetime').reset_index(drop=True)
        print(f"Prepended {len(hist_tail):,} historical bars for regime warmup")
        print(f"Combined: {len(raw):,} bars  "
              f"({raw['datetime'].min()} -> {raw['datetime'].max()})")

    if args.nq_daily:
        daily_raw = load_csv(args.nq_daily, 'NQ')
        daily = daily_raw.rename(columns={'datetime': 'date'})
        daily['date'] = pd.to_datetime(daily['date']).dt.date
    else:
        print("Building daily bars from intraday data...")
        daily = build_daily_bars(raw)
        daily = daily.rename(columns={'date': 'date'})
        daily['date'] = pd.to_datetime(daily['date']).dt.date

    n_daily = len(daily)
    print(f"Daily bars: {n_daily}")
    if n_daily < 50:
        print(f"WARNING: Only {n_daily} daily bars — regime needs 50+, signals will be limited.")
        print(f"Use --history to prepend historical data for regime warmup.")

    df_es = load_csv(args.es, 'ES') if args.es else None

    print("Scanning models...")
    gen = MultiModelGenerator(cfg)
    signals = gen.generate(raw, daily, df_es)

    model_counts = {}
    for s in signals:
        model_counts[s.model] = model_counts.get(s.model, 0) + 1
    for m, c in sorted(model_counts.items()):
        print(f"  {m}: {c} signals")
    print(f"  total: {len(signals)} signals")

    print("Simulating...")
    engine = BacktestEngineV2(cfg)
    trades = engine.run(raw, signals)
    print(f"  {len(trades)} trades")

    if args.csv:
        m = MetricsV2(trades, cfg)
        m.df.to_csv(args.csv, index=False)
        print(f"Trades -> {args.csv}")

    m = MetricsV2(trades, cfg)
    m.print_report()

    if args.plot:
        m.plot(args.plot)

    try:
        m.print_funded_projection(eval_contracts=20)
    except Exception as e:
        print(f"\nFunded projection skipped: {e}")


if __name__ == '__main__':
    main()
