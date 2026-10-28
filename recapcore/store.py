"""Local JSON caches: slim player catalog + league snapshot.

Uses atomic replace so a crash mid-write cannot leave a half JSON file.
Player catalog is reduced to name/position/team so RAM stays small after the
once-per-day Sleeper players download.
"""

from __future__ import annotations

import gc
import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from recapcore.client import SleeperClient
from recapcore.config import (
    DATA_DIR,
    LEAGUES,
    PLAYERS_CACHE_MAX_AGE_S,
    PLAYERS_META_PATH,
    PLAYERS_SLIM_PATH,
    PLAYERS_SLIM_VERSION,
    SNAPSHOT_PATH,
    ensure_dirs,
    validate_week,
)
from recapcore.league_format import summarize_format

SCHEMA_VERSION = 3


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_write_json(path: Path, payload: Any, *, indent: int | None = None) -> None:
    ensure_dirs()
    path = path.resolve()
    if DATA_DIR.resolve() not in path.parents and path.parent != DATA_DIR.resolve():
        raise ValueError("Refusing to write outside the data directory.")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=indent, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_json(path: Path) -> Any | None:
    path = path.resolve()
    if DATA_DIR.resolve() not in path.parents and path.parent != DATA_DIR.resolve():
        raise ValueError("Refusing to read outside the data directory.")
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def slim_players_catalog(raw: dict[str, Any]) -> dict[str, dict[str, str]]:
    """Keep only display fields. Drops injury reports, news, depth charts, etc."""
    slim: dict[str, dict[str, str]] = {}
    for pid, info in raw.items():
        if not isinstance(info, dict):
            continue
        first = str(info.get("first_name") or "").strip()
        last = str(info.get("last_name") or "").strip()
        name = f"{first} {last}".strip() or str(info.get("full_name") or pid)
        fantasy = info.get("fantasy_positions") or []
        fp = ",".join(str(x)[:8] for x in fantasy if x)[:48]
        slim[str(pid)] = {
            "n": name[:80],
            "p": str(info.get("position") or "")[:8],
            "t": str(info.get("team") or "")[:8],
            "fp": fp,
            "inj": str(info.get("injury_status") or "")[:20],
            "ib": str(info.get("injury_body_part") or "")[:24],
            "inote": str(info.get("injury_notes") or "")[:120],
            "prac": str(
                info.get("practice_participation") or info.get("practice_description") or ""
            )[:40],
            "dco": str(info.get("depth_chart_order") or "")[:4],
            "dcp": str(info.get("depth_chart_position") or "")[:8],
            "st": str(info.get("status") or "")[:24],
        }
    return slim


def ensure_player_cache(
    client: SleeperClient, *, force: bool = False, max_age_s: float | None = None
) -> dict[str, dict[str, str]]:
    meta = load_json(PLAYERS_META_PATH) or {}
    limit = PLAYERS_CACHE_MAX_AGE_S if max_age_s is None else float(max_age_s)
    age_ok = False
    if isinstance(meta, dict) and meta.get("players_schema") == PLAYERS_SLIM_VERSION:
        fetched = float(meta.get("fetched_unix") or 0)
        age_ok = (time.time() - fetched) < limit

    if not force and age_ok and PLAYERS_SLIM_PATH.is_file():
        cached = load_json(PLAYERS_SLIM_PATH)
        if isinstance(cached, dict):
            return cached

    raw = client.players_nfl()
    slim = slim_players_catalog(raw)
    del raw
    gc.collect()
    atomic_write_json(PLAYERS_SLIM_PATH, slim)
    atomic_write_json(
        PLAYERS_META_PATH,
        {
            "schema": SCHEMA_VERSION,
            "players_schema": PLAYERS_SLIM_VERSION,
            "fetched_unix": time.time(),
            "fetched_at": _utc_now(),
            "player_count": len(slim),
        },
        indent=2,
    )
    return slim


def player_label(players: dict[str, dict[str, str]], player_id: str | None) -> str:
    if not player_id:
        return "Empty slot"
    info = players.get(str(player_id))
    if not info:
        return str(player_id)
    bits = [info.get("n") or str(player_id)]
    pos = info.get("p") or ""
    team = info.get("t") or ""
    extra = "/".join(x for x in (pos, team) if x)
    if extra:
        bits.append(f"({extra})")
    return " ".join(bits)


