#!/bin/bash
# =============================================================================
# Device Monitor Script
# =============================================================================
# Runs on the HOST machine (not in Docker).
# Scans the local network for devices and writes the count to a JSON file
# that the Discord bot reads via a Docker volume mount.
#
# Also controls Samba service based on device count (original functionality).
#
# SETUP:
#   1. Place this script on your server
#   2. Make executable:  chmod +x device_monitor.sh
#   3. Create data dir:  sudo mkdir -p /opt/bot-data
#   4. Run as service or via cron (needs sudo for arp-scan)
#
# Default devices (7 total):
#   - 2 smart lights
#   - 1 display pi
#   - 1 mntain server
#   - 1 router
#   - 1 lock
#   - 1 printer (sometimes on, sometimes off)
#   - 1 speaker    
# =============================================================================

# Configuration
DATA_DIR="/opt/bot-data"
DATA_FILE="$DATA_DIR/device_count.json"
INTERFACE="eth0"
MIN_DEVICES_SAMBA=11
SCAN_INTERVAL=30  # seconds

# Ensure data directory exists
mkdir -p "$DATA_DIR"

write_device_data() {
    local count=$1
    local timestamp
    timestamp=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

    cat > "$DATA_FILE" <<EOF
{
    "device_count": $count,
    "timestamp": "$timestamp",
    "interface": "$INTERFACE"
}
EOF
}

echo "🔍 Device Monitor started on interface: $INTERFACE"
echo "📁 Writing data to: $DATA_FILE"
echo "⏱️  Scan interval: ${SCAN_INTERVAL}s"

while true; do
    # Use arp-scan to detect devices on the network and count them by IP address
    device_count=$(sudo arp-scan --interface="$INTERFACE" --localnet | grep -oE '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | wc -l)

    # Write device count to JSON file for the bot
    write_device_data "$device_count"

    echo "$(date): Detected $device_count devices"

    # Control Samba service based on device count (original functionality)
    if (( device_count >= MIN_DEVICES_SAMBA )); then
        echo "Device count ($device_count) meets or exceeds threshold. Starting Samba service."
        systemctl start smbd.service
    else
        echo "Device count ($device_count) below threshold. Stopping Samba service."
        systemctl stop smbd.service
    fi

    sleep "$SCAN_INTERVAL"
done
