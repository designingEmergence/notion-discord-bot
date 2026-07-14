"""Printer status reader and Discord embed builder for /printer-status."""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import discord


logger = logging.getLogger(__name__)
PRINTER_STATUS_PATH = Path(os.getenv("PRINTER_STATUS_PATH", "/app/host_data/printer_status.json"))
LOW_INK_THRESHOLD = 20
STALE_THRESHOLD_SECONDS = 15 * 60


def read_printer_status() -> dict[str, Any] | None:
    try:
        with PRINTER_STATUS_PATH.open(encoding="utf-8") as status_file:
            data = json.load(status_file)
        if not isinstance(data, dict) or not isinstance(data.get("supplies"), list):
            raise ValueError("missing supplies list")
        return data
    except FileNotFoundError:
        logger.warning("Printer status file not found: %s", PRINTER_STATUS_PATH)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        logger.error("Unable to read printer status file: %s", error)
    return None


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _time_ago(timestamp: Any) -> str:
    parsed = _parse_timestamp(timestamp)
    if parsed is None:
        return "unknown"
    seconds = max(0, int((datetime.now(timezone.utc) - parsed).total_seconds()))
    if seconds < 60:
        return "just now"
    if seconds < 120:
        return "1 minute ago"
    if seconds < 3600:
        return f"{seconds // 60} minutes ago"
    if seconds < 7200:
        return "1 hour ago"
    return f"{seconds // 3600} hours ago"


def _is_stale(timestamp: Any) -> bool:
    parsed = _parse_timestamp(timestamp)
    return parsed is None or (datetime.now(timezone.utc) - parsed).total_seconds() > STALE_THRESHOLD_SECONDS


def _supply_label(supply: dict[str, Any]) -> str:
    name = str(supply.get("name") or supply.get("color") or "Unknown supply")
    percentage = supply.get("percentage")
    return f"{name}: **{percentage}%**" if isinstance(percentage, (int, float)) else f"{name}: **level unknown**"


def _friendly_state(value: Any) -> str:
    ipp_states = {"3": "Idle", "4": "Printing", "5": "Stopped"}
    state = str(value or "unknown")
    return ipp_states.get(state, state.replace("-", " ").title())


def build_printer_status_embed() -> discord.Embed:
    data = read_printer_status()
    if data is None:
        embed = discord.Embed(
            title="🖨️ Printer status",
            description="🤷 I don't have a printer update yet. The ink scout may be offline—try again shortly.",
            color=0x95A5A6,
        )
        embed.set_footer(text="🕐 Last updated: unknown")
        return embed

    supplies = [item for item in data["supplies"] if isinstance(item, dict)]
    low_supplies = [
        supply for supply in supplies
        if isinstance(supply.get("percentage"), (int, float)) and supply["percentage"] <= LOW_INK_THRESHOLD
    ]
    stale = _is_stale(data.get("timestamp"))
    state = _friendly_state(data.get("printer_state"))

    if low_supplies:
        headline = "⚠️ Ink pantry alert! Time to give these cartridges some love."
        color = 0xE67E22
    elif stale:
        headline = "🕰️ This is the last known ink report; the scout has not checked in recently."
        color = 0xF1C40F
    else:
        headline = "✨ Ink levels are looking shipshape."
        color = 0x2ECC71

    embed = discord.Embed(title="🖨️ Printer status", description=headline, color=color)
    embed.add_field(name="Printer", value=state, inline=False)
    embed.add_field(
        name="Ink / toner levels",
        value="\n".join(_supply_label(supply) for supply in supplies) or "No supply details reported.",
        inline=False,
    )
    if low_supplies:
        embed.add_field(
            name="Needs attention",
            value=", ".join(str(supply.get("name") or supply.get("color") or "Unknown supply") for supply in low_supplies),
            inline=False,
        )
    footer = f"🕐 Last updated: {_time_ago(data.get('timestamp'))}"
    if stale:
        footer += " (may be out of date)"
    embed.set_footer(text=footer)
    return embed
