#!/bin/bash
# Deploy code to Oracle Cloud server
# Usage: ./deploy.sh <server-ip>
#   Example: ./deploy.sh 129.213.45.67

set -e

if [ -z "$1" ]; then
  echo "Usage: ./deploy.sh <server-ip>"
  echo "  Example: ./deploy.sh 129.213.45.67"
  exit 1
fi

SERVER="ubuntu@$1"
REMOTE_DIR="~/nq-es-trader"

echo "=== Deploying to $SERVER ==="

rsync -avz --exclude '.venv' --exclude '__pycache__' --exclude '.env' \
  --exclude '*.png' --exclude 'data/*.csv' --exclude '.git' \
  --exclude 'deploy' \
  -e ssh \
  "$(dirname "$0")/../" "$SERVER:$REMOTE_DIR/"

echo ""
echo "=== Code deployed. Restarting bot... ==="
ssh "$SERVER" "sudo systemctl restart nq-trader"
ssh "$SERVER" "sudo systemctl status nq-trader --no-pager"

echo ""
echo "=== Done. View logs: ssh $SERVER 'tail -f ~/nq-es-trader/bot.log' ==="
