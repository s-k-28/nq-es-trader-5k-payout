#!/usr/bin/env python3
"""Quick execution test — connects, fetches data, places 1 MNQ, verifies fill, flattens.
Run on your DEMO account to verify the full pipeline before going live.

Usage: python test_execution.py
"""
import os
import sys
import time
import logging
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

sys.path.insert(0, os.path.dirname(__file__))
from live.broker_topstep import TopStepBroker, BUY, SELL, MARKET


def main():
    username = os.getenv('TOPSTEP_USER')
    api_key = os.getenv('TOPSTEP_API_KEY')
    env = os.getenv('TOPSTEP_ENV', 'demo')
    acct_id = os.getenv('TOPSTEP_ACCOUNT_ID')
    acct_id = int(acct_id) if acct_id else None

    if not username or not api_key:
        print("ERROR: Set TOPSTEP_USER and TOPSTEP_API_KEY in .env")
        return

    if env != 'demo':
        print("SAFETY: This test script only runs on demo. Set TOPSTEP_ENV=demo")
        return

    print("=" * 60)
    print("  EXECUTION TEST — TopStepX Demo Account")
    print("=" * 60)

    # Step 1: Connect
    print("\n[1/6] Connecting to TopStepX...")
    broker = TopStepBroker(username, api_key, env=env, account_id=acct_id)
    try:
        broker.connect()
        print("      PASS — authenticated and account found")
    except Exception as e:
        print(f"      FAIL — {e}")
        return

    # Step 2: Account info
    print("\n[2/6] Fetching account info...")
    try:
        info = broker.get_account_info()
        bal = info.get('balance', 0)
        name = info.get('name', 'Unknown')
        print(f"      PASS — {name}, balance: ${bal:,.2f}")
    except Exception as e:
        print(f"      FAIL — {e}")
        return

    # Step 3: Market data
    print("\n[3/6] Fetching recent 1-min bars...")
    try:
        bars = broker.get_bars(minutes_back=30)
        if bars.empty:
            print("      WARN — no bars returned (market may be closed)")
            print("      Cannot test execution without market data.")
            return
        last = bars.iloc[-1]
        print(f"      PASS — {len(bars)} bars, latest: {last['datetime']} "
              f"close={last['close']:.2f}")
    except Exception as e:
        print(f"      FAIL — {e}")
        return

    # Step 4: Place 1-contract market buy
    print("\n[4/6] Placing 1 MNQ market BUY...")
    try:
        resp = broker._post('/api/Order/place', {
            'accountId': broker.account_id,
            'contractId': broker.contract_id,
            'type': MARKET,
            'side': BUY,
            'size': 1,
        })
        order_id = resp.get('orderId')
        print(f"      PASS — order placed (ID: {order_id})")
    except Exception as e:
        print(f"      FAIL — {e}")
        return

    # Step 5: Verify fill
    print("\n[5/6] Checking position (waiting 2s for fill)...")
    time.sleep(2)
    try:
        pos = broker.get_position()
        if pos and pos.get('size', 0) > 0:
            size = pos['size']
            avg = pos.get('averagePrice', 0)
            print(f"      PASS — position: {size} MNQ @ {avg:.2f}")
        else:
            print("      WARN — no position found (may have filled and closed)")
    except Exception as e:
        print(f"      FAIL — {e}")

    # Step 6: Flatten
    print("\n[6/6] Flattening position...")
    try:
        broker.flatten()
        time.sleep(1)
        pos = broker.get_position()
        if pos and pos.get('size', 0) > 0:
            print(f"      WARN — still have {pos['size']} contracts open")
        else:
            print("      PASS — position closed, flat")
    except Exception as e:
        print(f"      FAIL — {e}")

    print("\n" + "=" * 60)
    print("  TEST COMPLETE")
    print("  If all 6 steps passed, your execution pipeline is ready.")
    print("  Switch TOPSTEP_ENV=live in .env when you're ready to go.")
    print("=" * 60)


if __name__ == '__main__':
    main()
