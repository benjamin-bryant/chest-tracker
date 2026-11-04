"""Where the app keeps its data: settings, chest stats, and importing the old setup."""

import json
import os
import shutil
from datetime import date
from pathlib import Path

from store import Store

DEFAULTS = {
    "player_name": "",
    "webhook_url": "",
    "interact_keys": ["E"],
    "match_threshold": 0.8,
    "search_area": [0.15, 0.15, 0.85, 0.95],
    "bot_token": "",
    "channel_id": "",
    "run_bot": False,
    "tray_notice_shown": False,
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


class Settings:
    def __init__(self, folder: Path):
        self.path = folder / "settings.json"
        self.values = dict(DEFAULTS)
        if self.path.exists():
            self.values.update(json.loads(self.path.read_text(encoding="utf-8")))

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
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.total = saved["total"]
            self.today = saved["today"]
            self.day = saved["day"]
        self.roll_over()

    def roll_over(self, today: date | None = None) -> None:
        current = (today or date.today()).isoformat()
        if current != self.day:
            self.day = current
            self.today = 0

    def add(self, amount: int) -> None:
        self.roll_over()
        self.total = max(0, self.total + amount)
        self.today = max(0, self.today + amount)
        self.session = max(0, self.session + amount)
        self.save()

    def save(self) -> None:
        _write_json(self.path, {"total": self.total, "today": self.today, "day": self.day})


def import_old_setup(source: Path, folder: Path) -> bool:
    """Bring over the .bat version's files the first time the app runs next to them."""
    if (folder / "settings.json").exists() or not (source / "config.json").exists():
        return False

    old = json.loads((source / "config.json").read_text(encoding="utf-8"))
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
