#!/usr/bin/env python3
"""Generate comprehensive equity curve dashboard with multiple charts."""
import sys
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.gridspec import GridSpec
from datetime import time as dt_time

from config import Config
from data.loader import load_csv, build_daily_bars
from strategy.multi import MultiModelGenerator
from backtest.engine_v2 import BacktestEngineV2
from backtest.funded_sim import trades_to_daily_pnl, run_monte_carlo, run_eval_monte_carlo


def run_backtest():
    cfg = Config()
    print("Loading data...")
    raw = load_csv('data/Dataset_NQ_1min_2022_2025.csv', 'NQ')

    try:
        raw_2026 = load_csv('data/mnq_2026_1min.csv', 'NQ')
        raw = pd.concat([raw, raw_2026], ignore_index=True)
        raw = raw.sort_values('datetime').reset_index(drop=True)
        print(f"  Combined: {len(raw):,} bars ({raw['datetime'].min().date()} to {raw['datetime'].max().date()})")
    except Exception:
        print(f"  Loaded: {len(raw):,} bars")

    daily = build_daily_bars(raw)
    daily = daily.rename(columns={'date': 'date'})
    daily['date'] = pd.to_datetime(daily['date']).dt.date

    print("Generating signals...")
    gen = MultiModelGenerator(cfg)
    signals = gen.generate(raw, daily, None)
    print(f"  {len(signals)} signals")

    print("Running backtest...")
    engine = BacktestEngineV2(cfg, daily_win_cap=2.0, consec_cooldown=10)
    trades = engine.run(raw, signals)
    print(f"  {len(trades)} trades")

    return cfg, trades, daily, raw


def build_trade_df(trades):
    rows = []
    for t in trades:
        rows.append({
            'entry_time': t.entry_time,
            'exit_time': t.exit_time,
            'direction': t.direction,
            'model': t.model,
            'tag': t.tag,
            'entry': t.entry_price,
            'exit': t.exit_price,
            'stop': t.stop_price,
            'target': t.target_price,
            'reason': t.exit_reason,
            'risk_ticks': t.risk_ticks,
            'total_r': t.total_r,
            'moved_be': t.moved_be,
            'partial_taken': t.partial_taken,
        })
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['entry_time']).dt.date
    df['month'] = pd.to_datetime(df['entry_time']).dt.to_period('M')
    df['year'] = pd.to_datetime(df['entry_time']).dt.year
    df['dow'] = pd.to_datetime(df['entry_time']).dt.dayofweek
    df['hour'] = pd.to_datetime(df['entry_time']).dt.hour
    return df


MODEL_COLORS = {
    'ou_rev': '#2196F3',
    'pd_rev': '#FF9800',
    'vwap_rev': '#4CAF50',
    'or_rev': '#9C27B0',
    'ema_rev': '#F44336',
    'sweep': '#795548',
    'kalman_mom': '#00BCD4',
    'trend': '#E91E63',
    'pm_mom': '#607D8B',
}


def fig1_equity_and_drawdown(df, cfg, trades):
    """Main equity curve with dollar P&L and drawdown."""
    fig = plt.figure(figsize=(18, 14))
    gs = GridSpec(3, 1, height_ratios=[3, 1.5, 1.5], hspace=0.25, figure=fig)

    all_dates = sorted(df['date'].unique())
    daily_pnl = trades_to_daily_pnl(trades, all_dates, cfg)
    cum_pnl = np.cumsum(daily_pnl)
    dates_dt = [pd.Timestamp(d) for d in all_dates]

    # Panel 1: Cumulative dollar P&L
    ax1 = fig.add_subplot(gs[0])
    ax1.fill_between(dates_dt, cum_pnl, 0, where=cum_pnl >= 0,
                     color='#2196F3', alpha=0.3, interpolate=True)
    ax1.fill_between(dates_dt, cum_pnl, 0, where=cum_pnl < 0,
                     color='#F44336', alpha=0.3, interpolate=True)
    ax1.plot(dates_dt, cum_pnl, color='#1565C0', linewidth=1.8)
    ax1.axhline(0, color='gray', linewidth=0.5, linestyle='--')
    ax1.set_title('Cumulative Dollar P&L (Model-Tiered Risk Sizing, 100K Account)',
                  fontsize=14, fontweight='bold', pad=12)
    ax1.set_ylabel('Cumulative P&L ($)', fontsize=11)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))
    ax1.grid(True, alpha=0.3)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    ax1.xaxis.set_major_locator(mdates.MonthLocator(interval=3))

    final_pnl = cum_pnl[-1]
    ax1.annotate(f'Final: ${final_pnl:,.0f}',
                 xy=(dates_dt[-1], cum_pnl[-1]),
                 xytext=(-80, 15), textcoords='offset points',
                 fontsize=11, fontweight='bold', color='#1565C0',
                 arrowprops=dict(arrowstyle='->', color='#1565C0'))

    # Panel 2: Daily P&L bars
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    colors = ['#4CAF50' if p >= 0 else '#F44336' for p in daily_pnl]
    ax2.bar(dates_dt, daily_pnl, color=colors, alpha=0.7, width=1.5)
    ax2.axhline(0, color='gray', linewidth=0.5)
    ax2.axhline(-500, color='red', linewidth=1, linestyle='--', alpha=0.5, label='DLC $500')
    ax2.set_ylabel('Daily P&L ($)', fontsize=10)
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))
    ax2.legend(loc='lower left', fontsize=9)
    ax2.grid(True, alpha=0.3)

    # Panel 3: Drawdown
    ax3 = fig.add_subplot(gs[2], sharex=ax1)
    peak = np.maximum.accumulate(cum_pnl)
    dd = cum_pnl - peak
    ax3.fill_between(dates_dt, dd, 0, color='#F44336', alpha=0.4)
    ax3.plot(dates_dt, dd, color='#D32F2F', linewidth=1)
    ax3.axhline(-3000, color='red', linewidth=1.5, linestyle='--', alpha=0.7, label='$3K Trailing DD')
    ax3.set_ylabel('Drawdown ($)', fontsize=10)
    ax3.set_xlabel('Date', fontsize=10)
    ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))
    ax3.legend(loc='lower left', fontsize=9)
    ax3.grid(True, alpha=0.3)

    max_dd = dd.min()
    max_dd_idx = np.argmin(dd)
    ax3.annotate(f'Max DD: ${max_dd:,.0f}',
                 xy=(dates_dt[max_dd_idx], max_dd),
                 xytext=(40, -15), textcoords='offset points',
                 fontsize=10, color='#D32F2F',
                 arrowprops=dict(arrowstyle='->', color='#D32F2F'))

    fig.savefig('chart_equity_drawdown.png', dpi=180, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)
    print("  Saved chart_equity_drawdown.png")
    return daily_pnl, all_dates


