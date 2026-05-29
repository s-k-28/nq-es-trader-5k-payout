"""Tests for the forward-validation GO/NO-GO gate."""
from __future__ import annotations
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from validate_forward import compute_live_stats, gate


def _write_log(tmp_path, trades):
    """trades: list of (total_r, pnl_usd, date 'YYYY-MM-DD')."""
    p = tmp_path / 'decisions.jsonl'
    with open(p, 'w') as f:
        for r, pnl, d in trades:
            f.write(json.dumps({
                'action': 'trade_closed', 'total_r': r, 'pnl_usd': pnl,
                'timestamp': f'{d}T10:00:00-05:00',
            }) + '\n')
        # noise lines that must be ignored
        f.write(json.dumps({'action': 'skip_signal', 'reason': 'x'}) + '\n')
        f.write('not json\n')
    return str(p)


def _good_trades(n=40):
    # ~45% WR, ~+0.23R expectancy, one trade per distinct day (no cap breach).
    out = []
    for i in range(n):
        d = f'2026-03-{(i % 28) + 1:02d}'
        if i % 40 < 18:   # 18 wins
            out.append((1.3, 130.0, d))
        else:             # 22 losses
            out.append((-0.65, -65.0, d))
    return out


def test_insufficient_data(tmp_path):
    log = _write_log(tmp_path, _good_trades(10))
    out = gate(compute_live_stats([log]))
    assert out['verdict'] == 'INSUFFICIENT_DATA'


def test_go_when_tracking_baseline(tmp_path):
    log = _write_log(tmp_path, _good_trades(40))
    stats = compute_live_stats([log])
    assert stats['n_trades'] == 40
    assert 44 <= stats['win_rate'] <= 46
    assert stats['expectancy_r'] > 0.11
    assert gate(stats)['verdict'] == 'GO'


def test_no_go_low_expectancy(tmp_path):
    # 40 trades, all small losses spread across days (no cap breach) -> NO_GO.
    trades = [(-0.2, -20.0, f'2026-03-{(i % 28) + 1:02d}') for i in range(40)]
    out = gate(compute_live_stats([_write_log(tmp_path, trades)]))
    assert out['verdict'] == 'NO_GO'
    assert any(name == 'expectancy' and not ok for name, ok, _ in out['checks'])


def test_no_go_on_daily_cap_breach(tmp_path):
    # 40 winners overall, but 20 losers stacked on ONE day = -$1200 (> $1000 cap).
    trades = [(1.3, 130.0, f'2026-03-{i + 1:02d}') for i in range(20)]
    trades += [(-0.6, -60.0, '2026-03-25') for _ in range(20)]  # -$1200 same day
    stats = compute_live_stats([_write_log(tmp_path, trades)])
    assert stats['cap_breach_days'] == 1
    out = gate(stats)
    assert out['verdict'] == 'NO_GO'
    assert any(name == 'daily_cap_held' and not ok for name, ok, _ in out['checks'])


def test_aggregates_multiple_logs(tmp_path):
    a = tmp_path / 'a'; b = tmp_path / 'b'
    a.mkdir(); b.mkdir()
    la = _write_log(a, _good_trades(40)[:20])
    lb = _write_log(b, _good_trades(40)[20:])
    stats = compute_live_stats([la, lb])
    assert stats['n_trades'] == 40
