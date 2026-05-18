import sqlite3
import json
from pathlib import Path


class SessionDB:
    def __init__(self, db_path: str = "sessions.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "name TEXT NOT NULL, "
                "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "system_prompt TEXT DEFAULT '', "
                "is_active BOOLEAN DEFAULT 1)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS messages ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "session_id INTEGER NOT NULL, "
                "role TEXT NOT NULL, "
                "content TEXT NOT NULL, "
                "tool_calls TEXT, "
                "timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
                "FOREIGN KEY (session_id) REFERENCES sessions (id))"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_messages_session_id "
                "ON messages(session_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_active "
                "ON sessions(is_active)"
            )
            conn.commit()

    def create_session(self, name: str, system_prompt: str = "") -> int:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO sessions (name, system_prompt) VALUES (?, ?)",
                (name, system_prompt)
            )
            session_id = cursor.lastrowid
            conn.commit()
            if session_id is None:
                raise sqlite3.Error("Failed to create session - no row ID returned")
            return session_id

    def get_session(self, session_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM sessions WHERE id = ? AND is_active = 1",
                (session_id,)
            )
            return cursor.fetchone()

    def get_sessions(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT id, name, created_at, updated_at, system_prompt "
                "FROM sessions WHERE is_active = 1 "
                "ORDER BY updated_at DESC"
            )
            return cursor.fetchall()

    def delete_session(self, session_id: int) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "UPDATE sessions SET is_active = 0, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = ?",
                (session_id,)
            )
            conn.commit()
            return cursor.rowcount > 0

    def add_message(self, session_id: int, role: str, content: str, tool_calls=None):
        tool_calls_json = json.dumps(tool_calls) if tool_calls else None
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "INSERT INTO messages (session_id, role, content, tool_calls) "
                "VALUES (?, ?, ?, ?)",
                (session_id, role, content, tool_calls_json)
            )
            conn.execute(
                "UPDATE sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (session_id,)
            )
            conn.commit()
            return cursor.lastrowid

    def get_messages(self, session_id: int):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT role, content, tool_calls, timestamp "
                "FROM messages "
                "WHERE session_id = ? "
                "ORDER BY timestamp ASC",
                (session_id,)
            )
            messages = []
            for row in cursor.fetchall():
                message = {
                    "role": row["role"],
                    "content": row["content"],
                }
                if row["tool_calls"]:
                    try:
                        message["tool_calls"] = json.loads(row["tool_calls"])
                    except json.JSONDecodeError:
                        message["tool_calls"] = None
                messages.append(message)
            return messages
