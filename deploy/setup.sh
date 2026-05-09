#!/bin/bash
# Oracle Cloud Free Tier — NQ-ES Trader setup
# Run this on a fresh Ubuntu 22.04+ instance:
#   chmod +x setup.sh && ./setup.sh

set -e

echo "=== NQ-ES Trader — Server Setup ==="

sudo apt update && sudo apt install -y python3 python3-pip python3-venv tmux git

mkdir -p ~/nq-es-trader
cd ~/nq-es-trader

python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install pandas numpy matplotlib tabulate requests python-dotenv

if [ ! -f .env ]; then
  cat > .env <<'ENVEOF'
TOPSTEP_USER=your_username_here
TOPSTEP_API_KEY=your_api_key_here
TOPSTEP_ENV=demo
ENVEOF
  echo ""
  echo ">>> EDIT .env with your TopStepX credentials:"
  echo ">>>   nano ~/nq-es-trader/.env"
  echo ""
fi

sudo tee /etc/systemd/system/nq-trader.service > /dev/null <<'SVCEOF'
[Unit]
Description=NQ-ES Trader Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/nq-es-trader
ExecStart=/home/ubuntu/nq-es-trader/.venv/bin/python3 run_live.py
Restart=on-failure
RestartSec=30
StandardOutput=append:/home/ubuntu/nq-es-trader/bot.log
StandardError=append:/home/ubuntu/nq-es-trader/bot.log

[Install]
WantedBy=multi-user.target
SVCEOF

sudo systemctl daemon-reload
sudo systemctl enable nq-trader

echo ""
echo "=== Setup complete ==="
echo ""
echo "Next steps:"
echo "  1. Upload your code:  scp -r ./* ubuntu@<server-ip>:~/nq-es-trader/"
echo "  2. Edit credentials:  nano ~/nq-es-trader/.env"
echo "  3. Start the bot:     sudo systemctl start nq-trader"
echo "  4. Check status:      sudo systemctl status nq-trader"
echo "  5. View live logs:    tail -f ~/nq-es-trader/bot.log"
echo "  6. Stop the bot:      sudo systemctl stop nq-trader"
echo ""
echo "The bot will auto-restart on crash and auto-start on server reboot."
