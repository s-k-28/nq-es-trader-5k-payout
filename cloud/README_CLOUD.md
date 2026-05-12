# Cloud Deployment — Oracle Free Tier

Run the NQ trading bot 24/7 for free on Oracle Cloud.

## Step 1: Create Oracle Account
1. Go to https://www.oracle.com/cloud/free/
2. Sign up (need email + credit card for verification, won't be charged)
3. Pick region: **US East (Ashburn)** or **US West (Phoenix)**

## Step 2: Create VM
1. Go to Oracle Cloud Console > Compute > Instances > Create Instance
2. Image: **Ubuntu 22.04** (or latest)
3. Shape: **VM.Standard.A1.Flex** (Always Free ARM)
4. OCPU: **2** | RAM: **12 GB** (use half the free quota)
5. Add your SSH key
6. Create

## Step 3: Connect & Setup
```bash
ssh ubuntu@YOUR_VM_IP
wget https://raw.githubusercontent.com/s-k-28/nq-es-trader-5k-payout/main/cloud/setup_oracle.sh
bash setup_oracle.sh
```

## Step 4: Configure
```bash
# Install IB Gateway
bash ~/ibgateway-install.sh

# Set your IB credentials
nano ~/ibc/config.ini

# Test run
export IB_USER=your_username
export IB_PASS=your_password
bash ~/start_bot.sh
```

## Step 5: Auto-start (survives reboots)
```bash
sudo nano /etc/systemd/system/nq-trader.service
# Set your IB credentials in Environment lines
sudo systemctl enable nq-trader
sudo systemctl start nq-trader
```

## Monitoring
```bash
tail -f ~/bot.log                      # bot output
sudo journalctl -u nq-trader -f        # systemd logs
htop                                    # resource usage
```

## Cost: $0/month forever
