"""Deep diagnostic: find exactly where R is lost and what to fix."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import pandas as pd
import numpy as np
from datetime import time as dt_time
from config import Config
from data.loader import load_csv, build_daily_bars
from strategy.multi import MultiModelGenerator
from backtest.engine_v2 import BacktestEngineV2

cfg = Config()
raw = load_csv('data/Dataset_NQ_1min_2022_2025.csv', symbol='NQ')
daily = build_daily_bars(raw)
daily = daily.rename(columns={'date': 'date'})
daily['date'] = pd.to_datetime(daily['date']).dt.date

gen = MultiModelGenerator(cfg)
signals = gen.generate(raw, daily)
print(f"Signals: {len(signals)}")
engine = BacktestEngineV2(cfg)
trades = engine.run(raw, signals)

print(f"Total trades: {len(trades)}")
print(f"Total R: {sum(t.total_r for t in trades):.1f}")
print()

# 1. Exit reason breakdown by model
print("=" * 70)
print("  EXIT REASON BREAKDOWN BY MODEL")
print("=" * 70)
from collections import defaultdict
exit_by_model = defaultdict(lambda: defaultdict(list))
for t in trades:
    exit_by_model[t.model][t.exit_reason].append(t.total_r)

for model in sorted(exit_by_model):
    print(f"\n  {model}:")
    for reason in sorted(exit_by_model[model]):
        rs = exit_by_model[model][reason]
        n = len(rs)
        avg = np.mean(rs)
        total = sum(rs)
        print(f"    {reason:20s}  {n:4d} trades  avg {avg:+.2f}R  total {total:+.1f}R")

# 2. Time-of-day analysis
print("\n" + "=" * 70)
print("  TIME-OF-DAY WIN RATE (30-min buckets)")
print("=" * 70)
time_buckets = defaultdict(lambda: {'wins': 0, 'total': 0, 'r': 0.0})
for t in trades:
    hour = t.entry_time.hour
    minute = (t.entry_time.minute // 30) * 30
    bucket = f"{hour:02d}:{minute:02d}"
    time_buckets[bucket]['total'] += 1
    time_buckets[bucket]['r'] += t.total_r
    if t.total_r > 0:
        time_buckets[bucket]['wins'] += 1

for bucket in sorted(time_buckets):
    d = time_buckets[bucket]
    wr = d['wins'] / d['total'] * 100 if d['total'] > 0 else 0
    print(f"  {bucket}  {d['total']:4d} trades  {wr:.0f}% WR  {d['r']:+.1f}R")

# 3. Risk-tick distribution for stops
print("\n" + "=" * 70)
print("  RISK SIZE ANALYSIS (tick buckets)")
print("=" * 70)
risk_buckets = defaultdict(lambda: {'wins': 0, 'total': 0, 'r': 0.0})
for t in trades:
    bucket = int(t.risk_ticks // 20) * 20
    label = f"{bucket}-{bucket+19}"
    risk_buckets[label]['total'] += 1
    risk_buckets[label]['r'] += t.total_r
    if t.total_r > 0:
        risk_buckets[label]['wins'] += 1

for bucket in sorted(risk_buckets, key=lambda x: int(x.split('-')[0])):
    d = risk_buckets[bucket]
    wr = d['wins'] / d['total'] * 100 if d['total'] > 0 else 0
    print(f"  {bucket:>8s} ticks  {d['total']:4d} trades  {wr:.0f}% WR  {d['r']:+.1f}R")

# 4. Consecutive loss streaks
print("\n" + "=" * 70)
print("  LOSS STREAK ANALYSIS")
print("=" * 70)
streak = 0
max_streak = 0
streak_counts = defaultdict(int)
for t in trades:
    if t.total_r <= -0.5:
        streak += 1
    else:
        if streak > 0:
            streak_counts[streak] += 1
        max_streak = max(max_streak, streak)
        streak = 0
if streak > 0:
    streak_counts[streak] += 1
    max_streak = max(max_streak, streak)

print(f"  Max consecutive losses: {max_streak}")
for k in sorted(streak_counts):
    print(f"  {k}-loss streak: {streak_counts[k]} times")

# 5. Daily R distribution
print("\n" + "=" * 70)
print("  DAILY R DISTRIBUTION")
print("=" * 70)
daily_r = defaultdict(float)
daily_trades = defaultdict(int)
for t in trades:
    d = t.entry_time.date()
    daily_r[d] += t.total_r
    daily_trades[d] += 1

rs = list(daily_r.values())
neg_days = [r for r in rs if r < 0]
pos_days = [r for r in rs if r > 0]
flat_days = [r for r in rs if r == 0]

print(f"  Trading days: {len(rs)}")
print(f"  Positive days: {len(pos_days)} ({len(pos_days)/len(rs)*100:.0f}%)  avg {np.mean(pos_days):+.2f}R")
print(f"  Negative days: {len(neg_days)} ({len(neg_days)/len(rs)*100:.0f}%)  avg {np.mean(neg_days):+.2f}R")
print(f"  Flat days: {len(flat_days)}")
print(f"  Worst day: {min(rs):+.2f}R")
print(f"  Best day: {max(rs):+.2f}R")
print(f"  Daily std: {np.std(rs):.2f}R")
print(f"  Daily Sharpe (annualized): {np.mean(rs)/np.std(rs)*np.sqrt(252):.2f}")

# 6. Breakeven analysis - how many BE trades had MFE near partial threshold?
print("\n" + "=" * 70)
print("  BREAKEVEN TRADE ANALYSIS")
print("=" * 70)
be_trades = [t for t in trades if t.exit_reason == 'breakeven']
print(f"  Breakeven exits: {len(be_trades)}")
if be_trades:
    be_r = [t.total_r for t in be_trades]
    print(f"  Avg R on BE exits: {np.mean(be_r):+.3f}R")
    print(f"  Total R from BE: {sum(be_r):+.1f}R")
    partial_be = [t for t in be_trades if t.partial_taken]
    no_partial_be = [t for t in be_trades if not t.partial_taken]
    print(f"  With partial taken: {len(partial_be)} (avg {np.mean([t.total_r for t in partial_be]):+.3f}R)")
    if no_partial_be:
        print(f"  Without partial: {len(no_partial_be)} (avg {np.mean([t.total_r for t in no_partial_be]):+.3f}R)")

# 7. Stop loss analysis - how quickly do stops get hit?
print("\n" + "=" * 70)
print("  STOP LOSS TIMING (bars from entry to stop)")
print("=" * 70)
stop_trades = [t for t in trades if t.exit_reason == 'stop']
if stop_trades:
    durations = []
    for t in stop_trades:
        if t.exit_time and t.entry_time:
            mins = (t.exit_time - t.entry_time).total_seconds() / 60
            durations.append(mins)
    if durations:
        print(f"  Stop trades: {len(stop_trades)}")
        print(f"  Avg time to stop: {np.mean(durations):.0f} min")
        print(f"  Median time to stop: {np.median(durations):.0f} min")
        fast_stops = [d for d in durations if d <= 5]
        print(f"  Stopped within 5 min: {len(fast_stops)} ({len(fast_stops)/len(durations)*100:.0f}%)")
        print(f"  Total R from stops: {sum(t.total_r for t in stop_trades):+.1f}R")

# 8. Model interaction - does signal crowding hurt?
print("\n" + "=" * 70)
print("  SIGNAL CROWDING ANALYSIS")
print("=" * 70)
for i in range(1, len(trades)):
    pass  # We'd need the original signals for this

# 9. Regime analysis
print("\n" + "=" * 70)
print("  REGIME PERFORMANCE")
print("=" * 70)
# Rebuild regime map
mg = MultiModelGenerator(cfg)
daily_s = daily.sort_values('date').reset_index(drop=True)
ctx = mg._build_context(daily)
regime_map = ctx['regime_map']

regime_perf = defaultdict(lambda: {'wins': 0, 'total': 0, 'r': 0.0})
for t in trades:
    d = t.entry_time.date()
    regime = regime_map.get(d, 'unknown')
    regime_perf[regime]['total'] += 1
    regime_perf[regime]['r'] += t.total_r
    if t.total_r > 0:
        regime_perf[regime]['wins'] += 1

for regime in sorted(regime_perf):
    d = regime_perf[regime]
    wr = d['wins'] / d['total'] * 100 if d['total'] > 0 else 0
    print(f"  {regime:8s}  {d['total']:4d} trades  {wr:.0f}% WR  {d['r']:+.1f}R")

# 10. What would perfect daily profit capping look like?
print("\n" + "=" * 70)
print("  DAILY PROFIT CAP SENSITIVITY")
print("=" * 70)
for cap in [1.0, 1.5, 2.0, 2.5, 3.0, 999]:
    capped_r = 0
    day_r = defaultdict(float)
    for t in trades:
        d = t.entry_time.date()
        if day_r[d] < cap:
            day_r[d] += t.total_r
            capped_r += t.total_r
    label = f"cap={cap:.1f}R" if cap < 100 else "no cap"
    days = list(day_r.values())
    neg = sum(1 for r in days if r < 0)
    pos = sum(1 for r in days if r > 0)
    print(f"  {label:12s}  total={capped_r:+.1f}R  pos_days={pos}  neg_days={neg}  "
          f"pos%={pos/(pos+neg)*100:.0f}%  worst={min(days):+.1f}R  daily_std={np.std(days):.2f}")
