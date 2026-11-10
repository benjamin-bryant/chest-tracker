"""The message format the watcher posts and the bot reads back."""

import re

_PATTERN = re.compile(r"\U0001F4E6 chests \(([+-]\d+)\)(?: · player: (.{1,32}))?")


def format_delta(amount: int, player: str | None = None) -> str:
    message = f"\U0001F4E6 chests ({amount:+d})"
    return f"{message} · player: {player}" if player else message


def parse_delta(content: str) -> int | None:
    """Return the chest change in a tracker message, or None for anything else."""
    match = _PATTERN.fullmatch(content.strip())
    return int(match.group(1)) if match else None


def parse_player(content: str) -> str | None:
    """Read the explicit fallback identity used when Discord rejects a webhook name."""
    match = _PATTERN.fullmatch(content.strip())
    return match.group(2) if match else None
