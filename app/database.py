import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import settings
from .security import hash_password


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.lock = threading.Lock()

    def initialize(self) -> None:
        with self.lock:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS device_state (
                    device_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS commands (
                    id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    motor TEXT,
                    mode TEXT,
                    duration_seconds INTEGER,
                    origin TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS presence_state (
                    node_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS presence_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    safety_mode INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS presence_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id TEXT NOT NULL,
                    zone TEXT NOT NULL,
                    detected INTEGER NOT NULL,
                    person_count INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                """
            )
            columns = {
                row["name"]
                for row in self.connection.execute("PRAGMA table_info(commands)").fetchall()
            }
            if "mode" not in columns:
                self.connection.execute("ALTER TABLE commands ADD COLUMN mode TEXT")
            existing = self.connection.execute(
                "SELECT id FROM users WHERE email = ?", (settings.admin_email.lower(),)
            ).fetchone()
            if existing is None:
                self.connection.execute(
                    "INSERT INTO users(email,password_hash,created_at) VALUES(?,?,?)",
                    (
                        settings.admin_email.lower(),
                        hash_password(settings.admin_password),
                        utc_now(),
                    ),
                )
            self.connection.execute(
                """
                INSERT OR IGNORE INTO presence_settings(id,safety_mode,updated_at)
                VALUES(1,0,?)
                """,
                (utc_now(),),
            )
            self.connection.commit()

    def user_by_email(self, email: str) -> sqlite3.Row | None:
        with self.lock:
            return self.connection.execute(
                "SELECT * FROM users WHERE email = ?", (email.lower(),)
            ).fetchone()

    def save_state(self, device_id: str, payload: dict[str, Any]) -> None:
        now = utc_now()
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO device_state(device_id,payload,updated_at) VALUES(?,?,?)
                ON CONFLICT(device_id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at
                """,
                (device_id, json.dumps(payload), now),
            )
            self.connection.commit()

    def get_state(self, device_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT payload,updated_at FROM device_state WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        payload["cloudUpdatedAt"] = row["updated_at"]
        return payload

    def create_command(self, command: dict[str, Any]) -> None:
        now = utc_now()
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO commands(id,device_id,action,motor,mode,duration_seconds,origin,status,error,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    command["commandId"],
                    command["deviceId"],
                    command["action"],
                    command.get("motor"),
                    command.get("mode"),
                    command.get("durationSeconds"),
                    command["origin"],
                    "pending",
                    None,
                    now,
                    now,
                ),
            )
            self.connection.commit()

    def update_command(self, command_id: str, status: str, error: str | None = None) -> None:
        with self.lock:
            self.connection.execute(
                "UPDATE commands SET status=?,error=?,updated_at=? WHERE id=?",
                (status, error, utc_now(), command_id),
            )
            self.connection.commit()

    def command(self, command_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM commands WHERE id = ?", (command_id,)
            ).fetchone()
        return dict(row) if row is not None else None

    def save_presence(self, node_id: str, payload: dict[str, Any]) -> None:
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO presence_state(node_id,payload,updated_at) VALUES(?,?,?)
                ON CONFLICT(node_id) DO UPDATE SET
                    payload=excluded.payload,updated_at=excluded.updated_at
                """,
                (node_id, json.dumps(payload), utc_now()),
            )
            self.connection.commit()

    def get_presence(self, node_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT payload,updated_at FROM presence_state WHERE node_id=?",
                (node_id,),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        payload["cloudUpdatedAt"] = row["updated_at"]
        return payload

    def safety_mode(self) -> bool:
        with self.lock:
            row = self.connection.execute(
                "SELECT safety_mode FROM presence_settings WHERE id=1"
            ).fetchone()
        return bool(row["safety_mode"]) if row else False

    def set_safety_mode(self, enabled: bool) -> None:
        with self.lock:
            self.connection.execute(
                "UPDATE presence_settings SET safety_mode=?,updated_at=? WHERE id=1",
                (int(enabled), utc_now()),
            )
            self.connection.commit()

    def add_presence_event(self, payload: dict[str, Any]) -> None:
        with self.lock:
            self.connection.execute(
                """
                INSERT INTO presence_events(
                    node_id,zone,detected,person_count,payload,occurred_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    payload["nodeId"],
                    payload["zone"],
                    int(payload["detected"]),
                    payload["personCount"],
                    json.dumps(payload),
                    payload["lastSeen"],
                ),
            )
            self.connection.commit()

    def presence_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT id,payload,occurred_at FROM presence_events
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            payload = json.loads(row["payload"])
            payload["eventId"] = row["id"]
            payload["occurredAt"] = row["occurred_at"]
            result.append(payload)
        return result


db = Database(settings.database_path)
