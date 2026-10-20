"""Configuration for Sleeper Web.

Secrets and personal data are read from environment variables only.
No passwords, addresses, or email addresses are stored in this repository.
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Load a local .env file (development only; .env is gitignored).
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")
except ImportError:  # python-dotenv optional at runtime
    pass

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

# ---------------------------------------------------------------------------
# Admin authentication
# ---------------------------------------------------------------------------
# Preferred: ADMIN_PASSWORD_HASH (Werkzeug hash, e.g. "scrypt:32768:8:1$...").
# Fallback for local development only: ADMIN_PASSWORD (plaintext).
# If neither is set, admin login is disabled.
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
ADMIN_PASSWORD_HASH = os.environ.get("ADMIN_PASSWORD_HASH", "")
ADMIN_ENABLED = bool(ADMIN_PASSWORD or ADMIN_PASSWORD_HASH)

# ---------------------------------------------------------------------------
# Impressum (German legal notice) — supplied via environment variables.
# ---------------------------------------------------------------------------
IMPRESSUM = {
    "name": os.environ.get("IMPRESSUM_NAME", "").strip(),
    "street": os.environ.get("IMPRESSUM_STREET", "").strip(),
    "city": os.environ.get("IMPRESSUM_CITY", "").strip(),
    "email": os.environ.get("IMPRESSUM_EMAIL", "").strip(),
}
IMPRESSUM_CONFIGURED = bool(
    IMPRESSUM["name"] and IMPRESSUM["street"] and IMPRESSUM["city"]
)

# Sleeper API
SLEEPER_API_BASE = "https://api.sleeper.app/v1"
SLEEPER_PLAYERS_CACHE_HOURS = 24
