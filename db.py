"""Хранилище на sqlite3: профили, лог фидбека и связка «сообщение у админа → автор»."""
import sqlite3
from datetime import datetime, timezone

import config

_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    username   TEXT,
    full_name  TEXT,
    project    TEXT,
    banned     INTEGER NOT NULL DEFAULT 0,
    msg_count  INTEGER NOT NULL DEFAULT 0,
    first_seen TEXT,
    last_seen  TEXT
);

CREATE TABLE IF NOT EXISTS threads (
    admin_chat_id INTEGER NOT NULL,
    admin_msg_id  INTEGER NOT NULL,
    user_id       INTEGER NOT NULL,
    user_msg_id   INTEGER NOT NULL,
    PRIMARY KEY (admin_chat_id, admin_msg_id)
);

CREATE TABLE IF NOT EXISTS feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL,
    project    TEXT,
    kind       TEXT,
    text       TEXT,
    direction  TEXT NOT NULL DEFAULT 'in',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_feedback_user ON feedback (user_id);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(config.DB_PATH)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
        _conn.commit()
    return _conn


def touch_user(user_id: int, username: str | None, full_name: str) -> sqlite3.Row:
    """Заводит профиль при первом обращении, потом просто обновляет имя и время."""
    conn = connect()
    stamp = now()
    conn.execute(
        """INSERT INTO users (user_id, username, full_name, project, first_seen, last_seen)
           VALUES (?, ?, ?, NULL, ?, ?)
           ON CONFLICT (user_id) DO UPDATE SET
               username  = excluded.username,
               full_name = excluded.full_name,
               last_seen = excluded.last_seen""",
        (user_id, username, full_name, stamp, stamp),
    )
    conn.commit()
    return get_user(user_id)


def get_user(user_id: int) -> sqlite3.Row | None:
    return connect().execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()


def set_project(user_id: int, project: str) -> None:
    conn = connect()
    conn.execute("UPDATE users SET project = ? WHERE user_id = ?", (project, user_id))
    conn.commit()


def set_banned(user_id: int, banned: bool) -> None:
    conn = connect()
    conn.execute("UPDATE users SET banned = ? WHERE user_id = ?", (int(banned), user_id))
    conn.commit()


def bump_messages(user_id: int) -> None:
    conn = connect()
    conn.execute(
        "UPDATE users SET msg_count = msg_count + 1, last_seen = ? WHERE user_id = ?",
        (now(), user_id),
    )
    conn.commit()


def link(admin_chat_id: int, admin_msg_id: int, user_id: int, user_msg_id: int) -> None:
    """Запоминает, кому отвечать, если админ ответит реплаем на это сообщение."""
    conn = connect()
    conn.execute(
        """INSERT OR REPLACE INTO threads (admin_chat_id, admin_msg_id, user_id, user_msg_id)
           VALUES (?, ?, ?, ?)""",
        (admin_chat_id, admin_msg_id, user_id, user_msg_id),
    )
    conn.commit()


def resolve(admin_chat_id: int, admin_msg_id: int) -> sqlite3.Row | None:
    return connect().execute(
        "SELECT * FROM threads WHERE admin_chat_id = ? AND admin_msg_id = ?",
        (admin_chat_id, admin_msg_id),
    ).fetchone()


def log_message(user_id: int, project: str | None, kind: str, text: str | None, direction: str) -> None:
    conn = connect()
    conn.execute(
        """INSERT INTO feedback (user_id, project, kind, text, direction, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, project, kind, text, direction, now()),
    )
    conn.commit()


def stats() -> dict:
    conn = connect()
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    banned = conn.execute("SELECT COUNT(*) FROM users WHERE banned = 1").fetchone()[0]
    incoming = conn.execute("SELECT COUNT(*) FROM feedback WHERE direction = 'in'").fetchone()[0]
    outgoing = conn.execute("SELECT COUNT(*) FROM feedback WHERE direction = 'out'").fetchone()[0]
    last_24h = conn.execute(
        "SELECT COUNT(*) FROM feedback WHERE direction = 'in' AND created_at >= datetime('now', '-1 day')"
    ).fetchone()[0]
    by_project = conn.execute(
        """SELECT COALESCE(project, ?) AS project, COUNT(*) AS n
           FROM feedback WHERE direction = 'in'
           GROUP BY project ORDER BY n DESC""",
        (config.OTHER_PROJECT,),
    ).fetchall()
    return {
        "users": total_users,
        "banned": banned,
        "incoming": incoming,
        "outgoing": outgoing,
        "last_24h": last_24h,
        "by_project": by_project,
    }


def recent_users(limit: int = 15) -> list[sqlite3.Row]:
    return connect().execute(
        "SELECT * FROM users ORDER BY last_seen DESC LIMIT ?", (limit,)
    ).fetchall()


def export_rows() -> list[sqlite3.Row]:
    return connect().execute(
        """SELECT f.created_at, f.direction, f.user_id, u.username, u.full_name,
                  f.project, f.kind, f.text
           FROM feedback f LEFT JOIN users u ON u.user_id = f.user_id
           ORDER BY f.id"""
    ).fetchall()
