"""Comprehensive robustness tests for live trading bot.

Ensures the bot won't crash during Monday live trading with:
- API connection / auth / token refresh
- Data fetching edge cases
- Order lifecycle
- Executor risk controls (DLC, win cap, cooldown)
- AdaptiveGuard learning layer
- Network resilience
- Front month contract calculation
- Shadow mode
- Decision journal
- Orphan detection
- Bracket verification
- Partial profit locking
- Shutdown safety
"""
from __future__ import annotations
import sys, os
import json
import time
import threading
from datetime import datetime, timedelta, time as dt_time
from unittest.mock import MagicMock, patch
from collections import deque
from pathlib import Path

import pytest
import pandas as pd
import numpy as np
import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config import Config
from live.broker_topstep import (
    TopStepBroker, _front_month_mnq, BUY, SELL, LIMIT, STOP,
    ORD_FILLED, ORD_CANCELLED, ORD_REJECTED, ORD_EXPIRED, ORD_OPEN,
)
from live.executor_multi import LiveExecutor, LiveTrade
import live.executor_multi as em
from live.adaptive import AdaptiveGuard, TradeRecord, JournalEntry
from strategy.models.base import Signal, ModelRiskProfile
from tests.conftest import make_signal, _make_bars


# ═══════════════════════════════════════════════════════════════════
# HELPER: Create a LiveExecutor with mocked dependencies
# ═══════════════════════════════════════════════════════════════════

def _make_executor(cfg=None, shadow=False, position_size=0, tmp_path=None):
    """Create a LiveExecutor with fully mocked broker and dependencies."""
    if cfg is None:
        cfg = Config()
    broker = MagicMock()
    broker.position_size = MagicMock(return_value=position_size)
    broker.get_account_info = MagicMock(return_value={
        'id': 12345, 'name': 'Test', 'balance': 100000.0,
    })
    broker.get_daily_bars = MagicMock(return_value=pd.DataFrame())
    broker.get_bars = MagicMock(return_value=_make_bars(200))
    broker.get_latest_bars = MagicMock(return_value=_make_bars(5))
    broker.flatten = MagicMock()
    broker.cancel_order = MagicMock()
    broker.cancel_all_exit_orders = MagicMock()
    broker.place_limit_entry = MagicMock(return_value=9001)
    broker.place_bracket_entry = MagicMock(return_value={
        'entry': 9001, 'stop': 9002, 'target': 9003, 'attached': True})
    broker.place_exit_bracket = MagicMock(return_value={'stop': 9002, 'target': 9003})
    broker.modify_stop = MagicMock()
    broker.get_order_status = MagicMock(return_value=ORD_FILLED)
    broker.contract_id = 'CON.F.US.MNQ.U25'
    broker.tick_size = 0.25
    broker.account_id = 12345

    with patch('live.executor_multi.HubReporter'), \
         patch('live.executor_multi.TelegramAlerts'), \
         patch('live.executor_multi.MultiModelGenerator'):
        exe = LiveExecutor(cfg, broker, shadow=shadow)
    exe.shadow = shadow
    exe.buf = _make_bars(200)
    exe.daily_df = pd.DataFrame()
    exe.cur_date = datetime.now().date()
    exe.start_balance = 100000.0
    exe.peak_balance = 100000.0
    if tmp_path:
        # Redirect ALL state paths into tmp so tests never touch live state
        # (adaptive guard learning, autopsy, decision log, journal).
        em.DECISION_LOG = str(tmp_path / 'decisions.jsonl')
        em.ADAPTIVE_STATE = str(tmp_path / 'adaptive.json')
        em.AUTOPSY_STATE = str(tmp_path / 'autopsy.json')
        em.ADAPTIVE_JOURNAL = str(tmp_path / 'journal.json')
    return exe


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
                    account_search_response,
                    {'success': True, 'contract': {'name': 'MNQ', 'tickSize': 0.25}},
                ]),
                raise_for_status=MagicMock(),
            )
            broker = TopStepBroker(username='test', api_key='key123', env='demo')
            broker.connect()
            assert broker.token == 'valid_token_xyz'
            assert broker.account_id is not None

    def test_connect_bad_credentials(self, auth_failure_response):
        with patch('requests.post') as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value=auth_failure_response),
                raise_for_status=MagicMock(),
            )
            broker = TopStepBroker(username='bad', api_key='wrong', env='demo')
            with pytest.raises(RuntimeError, match="Auth failed"):
                broker.connect()

    def test_connect_no_accounts(self, auth_success_response):
        with patch('requests.post') as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=MagicMock(side_effect=[
                    auth_success_response,
                    {'success': True, 'accounts': []},
                ]),
                raise_for_status=MagicMock(),
            )
            broker = TopStepBroker(username='test', api_key='key', env='demo')
            with pytest.raises(RuntimeError, match="No active accounts"):
                broker.connect()

    def test_token_refresh(self, mock_broker):
        mock_broker.token_expiry = time.time() + 100  # Nearly expired
        with patch('requests.post') as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=MagicMock(return_value={'newToken': 'refreshed_token'}),
                raise_for_status=MagicMock(),
            )
            mock_broker._ensure_token()
            assert mock_broker.token == 'refreshed_token'

    def test_token_still_valid(self, mock_broker):
        mock_broker.token_expiry = time.time() + 50000
        original = mock_broker.token
        mock_broker._ensure_token()
        assert mock_broker.token == original