def fig2_model_breakdown(df):
    """Per-model performance: equity curves, win rates, trade counts."""
    models = sorted(df['model'].unique())
    n_models = len(models)

    fig = plt.figure(figsize=(20, 16))
    gs = GridSpec(3, 2, hspace=0.35, wspace=0.3, figure=fig)
    fig.suptitle('Per-Model Performance Breakdown', fontsize=16, fontweight='bold', y=0.98)

    # Panel 1: Stacked equity curves by model
    ax1 = fig.add_subplot(gs[0, :])
    for model in models:
        grp = df[df['model'] == model].sort_values('entry_time')
        cum_r = grp['total_r'].cumsum().values
        ax1.plot(range(len(cum_r)), cum_r, label=model,
                 color=MODEL_COLORS.get(model, 'gray'), linewidth=1.5)
    ax1.set_title('Cumulative R by Model', fontsize=13, fontweight='bold')
    ax1.set_xlabel('Trade #')
    ax1.set_ylabel('Cumulative R')
    ax1.legend(loc='upper left', ncol=3, fontsize=9)
    ax1.grid(True, alpha=0.3)
    ax1.axhline(0, color='gray', linewidth=0.5, linestyle='--')

    # Panel 2: Win rate by model (bar chart)
    ax2 = fig.add_subplot(gs[1, 0])
    model_stats = []
    for model in models:
        grp = df[df['model'] == model]
        wr = (grp['total_r'] > 0).mean() * 100
        n = len(grp)
        exp = grp['total_r'].mean()
        tot_r = grp['total_r'].sum()
        model_stats.append({'model': model, 'wr': wr, 'n': n, 'exp': exp, 'tot_r': tot_r})

    stats_df = pd.DataFrame(model_stats)
    bars = ax2.bar(stats_df['model'], stats_df['wr'],
                   color=[MODEL_COLORS.get(m, 'gray') for m in stats_df['model']],
                   alpha=0.8, edgecolor='white', linewidth=0.5)
    ax2.axhline(50, color='gray', linewidth=1, linestyle='--', alpha=0.5)
    ax2.set_title('Win Rate by Model (%)', fontsize=12, fontweight='bold')
    ax2.set_ylabel('Win Rate (%)')
    ax2.set_ylim(0, 80)
    for bar, n in zip(bars, stats_df['n']):
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                 f'n={n}', ha='center', va='bottom', fontsize=8)
    ax2.tick_params(axis='x', rotation=30)

    # Panel 3: Expectancy by model
    ax3 = fig.add_subplot(gs[1, 1])
    colors_exp = ['#4CAF50' if e > 0 else '#F44336' for e in stats_df['exp']]
    bars = ax3.bar(stats_df['model'], stats_df['exp'], color=colors_exp,
                   alpha=0.8, edgecolor='white', linewidth=0.5)
    ax3.axhline(0, color='gray', linewidth=1, linestyle='--')
    ax3.set_title('Expectancy per Trade (R)', fontsize=12, fontweight='bold')
    ax3.set_ylabel('Avg R per Trade')
    for bar, tr in zip(bars, stats_df['tot_r']):
        ax3.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.01,
                 f'{tr:+.0f}R', ha='center', va='bottom', fontsize=8)
    ax3.tick_params(axis='x', rotation=30)

    # Panel 4: Trade count by model (pie chart)
    ax4 = fig.add_subplot(gs[2, 0])
    pie_colors = [MODEL_COLORS.get(m, 'gray') for m in stats_df['model']]
    wedges, texts, autotexts = ax4.pie(
        stats_df['n'], labels=stats_df['model'], colors=pie_colors,
        autopct='%1.0f%%', startangle=90, textprops={'fontsize': 9})
    ax4.set_title('Trade Distribution by Model', fontsize=12, fontweight='bold')

    # Panel 5: Exit reason breakdown
    ax5 = fig.add_subplot(gs[2, 1])
    exit_stats = df.groupby('reason').agg(
        count=('total_r', 'count'),
        avg_r=('total_r', 'mean')
    ).sort_values('count', ascending=True)
    exit_colors = ['#4CAF50' if r > 0 else '#F44336' for r in exit_stats['avg_r']]
    ax5.barh(exit_stats.index, exit_stats['count'], color=exit_colors, alpha=0.8)
    for i, (cnt, ar) in enumerate(zip(exit_stats['count'], exit_stats['avg_r'])):
        ax5.text(cnt + 2, i, f'{ar:+.2f}R', va='center', fontsize=9)
    ax5.set_title('Exit Reasons (colored by avg R)', fontsize=12, fontweight='bold')
    ax5.set_xlabel('Count')

    fig.savefig('chart_model_breakdown.png', dpi=180, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)
    print("  Saved chart_model_breakdown.png")


