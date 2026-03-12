"""Busyness checker module.

Reads device count from a JSON file written by the host's device_monitor.sh
script and maps it to friendly busyness levels with a people-range estimate
for the Discord /how-busy command.

People range formula (for device_count >= QUIET_THRESHOLD):
    extra  = device_count - BASE_DEVICES
    lower  = ceil(extra / 2)
    upper  = extra
"""

import json
import math
import os
import random
import logging
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional

import discord

logger = logging.getLogger(__name__)

# Always-on infrastructure devices (routers, switches, etc.), excluding printer
BASE_DEVICES = 6

# Base devices + 1 printer; below this threshold the space is "very quiet"
QUIET_THRESHOLD = BASE_DEVICES + 1  # 7

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
    messages: list[str]


# ── Very-quiet tier (device_count < QUIET_THRESHOLD) ────────────────────────
VERY_QUIET = BusynessLevel(
    name="Very Quiet",
    emoji="😌",
    color=0x2ECC71,  # bright green
    messages=[
        "It's probably just you and the printer.",
        "Crickets. The whole space is yours!",
        "All yours — bring your work and your playlist.",
        "Ghost town vibes, in the best way.",
        "Super quiet. You basically have the run of the place.",
    ],
)

# ── Gradient tiers ordered by max upper-bound (= device_count - BASE_DEVICES) ─
# Selection uses the upper bound of the people range so the tier matches the
# number of people estimated to be in the space.
GRADIENT_TIERS: list[tuple[int, BusynessLevel]] = [
    (3, BusynessLevel(
        name="Quiet",
        emoji="🍃",
        color=0x27AE60,  # green
        messages=[
            "Just a couple of people. Peaceful.",
            "Quiet and relaxed — easy to focus.",
            "Minimal traffic right now.",
            "Low-key and calm.",
            "Barely anyone here. Nice.",
        ],
    )),
    (7, BusynessLevel(
        name="Light",
        emoji="☕",
        color=0xF1C40F,  # yellow
        messages=[
            "A few people around, but still easy to focus.",
            "Light activity. Plenty of good spots available.",
            "Starting to warm up — still comfortable.",
            "A gentle hum of activity.",
            "Relaxed and open.",
        ],
    )),
    (13, BusynessLevel(
        name="Moderate",
        emoji="👥",
        color=0xF39C12,  # amber
        messages=[
            "A good crowd — lively but workable.",
            "Moderate buzz. Nice energy.",
            "The space is warming up.",
            "Getting sociable. Still plenty of room.",
            "Comfortable hum.",
        ],
    )),
    (20, BusynessLevel(
        name="Busy",
        emoji="🐝",
        color=0xE67E22,  # orange
        messages=[
            "It's filling up! Expect company.",
            "Buzz level: noticeable.",
            "Getting lively in here.",
            "Seats are going — but the vibe's good.",
            "Plenty happening.",
        ],
    )),
    (9999, BusynessLevel(
        name="Very Lively",
        emoji="🚀",
        color=0xE74C3C,  # red
        messages=[
            "Full house energy!",
            "Packed — find your spot fast.",
            "Peak time. The room's in full swing.",
            "Standing room only vibes.",
            "Max vibe. It's buzzing in here!",
        ],
    )),
]


def _calculate_people_range(device_count: int) -> tuple[int, int]:
    """Return (lower, upper) estimate of people currently in the space.

    When device_count < QUIET_THRESHOLD the space is considered very quiet
    and we return (0, 1).  Otherwise:

        extra = device_count - BASE_DEVICES
        lower = ceil(extra / 2)
        upper = extra
    """
    if device_count < QUIET_THRESHOLD:
        return (0, 1)
    extra = device_count - BASE_DEVICES
    lower = math.ceil(extra / 2)
    upper = extra
    return (lower, upper)


def _get_level(device_count: int) -> BusynessLevel:
    """Map device count to a gradient busyness level."""
    if device_count < QUIET_THRESHOLD:
        return VERY_QUIET
    upper = device_count - BASE_DEVICES
    for max_upper, level in GRADIENT_TIERS:
        if upper <= max_upper:
            return level
    return GRADIENT_TIERS[-1][1]


def _format_people_range(lower: int, upper: int) -> str:
    """Format a people-range tuple as a readable string, e.g. '(2–3 people)'."""
    if lower == upper:
        label = "person" if lower == 1 else "people"
        return f"({lower} {label})"
    return f"({lower}–{upper} people)"


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

    Returns an embed with a gradient colour, busyness tier, a people-range
    estimate, a flavour message, and the time of the last device scan.
    """
    data = read_device_data()

    if data is None:
        embed = discord.Embed(
            title="How busy is post-office?",
            description=(
                "🤷 **Can't read the device count right now.**\n\n"
                "The scanner might be offline — try again in a minute."
            ),
            color=0x95A5A6,  # grey
        )
        embed.set_footer(text="🕐 Last scan: unknown")
        return embed

    device_count = data.get("device_count", 0)
    timestamp = data.get("timestamp", "")
    stale = _is_stale(timestamp)

    level = _get_level(device_count)
    lower, upper = _calculate_people_range(device_count)
    people_range = _format_people_range(lower, upper)
    flavour = random.choice(level.messages)
    time_ago = _time_ago(timestamp)

    description = (
        f"{level.emoji} **{level.name}**: {people_range}\n"
        f"\"{flavour}\""
    )

    embed = discord.Embed(
        title="How busy is post-office?",
        description=description,
        color=level.color,
    )

    footer = f"🕐 Last scan: {time_ago}"
    if stale:
        footer += " (may be out of date)"
    embed.set_footer(text=footer)

    return embed
