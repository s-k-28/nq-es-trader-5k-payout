"""Interactive Brokers broker — connects via IB Gateway/TWS for MNQ trading.

Implements the same interface as TopStepBroker so executor_multi works unchanged.
Uses ib_insync for async IB API communication.
"""
from __future__ import annotations
import asyncio
import logging
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

import pandas as pd
from ib_insync import IB, Contract, MarketOrder, LimitOrder, StopOrder, util

log = logging.getLogger(__name__)

ORD_FILLED = 2
ORD_CANCELLED = 3
ORD_REJECTED = 5
ORD_EXPIRED = 4

ET = ZoneInfo('US/Eastern')

MONTH_CODES = {1: 'F', 2: 'G', 3: 'H', 4: 'J', 5: 'K', 6: 'M',
               7: 'N', 8: 'Q', 9: 'U', 10: 'V', 11: 'X', 12: 'Z'}
QUARTERLY = {'H', 'M', 'U', 'Z'}


def _front_month_code() -> str:
    now = datetime.now()
    month = now.month
    year = now.year
    code = MONTH_CODES[month]
    if code not in QUARTERLY:
        for m in range(month, month + 4):
            adj = ((m - 1) % 12) + 1
            if MONTH_CODES[adj] in QUARTERLY:
                code = MONTH_CODES[adj]
                if adj < month:
                    year += 1
                break
    elif now.day > 15:
        for m in range(month + 1, month + 5):
            adj = ((m - 1) % 12) + 1
            if MONTH_CODES[adj] in QUARTERLY:
                code = MONTH_CODES[adj]
                if adj <= month:
                    year += 1
                break
    yyyymm = f"{year}{code}"
    return yyyymm


def _ib_status_to_int(status: str) -> int:
    mapping = {
        'Filled': ORD_FILLED,
        'Cancelled': ORD_CANCELLED,
        'ApiCancelled': ORD_CANCELLED,
        'Inactive': ORD_REJECTED,
    }
    return mapping.get(status, 0)