def fig3_monthly_yearly(df, daily_pnl, all_dates, cfg, trades):
    """Monthly returns heatmap and yearly comparison."""
    fig = plt.figure(figsize=(18, 14))
    gs = GridSpec(2, 2, hspace=0.35, wspace=0.3, figure=fig)
    fig.suptitle('Monthly & Yearly Performance', fontsize=16, fontweight='bold', y=0.98)

    # Build monthly dollar P&L
    date_pnl = dict(zip(all_dates, daily_pnl))
    monthly = {}
    for d, pnl in date_pnl.items():
        key = (pd.Timestamp(d).year, pd.Timestamp(d).month)
        monthly[key] = monthly.get(key, 0.0) + pnl

    # Panel 1: Monthly returns heatmap
    ax1 = fig.add_subplot(gs[0, :])
    years = sorted(set(k[0] for k in monthly.keys()))
    months = list(range(1, 13))
    month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                   'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    data = np.full((len(years), 12), np.nan)
    for (y, m), val in monthly.items():
        yi = years.index(y)
        data[yi, m-1] = val

    vmax = max(abs(np.nanmin(data)), abs(np.nanmax(data)))
    im = ax1.imshow(data, cmap='RdYlGn', aspect='auto',
                    vmin=-vmax, vmax=vmax, interpolation='nearest')
    ax1.set_xticks(range(12))
    ax1.set_xticklabels(month_names, fontsize=10)
    ax1.set_yticks(range(len(years)))
    ax1.set_yticklabels(years, fontsize=11)
    ax1.set_title('Monthly Dollar P&L Heatmap', fontsize=13, fontweight='bold')

    for yi in range(len(years)):
        for mi in range(12):
            val = data[yi, mi]
            if not np.isnan(val):
                color = 'white' if abs(val) > vmax * 0.6 else 'black'
                ax1.text(mi, yi, f'${val:,.0f}', ha='center', va='center',
                         fontsize=8, fontweight='bold', color=color)

    cbar = fig.colorbar(im, ax=ax1, pad=0.02, shrink=0.8)
    cbar.set_label('P&L ($)', fontsize=10)

    # Panel 2: Yearly summary bars
    ax2 = fig.add_subplot(gs[1, 0])
    yearly = {}
    yearly_trades = {}
    yearly_wr = {}
    for y in years:
        mask = df['year'] == y
        grp = df[mask]
        yearly_trades[y] = len(grp)
        yearly_wr[y] = (grp['total_r'] > 0).mean() * 100 if len(grp) else 0
        yearly[y] = sum(monthly.get((y, m), 0) for m in months)

    yvals = [yearly[y] for y in years]
    ycolors = ['#4CAF50' if v >= 0 else '#F44336' for v in yvals]
    bars = ax2.bar(range(len(years)), yvals, color=ycolors, alpha=0.8,
                   edgecolor='white', linewidth=0.5)
    ax2.set_xticks(range(len(years)))
    ax2.set_xticklabels(years, fontsize=11)
    ax2.set_title('Annual Dollar P&L', fontsize=12, fontweight='bold')
    ax2.set_ylabel('P&L ($)')
    ax2.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))
    ax2.axhline(0, color='gray', linewidth=0.5, linestyle='--')
    for bar, v, nt in zip(bars, yvals, [yearly_trades[y] for y in years]):
        ax2.text(bar.get_x() + bar.get_width()/2, bar.get_height() + max(abs(v)*0.02, 50),
                 f'${v:,.0f}\n({nt} trades)', ha='center', va='bottom', fontsize=9)
    ax2.grid(True, alpha=0.2, axis='y')

    # Panel 3: Yearly win rate + expectancy
    ax3 = fig.add_subplot(gs[1, 1])
    x = range(len(years))
    ax3.bar(x, [yearly_wr[y] for y in years], color='#2196F3', alpha=0.7, label='Win Rate %')
    ax3.axhline(50, color='gray', linewidth=1, linestyle='--', alpha=0.5)
    ax3.set_xticks(x)
    ax3.set_xticklabels(years, fontsize=11)
    ax3.set_ylabel('Win Rate (%)', color='#2196F3', fontsize=10)
    ax3.set_title('Yearly Win Rate & Avg Daily $', fontsize=12, fontweight='bold')
    ax3.set_ylim(0, 80)

    ax3r = ax3.twinx()
    yearly_avg_daily = {}
    for y in years:
        y_days = [daily_pnl[i] for i, d in enumerate(all_dates) if pd.Timestamp(d).year == y and daily_pnl[i] != 0]
        yearly_avg_daily[y] = np.mean(y_days) if y_days else 0
    ax3r.plot(x, [yearly_avg_daily[y] for y in years], 'o-', color='#FF9800',
              linewidth=2, markersize=8, label='Avg Daily $')
    ax3r.set_ylabel('Avg Daily P&L ($)', color='#FF9800', fontsize=10)
    ax3r.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))

    lines1, labels1 = ax3.get_legend_handles_labels()
    lines2, labels2 = ax3r.get_legend_handles_labels()
    ax3.legend(lines1 + lines2, labels1 + labels2, loc='upper left', fontsize=9)

    fig.savefig('chart_monthly_yearly.png', dpi=180, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)
    print("  Saved chart_monthly_yearly.png")


