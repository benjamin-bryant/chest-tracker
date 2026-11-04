"""SQLite storage for chest counts."""

import sqlite3
from pathlib import Path


class Store:
    def __init__(self, path: Path | str):
        self.db = sqlite3.connect(path)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY,
                player TEXT NOT NULL COLLATE NOCASE,
                amount INTEGER NOT NULL,
                ts INTEGER NOT NULL,
                message_id INTEGER UNIQUE
            );
            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
            """
        )
        self.db.commit()

    @classmethod
    def read_only(cls, path: Path) -> "Store":
        """Open for reading alongside the bot's connection, without taking any write lock."""
        store = cls.__new__(cls)
        store.db = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        return store

    def record_message(self, message_id: int, player: str, amount: int, ts: int) -> bool:
        """Save a tracker message. Returns False if it was already counted."""
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO events (player, amount, ts, message_id) VALUES (?, ?, ?, ?)",
            (player, amount, ts, message_id),
        )
        self.db.commit()
        return cursor.rowcount == 1

    def add_manual(self, player: str, amount: int, ts: int) -> None:
        self.db.execute(
            "INSERT INTO events (player, amount, ts) VALUES (?, ?, ?)", (player, amount, ts)
        )
        self.db.commit()

    def get_last_message_id(self) -> int | None:
        row = self.db.execute("SELECT value FROM meta WHERE key = 'last_message_id'").fetchone()
        return row[0] if row else None

    def set_last_message_id(self, message_id: int) -> None:
        # Kept apart from events so a /reset can't make old messages count again.
        self.db.execute(
            """
            INSERT INTO meta (key, value) VALUES ('last_message_id', ?)
            ON CONFLICT (key) DO UPDATE SET value = max(value, excluded.value)
            """,
            (message_id,),
        )
        self.db.commit()

    def total(self, player: str | None = None) -> int:
        return self._sum("", player)

    def today(self, player: str | None = None) -> int:
        return self._sum(
            "date(ts, 'unixepoch', 'localtime') = date('now', 'localtime')", player
        )

    def _sum(self, condition: str, player: str | None) -> int:
        clauses = [condition] if condition else []
        params = []
        if player is not None:
            clauses.append("player = ?")
            params.append(player)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.db.execute(f"SELECT COALESCE(SUM(amount), 0) FROM events {where}", params).fetchone()[0]

    def leaderboard(self, limit: int = 10) -> list[tuple[str, int]]:
        return self.db.execute(
            """
            SELECT player, SUM(amount) AS chests FROM events
            GROUP BY player ORDER BY chests DESC, player LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def reset_player(self, player: str) -> int:
        cursor = self.db.execute("DELETE FROM events WHERE player = ?", (player,))
        self.db.commit()
        return cursor.rowcount
