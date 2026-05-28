"""Reconcile in-memory daily P&L against broker-authoritative balance.

Principle: **broker cash is truth; the executor's computed `daily_pnl_usd` is a
hint.** The computed value is built from bar-close estimates and an optimistic
fixed-slip assumption, so it biases toward UNDERSTATING loss — which can let the
daily-loss cap be silently breached (one breach fails a funded eval).

The reconciler cross-checks the computed daily P&L against the broker's
NetLiquidation balance delta for the day (`current_balance - day_open_balance`),
which captures realized AND unrealized drawdown and is the one field both the IB
and TopStep adapters expose (`get_account_info()['balance']`).

Policy (all fail-closed):
- More-negative value wins when the two disagree beyond `tolerance`.
- Broker shows a cap breach the bot MISSED  -> action 'halt' (integrity alarm).
- Broker truth unavailable / no day baseline -> action 'skip' (don't trade blind).
- Divergence beyond tolerance, no breach     -> action 'correct' (adopt truth).
- Otherwise                                  -> action 'accept' (use computed).
"""
from __future__ import annotations
import logging

log = logging.getLogger(__name__)


class PnLReconciler:
    def __init__(self, broker, day_open_balance, dollar_loss_cap,
                 tolerance: float = 50.0):
        self.broker = broker
        self.day_open_balance = day_open_balance   # updated each _new_day
        self.dollar_loss_cap = dollar_loss_cap
        self.tolerance = tolerance

    def _broker_pnl(self) -> float:
        """Broker-truth P&L for the day = current NetLiq − day-open balance.

        Raises if no baseline or no balance is available (caller fails closed).
        """
        if self.day_open_balance is None:
            raise RuntimeError('no day_open_balance baseline')
        info = self.broker.get_account_info()
        bal = info.get('balance') if info else None
        if bal is None:
            raise RuntimeError('no balance field from broker')
        return float(bal) - float(self.day_open_balance)

    def reconcile(self, believed_pnl_usd: float) -> dict:
        try:
            broker_pnl = self._broker_pnl()
        except Exception as e:
            log.critical(
                f"Reconciler: broker truth unavailable ({e}) -- failing closed (skip entry)")
            return {
                'action': 'skip', 'reason': 'reconciliation_unavailable',
                'reconciled_pnl': believed_pnl_usd, 'broker_pnl': None,
                'believed_pnl': believed_pnl_usd, 'divergence': None,
                'dlc_breached': None,
            }

        divergence = abs(believed_pnl_usd - broker_pnl)
        # More-negative (more conservative) wins when they disagree beyond tolerance.
        if divergence > self.tolerance:
            reconciled = min(believed_pnl_usd, broker_pnl)
        else:
            reconciled = believed_pnl_usd

        dlc_breached = reconciled <= -self.dollar_loss_cap
        missed_breach = dlc_breached and believed_pnl_usd > -self.dollar_loss_cap

        if missed_breach:
            action, reason = 'halt', 'dlc_missed_breach'
        elif divergence > self.tolerance:
            action, reason = 'correct', f'divergence_{divergence:.0f}'
        else:
            action, reason = 'accept', f'divergence_{divergence:.0f}'

        return {
            'action': action, 'reason': reason,
            'reconciled_pnl': round(reconciled, 2),
            'broker_pnl': round(broker_pnl, 2),
            'believed_pnl': round(believed_pnl_usd, 2),
            'divergence': round(divergence, 2),
            'dlc_breached': dlc_breached,
        }