def fig4_funded_mc(daily_pnl, all_dates, cfg):
    """Monte Carlo funded account simulation results."""
    fig = plt.figure(figsize=(18, 14))
    gs = GridSpec(2, 2, hspace=0.35, wspace=0.3, figure=fig)
    fig.suptitle('Monte Carlo Funded Account Simulation (25,000 sims)',
                 fontsize=16, fontweight='bold', y=0.98)

    rng = np.random.default_rng(142)
    n_sims = 25000
    n_days = 60

    extractions = []
    equity_paths = []
    survived_paths = []
    blown_paths = []

    for sim in range(n_sims):
        sample = rng.choice(daily_pnl, size=n_days, replace=True)
        balance = 0.0
        peak = 0.0
        floor = -cfg.funded.trailing_dd
        static = False
        green_days = 0
        extracted = 0.0
        path = [0.0]
        blown = False

        for pnl in sample:
            if pnl != 0 and static:
                scale = 1.0
                for threshold, factor in cfg.funded.post_static_scaling:
                    if balance > threshold:
                        scale = factor
                        break
                pnl *= scale

            balance += pnl
            if pnl >= cfg.funded.green_day_min:
                green_days += 1
            if balance > peak:
                peak = balance
            if not static:
                floor = peak - cfg.funded.trailing_dd
                if peak >= cfg.funded.static_threshold:
                    static = True
                    floor = 0.0
            if balance <= floor:
                blown = True
                path.append(balance)
                break
            if green_days >= cfg.funded.green_days_per_payout and balance > 0:
                payout = min(cfg.funded.max_payout, balance * cfg.funded.payout_balance_pct)
                if payout > 0:
                    extracted += payout
                    balance -= payout
                    green_days = 0
            path.append(balance)

        extractions.append(extracted)
        if sim < 200:
            if blown:
                blown_paths.append(path)
            else:
                survived_paths.append(path)
        equity_paths.append(path)

    exs = np.array(extractions)

    # Panel 1: Sample equity paths
    ax1 = fig.add_subplot(gs[0, 0])
    for path in survived_paths[:80]:
        ax1.plot(range(len(path)), path, color='#4CAF50', alpha=0.08, linewidth=0.8)
    for path in blown_paths[:80]:
        ax1.plot(range(len(path)), path, color='#F44336', alpha=0.08, linewidth=0.8)
    ax1.axhline(0, color='gray', linewidth=1, linestyle='--')
    ax1.axhline(-3000, color='red', linewidth=1.5, linestyle='--', alpha=0.6, label='$3K DD')
    ax1.axhline(3000, color='green', linewidth=1.5, linestyle='--', alpha=0.6, label='Static Threshold')
    ax1.set_title('Sample Equity Paths (160 sims)', fontsize=12, fontweight='bold')
    ax1.set_xlabel('Trading Day')
    ax1.set_ylabel('Account Balance ($)')
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))
    ax1.legend(loc='upper left', fontsize=9)
    ax1.grid(True, alpha=0.3)

    # Panel 2: Extraction distribution
    ax2 = fig.add_subplot(gs[0, 1])
    ax2.hist(exs, bins=60, color='#2196F3', alpha=0.7, edgecolor='white', linewidth=0.5)
    ax2.axvline(5000, color='orange', linewidth=2, linestyle='--', label='$5K')
    ax2.axvline(10000, color='red', linewidth=2, linestyle='--', label='$10K')
    ax2.axvline(np.median(exs), color='green', linewidth=2, linestyle='-', label=f'Median: ${np.median(exs):,.0f}')
    ax2.set_title('Total Extraction Distribution', fontsize=12, fontweight='bold')
    ax2.set_xlabel('Total Extracted ($)')
    ax2.set_ylabel('Frequency')
    ax2.xaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))
    ax2.legend(fontsize=9)
    ax2.grid(True, alpha=0.3)

    # Panel 3: Survival and extraction rates
    ax3 = fig.add_subplot(gs[1, 0])
    survived = (exs > 0).sum()
    blown_count = n_sims - survived
    survival_rate = survived / n_sims * 100
    p5k = (exs >= 5000).sum() / n_sims * 100
    p10k = (exs >= 10000).sum() / n_sims * 100
    p15k = (exs >= 15000).sum() / n_sims * 100
    p20k = (exs >= 20000).sum() / n_sims * 100

    thresholds = ['Survived\n($>0)', 'P($5K+)', 'P($10K+)', 'P($15K+)', 'P($20K+)']
    rates = [survival_rate, p5k, p10k, p15k, p20k]
    bar_colors = ['#4CAF50', '#2196F3', '#FF9800', '#9C27B0', '#F44336']
    bars = ax3.bar(thresholds, rates, color=bar_colors, alpha=0.8, edgecolor='white')
    for bar, rate in zip(bars, rates):
        ax3.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 1,
                 f'{rate:.1f}%', ha='center', va='bottom', fontsize=11, fontweight='bold')
    ax3.set_title('Funded Account Probability Outcomes', fontsize=12, fontweight='bold')
    ax3.set_ylabel('Probability (%)')
    ax3.set_ylim(0, 105)
    ax3.grid(True, alpha=0.2, axis='y')

    # Panel 4: Stats summary table
    ax4 = fig.add_subplot(gs[1, 1])
    ax4.axis('off')

    active = daily_pnl[daily_pnl != 0]
    wins = daily_pnl[daily_pnl > 0]
    losses = daily_pnl[daily_pnl < 0]

    stats_data = [
        ['Metric', 'Value'],
        ['Simulations', f'{n_sims:,}'],
        ['Sim Window', f'{n_days} trading days'],
        ['Survival Rate', f'{survival_rate:.1f}%'],
        ['P($5K+)', f'{p5k:.1f}%'],
        ['P($10K+)', f'{p10k:.1f}%'],
        ['P($15K+)', f'{p15k:.1f}%'],
        ['Avg Extraction', f'${exs.mean():,.0f}'],
        ['Median Extraction', f'${np.median(exs):,.0f}'],
        ['', ''],
        ['Trading Days', f'{len(active)}'],
        ['Daily Win Rate', f'{len(wins)/len(active)*100:.1f}%' if len(active) else 'N/A'],
        ['Avg Win Day', f'${wins.mean():,.0f}' if len(wins) else 'N/A'],
        ['Avg Loss Day', f'-${abs(losses.mean()):,.0f}' if len(losses) else 'N/A'],
        ['W/L Ratio', f'{wins.mean()/abs(losses.mean()):.2f}' if len(losses) else 'N/A'],
        ['Trailing DD', f'${cfg.funded.trailing_dd:,.0f}'],
        ['Dollar Loss Cap', f'${cfg.funded.dollar_loss_cap:,.0f}'],
    ]

    table = ax4.table(cellText=stats_data[1:], colLabels=stats_data[0],
                      loc='center', cellLoc='left',
                      colWidths=[0.5, 0.4])
    table.auto_set_font_size(False)
    table.set_fontsize(10)
    table.scale(1, 1.4)

    for (row, col), cell in table.get_celld().items():
        if row == 0:
            cell.set_text_props(fontweight='bold')
            cell.set_facecolor('#E3F2FD')
        elif row == 10:
            cell.set_facecolor('#F5F5F5')
        else:
            cell.set_facecolor('white')
        cell.set_edgecolor('#E0E0E0')

    ax4.set_title('Monte Carlo Summary Statistics', fontsize=12, fontweight='bold', pad=20)

    fig.savefig('chart_funded_mc.png', dpi=180, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)
    print("  Saved chart_funded_mc.png")

    return {'survival_rate': survival_rate, 'p5k': p5k, 'p10k': p10k,
            'avg_extraction': exs.mean(), 'median_extraction': np.median(exs)}


