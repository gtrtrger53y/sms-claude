"""Where everything is saved: each phone number's account, chat history and memory.

Uses Postgres (for example a free Neon database) when DATABASE_URL is set, so
nothing is lost when the app redeploys. Without DATABASE_URL it falls back to a
local SQLite file, which is handy for testing on your own computer.
"""

import os
import re
import secrets
import sqlite3
import threading
import time

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DB_PATH = os.environ.get("DB_PATH", "conversations.db")
USE_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))

if USE_POSTGRES:
    from psycopg_pool import ConnectionPool

    # A small pool keeps connections open between texts, so replies stay fast.
    # check= reopens connections that Neon closed while it was asleep.
    _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=5, open=False,
                           check=ConnectionPool.check_connection, kwargs={"autocommit": True})
    _pool_lock = threading.Lock()
    _pool_open = False
else:
    _sqlite_lock = threading.Lock()

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
        phone TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'pending',
        name TEXT NOT NULL DEFAULT '',
        memory TEXT NOT NULL DEFAULT '',
        gmail_address TEXT NOT NULL DEFAULT '',
        gmail_secret TEXT NOT NULL DEFAULT '',
        created_at DOUBLE PRECISION NOT NULL,
        last_seen DOUBLE PRECISION NOT NULL DEFAULT 0,
        last_notice DOUBLE PRECISION NOT NULL DEFAULT 0
    )""",
    """CREATE TABLE IF NOT EXISTS messages (
        id {serial},
        phone TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS messages_phone_id ON messages (phone, id)",
    """CREATE TABLE IF NOT EXISTS link_tokens (
        token TEXT PRIMARY KEY,
        phone TEXT NOT NULL,
        purpose TEXT NOT NULL,
        expires_at DOUBLE PRECISION NOT NULL
    )""",
]


def _sql(query):
    return query.replace("?", "%s") if USE_POSTGRES else query


def _run(query, params=(), fetch=None):
    """Run one statement. fetch is None, 'one' or 'all'."""
    if USE_POSTGRES:
        _ensure_pool()
        with _pool.connection() as conn:
            cur = conn.execute(_sql(query), params)
            if fetch == "one":
                return cur.fetchone()
            if fetch == "all":
                return cur.fetchall()
            return cur.rowcount
    with _sqlite_lock:
        conn = sqlite3.connect(DB_PATH)
        try:
            cur = conn.execute(query, params)
            result = cur.fetchone() if fetch == "one" else cur.fetchall() if fetch == "all" else cur.rowcount
            conn.commit()
            return result
        finally:
            conn.close()


def _ensure_pool():
    global _pool_open
    if not _pool_open:
        with _pool_lock:
            if not _pool_open:
                _pool.open(wait=True, timeout=30)
                _pool_open = True


def init():
    serial = "BIGSERIAL PRIMARY KEY" if USE_POSTGRES else "INTEGER PRIMARY KEY AUTOINCREMENT"
    for statement in SCHEMA:
        _run(statement.replace("{serial}", serial))


# ---------- phone numbers ----------

def normalize_phone(text):
    """Turn '555-123-4567', '(555) 123 4567' or '+1 555 123 4567' into '+15551234567'."""
    text = (text or "").strip()
    digits = re.sub(r"\D", "", text)
    if text.startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


# ---------- accounts ----------

USER_FIELDS = ("phone", "status", "name", "memory", "gmail_address", "gmail_secret",
               "created_at", "last_seen", "last_notice")


def get_user(phone):
    row = _run(f"SELECT {', '.join(USER_FIELDS)} FROM users WHERE phone = ?", (phone,), "one")
    return dict(zip(USER_FIELDS, row)) if row else None


def ensure_user(phone, status="pending"):
    """Make an account for this number if there isn't one yet, and return it."""
    _run("INSERT INTO users (phone, status, created_at) VALUES (?, ?, ?) ON CONFLICT (phone) DO NOTHING",
         (phone, status, time.time()))
    return get_user(phone)


def set_status(phone, status, name=None):
    ensure_user(phone, status)
    if name is None:
        _run("UPDATE users SET status = ? WHERE phone = ?", (status, phone))
    else:
        _run("UPDATE users SET status = ?, name = ? WHERE phone = ?", (status, name, phone))


def list_users(status="active"):
    rows = _run("SELECT phone, name FROM users WHERE status = ? ORDER BY created_at", (status,), "all")
    return [(p, n) for p, n in rows]


def touch(phone):
    _run("UPDATE users SET last_seen = ? WHERE phone = ?", (time.time(), phone))


def set_last_notice(phone):
    _run("UPDATE users SET last_notice = ? WHERE phone = ?", (time.time(), phone))


def set_memory(phone, memory):
    _run("UPDATE users SET memory = ? WHERE phone = ?", (memory, phone))


def set_gmail(phone, address, secret):
    _run("UPDATE users SET gmail_address = ?, gmail_secret = ? WHERE phone = ?", (address, secret, phone))


def delete_user_data(phone):
    """Everything saved for this number: history, memory, email connection."""
    _run("DELETE FROM messages WHERE phone = ? OR phone LIKE ?", (phone, f"call:{phone}:%"))
    _run("DELETE FROM link_tokens WHERE phone = ?", (phone,))
    _run("UPDATE users SET memory = '', gmail_address = '', gmail_secret = '' WHERE phone = ?", (phone,))


# ---------- chat history ----------

def load_history(conversation, turns):
    rows = _run("SELECT role, content FROM messages WHERE phone = ? ORDER BY id DESC LIMIT ?",
                (conversation, turns * 2), "all")
    history = [{"role": r, "content": c} for r, c in reversed(rows)]
    # The API requires the conversation to start with a user message.
    while history and history[0]["role"] != "user":
        history.pop(0)
    return history


def save_message(conversation, role, content):
    _run("INSERT INTO messages (phone, role, content, created_at) VALUES (?, ?, ?, ?)",
         (conversation, role, content, time.time()))


def clear_history(conversation):
    _run("DELETE FROM messages WHERE phone = ?", (conversation,))


def messages_since(phone, since):
    row = _run("SELECT COUNT(*) FROM messages WHERE phone = ? AND role = 'user' AND created_at > ?",
               (phone, since), "one")
    return row[0] if row else 0


def prune_history(conversation, keep):
    """Keep the database small: drop all but the newest `keep` messages of a conversation."""
    _run("DELETE FROM messages WHERE phone = ? AND id NOT IN "
         "(SELECT id FROM messages WHERE phone = ? ORDER BY id DESC LIMIT ?)",
         (conversation, conversation, keep))


# ---------- one-time links ----------

def new_link_token(phone, purpose, minutes=15):
    token = secrets.token_urlsafe(24)
    _run("DELETE FROM link_tokens WHERE phone = ? AND purpose = ?", (phone, purpose))
    _run("INSERT INTO link_tokens (token, phone, purpose, expires_at) VALUES (?, ?, ?, ?)",
         (token, phone, purpose, time.time() + minutes * 60))
    return token


def peek_link_token(token, purpose):
    """Return the phone number a link belongs to, if it's still valid. Doesn't use it up."""
    row = _run("SELECT phone, expires_at FROM link_tokens WHERE token = ? AND purpose = ?",
               (token, purpose), "one")
    if not row or row[1] < time.time():
        return None
    return row[0]


def use_link_token(token, purpose):
    phone = peek_link_token(token, purpose)
    if phone:
        _run("DELETE FROM link_tokens WHERE token = ?", (token,))
    return phone