def _team_names(users: list[dict[str, Any]], rosters: list[dict[str, Any]]) -> dict[int, str]:
    by_user = {u.get("user_id"): u for u in users}
    names: dict[int, str] = {}
    for roster in rosters:
        rid = roster.get("roster_id")
        if rid is None:
            continue
        owner = by_user.get(roster.get("owner_id"), {})
        meta = owner.get("metadata") or {}
        name = meta.get("team_name") or owner.get("display_name") or f"Team {rid}"
        names[int(rid)] = str(name)[:80]
    return names


def _slim_scoring(raw: Any) -> dict[str, float]:
    """Keep numeric scoring keys only (small, league-specific)."""
    out: dict[str, float] = {}
    if not isinstance(raw, dict):
        return out
    for key, val in raw.items():
        if not isinstance(key, str) or len(key) > 40:
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        if num == 0:
            continue
        out[key] = num
    return out


def _slim_league(league: dict[str, Any]) -> dict[str, Any]:
    settings = league.get("settings") or {}
    slim = {
        "league_id": league.get("league_id"),
        "name": league.get("name"),
        "season": league.get("season"),
        "status": league.get("status"),
        "draft_id": league.get("draft_id"),
        "previous_league_id": league.get("previous_league_id"),
        "roster_positions": league.get("roster_positions") or [],
        "total_rosters": league.get("total_rosters"),
        "last_scored_leg": settings.get("last_scored_leg"),
        "playoff_week_start": settings.get("playoff_week_start"),
        "leg": settings.get("leg"),
        "num_teams": settings.get("num_teams"),
        "league_average_match": settings.get("league_average_match"),
        "league_type": settings.get("type"),
        "waiver_type": settings.get("waiver_type"),
        "waiver_budget": settings.get("waiver_budget"),
        "waiver_day": settings.get("waiver_day_of_week"),
        "scoring": _slim_scoring(league.get("scoring_settings")),
    }
    fmt = summarize_format(slim)
    slim["format_headline"] = fmt["headline"]
    slim["lineup"] = fmt["lineup"]
    return slim


