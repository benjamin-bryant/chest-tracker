"""Where the app keeps its data: settings, chest stats, and importing the old setup."""

import copy
import json
import logging
import os
import shutil
from datetime import date
from pathlib import Path

from store import Store

log = logging.getLogger("chest-tracker")

DEFAULTS = {
    "player_name": "",
    "webhook_url": "",
    "interact_keys": ["E"],
    "remove_key": "F10",
    "pause_key": "F9",
    "chime_enabled": False,
    "chime_volume": 0.6,
    "overlay_scale": 1.0,
    "overlay_opacity": 1.0,
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


def read_json(path: Path) -> dict | None:
    """Read a JSON object, or None if the file is missing or damaged.

    A damaged file is set aside as <name>.damaged so the app can start fresh
    instead of failing to open, and nothing is lost for good.
    """
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value
        raise ValueError("not a JSON object")
    except (OSError, ValueError) as error:
        log.warning("Couldn't read %s (%s). Starting fresh.", path.name, error.__class__.__name__)
        try:
            path.replace(path.with_name(path.name + ".damaged"))
        except OSError:
            pass
        return None


class Settings:
    def __init__(self, folder: Path):
        self.path = folder / "settings.json"
        self.values = copy.deepcopy(DEFAULTS)
        self.values.update(read_json(self.path) or {})

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
        saved = read_json(self.path)
        if saved:
            try:
                self.total = max(0, int(saved["total"]))
                self.today = max(0, int(saved["today"]))
                self.day = str(saved["day"])
            except (KeyError, TypeError, ValueError):
                self.total, self.today = 0, 0
        self.roll_over()

    def roll_over(self, today: date | None = None) -> None:
        current = (today or date.today()).isoformat()
        if current != self.day:
            self.day = current
            self.today = 0

    def add(self, amount: int) -> int:
        """Change the counts and return how much the total really changed.

        The counts never go below zero, so taking one off 0 changes nothing. Callers
        that report the change elsewhere (Discord) must use this number, not `amount`.
        """
        self.roll_over()
        before = self.total
        self.total = max(0, self.total + amount)
        self.today = max(0, self.today + amount)
        self.session = max(0, self.session + amount)
        self.save()
        return self.total - before

    def save(self) -> None:
        _write_json(self.path, {"total": self.total, "today": self.today, "day": self.day})


def import_old_setup(source: Path, folder: Path) -> bool:
    """Bring over the .bat version's files the first time the app runs next to them."""
    if (folder / "settings.json").exists() or not (source / "config.json").exists():
        return False

    try:
        old = json.loads((source / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(old, dict):
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
