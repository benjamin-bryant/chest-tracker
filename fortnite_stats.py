"""Small client and response normalizer for Fortnite-API.com BR player stats."""

from typing import Any

import requests

API_URL = "https://fortnite-api.com/v2/stats/br/v2"


class FortniteStatsError(Exception):
    """A safe, user-facing error from a stats lookup."""


def fetch_stats(
    api_key: str,
    account_name: str,
    account_type: str = "epic",
    time_window: str = "lifetime",
) -> dict[str, Any]:
    """Fetch and normalize a player's public Battle Royale stats."""
    api_key = api_key.strip()
    account_name = account_name.strip()
    if not api_key or not account_name:
        raise FortniteStatsError("Enter your Fortnite-API key and player name first.")
    if account_type not in {"epic", "psn", "xbl"}:
        raise FortniteStatsError("Choose Epic, PlayStation, or Xbox as the account type.")
    if time_window not in {"lifetime", "season"}:
        raise FortniteStatsError("Choose lifetime or current-season stats.")

    try:
        response = requests.get(
            API_URL,
            params={"name": account_name, "accountType": account_type, "timeWindow": time_window},
            headers={"Authorization": api_key},
            timeout=15,
        )
    except requests.RequestException as error:
        raise FortniteStatsError("Could not reach Fortnite-API.com. Check your connection and try again.") from error

    if response.status_code == 401:
        raise FortniteStatsError("The API key was rejected. Check the key in your Fortnite-API dashboard.")
    if response.status_code == 403:
        raise FortniteStatsError("The API denied this stats request. Check your key and account access.")
    if response.status_code == 404:
        raise FortniteStatsError("No stats were found. Check the player name and account type.")
    if response.status_code == 400:
        raise FortniteStatsError("The stats request was rejected. Check the player name and selected options.")
    if response.status_code == 429:
        raise FortniteStatsError("The stats service is rate-limiting requests. Wait a little, then try again.")
    if not response.ok:
        raise FortniteStatsError(f"The stats service returned HTTP {response.status_code}. Try again later.")

    try:
        payload = response.json()
    except ValueError as error:
        raise FortniteStatsError("The stats service returned an unreadable response.") from error

    data = payload.get("data") if isinstance(payload, dict) else None
    account = data.get("account") if isinstance(data, dict) else None
    stats = data.get("stats") if isinstance(data, dict) else None
    if not isinstance(account, dict) or not isinstance(stats, dict):
        message = payload.get("error") if isinstance(payload, dict) else None
        message = str(message) if message else "The service did not return stats for this account."
        raise FortniteStatsError(message)

    all_stats = stats.get("all") or {}
    if not isinstance(all_stats, dict):
        all_stats = {}
    return {
        "account": {
            "name": str(account.get("name") or account_name),
            "id": str(account.get("id") or ""),
        },
        "time_window": time_window,
        "stats": {
            "overall": _safe_stat_row(all_stats.get("overall")),
            "solo": _safe_stat_row(all_stats.get("solo")),
            "duo": _safe_stat_row(all_stats.get("duo")),
            "squad": _safe_stat_row(all_stats.get("squad")),
        },
    }


def _safe_stat_row(value: Any) -> dict[str, int | float] | None:
    if not isinstance(value, dict):
        return None
    fields = ("wins", "winRate", "kd", "kills", "matches", "minutesPlayed", "score")
    row = {}
    for field in fields:
        number = value.get(field)
        if isinstance(number, (int, float)) and not isinstance(number, bool):
            row[field] = number
    return row
