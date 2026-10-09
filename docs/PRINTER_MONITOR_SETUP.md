# Printer Monitor Setup Guide

`/printer-status` reads the most recent successful printer snapshot. A host-side
systemd timer queries the printer over IPP every five minutes and writes the
snapshot to `/opt/bot-data/printer_status.json`; Docker exposes that directory
to the bot as read-only at `/app/host_data`.

## Host setup

Install CUPS IPP tools and create the shared data directory:

```bash
sudo apt-get update
sudo apt-get install -y cups-ipp-utils
sudo install -d -m 0755 /opt/bot-data
```

Copy the poller and systemd units:

```bash
sudo install -m 0755 scripts/printer_monitor.py /usr/local/bin/printer_monitor.py
sudo install -m 0644 systemd/printer-monitor.service /etc/systemd/system/printer-monitor.service
sudo install -m 0644 systemd/printer-monitor.timer /etc/systemd/system/printer-monitor.timer
```

Create `/etc/default/printer-monitor` with the printer's stable IPP or IPPS URI:

```ini
PRINTER_URI=ipp://printer.local/ipp/print
# Optional: PRINTER_DATA_DIR=/opt/bot-data
# Optional: PRINTER_POLL_TIMEOUT_SECONDS=30
```

If the printer's IPv4 address can change, use its link-local IPv6 address
instead. It is derived from the printer's MAC address, so it stays the same
without a router reservation (find it with `ip -6 neigh`; `%25eth0` is the
server's network interface):

```ini
PRINTER_URI=ipp://[fe80::5265:f3ff:fef7:5bd0%25eth0]/ipp/print
```

Keep this file root-owned (`sudo chmod 600 /etc/default/printer-monitor`) if the
URI contains credentials. The printer must expose `marker-*` attributes over
IPP. The poller treats a missing response or missing supplies as a failure and
does not replace the prior snapshot.

Enable and verify the timer:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now printer-monitor.timer
sudo systemctl start printer-monitor.service
systemctl status printer-monitor.timer
cat /opt/bot-data/printer_status.json
```

The existing Docker Compose bind mount already makes the snapshot available to
the bot without granting the container write access. Rebuild/restart the bot
after deploying the command:

```bash
docker compose up -d --build bot
```

## Behaviour

The command displays all supply levels returned by the printer. Supplies at or
below 20% are highlighted. A snapshot older than 15 minutes is labelled as
possibly out of date; it remains the last known good report after failed polls.