# ═══════════════════════════════════════════════════════════════════
# 2. RATE LIMITING
# ═══════════════════════════════════════════════════════════════════

class TestRateLimiting:
    def test_429_retry_then_success(self, mock_broker):
        resp_429 = MagicMock(status_code=429)
        resp_429.raise_for_status = MagicMock(
            side_effect=requests.HTTPError(response=resp_429))
        resp_ok = MagicMock(status_code=200,
                            json=MagicMock(return_value={'success': True, 'data': 'ok'}))
        resp_ok.raise_for_status = MagicMock()
        with patch('requests.post', side_effect=[resp_429, resp_ok]):
            with patch('time.sleep'):
                result = mock_broker._post('/api/test', {})
                assert result == {'success': True, 'data': 'ok'}

    def test_429_all_retries_exhausted(self, mock_broker):
        resp_429 = MagicMock(status_code=429)
        resp_429.raise_for_status = MagicMock(
            side_effect=requests.HTTPError(response=resp_429))
        with patch('requests.post', return_value=resp_429):
            with patch('time.sleep'):
                with pytest.raises(requests.HTTPError):
                    mock_broker._post('/api/test', {})


# ═══════════════════════════════════════════════════════════════════
# 3. DATA FETCHING
# ═══════════════════════════════════════════════════════════════════

class TestDataFetching:
    def test_get_bars_success(self, mock_broker, bars_response):
        with patch.object(mock_broker, '_post', return_value=bars_response):
            with patch('time.sleep'):
                df = mock_broker.get_bars(minutes_back=30)
                assert len(df) == 3
                assert 'datetime' in df.columns
                assert 'close' in df.columns

    def test_get_bars_empty(self, mock_broker):
        with patch.object(mock_broker, '_post',
                          return_value={'success': True, 'bars': []}):
            with patch('time.sleep'):
                df = mock_broker.get_bars(minutes_back=30)
                assert df.empty

    def test_get_daily_bars_success(self, mock_broker):
        daily_resp = {
            'success': True,
            'bars': [
                {'t': '2025-05-01T00:00:00Z', 'o': 20000, 'h': 20100,
                 'l': 19900, 'c': 20050, 'v': 50000},
                {'t': '2025-05-02T00:00:00Z', 'o': 20050, 'h': 20200,
                 'l': 19950, 'c': 20150, 'v': 60000},
            ],
        }
        with patch.object(mock_broker, '_post', return_value=daily_resp):
            df = mock_broker.get_daily_bars(days_back=10)
            assert len(df) == 2
            assert 'date' in df.columns

    def test_bars_dedup(self, mock_broker):
        dup_resp = {
            'success': True,
            'bars': [
                {'t': '2025-05-09T09:30:00Z', 'o': 20000, 'h': 20005,
                 'l': 19995, 'c': 20002, 'v': 500},
                {'t': '2025-05-09T09:30:00Z', 'o': 20000, 'h': 20005,
                 'l': 19995, 'c': 20002, 'v': 500},
                {'t': '2025-05-09T09:31:00Z', 'o': 20002, 'h': 20008,
                 'l': 19998, 'c': 20006, 'v': 450},
            ],
        }
        with patch.object(mock_broker, '_post', return_value=dup_resp):
            with patch('time.sleep'):
                df = mock_broker.get_bars(minutes_back=10)
                assert len(df) == 2


# ═══════════════════════════════════════════════════════════════════
# 4. ORDER LIFECYCLE
# ═══════════════════════════════════════════════════════════════════

