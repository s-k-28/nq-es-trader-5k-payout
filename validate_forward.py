#!/usr/bin/env python3
"""Forward-validation gate: does the LIVE/paper bot track the backtest edge?

The honest substitute for a profit guarantee. Run the (fixed) bot in shadow/paper,
then point this at its decision log(s). It computes realized live stats, compares
them to the out-of-sample backtest baseline, and returns a GO / NO-GO / INSUFFICIENT
verdict. Scale to a fleet only on GO.

Usage:
  python3 validate_forward.py --log live/state/decisions.jsonl
  python3 validate_forward.py --fleet            # aggregate live/fleet/*/decisions.jsonl
  python3 validate_forward.py --log <path> --json
"""
from __future__ import annotations
import argparse
import glob
import json
import os
from collections import defaultdict

# Out-of-sample backtest baseline (3yr walk-forward, current settings):
# WR 44.3%, expectancy +0.220R, PF 1.61. The live edge must track this.
DEFAULT_BASELINE = {'win_rate': 44.3, 'expectancy_r': 0.220, 'profit_factor': 1.61}

# GO/NO-GO thresholds (conservative; tune with experience).
MIN_TRADES = 40            # statistical minimum
DAY_LOSS_CAP = 1000.0      # daily loss must never exceed this (funded rule)
EXPECTANCY_FLOOR_FRAC = 0.5  # live expectancy >= 50% of backtest (parity-drag allowance)
WR_TOLERANCE_PTS = 8.0     # live WR within 8 points of backtest


def _iter_trade_closed(log_path: str):
    if not os.path.exists(log_path):
        return
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            if rec.get('action') == 'trade_closed':
                yield rec


def compute_live_stats(log_paths: list[str], day_cap: float = DAY_LOSS_CAP) -> dict:
    """Realized stats from one or more decision logs."""
    rs, pnls, dates = [], [], []
    daily = defaultdict(float)
    for path in log_paths:
        for rec in _iter_trade_closed(path):
            r = rec.get('total_r')
            pnl = rec.get('pnl_usd', 0.0)
            if r is None:
                continue
            rs.append(float(r))
            pnls.append(float(pnl))
            d = (rec.get('timestamp') or '')[:10]
            daily[d] += float(pnl)
    n = len(rs)
    if n == 0:
        return {'n_trades': 0}
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r < 0]
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    day_vals = list(daily.values())
    return {
        'n_trades': n,
        'win_rate': len(wins) / n * 100,
        'expectancy_r': sum(rs) / n,
        'avg_win_r': sum(wins) / len(wins) if wins else 0.0,
        'avg_loss_r': sum(losses) / len(losses) if losses else 0.0,
        'profit_factor': gross_win / gross_loss if gross_loss > 0 else float('inf'),
        'total_usd': sum(pnls),
        'trading_days': len(day_vals),
        'max_daily_loss': min(day_vals) if day_vals else 0.0,
        'mean_daily_usd': sum(day_vals) / len(day_vals) if day_vals else 0.0,
        'monthly_estimate_usd': (sum(day_vals) / len(day_vals) * 21) if day_vals else 0.0,
        'cap_breach_days': sum(1 for v in day_vals if v <= -day_cap - 0.01),
    }


def gate(stats: dict, baseline: dict = None, *, min_trades: int = MIN_TRADES,
         day_cap: float = DAY_LOSS_CAP,
         expectancy_floor_frac: float = EXPECTANCY_FLOOR_FRAC,
         wr_tol_pts: float = WR_TOLERANCE_PTS) -> dict:
    """Return {'verdict': GO|NO_GO|INSUFFICIENT_DATA, 'checks': [...]}."""
    baseline = baseline or DEFAULT_BASELINE
    n = stats.get('n_trades', 0)
    if n < min_trades:
        return {'verdict': 'INSUFFICIENT_DATA',
                'reason': f'{n} trades < {min_trades} minimum', 'checks': []}

    exp_floor = baseline['expectancy_r'] * expectancy_floor_frac
    wr_floor = baseline['win_rate'] - wr_tol_pts
    checks = [
        ('daily_cap_held', stats['max_daily_loss'] > -day_cap - 0.01 and
                           stats['cap_breach_days'] == 0,
         f"max daily loss ${stats['max_daily_loss']:,.0f} vs cap -${day_cap:,.0f}, "
         f"{stats['cap_breach_days']} breach-days"),
        ('expectancy', stats['expectancy_r'] >= exp_floor,
         f"live {stats['expectancy_r']:+.3f}R vs floor {exp_floor:+.3f}R "
         f"(baseline {baseline['expectancy_r']:+.3f}R)"),
        ('win_rate', stats['win_rate'] >= wr_floor,
         f"live {stats['win_rate']:.1f}% vs floor {wr_floor:.1f}% "
         f"(baseline {baseline['win_rate']:.1f}%)"),
    ]
    verdict = 'GO' if all(ok for _, ok, _ in checks) else 'NO_GO'
    return {'verdict': verdict, 'checks': checks}


def _fleet_logs() -> list[str]:
    return sorted(glob.glob('live/fleet/*/decisions.jsonl'))


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description='Forward-validation GO/NO-GO gate')
    p.add_argument('--log', default='live/state/decisions.jsonl',
                   help='decision log path (default: live/state/decisions.jsonl)')
    p.add_argument('--fleet', action='store_true',
                   help='aggregate all live/fleet/*/decisions.jsonl logs')
    p.add_argument('--json', action='store_true')
    args = p.parse_args(argv)

    paths = _fleet_logs() if args.fleet else [args.log]
    stats = compute_live_stats(paths)
    result = gate(stats)

    if args.json:
        print(json.dumps({'stats': stats, 'gate': result}, indent=2, default=str))
        return 0

    print("=" * 64)
    print("  FORWARD-VALIDATION GATE")
    print("=" * 64)
    if stats.get('n_trades', 0) == 0:
        print("  No closed trades found in:", paths)
        return 0
    print(f"  trades={stats['n_trades']}  days={stats['trading_days']}  "
          f"WR={stats['win_rate']:.1f}%  expectancy={stats['expectancy_r']:+.3f}R  "
          f"PF={stats['profit_factor']:.2f}")
    print(f"  total=${stats['total_usd']:,.0f}  ~${stats['monthly_estimate_usd']:,.0f}/mo  "
          f"max daily loss=${stats['max_daily_loss']:,.0f}")
    print(f"\n  VERDICT: {result['verdict']}")
    if result.get('reason'):
        print(f"    {result['reason']}")
    for name, ok, detail in result.get('checks', []):
        print(f"    [{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    print("=" * 64)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
