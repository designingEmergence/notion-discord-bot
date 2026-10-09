"""Helpers for sending bot replies to Discord.

Discord rejects messages longer than 2000 characters, so long LLM answers are
split into several messages, preferring paragraph, line and word boundaries.
"""

from typing import List

DISCORD_MESSAGE_LIMIT = 2000

ERROR_REPLY = (
    "Sorry, I'm having trouble answering right now. "
    "Please try again in a few minutes."
)


def split_message(text: str, limit: int = DISCORD_MESSAGE_LIMIT) -> List[str]:
    """Split text into chunks of at most `limit` characters."""
    text = text.strip()
    chunks = []

    while len(text) > limit:
        window = text[:limit]
        # Prefer the latest paragraph break, then line break, then space
        split_at = -1
        for separator in ("\n\n", "\n", " "):
            split_at = window.rfind(separator)
            if split_at > 0:
                break
        if split_at <= 0:
            split_at = limit

        chunks.append(text[:split_at].rstrip())
        text = text[split_at:].lstrip()

    if text:
        chunks.append(text)
    return chunks
