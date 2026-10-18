"""Schema-only recovery support for historical encrypted delivery records.

No transport or execution logic is retained: salvage can preserve existing user data.
"""
import sqlite3
from hermes_cli.sqlite_util import add_column_if_missing

def initialize_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS delivery_obligations (
            obligation_id TEXT PRIMARY KEY,
            session_key TEXT NOT NULL,
            platform TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            thread_id TEXT,
            content TEXT NOT NULL,
            state TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            owner_pid INTEGER,
            owner_started_at INTEGER,
            last_error TEXT,
            adapter_profile TEXT
        )"""
    )
    if "adapter_profile" not in {row[1] for row in conn.execute("PRAGMA table_info(delivery_obligations)")}:
        add_column_if_missing(conn, "delivery_obligations", "adapter_profile", "adapter_profile TEXT")
