"""TopStepX broker — REST API for live MNQ trading on TopStep 100K."""
from __future__ import annotations
import os
import time
import logging
import threading
from datetime import datetime, timedelta, timezone
import requests
import pandas as pd

log = logging.getLogger(__name__)

URLS = {
    'demo': {
        'rest': 'https://api.topstepx.com',
        'market_hub': 'https://rtc.topstepx.com/hubs/market',
        'user_hub': 'https://rtc.topstepx.com/hubs/user',
    },
    'live': {
        'rest': 'https://api.topstepx.com',
        'market_hub': 'https://rtc.topstepx.com/hubs/market',
        'user_hub': 'https://rtc.topstepx.com/hubs/user',
    },
}

# Order types (ProjectX API enum)
LIMIT = 1
MARKET = 2
STOP_LIMIT = 3
STOP = 4
TRAILING_STOP = 5

# Sides
BUY = 0
SELL = 1

# Order statuses
ORD_OPEN = 1
ORD_FILLED = 2
ORD_CANCELLED = 3
ORD_EXPIRED = 4
ORD_REJECTED = 5
ORD_PENDING = 6

# Month codes for futures
MONTH_CODES = {1:'F', 2:'G', 3:'H', 4:'J', 5:'K', 6:'M',
               7:'N', 8:'Q', 9:'U', 10:'V', 11:'X', 12:'Z'}
QUARTERLY = {'H', 'M', 'U', 'Z'}


def _front_month_mnq() -> str:
    now = datetime.now()
    year = now.year % 100
    month = now.month
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
    return f"CON.F.US.MNQ.{code}{year}"


