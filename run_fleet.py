#!/usr/bin/env python3
"""Multi-account fleet runner / supervisor.

Spawns N independent funded accounts, each as its own OS subprocess, reusing
the existing entry points (run_ib.py / run_live.py) per account. Each child
gets an ISOLATED state directory via the shared contract env var
``FLEET_STATE_DIR`` (read by live/executor_multi.py), plus its own broker
credentials / IB clientId, so nothing collides.

Why subprocesses (not threads): IB's ib_insync event loop and the executor's
blocking run() loop are single-process designs, and STATE_DIR is resolved at
module import. The only safe isolation boundary is a separate process whose env
carries a distinct FLEET_STATE_DIR. SIGINT is propagated to children on
shutdown so each runs its own executor.shutdown() + broker.disconnect()
(flattening its own positions) — children are NEVER SIGKILLed during a clean
stop.

Usage:
  python3 run_fleet.py                       # uses fleet_config.json
  python3 run_fleet.py --config my.json
  python3 run_fleet.py --shadow              # force shadow mode for ALL accounts
  python3 run_fleet.py --only ib_paper_1,topstep_eval_1
  python3 run_fleet.py --restart             # restart a child if it crashes
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from fleet_config import FleetAccount, load_fleet

log = logging.getLogger('fleet')

DEFAULT_CONFIG = 'fleet_config.json'
# Grace period after SIGINT before we escalate to SIGTERM/SIGKILL on shutdown.
SHUTDOWN_GRACE_SEC = 30.0
# Minimum seconds a child must survive for a crash to be eligible for restart.
RESTART_MIN_UPTIME_SEC = 10.0
# Cap restarts per account so a repeatedly-crashing live account (e.g. expired
# session, bad API state) is NOT reconnected indefinitely — it's left down for
# manual intervention after this many attempts.
MAX_RESTARTS_PER_ACCOUNT = 5
RESTART_BACKOFF_BASE_SEC = 5.0


def build_argv(account: FleetAccount, shadow_override: bool) -> list[str]:
    """Construct the per-account child argv reusing the existing entry points.

    Credentials and the FLEET_STATE_DIR contract are passed via env (see
    FleetAccount.child_env), NOT argv, so secrets never appear in the process
    table.
    """
    shadow = account.shadow or shadow_override
    if account.broker == 'ib':
        argv = [
            sys.executable, 'run_ib.py',
            '--host', str(account.host),
            '--port', str(account.port),
            '--client-id', str(account.client_id),
        ]
    elif account.broker == 'topstep':
        argv = [
            sys.executable, 'run_live.py',
            '--env', str(account.topstep_env),
        ]
    else:  # pragma: no cover - load_fleet validates broker type
        raise ValueError(f"unknown broker {account.broker!r}")

    if shadow:
        argv.append('--shadow')
    return argv


def _spawn(account: FleetAccount, shadow_override: bool,
           project_root: Path) -> subprocess.Popen:
    """Spawn one account subprocess with isolated state dir + env + log file."""
    state_dir = project_root / account.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)

    env = account.child_env(os.environ.copy())
    argv = build_argv(account, shadow_override)

    log_path = state_dir / 'fleet.log'
    logf = open(log_path, 'a', buffering=1)
    logf.write(f"\n===== fleet start {time.strftime('%Y-%m-%dT%H:%M:%S')} "
               f"argv={argv} =====\n")

    log.info("starting account %s (%s) -> %s | log=%s",
             account.name, account.broker, account.state_dir, log_path)

    proc = subprocess.Popen(
        argv,
        env=env,
        cwd=str(project_root),
        stdout=logf,
        stderr=subprocess.STDOUT,
        # New process group so a terminal Ctrl+C to the fleet does not
        # double-signal children; we forward SIGINT explicitly.
        start_new_session=True,
    )
    # Stash the file handle + bookkeeping on the Popen for the supervisor.
    proc._fleet_logf = logf          # type: ignore[attr-defined]
    proc._fleet_account = account    # type: ignore[attr-defined]
    proc._fleet_started_at = time.monotonic()  # type: ignore[attr-defined]
    return proc


def _signal_child(proc: subprocess.Popen, sig: int) -> None:
    """Send a signal to a child's whole process group, best-effort."""
    try:
        os.killpg(os.getpgid(proc.pid), sig)
    except (ProcessLookupError, PermissionError):
        try:
            proc.send_signal(sig)
        except ProcessLookupError:
            pass


