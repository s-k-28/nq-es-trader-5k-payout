#!/bin/bash
# Oracle Cloud Free Tier — Hub server setup
# Run this on a free Oracle instance to host the live dashboard.
#   chmod +x setup_hub.sh && ./setup_hub.sh

set -e

echo "=== NQ-ES Trader — Hub Setup ==="

sudo apt update && sudo apt install -y python3 python3-pip

mkdir -p ~/nq-hub
cp server.py index.html ~/nq-hub/

sudo tee /etc/systemd/system/nq-hub.service > /dev/null <<'SVCEOF'
[Unit]
Description=NQ-ES Trader Live Hub
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/nq-hub
ExecStart=/usr/bin/python3 /home/ubuntu/nq-hub/server.py
Restart=always
RestartSec=10
StandardOutput=append:/home/ubuntu/nq-hub/hub.log
StandardError=append:/home/ubuntu/nq-hub/hub.log

[Install]
WantedBy=multi-user.target
SVCEOF

sudo systemctl daemon-reload
sudo systemctl enable nq-hub
sudo systemctl start nq-hub

# Open port 9090 in iptables (Oracle free tier also needs Security List rule in console)
sudo iptables -I INPUT -p tcp --dport 9090 -j ACCEPT

echo ""
echo "=== Hub running at http://$(curl -s ifconfig.me):9090 ==="
echo ""
echo "IMPORTANT: Also open port 9090 in Oracle Cloud Console:"
echo "  Networking > Virtual Cloud Networks > your VCN > Security Lists > Add Ingress Rule"
echo "  Source CIDR: 0.0.0.0/0  |  Destination Port: 9090  |  Protocol: TCP"
echo ""
echo "Then tell each bot user to add to their .env:"
echo "  HUB_URL=http://$(curl -s ifconfig.me):9090"
echo "  HUB_USER_ID=their_name"
