"""Live executor — 9-model strategy on TopStepX 100K funded account.

Sizing matches backtest exactly: model-tiered dollar risk from
cfg.funded.model_risk_dollars, converted to contracts via
min(50, floor(risk_dollars / (risk_ticks * $0.50))).

Risk controls (matching backtest engine_v2 + funded_sim):
- Daily win cap: 2.0R total, stop taking signals
- Dollar loss cap: $1,000/day, skip trades once breached
- Consecutive loss cooldown: 10 losses, skip next signal
- No daily R loss limit (allows intraday recovery)
- BE at 0.6R, trail at 0.001, pp=0.0 (from model risk profiles)

Funded account rules (TopStepX 100K XFA):
- $3,000 trailing drawdown, locks at $0 floor when peak >= $3K
- $5,000 max payout, 30% of balance, 5 green days ($150+)
"""
from __future__ import annotations
import logging
import time
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
from dataclasses import dataclass, field
import pandas as pd
from config import Config
from data.loader import build_daily_bars
from strategy.multi import MultiModelGenerator
from strategy.models.base import Signal
ORD_FILLED = 2
ORD_CANCELLED = 3
ORD_REJECTED = 5
ORD_EXPIRED = 4
from live.reporter import HubReporter
from live.alerts import TelegramAlerts
from live.adaptive import AdaptiveGuard

log = logging.getLogger(__name__)

TICK_SIZE = 0.25
MNQ_TICK_VALUE = 0.50
MAX_CONTRACTS = 20
CT = ZoneInfo('America/Chicago')
ENTRY_TIMEOUT_SEC = 60
STATE_DIR = 'live/state'
ADAPTIVE_STATE = f'{STATE_DIR}/adaptive.json'
ADAPTIVE_JOURNAL = f'{STATE_DIR}/journal.json'


@dataclass
class LiveTrade:
    signal: Signal
    direction: str
    entry_price: float
    stop_price: float
    target_price: float
    risk: float
    entry_time: datetime
    contracts: int
    order_ids: dict = field(default_factory=dict)
    pending: bool = True
    moved_be: bool = False
    partial_taken: bool = False
    trailing: bool = False
    mfe: float = 0.0


