"""Comprehensive robustness tests for live trading bot.

Ensures the bot won't crash during Monday live trading with:
- API connection / auth / token refresh
- Data fetching edge cases
- Order lifecycle
- Executor risk controls (DLC, win cap, cooldown)
- AdaptiveGuard learning layer
- Network resilience
- Front month contract calculation
"""
from __future__ import annotations
import sys, os
import json
import tempfile
import time
from datetime import datetime, timedelta, time as dt_time
from unittest.mock import MagicMock, patch, PropertyMock
from collections import deque

import pytest
import pandas as pd
import numpy as np
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config import Config
from live.broker_topstep import (
    TopStepBroker, _front_month_mnq, BUY, SELL, LIMIT, MARKET, STOP,
    ORD_FILLED, ORD_CANCELLED, ORD_REJECTED, ORD_EXPIRED, ORD_OPEN,
)
from live.adaptive import AdaptiveGuard, TradeRecord, JournalEntry
from strategy.models.base import Signal, ModelRiskProfile
from tests.conftest import make_signal, _make_bars


# ═══════════════════════════════════════════════════════════════════
# 1. BROKER CONNECTION & AUTH
# ═══════════════════════════════════════════════════════════════════

class TestBrokerAuth:
    def test_connect_success(self, mock_broker, auth_success_response,
                             account_search_response):
        with patch('requests.post') as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=MagicMock(side_effect=[
                    auth_success_response,
                ]),
            )
            mock_broker._post = MagicMock(side_effect=[
                account_search_response,
                {'contract': {'name': 'MNQ', 'tickSize': 0.25}},
            ])
            mock_broker.connect()
            assert mock_broker.token == 'valid_token_xyz'
            assert mock_broker.account_id == 12345

    def test_connect_bad_credentials(self, mock_broker, auth_failure_response):
        with patch('requests.post') as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value=auth_failure_response),
            )
            with pytest.raises(RuntimeError, match="Auth failed"):
                mock_broker.connect()

    def test_connect_no_accounts(self, mock_broker, auth_success_response):
        with patch('requests.post') as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value=auth_success_response),
            )
            mock_broker._post = MagicMock(return_value={'accounts': []})
            with pytest.raises(RuntimeError, match="No active accounts"):
                mock_broker.connect()

    def test_connect_wrong_account_id(self, mock_broker, auth_success_response):
        mock_broker.account_id = 99999
        with patch('requests.post') as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value=auth_success_response),
            )
            mock_broker._post = MagicMock(return_value={
                'accounts': [{'id': 12345, 'name': 'Test', 'balance': 100000}],
            })
            with pytest.raises(RuntimeError, match="Account 99999 not found"):
                mock_broker.connect()

    def test_token_refresh(self, mock_broker):
        mock_broker.token_expiry = time.time() + 100
        with patch('requests.post') as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={'newToken': 'refreshed_token'}),
            )
            mock_broker._ensure_token()
            assert mock_broker.token == 'refreshed_token'

    def test_token_still_valid(self, mock_broker):
        mock_broker.token_expiry = time.time() + 86400
        original_token = mock_broker.token
        mock_broker._ensure_token()
        assert mock_broker.token == original_token


# ═══════════════════════════════════════════════════════════════════
# 2. RATE LIMITING & RETRIES
# ═══════════════════════════════════════════════════════════════════

class TestRateLimiting:
    def test_429_retry_then_success(self, mock_broker):
        resp_429 = MagicMock(status_code=429)
        resp_200 = MagicMock(
            status_code=200,
            json=MagicMock(return_value={'success': True, 'data': 'ok'}),
        )
        resp_200.raise_for_status = MagicMock()
        with patch('requests.post', side_effect=[resp_429, resp_200]):
            with patch('time.sleep'):
                result = mock_broker._post('/api/test')
                assert result['data'] == 'ok'

    def test_429_all_retries_exhausted(self, mock_broker):
        resp_429 = MagicMock(status_code=429)
        resp_429.raise_for_status = MagicMock(
            side_effect=requests.HTTPError("429 Too Many Requests"))
        with patch('requests.post', return_value=resp_429):
            with patch('time.sleep'):
                with pytest.raises(requests.HTTPError):
                    mock_broker._post('/api/test')


