"""NFL schedule, rest/travel, and outdoor weather (Open-Meteo)."""

from __future__ import annotations

import time
from datetime import date
from typing import Any

from recapcore.client import SleeperClient
from recapcore.config import DATA_DIR, WEATHER_CACHE_MAX_AGE_S
from recapcore.store import atomic_write_json, load_json
from recapcore.venues import venue


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def load_schedule(client: SleeperClient, season: int) -> list[dict[str, Any]]:
    path = DATA_DIR / f"nfl_schedule_{int(season)}.json"
    cached = load_json(path)
    if isinstance(cached, dict) and int(cached.get("season") or 0) == int(season):
        age = time.time() - float(cached.get("fetched_unix") or 0)
        if age < 12 * 3600 and isinstance(cached.get("games"), list):
            return cached["games"]
    games = client.nfl_schedule(int(season))
    slim = []
    for g in games:
        if not isinstance(g, dict):
            continue
        slim.append(
            {
                "week": int(g.get("week") or 0),
                "date": str(g.get("date") or "")[:10],
                "start": str(g.get("start") or g.get("start_time") or g.get("kickoff") or "")[:40],
                "home": str(g.get("home") or "").upper(),
                "away": str(g.get("away") or "").upper(),
                "status": str(g.get("status") or ""),
            }
        )
    atomic_write_json(
        path,
        {"season": int(season), "fetched_unix": time.time(), "games": slim},
    )
    return slim


def games_for_week(games: list[dict[str, Any]], week: int) -> list[dict[str, Any]]:
    return [g for g in games if int(g.get("week") or 0) == int(week)]


def team_game(games: list[dict[str, Any]], week: int, team: str) -> dict[str, Any] | None:
    team = (team or "").upper()
    for g in games_for_week(games, week):
        if g.get("home") == team or g.get("away") == team:
            return g
    return None


def opponent_of(game: dict[str, Any], team: str) -> str | None:
    team = (team or "").upper()
    if game.get("home") == team:
        return game.get("away")
    if game.get("away") == team:
        return game.get("home")
    return None


def is_home(game: dict[str, Any], team: str) -> bool:
    return (team or "").upper() == (game.get("home") or "").upper()


def previous_game(games: list[dict[str, Any]], week: int, team: str) -> dict[str, Any] | None:
    team = (team or "").upper()
    prior = [
        g
        for g in games
        if int(g.get("week") or 0) < int(week) and (g.get("home") == team or g.get("away") == team)
    ]
    if not prior:
        return None
    return max(prior, key=lambda g: int(g.get("week") or 0))


def rest_and_travel(games: list[dict[str, Any]], week: int, team: str) -> dict[str, Any]:
    game = team_game(games, week, team)
    prev = previous_game(games, week, team)
    out: dict[str, Any] = {
        "days_rest": None,
        "short_week": False,
        "cross_country": False,
        "road_after_road": False,
        "home": bool(game and is_home(game, team)),
    }
    if not game:
        return out
    this_d = _parse_date(game.get("date"))
    prev_d = _parse_date(prev.get("date") if prev else None)
    if this_d and prev_d:
        days = (this_d - prev_d).days
        out["days_rest"] = days
        out["short_week"] = days <= 5
    if prev:
        out["road_after_road"] = (not is_home(prev, team)) and (not is_home(game, team))
        loc_now = game.get("home")
        loc_prev = prev.get("home")
        v_now = venue(loc_now or "")
        v_prev = venue(loc_prev or "")
        if v_now and v_prev:
            out["cross_country"] = v_now[3] in {"W", "E"} and v_prev[3] in {"W", "E"} and v_now[3] != v_prev[3]
    return out


def weather_for_game(client: SleeperClient, game: dict[str, Any]) -> dict[str, Any] | None:
    """Outdoor home stadiums only. Cached a few hours."""
    home = game.get("home") or ""
    info = venue(home)
    if not info:
        return None
    lat, lon, indoor, _coast = info
    if indoor:
        return {"indoor": True, "precip_pct": 0, "wind": 0, "temp_c": None}
    day = str(game.get("date") or "")[:10]
    cache_path = DATA_DIR / f"weather_{home}_{day}.json"
    cached = load_json(cache_path)
    if isinstance(cached, dict):
        age = time.time() - float(cached.get("fetched_unix") or 0)
        if age < WEATHER_CACHE_MAX_AGE_S:
            return cached.get("weather")
    raw = client.open_meteo_daily(lat, lon, day)
    daily = raw.get("daily") or {}
    weather = {
        "indoor": False,
        "precip_pct": _first_num(daily.get("precipitation_probability_max")),
        "precip_mm": _first_num(daily.get("precipitation_sum")),
        "wind": _first_num(daily.get("wind_speed_10m_max")),
        "temp_c": _first_num(daily.get("temperature_2m_max")),
    }
    atomic_write_json(cache_path, {"fetched_unix": time.time(), "weather": weather})
    return weather


def _first_num(value: Any) -> float:
    if isinstance(value, list) and value:
        value = value[0]
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
