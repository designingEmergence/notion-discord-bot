#!/usr/bin/env python3
"""Poll an IPP printer and atomically save its last known supply levels.

This program is intended to be run by ``printer-monitor.service``.  It has no
third-party Python dependencies; the host only needs CUPS' ``ipptool``.
"""

import csv
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


DATA_DIR = Path(os.getenv("PRINTER_DATA_DIR", "/opt/bot-data"))
DATA_FILE = DATA_DIR / "printer_status.json"
PRINTER_URI = os.getenv("PRINTER_URI", "")
IPPTOOL = os.getenv("IPPTOOL", "ipptool")
POLL_TIMEOUT_SECONDS = int(os.getenv("PRINTER_POLL_TIMEOUT_SECONDS", "30"))

REQUEST = """{
  NAME "Get printer status and supplies"
  OPERATION Get-Printer-Attributes
  GROUP operation-attributes-tag
  ATTR charset attributes-charset utf-8
  ATTR language attributes-natural-language en
  ATTR uri printer-uri $uri
  ATTR keyword requested-attributes printer-state,printer-state-reasons,marker-names,marker-levels,marker-high-levels,marker-colors,marker-types
  STATUS successful-ok
  DISPLAY printer-state
  DISPLAY printer-state-reasons
  DISPLAY marker-names
  DISPLAY marker-levels
  DISPLAY marker-high-levels
  DISPLAY marker-colors
  DISPLAY marker-types
}
"""


def _attribute_value(output: str, name: str) -> str | None:
    """Return an ipptool DISPLAY value, if the requested attribute exists."""
    match = re.search(
        rf"^\s*{re.escape(name)}(?:\s|\().*? = (.+)$", output, re.MULTILINE
    )
    return match.group(1).strip() if match else None


def _csv_values(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip().strip('"') for item in next(csv.reader([value]))]


def _integer_values(value: str | None) -> list[int | None]:
    values: list[int | None] = []
    for item in _csv_values(value):
        try:
            values.append(int(item))
        except ValueError:
            values.append(None)
    return values


def parse_ipptool_output(output: str) -> dict:
    """Build the stable JSON schema consumed by the Discord bot."""
    printer_state = _attribute_value(output, "printer-state")
    names = _csv_values(_attribute_value(output, "marker-names"))
    levels = _integer_values(_attribute_value(output, "marker-levels"))
    high_levels = _integer_values(_attribute_value(output, "marker-high-levels"))
    colors = _csv_values(_attribute_value(output, "marker-colors"))
    marker_types = _csv_values(_attribute_value(output, "marker-types"))

    if not printer_state or not names or not levels:
        raise ValueError("IPP response did not contain printer state and supply levels")

    supplies = []
    for index, name in enumerate(names):
        level = levels[index] if index < len(levels) else None
        capacity = high_levels[index] if index < len(high_levels) else None
        if level is None or level < 0:
            percentage = None
        elif capacity and capacity > 0:
            percentage = round(max(0, min(100, level * 100 / capacity)))
        else:
            # Many printers report marker-levels as percentages and omit highs.
            percentage = min(100, level)
        supplies.append({
            "name": name or f"Supply {index + 1}",
            "level": level,
            "capacity": capacity,
            "percentage": percentage,
            "color": colors[index] if index < len(colors) else None,
            "type": marker_types[index] if index < len(marker_types) else None,
        })

    return {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "printer_uri": PRINTER_URI,
        "printer_state": printer_state,
        "printer_state_reasons": _csv_values(_attribute_value(output, "printer-state-reasons")),
        "supplies": supplies,
    }


def query_printer() -> dict:
    if not PRINTER_URI:
        raise ValueError("PRINTER_URI must be set in the service environment")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".test", encoding="utf-8") as request:
        request.write(REQUEST)
        request.flush()
        result = subprocess.run(
            [IPPTOOL, "-t", PRINTER_URI, request.name],
            check=True,
            text=True,
            capture_output=True,
            timeout=POLL_TIMEOUT_SECONDS,
        )
    return parse_ipptool_output(result.stdout)


def write_snapshot(snapshot: dict) -> None:
    """Replace the snapshot only after a complete, successful query."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=DATA_DIR, prefix=".printer_status.", delete=False, encoding="utf-8"
    ) as temporary:
        json.dump(snapshot, temporary, indent=2)
        temporary.write("\n")
        temporary.flush()
        os.fsync(temporary.fileno())
        temporary_path = Path(temporary.name)
    # NamedTemporaryFile creates 0600 files; the bot container must read it
    os.chmod(temporary_path, 0o644)
    os.replace(temporary_path, DATA_FILE)


def main() -> int:
    try:
        snapshot = query_printer()
        write_snapshot(snapshot)
        print(f"Updated {DATA_FILE} at {snapshot['timestamp']}")
        return 0
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        # Deliberately do not touch DATA_FILE: callers need the last good value.
        print(f"Printer poll failed; keeping existing snapshot: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
