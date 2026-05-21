#!/usr/bin/env python3
"""SQLite persistence for assistant."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("ASSISTANT_DB", BASE_DIR / "assistant.db"))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def now_ts() -> int:
    return int(time.time())


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    cols = {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT '',
                is_secret INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                email TEXT PRIMARY KEY,
                google_sub TEXT,
                name TEXT,
                picture TEXT,
                google_token_json TEXT NOT NULL DEFAULT '',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                FOREIGN KEY(email) REFERENCES users(email) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_sessions_email_expires
            ON sessions(email, expires_at);

            CREATE TABLE IF NOT EXISTS user_settings (
                email TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL DEFAULT '',
                is_secret INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(email, key),
                FOREIGN KEY(email) REFERENCES users(email) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS pending_actions (
                id TEXT PRIMARY KEY,
                user_email TEXT,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                source TEXT NOT NULL DEFAULT 'web',
                result_json TEXT,
                error TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_pending_actions_user_status_created
            ON pending_actions(user_email, status, created_at DESC);

            CREATE TABLE IF NOT EXISTS chat_messages (
                id TEXT PRIMARY KEY,
                user_email TEXT,
                channel TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_chat_messages_user_channel_created
            ON chat_messages(user_email, channel, created_at DESC);

            CREATE TABLE IF NOT EXISTS chat_contexts (
                user_email TEXT NOT NULL,
                channel TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                last_compacted_at INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL,
                PRIMARY KEY(user_email, channel)
            );

            CREATE TABLE IF NOT EXISTS uploads (
                id TEXT PRIMARY KEY,
                user_email TEXT,
                filename TEXT NOT NULL,
                mime_type TEXT NOT NULL DEFAULT '',
                size INTEGER NOT NULL DEFAULT 0,
                path TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            """
        )
        _ensure_column(conn, "pending_actions", "user_email", "user_email TEXT")
        _ensure_column(conn, "chat_messages", "user_email", "user_email TEXT")
        _ensure_column(conn, "chat_contexts", "processing", "processing INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "chat_messages", "attachments_json", "attachments_json TEXT NOT NULL DEFAULT ''")


# ---------------------------------------------------------------------------
# System settings (pre-login / OAuth app credentials)
# ---------------------------------------------------------------------------
def get_setting(key: str, default: str | None = None) -> str | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return str(row["value"])


def get_settings(keys: list[str] | None = None) -> dict[str, str]:
    init_db()
    sql = "SELECT key, value FROM settings"
    params: tuple[Any, ...] = ()
    if keys:
        placeholders = ",".join("?" for _ in keys)
        sql += f" WHERE key IN ({placeholders})"
        params = tuple(keys)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}


