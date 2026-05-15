#!/usr/bin/env python3
"""TopStepX 50K Eval Simulator — EXACT rules as of 2026.

TopStepX 50K Trading Combine:
  - $3,000 profit target
  - $2,000 trailing drawdown (locks at $52,000)
  - $1,000 daily loss limit
  - 50% consistency rule: no single day > 50% of total profit
  - 50 MNQ max (5 mini), with scaling plan
  - Minimum 2 trading days
  - Scaling plan: start 20 MNQ, scale up with balance

Funded Account (Express):
  - 90/10 profit split
  - 5 winning days + 30 trading days for full first $10K payout
"""
import pandas as pd
import numpy as np
from tabulate import tabulate


# TopStep 50K scaling plan (MNQ)
# Balance gain → max MNQ
SCALING = [
    (0,    20),   # start: 2 mini = 20 MNQ
    (500,  30),   # +$500: 3 mini = 30 MNQ
    (1000, 40),   # +$1,000: 4 mini = 40 MNQ
    (1500, 50),   # +$1,500: 5 mini = 50 MNQ (max)
]


def max_contracts_for_equity(equity_gain: float) -> int:
    result = SCALING[0][1]
    for threshold, contracts in SCALING:
        if equity_gain >= threshold:
            result = contracts
    return result


def sim_eval_window(r_vals, r_to_usd_per_contract, median_risk_ticks,
                    max_contracts, window=20):
    """Simulate a single eval window with exact TopStep 50K rules."""
    TARGET = 3000
    TRAIL_DD = 2000
    DAILY_LOSS = 1000
    CONSISTENCY_PCT = 0.50
    MIN_DAYS = 2
    DD_LOCK = 2000  # trailing stops when equity reaches start + DD_LOCK

    eq = 0.0
    peak = 0.0
    dd_locked = False
    dd_floor = -TRAIL_DD
    daily_pnls = []
    n_days = len(r_vals)

    for j in range(min(window, n_days)):
        # Determine contracts for this day based on scaling plan
        contracts = min(max_contracts, max_contracts_for_equity(eq))
        r_to_usd = median_risk_ticks * contracts * 0.50

        day_usd = r_vals[j] * r_to_usd

        # Daily loss limit check
        if day_usd < -DAILY_LOSS:
            day_usd = -DAILY_LOSS  # capped at daily limit (position closed)

        eq += day_usd
        daily_pnls.append(day_usd)

        # Trailing DD
        if eq > peak:
            peak = eq
        if not dd_locked and peak >= DD_LOCK:
            dd_locked = True
            dd_floor = peak - TRAIL_DD
        if not dd_locked:
            dd_floor = peak - TRAIL_DD

        # Check blown
        if eq <= dd_floor:
            return {'passed': False, 'blown': True, 'day': j + 1,
                    'eq': eq, 'daily_pnls': daily_pnls}

        # Check pass conditions
        if eq >= TARGET and (j + 1) >= MIN_DAYS:
            # Consistency check: no single day > 50% of total profit
            best_day = max(daily_pnls)
            if best_day <= eq * CONSISTENCY_PCT:
                return {'passed': True, 'blown': False, 'day': j + 1,
                        'eq': eq, 'daily_pnls': daily_pnls}
            # If consistency fails, keep trading to dilute the best day

    # Window expired without pass or blow
    return {'passed': False, 'blown': False, 'day': min(window, n_days),
            'eq': eq, 'daily_pnls': daily_pnls}


def run_rolling_window(df, contract_counts):
    """Rolling window simulation over actual sequential data."""
    df['date'] = pd.to_datetime(df['entry_time']).dt.date
    daily_r = df.groupby('date')['total_r'].sum().reset_index()
    daily_r = daily_r.sort_values('date').reset_index(drop=True)
    r_vals = daily_r['total_r'].values
    median_risk = df['risk_ticks'].median()
    n = len(r_vals)
    WINDOW = 20

    print("=" * 85)
    print("  TOPSTEPX 50K — ROLLING WINDOW (sequential, exact rules)")
    print(f"  Target: $3,000 | Trail DD: $2,000 | Daily limit: $1,000")
    print(f"  Consistency: 50% | Min days: 2 | Scaling plan active")
    print(f"  Dataset: {len(df)} trades, {n} trading days, median risk {median_risk:.0f} ticks")
    print("=" * 85)
    print()

    results = []
    for max_c in contract_counts:
        passes = blown = total = 0
        days_list = []

        for start in range(n - WINDOW + 1):
            total += 1
            window_r = r_vals[start:start + WINDOW]
            result = sim_eval_window(window_r, None, median_risk, max_c, WINDOW)

            if result['passed']:
                passes += 1
                days_list.append(result['day'])
            elif result['blown']:
                blown += 1

        pr = passes / total if total > 0 else 0
        br = blown / total if total > 0 else 0
        med = int(np.median(days_list)) if days_list else 0
        fast = min(days_list) if days_list else 0
        r_usd_max = median_risk * max_c * 0.50

        results.append({
            'Max MNQ': max_c,
            'Start': max_contracts_for_equity(0),
            '$/R max': f'${r_usd_max:,.0f}',
            'Pass': f'{passes}',
            'Blown': f'{blown}',
            'Pass%': f'{pr:.1%}',
            'Blow%': f'{br:.1%}',
            'Fastest': f'{fast}d' if fast else '-',
            'Median': f'{med}d' if med else '-',
        })

    print(tabulate(results, headers='keys', tablefmt='simple'))
    return results