class TopStepBroker:
    def __init__(self, username: str, api_key: str, env: str = 'demo'):
        urls = URLS[env]
        self.base = urls['rest']
        self.token = None
        self.token_expiry = None
        self.username = username
        self.api_key = api_key
        self.account_id = None
        self.contract_id = None
        self.tick_size = 0.25

        self._stop_order_id = None
        self._target_order_id = None
        self._entry_order_id = None

    def _headers(self):
        return {
            'Authorization': f'Bearer {self.token}',
            'Content-Type': 'application/json',
        }

    def _post(self, endpoint: str, payload: dict = None) -> dict:
        self._ensure_token()
        url = f"{self.base}{endpoint}"
        for attempt in range(4):
            resp = requests.post(url, json=payload or {}, headers=self._headers(),
                                 timeout=15)
            if resp.status_code == 429:
                wait = min(2 ** attempt * 2, 30)
                log.warning(f"Rate limited (429), retry in {wait}s (attempt {attempt+1}/4)")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            if not data.get('success', True):
                err = data.get('errorMessage', 'Unknown error')
                raise RuntimeError(f"TopStepX API error: {err}")
            return data
        resp.raise_for_status()
        return {}

    def connect(self):
        log.info("Authenticating with TopStepX...")
        resp = requests.post(
            f"{self.base}/api/Auth/loginKey",
            json={"userName": self.username, "apiKey": self.api_key},
            headers={'Content-Type': 'application/json'},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not data.get('success'):
            raise RuntimeError(f"Auth failed: {data.get('errorMessage')}")
        self.token = data['token']
        self.token_expiry = time.time() + 23 * 3600
        log.info("Authenticated.")

        log.info("Fetching account...")
        acct_data = self._post('/api/Account/search',
                               {'onlyActiveAccounts': True})
        accounts = acct_data.get('accounts', [])
        if not accounts:
            raise RuntimeError("No active accounts found")
        self.account_id = accounts[0]['id']
        acct_name = accounts[0].get('name', 'Unknown')
        log.info(f"Account: {acct_name} (ID: {self.account_id})")

        self.contract_id = _front_month_mnq()
        log.info(f"Contract: {self.contract_id}")

        verify = self._post('/api/Contract/searchById',
                            {'contractId': self.contract_id})
        c = verify.get('contract')
        if c:
            self.tick_size = c.get('tickSize', 0.25)
            log.info(f"Verified: {c.get('name', self.contract_id)} "
                     f"tick={self.tick_size}")
        else:
            log.warning(f"Contract {self.contract_id} not found — "
                        f"check if front month is correct")

    def _ensure_token(self):
        if self.token and time.time() < self.token_expiry - 1800:
            return
        log.info("Refreshing token...")
        resp = requests.post(
            f"{self.base}/api/Auth/validate",
            json={}, headers=self._headers(), timeout=10,
        )
        data = resp.json()
        if data.get('newToken'):
            self.token = data['newToken']
        self.token_expiry = time.time() + 23 * 3600

    # ── Market Data ──────────────────────────────────────────────────

    def get_bars(self, minutes_back: int = 6000) -> pd.DataFrame:
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=minutes_back)

        all_bars = []
        chunk_start = start
        empty_streak = 0
        while chunk_start < end:
            chunk_end = min(chunk_start + timedelta(minutes=999), end)
            try:
                data = self._post('/api/History/retrieveBars', {
                    'contractId': self.contract_id,
                    'startTime': chunk_start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'endTime': chunk_end.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    'unit': 2,
                    'unitNumber': 1,
                    'limit': 1000,
                    'live': False,
                    'includePartialBar': False,
                })
            except Exception as e:
                log.warning(f"Bar fetch failed: {e}")
                chunk_start = chunk_end
                time.sleep(1)
                continue
            bars = data.get('bars', [])
            all_bars.extend(bars)
            if len(bars) == 0:
                empty_streak += 1
                chunk_start = chunk_end
            elif len(bars) < 1000:
                empty_streak = 0
                chunk_start = chunk_end
            else:
                empty_streak = 0
                last_t = pd.to_datetime(bars[-1]['t'])
                chunk_start = last_t + timedelta(minutes=1)
            if empty_streak > 5 and all_bars:
                break
            time.sleep(0.5)

        if not all_bars:
            return pd.DataFrame()

        df = pd.DataFrame(all_bars)
        df = df.rename(columns={'t': 'datetime', 'o': 'open', 'h': 'high',
                                'l': 'low', 'c': 'close', 'v': 'volume'})
        df['datetime'] = pd.to_datetime(df['datetime'])
        df = df.sort_values('datetime').drop_duplicates('datetime')
        df = df.reset_index(drop=True)
        return df

    def get_daily_bars(self, days_back: int = 90) -> pd.DataFrame:
        """Fetch daily bars directly from API for regime warmup."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days_back)
        try:
            data = self._post('/api/History/retrieveBars', {
                'contractId': self.contract_id,
                'startTime': start.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'endTime': end.strftime('%Y-%m-%dT%H:%M:%SZ'),
                'unit': 4,
                'unitNumber': 1,
                'limit': 1000,
                'live': False,
                'includePartialBar': False,
            })
        except Exception as e:
            log.warning(f"Daily bar fetch failed: {e}")
            return pd.DataFrame()
        bars = data.get('bars', [])
        if not bars:
            return pd.DataFrame()
        df = pd.DataFrame(bars)
        df = df.rename(columns={'t': 'date', 'o': 'open', 'h': 'high',
                                'l': 'low', 'c': 'close', 'v': 'volume'})
        df['date'] = pd.to_datetime(df['date'])
        df = df.sort_values('date').drop_duplicates('date').reset_index(drop=True)
        return df

    def get_latest_bars(self, n: int = 5) -> pd.DataFrame:
        return self.get_bars(minutes_back=n + 10)

    # ── Orders ───────────────────────────────────────────────────────

    def place_limit_entry(self, direction: str, qty: int,
                          entry_price: float) -> int:
        side = BUY if direction == 'long' else SELL
        entry = self._post('/api/Order/place', {
            'accountId': self.account_id,
            'contractId': self.contract_id,
            'type': LIMIT,
            'side': side,
            'size': qty,
            'limitPrice': self._round(entry_price),
        })
        self._entry_order_id = entry.get('orderId')
        log.info(f"Entry LIMIT {direction.upper()} {qty} @ {entry_price:.2f} "
                 f"(#{self._entry_order_id})")
        return self._entry_order_id

    def place_exit_bracket(self, direction: str, qty: int,
                           stop_price: float, target_price: float) -> dict:
        exit_side = SELL if direction == 'long' else BUY

        stop_resp = self._post('/api/Order/place', {
            'accountId': self.account_id,
            'contractId': self.contract_id,
            'type': STOP,
            'side': exit_side,
            'size': qty,
            'stopPrice': self._round(stop_price),
        })
        self._stop_order_id = stop_resp.get('orderId')

        target_resp = self._post('/api/Order/place', {
            'accountId': self.account_id,
            'contractId': self.contract_id,
            'type': LIMIT,
            'side': exit_side,
            'size': qty,
            'limitPrice': self._round(target_price),
        })
        self._target_order_id = target_resp.get('orderId')

        log.info(f"Exit bracket placed — stop: {stop_price} (#{self._stop_order_id})"
                 f" target: {target_price} (#{self._target_order_id})")
        return {
            'stop': self._stop_order_id,
            'target': self._target_order_id,
        }

    def modify_stop(self, new_price: float):
        if not self._stop_order_id:
            return
        self._post('/api/Order/modify', {
            'accountId': self.account_id,
            'orderId': self._stop_order_id,
            'stopPrice': self._round(new_price),
        })
        log.info(f"Stop modified → {new_price}")

    def get_order_status(self, order_id: int) -> int | None:
        if not order_id:
            return None
        try:
            data = self._post('/api/Order/searchOpen', {
                'accountId': self.account_id,
            })
            for o in data.get('orders', []):
                if o.get('id') == order_id:
                    return o.get('status')
            if self.position_size() > 0:
                return ORD_FILLED
            return ORD_CANCELLED
        except Exception:
            return None

    def cancel_order(self, order_id: int):
        if not order_id:
            return
        try:
            self._post('/api/Order/cancel', {
                'accountId': self.account_id,
                'orderId': order_id,
            })
        except Exception as e:
            log.warning(f"Cancel order {order_id} failed: {e}")

    def cancel_all_exit_orders(self):
        self.cancel_order(self._stop_order_id)
        self.cancel_order(self._target_order_id)
        self._stop_order_id = None
        self._target_order_id = None

    def flatten(self):
        self.cancel_all_exit_orders()
        try:
            self._post('/api/Position/closeContract', {
                'accountId': self.account_id,
                'contractId': self.contract_id,
            })
            log.info("Position flattened.")
        except Exception as e:
            log.warning(f"Flatten failed: {e}")

    # ── Position ─────────────────────────────────────────────────────

    def get_position(self) -> dict | None:
        data = self._post('/api/Position/searchOpen', {
            'accountId': self.account_id,
        })
        positions = data.get('positions', [])
        for p in positions:
            if p.get('contractId') == self.contract_id:
                return p
        return None

    def position_size(self) -> int:
        pos = self.get_position()
        return pos.get('size', 0) if pos else 0

    # ── Account ──────────────────────────────────────────────────────

    def get_account_info(self) -> dict:
        data = self._post('/api/Account/search',
                          {'onlyActiveAccounts': True})
        accounts = data.get('accounts', [])
        for a in accounts:
            if a['id'] == self.account_id:
                return a
        return {}

    # ── Helpers ──────────────────────────────────────────────────────

    def _round(self, price: float) -> float:
        return round(price / self.tick_size) * self.tick_size
