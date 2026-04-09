from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / 'chat.db'

_db_lock = threading.Lock()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        return conn

    def _init_db(self) -> None:
        with _db_lock, self._connect() as conn:
            conn.executescript(
                '''
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nickname TEXT NOT NULL UNIQUE,
                    session_token TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS dialogs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user1_id INTEGER NOT NULL,
                    user2_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(user1_id, user2_id),
                    FOREIGN KEY(user1_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(user2_id) REFERENCES users(id) ON DELETE CASCADE,
                    CHECK(user1_id < user2_id)
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dialog_id INTEGER NOT NULL,
                    sender_id INTEGER NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(dialog_id) REFERENCES dialogs(id) ON DELETE CASCADE,
                    FOREIGN KEY(sender_id) REFERENCES users(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_messages_dialog_id ON messages(dialog_id, id);
                CREATE INDEX IF NOT EXISTS idx_users_session_token ON users(session_token);
                '''
            )
            conn.commit()

    def create_or_login_user(self, nickname: str, session_token: str) -> dict[str, Any]:
        now = utc_now()
        with _db_lock, self._connect() as conn:
            existing = conn.execute(
                'SELECT * FROM users WHERE lower(nickname) = lower(?)',
                (nickname,),
            ).fetchone()
            if existing:
                conn.execute(
                    'UPDATE users SET session_token = ?, last_seen_at = ? WHERE id = ?',
                    (session_token, now, existing['id']),
                )
                conn.commit()
                updated = conn.execute('SELECT * FROM users WHERE id = ?', (existing['id'],)).fetchone()
                return dict(updated)

            conn.execute(
                'INSERT INTO users (nickname, session_token, created_at, last_seen_at) VALUES (?, ?, ?, ?)',
                (nickname, session_token, now, now),
            )
            conn.commit()
            row = conn.execute('SELECT * FROM users WHERE session_token = ?', (session_token,)).fetchone()
            return dict(row)

    def get_user_by_token(self, session_token: str) -> dict[str, Any] | None:
        with _db_lock, self._connect() as conn:
            row = conn.execute('SELECT * FROM users WHERE session_token = ?', (session_token,)).fetchone()
            return dict(row) if row else None

    def get_user_by_id(self, user_id: int) -> dict[str, Any] | None:
        with _db_lock, self._connect() as conn:
            row = conn.execute('SELECT * FROM users WHERE id = ?', (user_id,)).fetchone()
            return dict(row) if row else None

    def update_last_seen(self, user_id: int) -> None:
        with _db_lock, self._connect() as conn:
            conn.execute('UPDATE users SET last_seen_at = ? WHERE id = ?', (utc_now(), user_id))
            conn.commit()

    def list_other_users(self, current_user_id: int) -> list[dict[str, Any]]:
        with _db_lock, self._connect() as conn:
            rows = conn.execute(
                '''
                SELECT id, nickname, created_at, last_seen_at
                FROM users
                WHERE id != ?
                ORDER BY lower(nickname)
                ''',
                (current_user_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def create_or_get_dialog(self, current_user_id: int, target_user_id: int) -> dict[str, Any]:
        user1_id, user2_id = sorted((current_user_id, target_user_id))
        now = utc_now()
        with _db_lock, self._connect() as conn:
            row = conn.execute(
                'SELECT * FROM dialogs WHERE user1_id = ? AND user2_id = ?',
                (user1_id, user2_id),
            ).fetchone()
            if row:
                return self._dialog_with_meta(conn, row['id'], current_user_id)

            conn.execute(
                'INSERT INTO dialogs (user1_id, user2_id, created_at) VALUES (?, ?, ?)',
                (user1_id, user2_id, now),
            )
            conn.commit()
            new_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            return self._dialog_with_meta(conn, new_id, current_user_id)

    def _dialog_with_meta(self, conn: sqlite3.Connection, dialog_id: int, current_user_id: int) -> dict[str, Any]:
        row = conn.execute(
            '''
            SELECT
                d.id,
                d.user1_id,
                d.user2_id,
                d.created_at,
                CASE WHEN d.user1_id = ? THEN u2.id ELSE u1.id END AS partner_id,
                CASE WHEN d.user1_id = ? THEN u2.nickname ELSE u1.nickname END AS partner_nickname,
                (
                    SELECT text FROM messages m
                    WHERE m.dialog_id = d.id
                    ORDER BY m.id DESC
                    LIMIT 1
                ) AS last_message_text,
                (
                    SELECT created_at FROM messages m
                    WHERE m.dialog_id = d.id
                    ORDER BY m.id DESC
                    LIMIT 1
                ) AS last_message_created_at
            FROM dialogs d
            JOIN users u1 ON u1.id = d.user1_id
            JOIN users u2 ON u2.id = d.user2_id
            WHERE d.id = ?
            ''',
            (current_user_id, current_user_id, dialog_id),
        ).fetchone()
        if not row:
            raise ValueError('Dialog not found')
        return dict(row)

    def list_dialogs(self, current_user_id: int) -> list[dict[str, Any]]:
        with _db_lock, self._connect() as conn:
            rows = conn.execute(
                '''
                SELECT
                    d.id,
                    d.user1_id,
                    d.user2_id,
                    d.created_at,
                    CASE WHEN d.user1_id = ? THEN u2.id ELSE u1.id END AS partner_id,
                    CASE WHEN d.user1_id = ? THEN u2.nickname ELSE u1.nickname END AS partner_nickname,
                    (
                        SELECT text FROM messages m
                        WHERE m.dialog_id = d.id
                        ORDER BY m.id DESC
                        LIMIT 1
                    ) AS last_message_text,
                    (
                        SELECT created_at FROM messages m
                        WHERE m.dialog_id = d.id
                        ORDER BY m.id DESC
                        LIMIT 1
                    ) AS last_message_created_at
                FROM dialogs d
                JOIN users u1 ON u1.id = d.user1_id
                JOIN users u2 ON u2.id = d.user2_id
                WHERE d.user1_id = ? OR d.user2_id = ?
                ORDER BY COALESCE(last_message_created_at, d.created_at) DESC, d.id DESC
                ''',
                (current_user_id, current_user_id, current_user_id, current_user_id),
            ).fetchall()
            return [dict(row) for row in rows]

    def user_in_dialog(self, user_id: int, dialog_id: int) -> bool:
        with _db_lock, self._connect() as conn:
            row = conn.execute(
                'SELECT 1 FROM dialogs WHERE id = ? AND (user1_id = ? OR user2_id = ?)',
                (dialog_id, user_id, user_id),
            ).fetchone()
            return bool(row)

    def get_dialog_members(self, dialog_id: int) -> tuple[int, int] | None:
        with _db_lock, self._connect() as conn:
            row = conn.execute('SELECT user1_id, user2_id FROM dialogs WHERE id = ?', (dialog_id,)).fetchone()
            if not row:
                return None
            return (row['user1_id'], row['user2_id'])

    def add_message(self, dialog_id: int, sender_id: int, text: str) -> dict[str, Any]:
        now = utc_now()
        with _db_lock, self._connect() as conn:
            conn.execute(
                'INSERT INTO messages (dialog_id, sender_id, text, created_at) VALUES (?, ?, ?, ?)',
                (dialog_id, sender_id, text, now),
            )
            conn.commit()
            message_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            row = conn.execute(
                '''
                SELECT m.id, m.dialog_id, m.sender_id, u.nickname AS sender_nickname, m.text, m.created_at
                FROM messages m
                JOIN users u ON u.id = m.sender_id
                WHERE m.id = ?
                ''',
                (message_id,),
            ).fetchone()
            return dict(row)

    def list_messages(self, dialog_id: int, limit: int = 100) -> list[dict[str, Any]]:
        with _db_lock, self._connect() as conn:
            rows = conn.execute(
                '''
                SELECT m.id, m.dialog_id, m.sender_id, u.nickname AS sender_nickname, m.text, m.created_at
                FROM messages m
                JOIN users u ON u.id = m.sender_id
                WHERE m.dialog_id = ?
                ORDER BY m.id DESC
                LIMIT ?
                ''',
                (dialog_id, limit),
            ).fetchall()
            items = [dict(row) for row in rows]
            items.reverse()
            return items
