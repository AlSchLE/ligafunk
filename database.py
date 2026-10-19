"""Database models and utilities for Sleeper Web."""
import sqlite3
import hashlib
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from config import DATABASE_PATH, SESSION_LIFETIME_HOURS


def get_db() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Initialize the database with all tables."""
    conn = get_db()
    cursor = conn.cursor()

    # Leagues table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS leagues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            sleeper_league_id TEXT NOT NULL UNIQUE,
            league_code TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Teams table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            owner_name TEXT NOT NULL,
            sleeper_roster_id TEXT,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (league_id) REFERENCES leagues (id)
        )
    """)

    # Recaps table
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

    # Sessions table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            team_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP NOT NULL,
            FOREIGN KEY (team_id) REFERENCES teams (id)
        )
    """)

    # Webhooks table
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

    conn.commit()
    conn.close()


def hash_password(password: str) -> str:
    """Hash a password using SHA-256 with salt."""
    salt = secrets.token_hex(16)
    pwdhash = hashlib.sha256((password + salt).encode()).hexdigest()
    return f"{salt}${pwdhash}"


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against its hash."""
    salt, pwdhash = hashed.split("$")
    check = hashlib.sha256((password + salt).encode()).hexdigest()
    return check == pwdhash


def generate_league_code() -> str:
    """Generate a random league code."""
    return secrets.token_urlsafe(8)[:8].upper()


def create_session(team_id: int) -> str:
    """Create a new session for a team."""
    session_id = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(hours=SESSION_LIFETIME_HOURS)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sessions (id, team_id, expires_at) VALUES (?, ?, ?)",
        (session_id, team_id, expires_at.isoformat())
    )
    conn.commit()
    conn.close()
    return session_id


def get_session_team(session_id: str) -> Optional[dict]:
    """Get the team associated with a session."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT t.*, l.name as league_name, l.sleeper_league_id
        FROM sessions s
        JOIN teams t ON s.team_id = t.id
        JOIN leagues l ON t.league_id = l.id
        WHERE s.id = ? AND s.expires_at > ?
    """, (session_id, datetime.now().isoformat()))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def delete_session(session_id: str):
    """Delete a session."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()
    conn.close()
