"""Database models and utilities for Sleeper Web."""
import hmac
import os
import sqlite3
import hashlib
import secrets
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from werkzeug.security import check_password_hash, generate_password_hash

import config


def _get_db_path() -> str:
    """Get the current database path, ensuring the directory exists."""
    path = config.DATABASE_PATH
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        # Fallback to /tmp if the primary directory cannot be created.
        fallback = os.path.join(tempfile.gettempdir(), "sleeper_web.db")
        config.DATABASE_PATH = fallback
        return fallback


def get_db() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    db_path = _get_db_path()
    # A longer busy timeout avoids spurious "database is locked" errors when
    # two gunicorn workers write at the same time.
    conn = sqlite3.connect(db_path, timeout=10)
    try:
        # The database contains password hashes and webhook URLs. Keep it
        # readable only by the service account where the OS supports it.
        os.chmod(db_path, 0o600)
    except OSError:
        pass
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_column(cursor: sqlite3.Cursor, table: str, column: str, ddl: str) -> None:
    """Add a column to an existing table if it is missing (lightweight migration)."""
    existing = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})")}
    if column not in existing:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db():
    """Initialize the database with all tables."""
    conn = get_db()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leagues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sleeper_league_id TEXT NOT NULL UNIQUE,
            league_code TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            owner_name TEXT NOT NULL,
            sleeper_roster_id TEXT,
            username TEXT,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (league_id) REFERENCES leagues (id)
        )
    """)
    # Sleeper usernames were added after the first release. Keep the login
    # backward compatible for teams that still only have a team name.
    _ensure_column(cursor, "teams", "username", "TEXT")
    cursor.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_teams_league_username
        ON teams (league_id, username COLLATE NOCASE)
        WHERE username IS NOT NULL AND username <> ''
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS recaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_id INTEGER NOT NULL,
            week INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (league_id) REFERENCES leagues (id),
            FOREIGN KEY (created_by) REFERENCES teams (id)
        )
    """)

    # Recap style / theme (added after the first release, so migrate in place).
    _ensure_column(cursor, "recaps", "style", "TEXT DEFAULT 'nfl_reporter'")
    _ensure_column(cursor, "recaps", "theme", "TEXT DEFAULT 'none'")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            team_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            FOREIGN KEY (team_id) REFERENCES teams (id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_id INTEGER NOT NULL,
            webhook_type TEXT NOT NULL,
            webhook_url TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (league_id) REFERENCES leagues (id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS login_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT NOT NULL,
            client TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_login_attempts_lookup
        ON login_attempts (scope, client, created_at)
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions (expires_at)
    """)

    conn.commit()
    conn.close()


def hash_password(password: str) -> str:
    """Hash a team password with a slow, salted KDF (scrypt)."""
    return generate_password_hash(password, method="scrypt")


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password, including hashes from the first (legacy) release.

    Legacy hashes use ``salt$sha256``. They stay valid until the next
    successful login, where the caller can transparently re-hash them.
    """
    if not password or not hashed or not isinstance(hashed, str):
        return False
    try:
        if hashed.startswith(("scrypt:", "pbkdf2:")):
            return check_password_hash(hashed, password)
        salt, expected = hashed.split("$", 1)
        actual = hashlib.sha256((password + salt).encode()).hexdigest()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def password_needs_rehash(hashed: str) -> bool:
    """True for legacy SHA-256 hashes that should be upgraded on login."""
    return not (hashed or "").startswith(("scrypt:", "pbkdf2:"))


def generate_league_code() -> str:
    """Generate an unguessable league code (12 URL-safe characters)."""
    return secrets.token_urlsafe(9)[:12].upper()


def create_session(team_id: int) -> str:
    """Create a new session for a team."""
    session_id = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(hours=config.SESSION_LIFETIME_HOURS)

    conn = get_db()
    try:
        cursor = conn.cursor()
        # Opportunistic cleanup keeps the sessions table from growing forever.
        cursor.execute("DELETE FROM sessions WHERE expires_at <= ?",
                       (datetime.now().isoformat(),))
        cursor.execute(
            "INSERT INTO sessions (id, team_id, expires_at) VALUES (?, ?, ?)",
            (session_id, team_id, expires_at.isoformat())
        )
        conn.commit()
    finally:
        conn.close()
    return session_id


def get_session_team(session_id: str) -> Optional[dict]:
    """Get the team associated with a session."""
    if not session_id or not isinstance(session_id, str) or len(session_id) > 128:
        return None
    conn = get_db()
    try:
        cursor = conn.cursor()
        # Never load password_hash into the request/template context.
        cursor.execute("""
            SELECT t.id, t.league_id, t.name, t.owner_name, t.sleeper_roster_id,
                   t.username, l.name as league_name, l.sleeper_league_id,
                   l.league_code
            FROM sessions s
            JOIN teams t ON s.team_id = t.id
            JOIN leagues l ON t.league_id = l.id
            WHERE s.id = ? AND s.expires_at > ?
        """, (session_id, datetime.now().isoformat()))
        row = cursor.fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def delete_session(session_id: str):
    """Delete a session."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Login rate limiting (SQLite-backed so it survives multiple workers)
# ---------------------------------------------------------------------------

def login_attempts(scope: str, client: str, window_minutes: int = 15) -> int:
    """Count failed logins for one scope/client inside the sliding window."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM login_attempts "
            "WHERE scope = ? AND client = ? "
            "AND created_at >= datetime('now', ?)",
            (scope, client, f"-{int(window_minutes)} minutes"),
        ).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row else 0


def record_login_failure(scope: str, client: str) -> None:
    """Record one failed login and prune old rows."""
    conn = get_db()
    try:
        conn.execute("DELETE FROM login_attempts WHERE created_at < datetime('now', '-1 day')")
        conn.execute(
            "INSERT INTO login_attempts (scope, client, created_at) "
            "VALUES (?, ?, datetime('now'))",
            (scope, client),
        )
        conn.commit()
    finally:
        conn.close()


def clear_login_failures(scope: str, client: str) -> None:
    """Clear failures after a successful login."""
    conn = get_db()
    try:
        conn.execute(
            "DELETE FROM login_attempts WHERE scope = ? AND client = ?",
            (scope, client),
        )
        conn.commit()
    finally:
        conn.close()
