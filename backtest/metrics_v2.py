"""Metrics for multi-model trades — adds per-model breakdown."""
from __future__ import annotations
import pandas as pd
import numpy as np
from backtest.engine_v2 import Trade  # noqa: F401
from config import Config


class MetricsV2:
    def __init__(self, trades: list[Trade], cfg: Config):
        self.trades = trades
        self.cfg = cfg
        self.df = self._build()

    def _build(self):
        if not self.trades:
            return pd.DataFrame()
        return pd.DataFrame([{
            'entry_time': t.entry_time, 'exit_time': t.exit_time,
            'direction': t.direction, 'model': t.model, 'tag': t.tag,
            'entry': t.entry_price, 'exit': t.exit_price,
            'stop': t.stop_price, 'target': t.target_price,
            'reason': t.exit_reason, 'risk_ticks': t.risk_ticks,
            'total_r': t.total_r, 'moved_be': t.moved_be,
            'partial_taken': t.partial_taken,
        } for t in self.trades])

    def print_report(self):
        if self.df.empty:
            print("No trades.")
            return

        df = self.df
        n = len(df)
        w = df[df['total_r'] > 0]
        losers = df[df['total_r'] <= 0]

        wr = len(w) / n
        avg_w = w['total_r'].mean() if len(w) else 0
        avg_l = losers['total_r'].mean() if len(losers) else 0
        exp = df['total_r'].mean()

        gp = w['total_r'].sum() if len(w) else 0
        gl = abs(losers['total_r'].sum()) if len(losers) else 0
        pf = gp / gl if gl > 0 else float('inf')

        cum = df['total_r'].cumsum()
        dd = (cum - cum.expanding().max()).min()

        risk_usd = self.cfg.account_size * self.cfg.risk.risk_per_trade_pct / 100
        pnl = df['total_r'].sum() * risk_usd

        days = max(df['entry_time'].dt.date.nunique(), 1)
        tpd = n / days

        print("\n" + "=" * 65)
        print("  MULTI-MODEL NQ STRATEGY — BACKTEST RESULTS")
        print("=" * 65)

        rows = [
            ['Total Trades',       n],
            ['Trading Days',       days],
            ['Trades/Day',         f'{tpd:.1f}'],
            ['Win Rate',           f'{wr:.1%}'],
            ['Avg Win (R)',        f'{avg_w:.2f}'],
            ['Avg Loss (R)',       f'{avg_l:.2f}'],
            ['Expectancy (R)',     f'{exp:.3f}'],
            ['Profit Factor',      f'{pf:.2f}'],
            ['Total R',            f'{df["total_r"].sum():.1f}'],
            ['Max Drawdown (R)',   f'{dd:.1f}'],
            ['Avg Risk (ticks)',   f'{df["risk_ticks"].mean():.1f}'],
            ['Total PnL ($)',      f'${pnl:,.0f}'],
        ]

        try:
            from tabulate import tabulate
            print(tabulate(rows, headers=['Metric', 'Value'], tablefmt='simple'))
        except ImportError:
            for r in rows:
                print(f"  {r[0]:25s}  {r[1]}")

        # per model breakdown
        print("\n--- By Model ---")
        for model, grp in df.groupby('model'):
            mn = len(grp)
            mwr = (grp['total_r'] > 0).mean()
            mexp = grp['total_r'].mean()
            mtot = grp['total_r'].sum()
            print(f"  {model:6s}  {mn:3d} trades  {mwr:.0%} WR  "
                  f"avg {mexp:+.2f}R  total {mtot:+.1f}R")

        # per tag
        print("\n--- By Setup Type ---")
        for tag, grp in df.groupby('tag'):
            tn = len(grp)
            twr = (grp['total_r'] > 0).mean()
            texp = grp['total_r'].mean()
            print(f"  {tag:25s}  {tn:3d} trades  {twr:.0%} WR  avg {texp:+.2f}R")

        # exit reasons
        print("\n--- By Exit ---")
        for reason, grp in df.groupby('reason'):
            print(f"  {reason:18s}  {len(grp):3d} trades  avg {grp['total_r'].mean():+.2f}R")

        # monthly
        print("\n--- Monthly ---")
        df['month'] = pd.to_datetime(df['entry_time']).dt.to_period('M')
        for period, grp in df.groupby('month'):
            mwr = (grp['total_r'] > 0).mean()
            print(f"  {period}  {grp['total_r'].sum():+6.1f}R  "
                  f"{len(grp):3d} trades  {mwr:.0%} WR")

        print("=" * 65)

    def funded_sim(self, contracts: int = 6, target_usd: float = 3000,
                   max_dd_usd: float = 2500, window_days: int = 20,
                   risk_per_contract_ticks: float = None,
                   trailing_dd: bool = True, adaptive: bool = False,
                   ramp: bool = False, frontload: bool = False,
                   twophase: bool = False, cautious: bool = False,
                   consistency_pct: float = 0.0,
                   daily_loss_limit_usd: float = 0.0):
        if self.df.empty:
            return {'pass_rate': 0, 'blow_rate': 0, 'passes': 0, 'blows': 0, 'total': 0}

        df = self.df.copy()
        df['date'] = pd.to_datetime(df['entry_time']).dt.date

        mnq_tick_val = 0.50

        if risk_per_contract_ticks is None:
            risk_per_contract_ticks = df['risk_ticks'].median()

        daily_r = df.groupby('date')['total_r'].sum().reset_index()
        daily_r.columns = ['date', 'r']
        daily_r = daily_r.sort_values('date').reset_index(drop=True)

        r_vals = daily_r['r'].values
        n = len(r_vals)

        passes = blows = active = 0
        consistency_fails = 0
        daily_limit_blows = 0

        for start in range(n):
            eq = 0.0
            peak = 0.0
            passed = blown = False
            day_pnls = []
            for j in range(start, min(start + window_days, n)):
                if cautious:
                    c = self._cautious_size(eq, peak, contracts,
                                            target_usd, max_dd_usd,
                                            day_pnls)
                elif twophase:
                    c = self._twophase_size(eq, peak, contracts,
                                            target_usd, max_dd_usd,
                                            day_pnls)
                elif frontload:
                    c = self._frontload_size(eq, peak, contracts,
                                             target_usd, max_dd_usd,
                                             day_pnls)
                elif ramp:
                    c = self._ramp_size(eq, peak, contracts,
                                        target_usd, max_dd_usd,
                                        risk_per_contract_ticks, mnq_tick_val,
                                        day_pnls)
                elif adaptive:
                    c = self._adaptive_size(eq, peak, contracts,
                                            target_usd, max_dd_usd,
                                            risk_per_contract_ticks, mnq_tick_val,
                                            day_pnls)
                else:
                    c = contracts
                day_usd = r_vals[j] * risk_per_contract_ticks * c * mnq_tick_val
                if daily_loss_limit_usd > 0 and day_usd < -daily_loss_limit_usd:
                    blown = True
                    daily_limit_blows += 1
                    break
                eq += day_usd
                day_pnls.append(day_usd)
                if eq > peak:
                    peak = eq
                if trailing_dd:
                    dd = peak - eq
                else:
                    dd = -eq if eq < 0 else 0
                if eq >= target_usd:
                    if consistency_pct > 0 and eq > 0:
                        max_day = max(day_pnls)
                        if max_day > consistency_pct * eq:
                            consistency_fails += 1
                            break
                    passed = True
                    break
                if dd >= max_dd_usd:
                    blown = True
                    break
            if passed:
                passes += 1
            elif blown:
                blows += 1
            else:
                active += 1

        total = passes + blows + active
        risk_usd = risk_per_contract_ticks * contracts * mnq_tick_val
        return {
            'pass_rate': passes / total * 100 if total else 0,
            'blow_rate': blows / total * 100 if total else 0,
            'passes': passes, 'blows': blows, 'active': active,
            'total': total, 'risk_usd': risk_usd, 'contracts': contracts,
            'consistency_fails': consistency_fails,
            'daily_limit_blows': daily_limit_blows,
        }

    @staticmethod
    def _adaptive_size(eq, peak, base_contracts, target, max_dd,
                       risk_ticks, tick_val, day_results=None):
        min_c = max(2, base_contracts // 3)
        half_c = max(min_c, int(base_contracts * 0.5))
        trail_dd = peak - eq
        dd_pct = trail_dd / max_dd if max_dd > 0 else 1.0
        eq_pct = eq / target if target > 0 else 0.0

        if dd_pct > 0.55:
            return min_c
        if dd_pct > 0.35:
            return half_c

        if day_results and len(day_results) >= 2:
            if all(r < 0 for r in day_results[-2:]):
                return half_c

        if eq_pct >= 0.80:
            return min_c
        if eq_pct >= 0.60:
            return half_c

        if day_results and len(day_results) <= 2 and eq <= 0:
            return half_c

        return base_contracts

    @staticmethod
    def _ramp_size(eq, peak, max_contracts, target, max_dd,
                   risk_ticks, tick_val, day_results=None):
        min_c = max(2, max_contracts // 4)
        third_c = max(min_c, max_contracts // 3)
        half_c = max(min_c, max_contracts // 2)
        trail_dd = peak - eq
        dd_pct = trail_dd / max_dd if max_dd > 0 else 1.0

        if dd_pct > 0.50:
            return min_c
        if dd_pct > 0.30:
            return third_c

        if day_results and len(day_results) >= 2:
            if all(r < 0 for r in day_results[-2:]):
                return third_c

        progress = eq / target if target > 0 else 0.0

        if progress >= 0.85:
            return min_c
        if progress >= 0.65:
            return third_c

        if progress < 0:
            return half_c

        if progress < 0.08:
            return half_c
        if progress < 0.20:
            return max(half_c, int(max_contracts * 0.7))
        if progress < 0.40:
            return max(half_c, int(max_contracts * 0.85))
        return max_contracts

    @staticmethod
    def _frontload_size(eq, peak, base_contracts, target, max_dd,
                        day_results=None):
        min_c = max(2, base_contracts // 3)
        half_c = max(min_c, int(base_contracts * 0.5))
        three_q = max(half_c, int(base_contracts * 0.75))
        trail_dd = peak - eq
        dd_pct = trail_dd / max_dd if max_dd > 0 else 1.0
        progress = eq / target if target > 0 else 0.0

        if dd_pct > 0.55:
            return min_c
        if dd_pct > 0.35:
            return half_c

        if day_results and len(day_results) >= 3:
            if all(r < 0 for r in day_results[-3:]):
                return half_c

        if progress >= 0.95:
            return min_c

        return base_contracts

    @staticmethod
    def _twophase_size(eq, peak, base_contracts, target, max_dd,
                       day_results=None):
        min_c = max(2, base_contracts // 3)
        half_c = max(min_c, int(base_contracts * 0.5))
        high_c = min(base_contracts + 4, int(base_contracts * 1.5))
        trail_dd = peak - eq
        dd_pct = trail_dd / max_dd if max_dd > 0 else 1.0
        progress = eq / target if target > 0 else 0.0
        day_num = len(day_results) if day_results else 0

        if dd_pct > 0.55:
            return min_c
        if dd_pct > 0.35:
            return half_c

        if progress >= 0.92:
            return min_c
        if progress >= 0.70:
            return half_c

        if day_num <= 5:
            if dd_pct < 0.15:
                return high_c
            return base_contracts

        if day_results and len(day_results) >= 2:
            if all(r < 0 for r in day_results[-2:]):
                return half_c

        return base_contracts

    @staticmethod
    def _cautious_size(eq, peak, base_contracts, target, max_dd,
                       day_results=None):
        min_c = max(2, base_contracts // 3)
        half_c = max(min_c, int(base_contracts * 0.5))
        two_third = max(min_c, int(base_contracts * 0.67))
        trail_dd = peak - eq
        dd_pct = trail_dd / max_dd if max_dd > 0 else 1.0
        progress = eq / target if target > 0 else 0.0

        if dd_pct > 0.50:
            return min_c
        if dd_pct > 0.30:
            return half_c

        if progress >= 0.85:
            return min_c
        if progress >= 0.65:
            return half_c

        if day_results and day_results[-1] < 0:
            return two_third

        if day_results and len(day_results) >= 2:
            if all(r < 0 for r in day_results[-2:]):
                return half_c

        return base_contracts

    def funded_phase_sim(self, contracts: int = 6,
                         max_dd_usd: float = 2000,
                         daily_loss_limit_usd: float = 1200,
                         consistency_pct: float = 0.40,
                         payout_cycle_days: int = 2,
                         min_payout: float = 500,
                         payout_caps: list | None = None,
                         profit_split: float = 0.90,
                         sim_days: int = 60,
                         min_buffer: float = 800):
        if self.df.empty:
            return {}

        if payout_caps is None:
            payout_caps = [2000, 2000, 4000, 4000, 6000]

        df = self.df.copy()
        df['date'] = pd.to_datetime(df['entry_time']).dt.date
        mnq_tick_val = 0.50
        risk_per_contract_ticks = df['risk_ticks'].median()

        daily_r = df.groupby('date')['total_r'].sum().reset_index()
        daily_r.columns = ['date', 'r']
        daily_r = daily_r.sort_values('date').reset_index(drop=True)
        r_vals = daily_r['r'].values
        n = len(r_vals)

        all_payouts = []
        all_num_payouts = []
        blown_count = 0
        survived_count = 0

        num_sims = min(n - sim_days + 1, n)
        if num_sims <= 0:
            return {}

        for start in range(num_sims):
            bal = 53000.0
            floor = 51000.0
            cycle_pnls = []
            total_paid = 0.0
            payout_num = 0
            blown = False

            for day_i in range(sim_days):
                idx = start + day_i
                if idx >= n:
                    break

                day_usd = r_vals[idx] * risk_per_contract_ticks * contracts * mnq_tick_val

                if daily_loss_limit_usd > 0 and day_usd < -daily_loss_limit_usd:
                    blown = True
                    break

                bal += day_usd
                cycle_pnls.append(day_usd)

                if bal <= floor:
                    blown = True
                    break

                if (day_i + 1) % payout_cycle_days == 0 and len(cycle_pnls) > 0:
                    cycle_profit = sum(cycle_pnls)
                    buffer = bal - floor

                    if cycle_profit > 0 and buffer > min_buffer:
                        cons_ok = True
                        if consistency_pct > 0 and cycle_profit > 0:
                            max_day = max(cycle_pnls)
                            if max_day > consistency_pct * cycle_profit:
                                cons_ok = False

                        if cons_ok:
                            max_gross = buffer - min_buffer
                            cap_idx = min(payout_num, len(payout_caps) - 1)
                            cap = payout_caps[cap_idx]
                            gross = min(cycle_profit, max_gross)
                            payout = min(gross * profit_split, cap)
                            payout = max(payout, 0)

                            if payout >= min_payout:
                                total_paid += payout
                                payout_num += 1
                                bal -= payout / profit_split

                    cycle_pnls = []

            if blown:
                blown_count += 1
            else:
                survived_count += 1
            all_payouts.append(total_paid)
            all_num_payouts.append(payout_num)

        payouts_arr = np.array(all_payouts)
        num_payouts_arr = np.array(all_num_payouts)
        return {
            'sim_days': sim_days,
            'num_sims': len(all_payouts),
            'blown_pct': blown_count / len(all_payouts) * 100,
            'survived_pct': survived_count / len(all_payouts) * 100,
            'avg_payout': payouts_arr.mean(),
            'median_payout': float(np.median(payouts_arr)),
            'p25_payout': float(np.percentile(payouts_arr, 25)),
            'p75_payout': float(np.percentile(payouts_arr, 75)),
            'p90_payout': float(np.percentile(payouts_arr, 90)),
            'max_payout': payouts_arr.max(),
            'min_payout': payouts_arr.min(),
            'pct_with_payout': (payouts_arr > 0).mean() * 100,
            'avg_num_payouts': num_payouts_arr.mean(),
        }

    def print_funded_projection(self, eval_contracts: int = 20):
        print(f"\n{'='*80}")
        print("  TOPSTEP 50K — FUNDED PAYOUT PROJECTION")
        print(f"  Eval: {eval_contracts} MNQ | TopStep 50K Express")
        print(f"  Payout cap: $2K max, ≤50% balance, 5 green days ($150+)")
        print(f"{'='*80}")

        print(f"\n  --- Per-account funded performance (65 trading days, biweekly payouts) ---")
        print(f"  {'MNQ':>4s}  {'Surv':>5s}  {'Avg$':>7s}  {'Med$':>7s}  {'P75$':>7s}  {'P90$':>7s}  {'AvgN':>5s}")
        best_c = 0
        best_ev = 0
        for c in [5, 6, 7, 8, 9]:
            r = self.funded_phase_sim(contracts=c, sim_days=65,
                                      payout_cycle_days=10, min_buffer=1000)
            surv = r['survived_pct'] / 100
            ev = r['avg_payout'] * surv
            print(f"  {c:4d}  {r['survived_pct']:4.0f}%  "
                  f"${r['avg_payout']:6,.0f}  ${r['median_payout']:6,.0f}  "
                  f"${r['p75_payout']:6,.0f}  ${r['p90_payout']:6,.0f}  "
                  f"{r['avg_num_payouts']:5.1f}")
            if r['avg_payout'] > best_ev:
                best_ev = r['avg_payout']
                best_c = c

        r = self.funded_phase_sim(contracts=best_c, sim_days=65,
                                   payout_cycle_days=10, min_buffer=1000)
        eval_pass = 0.75
        eval_cost = 78

        print(f"\n  --- SUMMER PLAN (June → August, {best_c} MNQ funded) ---")
        for n_accts in [1, 2, 3, 5]:
            passed = round(n_accts * eval_pass)
            total_eval_cost = n_accts * eval_cost
            failed = n_accts - passed
            reset_cost = failed * 45
            total_cost = total_eval_cost + reset_cost

            avg_total = r['avg_payout'] * passed
            good_total = r['p75_payout'] * passed
            great_total = r['p90_payout'] * passed

            print(f"  Buy {n_accts} eval(s) → ~{passed} pass")
            print(f"    Cost: ${total_cost} (evals ${total_eval_cost} + resets ${reset_cost})")
            print(f"    Payouts: avg ${avg_total:,.0f}  good ${good_total:,.0f}  great ${great_total:,.0f}")
            print(f"    Net profit: avg ${avg_total - total_cost:,.0f}  good ${good_total - total_cost:,.0f}")

        print(f"{'='*80}")

    def plot(self, path: str | None = None):
        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
        except ImportError:
            print("matplotlib not installed")
            return

        fig, axes = plt.subplots(3, 1, figsize=(14, 10), tight_layout=True)
        eq = self.df['total_r'].cumsum()

        axes[0].plot(eq.values, lw=1.5)
        axes[0].set_title('Equity Curve (R)')
        axes[0].axhline(0, color='grey', ls='--', alpha=.5)
        axes[0].grid(True, alpha=.3)

        # color by model
        colors = {'OR': 'blue', 'VWAP': 'green', 'SWEEP': 'red'}
        for model, grp in self.df.groupby('model'):
            vals = grp['total_r'].values
            idxs = grp.index.values
            c = colors.get(model, 'grey')
            axes[1].bar(idxs, vals, color=c, alpha=0.7, label=model)
        axes[1].set_title('Per-Trade R by Model')
        axes[1].axhline(0, color='red', ls='--')
        axes[1].legend()
        axes[1].grid(True, alpha=.3)

        dd = eq - eq.expanding().max()
        axes[2].fill_between(range(len(dd)), dd.values, 0, color='red', alpha=.3)
        axes[2].set_title('Drawdown (R)')
        axes[2].grid(True, alpha=.3)

        if path:
            fig.savefig(path, dpi=150)
            print(f"Saved → {path}")
        else:
            plt.show()
        plt.close(fig)
