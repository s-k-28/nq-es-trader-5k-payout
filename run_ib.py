#!/usr/bin/env python3
"""
NQ 12-Model Trading Bot — Interactive Brokers Paper/Live
Connects to IB Gateway/TWS, runs all 12 models with model-tiered risk.

Usage:
  python3 run_ib.py                    # paper trading (port 4002)
  python3 run_ib.py --port 4001        # live trading (port 4001)
  python3 run_ib.py --client-id 2      # use different client ID
"""
import argparse
import logging
from dotenv import load_dotenv
from config import Config, InstrumentConfig

load_dotenv()
from live.broker_ib import IBBroker
from live.executor_multi import LiveExecutor
from tiers import apply_tier, VALID_TIERS, TIER_VALIDATION

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)


def main():
    p = argparse.ArgumentParser(description='NQ 12-Model Trading Bot — Interactive Brokers')
    p.add_argument('--host', default='127.0.0.1',
                   help='IB Gateway/TWS host (default: 127.0.0.1)')
    p.add_argument('--port', type=int, default=4002,
                   help='IB Gateway port (4002=paper, 4001=live)')
    p.add_argument('--client-id', type=int, default=1,
                   help='API client ID (default: 1)')
    p.add_argument('--shadow', action='store_true',
                   help='Shadow mode: generate signals but do not trade')
    p.add_argument('--tier', default=None, choices=VALID_TIERS,
                   help='Apply an account-tier preset (25k/50k/100k/150k)')
    args = p.parse_args()

    cfg = Config()
    cfg.instrument = InstrumentConfig('MNQ', 0.25, 0.50, 2.0)
    if args.tier:
        apply_tier(cfg, args.tier)
        v = TIER_VALIDATION[args.tier]
        print(f"  TIER {args.tier.upper()}: DLC ${cfg.funded.dollar_loss_cap:,.0f} | "
              f"trailing DD ${cfg.funded.trailing_dd:,.0f} | max {cfg.risk.max_contracts} MNQ | "
              f"MC ~{v['survival_pct']:.0f}% survival, ~${v['monthly_usd']:,}/mo (proper risk)")

    risk_tiers = cfg.funded.model_risk_dollars
    mode = 'PAPER' if args.port == 4002 else 'LIVE'

    print(f"""
+============================================================+
|          NQ TRADING BOT — INTERACTIVE BROKERS               |
+============================================================+
|  Models:     12 (OU, PD, VWAP, EMA, Kalman, Sweep,         |
|              OR, OU-Lunch, VWAP-Scalp, Open-Drive,          |
|              Trend, PM Mom)                                  |
|  Broker:     IB Gateway @ {args.host}:{args.port:<25}|
|  Mode:       {mode:<47}|
|  Risk tiers: OU $2,500 | PD $900 | rest $400               |
|  Max MNQ:    20 contracts | Min stop: 10 pts (40 ticks)     |
|  Exits:      BE 0.6R | Trail 0.001 | No partials           |
|  Daily:      Win cap 2.0R | DLC $1,000 | CC 10             |
+------------------------------------------------------------+
|  Press Ctrl+C to stop and flatten all positions             |
+============================================================+
""")

    for model, risk in sorted(risk_tiers.items()):
        print(f"  {model:15s}  ${risk:,}")
    print()

    broker = IBBroker(host=args.host, port=args.port, client_id=args.client_id)

    try:
        broker.connect()
    except Exception as e:
        print(f"Connection failed: {e}")
        print("\nMake sure IB Gateway is running:")
        print("  1. Open IB Gateway")
        print("  2. Select 'IB API' + 'Paper Trading'")
        print("  3. Log in")
        print("  4. Check API settings: port 4002, Read-Only unchecked")
        return

    executor = LiveExecutor(cfg, broker, shadow=args.shadow)

    try:
        executor.run()
    except KeyboardInterrupt:
        print("\n\nShutting down...")
        executor.shutdown()
        print("Clean exit.")
    finally:
        broker.disconnect()


if __name__ == '__main__':
    main()