def shutdown_all(procs: list[subprocess.Popen],
                 grace_sec: float = SHUTDOWN_GRACE_SEC) -> None:
    """Gracefully stop every child: SIGINT, wait, then escalate.

    SIGINT lets each entry point run KeyboardInterrupt -> executor.shutdown()
    (flatten positions) + broker.disconnect(). Only after the grace period do
    we escalate, so positions are flattened under normal conditions.
    """
    alive = [p for p in procs if p.poll() is None]
    for p in alive:
        acct = getattr(p, '_fleet_account', None)
        log.info("SIGINT -> %s", getattr(acct, 'name', p.pid))
        _signal_child(p, signal.SIGINT)

    deadline = time.monotonic() + grace_sec
    for p in alive:
        remaining = max(0.0, deadline - time.monotonic())
        try:
            p.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            acct = getattr(p, '_fleet_account', None)
            log.warning("SIGTERM -> %s (did not exit on SIGINT)",
                        getattr(acct, 'name', p.pid))
            _signal_child(p, signal.SIGTERM)

    # Final escalation for anything still alive.
    for p in procs:
        if p.poll() is None:
            try:
                p.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                acct = getattr(p, '_fleet_account', None)
                log.error("SIGKILL -> %s (unresponsive)",
                          getattr(acct, 'name', p.pid))
                _signal_child(p, signal.SIGKILL)
        logf = getattr(p, '_fleet_logf', None)
        if logf is not None:
            try:
                logf.close()
            except Exception:
                pass


def supervise(procs: list[subprocess.Popen], *, restart: bool,
              shadow_override: bool, project_root: Path,
              poll_interval: float = 2.0) -> None:
    """Block until all children exit (or Ctrl+C), optionally restarting crashes.

    Restart is bounded: a child must have survived RESTART_MIN_UPTIME_SEC to be
    restarted, so a child that crashes instantly (e.g. bad creds) does not spin.
    """
    restarts: dict[str, int] = {}
    try:
        while True:
            live = 0
            for i, p in enumerate(procs):
                if p.poll() is None:
                    live += 1
                    continue
                acct: FleetAccount = getattr(p, '_fleet_account')
                rc = p.returncode
                logf = getattr(p, '_fleet_logf', None)
                if logf is not None:
                    try:
                        logf.close()
                    except Exception:
                        pass
                uptime = time.monotonic() - getattr(p, '_fleet_started_at', 0.0)
                log.warning("account %s exited rc=%s after %.0fs",
                            acct.name, rc, uptime)
                if restart and rc != 0 and uptime >= RESTART_MIN_UPTIME_SEC:
                    n = restarts.get(acct.name, 0)
                    if n >= MAX_RESTARTS_PER_ACCOUNT:
                        log.error("account %s exceeded %d restarts — leaving it DOWN "
                                  "(manual intervention required)",
                                  acct.name, MAX_RESTARTS_PER_ACCOUNT)
                    else:
                        restarts[acct.name] = n + 1
                        backoff = min(60.0, RESTART_BACKOFF_BASE_SEC * (2 ** n))
                        log.info("restarting account %s (attempt %d/%d) after %.0fs",
                                 acct.name, n + 1, MAX_RESTARTS_PER_ACCOUNT, backoff)
                        time.sleep(backoff)
                        procs[i] = _spawn(acct, shadow_override, project_root)
                        live += 1
            if live == 0:
                log.info("all accounts have exited; fleet done")
                break
            time.sleep(poll_interval)
    except KeyboardInterrupt:
        log.info("Ctrl+C received — gracefully stopping fleet (flattening "
                 "each account's positions)...")
        shutdown_all(procs)
        log.info("fleet shutdown complete")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] [fleet] %(message)s',
        datefmt='%H:%M:%S',
    )
    p = argparse.ArgumentParser(description='Multi-account trading fleet runner')
    p.add_argument('--config', default=DEFAULT_CONFIG,
                   help=f'fleet manifest JSON (default: {DEFAULT_CONFIG})')
    p.add_argument('--shadow', action='store_true',
                   help='force shadow mode for ALL accounts (override manifest)')
    p.add_argument('--only', default=None,
                   help='comma-separated account names to run (subset)')
    p.add_argument('--restart', action='store_true',
                   help='restart an account subprocess if it crashes (rc!=0)')
    args = p.parse_args(argv)

    project_root = Path(__file__).resolve().parent
    accounts = load_fleet(args.config)

    if args.only:
        wanted = {n.strip() for n in args.only.split(',') if n.strip()}
        unknown = wanted - {a.name for a in accounts}
        if unknown:
            log.error("--only references unknown account(s): %s", sorted(unknown))
            return 2
        accounts = [a for a in accounts if a.name in wanted]
        if not accounts:
            log.error("--only selected no accounts")
            return 2

    log.info("launching fleet: %d account(s): %s",
             len(accounts), ', '.join(a.name for a in accounts))

    procs = [_spawn(a, args.shadow, project_root) for a in accounts]
    supervise(procs, restart=args.restart, shadow_override=args.shadow,
              project_root=project_root)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