class TestOrderLifecycle:
    def test_place_limit_entry(self, mock_broker):
        with patch.object(mock_broker, '_post',
                          return_value={'success': True, 'orderId': 101}):
            oid = mock_broker.place_limit_entry('long', 3, 20000.0)
            assert oid == 101

    def test_place_exit_bracket(self, mock_broker):
        call_count = [0]
        def mock_post(endpoint, payload=None):
            call_count[0] += 1
            return {'success': True, 'orderId': 200 + call_count[0]}
        with patch.object(mock_broker, '_post', side_effect=mock_post):
            ids = mock_broker.place_exit_bracket('long', 3, 19950.0, 20150.0)
            assert ids['stop'] == 201
            assert ids['target'] == 202

    def test_modify_stop(self, mock_broker):
        mock_broker._stop_order_id = 201
        with patch.object(mock_broker, '_post', return_value={'success': True}):
            mock_broker.modify_stop(19980.0)

    def test_cancel_order(self, mock_broker):
        with patch.object(mock_broker, '_post', return_value={'success': True}):
            mock_broker.cancel_order(101)

    def test_flatten(self, mock_broker):
        mock_broker._stop_order_id = 201
        mock_broker._target_order_id = 202
        with patch.object(mock_broker, '_post', return_value={'success': True}):
            mock_broker.flatten()
            assert mock_broker._stop_order_id is None
            assert mock_broker._target_order_id is None

    def test_price_rounding(self, mock_broker):
        assert mock_broker._round(20000.13) == 20000.25
        assert mock_broker._round(20000.0) == 20000.0
        assert mock_broker._round(19999.87) == 19999.75


# ═══════════════════════════════════════════════════════════════════
# 5. POSITION TRACKING
# ═══════════════════════════════════════════════════════════════════

class TestPositionTracking:
    def test_position_found(self, mock_broker):
        with patch.object(mock_broker, '_post', return_value={
            'success': True,
            'positions': [{'contractId': 'CON.F.US.MNQ.U25', 'size': 3}],
        }):
            pos = mock_broker.get_position()
            assert pos is not None
            assert pos['size'] == 3

    def test_no_positions(self, mock_broker):
        with patch.object(mock_broker, '_post', return_value={
            'success': True, 'positions': [],
        }):
            pos = mock_broker.get_position()
            assert pos is None

    def test_position_size(self, mock_broker):
        with patch.object(mock_broker, 'get_position',
                          return_value={'size': 5}):
            assert mock_broker.position_size() == 5


# ═══════════════════════════════════════════════════════════════════
# 6. FRONT MONTH CONTRACT
# ═══════════════════════════════════════════════════════════════════

class TestFrontMonth:
    def test_format_check(self):
        result = _front_month_mnq()
        assert result.startswith('CON.F.US.MNQ.')
        code_part = result.split('.')[-1]
        assert len(code_part) == 3  # e.g. U25

    def test_quarterly_code(self):
        with patch('live.broker_topstep.datetime') as mock_dt:
            mock_dt.now.return_value = datetime(2025, 6, 10)
            result = _front_month_mnq()
            assert 'M25' in result


# ═══════════════════════════════════════════════════════════════════
# 7. EXECUTOR RISK CONTROLS
# ═══════════════════════════════════════════════════════════════════