def run_monte_carlo(df, contract_counts, n_sims=20000):
    """Monte Carlo simulation with resampled daily R values."""
    df['date'] = pd.to_datetime(df['entry_time']).dt.date
    daily_r = df.groupby('date')['total_r'].sum().reset_index()
    r_vals = daily_r['total_r'].values
    median_risk = df['risk_ticks'].median()
    WINDOW = 20
    rng = np.random.default_rng(42)

    print(f"\n{'=' * 85}")
    print(f"  MONTE CARLO — {n_sims:,} SIMULATED EVALS (exact TopStep 50K rules)")
    print(f"  Sampling from {len(r_vals)} daily R values with replacement")
    print(f"  Daily limit: $1,000 | Consistency: 50% | Scaling plan active")
    print(f"{'=' * 85}\n")

    results = []
    for max_c in contract_counts:
        passes = blown = 0
        days_list = []

        for _ in range(n_sims):
            sample = rng.choice(r_vals, size=WINDOW, replace=True)
            result = sim_eval_window(sample, None, median_risk, max_c, WINDOW)

            if result['passed']:
                passes += 1
                days_list.append(result['day'])
            elif result['blown']:
                blown += 1

        pr = passes / n_sims
        br = blown / n_sims
        med = int(np.median(days_list)) if days_list else 0
        fast = min(days_list) if days_list else 0
        r_usd_start = median_risk * max_contracts_for_equity(0) * 0.50
        r_usd_max = median_risk * max_c * 0.50

        results.append({
            'Max MNQ': max_c,
            '$/R start': f'${r_usd_start:,.0f}',
            '$/R max': f'${r_usd_max:,.0f}',
            'Pass%': f'{pr:.1%}',
            'Blow%': f'{br:.1%}',
            'Active': f'{1-pr-br:.1%}',
            'Fastest': f'{fast}d' if fast else '-',
            'Median': f'{med}d' if med else '-',
            '_pr': pr, '_br': br, '_r_max': r_usd_max,
        })

    table = [{k: v for k, v in r.items() if not k.startswith('_')} for r in results]
    print(tabulate(table, headers='keys', tablefmt='simple'))
    return results


