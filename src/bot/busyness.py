"""Busyness checker module.

Reads device count from a JSON file written by the host's device_monitor.sh
script and maps it to simple, friendly busyness levels for the Discord
/how-busy command.
"""

import json
import os
import random
import logging
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

import discord

logger = logging.getLogger(__name__)

# Number of devices that are always on (infrastructure)
DEFAULT_DEVICES = 7

# Path inside the Docker container (mapped via volume from host)
DEVICE_DATA_PATH = os.getenv("DEVICE_DATA_PATH", "/app/host_data/device_count.json")

# If the data is older than this many seconds, show a staleness warning
STALE_THRESHOLD_SECONDS = 600  # 10 minutes


@dataclass
class BusynessLevel:
    """Represents a busyness tier with all its display properties."""
    name: str
    emoji: str
    color: int  # Discord embed color
    bar_fill: int  # out of 20 blocks
    messages: list[str]


# ── Busyness tiers ──────────────────────────────────────────────────────────
LEVELS = {
    "quiet": BusynessLevel(
        name="Pretty Quiet",
        emoji="🦗🍃",
        color=0x2ECC71,  # green
        bar_fill=4,
        messages=[
            "It’s calm right now. Plenty of room to spread out.",
            "On the quiet side — easy to focus.",
            "Low traffic. Pick your favourite spot.",
            "Not many around yet. Come enjoy the calm.",
            "Quiet hours energy.",
            "Space to think, space to work.",
        ],
    ),
    "medium": BusynessLevel(
        name="Nice & Buzzy",
        emoji="☕👥",
        color=0xF39C12,  # amber
        bar_fill=10,
        messages=[
            "A good buzz going. Feels alive.",
            "People around, seats still open.",
            "Nice balance — social but workable.",
            "The room’s warmed up.",
            "Good energy without the squeeze.",
            "A comfortable hum.",
        ],
    ),
    "busy": BusynessLevel(
        name="Quite Busy",
        emoji="🔥🐝",
        color=0xE67E22,  # orange
        bar_fill=15,
        messages=[
            "It’s filling up. Expect company.",
            "Busy and moving.",
            "Strong turnout today.",
            "Seats are going — but the vibe’s good.",
            "Plenty happening.",
            "Buzz level: noticeable.",
        ],
    ),
    "lively": BusynessLevel(
        name="Lively",
        emoji="🎉🚀",
        color=0xE74C3C,  # red
        bar_fill=20,
        messages=[
            "Full house energy.",
            "It’s lively in here.",
            "Busy, social, active.",
            "Seats are scarce. Atmosphere isn’t.",
            "Peak time.",
            "The room’s in full swing.",
        ],
    ),
}


def _get_level(device_count: int) -> BusynessLevel:
    """Map raw device count to a busyness level."""
    if device_count < 13:
        return LEVELS["quiet"]
    elif device_count < 20:
        return LEVELS["medium"]
    elif device_count < 26:
        return LEVELS["busy"]
    else:
        return LEVELS["lively"]


def _build_progress_bar(filled: int, total: int = 20) -> str:
    """Build a fun text progress bar."""
    bar = "█" * filled + "░" * (total - filled)
    percentage = int((filled / total) * 100)
    return f"{bar} {percentage}%"


def _time_ago(timestamp_str: str) -> str:
    """Convert an ISO timestamp to a human-readable 'time ago' string."""
    try:
        scan_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = now - scan_time
        seconds = int(delta.total_seconds())

        if seconds < 60:
            return "just now"
        elif seconds < 120:
            return "1 minute ago"
        elif seconds < 3600:
            return f"{seconds // 60} minutes ago"
        elif seconds < 7200:
            return "1 hour ago"
        else:
            return f"{seconds // 3600} hours ago"
    except Exception:
        return "unknown"


def _is_stale(timestamp_str: str) -> bool:
    """Check if the scan data is older than the stale threshold."""
    try:
        scan_time = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return (now - scan_time).total_seconds() > STALE_THRESHOLD_SECONDS
    except Exception:
        return True


def read_device_data() -> Optional[dict]:
    """Read the device count JSON file written by the host monitor script."""
    try:
        with open(DEVICE_DATA_PATH, "r") as f:
            data = json.load(f)
        return data
    except FileNotFoundError:
        logger.warning(f"Device data file not found: {DEVICE_DATA_PATH}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in device data file: {e}")
        return None
    except Exception as e:
        logger.error(f"Error reading device data: {e}")
        return None


def build_busyness_embed() -> discord.Embed:
    """
    Build a Discord embed showing how busy the space is.

    Returns an emoji-friendly embed with a progress bar,
    people-device estimate, and a short status message.
    """
    data = read_device_data()

    if data is None:
        embed = discord.Embed(
            title="🏠 How Busy Is The Space?",
            description=(
                "🤷 **Can’t read the device count right now.**\n\n"
                "The scanner might be offline — try again in a minute."
            ),
            color=0x95A5A6,  # grey
        )
        embed.set_footer(text="If this keeps happening, check the device monitor on the server.")
        return embed

    device_count = data.get("device_count", 0)
    timestamp = data.get("timestamp", "")
    stale = _is_stale(timestamp)

    level = _get_level(device_count)
    people_devices = max(0, device_count - DEFAULT_DEVICES)
    progress_bar = _build_progress_bar(level.bar_fill)
    flavour = random.choice(level.messages)
    time_ago = _time_ago(timestamp)

    description_lines = [
        f"## {level.emoji} {level.name} {level.emoji}",
        "",
        f"```{progress_bar}```",
        "",
        f"👥 **~{people_devices}** people-ish devices connected",
        f"🕐 Last scan: **{time_ago}**",
    ]

    if stale:
        description_lines.append("\n⚠️ *Latest scan is a bit old — data may be out of date.*")

    description_lines.extend(["", f"*\"{flavour}\"*"])

    embed = discord.Embed(
        title="🏠 How Busy Is The Space?",
        description="\n".join(description_lines),
        color=level.color,
    )

    embed.set_footer(text="📡 Device count from the network scanner")

    return embed