class TestExecutorRiskControls:
    def test_win_cap_blocks(self, tmp_path):
        exe = _make_executor(tmp_path=tmp_path)
        exe.daily_r = 2.5
        exe._alerted_win_cap = False
        now_ct = datetime(2025, 5, 9, 10, 0, 0, tzinfo=em.CT)
        with patch('live.executor_multi.datetime') as mock_dt:
            mock_dt.now.return_value = now_ct
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            exe._check_signals()
        exe.broker.place_limit_entry.assert_not_called()

    def test_dlc_blocks(self, tmp_path):
        exe = _make_executor(tmp_path=tmp_path)
        exe.daily_pnl_usd = -1100.0
        exe._alerted_dlc = False
        now_ct = datetime(2025, 5, 9, 10, 0, 0, tzinfo=em.CT)
        with patch('live.executor_multi.datetime') as mock_dt:
            mock_dt.now.return_value = now_ct
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            exe._check_signals()
        exe.broker.place_limit_entry.assert_not_called()

    def test_consec_cooldown(self, tmp_path):
        exe = _make_executor(tmp_path=tmp_path)
        exe.consec_losses = 10
        with patch('live.executor_multi.datetime') as mock_dt:
            mock_dt.now.return_value = datetime(2025, 5, 9, 10, 0, 0, tzinfo=em.CT)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            exe._check_signals()
        exe.broker.place_limit_entry.assert_not_called()
        assert exe.consec_losses == 0

    def test_signal_age_filter(self, tmp_path):
        exe = _make_executor(tmp_path=tmp_path)
        now_ct = datetime(2025, 5, 9, 10, 5, 0, tzinfo=em.CT)
        sig = make_signal(ts=pd.Timestamp('2025-05-09 10:00:00'))
        with patch('live.executor_multi.datetime') as mock_dt:
            mock_dt.now.return_value = now_ct
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            with patch.object(exe.gen, 'generate', return_value=[sig]):
                exe._check_signals()
        exe.broker.place_limit_entry.assert_not_called()

    def test_model_daily_limit(self, tmp_path):
        exe = _make_executor(tmp_path=tmp_path)
        rp = ModelRiskProfile(max_daily=1)
        sig = make_signal(model='ou_rev',
                          ts=pd.Timestamp('2025-05-09 10:00:00'),
                          risk_profile=rp)
        exe.daily_model_count[(exe.cur_date, 'ou_rev')] = 1
        now_ct = datetime(2025, 5, 9, 10, 0, 30, tzinfo=em.CT)
        with patch('live.executor_multi.datetime') as mock_dt:
            mock_dt.now.return_value = now_ct
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            with patch.object(exe.gen, 'generate', return_value=[sig]):
                exe._check_signals()
        exe.broker.place_limit_entry.assert_not_called()

    def test_enter_zero_risk(self, tmp_path):
        exe = _make_executor(tmp_path=tmp_path)
        sig = make_signal(entry=20000.0, stop=20000.0,
                          ts=pd.Timestamp('2025-05-09 10:00:00'))
        now_ct = datetime(2025, 5, 9, 10, 0, 30, tzinfo=em.CT)
        with patch('live.executor_multi.datetime') as mock_dt:
            mock_dt.now.return_value = now_ct
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            with patch.object(exe.gen, 'generate', return_value=[sig]):
                exe._check_signals()
        exe.broker.place_limit_entry.assert_not_called()

    def test_shutdown_cancels_pending(self):
        exe = _make_executor()
        sig = make_signal()
        exe.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime.now(), contracts=3,
            order_ids={'entry': 9001}, pending=True,
        )
        exe.shutdown()
        exe.broker.cancel_order.assert_called_with(9001)
        assert exe.trade is None

    def test_shutdown_flattens_open(self):
        exe = _make_executor()
        sig = make_signal()
        exe.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime.now(), contracts=3,
            order_ids={'entry': 9001, 'stop': 9002, 'target': 9003},
            pending=False,
        )
        exe.shutdown()
        exe.broker.flatten.assert_called()
        assert exe.trade is None

    def test_merge_bars_dedup(self):
        exe = _make_executor()
        exe.buf = _make_bars(10)
        last_time = exe.buf['datetime'].max()
        # Same timestamps should not be appended
        old_bars = exe.buf.tail(3).copy()
        count = exe._merge_bars(old_bars)
        assert count == 0

    def test_merge_bars_new(self):
        exe = _make_executor()
        exe.buf = _make_bars(10)
        last_time = exe.buf['datetime'].max()
        new_bars = _make_bars(3, start_time=last_time + timedelta(minutes=1))
        count = exe._merge_bars(new_bars)
        assert count == 3
        assert len(exe.buf) == 13


# ═══════════════════════════════════════════════════════════════════
# 8. ADAPTIVE GUARD
# ═══════════════════════════════════════════════════════════════════

class TestAdaptiveGuard:
    def test_initial_confidence(self):
        guard = AdaptiveGuard()
        assert guard.get_confidence('brand_new') == 1.0

    def test_drops_on_losses(self):
        guard = AdaptiveGuard()
        for _ in range(5):
            guard.record_trade('loser', -1.0, -50, 10, 1.0)
        assert guard.get_confidence('loser') == 0.0

    def test_recovers_on_wins(self):
        guard = AdaptiveGuard()
        for _ in range(4):
            guard.record_trade('recov', -1.0, -50, 10, 1.0)
        guard.record_trade('recov', 2.0, 100, 10, 1.0)
        assert guard.get_confidence('recov') > 0.0

    def test_volatility_normal(self):
        scale = AdaptiveGuard.check_volatility(12.0, 12.0)
        assert scale == 1.0

    def test_volatility_too_high(self):
        scale = AdaptiveGuard.check_volatility(30.0, 10.0)
        assert scale <= 0.5

    def test_pre_entry_all_clear(self):
        guard = AdaptiveGuard()
        scale, guards = guard.pre_entry_check('fresh', 10, 12.0, 12.0)
        assert scale == 1.0
        assert guards == []

    def test_pre_entry_unsafe_hour(self):
        guard = AdaptiveGuard()
        # Fill hour 14 with enough losses to flag it
        for _ in range(25):
            guard.record_trade('hr_model', -1.0, -50, 14, 1.0)
        scale, guards = guard.pre_entry_check('hr_model', 14, 12.0, 12.0)
        assert scale == 0.0
        assert any('unsafe_hour' in g for g in guards)


# ═══════════════════════════════════════════════════════════════════
# 9. ADAPTIVE GUARD PERSISTENCE
# ═══════════════════════════════════════════════════════════════════

