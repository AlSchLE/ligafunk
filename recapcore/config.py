"""Runtime paths, league IDs, and hard limits.

League IDs are public Sleeper identifiers (digits only). Nothing here is a secret.
Limits exist so a runaway API payload cannot fill RAM or disk on the host.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

APP_NAME = "sleeper-recap"
USER_AGENT = "sleeper-recap/2.0 (public-api; personal-use)"

# Official, unauthenticated Sleeper API. Always HTTPS.
API_BASE = "https://api.sleeper.app/v1"

# Digit-only IDs prevent path injection into API URLs.
_LEAGUE_ID_RE = re.compile(r"^[0-9]{6,32}$")

LEAGUES: dict[str, str] = {
    "liga1": "1389332038909198336",  # Saxy Fantasy Land
    "liga2": "1387730345675210752",  # Football league
}

# Start/sit defaults to these Sleeper team names (case-insensitive).
MY_TEAMS: dict[str, str] = {
    "liga1": "LE Shitheads",
    "liga2": "LE Thunder",
}

for _key, _lid in LEAGUES.items():
    if not _LEAGUE_ID_RE.fullmatch(_lid):
        raise RuntimeError(f"Invalid league id configured for {_key}")

SCRIPT_ROOT = Path(__file__).resolve().parent.parent

OUTPUT_DIR = SCRIPT_ROOT / "output"
DATA_DIR = SCRIPT_ROOT / "data"
LOG_DIR = SCRIPT_ROOT / "logs"

SNAPSHOT_PATH = DATA_DIR / "snapshot.json"
PLAYERS_SLIM_PATH = DATA_DIR / "players_nfl_slim.json"
PLAYERS_META_PATH = DATA_DIR / "players_meta.json"

# HTTP
CONNECT_TIMEOUT_S = 8.0
READ_TIMEOUT_S = 30.0
MAX_RETRIES = 3
RETRY_BASE_S = 1.25
MIN_INTERVAL_S = 0.12  # stay far under Sleeper's 1000 req/min guidance
MAX_JSON_BYTES = 20 * 1024 * 1024  # 20 MiB cap per response
PLAYERS_MAX_JSON_BYTES = 40 * 1024 * 1024
PLAYERS_CACHE_MAX_AGE_S = 24 * 60 * 60
PLAYERS_SLIM_VERSION = 4
PROJECTION_CACHE_MAX_AGE_S = 6 * 60 * 60
WEATHER_CACHE_MAX_AGE_S = 6 * 60 * 60
ODDS_CACHE_MAX_AGE_S = 3 * 60 * 60
PLAYERS_INJURY_MAX_AGE_S = 3 * 60 * 60

# Start/sit: blend of published/actual point totals, then real-data multipliers.
# Missing sources drop out and the remaining weights renormalize.
STARTSIT_W_PROJ = 0.50  # Sleeper weekly projection (league scoring)
STARTSIT_W_FORM = 0.25  # this-season actuals, later weeks heavier
STARTSIT_W_VS = 0.15  # actual games vs this NFL opponent (this year counts double)

# Analysis
WEEK_MIN = 1
WEEK_MAX = 22
BENCH_BLUNDER_MIN_DELTA = 5.0
DROP_REGRET_MIN_POINTS = 8.0
TRADE_REGRET_MIN_DELTA = 6.0
MAX_BLUNDERS_PER_TEAM = 3


def ensure_dirs() -> None:
    """Create local working dirs with user-only permissions where the OS allows it."""
    for path in (OUTPUT_DIR, DATA_DIR, LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass


def validate_week(week: int) -> int:
    if not isinstance(week, int) or isinstance(week, bool) or not (WEEK_MIN <= week <= WEEK_MAX):
        raise ValueError(f"Week must be an integer between {WEEK_MIN} and {WEEK_MAX}.")
    return week


def validate_league_key(key: str | None) -> dict[str, str]:
    if key is None:
        return dict(LEAGUES)
    if key not in LEAGUES:
        raise ValueError(f"Unknown league '{key}'. Use: {', '.join(LEAGUES)}")
    return {key: LEAGUES[key]}