def _slim_rosters(rosters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rosters:
        settings = r.get("settings") or {}
        out.append(
            {
                "roster_id": r.get("roster_id"),
                "owner_id": r.get("owner_id"),
                "players": r.get("players") or [],
                "starters": [str(x) if x else "" for x in (r.get("starters") or [])],
                "reserve": r.get("reserve") or [],
                "settings": {
                    "wins": settings.get("wins", 0),
                    "losses": settings.get("losses", 0),
                    "ties": settings.get("ties", 0),
                    "fpts": settings.get("fpts", 0),
                    "fpts_decimal": settings.get("fpts_decimal", 0),
                    "fpts_against": settings.get("fpts_against", 0),
                    "fpts_against_decimal": settings.get("fpts_against_decimal", 0),
                    "waiver_budget_used": settings.get("waiver_budget_used", 0),
                    "waiver_position": settings.get("waiver_position"),
                },
            }
        )
    return out


def _slim_users(users: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for u in users:
        meta = u.get("metadata") or {}
        out.append(
            {
                "user_id": u.get("user_id"),
                "display_name": u.get("display_name"),
                "team_name": meta.get("team_name"),
            }
        )
    return out


def _slim_matchup(row: dict[str, Any]) -> dict[str, Any]:
    points_map = row.get("players_points") or {}
    clean_points = {}
    if isinstance(points_map, dict):
        for pid, pts in points_map.items():
            try:
                clean_points[str(pid)] = float(pts or 0)
            except (TypeError, ValueError):
                continue
    starters_pts = []
    for pts in row.get("starters_points") or []:
        try:
            starters_pts.append(float(pts or 0))
        except (TypeError, ValueError):
            starters_pts.append(0.0)
    return {
        "roster_id": row.get("roster_id"),
        "matchup_id": row.get("matchup_id"),
        "points": float(row.get("points") or 0),
        "starters": [str(x) if x else "" for x in (row.get("starters") or [])],
        "players": [str(x) for x in (row.get("players") or []) if x],
        "starters_points": starters_pts,
        "players_points": clean_points,
    }


def _slim_txn(row: dict[str, Any]) -> dict[str, Any] | None:
    if row.get("status") != "complete":
        return None
    txn_type = row.get("type")
    if txn_type not in {"trade", "waiver", "free_agent"}:
        return None
    adds = row.get("adds") or {}
    drops = row.get("drops") or {}
    picks = []
    for pick in row.get("draft_picks") or []:
        if not isinstance(pick, dict):
            continue
        picks.append(
            {
                "season": str(pick.get("season") or "")[:8],
                "round": pick.get("round"),
                "roster_id": pick.get("roster_id"),
                "previous_owner_id": pick.get("previous_owner_id"),
                "owner_id": pick.get("owner_id"),
            }
        )
    return {
        "transaction_id": str(row.get("transaction_id") or "")[:32],
        "type": txn_type,
        "leg": row.get("leg"),
        "roster_ids": row.get("roster_ids") or [],
        "adds": adds if isinstance(adds, dict) else {},
        "drops": drops if isinstance(drops, dict) else {},
        "draft_picks": picks[:20],
        "created": row.get("created"),
    }


def points_total(settings: dict[str, Any]) -> float:
    whole = float(settings.get("fpts") or 0)
    frac = float(settings.get("fpts_decimal") or 0)
    # Sleeper stores decimals as an integer suffix (78 -> 0.78) in many leagues.
    if frac >= 1:
        frac = frac / 100.0
    return whole + frac


def fetch_snapshot(client: SleeperClient, *, recap_week: int | None = None) -> dict[str, Any]:
    """Pull NFL state, both leagues, current + previous week matchups/transactions."""
    ensure_dirs()
    state = client.nfl_state()
    requested = validate_week(int(recap_week)) if recap_week is not None else None

    leagues_out: dict[str, Any] = {}
    for key, league_id in LEAGUES.items():
        league = client.league(league_id)
        users = client.users(league_id)
        rosters = client.rosters(league_id)
        slim_league = _slim_league(league)
        scored = slim_league.get("last_scored_leg")
        if requested is not None:
            week_for_league = requested
            if scored:
                week_for_league = validate_week(min(requested, int(scored)))
        elif scored:
            week_for_league = validate_week(int(scored))
        else:
            display = int(state.get("display_week") or state.get("week") or 1)
            week_for_league = validate_week(max(1, display))
        match_cur = [_slim_matchup(m) for m in client.matchups(league_id, week_for_league)]
        tx_cur = [t for t in (_slim_txn(x) for x in client.transactions(league_id, week_for_league)) if t]
        match_prev = []
        tx_prev = []
        prev_for_league = week_for_league - 1 if week_for_league > 1 else None
        if prev_for_league:
            match_prev = [_slim_matchup(m) for m in client.matchups(league_id, prev_for_league)]
            tx_prev = [t for t in (_slim_txn(x) for x in client.transactions(league_id, prev_for_league)) if t]

        leagues_out[key] = {
            "league_id": league_id,
            "league": slim_league,
            "users": _slim_users(users),
            "rosters": _slim_rosters(rosters),
            "team_names": {str(k): v for k, v in _team_names(users, rosters).items()},
            "week": week_for_league,
            "prev_week": prev_for_league,
            "matchups": match_cur,
            "matchups_prev": match_prev,
            "transactions": tx_cur,
            "transactions_prev": tx_prev,
        }

    snapshot = {
        "schema": SCHEMA_VERSION,
        "fetched_at": _utc_now(),
        "nfl_state": {
            "week": state.get("week"),
            "display_week": state.get("display_week"),
            "season": state.get("season"),
            "season_type": state.get("season_type"),
            "season_has_scores": state.get("season_has_scores"),
        },
        "requested_week": requested
        or min(
            (
                int(b.get("week") or 1)
                for b in leagues_out.values()
            ),
            default=1,
        ),
        "prev_week": min(
            (int(b["prev_week"]) for b in leagues_out.values() if b.get("prev_week")),
            default=None,
        ),
        "leagues": leagues_out,
    }
    atomic_write_json(SNAPSHOT_PATH, snapshot, indent=2)
    return snapshot


def load_snapshot() -> dict[str, Any] | None:
    data = load_json(SNAPSHOT_PATH)
    if isinstance(data, dict) and data.get("schema") == SCHEMA_VERSION:
        return data
    return None