class TestAdaptiveGuardPersistence:
    def test_save_and_load_state(self, tmp_path):
        guard = AdaptiveGuard()
        guard.record_trade('persist', 1.5, 75, 10, 1.1)
        guard.record_trade('persist', -1.0, -50, 10, 0.9)
        path = str(tmp_path / 'state.json')
        guard.save_state(path)

        guard2 = AdaptiveGuard()
        assert guard2.load_state(path) is True
        assert guard2.get_confidence('persist') > 0.0

    def test_load_missing_file(self):
        guard = AdaptiveGuard()
        result = guard.load_state('/tmp/nonexistent_xyz_abc_999.json')
        assert result is False
        assert guard.get_confidence('any') == 1.0

    def test_save_and_load_journal(self, tmp_path):
        guard = AdaptiveGuard()
        guard.add_journal_entry(
            model='jrnl', direction='long', total_r=1.0, pnl_usd=50,
            confidence=1.0, hour=10, atr_ratio=1.0,
            guards_fired=[], volatility_scale=1.0, final_scale=1.0)
        path = str(tmp_path / 'journal.json')
        guard.save_journal(path)
        assert os.path.exists(path)
        with open(path) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]['model'] == 'jrnl'


# ═══════════════════════════════════════════════════════════════════
# 10. NETWORK RESILIENCE
# ═══════════════════════════════════════════════════════════════════

class TestNetworkResilience:
    def test_connection_refused(self, mock_broker):
        with patch('requests.post',
                   side_effect=requests.ConnectionError("refused")):
            with pytest.raises(requests.ConnectionError):
                mock_broker._post('/api/test', {})

    def test_cancel_survives_error(self, mock_broker):
        with patch.object(mock_broker, '_post',
                          side_effect=RuntimeError("Network error")):
            mock_broker.cancel_order(12345)  # should not raise

    def test_flatten_survives_error(self, mock_broker):
        mock_broker._stop_order_id = None
        mock_broker._target_order_id = None
        with patch.object(mock_broker, '_post',
                          side_effect=RuntimeError("Network error")):
            mock_broker.flatten()  # should not raise


# ═══════════════════════════════════════════════════════════════════
# 11. CONFIG
# ═══════════════════════════════════════════════════════════════════

class TestConfig:
    def test_config_loads(self):
        cfg = Config()
        assert cfg.instrument.tick_size == 0.25
        assert cfg.funded.trailing_dd == 3000.0
        assert cfg.funded.dollar_loss_cap == 800.0

    def test_model_risk_dollars(self):
        cfg = Config()
        assert 'ou_rev' in cfg.funded.model_risk_dollars
        assert cfg.funded.model_risk_dollars['ou_rev'] == 2500


# ═══════════════════════════════════════════════════════════════════
# 12. SHADOW MODE
# ═══════════════════════════════════════════════════════════════════

class TestShadowMode:
    def test_shadow_does_not_place_orders(self, tmp_path):
        exe = _make_executor(shadow=True, tmp_path=tmp_path)
        now_ct = datetime(2025, 5, 9, 10, 0, 30, tzinfo=em.CT)
        sig = make_signal(ts=pd.Timestamp('2025-05-09 10:00:00'))
        with patch('live.executor_multi.datetime') as mock_dt:
            mock_dt.now.return_value = now_ct
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            with patch.object(exe.gen, 'generate', return_value=[sig]):
                with patch.object(exe.guard, 'pre_entry_check',
                                  return_value=(1.0, [])):
                    exe._check_signals()
        exe.broker.place_limit_entry.assert_not_called()
        assert exe.trade is not None  # trade IS set in shadow mode

    def test_shadow_skips_broker_modify_stop(self, tmp_path):
        exe = _make_executor(shadow=True, tmp_path=tmp_path)
        exe.guard = MagicMock()
        sig = make_signal()
        exe.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime(2025, 5, 9, 10, 0, 0, tzinfo=em.CT),
            contracts=3, order_ids={}, pending=False,
        )
        # Shadow mode close_trade skips broker.flatten
        exe._close_trade('test_close')
        exe.broker.flatten.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# 13. DECISION JOURNAL
# ═══════════════════════════════════════════════════════════════════