# ═══════════════════════════════════════════════════════════════════
# 3. DATA FETCHING
# ═══════════════════════════════════════════════════════════════════

class TestDataFetching:
    def test_get_bars_returns_dataframe(self, mock_broker, bars_response):
        mock_broker._post = MagicMock(return_value=bars_response)
        with patch('time.sleep'):
            df = mock_broker.get_bars(minutes_back=10)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 3
        assert 'datetime' in df.columns
        assert 'close' in df.columns

    def test_get_bars_empty_response(self, mock_broker):
        mock_broker._post = MagicMock(return_value={'bars': []})
        with patch('time.sleep'):
            df = mock_broker.get_bars(minutes_back=10)
        assert df.empty

    def test_get_bars_api_failure(self, mock_broker):
        mock_broker._post = MagicMock(
            side_effect=requests.ConnectionError("Connection refused"))
        with patch('time.sleep'):
            df = mock_broker.get_bars(minutes_back=10)
        assert df.empty

    def test_get_daily_bars_success(self, mock_broker):
        daily_resp = {
            'bars': [
                {'t': '2025-05-08T00:00:00Z', 'o': 20000, 'h': 20100,
                 'l': 19900, 'c': 20050, 'v': 100000},
                {'t': '2025-05-09T00:00:00Z', 'o': 20050, 'h': 20200,
                 'l': 19950, 'c': 20150, 'v': 120000},
            ]
        }
        mock_broker._post = MagicMock(return_value=daily_resp)
        df = mock_broker.get_daily_bars(days_back=5)
        assert len(df) == 2
        assert 'date' in df.columns

    def test_get_daily_bars_failure(self, mock_broker):
        mock_broker._post = MagicMock(side_effect=Exception("API down"))
        df = mock_broker.get_daily_bars(days_back=5)
        assert df.empty

    def test_get_latest_bars(self, mock_broker, bars_response):
        mock_broker._post = MagicMock(return_value=bars_response)
        with patch('time.sleep'):
            df = mock_broker.get_latest_bars(n=5)
        assert isinstance(df, pd.DataFrame)

    def test_bars_dedup_and_sort(self, mock_broker):
        dup_bars = {
            'bars': [
                {'t': '2025-05-09T09:30:00Z', 'o': 20000, 'h': 20005,
                 'l': 19995, 'c': 20002, 'v': 500},
                {'t': '2025-05-09T09:30:00Z', 'o': 20001, 'h': 20006,
                 'l': 19996, 'c': 20003, 'v': 600},
                {'t': '2025-05-09T09:31:00Z', 'o': 20003, 'h': 20008,
                 'l': 19998, 'c': 20005, 'v': 450},
            ]
        }
        mock_broker._post = MagicMock(return_value=dup_bars)
        with patch('time.sleep'):
            df = mock_broker.get_bars(minutes_back=10)
        assert len(df) == 2


# ═══════════════════════════════════════════════════════════════════
# 4. ORDER LIFECYCLE
# ═══════════════════════════════════════════════════════════════════

