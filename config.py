"""Configuration for Sleeper Web."""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = os.environ.get("DATABASE_PATH", str(BASE_DIR / "sleeper_web.db"))
SECRET_KEY = os.environ.get("SECRET_KEY", os.urandom(32).hex())
SESSION_LIFETIME_HOURS = int(os.environ.get("SESSION_LIFETIME_HOURS", "24"))

# Sleeper API
SLEEPER_API_BASE = "https://api.sleeper.app/v1"
SLEEPER_PLAYERS_CACHE_HOURS = 24
