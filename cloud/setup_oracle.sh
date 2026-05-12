#!/bin/bash
# ============================================================
# Oracle Cloud Free Tier Setup — NQ Trading Bot + IB Gateway
# Run this on a fresh Ubuntu ARM VM (Oracle Always Free)
# ============================================================
set -e

echo "============================================================"
echo "  NQ Trading Bot — Oracle Cloud Setup"
echo "============================================================"
echo ""

# ── 1. System updates ──────────────────────────────────────
echo "[1/7] Updating system..."
sudo apt-get update -y && sudo apt-get upgrade -y

# ── 2. Install dependencies ───────────────────────────────
echo "[2/7] Installing dependencies..."
sudo apt-get install -y \
    python3 python3-pip python3-venv \
    unzip wget curl git \
    xvfb x11vnc xterm \
    default-jre \
    tmux htop

# ── 3. Clone the trading bot ──────────────────────────────
echo "[3/7] Cloning trading bot..."
cd ~
if [ -d "nq-es-trader-5k-payout" ]; then
    cd nq-es-trader-5k-payout && git pull
else
    git clone https://github.com/s-k-28/nq-es-trader-5k-payout.git
    cd nq-es-trader-5k-payout
fi

# ── 4. Install Python dependencies ────────────────────────
echo "[4/7] Installing Python packages..."
pip3 install --user -r requirements.txt 2>/dev/null || {
    pip3 install --user \
        pandas numpy scipy \
        ib_insync \
        python-dotenv \
        requests
}

# ── 5. Download IB Gateway ─────────────────────────────────
echo "[5/7] Downloading IB Gateway..."
cd ~
IB_GATEWAY_VERSION="10.30.1t"
if [ ! -d "Jts" ]; then
    # Download IB Gateway for Linux
    wget -q "https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-aarch64.sh" \
        -O ibgateway-install.sh || \
    wget -q "https://download2.interactivebrokers.com/installers/ibgateway/stable-standalone/ibgateway-stable-standalone-linux-x64.sh" \
        -O ibgateway-install.sh
    chmod +x ibgateway-install.sh
    echo "Run: bash ibgateway-install.sh"
    echo "  -> Accept defaults, install to ~/Jts"
fi

# ── 6. Install IBC (auto-login for IB Gateway) ────────────
echo "[6/7] Installing IBC..."
cd ~
if [ ! -d "ibc" ]; then
    IBC_VERSION="3.19.0"
    wget -q "https://github.com/IbcAlpha/IBC/releases/download/${IBC_VERSION}/IBCLinux-${IBC_VERSION}.zip" \
        -O ibc.zip
    mkdir -p ibc
    unzip -o ibc.zip -d ibc
    chmod +x ibc/*.sh ibc/*/*.sh 2>/dev/null
fi

# ── 7. Create config and start scripts ─────────────────────
echo "[7/7] Creating config files..."

# IBC config
mkdir -p ~/ibc
cat > ~/ibc/config.ini << 'IBCEOF'
# IBC Configuration
LogToConsole=yes
FIX=no
IbLoginId=YOUR_IB_USERNAME
IbPassword=YOUR_IB_PASSWORD
TradingMode=paper
ExistingSessionDetectedAction=primary
AcceptIncomingConnectionAction=accept
AcceptNonBrokerageAccountWarning=yes
AllowBlindTrading=yes
DismissPasswordExpiryWarning=yes
DismissNSEComplianceNotice=yes
ReadOnlyLogin=no
IBCEOF

# Start script for IB Gateway + Bot
cat > ~/start_bot.sh << 'STARTEOF'
#!/bin/bash
# Start IB Gateway headless + Trading Bot

# Kill any existing instances
pkill -f ibgateway 2>/dev/null
pkill -f run_ib.py 2>/dev/null
sleep 2

# Start virtual display
export DISPLAY=:1
Xvfb :1 -screen 0 1024x768x24 &
sleep 2

# Start IB Gateway via IBC
cd ~/ibc
bash gatewaystart.sh -inline \
    --gateway \
    --mode paper \
    --user $IB_USER \
    --pw $IB_PASS &

echo "Waiting 30s for IB Gateway to connect..."
sleep 30

# Start the trading bot
cd ~/nq-es-trader-5k-payout
python3 run_ib.py 2>&1 | tee -a ~/bot.log

STARTEOF
chmod +x ~/start_bot.sh

# Stop script
cat > ~/stop_bot.sh << 'STOPEOF'
#!/bin/bash
pkill -f run_ib.py
pkill -f ibgateway
pkill -f Xvfb
echo "Bot and IB Gateway stopped."
STOPEOF
chmod +x ~/stop_bot.sh

# Systemd service (auto-restart on crash)
sudo tee /etc/systemd/system/nq-trader.service > /dev/null << SVCEOF
[Unit]
Description=NQ Trading Bot
After=network.target

[Service]
Type=simple
User=$USER
Environment=DISPLAY=:1
Environment=IB_USER=YOUR_IB_USERNAME
Environment=IB_PASS=YOUR_IB_PASSWORD
ExecStartPre=/usr/bin/Xvfb :1 -screen 0 1024x768x24
ExecStart=/bin/bash /home/$USER/start_bot.sh
Restart=on-failure
RestartSec=60

[Install]
WantedBy=multi-user.target
SVCEOF

echo ""
echo "============================================================"
echo "  SETUP COMPLETE!"
echo "============================================================"
echo ""
echo "  Next steps:"
echo ""
echo "  1. Install IB Gateway:"
echo "     bash ~/ibgateway-install.sh"
echo ""
echo "  2. Edit IBC config with your IB credentials:"
echo "     nano ~/ibc/config.ini"
echo "     -> Change YOUR_IB_USERNAME and YOUR_IB_PASSWORD"
echo ""
echo "  3. Edit the systemd service:"
echo "     sudo nano /etc/systemd/system/nq-trader.service"
echo "     -> Change YOUR_IB_USERNAME and YOUR_IB_PASSWORD"
echo ""
echo "  4. Test run:"
echo "     export IB_USER=your_username"
echo "     export IB_PASS=your_password"
echo "     bash ~/start_bot.sh"
echo ""
echo "  5. Enable auto-start on boot:"
echo "     sudo systemctl enable nq-trader"
echo "     sudo systemctl start nq-trader"
echo ""
echo "  6. Check logs:"
echo "     tail -f ~/bot.log"
echo "     sudo journalctl -u nq-trader -f"
echo ""
echo "============================================================"