class TestOrderLifecycle:
    def test_place_limit_entry(self, mock_broker, order_place_response):
        mock_broker._post = MagicMock(return_value=order_place_response)
        oid = mock_broker.place_limit_entry('long', 5, 20000.0)
        assert oid == 9001
        call_args = mock_broker._post.call_args
        payload = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get('payload')
        assert payload['side'] == BUY
        assert payload['size'] == 5

    def test_place_short_entry(self, mock_broker, order_place_response):
        mock_broker._post = MagicMock(return_value=order_place_response)
        mock_broker.place_limit_entry('short', 3, 20100.0)
        call_args = mock_broker._post.call_args
        payload = call_args[0][1]
        assert payload['side'] == SELL

    def test_place_exit_bracket(self, mock_broker):
        mock_broker._post = MagicMock(side_effect=[
            {'orderId': 101},
            {'orderId': 102},
        ])
        ids = mock_broker.place_exit_bracket('long', 5, 19950.0, 20150.0)
        assert ids['stop'] == 101
        assert ids['target'] == 102

    def test_modify_stop(self, mock_broker):
        mock_broker._stop_order_id = 101
        mock_broker._post = MagicMock(return_value={'success': True})
        mock_broker.modify_stop(19975.0)
        call_args = mock_broker._post.call_args
        assert call_args[0][1]['stopPrice'] == 19975.0

    def test_modify_stop_no_order(self, mock_broker):
        mock_broker._stop_order_id = None
        mock_broker.modify_stop(19975.0)

    def test_cancel_order(self, mock_broker):
        mock_broker._post = MagicMock(return_value={'success': True})
        mock_broker.cancel_order(101)
        mock_broker._post.assert_called_once()

    def test_cancel_order_none(self, mock_broker):
        mock_broker.cancel_order(None)

    def test_cancel_order_failure(self, mock_broker):
        mock_broker._post = MagicMock(side_effect=Exception("Network error"))
        mock_broker.cancel_order(101)

    def test_flatten(self, mock_broker):
        mock_broker._stop_order_id = 101
        mock_broker._target_order_id = 102
        mock_broker._post = MagicMock(return_value={'success': True})
        mock_broker.flatten()
        assert mock_broker._stop_order_id is None
        assert mock_broker._target_order_id is None

    def test_flatten_failure(self, mock_broker):
        mock_broker._stop_order_id = None
        mock_broker._target_order_id = None
        mock_broker._post = MagicMock(side_effect=Exception("Error"))
        mock_broker.flatten()

    def test_price_rounding(self, mock_broker):
        assert mock_broker._round(20000.13) == 20000.25
        assert mock_broker._round(20000.0) == 20000.0
        assert mock_broker._round(20000.37) == 20000.25
        assert mock_broker._round(20000.38) == 20000.50


# ═══════════════════════════════════════════════════════════════════
# 5. POSITION TRACKING
# ═══════════════════════════════════════════════════════════════════

class TestPositionTracking:
    def test_get_position_found(self, mock_broker):
        mock_broker._post = MagicMock(return_value={
            'positions': [
                {'contractId': 'CON.F.US.MNQ.U25', 'size': 5},
            ],
        })
        pos = mock_broker.get_position()
        assert pos['size'] == 5

    def test_get_position_wrong_contract(self, mock_broker):
        mock_broker._post = MagicMock(return_value={
            'positions': [
                {'contractId': 'CON.F.US.ES.U25', 'size': 2},
            ],
        })
        pos = mock_broker.get_position()
        assert pos is None

    def test_get_position_no_positions(self, mock_broker):
        mock_broker._post = MagicMock(return_value={'positions': []})
        assert mock_broker.get_position() is None

    def test_position_size_zero(self, mock_broker):
        mock_broker._post = MagicMock(return_value={'positions': []})
        assert mock_broker.position_size() == 0

    def test_position_size_positive(self, mock_broker):
        mock_broker._post = MagicMock(return_value={
            'positions': [
                {'contractId': 'CON.F.US.MNQ.U25', 'size': 10},
            ],
        })
        assert mock_broker.position_size() == 10


# ═══════════════════════════════════════════════════════════════════
# 6. FRONT MONTH CONTRACT
# ═══════════════════════════════════════════════════════════════════

class TestFrontMonth:
    def test_front_month_format(self):
        result = _front_month_mnq()
        assert result.startswith('CON.F.US.MNQ.')
        code = result[-3]
        assert code in 'HMUZ'

    def test_front_month_quarterly(self):
        result = _front_month_mnq()
        quarter_code = result.split('.')[-1][0]
        assert quarter_code in 'HMUZ'

    @patch('live.broker_topstep.datetime')
    def test_front_month_march(self, mock_dt):
        mock_dt.now.return_value = datetime(2025, 3, 1)
        result = _front_month_mnq()
        assert 'H25' in result

    @patch('live.broker_topstep.datetime')
    def test_front_month_march_late(self, mock_dt):
        mock_dt.now.return_value = datetime(2025, 3, 20)
        result = _front_month_mnq()
        assert 'M25' in result

    @patch('live.broker_topstep.datetime')
    def test_front_month_january(self, mock_dt):
        mock_dt.now.return_value = datetime(2025, 1, 5)
        result = _front_month_mnq()
        assert 'H25' in result