class TestDecisionJournal:
    def test_stale_signal_logged(self, tmp_path, monkeypatch):
        log_path = str(tmp_path / 'decisions.jsonl')
        monkeypatch.setattr(em, 'DECISION_LOG', log_path)
        exe = _make_executor(tmp_path=tmp_path)
        now_ct = datetime(2025, 5, 9, 10, 5, 0, tzinfo=em.CT)
        sig = make_signal(ts=pd.Timestamp('2025-05-09 10:00:00'))
        with patch('live.executor_multi.datetime') as mock_dt:
            mock_dt.now.return_value = now_ct
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            with patch.object(exe.gen, 'generate', return_value=[sig]):
                exe._check_signals()
        with open(log_path) as f:
            lines = f.readlines()
        assert any(json.loads(l)['reason'] == 'stale_signal' for l in lines)

    def test_entry_attempt_logged(self, tmp_path, monkeypatch):
        log_path = str(tmp_path / 'decisions.jsonl')
        monkeypatch.setattr(em, 'DECISION_LOG', log_path)
        exe = _make_executor(tmp_path=tmp_path)
        now_ct = datetime(2025, 5, 9, 10, 0, 30, tzinfo=em.CT)
        sig = make_signal(ts=pd.Timestamp('2025-05-09 10:00:00'))
        with patch('live.executor_multi.datetime') as mock_dt:
            mock_dt.now.return_value = now_ct
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            with patch.object(exe.gen, 'generate', return_value=[sig]):
                with patch.object(exe.guard, 'pre_entry_check',
                                  return_value=(1.0, [])):
                    exe._check_signals()
        with open(log_path) as f:
            lines = f.readlines()
        assert any(json.loads(l)['action'] == 'entry_attempt' for l in lines)

    def test_win_cap_logged(self, tmp_path, monkeypatch):
        log_path = str(tmp_path / 'decisions.jsonl')
        monkeypatch.setattr(em, 'DECISION_LOG', log_path)
        exe = _make_executor(tmp_path=tmp_path)
        exe.daily_r = 2.5
        exe._alerted_win_cap = False
        now_ct = datetime(2025, 5, 9, 10, 0, 0, tzinfo=em.CT)
        with patch('live.executor_multi.datetime') as mock_dt:
            mock_dt.now.return_value = now_ct
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            exe._check_signals()
        with open(log_path) as f:
            lines = f.readlines()
        record = json.loads(lines[-1])
        assert record['action'] == 'skip_all'
        assert record['reason'] == 'daily_win_cap'


# ═══════════════════════════════════════════════════════════════════
# 14. ORPHAN DETECTION
# ═══════════════════════════════════════════════════════════════════

class TestOrphanDetection:
    def test_orphan_detected_and_flattened(self, tmp_path):
        exe = _make_executor(position_size=2, tmp_path=tmp_path)
        exe.broker.position_size.return_value = 2
        exe._check_orphan_position()
        exe.broker.flatten.assert_called_once()

    def test_no_orphan_no_flatten(self):
        exe = _make_executor(position_size=0)
        exe.broker.position_size.return_value = 0
        exe._check_orphan_position()
        exe.broker.flatten.assert_not_called()


# ═══════════════════════════════════════════════════════════════════
# 15. BRACKET VERIFICATION
# ═══════════════════════════════════════════════════════════════════

class TestBracketVerification:
    def test_bracket_incomplete_flattens(self):
        exe = _make_executor()
        exe.broker.place_exit_bracket.return_value = {'stop': None, 'target': 9003}
        exe.broker.get_order_status.return_value = ORD_FILLED
        sig = make_signal()
        exe.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime.now(), contracts=3,
            order_ids={'entry': 9001}, pending=True,
        )
        exe._check_entry_fill()
        exe.broker.flatten.assert_called()
        assert exe.trade is None

    def test_bracket_exception_flattens(self):
        exe = _make_executor()
        exe.broker.place_exit_bracket.side_effect = RuntimeError("API fail")
        exe.broker.get_order_status.return_value = ORD_FILLED
        sig = make_signal()
        exe.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime.now(), contracts=3,
            order_ids={'entry': 9001}, pending=True,
        )
        exe._check_entry_fill()
        exe.broker.flatten.assert_called()
        assert exe.trade is None

    def test_bracket_success_sets_ids(self):
        exe = _make_executor()
        exe.broker.place_exit_bracket.return_value = {'stop': 9002, 'target': 9003}
        exe.broker.get_order_status.return_value = ORD_FILLED
        sig = make_signal()
        exe.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime.now(), contracts=3,
            order_ids={'entry': 9001}, pending=True,
        )
        exe._check_entry_fill()
        assert exe.trade is not None
        assert exe.trade.order_ids['stop'] == 9002
        assert exe.trade.order_ids['target'] == 9003
        assert exe.trade.pending is False


# ═══════════════════════════════════════════════════════════════════
# 16. PARTIAL PROFIT LOCKING
# ═══════════════════════════════════════════════════════════════════