def set_setting(key: str, value: str, is_secret: bool = False) -> None:
    init_db()
    ts = now_ts()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO settings(key, value, is_secret, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                is_secret = excluded.is_secret,
                updated_at = excluded.updated_at
            """,
            (key, value, int(is_secret), ts),
        )


def set_settings(values: dict[str, str], secret_keys: set[str] | None = None) -> None:
    secret_keys = secret_keys or set()
    init_db()
    ts = now_ts()
    with _connect() as conn:
        for key, value in values.items():
            conn.execute(
                """
                INSERT INTO settings(key, value, is_secret, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    is_secret = excluded.is_secret,
                    updated_at = excluded.updated_at
                """,
                (key, value, int(key in secret_keys), ts),
            )


# ---------------------------------------------------------------------------
# Users / sessions
# ---------------------------------------------------------------------------
def upsert_user(email: str, google_sub: str = "", name: str = "", picture: str = "", token_json: str = "") -> dict[str, Any]:
    email = email.strip().lower()
    if not email:
        raise ValueError("email fehlt")
    init_db()
    ts = now_ts()
    with _connect() as conn:
        existing = conn.execute("SELECT created_at FROM users WHERE email = ?", (email,)).fetchone()
        created = int(existing["created_at"]) if existing else ts
        conn.execute(
            """
            INSERT INTO users(email, google_sub, name, picture, google_token_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(email) DO UPDATE SET
                google_sub = excluded.google_sub,
                name = excluded.name,
                picture = excluded.picture,
                google_token_json = excluded.google_token_json,
                updated_at = excluded.updated_at
            """,
            (email, google_sub, name, picture, token_json, created, ts),
        )
    return get_user(email) or {"email": email}


def get_user(email: str) -> dict[str, Any] | None:
    init_db()
    email = email.strip().lower()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return dict(row) if row else None


def update_user_token(email: str, token_json: str) -> None:
    init_db()
    ts = now_ts()
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET google_token_json = ?, updated_at = ? WHERE email = ?",
            (token_json, ts, email.strip().lower()),
        )


def create_session(email: str, max_age_days: int = 30) -> str:
    init_db()
    session_id = uuid.uuid4().hex + uuid.uuid4().hex
    ts = now_ts()
    expires = ts + max_age_days * 86400
    with _connect() as conn:
        conn.execute(
            "INSERT INTO sessions(session_id, email, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (session_id, email.strip().lower(), ts, expires),
        )
    return session_id


def get_session(session_id: str) -> dict[str, Any] | None:
    init_db()
    if not session_id:
        return None
    ts = now_ts()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ? AND expires_at > ?",
            (session_id, ts),
        ).fetchone()
    return dict(row) if row else None


def delete_session(session_id: str) -> None:
    init_db()
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))


def cleanup_sessions() -> None:
    init_db()
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now_ts(),))


# ---------------------------------------------------------------------------
# User settings (post-login / personal config)
# ---------------------------------------------------------------------------
def get_user_setting(email: str, key: str, default: str | None = None) -> str | None:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT value FROM user_settings WHERE email = ? AND key = ?",
            (email.strip().lower(), key),
        ).fetchone()
    if row is None:
        return default
    return str(row["value"])


def get_user_settings(email: str, keys: list[str] | None = None) -> dict[str, str]:
    init_db()
    email = email.strip().lower()
    sql = "SELECT key, value FROM user_settings WHERE email = ?"
    params: list[Any] = [email]
    if keys:
        placeholders = ",".join("?" for _ in keys)
        sql += f" AND key IN ({placeholders})"
        params.extend(keys)
    with _connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return {str(row["key"]): str(row["value"]) for row in rows}


def set_user_setting(email: str, key: str, value: str, is_secret: bool = False) -> None:
    set_user_settings(email, {key: value}, {key} if is_secret else set())


def set_user_settings(email: str, values: dict[str, str], secret_keys: set[str] | None = None) -> None:
    secret_keys = secret_keys or set()
    init_db()
    email = email.strip().lower()
    ts = now_ts()
    with _connect() as conn:
        for key, value in values.items():
            conn.execute(
                """
                INSERT INTO user_settings(email, key, value, is_secret, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(email, key) DO UPDATE SET
                    value = excluded.value,
                    is_secret = excluded.is_secret,
                    updated_at = excluded.updated_at
                """,
                (email, key, value, int(key in secret_keys), ts),
            )


def first_user_with_settings(keys: list[str]) -> str | None:
    """Return first user email that has all keys with non-empty values."""
    init_db()
    if not keys:
        return None
    placeholders = ",".join("?" for _ in keys)
    with _connect() as conn:
        rows = conn.execute(
            f"""
            SELECT email, COUNT(DISTINCT key) AS cnt
            FROM user_settings
            WHERE key IN ({placeholders}) AND value != ''
            GROUP BY email
            HAVING cnt = ?
            ORDER BY email ASC
            LIMIT 1
            """,
            tuple(keys) + (len(keys),),
        ).fetchall()
    return str(rows[0]["email"]) if rows else None


def user_email_by_setting(key: str, value: str) -> str | None:
    init_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT email FROM user_settings WHERE key = ? AND value = ? LIMIT 1",
            (key, value),
        ).fetchone()
    return str(row["email"]) if row else None


# ---------------------------------------------------------------------------
# Misc helpers
# ---------------------------------------------------------------------------
def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]


# ---------------------------------------------------------------------------
# Pending actions
# ---------------------------------------------------------------------------
def create_pending_action(action_type: str, title: str, payload: dict[str, Any], source: str = "web", user_email: str | None = None) -> dict[str, Any]:
    init_db()
    action_id = uuid.uuid4().hex[:16]
    ts = now_ts()
    email = user_email.strip().lower() if user_email else None
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO pending_actions(id, user_email, type, title, payload_json, status, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (action_id, email, action_type, title, json.dumps(payload, ensure_ascii=False), source, ts, ts),
        )
    return get_pending_action(action_id, user_email=email) or {}


def get_pending_action(action_id: str, user_email: str | None = None) -> dict[str, Any] | None:
    init_db()
    params: list[Any] = [action_id]
    sql = "SELECT * FROM pending_actions WHERE id = ?"
    if user_email is not None:
        sql += " AND (user_email = ? OR user_email IS NULL)"
        params.append(user_email.strip().lower())
    with _connect() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    return _pending_row_to_dict(row) if row else None


def list_pending_actions(include_done: bool = False, limit: int = 100, user_email: str | None = None) -> list[dict[str, Any]]:
    init_db()
    params: list[Any] = []
    where: list[str] = []
    if not include_done:
        where.append("status = 'pending'")
    if user_email is not None:
        where.append("(user_email = ? OR user_email IS NULL)")
        params.append(user_email.strip().lower())
    sql = "SELECT * FROM pending_actions"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [_pending_row_to_dict(row) for row in rows]


def update_pending_action(action_id: str, status: str, result: dict[str, Any] | None = None, error: str | None = None, user_email: str | None = None) -> dict[str, Any] | None:
    init_db()
    ts = now_ts()
    params: list[Any] = [
        status,
        json.dumps(result, ensure_ascii=False) if result is not None else None,
        error,
        ts,
        action_id,
    ]
    sql = """
        UPDATE pending_actions
        SET status = ?, result_json = ?, error = ?, updated_at = ?
        WHERE id = ?
    """
    if user_email is not None:
        sql += " AND (user_email = ? OR user_email IS NULL)"
        params.append(user_email.strip().lower())
    with _connect() as conn:
        conn.execute(sql, tuple(params))
    return get_pending_action(action_id, user_email=user_email)


# ---------------------------------------------------------------------------
# Chat messages
# ---------------------------------------------------------------------------
def add_chat_message(channel: str, role: str, content: str, user_email: str | None = None, attachments_json: str = "") -> dict[str, Any]:
    init_db()
    msg_id = uuid.uuid4().hex[:16]
    ts = now_ts()
    email = user_email.strip().lower() if user_email else None
    with _connect() as conn:
        conn.execute(
            "INSERT INTO chat_messages(id, user_email, channel, role, content, attachments_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (msg_id, email, channel, role, content, attachments_json, ts),
        )
    return {"id": msg_id, "user_email": email, "channel": channel, "role": role, "content": content, "attachments_json": attachments_json, "created_at": ts}


def recent_chat_messages(channel: str, limit: int = 20, user_email: str | None = None) -> list[dict[str, Any]]:
    init_db()
    params: list[Any] = [channel]
    sql = """
        SELECT * FROM chat_messages
        WHERE channel = ?
    """
    if user_email is not None:
        sql += " AND (user_email = ? OR user_email IS NULL)"
        params.append(user_email.strip().lower())
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    messages = [dict(row) for row in rows]
    messages.reverse()
    for m in messages:
        m["attachments"] = json.loads(m.get("attachments_json") or "[]") if m.get("attachments_json") else []
    return messages


def get_chat_context(channel: str = "web", user_email: str | None = None) -> dict[str, Any]:
    init_db()
    email = user_email.strip().lower() if user_email else ""
    if not email:
        return {"summary": "", "last_compacted_at": 0, "updated_at": 0}
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM chat_contexts WHERE user_email = ? AND channel = ?",
            (email, channel),
        ).fetchone()
    return dict(row) if row else {"summary": "", "last_compacted_at": 0, "updated_at": 0}


def set_chat_context(channel: str, summary: str, user_email: str | None = None) -> dict[str, Any]:
    init_db()
    email = user_email.strip().lower() if user_email else ""
    if not email:
        raise ValueError("user_email fehlt")
    ts = now_ts()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO chat_contexts(user_email, channel, summary, last_compacted_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_email, channel) DO UPDATE SET
                summary = excluded.summary,
                last_compacted_at = excluded.last_compacted_at,
                updated_at = excluded.updated_at
            """,
            (email, channel, summary, ts, ts),
        )
    return get_chat_context(channel, user_email=email)


def replace_chat_with_compact_summary(channel: str, summary: str, user_email: str | None = None) -> dict[str, Any]:
    """Persist compact context and make visible chat history match it."""
    init_db()
    email = user_email.strip().lower() if user_email else ""
    if not email:
        raise ValueError("user_email fehlt")
    ts = now_ts()
    msg_id = uuid.uuid4().hex[:16]
    content = "🧠 Kompakter Kontext:\n" + summary.strip()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO chat_contexts(user_email, channel, summary, last_compacted_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_email, channel) DO UPDATE SET
                summary = excluded.summary,
                last_compacted_at = excluded.last_compacted_at,
                updated_at = excluded.updated_at
            """,
            (email, channel, summary, ts, ts),
        )
        conn.execute(
            "DELETE FROM chat_messages WHERE channel = ? AND (user_email = ? OR user_email IS NULL)",
            (channel, email),
        )
        conn.execute(
            "INSERT INTO chat_messages(id, user_email, channel, role, content, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (msg_id, email, channel, "assistant", content, ts),
        )
    return {"id": msg_id, "user_email": email, "channel": channel, "role": "assistant", "content": content, "created_at": ts}


def get_chat_processing(channel: str, user_email: str | None = None) -> bool:
    init_db()
    email = user_email.strip().lower() if user_email else ""
    if not email:
        return False
    with _connect() as conn:
        row = conn.execute(
            "SELECT processing FROM chat_contexts WHERE user_email = ? AND channel = ?",
            (email, channel),
        ).fetchone()
    return bool(int(row["processing"])) if row else False


def set_chat_processing(channel: str, pending: bool, user_email: str | None = None) -> None:
    init_db()
    email = user_email.strip().lower() if user_email else ""
    if not email:
        return
    ts = now_ts()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO chat_contexts(user_email, channel, summary, last_compacted_at, updated_at, processing)
            VALUES (?, ?, '', 0, ?, ?)
            ON CONFLICT(user_email, channel) DO UPDATE SET
                processing = excluded.processing,
                updated_at = excluded.updated_at
            """,
            (email, channel, ts, int(pending)),
        )


def clear_chat_history(channel: str = "web", user_email: str | None = None) -> dict[str, Any]:
    """Delete all chat messages and reset context for a user."""
    init_db()
    email = user_email.strip().lower() if user_email else ""
    if not email:
        raise ValueError("user_email fehlt")
    ts = now_ts()
    with _connect() as conn:
        conn.execute(
            "DELETE FROM chat_messages WHERE channel = ? AND (user_email = ? OR user_email IS NULL)",
            (channel, email),
        )
        conn.execute(
            """
            INSERT INTO chat_contexts(user_email, channel, summary, last_compacted_at, updated_at, processing)
            VALUES (?, ?, '', 0, ?, 0)
            ON CONFLICT(user_email, channel) DO UPDATE SET
                summary = '',
                last_compacted_at = 0,
                updated_at = excluded.updated_at,
                processing = 0
            """,
            (email, channel, ts),
        )
    return {"status": "cleared", "channel": channel, "messages": 0}


def _pending_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "user_email": str(row["user_email"]) if "user_email" in row.keys() and row["user_email"] else None,
        "type": str(row["type"]),
        "title": str(row["title"]),
        "payload": json.loads(row["payload_json"]),
        "status": str(row["status"]),
        "source": str(row["source"]),
        "result": json.loads(row["result_json"]) if row["result_json"] else None,
        "error": str(row["error"]) if row["error"] else None,
        "created_at": int(row["created_at"]),
        "updated_at": int(row["updated_at"]),
    }


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------
UPLOAD_DIR = BASE_DIR / "data" / "uploads"


def create_upload(user_email: str | None, filename: str, mime_type: str, size: int, data: bytes) -> dict[str, Any]:
    init_db()
    upload_id = uuid.uuid4().hex[:16]
    ts = now_ts()
    email = user_email.strip().lower() if user_email else None
    user_dir = UPLOAD_DIR / (email or "anonymous")
    user_dir.mkdir(parents=True, exist_ok=True)
    file_path = user_dir / f"{upload_id}_{filename}"
    file_path.write_bytes(data)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO uploads(id, user_email, filename, mime_type, size, path, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (upload_id, email, filename, mime_type, size, str(file_path.relative_to(BASE_DIR)), ts),
        )
    return {"id": upload_id, "user_email": email, "filename": filename, "mime_type": mime_type, "size": size, "created_at": ts}


def get_upload(upload_id: str, user_email: str | None = None) -> dict[str, Any] | None:
    init_db()
    params: list[Any] = [upload_id]
    sql = "SELECT * FROM uploads WHERE id = ?"
    if user_email is not None:
        sql += " AND (user_email = ? OR user_email IS NULL)"
        params.append(user_email.strip().lower())
    with _connect() as conn:
        row = conn.execute(sql, tuple(params)).fetchone()
    if not row:
        return None
    return dict(row)


def list_uploads(user_email: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    init_db()
    params: list[Any] = []
    sql = "SELECT * FROM uploads"
    if user_email is not None:
        sql += " WHERE user_email = ? OR user_email IS NULL"
        params.append(user_email.strip().lower())
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, tuple(params)).fetchall()
    return [dict(row) for row in rows]