# ═══════════════════════════════════════════════════════════════════
# 7. EXECUTOR RISK CONTROLS
# ═══════════════════════════════════════════════════════════════════

class TestExecutorRiskControls:
    def _make_executor(self, cfg, mock_broker_connected):
        from live.executor_multi import LiveExecutor
        with patch.object(LiveExecutor, '__init__', lambda self, *a, **kw: None):
            executor = LiveExecutor.__new__(LiveExecutor)
        executor.cfg = cfg
        executor.broker = mock_broker_connected
        executor.reporter = MagicMock()
        executor.alerts = MagicMock()
        executor.guard = AdaptiveGuard()
        executor.buf = _make_bars(200)
        executor.daily_df = pd.DataFrame()
        executor.gen = MagicMock()
        executor.last_signal_key = None
        executor.trade = None
        executor.daily_r = 0.0
        executor.daily_pnl_usd = 0.0
        executor.daily_model_count = {}
        executor.cur_date = datetime.now().date()
        executor.peak_balance = 100000
        executor.start_balance = 100000
        executor.green_days = 0
        executor.total_days = 0
        executor.consec_losses = 0
        executor.daily_win_cap = 2.0
        executor.consec_cooldown = 10
        executor.dollar_loss_cap = cfg.funded.dollar_loss_cap
        executor._alerted_win_cap = False
        executor._alerted_dlc = False
        return executor

    def test_win_cap_blocks_signals(self, cfg, mock_broker_connected):
        executor = self._make_executor(cfg, mock_broker_connected)
        executor.daily_r = 2.5
        executor._check_signals()
        executor.gen.generate.assert_not_called()

    def test_dlc_blocks_signals(self, cfg, mock_broker_connected):
        executor = self._make_executor(cfg, mock_broker_connected)
        executor.daily_pnl_usd = -600
        executor._check_signals()
        executor.gen.generate.assert_not_called()

    def test_consec_loss_cooldown(self, cfg, mock_broker_connected):
        executor = self._make_executor(cfg, mock_broker_connected)
        executor.consec_losses = 10
        from zoneinfo import ZoneInfo
        ct = ZoneInfo('America/Chicago')
        mock_now = datetime(2025, 5, 9, 10, 0, tzinfo=ct)
        with patch('live.executor_multi.datetime') as mock_dt:
            mock_dt.now.return_value = mock_now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            executor._check_signals()
        executor.gen.generate.assert_not_called()
        assert executor.consec_losses == 0

    def test_signal_age_filter(self, cfg, mock_broker_connected):
        executor = self._make_executor(cfg, mock_broker_connected)
        old_ts = pd.Timestamp(datetime.now() - timedelta(minutes=10))
        old_sig = make_signal(ts=old_ts)
        executor.gen.generate = MagicMock(return_value=[old_sig])

        with patch('live.executor_multi.datetime') as mock_dt:
            from zoneinfo import ZoneInfo
            ct = ZoneInfo('America/Chicago')
            mock_dt.now.return_value = datetime.now(ct)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            executor._check_signals()

        assert executor.trade is None

    def test_model_daily_limit(self, cfg, mock_broker_connected):
        executor = self._make_executor(cfg, mock_broker_connected)
        today = datetime.now().date()
        executor.daily_model_count[(today, 'ou_rev')] = 5
        sig = make_signal(model='ou_rev')
        sig.risk_profile = ModelRiskProfile(max_daily=3)
        executor.gen.generate = MagicMock(return_value=[sig])
        executor._check_signals()
        assert executor.trade is None

    def test_enter_trade_zero_risk(self, cfg, mock_broker_connected):
        executor = self._make_executor(cfg, mock_broker_connected)
        sig = make_signal(entry=20000.0, stop=20000.0)
        executor._enter_trade(sig)
        assert executor.trade is None

    def test_enter_trade_dlc_scale_down(self, cfg, mock_broker_connected):
        executor = self._make_executor(cfg, mock_broker_connected)
        executor.daily_pnl_usd = -400
        sig = make_signal(entry=20000.0, stop=19950.0, target=20150.0)
        mock_broker_connected.place_limit_entry = MagicMock(return_value=9001)
        executor._enter_trade(sig)
        if executor.trade:
            assert executor.trade.contracts > 0

    def test_close_trade_updates_pnl(self, cfg, mock_broker_connected):
        executor = self._make_executor(cfg, mock_broker_connected)
        from live.executor_multi import LiveTrade
        sig = make_signal()
        executor.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime.now(), contracts=5, pending=False,
        )
        mock_broker_connected._post = MagicMock(return_value={
            'trades': [
                {'contractId': 'CON.F.US.MNQ.U25', 'profitAndLoss': 250.0},
            ],
        })
        mock_broker_connected.cancel_all_exit_orders = MagicMock()
        executor._on_trade_closed()
        assert executor.daily_pnl_usd > 0
        assert executor.trade is None

    def test_new_day_resets(self, cfg, mock_broker_connected):
        executor = self._make_executor(cfg, mock_broker_connected)
        executor.daily_r = 1.5
        executor.daily_pnl_usd = 300
        executor.daily_model_count = {('2025-05-08', 'ou_rev'): 3}
        executor.cur_date = datetime(2025, 5, 8).date()
        executor._new_day(datetime(2025, 5, 9).date())
        assert executor.daily_r == 0.0
        assert executor.daily_pnl_usd == 0.0
        assert executor.daily_model_count == {}

    def test_shutdown_cancels_pending(self, cfg, mock_broker_connected):
        executor = self._make_executor(cfg, mock_broker_connected)
        from live.executor_multi import LiveTrade
        sig = make_signal()
        executor.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime.now(), contracts=5, pending=True,
            order_ids={'entry': 9001},
        )
        mock_broker_connected.cancel_order = MagicMock()
        executor.shutdown()
        mock_broker_connected.cancel_order.assert_called_with(9001)
        assert executor.trade is None

    def test_shutdown_flattens_open(self, cfg, mock_broker_connected):
        executor = self._make_executor(cfg, mock_broker_connected)
        from live.executor_multi import LiveTrade
        sig = make_signal()
        executor.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime.now(), contracts=5, pending=False,
        )
        mock_broker_connected.flatten = MagicMock()
        executor.shutdown()
        mock_broker_connected.flatten.assert_called_once()

    def test_merge_bars_dedup(self, cfg, mock_broker_connected):
        executor = self._make_executor(cfg, mock_broker_connected)
        initial_len = len(executor.buf)
        latest = executor.buf.tail(5).copy()
        new_count = executor._merge_bars(latest)
        assert new_count == 0
        assert len(executor.buf) == initial_len

    def test_merge_bars_new_data(self, cfg, mock_broker_connected):
        executor = self._make_executor(cfg, mock_broker_connected)
        initial_len = len(executor.buf)
        last_time = executor.buf['datetime'].max()
        new_bar = pd.DataFrame([{
            'datetime': last_time + timedelta(minutes=1),
            'open': 20100, 'high': 20110, 'low': 20090,
            'close': 20105, 'volume': 500,
        }])
        new_bar['datetime'] = pd.to_datetime(new_bar['datetime'])
        new_count = executor._merge_bars(new_bar)
        assert new_count == 1
        assert len(executor.buf) == initial_len + 1

    def test_merge_bars_cap_at_30000(self, cfg, mock_broker_connected):
        executor = self._make_executor(cfg, mock_broker_connected)
        executor.buf = _make_bars(30000)
        last_time = executor.buf['datetime'].max()
        new_bar = pd.DataFrame([{
            'datetime': last_time + timedelta(minutes=1),
            'open': 20100, 'high': 20110, 'low': 20090,
            'close': 20105, 'volume': 500,
        }])
        new_bar['datetime'] = pd.to_datetime(new_bar['datetime'])
        executor._merge_bars(new_bar)
        assert len(executor.buf) <= 30000