class TestPartialProfitLocking:
    def test_partial_r_locked_on_mfe(self):
        exe = _make_executor()
        rp = ModelRiskProfile(partial_rr=1.5, partial_pct=0.5, trail_pct=0.3)
        sig = make_signal(entry=20000.0, stop=19950.0, target=20150.0,
                          risk_profile=rp)
        exe.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime(2025, 5, 9, 10, 0, 0, tzinfo=em.CT),
            contracts=3, order_ids={'entry': 9001, 'stop': 9002, 'target': 9003},
            pending=False,
        )
        # Set bar high to trigger partial (MFE >= 1.5 * 50 = 75)
        high_bar = exe.buf.iloc[-1].copy()
        high_bar['high'] = 20000.0 + 80.0
        exe.buf.iloc[-1] = high_bar

        exe.broker.position_size.return_value = 3
        now_ct = datetime(2025, 5, 9, 10, 5, 0, tzinfo=em.CT)
        with patch('live.executor_multi.datetime') as mock_dt:
            mock_dt.now.return_value = now_ct
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            exe._manage_trade()

        assert exe.trade.partial_taken is True
        assert exe.trade.partial_r_locked == pytest.approx(0.75)  # 0.5 * 1.5
        assert exe.trade.trailing is True

    def test_partial_affects_total_r_on_close(self):
        exe = _make_executor()
        exe.guard = MagicMock()
        rp = ModelRiskProfile(partial_rr=1.5, partial_pct=0.5)
        sig = make_signal(entry=20000.0, stop=19950.0, target=20150.0,
                          risk_profile=rp)
        exe.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime(2025, 5, 9, 10, 0, 0, tzinfo=em.CT),
            contracts=3, order_ids={'entry': 9001, 'stop': 9002, 'target': 9003},
            pending=False, partial_taken=True, partial_r_locked=0.75,
        )
        exe.broker.position_size.return_value = 0
        exe.broker._post.return_value = {'trades': []}
        exe.broker.get_exit_fill_price.return_value = None

        last_bar = exe.buf.iloc[-1].copy()
        last_bar['close'] = 20025.0
        exe.buf.iloc[-1] = last_bar

        now_ct = datetime(2025, 5, 9, 10, 10, 0, tzinfo=em.CT)
        with patch('live.executor_multi.datetime') as mock_dt:
            mock_dt.now.return_value = now_ct
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            exe._manage_trade()

        assert exe.trade is None
        # total_r = 0.75 + (1-0.5)*0.5 = 1.0
        assert exe.daily_r == pytest.approx(1.0, abs=0.1)


# ═══════════════════════════════════════════════════════════════════
# 17. SHUTDOWN SAFETY
# ═══════════════════════════════════════════════════════════════════

class TestShutdownSafety:
    def test_shutdown_verifies_flat(self):
        exe = _make_executor()
        sig = make_signal()
        exe.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime.now(), contracts=3,
            order_ids={'entry': 9001, 'stop': 9002, 'target': 9003},
            pending=False,
        )
        # First position_size returns > 0 (still open), then 0
        exe.broker.position_size.side_effect = [2, 0]
        exe.shutdown()
        assert exe.broker.flatten.call_count >= 2

    def test_reset_trade_cleans_state(self):
        exe = _make_executor()
        sig = make_signal()
        exe.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime.now(), contracts=3,
            order_ids={'entry': 9001}, pending=True,
        )
        exe._reset_trade()
        assert exe.trade is None


# ═══════════════════════════════════════════════════════════════════
# 18. ATOMIC BRACKET ENTRY  (no unprotected window)
# ═══════════════════════════════════════════════════════════════════

def _enter_signal(exe, sig, now_ct=None):
    """Drive _enter_trade with guard/analyzer mocked clear."""
    if now_ct is None:
        now_ct = datetime(2025, 5, 9, 10, 0, 30, tzinfo=em.CT)
    exe.guard = MagicMock()
    exe.guard.pre_entry_check.return_value = (1.0, [])
    exe.analyzer = MagicMock()
    exe.analyzer.should_block_regime.return_value = (False, '')
    exe.analyzer.get_recent_failure_pattern.return_value = None
    with patch('live.executor_multi.datetime') as mock_dt:
        mock_dt.now.return_value = now_ct
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        exe._enter_trade(sig)


