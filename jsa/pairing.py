"""The browser extension's pairing key (plan 30, ADR 0031).

The dashboard's `/ext/` endpoints hand out personal data (name, email,
phone, links) so the extension can fill an application form. The Host check
alone would let any other installed extension with localhost access read
them, so they need a key of their own.

The key is made on request, shown once, and only its SHA-256 is kept. A web
page can't send the custom header without a CORS preflight, and the
dashboard grants none, so pages can't reach these endpoints either way.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3

from . import db

KEY_SETTING = "ext_key_sha256"
PAIRED_SETTING = "ext_paired_at"
HEADER = "X-JSA-Key"


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8", "replace")).hexdigest()


def connect(con: sqlite3.Connection) -> str:
    """A new key; any earlier one stops working. Returned once, never stored."""
    key = secrets.token_urlsafe(32)
    for name, value in ((KEY_SETTING, _digest(key)), (PAIRED_SETTING, db.utcnow())):
        con.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (name, value))
    return key


def disconnect(con: sqlite3.Connection) -> None:
    con.execute("DELETE FROM settings WHERE key IN (?, ?)", (KEY_SETTING, PAIRED_SETTING))


def paired_at(con: sqlite3.Connection) -> str | None:
    try:
        row = con.execute("SELECT value FROM settings WHERE key = ?",
                          (PAIRED_SETTING,)).fetchone()
    except sqlite3.OperationalError:          # an older tracker: no table yet
        return None
    return row["value"] if row else None


def check(con: sqlite3.Connection, sent: str | None) -> bool:
    """True only for the current key. With none paired, nothing passes."""
    if not sent:
        return False
    try:
        row = con.execute("SELECT value FROM settings WHERE key = ?",
                          (KEY_SETTING,)).fetchone()
    except sqlite3.OperationalError:
        return False
    if not row or not row["value"]:
        return False
    return secrets.compare_digest(_digest(sent).encode(), str(row["value"]).encode())