# ═══════════════════════════════════════════════════════════════════
# 8. ADAPTIVE GUARD
# ═══════════════════════════════════════════════════════════════════

class TestAdaptiveGuard:
    def test_initial_confidence_is_one(self):
        guard = AdaptiveGuard()
        assert guard.get_confidence('ou_rev') == 1.0

    def test_confidence_drops_on_losses(self):
        guard = AdaptiveGuard()
        for i in range(6):
            guard.record_trade('ou_rev', -1.0, -50.0, 10, 1.0)
        assert guard.get_confidence('ou_rev') == 0.0

    def test_confidence_recovers_on_wins(self):
        guard = AdaptiveGuard()
        for i in range(5):
            guard.record_trade('ou_rev', -1.0, -50.0, 10, 1.0)
        guard.record_trade('ou_rev', 1.0, 50.0, 10, 1.0)
        conf = guard.get_confidence('ou_rev')
        assert conf > 0.0

    def test_low_win_rate_halves_confidence(self):
        guard = AdaptiveGuard()
        for i in range(8):
            guard.record_trade('ou_rev', -1.0, -50.0, 10, 1.0)
        guard.record_trade('ou_rev', 1.0, 50.0, 10, 1.0)
        guard._consec_losses['ou_rev'] = 0
        guard._confidence['ou_rev'] = 1.0
        guard._update_confidence('ou_rev', False)
        assert guard._confidence['ou_rev'] <= 0.5

    def test_safe_hour_insufficient_data(self):
        guard = AdaptiveGuard()
        assert guard.is_safe_hour(10) is True

    def test_unsafe_hour_after_enough_losses(self):
        guard = AdaptiveGuard()
        for i in range(25):
            guard.record_trade('ou_rev', -1.0, -50.0, 14, 1.0)
        assert guard.is_safe_hour(14) is False

    def test_safe_hour_net_positive(self):
        guard = AdaptiveGuard()
        for i in range(25):
            guard.record_trade('ou_rev', 1.0, 50.0, 10, 1.0)
        assert guard.is_safe_hour(10) is True

    def test_volatility_normal(self):
        scale = AdaptiveGuard.check_volatility(12.0, 12.0)
        assert scale == 1.0

    def test_volatility_too_high(self):
        scale = AdaptiveGuard.check_volatility(30.0, 12.0)
        assert scale == 0.5

    def test_volatility_too_low(self):
        scale = AdaptiveGuard.check_volatility(3.0, 12.0)
        assert scale == 0.5

    def test_volatility_elevated(self):
        scale = AdaptiveGuard.check_volatility(20.0, 12.0)
        assert 0.5 < scale < 1.0

    def test_volatility_zero_avg(self):
        scale = AdaptiveGuard.check_volatility(12.0, 0.0)
        assert scale == 1.0

    def test_pre_entry_check_all_clear(self):
        guard = AdaptiveGuard()
        scale, guards = guard.pre_entry_check('ou_rev', 10, 12.0, 12.0)
        assert scale == 1.0
        assert guards == []

    def test_pre_entry_check_unsafe_hour(self):
        guard = AdaptiveGuard()
        for i in range(25):
            guard._hour_pnl[14].append(-50.0)
        scale, guards = guard.pre_entry_check('ou_rev', 14, 12.0, 12.0)
        assert scale == 0.0
        assert any('unsafe_hour' in g for g in guards)

    def test_pre_entry_check_low_confidence(self):
        guard = AdaptiveGuard()
        guard._confidence['ou_rev'] = 0.5
        scale, guards = guard.pre_entry_check('ou_rev', 10, 12.0, 12.0)
        assert scale == 0.5
        assert any('confidence' in g for g in guards)

    def test_pre_entry_check_combined(self):
        guard = AdaptiveGuard()
        guard._confidence['ou_rev'] = 0.5
        scale, guards = guard.pre_entry_check('ou_rev', 10, 30.0, 12.0)
        assert scale < 0.5
        assert len(guards) >= 2