def fig5_timing_analysis(df):
    """Day-of-week, hour-of-day, and R:R distribution analysis."""
    fig = plt.figure(figsize=(18, 12))
    gs = GridSpec(2, 3, hspace=0.35, wspace=0.35, figure=fig)
    fig.suptitle('Timing & Distribution Analysis', fontsize=16, fontweight='bold', y=0.98)

    # Panel 1: Day of week performance
    ax1 = fig.add_subplot(gs[0, 0])
    dow_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri']
    dow_stats = df.groupby('dow').agg(
        avg_r=('total_r', 'mean'),
        count=('total_r', 'count'),
        wr=('total_r', lambda x: (x > 0).mean() * 100)
    )
    dow_colors = ['#4CAF50' if r > 0 else '#F44336' for r in dow_stats['avg_r']]
    bars = ax1.bar(range(5), dow_stats['avg_r'].reindex(range(5), fill_value=0),
                   color=dow_colors, alpha=0.8)
    ax1.set_xticks(range(5))
    ax1.set_xticklabels(dow_names)
    ax1.set_title('Avg R by Day of Week', fontsize=12, fontweight='bold')
    ax1.set_ylabel('Avg R')
    ax1.axhline(0, color='gray', linewidth=0.5, linestyle='--')
    for i, bar in enumerate(bars):
        if i in dow_stats.index:
            n = dow_stats.loc[i, 'count']
            wr = dow_stats.loc[i, 'wr']
            ax1.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.01,
                     f'n={n}\n{wr:.0f}%', ha='center', va='bottom', fontsize=8)
    ax1.grid(True, alpha=0.2, axis='y')

    # Panel 2: Hour of day performance
    ax2 = fig.add_subplot(gs[0, 1])
    hour_stats = df.groupby('hour').agg(
        avg_r=('total_r', 'mean'),
        count=('total_r', 'count')
    )
    hr_colors = ['#4CAF50' if r > 0 else '#F44336' for r in hour_stats['avg_r']]
    ax2.bar(hour_stats.index, hour_stats['avg_r'], color=hr_colors, alpha=0.8)
    ax2.set_title('Avg R by Hour of Day (ET)', fontsize=12, fontweight='bold')
    ax2.set_xlabel('Hour')
    ax2.set_ylabel('Avg R')
    ax2.axhline(0, color='gray', linewidth=0.5, linestyle='--')
    ax2.grid(True, alpha=0.2, axis='y')

    # Panel 3: R distribution histogram
    ax3 = fig.add_subplot(gs[0, 2])
    r_vals = df['total_r'].clip(-3, 5)
    ax3.hist(r_vals, bins=50, color='#2196F3', alpha=0.7, edgecolor='white')
    ax3.axvline(0, color='red', linewidth=1.5, linestyle='--')
    ax3.axvline(df['total_r'].mean(), color='green', linewidth=2,
                label=f'Mean: {df["total_r"].mean():.3f}R')
    ax3.set_title('Trade R Distribution', fontsize=12, fontweight='bold')
    ax3.set_xlabel('R-Multiple')
    ax3.set_ylabel('Frequency')
    ax3.legend(fontsize=9)
    ax3.grid(True, alpha=0.2)

    # Panel 4: Direction breakdown
    ax4 = fig.add_subplot(gs[1, 0])
    for direction in ['long', 'short']:
        grp = df[df['direction'] == direction]
        if len(grp) == 0:
            continue
        cum_r = grp.sort_values('entry_time')['total_r'].cumsum().values
        color = '#2196F3' if direction == 'long' else '#FF9800'
        ax4.plot(range(len(cum_r)), cum_r, label=f'{direction} (n={len(grp)}, {(grp["total_r"]>0).mean():.0%} WR)',
                 color=color, linewidth=1.5)
    ax4.set_title('Equity by Direction', fontsize=12, fontweight='bold')
    ax4.set_xlabel('Trade #')
    ax4.set_ylabel('Cumulative R')
    ax4.legend(fontsize=9)
    ax4.axhline(0, color='gray', linewidth=0.5, linestyle='--')
    ax4.grid(True, alpha=0.3)

    # Panel 5: Consecutive wins/losses
    ax5 = fig.add_subplot(gs[1, 1])
    results = (df['total_r'] > 0).astype(int).values
    streaks = []
    current = 1
    for i in range(1, len(results)):
        if results[i] == results[i-1]:
            current += 1
        else:
            streaks.append((current, 'W' if results[i-1] == 1 else 'L'))
            current = 1
    streaks.append((current, 'W' if results[-1] == 1 else 'L'))

    win_streaks = [s[0] for s in streaks if s[1] == 'W']
    loss_streaks = [s[0] for s in streaks if s[1] == 'L']

    max_streak = max(max(win_streaks) if win_streaks else 0,
                     max(loss_streaks) if loss_streaks else 0)
    bins = range(1, min(max_streak + 2, 16))
    ax5.hist(win_streaks, bins=bins, color='#4CAF50', alpha=0.6, label='Win Streaks', align='left')
    ax5.hist(loss_streaks, bins=bins, color='#F44336', alpha=0.6, label='Loss Streaks', align='left')
    ax5.set_title('Consecutive Win/Loss Streaks', fontsize=12, fontweight='bold')
    ax5.set_xlabel('Streak Length')
    ax5.set_ylabel('Frequency')
    ax5.legend(fontsize=9)
    ax5.grid(True, alpha=0.2)

    # Panel 6: Risk ticks distribution
    ax6 = fig.add_subplot(gs[1, 2])
    for model in sorted(df['model'].unique()):
        grp = df[df['model'] == model]
        ax6.hist(grp['risk_ticks'].clip(0, 200), bins=30, alpha=0.5,
                 color=MODEL_COLORS.get(model, 'gray'), label=model)
    ax6.set_title('Risk (Ticks) Distribution by Model', fontsize=12, fontweight='bold')
    ax6.set_xlabel('Risk Ticks')
    ax6.set_ylabel('Frequency')
    ax6.legend(fontsize=8, ncol=2)
    ax6.grid(True, alpha=0.2)

    fig.savefig('chart_timing_analysis.png', dpi=180, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)
    print("  Saved chart_timing_analysis.png")


