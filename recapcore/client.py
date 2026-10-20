"""Bounded HTTPS client for the public Sleeper API.

Security / host-hygiene:
- TLS certificate verification stays on
- URL paths are allowlisted (no user-controlled hosts)
- Response bodies are size-capped before JSON parse
- Retries only on 429 / transient 5xx, with backoff
- Requests are serialized with a minimum interval
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import requests

from recapcore.config import (
    API_BASE,
    CONNECT_TIMEOUT_S,
    MAX_JSON_BYTES,
    MAX_RETRIES,
    MIN_INTERVAL_S,
    READ_TIMEOUT_S,
    RETRY_BASE_S,
    USER_AGENT,
)

_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]+$")
_LEAGUE_ID_RE = re.compile(r"^[0-9]{6,32}$")
_TRANSIENT = frozenset({429, 500, 502, 503, 504})
_ALLOWED_HOSTS = frozenset(
    {
        "api.sleeper.app",
        "api.open-meteo.com",
        "site.api.espn.com",
        "api.the-odds-api.com",
    }
)


class SleeperAPIError(RuntimeError):
    """Raised when the Sleeper API cannot be used safely or successfully."""


def _valid_league_id(league_id: str) -> str:
    """Only digit-only Sleeper IDs may appear in API paths."""
    value = str(league_id or "")
    if not _LEAGUE_ID_RE.fullmatch(value):
        raise SleeperAPIError("Refusing invalid league id.")
    return value


def _valid_week(week: int) -> int:
    try:
        value = int(week)
    except (TypeError, ValueError) as exc:
        raise SleeperAPIError("Refusing invalid week.") from exc
    if not 1 <= value <= 22:
        raise SleeperAPIError("Refusing invalid week.")
    return value


class SleeperClient:
    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            }
        )
        self._session.verify = True
        # Ignore proxy/.netrc settings from the environment; only the
        # allowlisted hosts above may be contacted.
        self._session.trust_env = False
        self._lock = threading.Lock()
        self._last_call = 0.0
        self.calls = 0

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> SleeperClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_json(self, path: str, *, max_bytes: int = MAX_JSON_BYTES) -> Any:
        if not _PATH_RE.fullmatch(path) or ".." in path.split("/"):
            raise SleeperAPIError("Refusing unsafe API path.")
        url = urljoin(API_BASE + "/", path.lstrip("/"))
        if not url.startswith(API_BASE + "/"):
            raise SleeperAPIError("Refusing to leave the Sleeper API origin.")
        return self.get_https(url, max_bytes=max_bytes)

    def get_https(self, url: str, *, max_bytes: int = MAX_JSON_BYTES, headers: dict[str, str] | None = None) -> Any:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc.lower() not in _ALLOWED_HOSTS:
            raise SleeperAPIError("Refusing non-allowlisted HTTPS host.")
        if parsed.query and not re.fullmatch(r"[A-Za-z0-9_.=&%,-]+", parsed.query):
            raise SleeperAPIError("Refusing unsafe query string.")

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            self._throttle()
            try:
                resp = self._session.get(
                    url,
                    timeout=(CONNECT_TIMEOUT_S, READ_TIMEOUT_S),
                    stream=True,
                    allow_redirects=False,
                    headers=headers,
                )
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(RETRY_BASE_S * (attempt + 1))
                continue

            self.calls += 1
            if resp.status_code in _TRANSIENT:
                resp.close()
                time.sleep(RETRY_BASE_S * (attempt + 1))
                continue
            if resp.status_code != 200:
                resp.close()
                raise SleeperAPIError(f"HTTP {resp.status_code} for {parsed.path}")

            raw = self._read_capped(resp, max_bytes)
            if not raw or raw.strip() in (b"null", b""):
                return None
            try:
                return json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise SleeperAPIError(f"Invalid JSON from {parsed.path}") from exc

        raise SleeperAPIError(f"API unavailable for {parsed.path}: {last_exc}")

    def _throttle(self) -> None:
        with self._lock:
            wait = MIN_INTERVAL_S - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()

    @staticmethod
    def _read_capped(resp: requests.Response, max_bytes: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        try:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise SleeperAPIError(
                        f"Response exceeded {max_bytes} bytes; aborting to protect host memory."
                    )
                chunks.append(chunk)
        finally:
            resp.close()
        return b"".join(chunks)

    def nfl_state(self) -> dict[str, Any]:
        data = self.get_json("/state/nfl")
        if not isinstance(data, dict):
            raise SleeperAPIError("Unexpected NFL state payload.")
        return data

    def league(self, league_id: str) -> dict[str, Any]:
        data = self.get_json(f"/league/{_valid_league_id(league_id)}")
        if not isinstance(data, dict):
            raise SleeperAPIError("Unexpected league payload.")
        return data

    def users(self, league_id: str) -> list[dict[str, Any]]:
        return self.get_json(f"/league/{_valid_league_id(league_id)}/users") or []

    def rosters(self, league_id: str) -> list[dict[str, Any]]:
        return self.get_json(f"/league/{_valid_league_id(league_id)}/rosters") or []

    def matchups(self, league_id: str, week: int) -> list[dict[str, Any]]:
        league_id = _valid_league_id(league_id)
        week = _valid_week(week)
        return self.get_json(f"/league/{league_id}/matchups/{week}") or []

    def transactions(self, league_id: str, week: int) -> list[dict[str, Any]]:
        league_id = _valid_league_id(league_id)
        week = _valid_week(week)
        return self.get_json(f"/league/{league_id}/transactions/{week}") or []

    def draft_picks(self, draft_id: str) -> list[dict[str, Any]]:
        if not re.fullmatch(r"[0-9]{6,32}", draft_id or ""):
            return []
        return self.get_json(f"/draft/{draft_id}/picks") or []

    def winners_bracket(self, league_id: str) -> list[dict[str, Any]]:
        return self.get_json(f"/league/{_valid_league_id(league_id)}/winners_bracket") or []

    def players_nfl(self) -> dict[str, Any]:
        from recapcore.config import PLAYERS_MAX_JSON_BYTES

        data = self.get_json("/players/nfl", max_bytes=PLAYERS_MAX_JSON_BYTES)
        if not isinstance(data, dict):
            raise SleeperAPIError("Unexpected players catalog payload.")
        return data

    def nfl_stats_week(self, season: int, week: int) -> dict[str, Any]:
        data = self.get_json(f"/stats/nfl/regular/{int(season)}/{int(week)}") or {}
        return data if isinstance(data, dict) else {}

    def nfl_projections_week(self, season: int, week: int) -> dict[str, Any]:
        data = self.get_json(f"/projections/nfl/regular/{int(season)}/{int(week)}") or {}
        return data if isinstance(data, dict) else {}

    def nfl_schedule(self, season: int) -> list[dict[str, Any]]:
        year = int(season)
        data = self.get_https(f"https://api.sleeper.app/schedule/nfl/regular/{year}") or []
        return data if isinstance(data, list) else []

    def open_meteo_daily(
        self,
        lat: float,
        lon: float,
        date_iso: str,
    ) -> dict[str, Any]:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_iso):
            raise SleeperAPIError("Invalid weather date.")
        if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
            raise SleeperAPIError("Invalid weather coordinates.")
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat:.4f}&longitude={lon:.4f}"
            "&daily=precipitation_probability_max,precipitation_sum,wind_speed_10m_max,temperature_2m_max"
            f"&start_date={date_iso}&end_date={date_iso}&timezone=UTC"
        )
        data = self.get_https(url) or {}
        return data if isinstance(data, dict) else {}

    def nfl_trending(self, kind: str = "add") -> list[dict[str, Any]]:
        if kind not in {"add", "drop"}:
            raise SleeperAPIError("Trending kind must be add or drop.")
        data = self.get_json(f"/players/nfl/trending/{kind}") or []
        return data if isinstance(data, list) else []

    def espn_nfl_scoreboard(self) -> dict[str, Any]:
        data = self.get_https(
            "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
        )
        return data if isinstance(data, dict) else {}

    def odds_api_nfl(self, api_key: str) -> list[Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", api_key or ""):
            raise SleeperAPIError("Refusing invalid Odds API key.")
        url = (
            "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"
            "?regions=us&markets=spreads,totals&oddsFormat=american"
        )
        # Send API key as header to avoid leaking it in URLs/logs
        headers = {"Authorization": f"Bearer {api_key}"}
        data = self.get_https(url, headers=headers) or []
        return data if isinstance(data, list) else []