class TestAdaptiveGuardPersistence:
    def test_save_and_load_state(self):
        guard = AdaptiveGuard()
        guard.record_trade('ou_rev', 1.0, 50.0, 10, 1.0)
        guard.record_trade('ou_rev', -0.8, -40.0, 11, 1.2)
        guard.record_trade('pd_rev', 0.5, 25.0, 9, 0.9)

        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            path = f.name

        try:
            guard.save_state(path)
            guard2 = AdaptiveGuard()
            assert guard2.load_state(path) is True
            assert guard2.get_confidence('ou_rev') == guard.get_confidence('ou_rev')
            assert len(guard2._trades['ou_rev']) == 2
            assert len(guard2._trades['pd_rev']) == 1
        finally:
            os.unlink(path)

    def test_load_state_missing_file(self):
        guard = AdaptiveGuard()
        assert guard.load_state('/nonexistent/path.json') is False

    def test_load_state_corrupt_json(self):
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False, mode='w') as f:
            f.write('{corrupt json!!!')
            path = f.name
        try:
            guard = AdaptiveGuard()
            assert guard.load_state(path) is False
        finally:
            os.unlink(path)

    def test_save_and_load_journal(self):
        guard = AdaptiveGuard()
        guard.add_journal_entry(
            model='ou_rev', direction='long', total_r=1.0, pnl_usd=50.0,
            confidence=1.0, hour=10, atr_ratio=1.0, guards_fired=[],
            volatility_scale=1.0, final_scale=1.0,
        )

        with tempfile.NamedTemporaryFile(suffix='.json', delete=False) as f:
            path = f.name

        try:
            guard.save_journal(path)
            with open(path) as f:
                data = json.load(f)
            assert len(data) == 1
            assert data[0]['model'] == 'ou_rev'

            guard.add_journal_entry(
                model='pd_rev', direction='short', total_r=-0.5, pnl_usd=-25.0,
                confidence=0.8, hour=11, atr_ratio=1.3, guards_fired=['volatility:0.8'],
                volatility_scale=0.8, final_scale=0.64,
            )
            guard.save_journal(path)

            with open(path) as f:
                data = json.load(f)
            assert len(data) == 2
        finally:
            os.unlink(path)

    def test_journal_corrupt_existing(self):
        with tempfile.NamedTemporaryFile(suffix='.json', delete=False, mode='w') as f:
            f.write('NOT JSON')
            path = f.name

        try:
            guard = AdaptiveGuard()
            guard.add_journal_entry(
                model='ou_rev', direction='long', total_r=1.0, pnl_usd=50.0,
                confidence=1.0, hour=10, atr_ratio=1.0, guards_fired=[],
                volatility_scale=1.0, final_scale=1.0,
            )
            guard.save_journal(path)
            with open(path) as f:
                data = json.load(f)
            assert len(data) == 1
        finally:
            os.unlink(path)


