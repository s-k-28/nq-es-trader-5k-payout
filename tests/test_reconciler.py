"""Tests for PnLReconciler — broker-cash-authoritative daily P&L, fail-closed.

The daily-loss cap is the funded account's primary survival control, but the
executor's `daily_pnl_usd` is computed from bar-close estimates that bias toward
UNDERSTATING loss. The reconciler cross-checks it against the broker's actual
NetLiquidation balance delta and takes the more-conservative (more-negative)
value, so the cap cannot be silently breached.
"""
from __future__ import annotations
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from live.reconciler import PnLReconciler


def _broker(net_liq):
    b = MagicMock()
    b.get_account_info = MagicMock(return_value={'balance': net_liq})
    return b


def test_adopts_more_negative_broker_loss_and_halts_on_missed_breach():
    # Day opened at 100_000; broker now 98_500 => real loss -1500.
    # Bot believes only -880 (within the $1,000 cap) -> MISSED breach -> halt.
    r = PnLReconciler(_broker(98_500), day_open_balance=100_000,
                      dollar_loss_cap=1000, tolerance=50)
    out = r.reconcile(believed_pnl_usd=-880.0)
    assert out['reconciled_pnl'] == -1500.0   # broker truth wins (more negative)
    assert out['dlc_breached'] is True
    assert out['action'] == 'halt'
    assert out['reason'] == 'dlc_missed_breach'


def test_keeps_computed_when_within_tolerance():
    # broker -860 vs believed -880 => $20 divergence, within $50 tolerance.
    r = PnLReconciler(_broker(99_140), day_open_balance=100_000,
                      dollar_loss_cap=1000, tolerance=50)
    out = r.reconcile(believed_pnl_usd=-880.0)
    assert out['action'] == 'accept'
    assert out['reconciled_pnl'] == -880.0


def test_corrects_on_divergence_without_breach():
    # broker -600 vs believed -300 => $300 divergence, no cap breach -> correct.
    r = PnLReconciler(_broker(99_400), day_open_balance=100_000,
                      dollar_loss_cap=1000, tolerance=50)
    out = r.reconcile(believed_pnl_usd=-300.0)
    assert out['action'] == 'correct'
    assert out['reconciled_pnl'] == -600.0
    assert out['dlc_breached'] is False


def test_already_known_breach_adopts_worse_broker_truth_not_halt():
    # Bot already knows it's past the cap (-1100). Broker is worse (-1200).
    # NOT a missed breach (bot already > -cap is false) -> no halt; just adopt the
    # more-negative broker truth ('correct') and let the existing DLC gate skip_all.
    r = PnLReconciler(_broker(98_800), day_open_balance=100_000,
                      dollar_loss_cap=1000, tolerance=50)
    out = r.reconcile(believed_pnl_usd=-1100.0)
    assert out['action'] == 'correct'
    assert out['reconciled_pnl'] == -1200.0
    assert out['dlc_breached'] is True


def test_fails_closed_when_broker_unavailable():
    b = MagicMock()
    b.get_account_info = MagicMock(side_effect=RuntimeError('api down'))
    r = PnLReconciler(b, day_open_balance=100_000, dollar_loss_cap=1000, tolerance=50)
    out = r.reconcile(believed_pnl_usd=-200.0)
    assert out['action'] == 'skip'   # don't trade blind; retry next tick
    assert out['reason'] == 'reconciliation_unavailable'


def test_fails_closed_when_no_baseline_yet():
    # Before the first _new_day, day_open_balance is None -> cannot reconcile.
    r = PnLReconciler(_broker(99_000), day_open_balance=None,
                      dollar_loss_cap=1000, tolerance=50)
    out = r.reconcile(believed_pnl_usd=-100.0)
    assert out['action'] == 'skip'
    assert out['reason'] == 'reconciliation_unavailable'


def test_missing_balance_field_fails_closed():
    b = MagicMock()
    b.get_account_info = MagicMock(return_value={})   # no 'balance'
    r = PnLReconciler(b, day_open_balance=100_000, dollar_loss_cap=1000, tolerance=50)
    out = r.reconcile(believed_pnl_usd=-100.0)
    assert out['action'] == 'skip'
    assert out['reason'] == 'reconciliation_unavailable'
