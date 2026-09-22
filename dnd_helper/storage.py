"""Small SQLite boundary: atomic game commands, undo checkpoints and durable delivery."""

import json
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from pathlib import Path

from .rules import GameError


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise GameError("Сохранение создано более новой версией приложения.")
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS game (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS checkpoints (id INTEGER PRIMARY KEY, session TEXT, body TEXT);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                PRAGMA user_version=1;
            """)

    @contextmanager
    def connect(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                yield db
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

    def read(self):
        with self.connect() as db:
            row = db.execute("SELECT body FROM game WHERE id=1").fetchone()
            return json.loads(row[0]) if row else None

    def mutate(self, command_id, expected, fn):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM game WHERE id=1").fetchone()
            state = json.loads(row[0]) if row else None
            if db.execute("SELECT 1 FROM receipts WHERE id=?", (command_id,)).fetchone():
                return state
            if expected is not None and (state is None or state["revision"] != expected):
                raise GameError("Состояние изменилось. Обновите экран и повторите действие.")
            revision = state["revision"] if state else 0
            state = fn(state, db)
            # Monotonic across new sessions too: an old browser tab cannot match a reused revision.
            state["revision"] = revision + 1
            db.execute("INSERT OR REPLACE INTO game VALUES (1, ?)", (encode(state),))
            db.execute("INSERT INTO receipts VALUES (?, ?)", (command_id, time.time()))
            return state

    def seen(self, key):
        with self.connect() as db:
            return bool(db.execute("SELECT 1 FROM receipts WHERE id=?", (key,)).fetchone())

    def get_meta(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set_meta(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, encode(value)))

    def backup(self):
        folder = self.path.parent / "backups"
        folder.mkdir(exist_ok=True)
        target = folder / f"game-{time.time_ns()}.sqlite3"
        with self.connect() as db, closing(sqlite3.connect(target)) as dest:
            db.backup(dest)
        return target