class TestTradeRecord:
    def test_win_classification(self):
        rec = TradeRecord(
            timestamp='2025-05-09T10:00:00',
            model='ou_rev', total_r=0.5, pnl_usd=25.0,
            hour=10, atr_ratio=1.0,
        )
        assert rec.win is True

    def test_loss_classification(self):
        rec = TradeRecord(
            timestamp='2025-05-09T10:00:00',
            model='ou_rev', total_r=-0.8, pnl_usd=-40.0,
            hour=10, atr_ratio=1.0,
        )
        assert rec.win is False

    def test_roundtrip_serialization(self):
        rec = TradeRecord(
            timestamp='2025-05-09T10:00:00',
            model='ou_rev', total_r=0.5, pnl_usd=25.0,
            hour=10, atr_ratio=1.0,
        )
        d = rec.to_dict()
        rec2 = TradeRecord.from_dict(d)
        assert rec2.model == rec.model
        assert rec2.total_r == rec.total_r
        assert rec2.win == rec.win


# ═══════════════════════════════════════════════════════════════════
# 9. NETWORK RESILIENCE
# ═══════════════════════════════════════════════════════════════════

class TestNetworkResilience:
    def test_connection_refused(self, mock_broker):
        with patch('requests.post', side_effect=requests.ConnectionError("refused")):
            with pytest.raises(requests.ConnectionError):
                mock_broker.connect()

    def test_timeout(self, mock_broker):
        with patch('requests.post', side_effect=requests.Timeout("timed out")):
            with pytest.raises(requests.Timeout):
                mock_broker.connect()

    def test_500_error(self, mock_broker):
        resp = MagicMock(status_code=500)
        resp.raise_for_status.side_effect = requests.HTTPError("500 Server Error")
        with patch('requests.post', return_value=resp):
            with pytest.raises(requests.HTTPError):
                mock_broker.connect()

    def test_malformed_json(self, mock_broker):
        resp = MagicMock(status_code=200)
        resp.json.side_effect = json.JSONDecodeError("", "", 0)
        with patch('requests.post', return_value=resp):
            with pytest.raises(json.JSONDecodeError):
                mock_broker.connect()

    def test_cancel_order_survives_network_error(self, mock_broker):
        mock_broker._post = MagicMock(
            side_effect=requests.ConnectionError("lost"))
        mock_broker.cancel_order(101)

    def test_flatten_survives_network_error(self, mock_broker):
        mock_broker._stop_order_id = None
        mock_broker._target_order_id = None
        mock_broker._post = MagicMock(
            side_effect=requests.ConnectionError("lost"))
        mock_broker.flatten()