class TestAtomicBracketEntry:
    def test_entry_places_atomic_bracket(self, tmp_path):
        """Entry must submit stop+target atomically — no naked-position window."""
        exe = _make_executor(tmp_path=tmp_path)
        sig = make_signal(model='ou_rev', entry=20000.0, stop=19950.0,
                          target=20150.0)
        _enter_signal(exe, sig)

        exe.broker.place_bracket_entry.assert_called_once()
        assert exe.trade is not None
        # The protective stop exists the instant the position can fill.
        assert exe.trade.order_ids.get('stop') == 9002
        assert exe.trade.order_ids.get('target') == 9003
        assert exe.trade.bracket_attached is True
        assert exe.trade.pending is True
        # Legacy lone-limit path must NOT be used.
        exe.broker.place_limit_entry.assert_not_called()

    def test_atomic_fill_does_not_replace_bracket(self, tmp_path):
        """On fill, an attached bracket is already live — don't place it again."""
        exe = _make_executor(tmp_path=tmp_path)
        exe.broker.get_order_status.return_value = ORD_FILLED
        sig = make_signal()
        exe.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime.now(), contracts=3,
            order_ids={'entry': 9001, 'stop': 9002, 'target': 9003},
            pending=True, bracket_attached=True,
        )
        exe._check_entry_fill()
        exe.broker.place_exit_bracket.assert_not_called()
        assert exe.trade is not None
        assert exe.trade.pending is False
        assert exe.trade.order_ids['stop'] == 9002

    def test_atomic_incomplete_bracket_halts(self, tmp_path):
        """If the atomic bracket comes back without a stop, flatten + halt."""
        exe = _make_executor(tmp_path=tmp_path)
        exe.broker.place_bracket_entry.return_value = {
            'entry': 9001, 'stop': None, 'target': 9003, 'attached': True}
        sig = make_signal(model='ou_rev')
        _enter_signal(exe, sig)
        exe.broker.flatten.assert_called()
        assert exe.halted is True
        assert exe.trade is None

    def test_pending_cancel_cancels_attached_children(self, tmp_path):
        """A cancelled entry with an attached bracket must cancel the children."""
        exe = _make_executor(tmp_path=tmp_path)
        exe.broker.get_order_status.return_value = ORD_CANCELLED
        sig = make_signal()
        exe.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime.now(), contracts=3,
            order_ids={'entry': 9001, 'stop': 9002, 'target': 9003},
            pending=True, bracket_attached=True,
        )
        exe._check_entry_fill()
        exe.broker.cancel_all_exit_orders.assert_called()
        assert exe.trade is None

    def test_deferred_broker_still_places_bracket_on_fill(self, tmp_path):
        """Brokers without native brackets (attached=False) keep deferred path."""
        exe = _make_executor(tmp_path=tmp_path)
        exe.broker.get_order_status.return_value = ORD_FILLED
        exe.broker.place_exit_bracket.return_value = {'stop': 7002, 'target': 7003}
        sig = make_signal()
        exe.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime.now(), contracts=3,
            order_ids={'entry': 9001}, pending=True, bracket_attached=False,
        )
        exe._check_entry_fill()
        exe.broker.place_exit_bracket.assert_called_once()
        assert exe.trade.order_ids['stop'] == 7002
        assert exe.trade.pending is False


# ═══════════════════════════════════════════════════════════════════
# 19. DLC HARD GUARD  (bound single-trade overrun)
# ═══════════════════════════════════════════════════════════════════

class TestDlcHardGuard:
    def test_hard_stop_force_flattens_on_overrun(self, tmp_path):
        """Open loss past the per-trade hard cap force-flattens immediately."""
        exe = _make_executor(tmp_path=tmp_path)
        exe.guard = MagicMock()
        sig = make_signal(entry=20000.0, stop=19950.0, target=20150.0)
        exe.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime(2025, 5, 9, 10, 0, 0, tzinfo=em.CT),
            contracts=3, order_ids={'entry': 9001, 'stop': 9002, 'target': 9003},
            pending=False, bracket_attached=True, hard_loss_usd=300.0,
        )
        exe.broker.position_size.return_value = 3
        exe.broker.get_exit_fill_price.return_value = None
        # Drive price 100 pts against a long: open loss = 100*2*3 = $600 > $300 cap.
        last = exe.buf.iloc[-1].copy()
        last['low'] = 19900.0
        last['close'] = 19900.0
        last['high'] = 20000.0
        exe.buf.iloc[-1] = last

        now_ct = datetime(2025, 5, 9, 10, 5, 0, tzinfo=em.CT)
        with patch('live.executor_multi.datetime') as mock_dt:
            mock_dt.now.return_value = now_ct
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            exe._manage_trade()

        exe.broker.flatten.assert_called()
        assert exe.trade is None
        assert exe.daily_pnl_usd < 0

    def test_hard_stop_not_triggered_within_cap(self, tmp_path):
        """A normal adverse tick within the hard cap must NOT force-flatten."""
        exe = _make_executor(tmp_path=tmp_path)
        exe.guard = MagicMock()
        sig = make_signal(entry=20000.0, stop=19950.0, target=20150.0)
        exe.trade = LiveTrade(
            signal=sig, direction='long', entry_price=20000.0,
            stop_price=19950.0, target_price=20150.0, risk=50.0,
            entry_time=datetime(2025, 5, 9, 10, 0, 0, tzinfo=em.CT),
            contracts=3, order_ids={'entry': 9001, 'stop': 9002, 'target': 9003},
            pending=False, bracket_attached=True, hard_loss_usd=300.0,
        )
        exe.broker.position_size.return_value = 3
        # 20 pts against a long: open loss = 20*2*3 = $120 < $300 cap.
        last = exe.buf.iloc[-1].copy()
        last['low'] = 19980.0
        last['close'] = 19990.0
        last['high'] = 20000.0
        exe.buf.iloc[-1] = last

        now_ct = datetime(2025, 5, 9, 10, 5, 0, tzinfo=em.CT)
        with patch('live.executor_multi.datetime') as mock_dt:
            mock_dt.now.return_value = now_ct
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            exe._manage_trade()

        exe.broker.flatten.assert_not_called()
        assert exe.trade is not None