class LiveExecutor:
    def __init__(self, cfg: Config, broker):
        self.cfg = cfg
        self.broker = broker
        self.reporter = HubReporter()
        self.alerts = TelegramAlerts()
        self.guard = AdaptiveGuard()

        self.buf = pd.DataFrame()
        self.daily_df = pd.DataFrame()
        self.gen = MultiModelGenerator(cfg)
        self.last_signal_key = None
        self.trade: LiveTrade | None = None

        self.daily_r = 0.0
        self.daily_pnl_usd = 0.0
        self.daily_model_count = {}
        self.cur_date = None
        self.peak_balance = None
        self.start_balance = None
        self.green_days = 0
        self.total_days = 0
        self.consec_losses = 0

        self.daily_win_cap = 2.0
        self.consec_cooldown = 10
        self.dollar_loss_cap = cfg.funded.dollar_loss_cap

    def run(self):
        log.info("Fetching daily bars from API for regime detection...")
        self.daily_df = self.broker.get_daily_bars(days_back=90)
        if not self.daily_df.empty:
            self.daily_df['date'] = pd.to_datetime(self.daily_df['date']).dt.date
        n_daily = len(self.daily_df)
        log.info(f"Daily bars from API: {n_daily}")
        if n_daily < 20:
            log.warning(f"Only {n_daily} daily bars -- regime will default to 'chop'")
        elif n_daily < 50:
            log.info(f"Using EMA20-only regime ({n_daily} bars, need 50 for full)")

        log.info("Fetching recent 1-min bars for signal warmup...")
        self.buf = self.broker.get_bars(minutes_back=20000)
        if self.buf.empty:
            log.warning("No 1-min bars returned, retrying with shorter window...")
            self.buf = self.broker.get_bars(minutes_back=5000)
        if not self.buf.empty:
            log.info(f"Loaded {len(self.buf)} bars "
                     f"({self.buf['datetime'].min()} to {self.buf['datetime'].max()})")
            intraday_daily = build_daily_bars(self.buf)
            intraday_daily['date'] = pd.to_datetime(intraday_daily['date']).dt.date
            if not self.daily_df.empty:
                combined = pd.concat([self.daily_df, intraday_daily], ignore_index=True)
                self.daily_df = combined.drop_duplicates('date', keep='last') \
                    .sort_values('date').reset_index(drop=True)
            else:
                self.daily_df = intraday_daily
            log.info(f"Combined daily bars: {len(self.daily_df)}")
        else:
            log.warning("No 1-min bars available -- will rely on daily bars only")

        acct = self.broker.get_account_info()
        self.start_balance = acct.get('balance', 100000)
        self.peak_balance = self.start_balance
        log.info(f"Account balance: ${self.start_balance:,.0f}")

        self.guard.load_state(ADAPTIVE_STATE)

        risk_str = ' | '.join(f"{m}:${d:,}" for m, d in
                              sorted(self.cfg.funded.model_risk_dollars.items()))
        log.info(f"Model risk tiers: {risk_str}")
        log.info(f"Daily win cap: {self.daily_win_cap}R | "
                 f"Dollar loss cap: ${self.dollar_loss_cap:,.0f} | "
                 f"Consec cooldown: {self.consec_cooldown}")
        log.info(f"Trailing DD: ${self.cfg.funded.trailing_dd:,.0f} | "
                 f"Static threshold: ${self.cfg.funded.static_threshold:,.0f}")
        self.reporter.start()
        self.alerts.bot_started(self.start_balance, 9)
        log.info("Waiting for signals...\n")

        while True:
            try:
                self._tick()
            except KeyboardInterrupt:
                raise
            except Exception:
                log.exception("Tick error")
            time.sleep(30)

    def _tick(self):
        now = datetime.now(CT)
        today = now.date()

        if today != self.cur_date:
            self._new_day(today)

        if now.time() < dt_time(5, 0):
            return
        if now.time() >= dt_time(15, 55):
            if self.trade:
                log.info("Session close -- flattening")
                self._close_trade('session_close')
            if now.time() >= dt_time(16, 0) and now.time() < dt_time(16, 1):
                trades_today = sum(1 for k in self.daily_model_count.values())
                wins = max(0, int(trades_today * (0.5 + self.daily_r / max(trades_today, 1) * 0.1))) if trades_today > 0 else 0
                self.alerts.daily_summary(
                    trades_today, wins, self.daily_r,
                    self.daily_pnl_usd, self.consec_losses)
            return

        latest = self.broker.get_latest_bars(10)
        if latest.empty:
            return
        new_bars = self._merge_bars(latest)
        if new_bars == 0:
            return

        self.daily_df = build_daily_bars(self.buf)
        self.daily_df['date'] = pd.to_datetime(self.daily_df['date']).dt.date

        if self.trade:
            self._manage_trade()
        else:
            self._check_signals()

    def _new_day(self, today):
        if self.cur_date is not None:
            self.total_days += 1
            if self.daily_pnl_usd >= self.cfg.funded.green_day_min:
                self.green_days += 1

        self.cur_date = today
        self.daily_r = 0.0
        self.daily_pnl_usd = 0.0
        self.daily_model_count = {}
        self._alerted_win_cap = False
        self._alerted_dlc = False

        log.info(f"\n{'='*60}")
        log.info(f"New day: {today}")

        acct = self.broker.get_account_info()
        bal = acct.get('balance', self.start_balance)
        if bal > self.peak_balance:
            self.peak_balance = bal

        pnl = bal - self.start_balance
        dd = self.peak_balance - bal
        static = (self.peak_balance - self.start_balance) >= self.cfg.funded.static_threshold
        if static:
            floor = self.start_balance
        else:
            floor = self.peak_balance - self.cfg.funded.trailing_dd
        cushion = bal - floor

        log.info(f"Balance: ${bal:,.0f} | P&L: ${pnl:+,.0f} | Peak: ${self.peak_balance:,.0f}")
        log.info(f"DD floor: ${floor:,.0f} ({'STATIC' if static else 'trailing'}) | "
                 f"Cushion: ${cushion:,.0f} | DD: ${dd:,.0f}")
        log.info(f"Green days: {self.green_days} | Trading days: {self.total_days}")

        if cushion < 1000 and cushion > 0:
            self.alerts.dd_warning(bal, self.peak_balance, dd, self.cfg.funded.trailing_dd)

        if self.green_days >= self.cfg.funded.green_days_per_payout and bal > self.start_balance:
            payout = min(self.cfg.funded.max_payout,
                         (bal - self.start_balance) * self.cfg.funded.payout_balance_pct)
            if payout > 0:
                log.info(f"*** PAYOUT ELIGIBLE: ${payout:,.0f} ***")

        log.info(f"{'='*60}\n")

    def _merge_bars(self, latest: pd.DataFrame) -> int:
        if self.buf.empty:
            self.buf = latest
            return len(latest)
        last_time = self.buf['datetime'].max()
        new = latest[latest['datetime'] > last_time]
        if new.empty:
            return 0
        self.buf = pd.concat([self.buf, new], ignore_index=True)
        if len(self.buf) > 30000:
            self.buf = self.buf.iloc[-30000:].reset_index(drop=True)
        return len(new)

    def _check_signals(self):
        now = datetime.now(CT)
        if now.time() >= dt_time(15, 45):
            return

        if self.daily_r >= self.daily_win_cap:
            if not self._alerted_win_cap:
                self.alerts.win_cap_hit(self.daily_r, self.daily_win_cap)
                self._alerted_win_cap = True
            return

        if self.daily_pnl_usd <= -self.dollar_loss_cap:
            if not self._alerted_dlc:
                self.alerts.dlc_warning(self.daily_pnl_usd, self.dollar_loss_cap)
                self._alerted_dlc = True
            return

        if self.consec_losses >= self.consec_cooldown:
            self.consec_losses = 0
            return

        try:
            signals = self.gen.generate(self.buf, self.daily_df, None)
        except Exception:
            log.exception("Signal generation failed")
            return

        if not signals:
            return

        sig = signals[-1]
        sig_key = f"{sig.model}_{sig.ts}_{sig.direction}"
        if sig_key == self.last_signal_key:
            return

        now = datetime.now(CT)
        sig_age = (now - sig.ts.to_pydatetime().replace(tzinfo=CT)).total_seconds()
        if sig_age > 120:
            return

        model_key = (self.cur_date, sig.model)
        rp = sig.risk_profile
        if rp and self.daily_model_count.get(model_key, 0) >= rp.max_daily:
            return

        self.last_signal_key = sig_key
        self._enter_trade(sig)

    def _enter_trade(self, sig: Signal):
        risk = abs(sig.entry - sig.stop)
        if risk <= 0:
            return

        if sig.risk_ticks < self.cfg.risk.min_risk_ticks:
            log.info(f"    SKIP -- stop too tight: {sig.risk_ticks:.0f} ticks "
                     f"(min {self.cfg.risk.min_risk_ticks})")
            return

        risk_per_contract = sig.risk_ticks * MNQ_TICK_VALUE
        if risk_per_contract <= 0:
            return

        model_risk = self.cfg.funded.model_risk_dollars.get(sig.model, 400)
        qty = min(MAX_CONTRACTS, int(model_risk / risk_per_contract))
        if qty <= 0:
            return

        now_ct = datetime.now(CT)
        atr = self._current_atr()
        avg_atr = self._avg_atr()
        scale, guards = self.guard.pre_entry_check(
            sig.model, now_ct.hour, atr, avg_atr)
        if scale == 0.0:
            log.info(f"    SKIP -- AdaptiveGuard blocked: {guards}")
            return
        if scale < 1.0:
            scaled_qty = max(1, int(qty * scale))
            log.info(f"    AdaptiveGuard scale: {qty} -> {scaled_qty} MNQ ({guards})")
            qty = scaled_qty

        dlc_remaining = self.dollar_loss_cap + self.daily_pnl_usd
        potential_loss = sig.risk_ticks * MNQ_TICK_VALUE * qty
        if potential_loss > dlc_remaining:
            max_qty = int(dlc_remaining / (sig.risk_ticks * MNQ_TICK_VALUE))
            if max_qty <= 0:
                log.info(f"    SKIP -- DLC exhausted: P&L ${self.daily_pnl_usd:,.0f}, "
                         f"remaining ${dlc_remaining:,.0f}")
                return
            log.info(f"    DLC scale: {qty} -> {max_qty} MNQ "
                     f"(remaining ${dlc_remaining:,.0f})")
            qty = max_qty
            potential_loss = sig.risk_ticks * MNQ_TICK_VALUE * qty

        log.info(f"\n>>> SIGNAL: [{sig.model}] {sig.direction.upper()} "
                 f"@ {sig.entry:.2f} ({qty} MNQ, ${model_risk} risk tier)")
        log.info(f"    Stop: {sig.stop:.2f} | Target: {sig.target:.2f} | "
                 f"RR: {sig.rr:.1f} | Risk: {sig.risk_ticks:.0f} ticks | "
                 f"Max loss: ${potential_loss:,.0f}")

        try:
            entry_id = self.broker.place_limit_entry(
                direction=sig.direction,
                qty=qty,
                entry_price=sig.entry,
            )
        except Exception:
            log.exception("Order placement failed")
            return

        self.trade = LiveTrade(
            signal=sig,
            direction=sig.direction,
            entry_price=sig.entry,
            stop_price=sig.stop,
            target_price=sig.target,
            risk=risk,
            entry_time=datetime.now(CT),
            contracts=qty,
            order_ids={'entry': entry_id},
            pending=True,
        )

        model_key = (self.cur_date, sig.model)
        self.daily_model_count[model_key] = \
            self.daily_model_count.get(model_key, 0) + 1

        log.info(f"    PENDING -- {qty} MNQ limit @ {sig.entry:.2f}")

    def _manage_trade(self):
        t = self.trade
        if not t:
            return

        if t.pending:
            self._check_entry_fill()
            return

        pos = self.broker.position_size()
        if pos == 0:
            self._on_trade_closed()
            return

        bar = self.buf.iloc[-1]
        is_long = t.direction == 'long'
        rp = t.signal.risk_profile

        be_trigger = rp.be_trigger_rr if rp else self.cfg.risk.be_trigger_rr
        partial_rr = rp.partial_rr if rp else self.cfg.risk.partial_rr
        trail_pct = rp.trail_pct if rp else 0.0
        time_stop_min = rp.time_stop_minutes if rp else self.cfg.strategy.time_stop_minutes

        if is_long:
            best = bar['high'] - t.entry_price
        else:
            best = t.entry_price - bar['low']

        if best > t.mfe:
            t.mfe = best
            if t.trailing and trail_pct > 0:
                trail_dist = trail_pct * t.risk
                if is_long:
                    new_stop = t.entry_price + t.mfe - trail_dist
                    if new_stop > t.stop_price:
                        t.stop_price = round(new_stop / TICK_SIZE) * TICK_SIZE
                        self.broker.modify_stop(t.stop_price)
                else:
                    new_stop = t.entry_price - t.mfe + trail_dist
                    if new_stop < t.stop_price:
                        t.stop_price = round(new_stop / TICK_SIZE) * TICK_SIZE
                        self.broker.modify_stop(t.stop_price)

        if not t.partial_taken and t.risk > 0 and best >= t.risk * partial_rr:
            t.partial_taken = True
            if trail_pct > 0:
                t.trailing = True
            log.info(f"    Trail activated (MFE >= {partial_rr}R)")

        if not t.moved_be and t.risk > 0 and best >= t.risk * be_trigger:
            t.moved_be = True
            t.stop_price = t.entry_price
            self.broker.modify_stop(t.entry_price)
            log.info(f"    Moved stop to BREAKEVEN @ {t.entry_price:.2f}")

        elapsed = (datetime.now(CT) - t.entry_time).total_seconds() / 60
        if elapsed >= time_stop_min and not t.moved_be:
            log.info(f"    TIME STOP ({time_stop_min} min)")
            self._close_trade('time_stop')
            return

        if datetime.now(CT).time() >= dt_time(15, 55):
            log.info("    SESSION CLOSE")
            self._close_trade('session_close')

    def _check_entry_fill(self):
        t = self.trade
        entry_id = t.order_ids.get('entry')
        status = self.broker.get_order_status(entry_id)

        if status in (ORD_CANCELLED, ORD_REJECTED, ORD_EXPIRED):
            status_name = {ORD_CANCELLED: 'CANCELLED', ORD_REJECTED: 'REJECTED',
                           ORD_EXPIRED: 'EXPIRED'}
            log.info(f"    ENTRY {status_name.get(status, 'REMOVED')} -- resetting")
            self._reset_trade()
            return

        if status == ORD_FILLED:
            t.pending = False
            t.entry_time = datetime.now(CT)
            log.info(f"    FILLED -- {t.contracts} MNQ @ {t.entry_price:.2f}")
            model_risk = self.cfg.funded.model_risk_dollars.get(t.signal.model, 400)
            self.alerts.trade_entry(
                t.signal.model, t.direction, t.entry_price,
                t.stop_price, t.target_price, t.contracts,
                t.signal.risk_ticks, model_risk)
            try:
                exit_ids = self.broker.place_exit_bracket(
                    direction=t.direction,
                    qty=t.contracts,
                    stop_price=t.stop_price,
                    target_price=t.target_price,
                )
                t.order_ids.update(exit_ids)
            except Exception:
                log.exception("Exit bracket placement failed -- flattening")
                self.broker.flatten()
                self._reset_trade()
            return

        elapsed = (datetime.now(CT) - t.entry_time).total_seconds()
        if elapsed >= ENTRY_TIMEOUT_SEC:
            log.info(f"    ENTRY TIMEOUT -- {elapsed:.0f}s, cancelling")
            self.broker.cancel_order(entry_id)
            self._reset_trade()

    def _reset_trade(self):
        self.broker._stop_order_id = None
        self.broker._target_order_id = None
        self.broker._entry_order_id = None
        self.trade = None

    def _on_trade_closed(self):
        t = self.trade
        if not t:
            return

        self.broker.cancel_all_exit_orders()

        try:
            trades = self.broker._post('/api/Trade/search', {
                'accountId': self.broker.account_id,
                'startTimestamp': t.entry_time.strftime('%Y-%m-%dT%H:%M:%SZ'),
            })
            trade_list = trades.get('trades', [])
            pnl = sum((tr.get('profitAndLoss') or 0) for tr in trade_list
                      if tr.get('contractId') == self.broker.contract_id)
        except Exception:
            pnl = 0

        if pnl == 0:
            exit_price = None
            if hasattr(self.broker, 'get_exit_fill_price'):
                exit_price = self.broker.get_exit_fill_price()
            if exit_price is None and not self.buf.empty:
                exit_price = self.buf.iloc[-1]['close']
            if exit_price is not None:
                is_long = t.direction == 'long'
                raw = (exit_price - t.entry_price) if is_long else (t.entry_price - exit_price)
                pnl = raw * t.contracts * MNQ_TICK_VALUE / TICK_SIZE

        risk_usd = t.risk * t.contracts * MNQ_TICK_VALUE / TICK_SIZE
        total_r = pnl / risk_usd if risk_usd > 0 else 0

        self.daily_r += total_r
        self.daily_pnl_usd += pnl

        if total_r <= -0.5:
            self.consec_losses += 1
        else:
            self.consec_losses = 0

        reason = 'target' if total_r > 0.5 else ('stop' if total_r < -0.5 else 'breakeven')
        log.info(f"\n    CLOSED -- {reason} | {total_r:+.2f}R (${pnl:+,.0f})")
        log.info(f"    Daily: {self.daily_r:+.2f}R (${self.daily_pnl_usd:+,.0f}) | "
                 f"Consec losses: {self.consec_losses}")

        self.alerts.trade_exit(
            t.signal.model, t.direction, total_r, pnl, reason,
            self.daily_r, self.daily_pnl_usd)

        self.reporter.report_trade({
            'model': t.signal.model, 'direction': t.direction,
            'entry': t.entry_price, 'stop': t.stop_price, 'target': t.target_price,
            'risk_ticks': t.signal.risk_ticks, 'contracts': t.contracts,
            'total_r': round(total_r, 3), 'pnl_usd': round(pnl, 2),
            'reason': reason, 'entry_time': t.entry_time.isoformat(),
            'exit_time': datetime.now(CT).isoformat(),
            'daily_r': round(self.daily_r, 3), 'daily_pnl': round(self.daily_pnl_usd, 2),
        })

        now_ct = datetime.now(CT)
        self.guard.record_trade(
            t.signal.model, total_r, pnl, now_ct.hour,
            self._current_atr() / max(self._avg_atr(), 0.01))
        self.guard.save_state(ADAPTIVE_STATE)

        self._reset_trade()

    def _close_trade(self, reason: str):
        t = self.trade
        if not t:
            return

        log.info(f"    Closing trade: {reason}")

        if t.pending:
            self.broker.cancel_order(t.order_ids.get('entry'))
            self._reset_trade()
            return

        self.broker.flatten()

        bar = self.buf.iloc[-1] if not self.buf.empty else None
        if bar is not None:
            is_long = t.direction == 'long'
            if is_long:
                raw_pnl = (bar['close'] - t.entry_price)
            else:
                raw_pnl = (t.entry_price - bar['close'])
            total_r = raw_pnl / t.risk if t.risk > 0 else 0
        else:
            total_r = 0

        self.daily_r += total_r
        pnl_usd = total_r * t.risk * t.contracts * MNQ_TICK_VALUE / TICK_SIZE
        self.daily_pnl_usd += pnl_usd

        if total_r <= -0.5:
            self.consec_losses += 1
        else:
            self.consec_losses = 0

        log.info(f"    Result: {total_r:+.2f}R (${pnl_usd:+,.0f}) | "
                 f"Daily: {self.daily_r:+.2f}R (${self.daily_pnl_usd:+,.0f})")

        self.alerts.trade_exit(
            t.signal.model, t.direction, total_r, pnl_usd, reason,
            self.daily_r, self.daily_pnl_usd)

        self.reporter.report_trade({
            'model': t.signal.model, 'direction': t.direction,
            'entry': t.entry_price, 'stop': t.stop_price, 'target': t.target_price,
            'risk_ticks': t.signal.risk_ticks, 'contracts': t.contracts,
            'total_r': round(total_r, 3), 'pnl_usd': round(pnl_usd, 2),
            'reason': reason, 'entry_time': t.entry_time.isoformat(),
            'exit_time': datetime.now(CT).isoformat(),
            'daily_r': round(self.daily_r, 3), 'daily_pnl': round(self.daily_pnl_usd, 2),
        })

        now_ct = datetime.now(CT)
        self.guard.record_trade(
            t.signal.model, total_r, pnl_usd, now_ct.hour,
            self._current_atr() / max(self._avg_atr(), 0.01))
        self.guard.save_state(ADAPTIVE_STATE)

        self._reset_trade()

    def shutdown(self):
        if self.trade:
            if self.trade.pending:
                log.info("Shutdown -- cancelling pending entry")
                self.broker.cancel_order(self.trade.order_ids.get('entry'))
            else:
                log.info("Shutdown -- flattening open position")
                self.broker.flatten()
            self._reset_trade()
        self.guard.save_state(ADAPTIVE_STATE)
        self.guard.save_journal(ADAPTIVE_JOURNAL)
        self.alerts.bot_stopped("shutdown")
        log.info("Executor shutdown complete.")

    def _current_atr(self) -> float:
        if self.buf.empty or len(self.buf) < 15:
            return 0.0
        recent = self.buf.tail(14)
        tr = (recent['high'] - recent['low']).mean()
        return float(tr)

    def _avg_atr(self) -> float:
        if self.buf.empty or len(self.buf) < 200:
            return self._current_atr()
        window = self.buf.tail(200)
        tr = (window['high'] - window['low']).mean()
        return float(tr)