# ═══════════════════════════════════════════════════════════════════
# 10. CONFIG VALIDATION
# ═══════════════════════════════════════════════════════════════════

class TestConfig:
    def test_config_loads(self):
        cfg = Config()
        assert cfg.funded.trailing_dd > 0
        assert cfg.funded.dollar_loss_cap > 0
        assert cfg.funded.green_day_min > 0

    def test_model_risk_dollars(self):
        cfg = Config()
        tiers = cfg.funded.model_risk_dollars
        assert isinstance(tiers, dict)
        assert len(tiers) > 0
        for model, risk in tiers.items():
            assert risk > 0
            assert isinstance(model, str)

    def test_instrument_config(self):
        cfg = Config()
        assert cfg.instrument.tick_size > 0
        assert cfg.instrument.tick_value > 0


# ═══════════════════════════════════════════════════════════════════
# 11. EDGE CASES
# ═══════════════════════════════════════════════════════════════════

class TestEdgeCases:
    def test_empty_buf_merge(self):
        from live.executor_multi import LiveExecutor
        with patch.object(LiveExecutor, '__init__', lambda self, *a, **kw: None):
            executor = LiveExecutor.__new__(LiveExecutor)
        executor.buf = pd.DataFrame()
        new = _make_bars(5)
        new_count = executor._merge_bars(new)
        assert new_count == 5
        assert len(executor.buf) == 5

    def test_signal_with_zero_rr(self):
        sig = make_signal(entry=20000.0, stop=20000.0, target=20000.0)
        assert sig.rr == 0

    def test_adaptive_guard_thread_safety(self):
        import threading
        guard = AdaptiveGuard()
        errors = []

        def record_trades():
            try:
                for i in range(50):
                    r = 1.0 if i % 2 == 0 else -0.5
                    guard.record_trade('ou_rev', r, r * 50, 10, 1.0)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=record_trades) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        conf = guard.get_confidence('ou_rev')
        assert 0.0 <= conf <= 1.0

    def test_adaptive_guard_multiple_models(self):
        guard = AdaptiveGuard()
        models = ['ou_rev', 'pd_rev', 'vwap_rev', 'ema_rev', 'sweep']

        for m in models:
            guard.record_trade(m, 1.0, 50.0, 10, 1.0)

        for m in models:
            assert guard.get_confidence(m) == 1.0

        for i in range(6):
            guard.record_trade('sweep', -1.0, -50.0, 10, 1.0)

        assert guard.get_confidence('sweep') == 0.0
        assert guard.get_confidence('ou_rev') == 1.0

    def test_order_status_open_order(self, mock_broker):
        mock_broker._post = MagicMock(return_value={
            'orders': [{'id': 9001, 'status': ORD_OPEN}],
        })
        status = mock_broker.get_order_status(9001)
        assert status == ORD_OPEN

    def test_order_status_filled_via_position(self, mock_broker):
        mock_broker._post = MagicMock(return_value={'orders': []})
        mock_broker.position_size = MagicMock(return_value=5)
        status = mock_broker.get_order_status(9001)
        assert status == ORD_FILLED

    def test_order_status_cancelled(self, mock_broker):
        mock_broker._post = MagicMock(return_value={'orders': []})
        mock_broker.position_size = MagicMock(return_value=0)
        status = mock_broker.get_order_status(9001)
        assert status == ORD_CANCELLED

    def test_order_status_none_id(self, mock_broker):
        assert mock_broker.get_order_status(None) is None
