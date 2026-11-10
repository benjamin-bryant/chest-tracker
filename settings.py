"""Where the app keeps its data: settings, chest stats, and importing the old setup."""

import json
import logging
import os
import shutil
from datetime import date
from pathlib import Path

from store import Store

log = logging.getLogger("chest-tracker")

_NAMED_INPUTS = {
    "BACKSPACE", "SPACE", "SHIFT", "CTRL", "ALT", "TAB", "MOUSE1", "MOUSE2",
    "MOUSE3", "MOUSE4", "MOUSE5", "WHEELUP", "WHEELDOWN",
    *(f"F{number}" for number in range(1, 13)),
}


def _valid_input_name(value: str, allow_wheel: bool) -> bool:
    normalized = value.strip().upper()
    named = _NAMED_INPUTS if allow_wheel else _NAMED_INPUTS - {"WHEELUP", "WHEELDOWN"}
    return normalized in named or (len(normalized) == 1 and normalized.isascii() and normalized.isalnum())

DEFAULTS = {
    "player_name": "",
    "webhook_url": "",
    "interact_keys": ["E"],
    "remove_key": "F10",
    "match_threshold": 0.8,
    "search_area": [0.15, 0.15, 0.85, 0.95],
    "bot_token": "",
    "channel_id": "",
    "run_bot": False,
    "tray_notice_shown": False,
    "overlay_enabled": False,
    "overlay_position": [0.76, 0.06],
    "fortnite_api_key": "",
    "fortnite_account_name": "",
    "fortnite_account_type": "epic",
    "fortnite_stats_window": "lifetime",
    "fortnite_stats_cache": {},
}


def data_dir() -> Path:
    override = os.environ.get("CHEST_TRACKER_DATA")
    folder = Path(override) if override else Path(os.environ["APPDATA"]) / "ChestTracker"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _write_json(path: Path, value: dict) -> None:
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2), encoding="utf-8")
    temp.replace(path)


def _quarantine_json(path: Path, label: str) -> None:
    backup = path.with_name(path.name + ".corrupt")
    suffix = 1
    while backup.exists():
        backup = path.with_name(path.name + f".corrupt.{suffix}")
        suffix += 1
    try:
        path.replace(backup)
        log.warning("Preserved the damaged %s file as %s", label, backup.name)
    except OSError:
        log.warning("Could not preserve the damaged %s file", label)


def quarantine_json(path: Path, label: str) -> None:
    """Preserve a syntactically valid JSON file that contains invalid values."""
    _quarantine_json(path, label)


def read_json_object(path: Path, label: str) -> dict | None:
    """Read a JSON object, preserving an invalid file and allowing defaults to load."""
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected a JSON object")
        return value
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        log.warning("Ignoring unreadable %s file: %s", label, error.__class__.__name__)
        _quarantine_json(path, label)
        return None


class Settings:
    def __init__(self, folder: Path):
        self.path = folder / "settings.json"
        self.values = dict(DEFAULTS)
        saved = read_json_object(self.path, "settings")
        if saved is not None:
            if saved.get("remove_key") == "BACKSPACE":
                # Move installations using the former default to the safer game-scoped key.
                saved["remove_key"] = "F10"
                log.info("Changed the previous default undo key from Backspace to F10")
            self.values.update(saved)
        self._normalize()

    def _normalize(self) -> None:
        defaults = DEFAULTS
        text_keys = (
            "player_name", "webhook_url", "remove_key", "bot_token", "channel_id",
            "fortnite_api_key", "fortnite_account_name",
        )
        for key in text_keys:
            if not isinstance(self.values.get(key), str):
                self.values[key] = defaults[key]
                log.warning("Invalid %s setting; using its default", key)

        interact = self.values.get("interact_keys")
        if (
            not isinstance(interact, list) or not interact
            or not all(isinstance(k, str) and _valid_input_name(k, allow_wheel=True) for k in interact)
        ):
            self.values["interact_keys"] = list(defaults["interact_keys"])
            log.warning("Invalid interact_keys setting; using its default")
        if not _valid_input_name(self.values["remove_key"], allow_wheel=False):
            self.values["remove_key"] = defaults["remove_key"]
            log.warning("Invalid remove_key setting; using its default")
        if self.values["remove_key"].upper() in {key.upper() for key in self.values["interact_keys"]}:
            interact_names = {key.upper() for key in self.values["interact_keys"]}
            fallback = next(
                (candidate for candidate in ("F10", "F9", "BACKSPACE", "F7", "F6")
                 if candidate not in interact_names),
                "F10",
            )
            self.values["remove_key"] = fallback
            log.warning("Undo key matched the interact key; moved undo to %s", self.values["remove_key"])

        threshold = self.values.get("match_threshold")
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            self.values["match_threshold"] = defaults["match_threshold"]
            log.warning("Invalid match_threshold setting; using its default")
        else:
            self.values["match_threshold"] = min(1.0, max(0.0, float(threshold)))

        for key, size in (("search_area", 4), ("overlay_position", 2)):
            value = self.values.get(key)
            if (
                not isinstance(value, list) or len(value) != size
                or any(isinstance(part, bool) or not isinstance(part, (int, float)) for part in value)
            ):
                self.values[key] = list(defaults[key])
                log.warning("Invalid %s setting; using its default", key)
            elif key == "overlay_position":
                self.values[key] = [min(1.0, max(0.0, float(part))) for part in value]
        area = self.values["search_area"]
        if not all(0.0 <= float(part) <= 1.0 for part in area) or area[0] >= area[2] or area[1] >= area[3]:
            self.values["search_area"] = list(defaults["search_area"])
            log.warning("Invalid search_area setting; using its default")

        if self.values.get("fortnite_account_type") not in ("epic", "psn", "xbl"):
            self.values["fortnite_account_type"] = defaults["fortnite_account_type"]
        if self.values.get("fortnite_stats_window") not in ("lifetime", "season"):
            self.values["fortnite_stats_window"] = defaults["fortnite_stats_window"]
        cache = self.values.get("fortnite_stats_cache")
        if (
            not isinstance(cache, dict)
            or not isinstance(cache.get("account"), dict)
            or not isinstance(cache.get("stats"), dict)
        ):
            self.values["fortnite_stats_cache"] = {}
        for key in ("run_bot", "tray_notice_shown", "overlay_enabled"):
            if not isinstance(self.values.get(key), bool):
                self.values[key] = defaults[key]
                log.warning("Invalid %s setting; using its default", key)

    def __getitem__(self, key: str):
        return self.values[key]

    def set(self, **changes) -> None:
        self.values.update(changes)
        _write_json(self.path, self.values)


