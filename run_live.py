#!/usr/bin/env python3
"""
NQ 12-Model Trading Bot -- TopStepX 100K
Connects to TopStepX via REST API, runs all 12 models with model-tiered risk.

Usage:
  python run_live.py              # uses .env file
  python run_live.py --env demo   # demo mode
"""
import argparse
import logging
import os
from dotenv import load_dotenv
from config import Config, InstrumentConfig
from live.broker_topstep import TopStepBroker
from live.executor_multi import LiveExecutor

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)


def main():
    p = argparse.ArgumentParser(description='NQ Trading Bot -- TopStepX 100K')
    p.add_argument('--env', default=os.getenv('TOPSTEP_ENV', 'demo'),
                   choices=['demo', 'live'])
    p.add_argument('--shadow', action='store_true',
                   help='Run in shadow mode (no orders placed, full decision logging)')
    args = p.parse_args()

    username = os.getenv('TOPSTEP_USER')
    api_key = os.getenv('TOPSTEP_API_KEY')

    if not username or not api_key:
        print("ERROR: Missing credentials.")
        print("Copy .env.example to .env and fill in your TopStepX credentials:")
        print("  TOPSTEP_USER=your_username")
        print("  TOPSTEP_API_KEY=your_api_key")
        return

    cfg = Config()
    cfg.instrument = InstrumentConfig('MNQ', 0.25, 0.50, 2.0)

    risk_tiers = cfg.funded.model_risk_dollars

    print(f"""
+============================================================+
|          NQ TRADING BOT -- TOPSTEP 100K XFA                 |
+============================================================+
|  Models:     12 (OU, PD, VWAP, OR, OU-Lunch, VWAP-Scalp,   |
|              Open-Drive, Trend, PM Mom, EMA, Kalman, Sweep)  |
|  Mode:       {args.env.upper():<47}|
|  Risk tiers: OU $2,500 | PD $900 | rest $400               |
|  Max MNQ:    20 contracts | Min stop: 25 pts               |
|  Exits:      BE 0.6R | Trail 0.001 | No partials           |
|  Daily:      Win cap 2.0R | DLC $800 | CC 10               |
|  Account:    $3K trailing DD, static at $3K peak            |
|  Payouts:    $5K max, 30% bal, 5 green days ($150+)         |
+------------------------------------------------------------+
|  Press Ctrl+C to stop and flatten all positions             |
+============================================================+
""")

    for model, risk in sorted(risk_tiers.items()):
        print(f"  {model:15s}  ${risk:,}")
    print()

    acct_id = os.getenv('TOPSTEP_ACCOUNT_ID')
    acct_id = int(acct_id) if acct_id else None
    broker = TopStepBroker(username, api_key, env=args.env, account_id=acct_id)

    try:
        broker.connect()
    except Exception as e:
        print(f"Connection failed: {e}")
        print("Check your .env credentials and try again.")
        return

    executor = LiveExecutor(cfg, broker, shadow=args.shadow)

    try:
        executor.run()
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        executor.shutdown()
        print("Clean exit.")


if __name__ == '__main__':
    main()
