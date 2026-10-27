"""SQLite persistence for MCP OAuth state, tokens, and client registrations."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

DB_PATH = os.environ.get(
    "DB_PATH",
    str(Path(__file__).parent.parent / "gads_mcp.db"),
)

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        with _lock:
            if _conn is None:
                c = sqlite3.connect(DB_PATH, check_same_thread=False)
                c.row_factory = sqlite3.Row
                c.execute("PRAGMA journal_mode=WAL")
                _conn = c
    return _conn


def _exec(sql: str, params: tuple = ()) -> sqlite3.Cursor:
    with _lock:
        conn = _get_conn()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur


def init_db() -> None:
    with _lock:
        conn = _get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id   TEXT PRIMARY KEY,
                data        TEXT NOT NULL,
                created_at  INTEGER NOT NULL DEFAULT (unixepoch())
            );

            CREATE TABLE IF NOT EXISTS oauth_pending (
                google_state    TEXT PRIMARY KEY,
                mcp_state       TEXT,
                client_id       TEXT NOT NULL,
                redirect_uri    TEXT NOT NULL,
                code_challenge  TEXT NOT NULL,
                scopes          TEXT NOT NULL,
                google_rt_enc   TEXT,
                expires_at      INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS oauth_codes (
                code                    TEXT PRIMARY KEY,
                client_id               TEXT NOT NULL,
                redirect_uri            TEXT NOT NULL,
                redirect_uri_explicit   INTEGER NOT NULL,
                code_challenge          TEXT NOT NULL,
                scopes                  TEXT NOT NULL,
                google_rt_enc           TEXT NOT NULL,
                customer_id             TEXT NOT NULL,
                login_customer_id       TEXT,
                resource                TEXT,
                expires_at              INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS oauth_access_tokens (
                token               TEXT PRIMARY KEY,
                client_id           TEXT NOT NULL,
                scopes              TEXT NOT NULL,
                google_rt_enc       TEXT NOT NULL,
                customer_id         TEXT NOT NULL,
                login_customer_id   TEXT,
                expires_at          INTEGER
            );

            CREATE TABLE IF NOT EXISTS oauth_refresh_tokens (
                token               TEXT PRIMARY KEY,
                client_id           TEXT NOT NULL,
                scopes              TEXT NOT NULL,
                google_rt_enc       TEXT NOT NULL,
                customer_id         TEXT NOT NULL,
                login_customer_id   TEXT,
                expires_at          INTEGER
            );
        """)
        conn.commit()


# ---------------------------------------------------------------------------
# Clients
# ---------------------------------------------------------------------------

def save_client(client_id: str, data: dict) -> None:
    _exec(
        "INSERT OR REPLACE INTO oauth_clients (client_id, data) VALUES (?, ?)",
        (client_id, json.dumps(data)),
    )


def get_client(client_id: str) -> dict | None:
    row = _exec(
        "SELECT data FROM oauth_clients WHERE client_id = ?",
        (client_id,),
    ).fetchone()
    return json.loads(row["data"]) if row else None


# ---------------------------------------------------------------------------
# Pending authorization (MCP → Google OAuth leg, awaiting Google callback)
# ---------------------------------------------------------------------------

def save_pending(
    google_state: str,
    mcp_state: str | None,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    scopes: list[str],
    expires_at: int,
) -> None:
    _exec(
        """INSERT INTO oauth_pending
               (google_state, mcp_state, client_id, redirect_uri,
                code_challenge, scopes, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (google_state, mcp_state, client_id, redirect_uri,
         code_challenge, json.dumps(scopes), expires_at),
    )


def get_pending(google_state: str) -> dict | None:
    row = _exec(
        "SELECT * FROM oauth_pending WHERE google_state = ?",
        (google_state,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["scopes"] = json.loads(d["scopes"])
    return d


def update_pending_google_rt(google_state: str, google_rt_enc: str) -> None:
    _exec(
        "UPDATE oauth_pending SET google_rt_enc = ? WHERE google_state = ?",
        (google_rt_enc, google_state),
    )


def delete_pending(google_state: str) -> None:
    _exec("DELETE FROM oauth_pending WHERE google_state = ?", (google_state,))


# ---------------------------------------------------------------------------
# Authorization codes
# ---------------------------------------------------------------------------

def save_code(
    code: str,
    client_id: str,
    redirect_uri: str,
    redirect_uri_explicit: bool,
    code_challenge: str,
    scopes: list[str],
    google_rt_enc: str,
    customer_id: str,
    login_customer_id: str | None,
    resource: str | None,
    expires_at: int,
) -> None:
    _exec(
        """INSERT INTO oauth_codes
               (code, client_id, redirect_uri, redirect_uri_explicit,
                code_challenge, scopes, google_rt_enc, customer_id,
                login_customer_id, resource, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (code, client_id, redirect_uri, int(redirect_uri_explicit),
         code_challenge, json.dumps(scopes), google_rt_enc,
         customer_id, login_customer_id, resource, expires_at),
    )


def get_code(code: str) -> dict | None:
    row = _exec(
        "SELECT * FROM oauth_codes WHERE code = ?",
        (code,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["scopes"] = json.loads(d["scopes"])
    d["redirect_uri_explicit"] = bool(d["redirect_uri_explicit"])
    return d


def delete_code(code: str) -> None:
    _exec("DELETE FROM oauth_codes WHERE code = ?", (code,))


# ---------------------------------------------------------------------------
# Access tokens
# ---------------------------------------------------------------------------

def save_access_token(
    token: str,
    client_id: str,
    scopes: list[str],
    google_rt_enc: str,
    customer_id: str,
    login_customer_id: str | None,
    expires_at: int | None,
) -> None:
    _exec(
        """INSERT INTO oauth_access_tokens
               (token, client_id, scopes, google_rt_enc,
                customer_id, login_customer_id, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (token, client_id, json.dumps(scopes), google_rt_enc,
         customer_id, login_customer_id, expires_at),
    )


def get_access_token_row(token: str) -> dict | None:
    row = _exec(
        "SELECT * FROM oauth_access_tokens WHERE token = ?",
        (token,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["scopes"] = json.loads(d["scopes"])
    return d


def delete_access_token(token: str) -> None:
    _exec("DELETE FROM oauth_access_tokens WHERE token = ?", (token,))


# ---------------------------------------------------------------------------
# Refresh tokens
# ---------------------------------------------------------------------------

def save_refresh_token(
    token: str,
    client_id: str,
    scopes: list[str],
    google_rt_enc: str,
    customer_id: str,
    login_customer_id: str | None,
    expires_at: int | None,
) -> None:
    _exec(
        """INSERT INTO oauth_refresh_tokens
               (token, client_id, scopes, google_rt_enc,
                customer_id, login_customer_id, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (token, client_id, json.dumps(scopes), google_rt_enc,
         customer_id, login_customer_id, expires_at),
    )


def get_refresh_token_row(token: str) -> dict | None:
    row = _exec(
        "SELECT * FROM oauth_refresh_tokens WHERE token = ?",
        (token,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["scopes"] = json.loads(d["scopes"])
    return d


def delete_refresh_token(token: str) -> None:
    _exec("DELETE FROM oauth_refresh_tokens WHERE token = ?", (token,))
