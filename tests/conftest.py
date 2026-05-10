"""Shared fixtures for live trading robustness tests."""
from __future__ import annotations
import sys, os
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta, timezone, time as dt_time
from dataclasses import dataclass
import pandas as pd
import numpy as np

# Ensure project root is on sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from config import Config
from strategy.models.base import Signal, ModelRiskProfile
from live.broker_topstep import TopStepBroker


# ── Sample Config ──────────────────────────────────────────────────

@pytest.fixture
def cfg():
    """Default Config with standard funded account parameters."""
    return Config()


# ── Sample Bar DataFrames ──────────────────────────────────────────

def _make_bars(n: int, start_price: float = 20000.0,
               start_time: datetime | None = None,
               freq_minutes: int = 1) -> pd.DataFrame:
    """Generate n synthetic 1-min bars."""
    if start_time is None:
        start_time = datetime(2025, 5, 9, 9, 30)
    rng = np.random.default_rng(42)
    rows = []
    price = start_price
    for i in range(n):
        ts = start_time + timedelta(minutes=i * freq_minutes)
        drift = rng.normal(0, 1.5)
        price += drift
        o = round(price, 2)
        h = round(o + abs(rng.normal(0, 2.0)), 2)
        l = round(o - abs(rng.normal(0, 2.0)), 2)
        c = round(o + rng.normal(0, 1.0), 2)
        h = max(h, o, c)
        l = min(l, o, c)
        v = max(10, int(abs(rng.normal(500, 200))))
        rows.append({'datetime': ts, 'open': o, 'high': h, 'low': l,
                     'close': c, 'volume': v})
    df = pd.DataFrame(rows)
    df['datetime'] = pd.to_datetime(df['datetime'])
    return df


@pytest.fixture
def sample_bars():
    """200 bars of synthetic 1-min data starting at 09:30."""
    return _make_bars(200)


@pytest.fixture
def sample_bars_500():
    """500 bars of synthetic 1-min data."""
    return _make_bars(500)


@pytest.fixture
def empty_bars():
    """Empty DataFrame with correct columns."""
    return pd.DataFrame(columns=['datetime', 'open', 'high', 'low',
                                 'close', 'volume'])


@pytest.fixture
def sample_daily_bars():
    """60 days of synthetic daily bars."""
    rng = np.random.default_rng(99)
    rows = []
    price = 20000.0
    base = datetime(2025, 3, 1)
    for d in range(60):
        dt = base + timedelta(days=d)
        if dt.weekday() >= 5:
            continue
        drift = rng.normal(0, 50)
        price += drift
        rows.append({
            'date': dt,
            'open': round(price, 2),
            'high': round(price + abs(rng.normal(0, 80)), 2),
            'low': round(price - abs(rng.normal(0, 80)), 2),
            'close': round(price + rng.normal(0, 30), 2),
            'volume': int(abs(rng.normal(50000, 20000))),
        })
    df = pd.DataFrame(rows)
    df['date'] = pd.to_datetime(df['date'])
    return df


# ── Sample Signal ──────────────────────────────────────────────────

def make_signal(model: str = 'ou_rev', direction: str = 'long',
                entry: float = 20000.0, stop: float = 19950.0,
                target: float = 20150.0, idx: int = 100,
                ts: pd.Timestamp | None = None,
                risk_profile: ModelRiskProfile | None = None) -> Signal:
    """Create a sample Signal for testing."""
    if ts is None:
        ts = pd.Timestamp('2025-05-09 10:00:00')
    risk = abs(entry - stop)
    reward = abs(target - entry)
    return Signal(
        idx=idx, ts=ts, model=model, direction=direction,
        entry=entry, stop=stop, target=target,
        risk_ticks=risk / 0.25, reward_ticks=reward / 0.25,
        rr=reward / risk if risk > 0 else 0,
        tag='test', priority=50,
        risk_profile=risk_profile or ModelRiskProfile(),
    )


@pytest.fixture
def sample_signal():
    return make_signal()


# ── Mock Broker ────────────────────────────────────────────────────

@pytest.fixture
def mock_broker():
    """A TopStepBroker with all API calls mocked out."""
    broker = TopStepBroker(username='test_user', api_key='test_key',
                           env='demo', account_id=12345)
    broker.token = 'fake_token_abc123'
    broker.token_expiry = 9999999999.0
    broker.contract_id = 'CON.F.US.MNQ.U25'
    broker.tick_size = 0.25
    return broker


@pytest.fixture
def mock_broker_connected(mock_broker):
    """Mock broker that's already connected with position/order mocks."""
    mock_broker.get_position = MagicMock(return_value=None)
    mock_broker.position_size = MagicMock(return_value=0)
    mock_broker.get_account_info = MagicMock(return_value={
        'id': 12345, 'name': 'Test Account', 'balance': 100000.0,
    })
    return mock_broker


# ── API Response Templates ─────────────────────────────────────────

@pytest.fixture
def auth_success_response():
    return {'success': True, 'token': 'valid_token_xyz'}


@pytest.fixture
def auth_failure_response():
    return {'success': False, 'errorMessage': 'Invalid credentials'}


@pytest.fixture
def account_search_response():
    return {
        'success': True,
        'accounts': [
            {'id': 12345, 'name': 'TopStep 100K', 'balance': 100000.0},
        ],
    }


@pytest.fixture
def empty_account_response():
    return {'success': True, 'accounts': []}


@pytest.fixture
def order_place_response():
    return {'success': True, 'orderId': 9001}


@pytest.fixture
def bars_response():
    """API-format bar data (pre-rename)."""
    return {
        'success': True,
        'bars': [
            {'t': '2025-05-09T09:30:00Z', 'o': 20000.0, 'h': 20005.0,
             'l': 19995.0, 'c': 20002.0, 'v': 500},
            {'t': '2025-05-09T09:31:00Z', 'o': 20002.0, 'h': 20008.0,
             'l': 19998.0, 'c': 20006.0, 'v': 450},
            {'t': '2025-05-09T09:32:00Z', 'o': 20006.0, 'h': 20010.0,
             'l': 20001.0, 'c': 20009.0, 'v': 600},
        ],
    }
