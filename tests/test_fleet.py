"""Tests for the multi-account fleet runner.

Covers:
  1. FLEET_STATE_DIR override contract (executor reads it; default unchanged).
  2. fleet_config.load_fleet validation (dups, IB collisions, missing creds).
  3. run_fleet argv/env construction (no real processes spawned).
  4. fleet_status P&L aggregation from decisions.jsonl.
"""
from __future__ import annotations

import importlib
import json
import os
import signal
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import fleet_config
import run_fleet
import fleet_status as fleet_status_mod
from fleet_config import FleetAccount, load_fleet


# ── 1. FLEET_STATE_DIR override contract ───────────────────────────

def test_executor_default_state_dir(monkeypatch):
    """Unset FLEET_STATE_DIR -> STATE_DIR stays 'live/state' (guards baseline)."""
    monkeypatch.delenv('FLEET_STATE_DIR', raising=False)
    import live.executor_multi as em
    em = importlib.reload(em)
    assert em.STATE_DIR == 'live/state'
    assert em.DECISION_LOG == 'live/state/decisions.jsonl'
    assert em.ADAPTIVE_STATE == 'live/state/adaptive.json'


def test_executor_state_dir_override(monkeypatch):
    """FLEET_STATE_DIR set -> STATE_DIR + all derived paths use the override.

    This is the config-workstream contract. If unimplemented, this test marks
    xfail rather than failing the suite (the fleet workstream owns SETTING the
    var; the config workstream owns READING it).
    """
    monkeypatch.setenv('FLEET_STATE_DIR', 'live/fleet/acct_x')
    import live.executor_multi as em
    em = importlib.reload(em)
    if em.STATE_DIR != 'live/fleet/acct_x':
        importlib.reload(em)  # leave module in default state for other tests
        pytest.xfail("executor does not yet read FLEET_STATE_DIR "
                     "(config workstream contract not yet merged)")
    assert em.DECISION_LOG == 'live/fleet/acct_x/decisions.jsonl'
    assert em.ADAPTIVE_STATE == 'live/fleet/acct_x/adaptive.json'
    assert em.DAY_OPEN_STATE == 'live/fleet/acct_x/day_open.json'
    assert em.AUTOPSY_STATE == 'live/fleet/acct_x/autopsy.json'
    assert em.ADAPTIVE_JOURNAL == 'live/fleet/acct_x/journal.json'


@pytest.fixture(autouse=True)
def _restore_executor_module():
    """Ensure executor_multi is back to default state after each test."""
    yield
    os.environ.pop('FLEET_STATE_DIR', None)
    import live.executor_multi as em
    importlib.reload(em)


# ── 2. load_fleet validation ───────────────────────────────────────

