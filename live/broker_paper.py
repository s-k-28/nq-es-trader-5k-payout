"""Paper broker — simulates order execution locally with no real account.

Implements the same interface as TopStepBroker so the executor works unchanged.
Tracks positions, fills, P&L, and account balance in memory.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger(__name__)

ORD_FILLED = 'filled'
ORD_CANCELLED = 'cancelled'
ORD_REJECTED = 'rejected'
ORD_EXPIRED = 'expired'


@dataclass
class PaperPosition:
    symbol: str = ''
    qty: int = 0
    avg_price: float = 0.0
    side: str = ''


@dataclass
class PaperOrder:
    order_id: str = ''
    symbol: str = ''
    side: str = ''
    qty: int = 0
    order_type: str = 'market'
    limit_price: float = 0.0
    stop_price: float = 0.0
    status: str = ''
    fill_price: float = 0.0
    fill_time: datetime | None = None


class PaperBroker:
    """Simulated broker for paper trading with no real account."""

    def __init__(self, starting_balance: float = 100_000.0,
                 tick_value: float = 0.50, symbol: str = 'MNQM5'):
        self.starting_balance = starting_balance
        self.balance = starting_balance
        self.tick_value = tick_value
        self.symbol = symbol
        self.position: PaperPosition | None = None
        self.orders: list[PaperOrder] = []
        self.fills: list[PaperOrder] = []
        self.pending_stops: list[PaperOrder] = []
        self.trade_history: list[dict] = []
        self._order_counter = 0
        self._last_price = 0.0
        self.connected = False
        self.account_id = 'PAPER-SIM'
        self.peak_balance = starting_balance
        self.daily_pnl = 0.0
        self.total_pnl = 0.0

    def connect(self):
        self.connected = True
        log.info(f"Paper broker connected — ${self.starting_balance:,.0f} starting balance")

    def disconnect(self):
        self.connected = False
        log.info("Paper broker disconnected")

    def get_account(self) -> dict:
        return {
            'id': self.account_id,
            'balance': self.balance,
            'pnl': self.total_pnl,
            'daily_pnl': self.daily_pnl,
            'buying_power': self.balance,
        }

    def get_position(self, symbol: str = None) -> dict | None:
        if self.position and self.position.qty != 0:
            return {
                'symbol': self.position.symbol,
                'qty': self.position.qty,
                'side': self.position.side,
                'avg_price': self.position.avg_price,
                'unrealized_pnl': self._unrealized_pnl(),
            }
        return None

    def _unrealized_pnl(self) -> float:
        if not self.position or self.position.qty == 0:
            return 0.0
        if self.position.side == 'long':
            return (self._last_price - self.position.avg_price) * self.position.qty / 0.25 * self.tick_value
        else:
            return (self.position.avg_price - self._last_price) * self.position.qty / 0.25 * self.tick_value

    def place_market_order(self, symbol: str, side: str, qty: int) -> dict:
        self._order_counter += 1
        oid = f'PAPER-{self._order_counter}'

        order = PaperOrder(
            order_id=oid, symbol=symbol, side=side, qty=qty,
            order_type='market', status=ORD_FILLED,
            fill_price=self._last_price,
            fill_time=datetime.now(),
        )

        self._execute_fill(order)

        return {
            'order_id': oid,
            'status': ORD_FILLED,
            'fill_price': order.fill_price,
            'fill_qty': qty,
        }

    def place_limit_order(self, symbol: str, side: str, qty: int,
                          limit_price: float) -> dict:
        self._order_counter += 1
        oid = f'PAPER-{self._order_counter}'
        order = PaperOrder(
            order_id=oid, symbol=symbol, side=side, qty=qty,
            order_type='limit', limit_price=limit_price,
            status=ORD_FILLED, fill_price=limit_price,
            fill_time=datetime.now(),
        )
        self._execute_fill(order)
        return {'order_id': oid, 'status': ORD_FILLED, 'fill_price': limit_price}

    def place_stop_order(self, symbol: str, side: str, qty: int,
                         stop_price: float) -> dict:
        self._order_counter += 1
        oid = f'PAPER-{self._order_counter}'
        order = PaperOrder(
            order_id=oid, symbol=symbol, side=side, qty=qty,
            order_type='stop', stop_price=stop_price, status='pending',
        )
        self.pending_stops.append(order)
        return {'order_id': oid, 'status': 'pending', 'stop_price': stop_price}

    def cancel_order(self, order_id: str) -> bool:
        self.pending_stops = [o for o in self.pending_stops if o.order_id != order_id]
        return True

    def cancel_all_orders(self, symbol: str = None) -> int:
        n = len(self.pending_stops)
        self.pending_stops.clear()
        return n

    def flatten(self, symbol: str = None) -> dict:
        if self.position and self.position.qty > 0:
            side = 'sell' if self.position.side == 'long' else 'buy'
            result = self.place_market_order(self.symbol, side, self.position.qty)
            self.cancel_all_orders()
            return result
        return {'status': 'flat'}

    def _execute_fill(self, order: PaperOrder):
        self.fills.append(order)

        if self.position is None or self.position.qty == 0:
            self.position = PaperPosition(
                symbol=order.symbol,
                qty=order.qty,
                avg_price=order.fill_price,
                side='long' if order.side == 'buy' else 'short',
            )
        elif (self.position.side == 'long' and order.side == 'sell') or \
             (self.position.side == 'short' and order.side == 'buy'):
            # Closing trade
            if self.position.side == 'long':
                pnl_per_contract = (order.fill_price - self.position.avg_price) / 0.25 * self.tick_value
            else:
                pnl_per_contract = (self.position.avg_price - order.fill_price) / 0.25 * self.tick_value

            close_qty = min(order.qty, self.position.qty)
            trade_pnl = pnl_per_contract * close_qty
            self.balance += trade_pnl
            self.total_pnl += trade_pnl
            self.daily_pnl += trade_pnl

            if self.balance > self.peak_balance:
                self.peak_balance = self.balance

            remaining = self.position.qty - close_qty
            if remaining <= 0:
                self.position = PaperPosition()
            else:
                self.position.qty = remaining

            self.trade_history.append({
                'time': order.fill_time,
                'side': self.position.side if remaining > 0 else order.side,
                'qty': close_qty,
                'entry': self.position.avg_price if trade_pnl != 0 else order.fill_price,
                'exit': order.fill_price,
                'pnl': trade_pnl,
                'balance': self.balance,
            })

            log.info(f"  PAPER FILL: {order.side} {close_qty} @ {order.fill_price:.2f} "
                     f"| PnL: ${trade_pnl:+,.2f} | Balance: ${self.balance:,.2f}")
        else:
            # Adding to position
            total_cost = self.position.avg_price * self.position.qty + order.fill_price * order.qty
            self.position.qty += order.qty
            self.position.avg_price = total_cost / self.position.qty

    def update_price(self, price: float):
        """Call on every new bar to update last price and check pending stops."""
        self._last_price = price
        triggered = []
        for stop in self.pending_stops:
            if stop.side == 'sell' and price <= stop.stop_price:
                stop.status = ORD_FILLED
                stop.fill_price = stop.stop_price
                stop.fill_time = datetime.now()
                triggered.append(stop)
            elif stop.side == 'buy' and price >= stop.stop_price:
                stop.status = ORD_FILLED
                stop.fill_price = stop.stop_price
                stop.fill_time = datetime.now()
                triggered.append(stop)

        for order in triggered:
            self.pending_stops.remove(order)
            self._execute_fill(order)

    def reset_daily(self):
        self.daily_pnl = 0.0
