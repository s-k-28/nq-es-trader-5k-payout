"""Non-blocking trade reporter — sends heartbeats and trades to the central hub.

Fails silently so trading is never affected if the hub is down.
"""
from __future__ import annotations
import threading
import time
import requests
import os


class HubReporter:
    def __init__(self, hub_url: str | None = None, user_id: str | None = None):
        self.hub_url = (hub_url or os.getenv('HUB_URL', '')).rstrip('/')
        self.user_id = user_id or os.getenv('HUB_USER_ID', 'anonymous')
        self.enabled = bool(self.hub_url)
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if not self.enabled:
            return
        self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._thread.start()
        print(f"  Hub reporter started → {self.hub_url} (user: {self.user_id})")

    def stop(self):
        self._stop.set()

    def report_trade(self, trade_data: dict):
        if not self.enabled:
            return
        threading.Thread(target=self._send_trade, args=(trade_data,), daemon=True).start()

    def _heartbeat_loop(self):
        while not self._stop.is_set():
            try:
                requests.post(f"{self.hub_url}/api/heartbeat", json={
                    'user_id': self.user_id,
                }, timeout=5)
            except Exception:
                pass
            self._stop.wait(30)

    def _send_trade(self, trade_data: dict):
        try:
            trade_data['user_id'] = self.user_id
            requests.post(f"{self.hub_url}/api/trade", json=trade_data, timeout=5)
        except Exception:
            pass
