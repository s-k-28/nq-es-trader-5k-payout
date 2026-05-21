#!/bin/bash
# Double-click this file to start the trading bot (Mac)
cd "$(dirname "$0")"
echo "Starting NQ Trading Bot..."
python3 run_live.py --env live
read -p "Press Enter to close..."