class Stats:
    """This player's own chest counts, kept on this PC."""

    def __init__(self, folder: Path):
        self.path = folder / "stats.json"
        self.total = 0
        self.today = 0
        self.day = date.today().isoformat()
        self.session = 0
        saved = read_json_object(self.path, "stats")
        if saved is not None:
            total, today, day = saved.get("total"), saved.get("today"), saved.get("day")
            valid_counts = (
                isinstance(total, int) and not isinstance(total, bool) and total >= 0
                and isinstance(today, int) and not isinstance(today, bool) and today >= 0
            )
            try:
                date.fromisoformat(day)
                valid_day = True
            except (TypeError, ValueError):
                valid_day = False
            if valid_counts and valid_day:
                self.total, self.today, self.day = total, today, day
            else:
                log.warning("Ignoring invalid stats values and starting with empty totals")
                _quarantine_json(self.path, "stats")
        self.roll_over()

    def roll_over(self, today: date | None = None) -> None:
        current = (today or date.today()).isoformat()
        if current != self.day:
            self.day = current
            self.today = 0

    def add(self, amount: int) -> int:
        previous_total = self.total
        self.roll_over()
        self.total = max(0, self.total + amount)
        self.today = max(0, self.today + amount)
        self.session = max(0, self.session + amount)
        self.save()
        return self.total - previous_total

    def save(self) -> None:
        _write_json(self.path, {"total": self.total, "today": self.today, "day": self.day})


def import_old_setup(source: Path, folder: Path) -> bool:
    """Bring over the .bat version's files the first time the app runs next to them."""
    if (folder / "settings.json").exists() or not (source / "config.json").exists():
        return False

    old = read_json_object(source / "config.json", "legacy setup")
    if old is None:
        return False
    values = {
        key: old[key]
        for key in ("player_name", "webhook_url", "interact_keys", "match_threshold", "search_area")
        if key in old
    }

    env_path = source / ".env"
    if env_path.exists():
        env = dict(
            line.split("=", 1)
            for line in env_path.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
        values["bot_token"] = env.get("DISCORD_TOKEN", "").strip()
        values["channel_id"] = env.get("CHANNEL_ID", "").strip()
        values["run_bot"] = bool(values["bot_token"] and values["channel_id"])

    if (source / "templates").is_dir():
        shutil.copytree(source / "templates", folder / "templates", dirs_exist_ok=True)
    for name in ("chests.db", "pending.json"):
        if (source / name).exists():
            shutil.copy2(source / name, folder / name)

    if (folder / "chests.db").exists() and values.get("player_name"):
        store = Store(folder / "chests.db")
        total = max(0, store.total(values["player_name"]))
        today = max(0, store.today(values["player_name"]))
        store.db.close()
        _write_json(
            folder / "stats.json",
            {"total": total, "today": today, "day": date.today().isoformat()},
        )

    Settings(folder).set(**values)
    return True
