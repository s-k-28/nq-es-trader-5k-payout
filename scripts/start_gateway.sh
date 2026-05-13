#!/bin/bash
# Start IB Gateway headless with IBC on GCP VM
# Usage: bash ~/nq-es-trader-5k-payout/scripts/start_gateway.sh

# Start Xvfb if not running
if ! pgrep -x Xvfb > /dev/null; then
    Xvfb :1 -screen 0 1024x768x24 &
    sleep 1
fi
export DISPLAY=:1

GWDIR="$HOME/Jts/ibgateway/1045"
IBC_INI="$HOME/ibc/config.ini"
IBC_JAR="$HOME/ibc/IBC.jar"

cd "$GWDIR"

java \
  --add-opens=java.base/java.util=ALL-UNNAMED \
  --add-opens=java.base/java.util.concurrent=ALL-UNNAMED \
  --add-exports=java.base/sun.util=ALL-UNNAMED \
  --add-exports=java.desktop/com.sun.java.swing.plaf.motif=ALL-UNNAMED \
  --add-opens=java.desktop/java.awt=ALL-UNNAMED \
  --add-opens=java.desktop/java.awt.dnd=ALL-UNNAMED \
  --add-opens=java.desktop/javax.swing=ALL-UNNAMED \
  --add-opens=java.desktop/javax.swing.event=ALL-UNNAMED \
  --add-opens=java.desktop/javax.swing.plaf.basic=ALL-UNNAMED \
  --add-opens=java.desktop/javax.swing.table=ALL-UNNAMED \
  --add-opens=java.desktop/sun.awt=ALL-UNNAMED \
  --add-exports=java.desktop/sun.awt.X11=ALL-UNNAMED \
  --add-exports=java.desktop/sun.swing=ALL-UNNAMED \
  --add-opens=jdk.management/com.sun.management.internal=ALL-UNNAMED \
  -Xmx512m \
  -cp "jars/*:$IBC_JAR" \
  ibcalpha.ibc.IbcGateway "$IBC_INI"
