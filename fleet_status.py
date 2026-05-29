#!/usr/bin/env python3
"""Fleet status reader: aggregate per-account P&L from each account's log.

Reads every account's ``decisions.jsonl`` (under its isolated FLEET_STATE_DIR)
and summarizes today's realized P&L, R, trade count, and halt state — the same
``trade_closed`` records the executor writes (fields: pnl_usd, total_r, model,
timestamp). Read-only: never touches broker connections or live state.

Usage:
  python3 fleet_status.py                    # uses fleet_config.json, today
  python3 fleet_status.py --config my.json
  python3 fleet_status.py --date 2026-05-28
  python3 fleet_status.py --json             # machine-readable output
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from fleet_config import FleetAccount, load_fleet


def read_account_pnl(decision_log: str | Path,
                     date_str: str | None = None) -> dict:
    """Aggregate one account's decisions.jsonl into a P&L summary.

    Mirrors the executor's own reconstruction logic: sums ``pnl_usd`` /
    ``total_r`` over ``trade_closed`` records (optionally filtered to ``date_str``
    by timestamp prefix), counts trades, tracks the latest ``daily_pnl`` snapshot,
    and detects ``bot_halted``.
    """
    log_path = Path(decision_log)
    summary = {
        'pnl_usd': 0.0,
        'total_r': 0.0,
        'trades': 0,
        'wins': 0,
        'losses': 0,
        'halted': False,
        'last_daily_pnl': None,
        'last_timestamp': None,
        'by_model': {},
        'log_exists': log_path.exists(),
    }
    if not log_path.exists():
        return summary

    try:
        with open(log_path, 'r') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                ts = rec.get('timestamp', '')
                if date_str and not ts.startswith(date_str):
                    continue
                action = rec.get('action')
                if action == 'trade_closed':
                    pnl = float(rec.get('pnl_usd', 0.0) or 0.0)
                    r = float(rec.get('total_r', 0.0) or 0.0)
                    summary['pnl_usd'] += pnl
                    summary['total_r'] += r
                    summary['trades'] += 1
                    if r > 0:
                        summary['wins'] += 1
                    elif r < 0:
                        summary['losses'] += 1
                    model = rec.get('model', '')
                    if model:
                        m = summary['by_model'].setdefault(
                            model, {'trades': 0, 'pnl_usd': 0.0})
                        m['trades'] += 1
                        m['pnl_usd'] += pnl
                    if 'daily_pnl' in rec:
                        summary['last_daily_pnl'] = rec['daily_pnl']
                    summary['last_timestamp'] = ts or summary['last_timestamp']
                elif action == 'bot_halted':
                    summary['halted'] = True
                    summary['last_timestamp'] = ts or summary['last_timestamp']
    except OSError:
        return summary

    summary['pnl_usd'] = round(summary['pnl_usd'], 2)
    summary['total_r'] = round(summary['total_r'], 3)
    for m in summary['by_model'].values():
        m['pnl_usd'] = round(m['pnl_usd'], 2)
    return summary


def fleet_status(accounts: list[FleetAccount],
                 date_str: str | None = None,
                 project_root: Path | None = None) -> dict:
    """Build an aggregated status dict over the whole fleet."""
    root = project_root or Path('.')
    per_account = []
    total_pnl = 0.0
    total_r = 0.0
    total_trades = 0
    for a in accounts:
        log_path = root / a.decision_log
        s = read_account_pnl(log_path, date_str)
        s['name'] = a.name
        s['broker'] = a.broker
        s['shadow'] = a.shadow
        s['state_dir'] = a.state_dir
        per_account.append(s)
        total_pnl += s['pnl_usd']
        total_r += s['total_r']
        total_trades += s['trades']

    return {
        'date': date_str,
        'accounts': per_account,
        'totals': {
            'pnl_usd': round(total_pnl, 2),
            'total_r': round(total_r, 3),
            'trades': total_trades,
            'accounts': len(per_account),
        },
    }


def format_status(status: dict) -> str:
    """Human-readable table of the fleet status dict."""
    lines = []
    date = status.get('date') or 'all-time'
    lines.append(f"Fleet status ({date})")
    lines.append('=' * 74)
    lines.append(f"{'account':<20}{'broker':<9}{'trades':>7}{'R':>9}"
                 f"{'P&L $':>12}{'halt':>7}")
    lines.append('-' * 74)
    for a in status['accounts']:
        halt = 'HALT' if a['halted'] else ('--' if a['log_exists'] else 'n/a')
        sh = '*' if a.get('shadow') else ' '
        lines.append(
            f"{a['name'][:19]+sh:<20}{a['broker']:<9}{a['trades']:>7}"
            f"{a['total_r']:>9.2f}{a['pnl_usd']:>12,.2f}{halt:>7}")
    lines.append('-' * 74)
    t = status['totals']
    lines.append(f"{'TOTAL ('+str(t['accounts'])+' acct)':<29}{t['trades']:>7}"
                 f"{t['total_r']:>9.2f}{t['pnl_usd']:>12,.2f}")
    return '\n'.join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description='Fleet P&L status reader')
    p.add_argument('--config', default='fleet_config.json',
                   help='fleet manifest JSON (default: fleet_config.json)')
    p.add_argument('--date', default=datetime.now().strftime('%Y-%m-%d'),
                   help="date filter YYYY-MM-DD (default: today; 'all' for all)")
    p.add_argument('--json', action='store_true',
                   help='emit machine-readable JSON instead of a table')
    args = p.parse_args(argv)

    date_str = None if args.date == 'all' else args.date
    accounts = load_fleet(args.config)
    project_root = Path(__file__).resolve().parent
    status = fleet_status(accounts, date_str, project_root)

    if args.json:
        print(json.dumps(status, indent=2))
    else:
        print(format_status(status))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
