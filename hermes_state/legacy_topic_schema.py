"""Schema-only salvage of historical topic metadata; no transport or topic execution API."""
import sqlite3


def initialize_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS telegram_dm_topic_mode (
        profile_name TEXT NOT NULL DEFAULT 'default',
        chat_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        enabled INTEGER NOT NULL DEFAULT 1,
        activated_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        has_topics_enabled INTEGER,
        allows_users_to_create_topics INTEGER,
        capability_checked_at REAL,
        intro_message_id TEXT,
        pinned_message_id TEXT,
        PRIMARY KEY (profile_name, chat_id)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS telegram_dm_topic_bindings (
        profile_name TEXT NOT NULL DEFAULT 'default',
        chat_id TEXT NOT NULL,
        thread_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        session_key TEXT NOT NULL,
        session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
        managed_mode TEXT NOT NULL DEFAULT 'auto',
        linked_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY (profile_name, chat_id, thread_id)
        )"""
    )
