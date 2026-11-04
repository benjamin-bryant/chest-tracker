"""The message format the watcher posts and the bot reads back."""

import re

_PATTERN = re.compile(r"\U0001F4E6 chests \(([+-]\d+)\)")


def format_delta(amount: int) -> str:
    return f"\U0001F4E6 chests ({amount:+d})"


def parse_delta(content: str) -> int | None:
    """Return the chest change in a tracker message, or None for anything else."""
    match = _PATTERN.fullmatch(content.strip())
    return int(match.group(1)) if match else None
