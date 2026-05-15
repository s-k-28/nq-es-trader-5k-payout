#!/usr/bin/env python3
"""Show day-by-day funded eval simulation — what it looks like in practice."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import argparse
import pandas as pd
import numpy as np
from config import Config
from data.loader import load_csv, build_daily_bars
from strategy.multi import MultiModelGenerator
from backtest.engine_v2 import BacktestEngineV2
from backtest.metrics_v2 import MetricsV2


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--nq', required=True)
    args = p.parse_args()

    cfg = Config()
    raw = load_csv(args.nq, 'NQ')
    daily = build_daily_bars(raw)
    daily = daily.rename(columns={'date': 'date'})
    daily['date'] = pd.to_datetime(daily['date']).dt.date

    gen = MultiModelGenerator(cfg)
    signals = gen.generate(raw, daily, None)
    engine = BacktestEngineV2(cfg)
    trades = engine.run(raw, signals)
    m = MetricsV2(trades, cfg)
    df = m.df.copy()
    df['date'] = pd.to_datetime(df['entry_time']).dt.date

    # Daily aggregation
    daily_r = df.groupby('date').agg(
        trades=('total_r', 'count'),
        total_r=('total_r', 'sum'),
        models=('model', lambda x: ','.join(sorted(set(x)))),
        wins=('total_r', lambda x: (x > 0).sum()),
        losses=('total_r', lambda x: (x <= 0).sum()),
    ).reset_index().sort_values('date')

    contracts = 20
    mnq_tick_val = 0.50
    risk_ticks = df['risk_ticks'].median()
    r_to_usd = risk_ticks * contracts * mnq_tick_val

    target = 3000
    max_dd = 2000
    daily_loss_limit = 1000

    r_vals = daily_r['total_r'].values
    dates = daily_r['date'].values
    n = len(r_vals)

    # ---- Daily R distribution ----
    print("=" * 70)
    print("  DAILY R DISTRIBUTION (what a typical day looks like)")
    print("=" * 70)
    print(f"  Total trading days: {n}")
    print(f"  Risk per trade: {risk_ticks:.0f} ticks × 20 MNQ × $0.50 = ${r_to_usd:.0f}/R")
    print(f"  Avg daily R: {r_vals.mean():+.2f}R (${r_vals.mean() * r_to_usd:+.0f})")
    print(f"  Median daily R: {np.median(r_vals):+.2f}R (${np.median(r_vals) * r_to_usd:+.0f})")

    pcts = [5, 10, 25, 50, 75, 90, 95]
    print(f"\n  Percentiles:")
    for pct in pcts:
        v = np.percentile(r_vals, pct)
        print(f"    P{pct:2d}: {v:+.2f}R  (${v * r_to_usd:+.0f})")

    green = (r_vals > 0).sum()
    red = (r_vals < 0).sum()
    flat = (r_vals == 0).sum()
    print(f"\n  Green days: {green} ({green/n:.0%})")
    print(f"  Red days:   {red} ({red/n:.0%})")
    print(f"  Flat days:  {flat} ({flat/n:.0%})")

    # Histogram buckets
    print(f"\n  Daily R histogram:")
    buckets = [(-99, -1.5), (-1.5, -1.0), (-1.0, -0.5), (-0.5, 0),
               (0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 99)]
    for lo, hi in buckets:
        count = ((r_vals >= lo) & (r_vals < hi)).sum()
        bar = "#" * count
        label = f"  [{lo:+5.1f}, {hi:+5.1f})" if hi < 99 else f"  [{lo:+5.1f},   +∞)"
        if lo == -99:
            label = f"  (  -∞, {hi:+5.1f})"
        print(f"  {label}  {count:3d} days  {bar}")

    # ---- Example 20-day windows ----
    print(f"\n{'=' * 70}")
    print("  EXAMPLE 20-DAY EVAL WINDOWS (20 MNQ, TopStep 50K)")
    print(f"  Target: ${target:,}  |  Trail DD: ${max_dd:,}  |  Daily limit: ${daily_loss_limit:,}")
    print(f"{'=' * 70}")

    # Find best, worst, and median windows
    results = []
    for start in range(n - 20 + 1):
        eq = 0.0
        peak = 0.0
        passed = blown = False
        end_day = 20
        for j in range(20):
            day_usd = r_vals[start + j] * r_to_usd
            if day_usd < -daily_loss_limit:
                blown = True
                end_day = j + 1
                break
            eq += day_usd
            if eq > peak:
                peak = eq
            dd = peak - eq
            if eq >= target:
                passed = True
                end_day = j + 1
                break
            if dd >= max_dd:
                blown = True
                end_day = j + 1
                break
        results.append({
            'start': start,
            'eq': eq,
            'passed': passed,
            'blown': blown,
            'end_day': end_day,
            'date': dates[start],
        })

    passed_wins = [r for r in results if r['passed']]
    blown_wins = [r for r in results if r['blown']]
    active_wins = [r for r in results if not r['passed'] and not r['blown']]

    # Pick examples: fastest pass, typical pass, close call, blown
    examples = []
    if passed_wins:
        fastest = min(passed_wins, key=lambda r: r['end_day'])
        examples.append(('FASTEST PASS', fastest))
        median_pass = sorted(passed_wins, key=lambda r: r['end_day'])[len(passed_wins)//2]
        examples.append(('TYPICAL PASS', median_pass))
    if blown_wins:
        worst = min(blown_wins, key=lambda r: r['eq'])
        examples.append(('BLOWN ACCOUNT', worst))
    if active_wins:
        close = max(active_wins, key=lambda r: r['eq'])
        examples.append(('CLOSE BUT NO PASS', close))

    for label, result in examples:
        start = result['start']
        print(f"\n  --- {label} (starting {dates[start]}) ---")
        print(f"  {'Day':>4s}  {'Date':>12s}  {'Trades':>6s}  {'Day R':>7s}  {'Day $':>8s}  "
              f"{'Bal':>9s}  {'Peak':>9s}  {'DD':>8s}  {'Status'}")
        print(f"  {'':->4s}  {'':->12s}  {'':->6s}  {'':->7s}  {'':->8s}  "
              f"{'':->9s}  {'':->9s}  {'':->8s}  {'':->10s}")

        eq = 0.0
        peak = 0.0
        bal = 50000.0
        for j in range(min(20, result['end_day'])):
            idx = start + j
            day_r = r_vals[idx]
            day_usd = day_r * r_to_usd
            day_trades = int(daily_r.iloc[idx]['trades'])

            eq += day_usd
            bal = 50000 + eq
            if eq > peak:
                peak = eq
            dd = peak - eq

            status = ""
            if eq >= target:
                status = "*** PASSED ***"
            elif dd >= max_dd:
                status = "!!! BLOWN !!!"
            elif day_usd < -daily_loss_limit:
                status = "!!! DAILY LIMIT !!!"
            elif dd >= max_dd * 0.7:
                status = "DANGER"
            elif eq >= target * 0.8:
                status = "ALMOST"

            print(f"  {j+1:4d}  {str(dates[idx]):>12s}  {day_trades:6d}  "
                  f"{day_r:+7.2f}  ${day_usd:+7.0f}  "
                  f"${bal:8,.0f}  ${50000+peak:8,.0f}  "
                  f"${dd:7,.0f}  {status}")

        final_status = "PASSED" if result['passed'] else ("BLOWN" if result['blown'] else f"ACTIVE (${eq:+,.0f})")
        print(f"  Result: {final_status} after {result['end_day']} days")

    # ---- Summary stats ----
    print(f"\n{'=' * 70}")
    print("  OVERALL EVAL STATS")
    print(f"{'=' * 70}")
    total = len(results)
    passes = len(passed_wins)
    blows = len(blown_wins)
    actives = len(active_wins)
    print(f"  Windows tested: {total}")
    print(f"  Pass:   {passes} ({passes/total:.1%})")
    print(f"  Blown:  {blows} ({blows/total:.1%})")
    print(f"  Active: {actives} ({actives/total:.1%})")

    if passed_wins:
        days_to_pass = [r['end_day'] for r in passed_wins]
        print(f"\n  Days to pass:")
        print(f"    Fastest: {min(days_to_pass)} days")
        print(f"    Median:  {sorted(days_to_pass)[len(days_to_pass)//2]} days")
        print(f"    Slowest: {max(days_to_pass)} days")
        print(f"    Avg:     {sum(days_to_pass)/len(days_to_pass):.1f} days")

    # ---- What a typical week looks like ----
    print(f"\n{'=' * 70}")
    print("  WHAT A TYPICAL WEEK LOOKS LIKE")
    print(f"{'=' * 70}")
    weekly_r = []
    for i in range(0, n - 5, 5):
        week_r = r_vals[i:i+5].sum()
        weekly_r.append(week_r)
    weekly_r = np.array(weekly_r)
    print(f"  Avg week:    {weekly_r.mean():+.2f}R (${weekly_r.mean() * r_to_usd:+,.0f})")
    print(f"  Median week: {np.median(weekly_r):+.2f}R (${np.median(weekly_r) * r_to_usd:+,.0f})")
    print(f"  Best week:   {weekly_r.max():+.2f}R (${weekly_r.max() * r_to_usd:+,.0f})")
    print(f"  Worst week:  {weekly_r.min():+.2f}R (${weekly_r.min() * r_to_usd:+,.0f})")
    green_w = (weekly_r > 0).sum()
    print(f"  Green weeks: {green_w}/{len(weekly_r)} ({green_w/len(weekly_r):.0%})")


if __name__ == '__main__':
    main()
