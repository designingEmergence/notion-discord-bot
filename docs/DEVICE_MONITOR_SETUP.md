# Device Monitor Setup Guide

The `/how-busy` command relies on a host-side script that scans the local network and writes the device count to a JSON file. The bot reads this file via a Docker volume mount.

## Architecture

```
┌─────────────────────────┐         ┌──────────────────────────┐
│   HOST SERVER            │         │   DOCKER (bot container) │
│                          │         │                          │
│  device_monitor.sh       │         │  /how-busy command       │
│   ↓ writes               │  bind   │   ↑ reads                │
│  /opt/bot-data/           │ mount   │  /app/host_data/         │
│   device_count.json      │────────→│   device_count.json      │
└─────────────────────────┘         └──────────────────────────┘
```

## Host Setup

### 1. Install `arp-scan`

```bash
sudo apt-get install arp-scan    # Debian/Ubuntu
```

### 2. Create the data directory

```bash
sudo mkdir -p /opt/bot-data
```

### 3. Copy the monitor script

```bash
sudo cp scripts/device_monitor.sh /opt/device_monitor.sh
sudo chmod +x /opt/device_monitor.sh
```

### 4. Configure the script (optional)

Edit `/opt/device_monitor.sh` and adjust:
- `INTERFACE` — your network interface (default: `eth0`)
- `MIN_DEVICES_SAMBA` — Samba threshold (default: `11`)
- `SCAN_INTERVAL` — seconds between scans (default: `30`)

### 5. Run as a systemd service (recommended)

Create `/etc/systemd/system/device-monitor.service`:

```ini
[Unit]
Description=Device Monitor for Post Office Bot
After=network.target

[Service]
Type=simple
ExecStart=/opt/device_monitor.sh
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable device-monitor
sudo systemctl start device-monitor
```

### 6. Verify it's working

```bash
cat /opt/bot-data/device_count.json
```

You should see something like:
```json
{
    "device_count": 14,
    "timestamp": "2026-02-13T10:30:00Z",
    "interface": "eth0"
}
```

## Docker Setup

The `docker-compose.yml` already includes the volume mount:

```yaml
volumes:
  - /opt/bot-data:/app/host_data:ro
```

The `:ro` flag makes it read-only inside the container — the bot only reads, never writes.

## Busyness Levels

| Raw Devices | People Devices | Level | Display |
|-------------|---------------|-------|---------|
| < 13        | < 6           | Quiet | 🦗🍃 Pretty Quiet |
| 13–19       | 6–12          | Medium | ☕👥 Nice & Buzzy |
| 20–25       | 13–18         | Busy | 🔥🐝 Quite Busy |
| 26+         | 19+           | Lively | 🎉🚀 IT'S POPPIN |

**Default devices (7):** 2 smart lights, display pi, maintain server, router, + 2 infra.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Bot says "Can't reach the vibe-o-meter" | Check that `device_monitor.sh` is running and `/opt/bot-data/device_count.json` exists |
| Data shows as stale | The monitor script may have crashed — check `systemctl status device-monitor` |
| Wrong device count | Verify the correct network interface in the script (`ip link show`) |
| Permission denied | Ensure the script runs with sudo (arp-scan needs root) |