def fig6_walkforward(df, daily_pnl, all_dates, cfg, trades):
    """Walk-forward analysis: yearly P10K progression and rolling metrics."""
    fig = plt.figure(figsize=(18, 10))
    gs = GridSpec(2, 2, hspace=0.35, wspace=0.3, figure=fig)
    fig.suptitle('Walk-Forward Validation & Rolling Performance',
                 fontsize=16, fontweight='bold', y=0.98)

    years = sorted(df['year'].unique())

    # Per-year MC
    year_results = {}
    for y in years:
        y_mask = [i for i, d in enumerate(all_dates) if pd.Timestamp(d).year == y]
        if len(y_mask) < 20:
            continue
        y_pnl = daily_pnl[y_mask]
        mc = run_monte_carlo(y_pnl, cfg, n_sims=10000, n_days=60, seed=142)
        year_results[y] = mc

    # Panel 1: P10K by year
    ax1 = fig.add_subplot(gs[0, 0])
    yr_labels = [str(y) for y in year_results.keys()]
    p10k_vals = [year_results[y]['p10k'] for y in year_results]
    survival_vals = [year_results[y]['survival_rate'] for y in year_results]
    x = range(len(yr_labels))
    ax1.bar(x, p10k_vals, color='#FF9800', alpha=0.8, label='P($10K+)', width=0.4, align='center')
    ax1.bar([i+0.4 for i in x], survival_vals, color='#4CAF50', alpha=0.6,
            label='Survival', width=0.4, align='center')
    ax1.axhline(75, color='red', linewidth=1.5, linestyle='--', alpha=0.7, label='75% Target')
    ax1.set_xticks([i+0.2 for i in x])
    ax1.set_xticklabels(yr_labels, fontsize=11)
    ax1.set_title('P($10K) and Survival by Year', fontsize=12, fontweight='bold')
    ax1.set_ylabel('Probability (%)')
    ax1.set_ylim(0, 105)
    ax1.legend(fontsize=9)
    for i, (p, s) in enumerate(zip(p10k_vals, survival_vals)):
        ax1.text(i, p + 1, f'{p:.0f}%', ha='center', va='bottom', fontsize=10, fontweight='bold')
    ax1.grid(True, alpha=0.2, axis='y')

    # Panel 2: Rolling 60-day win rate
    ax2 = fig.add_subplot(gs[0, 1])
    active_pnl = [(d, p) for d, p in zip(all_dates, daily_pnl) if p != 0]
    if len(active_pnl) > 60:
        roll_dates = []
        roll_wr = []
        roll_avg = []
        for i in range(60, len(active_pnl)):
            window = [p for _, p in active_pnl[i-60:i]]
            wins = sum(1 for p in window if p > 0)
            roll_dates.append(pd.Timestamp(active_pnl[i][0]))
            roll_wr.append(wins / 60 * 100)
            roll_avg.append(np.mean(window))

        ax2.plot(roll_dates, roll_wr, color='#2196F3', linewidth=1.2, alpha=0.8)
        ax2.axhline(50, color='gray', linewidth=1, linestyle='--', alpha=0.5)
        ax2.set_title('Rolling 60-Day Win Rate', fontsize=12, fontweight='bold')
        ax2.set_ylabel('Win Rate (%)')
        ax2.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
        ax2.xaxis.set_major_locator(mdates.MonthLocator(interval=4))
        ax2.grid(True, alpha=0.3)
        ax2.set_ylim(30, 80)

    # Panel 3: Rolling 60-day avg daily P&L
    ax3 = fig.add_subplot(gs[1, 0])
    if len(active_pnl) > 60:
        ax3.plot(roll_dates, roll_avg, color='#FF9800', linewidth=1.2, alpha=0.8)
        ax3.axhline(0, color='gray', linewidth=1, linestyle='--')
        ax3.fill_between(roll_dates, roll_avg, 0,
                         where=[a >= 0 for a in roll_avg], color='#4CAF50', alpha=0.2)
        ax3.fill_between(roll_dates, roll_avg, 0,
                         where=[a < 0 for a in roll_avg], color='#F44336', alpha=0.2)
        ax3.set_title('Rolling 60-Day Avg Daily P&L', fontsize=12, fontweight='bold')
        ax3.set_ylabel('Avg Daily P&L ($)')
        ax3.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))
        ax3.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
        ax3.xaxis.set_major_locator(mdates.MonthLocator(interval=4))
        ax3.grid(True, alpha=0.3)

    # Panel 4: Cumulative trades over time by model
    ax4 = fig.add_subplot(gs[1, 1])
    models = sorted(df['model'].unique())
    for model in models:
        grp = df[df['model'] == model].sort_values('entry_time')
        dates_m = pd.to_datetime(grp['entry_time'])
        ax4.plot(dates_m, range(1, len(grp)+1), label=model,
                 color=MODEL_COLORS.get(model, 'gray'), linewidth=1.2)
    ax4.set_title('Cumulative Trade Count by Model', fontsize=12, fontweight='bold')
    ax4.set_xlabel('Date')
    ax4.set_ylabel('Total Trades')
    ax4.legend(loc='upper left', fontsize=8, ncol=2)
    ax4.xaxis.set_major_formatter(mdates.DateFormatter('%b %Y'))
    ax4.xaxis.set_major_locator(mdates.MonthLocator(interval=4))
    ax4.grid(True, alpha=0.3)

    fig.savefig('chart_walkforward.png', dpi=180, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)
    print("  Saved chart_walkforward.png")
    return year_results


