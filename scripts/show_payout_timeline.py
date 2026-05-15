#!/usr/bin/env python3
"""Simulate full payout cadence over 3 months on funded account."""
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

    daily_r = df.groupby('date')['total_r'].sum().reset_index()
    daily_r.columns = ['date', 'r']
    daily_r = daily_r.sort_values('date').reset_index(drop=True)

    contracts = 20
    mnq_tick_val = 0.50
    risk_ticks = df['risk_ticks'].median()
    r_to_usd = risk_ticks * contracts * mnq_tick_val

    r_vals = daily_r['r'].values
    dates = daily_r['date'].values
    n = len(r_vals)

    sim_days = 45  # ~60 calendar days of trading
    payout_caps = [5000, 5000, 5000, 5000, 5000]  # TopStep 100K: $5K max per payout
    payout_cycle_calendar = 3  # can request every 3 calendar days
    payout_cycle_trading = 5  # but need ~5 trading days to build profit
    profit_split = 1.00  # 100% of first $10K, then 90%
    min_buffer = 800
    min_payout = 500
    daily_loss_limit = 500

    # Run many sims, collect detailed traces
    all_sims = []
    for start in range(min(n - sim_days, n)):
        bal = 103000.0
        floor = 100000.0
        total_paid = 0.0
        payout_num = 0
        blown = False
        payouts = []
        daily_log = []
        cycle_pnl = 0.0
        days_in_cycle = 0

        for day_i in range(sim_days):
            idx = (start + day_i) % n
            day_usd = r_vals[idx] * r_to_usd

            if day_usd < -daily_loss_limit:
                blown = True
                break

            bal += day_usd
            cycle_pnl += day_usd
            days_in_cycle += 1

            if bal <= floor:
                blown = True
                break

            daily_log.append({
                'day': day_i + 1,
                'pnl': day_usd,
                'bal': bal,
                'floor': floor,
                'buffer': bal - floor,
            })

            # Try payout every 5 trading days
            if days_in_cycle >= payout_cycle_trading:
                buffer = bal - floor
                if cycle_pnl > 0 and buffer > min_buffer:
                    max_gross = buffer - min_buffer
                    cap_idx = min(payout_num, len(payout_caps) - 1)
                    cap = payout_caps[cap_idx]
                    gross = min(cycle_pnl, max_gross)
                    payout = min(gross * profit_split, cap)
                    payout = max(payout, 0)

                    if payout >= min_payout:
                        total_paid += payout
                        payout_num += 1
                        bal -= payout / profit_split
                        payouts.append({
                            'day': day_i + 1,
                            'week': (day_i) // 5 + 1,
                            'payout_num': payout_num,
                            'gross': payout / profit_split,
                            'net': payout,
                            'cap': cap,
                            'bal_after': bal,
                            'buffer_after': bal - floor,
                        })

                cycle_pnl = 0.0
                days_in_cycle = 0

        all_sims.append({
            'start': start,
            'blown': blown,
            'total_paid': total_paid,
            'num_payouts': payout_num,
            'payouts': payouts,
            'final_bal': bal if not blown else 0,
            'daily_log': daily_log,
        })

    # === Summary stats ===
    total_paids = np.array([s['total_paid'] for s in all_sims])
    num_payouts = np.array([s['num_payouts'] for s in all_sims])
    blown_arr = np.array([s['blown'] for s in all_sims])

    print("=" * 75)
    print("  FUNDED ACCOUNT PAYOUT SIMULATION — 3 MONTHS (65 TRADING DAYS)")
    print(f"  9 MNQ | Start $53K | Floor $51K | Caps: $2K/$2K/$4K/$4K/$6K")
    print(f"  Payout every ~5 trading days | 90/10 split | $800 min buffer kept")
    print("=" * 75)

    print(f"\n  Survival rate: {(~blown_arr).mean():.0%} ({(~blown_arr).sum()}/{len(blown_arr)})")
    print(f"  Blown rate:    {blown_arr.mean():.0%}")

    survived = [s for s in all_sims if not s['blown']]
    if survived:
        surv_paid = np.array([s['total_paid'] for s in survived])
        surv_npay = np.array([s['num_payouts'] for s in survived])
        print(f"\n  --- Among survived accounts ---")
        print(f"  Avg total payout:    ${surv_paid.mean():,.0f}")
        print(f"  Median total payout: ${np.median(surv_paid):,.0f}")
        print(f"  P25 payout:          ${np.percentile(surv_paid, 25):,.0f}")
        print(f"  P75 payout:          ${np.percentile(surv_paid, 75):,.0f}")
        print(f"  P90 payout:          ${np.percentile(surv_paid, 90):,.0f}")
        print(f"  Max payout:          ${surv_paid.max():,.0f}")
        print(f"  Avg # payouts:       {surv_npay.mean():.1f}")
        print(f"  Max # payouts:       {surv_npay.max()}")

        # Payout by number distribution
        print(f"\n  --- Payout timeline (when each payout typically arrives) ---")
        print(f"  {'Payout#':>8s}  {'Cap':>6s}  {'AvgDay':>7s}  {'AvgWeek':>8s}  {'Avg$':>7s}  {'% get it':>8s}")
        for pn in range(1, 8):
            p_data = []
            for s in survived:
                for pay in s['payouts']:
                    if pay['payout_num'] == pn:
                        p_data.append(pay)
            if not p_data:
                break
            avg_day = np.mean([p['day'] for p in p_data])
            avg_week = avg_day / 5
            avg_net = np.mean([p['net'] for p in p_data])
            pct_get = len(p_data) / len(survived) * 100
            cap = payout_caps[min(pn-1, len(payout_caps)-1)]
            print(f"  {pn:8d}  ${cap:5,}  {avg_day:6.0f}d  {avg_week:6.1f}wk  ${avg_net:6,.0f}  {pct_get:7.0f}%")

    # === Show 3 detailed examples ===
    survived_sorted = sorted(survived, key=lambda s: s['total_paid'])

    examples = []
    if survived_sorted:
        # Median payout example
        mid = survived_sorted[len(survived_sorted)//2]
        examples.append(('TYPICAL (median payout)', mid))
        # Good example (P75)
        p75_idx = int(len(survived_sorted) * 0.75)
        examples.append(('GOOD SCENARIO (P75)', survived_sorted[p75_idx]))
        # Great example (P90)
        p90_idx = int(len(survived_sorted) * 0.90)
        examples.append(('GREAT SCENARIO (P90)', survived_sorted[p90_idx]))

    for label, sim in examples:
        print(f"\n  {'=' * 70}")
        print(f"  {label} — Total paid: ${sim['total_paid']:,.0f} in {sim['num_payouts']} payouts")
        print(f"  {'=' * 70}")

        # Show week-by-week summary with payouts marked
        log = sim['daily_log']
        payouts = sim['payouts']
        payout_days = {p['day']: p for p in payouts}

        print(f"  {'Week':>5s}  {'Days':>8s}  {'Week PnL':>9s}  {'Balance':>10s}  {'Buffer':>8s}  {'Payout':>20s}  {'Total Paid':>11s}")
        print(f"  {'':->5s}  {'':->8s}  {'':->9s}  {'':->10s}  {'':->8s}  {'':->20s}  {'':->11s}")

        total_paid_so_far = 0
        for week in range(1, 14):
            start_day = (week - 1) * 5 + 1
            end_day = min(week * 5, len(log))
            if start_day > len(log):
                break

            week_entries = [e for e in log if start_day <= e['day'] <= end_day]
            if not week_entries:
                break

            week_pnl = sum(e['pnl'] for e in week_entries)
            end_bal = week_entries[-1]['bal']
            end_buffer = week_entries[-1]['buffer']

            # Check for payouts this week
            week_payouts = [payout_days[d] for d in range(start_day, end_day + 1) if d in payout_days]
            payout_str = ""
            for wp in week_payouts:
                total_paid_so_far += wp['net']
                payout_str = f"#{wp['payout_num']} ${wp['net']:,.0f} (cap ${wp['cap']:,})"

            print(f"  {week:5d}  {start_day:3d}-{end_day:3d}  ${week_pnl:+8,.0f}  "
                  f"${end_bal:9,.0f}  ${end_buffer:7,.0f}  {payout_str:>20s}  ${total_paid_so_far:10,.0f}")

    # === Multi-account projection ===
    print(f"\n{'=' * 75}")
    print("  MULTI-ACCOUNT SUMMER PROJECTION (June → August)")
    print(f"{'=' * 75}")

    eval_pass_rate = 0.75
    eval_cost = 78
    reset_cost = 45

    for n_accts in [1, 2, 3, 5, 8]:
        passed = round(n_accts * eval_pass_rate)
        if passed == 0:
            passed = 1
        failed = n_accts - passed
        cost = n_accts * eval_cost + failed * reset_cost

        # From survived distribution
        if survived:
            avg_per = surv_paid.mean()
            med_per = float(np.median(surv_paid))
            p75_per = float(np.percentile(surv_paid, 75))
            p90_per = float(np.percentile(surv_paid, 90))
        else:
            avg_per = med_per = p75_per = p90_per = 0

        surv_rate = (~blown_arr).mean()
        # Expected accounts that survive funded phase
        funded_survive = passed * surv_rate

        avg_total = avg_per * funded_survive
        med_total = med_per * funded_survive
        p75_total = p75_per * funded_survive
        p90_total = p90_per * funded_survive

        print(f"\n  Buy {n_accts} eval(s) @ ${eval_cost} each:")
        print(f"    ~{passed} pass eval (75%) → ~{funded_survive:.1f} survive funded ({surv_rate:.0%})")
        print(f"    Total cost: ${cost}")
        print(f"    {'':>10s}  {'Gross':>8s}  {'- Cost':>8s}  {'= Net':>8s}")
        print(f"    {'Average':>10s}  ${avg_total:7,.0f}  ${cost:7,}  ${avg_total - cost:7,.0f}")
        print(f"    {'Median':>10s}  ${med_total:7,.0f}  ${cost:7,}  ${med_total - cost:7,.0f}")
        print(f"    {'Good(P75)':>10s}  ${p75_total:7,.0f}  ${cost:7,}  ${p75_total - cost:7,.0f}")
        print(f"    {'Great(P90)':>10s}  ${p90_total:7,.0f}  ${cost:7,}  ${p90_total - cost:7,.0f}")

    # === Cash flow timeline ===
    print(f"\n{'=' * 75}")
    print("  EXPECTED CASH FLOW TIMELINE (single account, typical scenario)")
    print(f"{'=' * 75}")
    print(f"  Week 1-2:   Pass eval (9 trading days avg)")
    print(f"  Week 3:     First few funded trading days, build buffer")
    if survived:
        # Get typical first payout timing
        first_payouts = [s['payouts'][0] for s in survived if s['payouts']]
        if first_payouts:
            avg_first_day = np.mean([p['day'] for p in first_payouts])
            avg_first_amt = np.mean([p['net'] for p in first_payouts])
            print(f"  Week {avg_first_day/5 + 2:.0f}:     First payout ~${avg_first_amt:,.0f} (avg day {avg_first_day:.0f} of funded)")

        second_payouts = [s['payouts'][1] for s in survived if len(s['payouts']) >= 2]
        if second_payouts:
            avg_second_day = np.mean([p['day'] for p in second_payouts])
            avg_second_amt = np.mean([p['net'] for p in second_payouts])
            print(f"  Week {avg_second_day/5 + 2:.0f}:     Second payout ~${avg_second_amt:,.0f} (avg day {avg_second_day:.0f} of funded)")

        third_payouts = [s['payouts'][2] for s in survived if len(s['payouts']) >= 3]
        if third_payouts:
            avg_third_day = np.mean([p['day'] for p in third_payouts])
            avg_third_amt = np.mean([p['net'] for p in third_payouts])
            print(f"  Week {avg_third_day/5 + 2:.0f}:    Third payout ~${avg_third_amt:,.0f} (cap goes to $4K)")

    print(f"{'=' * 75}")


if __name__ == '__main__':
    main()
