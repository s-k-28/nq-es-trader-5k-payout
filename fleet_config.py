"""Multi-account fleet configuration: schema + loader.

A "fleet" is a set of independent funded accounts, each run as its own OS
subprocess (see run_fleet.py). This module only defines the config schema and
the loader/validator — it spawns nothing and imports no broker/executor code,
so it is safe to import from tests.

Each account gets an ISOLATED state directory exposed to its subprocess via the
shared contract env var ``FLEET_STATE_DIR`` (read by live/executor_multi.py).
State therefore never collides between accounts.

The fleet manifest is a JSON file shaped like::

    {
      "accounts": [
        {"name": "ib_paper_1", "broker": "ib", "host": "127.0.0.1",
         "port": 4002, "client_id": 11, "shadow": false},
        {"name": "topstep_eval_1", "broker": "topstep", "topstep_env": "demo",
         "topstep_user": "...", "topstep_api_key": "...",
         "topstep_account_id": 12345678, "shadow": false}
      ]
    }
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

# The shared contract env var name. Setting this for a subprocess overrides
# STATE_DIR in live/executor_multi.py so each account is isolated. Keep this
# name EXACTLY in sync with the executor's os.environ.get('FLEET_STATE_DIR').
FLEET_STATE_DIR_ENV = 'FLEET_STATE_DIR'

# Base directory under which each account's isolated state dir is created.
FLEET_STATE_ROOT = 'live/fleet'

VALID_BROKERS = ('ib', 'topstep')


@dataclass
class FleetAccount:
    """One funded account in the fleet.

    The ``name`` drives the isolated state directory and must be unique across
    the fleet. Broker-specific fields are only required for that broker type;
    the loader validates them.
    """
    name: str
    broker: str  # 'ib' | 'topstep'
    shadow: bool = False
    tier: str | None = None  # account-tier preset: '25k'|'50k'|'100k'|'150k'

    # IB-only
    host: str = '127.0.0.1'
    port: int = 4002
    client_id: int = 1

    # TopStep-only
    topstep_user: str | None = None
    topstep_api_key: str | None = None
    topstep_env: str = 'demo'
    topstep_account_id: int | None = None

    # Optional integrations (per-account isolation of notifications/reporting)
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    hub_url: str | None = None
    hub_user_id: str | None = None

    @property
    def state_dir(self) -> str:
        """Isolated state directory for this account (FLEET_STATE_DIR value)."""
        return f'{FLEET_STATE_ROOT}/{self.name}'

    @property
    def decision_log(self) -> str:
        """Path to this account's decisions.jsonl (matches executor layout)."""
        return f'{self.state_dir}/decisions.jsonl'

    def child_env(self, base_env: dict[str, str]) -> dict[str, str]:
        """Build the environment for this account's subprocess.

        Starts from ``base_env`` (typically os.environ.copy()), injects the
        FLEET_STATE_DIR contract plus per-account creds/integrations. Only
        non-None values are injected so the manifest can defer to ambient env.
        """
        env = dict(base_env)
        env[FLEET_STATE_DIR_ENV] = self.state_dir

        if self.broker == 'topstep':
            if self.topstep_user is not None:
                env['TOPSTEP_USER'] = self.topstep_user
            if self.topstep_api_key is not None:
                env['TOPSTEP_API_KEY'] = self.topstep_api_key
            if self.topstep_env is not None:
                env['TOPSTEP_ENV'] = self.topstep_env
            if self.topstep_account_id is not None:
                env['TOPSTEP_ACCOUNT_ID'] = str(self.topstep_account_id)

        if self.telegram_bot_token is not None:
            env['TELEGRAM_BOT_TOKEN'] = self.telegram_bot_token
        if self.telegram_chat_id is not None:
            env['TELEGRAM_CHAT_ID'] = self.telegram_chat_id
        if self.hub_url is not None:
            env['HUB_URL'] = self.hub_url
        if self.hub_user_id is not None:
            env['HUB_USER_ID'] = self.hub_user_id

        return env


_ALLOWED_KEYS = set(FleetAccount.__dataclass_fields__.keys())


def _account_from_dict(raw: dict) -> FleetAccount:
    if 'name' not in raw or not str(raw.get('name', '')).strip():
        raise ValueError("account is missing required 'name'")
    name = raw['name']
    broker = raw.get('broker')
    if broker not in VALID_BROKERS:
        raise ValueError(
            f"account '{name}': broker must be one of {VALID_BROKERS}, got {broker!r}")

    unknown = set(raw) - _ALLOWED_KEYS
    if unknown:
        raise ValueError(
            f"account '{name}': unknown field(s) {sorted(unknown)}")

    return FleetAccount(**{k: raw[k] for k in raw})


def _validate(accounts: list[FleetAccount]) -> None:
    if not accounts:
        raise ValueError('fleet manifest contains no accounts')

    # Unique names (drives state dir isolation).
    seen_names: set[str] = set()
    for a in accounts:
        if a.name in seen_names:
            raise ValueError(f"duplicate account name: {a.name!r}")
        seen_names.add(a.name)

    # Unique IB (host, port, client_id) triples — collisions silently break
    # IB API connections, so this is safety-critical.
    seen_ib: dict[tuple, str] = {}
    for a in accounts:
        if a.broker != 'ib':
            continue
        key = (a.host, a.port, a.client_id)
        if key in seen_ib:
            raise ValueError(
                f"IB connection collision: accounts {seen_ib[key]!r} and "
                f"{a.name!r} share host/port/client_id {key}")
        seen_ib[key] = a.name

    # Per-broker required creds.
    for a in accounts:
        if a.broker == 'topstep':
            missing = [k for k in ('topstep_user', 'topstep_api_key')
                       if not getattr(a, k)]
            if missing:
                raise ValueError(
                    f"topstep account '{a.name}': missing required {missing} "
                    f"(set in manifest or ambient env is NOT consulted for "
                    f"validation — provide explicit values)")


def load_fleet(path: str | Path) -> list[FleetAccount]:
    """Load + validate a fleet manifest JSON file.

    Raises ValueError on any validation failure (duplicate names, IB
    host/port/client_id collisions, missing TopStep creds, unknown fields).
    """
    path = Path(path)
    with open(path, 'r') as f:
        data = json.load(f)

    if not isinstance(data, dict) or 'accounts' not in data:
        raise ValueError("manifest must be an object with an 'accounts' list")
    raw_accounts = data['accounts']
    if not isinstance(raw_accounts, list):
        raise ValueError("'accounts' must be a list")

    accounts = [_account_from_dict(r) for r in raw_accounts]
    _validate(accounts)
    return accounts