def print_summary(df, mc_stats, year_results, eval_stats=None):
    """Print text summary of all results."""
    n = len(df)
    w = df[df['total_r'] > 0]
    l = df[df['total_r'] <= 0]

    print("\n" + "=" * 70)
    print("  COMPREHENSIVE BACKTEST SUMMARY")
    print("=" * 70)
    print(f"  Total Trades:     {n}")
    print(f"  Trading Days:     {df['date'].nunique()}")
    print(f"  Trades/Day:       {n / df['date'].nunique():.1f}")
    print(f"  Win Rate:         {len(w)/n:.1%}")
    print(f"  Avg Win (R):      {w['total_r'].mean():.3f}")
    print(f"  Avg Loss (R):     {l['total_r'].mean():.3f}")
    print(f"  Expectancy (R):   {df['total_r'].mean():.4f}")
    gp = w['total_r'].sum()
    gl = abs(l['total_r'].sum())
    pf = gp / gl if gl > 0 else float('inf')
    print(f"  Profit Factor:    {pf:.2f}")
    print(f"  Total R:          {df['total_r'].sum():.1f}")

    if eval_stats:
        print(f"\n  --- Eval Pass MC (25K sims, $6K target, $3K trailing DD) ---")
        print(f"  Pass Rate:        {eval_stats['pass_rate']:.1f}%")
        print(f"  Avg Days to Pass: {eval_stats['avg_days']:.0f}")
        print(f"  Median Days:      {eval_stats['median_days']:.0f}")
        print(f"  Fast (P10):       {eval_stats['p10_days']:.0f} days")
        print(f"  Slow (P90):       {eval_stats['p90_days']:.0f} days")

    print(f"\n  --- Funded MC (25K sims, 60 days) ---")
    print(f"  Survival Rate:    {mc_stats['survival_rate']:.1f}%")
    print(f"  P($5K+):          {mc_stats['p5k']:.1f}%")
    print(f"  P($10K+):         {mc_stats['p10k']:.1f}%")
    print(f"  Avg Extraction:   ${mc_stats['avg_extraction']:,.0f}")
    print(f"  Median Extraction: ${mc_stats['median_extraction']:,.0f}")

    print(f"\n  --- Per-Year P10K ---")
    for y, res in sorted(year_results.items()):
        print(f"  {y}: P10K={res['p10k']:.1f}%  Surv={res['survival_rate']:.1f}%  "
              f"AvgExt=${res['avg_extraction']:,.0f}")

    print(f"\n  --- Per-Model ---")
    for model, grp in df.groupby('model'):
        mn = len(grp)
        mwr = (grp['total_r'] > 0).mean()
        mexp = grp['total_r'].mean()
        mtot = grp['total_r'].sum()
        print(f"  {model:15s}  {mn:4d} trades  {mwr:.0%} WR  "
              f"avg {mexp:+.3f}R  total {mtot:+.1f}R")
    print("=" * 70)