def show_example_windows(df, max_contracts):
    """Show detailed day-by-day for fastest pass, typical pass, blown."""
    df['date'] = pd.to_datetime(df['entry_time']).dt.date
    daily_r = df.groupby('date')['total_r'].sum().reset_index()
    daily_r = daily_r.sort_values('date').reset_index(drop=True)
    r_vals = daily_r['total_r'].values
    dates = daily_r['date'].values
    median_risk = df['risk_ticks'].median()
    n = len(r_vals)
    WINDOW = 20

    all_windows = []
    for start in range(n - WINDOW + 1):
        window_r = r_vals[start:start + WINDOW]
        result = sim_eval_window(window_r, None, median_risk, max_contracts, WINDOW)
        result['start'] = start
        all_windows.append(result)

    passed_wins = [w for w in all_windows if w['passed']]
    blown_wins = [w for w in all_windows if w['blown']]

    examples = []
    if passed_wins:
        fastest = min(passed_wins, key=lambda w: w['day'])
        examples.append(('FASTEST PASS', fastest))
        idx = len(passed_wins) // 2
        typical = sorted(passed_wins, key=lambda w: w['day'])[idx]
        examples.append(('TYPICAL PASS', typical))
    if blown_wins:
        worst = min(blown_wins, key=lambda w: w['eq'])
        examples.append(('BLOWN (worst)', worst))

    print(f"\n{'=' * 85}")
    print(f"  EXAMPLE WINDOWS — max {max_contracts} MNQ (scaling from 20)")
    print(f"{'=' * 85}")

    for label, win in examples:
        start = win['start']
        pnls = win['daily_pnls']
        print(f"\n  --- {label} (starting {dates[start]}) ---")
        print(f"  {'Day':>4s}  {'Date':>12s}  {'MNQ':>4s}  {'Day R':>7s}  {'Day $':>8s}  "
              f"{'Equity':>9s}  {'Peak':>9s}  {'Trail DD':>9s}  {'Best%':>6s}  {'Status'}")
        print(f"  {'':->4s}  {'':->12s}  {'':->4s}  {'':->7s}  {'':->8s}  "
              f"{'':->9s}  {'':->9s}  {'':->9s}  {'':->6s}  {'':->12s}")

        eq = 0.0
        peak = 0.0
        dd_locked = False
        running_pnls = []

        for j, day_pnl in enumerate(pnls):
            contracts = min(max_contracts, max_contracts_for_equity(eq))
            r_usd = median_risk * contracts * 0.50
            day_r = r_vals[start + j]

            eq += day_pnl
            running_pnls.append(day_pnl)
            if eq > peak:
                peak = eq
            if peak >= 2000:
                dd_locked = True
            dd = peak - eq if not dd_locked else max(0, peak - eq)

            best_day = max(running_pnls) if running_pnls else 0
            consistency = best_day / eq * 100 if eq > 0 else 0

            status = ""
            if win['passed'] and j + 1 == win['day']:
                status = "*** PASSED ***"
            elif win['blown'] and j + 1 == win['day']:
                status = "!!! BLOWN !!!"
            elif dd >= 1500:
                status = "DANGER"
            elif eq >= 2400:
                status = "ALMOST"

            print(f"  {j+1:4d}  {str(dates[start + j]):>12s}  {contracts:4d}  "
                  f"{day_r:+7.2f}  ${day_pnl:+7.0f}  ${50000+eq:8,.0f}  "
                  f"${50000+peak:8,.0f}  ${dd:8,.0f}  {consistency:5.0f}%  {status}")


def compare_rules():
    """Compare old (wrong) rules vs actual TopStep 50K rules."""
    print(f"\n{'=' * 85}")
    print("  RULE COMPARISON — What changed")
    print(f"{'=' * 85}")
    print(f"  {'Rule':<30s} {'Old (wrong)':>15s}  {'Actual':>15s}  {'Impact'}")
    print(f"  {'':->30s} {'':->15s}  {'':->15s}  {'':->20s}")
    print(f"  {'Daily loss limit':<30s} {'NONE':>15s}  {'$1,000':>15s}  HURTS — caps bad days")
    print(f"  {'Consistency rule':<30s} {'NONE':>15s}  {'50%':>15s}  HURTS — can't pass on 1 big day")
    print(f"  {'Min trading days':<30s} {'5':>15s}  {'2':>15s}  HELPS — faster pass possible")
    print(f"  {'Scaling plan':<30s} {'NO':>15s}  {'YES':>15s}  HURTS — start at 20 MNQ")
    print(f"  {'DD locks at $52K':<30s} {'NO':>15s}  {'YES':>15s}  HELPS — safety net")


def main():
    df = pd.read_csv('trades_current.csv')

    compare_rules()

    contract_counts = [20, 25, 30, 35, 40, 45, 50]
    run_rolling_window(df, contract_counts)

    mc_results = run_monte_carlo(df, contract_counts)

    # Find optimal
    viable = [r for r in mc_results if r['_br'] < 0.25]
    if viable:
        best = max(viable, key=lambda r: r['_pr'])
    else:
        best = max(mc_results, key=lambda r: r['_pr'] - r['_br'])

    print(f"\n  RECOMMENDED: max {best['Max MNQ']} MNQ")
    print(f"  — {best['Pass%']} pass rate, {best['Blow%']} blow rate")
    print(f"  — Starts at 20 MNQ, scales up to {best['Max MNQ']} MNQ")
    print(f"  — {best['Median']} median days to pass")

    show_example_windows(df, best['Max MNQ'])

    # Economics
    eval_cost = 165
    pr = best['_pr']
    print(f"\n{'=' * 85}")
    print(f"  ECONOMICS")
    print(f"{'=' * 85}")
    if pr > 0:
        print(f"  Pass probability:       {pr:.1%}")
        print(f"  Eval cost:              ${eval_cost}")
        print(f"  Expected attempts:      {1/pr:.1f}")
        print(f"  Expected cost to pass:  ${eval_cost/pr:,.0f}")
        print(f"  Funded payout split:    90/10")
        print(f"  First $10K:             100% to trader (after 5 win days + 30 trading days)")


if __name__ == '__main__':
    main()
