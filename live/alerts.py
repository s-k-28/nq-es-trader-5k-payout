"""Telegram alerts — sends trade notifications to your phone.

Setup:
  1. Open Telegram, search @BotFather
  2. Send /newbot, name it whatever (e.g. "NQ Trader Bot")
  3. Copy the bot token
  4. Search @userinfobot, send /start, copy your chat ID
  5. Add to .env:
       TELEGRAM_BOT_TOKEN=your_bot_token
       TELEGRAM_CHAT_ID=your_chat_id
"""
from __future__ import annotations
import threading
import os
import requests


class TelegramAlerts:
    def __init__(self):
        self.token = os.getenv('TELEGRAM_BOT_TOKEN', '')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID', '')
        self.enabled = bool(self.token and self.chat_id)

    def _send(self, text: str):
        if not self.enabled:
            return
        threading.Thread(target=self._post, args=(text,), daemon=True).start()

    def _post(self, text: str):
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={'chat_id': self.chat_id, 'text': text, 'parse_mode': 'HTML'},
                timeout=10,
            )
        except Exception:
            pass

    def trade_entry(self, model: str, direction: str, entry: float,
                    stop: float, target: float, contracts: int,
                    risk_ticks: float, risk_dollars: int):
        arrow = "⬆" if direction == 'long' else "⬇"
        self._send(
            f"{arrow} <b>NEW TRADE</b>\n"
            f"Model: <code>{model}</code>\n"
            f"Direction: {direction.upper()}\n"
            f"Entry: {entry:.2f}\n"
            f"Stop: {stop:.2f} | Target: {target:.2f}\n"
            f"Size: {contracts} MNQ (${risk_dollars:,} tier)\n"
            f"Risk: {risk_ticks:.0f} ticks"
        )

    def trade_exit(self, model: str, direction: str, total_r: float,
                   pnl_usd: float, reason: str, daily_r: float,
                   daily_pnl: float):
        if total_r > 0.1:
            icon = "✅"
        elif total_r < -0.1:
            icon = "❌"
        else:
            icon = "➖"
        self._send(
            f"{icon} <b>TRADE CLOSED</b>\n"
            f"Model: <code>{model}</code> | {reason}\n"
            f"Result: {total_r:+.2f}R (${pnl_usd:+,.0f})\n"
            f"\n<b>Daily:</b> {daily_r:+.2f}R (${daily_pnl:+,.0f})"
        )

    def daily_summary(self, trades: int, wins: int, daily_r: float,
                      daily_pnl: float, consec_losses: int):
        wr = (wins / trades * 100) if trades > 0 else 0
        icon = "\U0001F7E2" if daily_pnl >= 0 else "\U0001F534"
        self._send(
            f"{icon} <b>END OF DAY</b>\n"
            f"Trades: {trades} ({wr:.0f}% WR)\n"
            f"P&L: {daily_r:+.2f}R (${daily_pnl:+,.0f})\n"
            f"Consec losses: {consec_losses}"
        )

    def dlc_warning(self, daily_pnl: float, cap: float):
        self._send(
            f"⚠️ <b>DLC BREACHED</b>\n"
            f"Daily P&L: ${daily_pnl:+,.0f}\n"
            f"Cap: -${cap:,.0f}\n"
            f"No more trades today."
        )

    def dd_warning(self, balance: float, peak: float, dd: float, max_dd: float):
        pct = (dd / max_dd * 100) if max_dd > 0 else 0
        self._send(
            f"\U0001F6A8 <b>DRAWDOWN ALERT</b>\n"
            f"Balance: ${balance:,.0f}\n"
            f"Peak: ${peak:,.0f}\n"
            f"DD: ${dd:,.0f} / ${max_dd:,.0f} ({pct:.0f}%)\n"
            f"Reduce risk or stop trading."
        )

    def win_cap_hit(self, daily_r: float, cap: float):
        self._send(
            f"\U0001F3C6 <b>WIN CAP HIT</b>\n"
            f"Daily R: {daily_r:+.2f} (cap: {cap}R)\n"
            f"Done for the day. Good session."
        )

    def bot_started(self, balance: float, models: int):
        self._send(
            f"\U0001F680 <b>BOT STARTED</b>\n"
            f"Account: ${balance:,.0f}\n"
            f"Models: {models}\n"
            f"Waiting for signals..."
        )

    def bot_stopped(self, reason: str = "manual"):
        self._send(
            f"⏹ <b>BOT STOPPED</b>\n"
            f"Reason: {reason}"
        )

    def error(self, msg: str):
        self._send(f"❗ <b>ERROR</b>\n{msg}")