def main():
    cfg, trades, daily, raw = run_backtest()
    df = build_trade_df(trades)

    print(f"\nGenerating charts...")

    daily_pnl, all_dates = fig1_equity_and_drawdown(df, cfg, trades)
    fig2_model_breakdown(df)
    fig3_monthly_yearly(df, daily_pnl, all_dates, cfg, trades)
    mc_stats = fig4_funded_mc(daily_pnl, all_dates, cfg)
    fig5_timing_analysis(df)
    year_results = fig6_walkforward(df, daily_pnl, all_dates, cfg, trades)

    print("\nRunning eval pass simulation...")
    eval_stats = run_eval_monte_carlo(daily_pnl, cfg)
    print(f"  Eval pass rate: {eval_stats['pass_rate']:.1f}% "
          f"(avg {eval_stats['avg_days']:.0f} days)")

    print_summary(df, mc_stats, year_results, eval_stats)

    print(f"\nAll charts saved:")
    print(f"  1. chart_equity_drawdown.png  - Main equity curve + daily P&L + drawdown")
    print(f"  2. chart_model_breakdown.png  - Per-model equity, WR, expectancy, exits")
    print(f"  3. chart_monthly_yearly.png   - Monthly heatmap + yearly bars")
    print(f"  4. chart_funded_mc.png        - Monte Carlo paths + extraction distribution")
    print(f"  5. chart_timing_analysis.png  - DOW, hour, R distribution, streaks")
    print(f"  6. chart_walkforward.png      - Walk-forward P10K + rolling metrics")


if __name__ == '__main__':
    main()
