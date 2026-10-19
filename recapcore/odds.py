"""Vegas implied team totals (ESPN scoreboard, optional The Odds API)."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from recapcore.client import SleeperClient, SleeperAPIError
from recapcore.config import DATA_DIR, ODDS_CACHE_MAX_AGE_S
from recapcore.store import atomic_write_json, load_json

log = logging.getLogger("sleeper_recap")

# Full names → Sleeper abbreviations.
NFL_ABBR: dict[str, str] = {
    "Arizona Cardinals": "ARI",
    "Atlanta Falcons": "ATL",
    "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR",
    "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN",
    "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN",
    "Detroit Lions": "DET",
    "Green Bay Packers": "GB",
    "Houston Texans": "HOU",
    "Indianapolis Colts": "IND",
    "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV",
    "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LAR",
    "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN",
    "New England Patriots": "NE",
    "New Orleans Saints": "NO",
    "New York Giants": "NYG",
    "New York Jets": "NYJ",
    "Philadelphia Eagles": "PHI",
    "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA",
    "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN",
    "Washington Commanders": "WAS",
}


def _abbr(name: str) -> str:
    name = (name or "").strip()
    if name.upper() in NFL_ABBR.values():
        return name.upper()
    return NFL_ABBR.get(name, "")


def _implied(total: float, home_spread: float) -> tuple[float, float]:
    """home_spread is negative when the home team is favored."""
    home = total / 2.0 - home_spread / 2.0
    return home, total - home


def _from_espn(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for event in raw.get("events") or []:
        if not isinstance(event, dict):
            continue
        comps = event.get("competitions") or []
        if not comps or not isinstance(comps[0], dict):
            continue
        comp = comps[0]
        home = away = ""
        for c in comp.get("competitors") or []:
            if not isinstance(c, dict):
                continue
            abbr = str((c.get("team") or {}).get("abbreviation") or "").upper()
            if c.get("homeAway") == "home":
                home = abbr
            elif c.get("homeAway") == "away":
                away = abbr
        if not home or not away:
            continue
        odds_l = [o for o in (comp.get("odds") or []) if isinstance(o, dict)]
        if not odds_l:
            continue
        o0 = odds_l[0]
        try:
            total = float(o0.get("overUnder"))
            spread = float(o0.get("spread"))
        except (TypeError, ValueError):
            continue
        # ESPN spread is typically the favorite's line; prefer homeTeamOdds.spread when present.
        home_odds = o0.get("homeTeamOdds") or {}
        if isinstance(home_odds, dict) and home_odds.get("spread") is not None:
            try:
                spread = float(home_odds.get("spread"))
            except (TypeError, ValueError):
                pass
        h_imp, a_imp = _implied(total, spread)
        start = str(event.get("date") or comp.get("date") or "")
        out[home] = {"spread": spread, "total": total, "implied": h_imp, "opp": away, "start": start, "src": "espn"}
        out[away] = {"spread": -spread, "total": total, "implied": a_imp, "opp": home, "start": start, "src": "espn"}
    return out


def _from_odds_api(raw: list[Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for event in raw:
        if not isinstance(event, dict):
            continue
        home = _abbr(str(event.get("home_team") or ""))
        away = _abbr(str(event.get("away_team") or ""))
        if not home or not away:
            continue
        start = str(event.get("commence_time") or "")
        total = None
        home_spread = None
        for book in event.get("bookmakers") or []:
            if not isinstance(book, dict):
                continue
            for market in book.get("markets") or []:
                if not isinstance(market, dict):
                    continue
                key = market.get("key")
                outcomes = market.get("outcomes") or []
                if key == "totals":
                    for oc in outcomes:
                        if isinstance(oc, dict) and oc.get("name") == "Over" and oc.get("point") is not None:
                            total = float(oc["point"])
                            break
                elif key == "spreads":
                    for oc in outcomes:
                        if not isinstance(oc, dict):
                            continue
                        if _abbr(str(oc.get("name") or "")) == home and oc.get("point") is not None:
                            home_spread = float(oc["point"])
                            break
            if total is not None and home_spread is not None:
                break
        if total is None or home_spread is None:
            continue
        h_imp, a_imp = _implied(total, home_spread)
        out[home] = {
            "spread": home_spread,
            "total": total,
            "implied": h_imp,
            "opp": away,
            "start": start,
            "src": "odds-api",
        }
        out[away] = {
            "spread": -home_spread,
            "total": total,
            "implied": a_imp,
            "opp": home,
            "start": start,
            "src": "odds-api",
        }
    return out


def load_vegas(client: SleeperClient) -> dict[str, dict[str, Any]]:
    """Map team abbr → spread/total/implied. ESPN first (no key), then The Odds API."""
    path = DATA_DIR / "nfl_vegas.json"
    cached = load_json(path)
    if isinstance(cached, dict) and cached.get("by_team"):
        age = time.time() - float(cached.get("fetched_unix") or 0)
        if age < ODDS_CACHE_MAX_AGE_S:
            return cached["by_team"]

    by_team: dict[str, dict[str, Any]] = {}
    source = ""
    try:
        raw = client.espn_nfl_scoreboard()
        if isinstance(raw, dict):
            by_team = _from_espn(raw)
            source = "espn"
    except SleeperAPIError as exc:
        log.warning("ESPN odds unavailable: %s", exc)

    if not by_team:
        key = (os.environ.get("ODDS_API_KEY") or "").strip()
        if key:
            try:
                raw = client.odds_api_nfl(key)
                if isinstance(raw, list):
                    by_team = _from_odds_api(raw)
                    source = "odds-api"
            except SleeperAPIError as exc:
                log.warning("The Odds API unavailable: %s", exc)
        else:
            log.info("Vegas skipped: ESPN failed and ODDS_API_KEY is not set")

    atomic_write_json(
        path,
        {"fetched_unix": time.time(), "source": source, "by_team": by_team},
    )
    return by_team


def league_avg_implied(vegas: dict[str, dict[str, Any]]) -> float | None:
    vals = [float(v["implied"]) for v in vegas.values() if v.get("implied") is not None]
    if not vals:
        return None
    return sum(vals) / len(vals)