class IBBroker:
    """Interactive Brokers adapter matching TopStepBroker interface."""

    def __init__(self, host: str = '127.0.0.1', port: int = 4002,
                 client_id: int = 1, symbol: str = 'MNQ'):
        self.host = host
        self.port = port
        self.client_id = client_id
        self.symbol = symbol
        self.ib = IB()
        self.contract: Contract | None = None
        self.contract_id = None
        self.account_id = ''
        self.tick_size = 0.25

        self._stop_order_id = None
        self._target_order_id = None
        self._entry_order_id = None

    def connect(self):
        log.info(f"Connecting to IB Gateway at {self.host}:{self.port} "
                 f"(client {self.client_id})...")
        self.ib.connect(self.host, self.port, clientId=self.client_id,
                        timeout=15)

        accounts = self.ib.managedAccounts()
        if not accounts:
            raise RuntimeError("No IB accounts found")
        self.account_id = accounts[0]
        log.info(f"Connected — Account: {self.account_id}")

        front = _front_month_code()
        year = int(front[:4])
        month_code = front[4]
        month_num = {v: k for k, v in MONTH_CODES.items()}[month_code]
        last_trade = f"{year}{month_num:02d}"

        self.contract = Contract(
            secType='FUT',
            symbol='MNQ',
            exchange='CME',
            currency='USD',
            lastTradeDateOrContractMonth=last_trade,
        )

        qualified = self.ib.qualifyContracts(self.contract)
        if not qualified:
            raise RuntimeError(f"Could not qualify MNQ contract for {last_trade}")

        self.contract = qualified[0]
        self.contract_id = self.contract.conId
        log.info(f"Contract: {self.contract.localSymbol} "
                 f"(conId: {self.contract_id})")

    def disconnect(self):
        if self.ib.isConnected():
            self.ib.disconnect()
            log.info("Disconnected from IB Gateway")

    def get_bars(self, minutes_back: int = 6000) -> pd.DataFrame:
        seconds = minutes_back * 60
        if seconds <= 86400:
            duration = f"{seconds} S"
        else:
            days = minutes_back // (60 * 24) + 1
            duration = f"{min(days, 30)} D"

        try:
            ib_bars = self.ib.reqHistoricalData(
                self.contract,
                endDateTime='',
                durationStr=duration,
                barSizeSetting='1 min',
                whatToShow='TRADES',
                useRTH=False,
                formatDate=1,
                timeout=30,
            )
        except Exception as e:
            log.warning(f"Bar fetch failed: {e}")
            return pd.DataFrame()

        if not ib_bars:
            return pd.DataFrame()

        df = util.df(ib_bars)
        df = df.rename(columns={'date': 'datetime'})
        df['datetime'] = pd.to_datetime(df['datetime'])
        if df['datetime'].dt.tz is not None:
            df['datetime'] = df['datetime'].dt.tz_localize(None)
        df = df[['datetime', 'open', 'high', 'low', 'close', 'volume']].copy()
        df['volume'] = df['volume'].fillna(0).astype(int)
        df.loc[df['volume'] <= 0, 'volume'] = 1
        df = df.sort_values('datetime').drop_duplicates('datetime').reset_index(drop=True)
        return df

    def get_daily_bars(self, days_back: int = 90) -> pd.DataFrame:
        try:
            ib_bars = self.ib.reqHistoricalData(
                self.contract,
                endDateTime='',
                durationStr=f"{min(days_back, 365)} D",
                barSizeSetting='1 day',
                whatToShow='TRADES',
                useRTH=True,
                formatDate=1,
                timeout=30,
            )
        except Exception as e:
            log.warning(f"Daily bar fetch failed: {e}")
            return pd.DataFrame()

        if not ib_bars:
            return pd.DataFrame()

        df = util.df(ib_bars)
        df = df.rename(columns={'date': 'date'})
        df['date'] = pd.to_datetime(df['date'])
        df = df[['date', 'open', 'high', 'low', 'close', 'volume']].copy()
        df = df.sort_values('date').drop_duplicates('date').reset_index(drop=True)
        return df

    def get_latest_bars(self, n: int = 5) -> pd.DataFrame:
        return self.get_bars(minutes_back=n + 10)

    def place_limit_entry(self, direction: str, qty: int,
                          entry_price: float) -> int:
        action = 'BUY' if direction == 'long' else 'SELL'
        order = LimitOrder(action, qty, self._round(entry_price))
        order.tif = 'DAY'
        trade = self.ib.placeOrder(self.contract, order)
        self._entry_order_id = trade.order.orderId
        log.info(f"Entry LIMIT {direction.upper()} {qty} @ {entry_price:.2f} "
                 f"(#{self._entry_order_id})")
        return self._entry_order_id

    def place_bracket_entry(self, direction: str, qty: int, entry_price: float,
                            stop_price: float, target_price: float) -> dict:
        """Submit entry + protective stop + target as a native IB bracket.

        The stop/target children carry the parent's ``parentId`` so they only
        become active when the entry fills, but they are transmitted together
        with the parent. The protective stop is therefore live at IB the instant
        the position opens — there is no window where the position sits with no
        stop. Returns ``attached=True`` so the executor skips post-fill placement.
        """
        action = 'BUY' if direction == 'long' else 'SELL'
        exit_action = 'SELL' if direction == 'long' else 'BUY'
        oca_group = f"br_{int(time.time())}_{qty}"

        parent = LimitOrder(action, qty, self._round(entry_price))
        parent.orderId = self.ib.client.getReqId()
        parent.tif = 'DAY'
        parent.transmit = False

        stop_order = StopOrder(exit_action, qty, self._round(stop_price))
        stop_order.orderId = self.ib.client.getReqId()
        stop_order.parentId = parent.orderId
        stop_order.tif = 'GTC'
        stop_order.ocaGroup = oca_group
        stop_order.ocaType = 1
        stop_order.transmit = False

        target_order = LimitOrder(exit_action, qty, self._round(target_price))
        target_order.orderId = self.ib.client.getReqId()
        target_order.parentId = parent.orderId
        target_order.tif = 'GTC'
        target_order.ocaGroup = oca_group
        target_order.ocaType = 1
        target_order.transmit = True  # transmits the whole group atomically

        self.ib.placeOrder(self.contract, parent)
        self.ib.placeOrder(self.contract, stop_order)
        self.ib.placeOrder(self.contract, target_order)

        self._entry_order_id = parent.orderId
        self._stop_order_id = stop_order.orderId
        self._target_order_id = target_order.orderId

        log.info(f"Bracket entry {direction.upper()} {qty} @ {entry_price:.2f} "
                 f"(#{parent.orderId}) | stop {stop_price:.2f} (#{stop_order.orderId})"
                 f" target {target_price:.2f} (#{target_order.orderId}) OCA {oca_group}")
        return {
            'entry': parent.orderId,
            'stop': stop_order.orderId,
            'target': target_order.orderId,
            'attached': True,
        }

    def place_exit_bracket(self, direction: str, qty: int,
                           stop_price: float, target_price: float) -> dict:
        exit_action = 'SELL' if direction == 'long' else 'BUY'
        oca_group = f"exit_{int(time.time())}_{qty}"

        stop_order = StopOrder(exit_action, qty, self._round(stop_price))
        stop_order.tif = 'GTC'
        stop_order.ocaGroup = oca_group
        stop_order.ocaType = 1
        stop_trade = self.ib.placeOrder(self.contract, stop_order)
        self._stop_order_id = stop_trade.order.orderId

        try:
            target_order = LimitOrder(exit_action, qty, self._round(target_price))
            target_order.tif = 'GTC'
            target_order.ocaGroup = oca_group
            target_order.ocaType = 1
            target_trade = self.ib.placeOrder(self.contract, target_order)
            self._target_order_id = target_trade.order.orderId
        except Exception as e:
            log.error(f"Target order failed: {e} — cancelling stop to avoid naked order")
            self.cancel_order(self._stop_order_id)
            self._stop_order_id = None
            raise

        log.info(f"Exit bracket — stop: {stop_price:.2f} (#{self._stop_order_id})"
                 f" target: {target_price:.2f} (#{self._target_order_id})"
                 f" OCA: {oca_group}")
        return {
            'stop': self._stop_order_id,
            'target': self._target_order_id,
        }

    def modify_stop(self, new_price: float) -> bool:
        if not self._stop_order_id:
            return False
        for trade in self.ib.openTrades():
            if trade.order.orderId == self._stop_order_id:
                trade.order.auxPrice = self._round(new_price)
                self.ib.placeOrder(self.contract, trade.order)
                log.info(f"Stop modified → {new_price:.2f}")
                return True
        log.warning(f"Stop order {self._stop_order_id} not found in open trades")
        return False

    def get_order_status(self, order_id: int) -> int | None:
        if not order_id:
            return None
        self.ib.sleep(0.2)
        for trade in self.ib.trades():
            if trade.order.orderId == order_id:
                status = trade.orderStatus.status
                mapped = _ib_status_to_int(status)
                if mapped:
                    return mapped
                if status in ('Submitted', 'PreSubmitted', 'PendingSubmit', 'PendingCancel'):
                    return 0
                return 0
        return ORD_CANCELLED

    def cancel_order(self, order_id: int):
        if not order_id:
            return
        for trade in self.ib.openTrades():
            if trade.order.orderId == order_id:
                try:
                    self.ib.cancelOrder(trade.order)
                except Exception as e:
                    log.warning(f"Cancel order {order_id} failed: {e}")
                return
        log.debug(f"Order {order_id} not in open trades (already filled/cancelled)")

    def cancel_all_exit_orders(self):
        self.cancel_order(self._stop_order_id)
        self.cancel_order(self._target_order_id)
        self._stop_order_id = None
        self._target_order_id = None

    def flatten(self) -> bool:
        self.cancel_all_exit_orders()
        last_trade = None
        for attempt in range(3):
            pos = self.get_position()
            if not pos or pos['size'] == 0:
                log.info("Position flattened and verified.")
                return True
            if last_trade is not None:
                status = last_trade.orderStatus.status
                if status == 'Filled':
                    try:
                        self.ib.sleep(1)
                    except KeyboardInterrupt:
                        log.critical("Ctrl+C during flatten — checking position")
                    continue
                if status not in ('Cancelled', 'Inactive', 'ApiCancelled'):
                    self.ib.cancelOrder(last_trade.order)
                    try:
                        self.ib.sleep(2)
                    except KeyboardInterrupt:
                        log.critical("Ctrl+C during flatten cancel wait")
            action = 'SELL' if pos['side'] == 'long' else 'BUY'
            order = MarketOrder(action, pos['size'])
            order.tif = 'DAY'
            last_trade = self.ib.placeOrder(self.contract, order)
            log.info(f"Flatten {action} {pos['size']} MNQ (attempt {attempt+1})")
            try:
                self.ib.sleep(3)
            except KeyboardInterrupt:
                log.critical("Ctrl+C during flatten sleep — order submitted, checking position")
            remaining = self.get_position()
            if not remaining or remaining['size'] == 0:
                log.info("Position flattened and verified.")
                return True
            log.warning(f"Flatten sent but {remaining['size']} {remaining['side']} remain (attempt {attempt+1})")
        log.error("FLATTEN VERIFICATION FAILED — POSITION MAY BE OPEN")
        return False

    def get_position(self) -> dict | None:
        self.ib.sleep(0.1)
        for pos in self.ib.positions():
            if pos.contract.conId == self.contract_id:
                if pos.position != 0:
                    multiplier = float(self.contract.multiplier) if self.contract and self.contract.multiplier else 2.0
                    return {
                        'contractId': self.contract_id,
                        'size': abs(int(pos.position)),
                        'side': 'long' if pos.position > 0 else 'short',
                        'avgPrice': pos.avgCost / multiplier if pos.avgCost else 0,
                    }
        return None

    def position_size(self) -> int:
        pos = self.get_position()
        return pos.get('size', 0) if pos else 0

    def get_account_info(self) -> dict:
        self.ib.sleep(0.1)
        summary = {}
        for av in self.ib.accountValues():
            if av.account == self.account_id:
                if av.tag == 'NetLiquidation' and av.currency == 'USD':
                    summary['balance'] = float(av.value)
                elif av.tag == 'TotalCashBalance' and av.currency == 'USD':
                    summary['cash'] = float(av.value)
                elif av.tag == 'UnrealizedPnL' and av.currency == 'USD':
                    summary['unrealizedPnl'] = float(av.value)
                elif av.tag == 'RealizedPnL' and av.currency == 'USD':
                    summary['realizedPnl'] = float(av.value)

        summary.setdefault('balance', 0)
        summary['id'] = self.account_id
        summary['name'] = f'IB-{self.account_id}'
        return summary

    def get_exit_fill_price(self, entry_time=None) -> float | None:
        """Return the fill price of the most recent execution on this contract."""
        try:
            fills = self.ib.fills()
            contract_fills = [f for f in fills
                              if f.contract.conId == self.contract_id]
            if entry_time is not None:
                contract_fills = [f for f in contract_fills
                                  if f.time >= entry_time]
            if not contract_fills:
                return None
            contract_fills.sort(key=lambda f: f.time, reverse=True)
            return contract_fills[0].execution.price
        except Exception:
            return None

    def _post(self, endpoint: str, payload: dict = None) -> dict:
        if 'Trade/search' in endpoint:
            return {'trades': []}
        return {}

    def _round(self, price: float) -> float:
        return round(price / self.tick_size) * self.tick_size