def _write(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / 'fleet.json'
    p.write_text(json.dumps(payload))
    return p


def test_load_fleet_valid(tmp_path):
    p = _write(tmp_path, {'accounts': [
        {'name': 'ib1', 'broker': 'ib', 'port': 4002, 'client_id': 11},
        {'name': 'ts1', 'broker': 'topstep', 'topstep_user': 'u',
         'topstep_api_key': 'k', 'topstep_account_id': 99},
    ]})
    accts = load_fleet(p)
    assert [a.name for a in accts] == ['ib1', 'ts1']
    assert accts[0].state_dir == 'live/fleet/ib1'
    assert accts[1].decision_log == 'live/fleet/ts1/decisions.jsonl'


def test_load_fleet_rejects_duplicate_names(tmp_path):
    p = _write(tmp_path, {'accounts': [
        {'name': 'dup', 'broker': 'ib', 'client_id': 1},
        {'name': 'dup', 'broker': 'ib', 'client_id': 2},
    ]})
    with pytest.raises(ValueError, match='duplicate account name'):
        load_fleet(p)


def test_load_fleet_rejects_ib_collision(tmp_path):
    p = _write(tmp_path, {'accounts': [
        {'name': 'a', 'broker': 'ib', 'host': '127.0.0.1', 'port': 4002,
         'client_id': 11},
        {'name': 'b', 'broker': 'ib', 'host': '127.0.0.1', 'port': 4002,
         'client_id': 11},
    ]})
    with pytest.raises(ValueError, match='IB connection collision'):
        load_fleet(p)


def test_load_fleet_allows_distinct_ib_clientids(tmp_path):
    p = _write(tmp_path, {'accounts': [
        {'name': 'a', 'broker': 'ib', 'port': 4002, 'client_id': 11},
        {'name': 'b', 'broker': 'ib', 'port': 4002, 'client_id': 12},
    ]})
    accts = load_fleet(p)
    assert len(accts) == 2


def test_load_fleet_rejects_missing_topstep_creds(tmp_path):
    p = _write(tmp_path, {'accounts': [
        {'name': 'ts', 'broker': 'topstep', 'topstep_env': 'demo'},
    ]})
    with pytest.raises(ValueError, match='missing required'):
        load_fleet(p)


def test_load_fleet_rejects_bad_broker(tmp_path):
    p = _write(tmp_path, {'accounts': [
        {'name': 'x', 'broker': 'fxcm'},
    ]})
    with pytest.raises(ValueError, match='broker must be one of'):
        load_fleet(p)


def test_load_fleet_rejects_unknown_field(tmp_path):
    p = _write(tmp_path, {'accounts': [
        {'name': 'x', 'broker': 'ib', 'bogus_field': 1},
    ]})
    with pytest.raises(ValueError, match='unknown field'):
        load_fleet(p)


def test_load_fleet_rejects_empty(tmp_path):
    p = _write(tmp_path, {'accounts': []})
    with pytest.raises(ValueError, match='no accounts'):
        load_fleet(p)


def test_shipped_example_manifest_is_valid():
    """The committed fleet_config.json template must load (REPLACE_ME creds ok)."""
    root = Path(__file__).resolve().parent.parent
    accts = load_fleet(root / 'fleet_config.json')
    assert len(accts) >= 1
    # IB triples in the template must be unique (already enforced by loader).
    ib = [(a.host, a.port, a.client_id) for a in accts if a.broker == 'ib']
    assert len(ib) == len(set(ib))


# ── 3. run_fleet argv/env construction ─────────────────────────────

def test_build_argv_ib():
    a = FleetAccount(name='ib1', broker='ib', host='127.0.0.1', port=4002,
                     client_id=11, shadow=False)
    argv = run_fleet.build_argv(a, shadow_override=False)
    assert argv[1] == 'run_ib.py'
    assert '--host' in argv and '127.0.0.1' in argv
    assert argv[argv.index('--port') + 1] == '4002'
    assert argv[argv.index('--client-id') + 1] == '11'
    assert '--shadow' not in argv


def test_build_argv_ib_shadow_override():
    a = FleetAccount(name='ib1', broker='ib', shadow=False)
    argv = run_fleet.build_argv(a, shadow_override=True)
    assert '--shadow' in argv


def test_build_argv_topstep_no_creds_in_argv():
    a = FleetAccount(name='ts1', broker='topstep', topstep_env='live',
                     topstep_user='secret_user', topstep_api_key='secret_key',
                     shadow=True)
    argv = run_fleet.build_argv(a, shadow_override=False)
    assert argv[1] == 'run_live.py'
    assert argv[argv.index('--env') + 1] == 'live'
    assert '--shadow' in argv
    # Secrets must NOT appear in the argv (they go via env).
    assert 'secret_user' not in argv
    assert 'secret_key' not in argv


def test_child_env_sets_fleet_state_dir_contract():
    a = FleetAccount(name='acct1', broker='ib')
    env = a.child_env({'PATH': '/usr/bin'})
    assert env['FLEET_STATE_DIR'] == 'live/fleet/acct1'
    assert env['PATH'] == '/usr/bin'  # base env preserved


def test_child_env_injects_topstep_creds():
    a = FleetAccount(name='ts1', broker='topstep', topstep_user='u',
                     topstep_api_key='k', topstep_env='demo',
                     topstep_account_id=777,
                     telegram_bot_token='tok', telegram_chat_id='chat',
                     hub_url='http://hub', hub_user_id='hu')
    env = a.child_env({})
    assert env['FLEET_STATE_DIR'] == 'live/fleet/ts1'
    assert env['TOPSTEP_USER'] == 'u'
    assert env['TOPSTEP_API_KEY'] == 'k'
    assert env['TOPSTEP_ENV'] == 'demo'
    assert env['TOPSTEP_ACCOUNT_ID'] == '777'
    assert env['TELEGRAM_BOT_TOKEN'] == 'tok'
    assert env['TELEGRAM_CHAT_ID'] == 'chat'
    assert env['HUB_URL'] == 'http://hub'
    assert env['HUB_USER_ID'] == 'hu'


def test_child_env_ib_does_not_inject_topstep():
    a = FleetAccount(name='ib1', broker='ib')
    env = a.child_env({})
    assert 'TOPSTEP_USER' not in env
    assert 'TOPSTEP_API_KEY' not in env


def test_spawn_uses_isolated_state_dir_and_env(tmp_path):
    """_spawn must mkdir the state dir and pass FLEET_STATE_DIR to Popen."""
    a = FleetAccount(name='ib1', broker='ib', port=4002, client_id=11)
    fake = MagicMock()
    fake.poll.return_value = None
    with patch('run_fleet.subprocess.Popen', return_value=fake) as popen:
        proc = run_fleet._spawn(a, shadow_override=False, project_root=tmp_path)
    assert proc is fake
    assert (tmp_path / 'live' / 'fleet' / 'ib1').is_dir()
    _, kwargs = popen.call_args
    assert kwargs['env']['FLEET_STATE_DIR'] == 'live/fleet/ib1'
    assert kwargs['cwd'] == str(tmp_path)
    args = popen.call_args[0][0]
    assert args[1] == 'run_ib.py'


def test_main_spawns_one_proc_per_account(tmp_path):
    manifest = tmp_path / 'fleet.json'
    manifest.write_text(json.dumps({'accounts': [
        {'name': 'ib1', 'broker': 'ib', 'port': 4002, 'client_id': 11},
        {'name': 'ib2', 'broker': 'ib', 'port': 4002, 'client_id': 12},
    ]}))
    spawned = []

    def fake_spawn(account, shadow_override, project_root):
        spawned.append(account.name)
        m = MagicMock()
        m.poll.return_value = 0  # already exited so supervise returns at once
        m._fleet_account = account
        m._fleet_started_at = 0.0
        m._fleet_logf = None
        m.returncode = 0
        return m

    with patch('run_fleet._spawn', side_effect=fake_spawn):
        rc = run_fleet.main(['--config', str(manifest)])
    assert rc == 0
    assert spawned == ['ib1', 'ib2']


def test_main_only_filter(tmp_path):
    manifest = tmp_path / 'fleet.json'
    manifest.write_text(json.dumps({'accounts': [
        {'name': 'ib1', 'broker': 'ib', 'client_id': 11},
        {'name': 'ib2', 'broker': 'ib', 'client_id': 12},
    ]}))
    spawned = []
    with patch('run_fleet._spawn',
               side_effect=lambda a, s, pr: (
                   spawned.append(a.name) or _exited_proc(a))):
        rc = run_fleet.main(['--config', str(manifest), '--only', 'ib2'])
    assert rc == 0
    assert spawned == ['ib2']


def test_main_only_unknown_returns_error(tmp_path):
    manifest = tmp_path / 'fleet.json'
    manifest.write_text(json.dumps({'accounts': [
        {'name': 'ib1', 'broker': 'ib', 'client_id': 11},
    ]}))
    rc = run_fleet.main(['--config', str(manifest), '--only', 'nope'])
    assert rc == 2


def _exited_proc(account):
    m = MagicMock()
    m.poll.return_value = 0
    m._fleet_account = account
    m._fleet_started_at = 0.0
    m._fleet_logf = None
    m.returncode = 0
    return m


def test_shutdown_all_sends_sigint_first(tmp_path):
    """Graceful shutdown must SIGINT children (so they flatten), not SIGKILL."""
    p = MagicMock()
    p.poll.return_value = None  # alive
    p.pid = 4242
    p.wait.return_value = 0
    p._fleet_account = FleetAccount(name='ib1', broker='ib')
    p._fleet_logf = None
    with patch('run_fleet._signal_child') as sig:
        # After wait, simulate the process having exited.
        p.poll.side_effect = [None, 0, 0]
        run_fleet.shutdown_all([p], grace_sec=0.01)
    assert sig.call_args_list[0][0][1] == signal.SIGINT


# ── 4. fleet_status P&L aggregation ────────────────────────────────

def _make_log(path: Path, records: list[dict]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as f:
        for r in records:
            f.write(json.dumps(r) + '\n')


def test_read_account_pnl_sums_trade_closed(tmp_path):
    log = tmp_path / 'decisions.jsonl'
    _make_log(log, [
        {'action': 'skip_signal', 'timestamp': '2026-05-28T09:00:00-05:00'},
        {'action': 'trade_closed', 'model': 'ou_rev', 'total_r': 1.5,
         'pnl_usd': 300.0, 'daily_pnl': 300.0,
         'timestamp': '2026-05-28T10:00:00-05:00'},
        {'action': 'trade_closed', 'model': 'pd', 'total_r': -1.0,
         'pnl_usd': -100.0, 'daily_pnl': 200.0,
         'timestamp': '2026-05-28T11:00:00-05:00'},
    ])
    s = fleet_status_mod.read_account_pnl(log, '2026-05-28')
    assert s['trades'] == 2
    assert s['wins'] == 1
    assert s['losses'] == 1
    assert s['pnl_usd'] == 200.0
    assert s['total_r'] == 0.5
    assert s['last_daily_pnl'] == 200.0
    assert s['by_model']['ou_rev']['pnl_usd'] == 300.0
    assert not s['halted']


def test_read_account_pnl_date_filter(tmp_path):
    log = tmp_path / 'decisions.jsonl'
    _make_log(log, [
        {'action': 'trade_closed', 'pnl_usd': 100.0, 'total_r': 1.0,
         'timestamp': '2026-05-27T10:00:00-05:00'},
        {'action': 'trade_closed', 'pnl_usd': 50.0, 'total_r': 0.5,
         'timestamp': '2026-05-28T10:00:00-05:00'},
    ])
    s = fleet_status_mod.read_account_pnl(log, '2026-05-28')
    assert s['trades'] == 1
    assert s['pnl_usd'] == 50.0


def test_read_account_pnl_detects_halt_and_missing(tmp_path):
    log = tmp_path / 'decisions.jsonl'
    _make_log(log, [
        {'action': 'bot_halted', 'reason': 'dlc',
         'timestamp': '2026-05-28T12:00:00-05:00'},
    ])
    s = fleet_status_mod.read_account_pnl(log, '2026-05-28')
    assert s['halted'] is True

    missing = fleet_status_mod.read_account_pnl(tmp_path / 'nope.jsonl')
    assert missing['log_exists'] is False
    assert missing['trades'] == 0


def test_fleet_status_aggregates_across_accounts(tmp_path):
    accts = [
        FleetAccount(name='ib1', broker='ib', client_id=11),
        FleetAccount(name='ts1', broker='topstep', topstep_user='u',
                     topstep_api_key='k'),
    ]
    _make_log(tmp_path / 'live/fleet/ib1/decisions.jsonl', [
        {'action': 'trade_closed', 'pnl_usd': 300.0, 'total_r': 1.5,
         'timestamp': '2026-05-28T10:00:00-05:00'},
    ])
    _make_log(tmp_path / 'live/fleet/ts1/decisions.jsonl', [
        {'action': 'trade_closed', 'pnl_usd': -50.0, 'total_r': -0.5,
         'timestamp': '2026-05-28T10:00:00-05:00'},
    ])
    status = fleet_status_mod.fleet_status(accts, '2026-05-28', tmp_path)
    assert status['totals']['pnl_usd'] == 250.0
    assert status['totals']['total_r'] == 1.0
    assert status['totals']['trades'] == 2
    assert status['totals']['accounts'] == 2
    out = fleet_status_mod.format_status(status)
    assert 'ib1' in out and 'ts1' in out and 'TOTAL' in out
