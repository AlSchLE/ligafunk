"""Configuration for Sleeper Web."""
import os
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Database path: env var > /tmp (Render) > local (dev)
_env_db = os.environ.get("DATABASE_PATH")
if _env_db:
    DATABASE_PATH = _env_db
elif os.environ.get("RENDER"):
    DATABASE_PATH = "/tmp/sleeper_web.db"
else:
    DATABASE_PATH = str(BASE_DIR / "sleeper_web.db")

SECRET_KEY = os.environ.get("SECRET_KEY", os.urandom(32).hex())
SESSION_LIFETIME_HOURS = int(os.environ.get("SESSION_LIFETIME_HOURS", "24"))

# Sleeper API
SLEEPER_API_BASE = "https://api.sleeper.app/v1"
SLEEPER_PLAYERS_CACHE_HOURS = 24
