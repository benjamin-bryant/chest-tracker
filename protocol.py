"""The message format the watcher posts and the bot reads back."""

import re
from urllib.parse import urlsplit

_PATTERN = re.compile(r"\U0001F4E6 chests \(([+-]\d+)\)")


def format_delta(amount: int) -> str:
    return f"\U0001F4E6 chests ({amount:+d})"


def parse_delta(content: str) -> int | None:
    """Return the chest change in a tracker message, or None for anything else."""
    match = _PATTERN.fullmatch(content.strip())
    return int(match.group(1)) if match else None


# Discord refuses webhook posts whose username contains these words, whatever the case.
_BANNED_NAME_WORDS = ("discord", "clyde")
MAX_NAME_LENGTH = 32
_WEBHOOK_HOSTS = {"discord.com", "discordapp.com", "ptb.discord.com", "canary.discord.com"}


def name_problem(name: str) -> str | None:
    """Why Discord would refuse this player name, or None if it is fine.

    The name is sent as the webhook's username, so it has to follow Discord's rules.
    An empty name is allowed (it just means "don't share to Discord").
    """
    name = name.strip()
    if not name:
        return None
    if len(name) > MAX_NAME_LENGTH:
        return f"Use {MAX_NAME_LENGTH} characters or fewer."
    lowered = name.lower()
    for word in _BANNED_NAME_WORDS:
        if word in lowered:
            return f'Discord doesn\'t allow names that contain "{word}". Pick another name.'
    return None


def is_webhook_url(url: str) -> bool:
    """True for a https://discord.com/api/webhooks/... link (and Discord's other official hosts)."""
    parts = urlsplit(url.strip())
    return (
        parts.scheme == "https"
        and (parts.hostname or "").lower() in _WEBHOOK_HOSTS
        and parts.path.startswith("/api/webhooks/")
    )
