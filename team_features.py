"""Team-centric league insights for the private team dashboard.

The public Sleeper API provides league, roster, matchup and projection data.
Everything in this module is scoped to one logged-in team: standings highlight
the user's team, the matchup center opens on the user's matchup, records show
the user's history, and playoff odds are shown from the user's perspective.
"""

from __future__ import annotations

import logging
import math
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from recapcore.analyze import pair_matchups
from recapcore.client import SleeperClient
from recapcore.config import PLAYERS_INJURY_MAX_AGE_S
from recapcore.league_format import starter_slots
from recapcore.startsit import league_points, load_week_bundle
from recapcore.store import ensure_player_cache, player_label, points_total

log = logging.getLogger("sleeper-web")

_CACHE_TTL_S = 180.0
_HISTORY_CACHE_TTL_S = 6 * 60 * 60
_CACHE_MAX_ENTRIES = 128
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    """Clear the short-lived API cache (used by tests and manual refreshes)."""
    with _cache_lock:
        _cache.clear()


def _cached(key: str, loader, *, ttl: float = _CACHE_TTL_S):
    now = time.monotonic()
    with _cache_lock:
        item = _cache.get(key)
        if item and now - item[0] < ttl:
            return item[1]

    value = loader()
    with _cache_lock:
        if len(_cache) >= _CACHE_MAX_ENTRIES:
            oldest = min(_cache, key=lambda entry: _cache[entry][0])
            _cache.pop(oldest, None)
        _cache[key] = (time.monotonic(), value)
    return value


def _f(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _clean_text(value: Any, fallback: str = "") -> str:
    text = "".join(
        ch for ch in str(value or "") if ord(ch) >= 0x20 and ord(ch) != 0x7f
    ).strip()
    return text or fallback


def _team_names(users: list[dict[str, Any]], rosters: list[dict[str, Any]]) -> dict[str, str]:
    by_user = {
        str(user.get("user_id")): user
        for user in users
        if isinstance(user, dict) and user.get("user_id") is not None
    }
    names: dict[str, str] = {}
    for roster in rosters:
        rid = str(roster.get("roster_id") or "")
        if not rid:
            continue
        owner = by_user.get(str(roster.get("owner_id") or ""), {})
        metadata = owner.get("metadata") if isinstance(owner.get("metadata"), dict) else {}
        name = (
            metadata.get("team_name")
            or owner.get("display_name")
            or owner.get("username")
            or f"Team {rid}"
        )
        names[rid] = _clean_text(name, f"Team {rid}")[:80]
    return names


def _owner_map(users: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Map stable Sleeper user IDs to names, independent of team renames."""
    owners: dict[str, dict[str, str]] = {}
    for user in users:
        if not isinstance(user, dict) or user.get("user_id") is None:
            continue
        user_id = str(user.get("user_id"))
        metadata = user.get("metadata") if isinstance(user.get("metadata"), dict) else {}
        owners[user_id] = {
            "username": _clean_text(user.get("username"))[:80],
            "display_name": _clean_text(user.get("display_name"))[:80],
            "team_name": _clean_text(metadata.get("team_name"))[:80],
        }
    return owners


def _slim_roster(raw: dict[str, Any]) -> dict[str, Any]:
    settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
    return {
        "roster_id": str(raw.get("roster_id") or ""),
        "owner_id": str(raw.get("owner_id") or ""),
        "players": [str(pid) for pid in (raw.get("players") or []) if pid],
        "starters": [str(pid) if pid else "" for pid in (raw.get("starters") or [])],
        "reserve": [str(pid) for pid in (raw.get("reserve") or []) if pid],
        "settings": {
            "wins": int(_f(settings.get("wins"))),
            "losses": int(_f(settings.get("losses"))),
            "ties": int(_f(settings.get("ties"))),
            "fpts": _f(settings.get("fpts")),
            "fpts_decimal": _f(settings.get("fpts_decimal")),
            "fpts_against": _f(settings.get("fpts_against")),
            "fpts_against_decimal": _f(settings.get("fpts_against_decimal")),
            "waiver_budget_used": _f(settings.get("waiver_budget_used")),
            "waiver_position": settings.get("waiver_position"),
        },
    }


def _slim_matchup(raw: dict[str, Any]) -> dict[str, Any]:
    players_points = raw.get("players_points") if isinstance(raw.get("players_points"), dict) else {}
    return {
        "roster_id": str(raw.get("roster_id") or ""),
        "matchup_id": raw.get("matchup_id"),
        "points": _f(raw.get("points")),
        "starters": [str(pid) if pid else "" for pid in (raw.get("starters") or [])],
        "players": [str(pid) for pid in (raw.get("players") or []) if pid],
        "starters_points": [_f(value) for value in (raw.get("starters_points") or [])],
        "players_points": {
            str(pid): _f(value) for pid, value in players_points.items()
        },
    }


def _load_base(
    client: SleeperClient,
    league_id: str,
    *,
    ttl: float = _CACHE_TTL_S,
) -> dict[str, Any]:
    def load():
        league = client.league(league_id)
        users = client.users(league_id)
        rosters = [
            _slim_roster(row)
            for row in client.rosters(league_id)
            if isinstance(row, dict)
        ]
        return {
            "league": league,
            "rosters": rosters,
            "names": _team_names(users, rosters),
            "owners": _owner_map(users),
        }

    return _cached(f"base:{league_id}", load, ttl=ttl)


def _load_week(
    client: SleeperClient,
    league_id: str,
    week: int,
    *,
    ttl: float = _CACHE_TTL_S,
) -> list[dict[str, Any]]:
    def load():
        return [
            _slim_matchup(row)
            for row in client.matchups(league_id, week)
            if isinstance(row, dict)
        ]

    return _cached(f"week:{league_id}:{week}", load, ttl=ttl)


def _completed_weeks(league: dict[str, Any]) -> int:
    settings = league.get("settings") if isinstance(league.get("settings"), dict) else {}
    return _clamp(int(_f(settings.get("last_scored_leg"))), 0, 22)


def _current_week(league: dict[str, Any], state: dict[str, Any]) -> int:
    settings = league.get("settings") if isinstance(league.get("settings"), dict) else {}
    for value in (
        settings.get("leg"),
        state.get("display_week"),
        state.get("week"),
        _completed_weeks(league) + 1,
    ):
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= number <= 22:
            return number
    return 1


def _season(league: dict[str, Any], state: dict[str, Any]) -> int:
    try:
        return int(league.get("season") or state.get("season") or 0)
    except (TypeError, ValueError):
        return 0


def _playoff_config(league: dict[str, Any], roster_count: int) -> tuple[int, int]:
    settings = league.get("settings") if isinstance(league.get("settings"), dict) else {}
    teams = _clamp(int(_f(settings.get("playoff_teams")) or 6), 2, max(2, roster_count))
    start = _clamp(int(_f(settings.get("playoff_week_start")) or 15), 1, 22)
    return teams, start


def _resolve_roster(
    rosters: list[dict[str, Any]],
    roster_id: str | int | None,
    team_name: str | None,
    names: dict[str, str],
) -> dict[str, Any]:
    if roster_id not in (None, ""):
        for roster in rosters:
            if str(roster.get("roster_id")) == str(roster_id):
                return roster
    if team_name:
        wanted = str(team_name).strip().casefold()
        for roster in rosters:
            rid = str(roster.get("roster_id"))
            if names.get(rid, "").strip().casefold() == wanted:
                return roster
    raise ValueError(
        "Your team could not be matched to a roster in this Sleeper league. "
        "Ask the admin to set your Sleeper roster ID."
    )


def _roster_for_owner(
    rosters: list[dict[str, Any]], owner_id: str | int | None
) -> dict[str, Any] | None:
    if owner_id in (None, ""):
        return None
    wanted = str(owner_id)
    for roster in rosters:
        if str(roster.get("owner_id") or "") == wanted:
            return roster
    return None


def _owner_label(
    owners: dict[str, dict[str, str]] | None,
    owner_id: str,
    fallback: str = "",
) -> str:
    info = (owners or {}).get(str(owner_id or ""), {})
    return (
        info.get("username")
        or info.get("display_name")
        or fallback
    )


def _result(points: float, opponent_points: float) -> str:
    if abs(points - opponent_points) < 1e-9:
        return "T"
    return "W" if points > opponent_points else "L"


def _streak(games: list[dict[str, Any]]) -> str:
    if not games:
        return "—"
    last = games[-1]["result"]
    count = 0
    for game in reversed(games):
        if game["result"] != last:
            break
        count += 1
    return f"{last}{count}"


def _player_name(players: dict[str, dict[str, str]], pid: str | None) -> str:
    if not pid:
        return ""
    return str((players.get(str(pid)) or {}).get("n") or player_label(players, str(pid)))


def _game_log(
    weeks: dict[int, list[dict[str, Any]]],
    names: dict[str, str],
    players: dict[str, dict[str, str]],
    me_id: str,
    *,
    season: int = 0,
    owners: dict[str, dict[str, str]] | None = None,
    rosters_by_id: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    games: list[dict[str, Any]] = []
    rosters_by_id = rosters_by_id or {}
    for week in sorted(weeks):
        for team_a, team_b in pair_matchups(weeks[week]):
            if team_b is None:
                continue
            for side, opponent in ((team_a, team_b), (team_b, team_a)):
                rid = str(side.get("roster_id") or "")
                opp_id = str(opponent.get("roster_id") or "")
                if not rid or not opp_id:
                    continue
                starters = set(side.get("starters") or [])
                bench_ids = [
                    pid for pid in (side.get("players") or []) if pid not in starters
                ]
                bench_points = sum(
                    _f((side.get("players_points") or {}).get(pid)) for pid in bench_ids
                )
                top_bench = max(
                    bench_ids,
                    key=lambda pid: _f((side.get("players_points") or {}).get(pid)),
                    default="",
                )
                points = _f(side.get("points"))
                opp_points = _f(opponent.get("points"))
                owner_id = str(
                    (rosters_by_id.get(rid) or {}).get("owner_id") or ""
                )
                opp_owner_id = str(
                    (rosters_by_id.get(opp_id) or {}).get("owner_id") or ""
                )
                games.append(
                    {
                        "season": season,
                        "week": week,
                        "roster_id": rid,
                        "team": names.get(rid, f"Team {rid}"),
                        "owner_id": owner_id,
                        "owner_name": _owner_label(owners, owner_id),
                        "opponent_id": opp_id,
                        "opponent": names.get(opp_id, f"Team {opp_id}"),
                        "opponent_owner_id": opp_owner_id,
                        "opponent_owner_name": _owner_label(owners, opp_owner_id),
                        "points": round(points, 2),
                        "opponent_points": round(opp_points, 2),
                        "result": _result(points, opp_points),
                        "margin": round(points - opp_points, 2),
                        "abs_margin": round(abs(points - opp_points), 2),
                        "combined": round(points + opp_points, 2),
                        "bench_points": round(bench_points, 2),
                        "bench_player": _player_name(players, top_bench),
                        "is_me": bool(me_id) and rid == me_id,
                    }
                )
    return games


def _standings_rows(
    league: dict[str, Any],
    rosters: list[dict[str, Any]],
    names: dict[str, str],
    weeks: dict[int, list[dict[str, Any]]],
    players: dict[str, dict[str, str]],
    me_id: str,
    playoff_teams: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    games = _game_log(weeks, names, players, me_id)
    logs: dict[str, list[dict[str, Any]]] = {}
    for game in games:
        logs.setdefault(game["roster_id"], []).append(game)

    all_play: dict[str, dict[str, float]] = {
        str(roster.get("roster_id")): {
            "wins": 0,
            "losses": 0,
            "ties": 0,
            "games": 0,
            "expected": 0.0,
        }
        for roster in rosters
    }
    for rows in weeks.values():
        for row in rows:
            rid = str(row.get("roster_id") or "")
            if not rid:
                continue
            # All-play compares a team against every other team in the
            # league that week, not only against its actual matchup opponent.
            opponents = [
                other
                for other in rows
                if str(other.get("roster_id") or "") != rid
            ]
            if not opponents:
                continue
            points = _f(row.get("points"))
            wins = sum(1 for other in opponents if points > _f(other.get("points")) + 1e-9)
            losses = sum(1 for other in opponents if points < _f(other.get("points")) - 1e-9)
            ties = len(opponents) - wins - losses
            bucket = all_play[rid]
            bucket["wins"] += wins
            bucket["losses"] += losses
            bucket["ties"] += ties
            bucket["games"] += len(opponents)
            bucket["expected"] += (wins + 0.5 * ties) / len(opponents)

    waiver_budget = _f((league.get("settings") or {}).get("waiver_budget"))
    rows: list[dict[str, Any]] = []
    for roster in rosters:
        rid = str(roster.get("roster_id") or "")
        settings = roster.get("settings") or {}
        wins = int(_f(settings.get("wins")))
        losses = int(_f(settings.get("losses")))
        ties = int(_f(settings.get("ties")))
        games_played = wins + losses + ties
        win_pct = (wins + 0.5 * ties) / games_played if games_played else 0.0
        pf = points_total(settings)
        pa = points_total(
            {
                "fpts": settings.get("fpts_against"),
                "fpts_decimal": settings.get("fpts_against_decimal"),
            }
        )
        team_games = logs.get(rid, [])
        last5 = team_games[-5:]
        last5_pct = (
            sum(1.0 if game["result"] == "W" else 0.5 if game["result"] == "T" else 0.0
                for game in last5) / len(last5)
            if last5
            else 0.0
        )
        ap = all_play.get(rid, {"wins": 0, "losses": 0, "ties": 0, "games": 0, "expected": 0.0})
        ap_games = int(ap["games"])
        ap_pct = (ap["wins"] + 0.5 * ap["ties"]) / ap_games if ap_games else 0.0
        used = _f(settings.get("waiver_budget_used"))
        rows.append(
            {
                "roster_id": rid,
                "name": names.get(rid, f"Team {rid}"),
                "wins": wins,
                "losses": losses,
                "ties": ties,
                "games": games_played,
                "win_pct": win_pct,
                "pf": pf,
                "pa": pa,
                "ppg": pf / games_played if games_played else 0.0,
                "all_play_wins": int(ap["wins"]),
                "all_play_losses": int(ap["losses"]),
                "all_play_ties": int(ap["ties"]),
                "all_play_pct": ap_pct,
                "expected_wins": ap["expected"],
                "luck": (wins + 0.5 * ties) - ap["expected"],
                "last5_pct": last5_pct,
                "form": [game["result"] for game in last5],
                "streak": _streak(team_games),
                "waiver_remaining": max(0.0, waiver_budget - used) if waiver_budget else None,
                "is_me": rid == me_id,
            }
        )

    rows.sort(key=lambda row: (row["win_pct"], row["pf"]), reverse=True)
    leader = rows[0] if rows else None
    for index, row in enumerate(rows, 1):
        row["rank"] = index
        row["playoff_spot"] = index <= playoff_teams
        row["games_back"] = (
            max(
                0.0,
                ((leader["wins"] - row["wins"]) + (row["losses"] - leader["losses"])) / 2,
            )
            if leader
            else 0.0
        )

    max_pf = max((row["pf"] for row in rows), default=0.0) or 1.0
    for row in rows:
        row["power_score"] = round(
            0.40 * row["win_pct"]
            + 0.30 * row["all_play_pct"]
            + 0.20 * (row["pf"] / max_pf)
            + 0.10 * row["last5_pct"],
            4,
        )

    power_rows = sorted(rows, key=lambda row: row["power_score"], reverse=True)
    for index, row in enumerate(power_rows, 1):
        row["power_rank"] = index
    return rows, power_rows


def get_standings(
    league_id: str,
    *,
    roster_id: str | int | None = None,
    team_name: str | None = None,
) -> dict[str, Any]:
    """Standings, all-play records and power rankings for the logged-in team."""
    with SleeperClient() as client:
        base = _load_base(client, league_id)
        league = base["league"]
        try:
            state = client.nfl_state()
        except Exception as exc:  # noqa: BLE001 - page still works without state
            log.warning("NFL state unavailable for standings: %s", exc)
            state = {}
        completed = _completed_weeks(league)
        weeks = {
            week: _load_week(client, league_id, week)
            for week in range(1, completed + 1)
        }
        players: dict[str, dict[str, str]] = {}
        try:
            players = ensure_player_cache(client, max_age_s=PLAYERS_INJURY_MAX_AGE_S)
        except Exception as exc:  # noqa: BLE001 - names fall back to player ids
            log.warning("Player cache unavailable for standings: %s", exc)
        me = _resolve_roster(base["rosters"], roster_id, team_name, base["names"])
        me_id = str(me.get("roster_id") or "")
        playoff_teams, playoff_start = _playoff_config(league, len(base["rosters"]))
        standings, power = _standings_rows(
            league,
            base["rosters"],
            base["names"],
            weeks,
            players,
            me_id,
            playoff_teams,
        )
        current_week = _current_week(league, state)
        my = next((row for row in standings if row["is_me"]), None)
        my_power = next((row for row in power if row["is_me"]), None)
        return {
            "league_name": league.get("name") or "",
            "season": _season(league, state),
            "weeks_played": completed,
            "current_week": current_week,
            "playoff_teams": playoff_teams,
            "playoff_week_start": playoff_start,
            "games_left": max(0, playoff_start - current_week),
            "standings": standings,
            "power": power,
            "my": my,
            "my_power": my_power,
        }


def _win_probability(points_a: float, points_b: float, sigma: float = 25.0) -> float:
    diff = _f(points_a) - _f(points_b)
    probability = 0.5 * (1.0 + math.erf(diff / (sigma * math.sqrt(2.0))))
    return max(0.05, min(0.95, probability))


def _side_summary(
    row: dict[str, Any],
    roster: dict[str, Any] | None,
    names: dict[str, str],
    players: dict[str, dict[str, str]],
    projections: dict[str, dict[str, Any]],
    scoring: dict[str, Any],
    slots: list[str],
    me_id: str,
) -> dict[str, Any]:
    rid = str(row.get("roster_id") or "")
    settings = (roster or {}).get("settings") or {}
    wins = int(_f(settings.get("wins")))
    losses = int(_f(settings.get("losses")))
    ties = int(_f(settings.get("ties")))
    starters = row.get("starters") or []
    starter_points = row.get("starters_points") or []
    points_map = row.get("players_points") or {}
    starter_rows: list[dict[str, Any]] = []
    for index, pid in enumerate(starters):
        if not pid:
            continue
        slot = slots[index] if index < len(slots) else "FLEX"
        actual = (
            starter_points[index]
            if index < len(starter_points)
            else _f(points_map.get(pid))
        )
        info = players.get(str(pid)) or {}
        starter_rows.append(
            {
                "pid": str(pid),
                "slot": slot,
                "name": _player_name(players, str(pid)),
                "pos": str(info.get("p") or ""),
                "team": str(info.get("t") or "").upper(),
                "injury": str(info.get("inj") or ""),
                "points": round(_f(actual), 2),
                "proj": round(league_points(projections.get(str(pid)) or {}, scoring), 2),
            }
        )

    projected = sum(player["proj"] for player in starter_rows)
    source = "Sleeper projections"
    if projected <= 0:
        games_played = wins + losses + ties
        projected = points_total(settings) / games_played if games_played else 0.0
        source = "Season average"

    starter_ids = {player["pid"] for player in starter_rows}
    bench_ids = [
        pid for pid in (row.get("players") or []) if pid not in starter_ids
    ]
    bench_points = sum(_f(points_map.get(pid)) for pid in bench_ids)
    top_bench_id = max(
        bench_ids,
        key=lambda pid: _f(points_map.get(pid)),
        default="",
    )
    bench_top = (
        {
            "name": _player_name(players, top_bench_id),
            "points": round(_f(points_map.get(top_bench_id)), 2),
        }
        if top_bench_id
        else None
    )

    return {
        "roster_id": rid,
        "name": names.get(rid, f"Team {rid}"),
        "record": f"{wins}-{losses}" + (f"-{ties}" if ties else ""),
        "points": round(_f(row.get("points")), 2),
        "projected": round(projected, 1),
        "projection_source": source,
        "starters": starter_rows,
        "remaining": [player for player in starter_rows if player["points"] == 0],
        "bench_points": round(bench_points, 2),
        "bench_top": bench_top,
        "is_me": rid == me_id,
    }


def get_matchup_center(
    league_id: str,
    *,
    roster_id: str | int | None = None,
    team_name: str | None = None,
    week: int | None = None,
) -> dict[str, Any]:
    """Current or selected week's matchups, focused on the logged-in team."""
    with SleeperClient() as client:
        base = _load_base(client, league_id)
        league = base["league"]
        try:
            state = client.nfl_state()
        except Exception as exc:  # noqa: BLE001
            log.warning("NFL state unavailable for matchup center: %s", exc)
            state = {}
        current_week = _current_week(league, state)
        completed = _completed_weeks(league)
        selected = _clamp(week or current_week, 1, 22)
        rows = _load_week(client, league_id, selected)
        me = _resolve_roster(base["rosters"], roster_id, team_name, base["names"])
        me_id = str(me.get("roster_id") or "")

        players: dict[str, dict[str, str]] = {}
        try:
            players = ensure_player_cache(client, max_age_s=PLAYERS_INJURY_MAX_AGE_S)
        except Exception as exc:  # noqa: BLE001
            log.warning("Player cache unavailable for matchup center: %s", exc)

        season = _season(league, state)
        projections: dict[str, dict[str, Any]] = {}
        if season and rows:
            try:
                projections = load_week_bundle(
                    client, season, selected, projections=True
                )
            except Exception as exc:  # noqa: BLE001 - fall back to season averages
                log.warning("Projections unavailable for week %s: %s", selected, exc)

        scoring = league.get("scoring_settings") or {}
        slots = starter_slots(league.get("roster_positions") or [])
        rosters_by_id = {
            str(roster.get("roster_id")): roster for roster in base["rosters"]
        }
        is_final = selected <= completed
        pairs: list[dict[str, Any]] = []
        for team_a, team_b in pair_matchups(rows):
            side_a = _side_summary(
                team_a,
                rosters_by_id.get(str(team_a.get("roster_id"))),
                base["names"],
                players,
                projections,
                scoring,
                slots,
                me_id,
            )
            if team_b is None:
                side_a["win_prob"] = None
                pairs.append(
                    {
                        "matchup_id": team_a.get("matchup_id"),
                        "team_a": side_a,
                        "team_b": None,
                        "is_me": side_a["is_me"],
                    }
                )
                continue
            side_b = _side_summary(
                team_b,
                rosters_by_id.get(str(team_b.get("roster_id"))),
                base["names"],
                players,
                projections,
                scoring,
                slots,
                me_id,
            )
            if is_final:
                probability = (
                    1.0
                    if side_a["points"] > side_b["points"]
                    else 0.0
                    if side_a["points"] < side_b["points"]
                    else 0.5
                )
            else:
                probability = _win_probability(side_a["projected"], side_b["projected"])
            side_a["win_prob"] = probability
            side_b["win_prob"] = 1.0 - probability
            pairs.append(
                {
                    "matchup_id": team_a.get("matchup_id"),
                    "team_a": side_a,
                    "team_b": side_b,
                    "is_me": side_a["is_me"] or side_b["is_me"],
                }
            )

        pairs.sort(key=lambda pair: (pair["matchup_id"] is None, pair["matchup_id"] or 0))
        my_pair = next((pair for pair in pairs if pair["is_me"]), None)
        return {
            "week": selected,
            "current_week": current_week,
            "completed_week": completed,
            "is_final": is_final,
            "my_pair": my_pair,
            "other_pairs": [pair for pair in pairs if not pair["is_me"]],
            "pairs": pairs,
            "projection_note": any(
                side.get("projection_source") == "Season average"
                for pair in pairs
                for side in (pair["team_a"], pair["team_b"])
                if side
            ),
        }


def _weekly_awards(week: int, games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not games:
        return []

    def award(key, label, emoji, game, value, detail):
        return {
            "key": key,
            "label": label,
            "emoji": emoji,
            "week": week,
            "team": game["team"],
            "opponent": game["opponent"],
            "value": value,
            "detail": detail,
            "is_me": game["is_me"],
        }

    highest = max(games, key=lambda game: game["points"])
    lowest = min(games, key=lambda game: game["points"])
    blowout = max(games, key=lambda game: game["abs_margin"])
    closest = min(games, key=lambda game: game["abs_margin"])
    bench = max(games, key=lambda game: game["bench_points"])
    shootout = max(games, key=lambda game: game["combined"])
    losses = [game for game in games if game["result"] == "L"]
    bad_beat = max(losses, key=lambda game: game["points"]) if losses else None

    awards = [
        award(
            "highest", "Highest score", "🚀", highest,
            f"{highest['points']:.2f}",
            f"vs {highest['opponent']}",
        ),
        award(
            "lowest", "Lowest score", "🧊", lowest,
            f"{lowest['points']:.2f}",
            f"vs {lowest['opponent']}",
        ),
        award(
            "blowout", "Biggest blowout", "💥", blowout,
            f"+{blowout['abs_margin']:.2f}",
            f"{blowout['team']} over {blowout['opponent']}",
        ),
        award(
            "closest", "Closest game", "📏", closest,
            f"{closest['abs_margin']:.2f}",
            f"{closest['team']} vs {closest['opponent']}",
        ),
        award(
            "bench", "Bench MVP", "🪑", bench,
            f"{bench['bench_points']:.2f}",
            bench["bench_player"] or "No bench points",
        ),
        award(
            "shootout", "Shootout", "🎪", shootout,
            f"{shootout['combined']:.2f}",
            f"{shootout['team']} vs {shootout['opponent']}",
        ),
    ]
    if bad_beat:
        awards.append(
            award(
                "bad_beat", "Bad beat", "💔", bad_beat,
                f"{bad_beat['points']:.2f}",
                f"lost by {bad_beat['abs_margin']:.2f}",
            )
        )
    return awards


def _season_records(
    games: list[dict[str, Any]],
    standings: list[dict[str, Any]],
    *,
    all_time: bool = False,
) -> list[dict[str, Any]]:
    if not games:
        return []

    def record(key, label, emoji, game, value, detail):
        return {
            "key": key,
            "label": label,
            "emoji": emoji,
            "team": game["team"],
            "owner_name": game.get("owner_name") or "",
            "opponent": game["opponent"],
            "season": game.get("season"),
            "week": game["week"],
            "value": value,
            "detail": detail,
            "is_me": game["is_me"],
        }

    highest = max(games, key=lambda game: game["points"])
    lowest = min(games, key=lambda game: game["points"])
    blowout = max(games, key=lambda game: game["abs_margin"])
    closest = min(games, key=lambda game: game["abs_margin"])
    bench = max(games, key=lambda game: game["bench_points"])
    shootout = max(games, key=lambda game: game["combined"])
    losses = [game for game in games if game["result"] == "L"]
    bad_beat = max(losses, key=lambda game: game["points"]) if losses else None

    # Group by stable Sleeper user when available. That keeps a manager's
    # history together even when the team name or roster id changes.
    by_team: dict[str, list[dict[str, Any]]] = {}
    for game in games:
        key = game.get("owner_id") or game["roster_id"]
        by_team.setdefault(str(key), []).append(game)
    longest_team = ""
    longest_streak = 0
    longest_game = games[0]
    for team_games in by_team.values():
        team_games.sort(
            key=lambda game: (game.get("season") or 0, game["week"])
        )
        current = 0
        current_season = None
        for game in team_games:
            season = game.get("season")
            if current_season is not None and season != current_season:
                current = 0
            current_season = season
            if game["result"] == "W":
                current += 1
                if current > longest_streak:
                    longest_streak = current
                    longest_team = game["team"]
                    longest_game = game
            else:
                current = 0

    records = [
        record(
            "highest", "Highest score", "🚀", highest,
            f"{highest['points']:.2f}",
            f"Week {highest['week']} vs {highest['opponent']}",
        ),
        record(
            "lowest", "Lowest score", "🧊", lowest,
            f"{lowest['points']:.2f}",
            f"Week {lowest['week']} vs {lowest['opponent']}",
        ),
        record(
            "blowout", "Biggest blowout", "💥", blowout,
            f"+{blowout['abs_margin']:.2f}",
            f"Week {blowout['week']}: {blowout['team']} over {blowout['opponent']}",
        ),
        record(
            "closest", "Closest game", "📏", closest,
            f"{closest['abs_margin']:.2f}",
            f"Week {closest['week']}: {closest['team']} vs {closest['opponent']}",
        ),
        record(
            "bench", "Best bench game", "🪑", bench,
            f"{bench['bench_points']:.2f}",
            f"Week {bench['week']}: {bench['bench_player'] or 'bench'}",
        ),
        record(
            "shootout", "Highest combined score", "🎪", shootout,
            f"{shootout['combined']:.2f}",
            f"Week {shootout['week']}: {shootout['team']} vs {shootout['opponent']}",
        ),
    ]
    if bad_beat:
        records.append(
            record(
                "bad_beat", "Most points in a loss", "💔", bad_beat,
                f"{bad_beat['points']:.2f}",
                f"Week {bad_beat['week']}: lost by {bad_beat['abs_margin']:.2f}",
            )
        )
    if longest_streak:
        records.append(
            record(
                "streak", "Longest win streak", "🔥", longest_game,
                f"{longest_streak} games",
                f"{longest_team}",
            )
        )

    best_record = standings[0] if standings else None
    most_points = max(standings, key=lambda row: row["pf"], default=None)
    period = "All seasons" if all_time else "Regular season"
    if best_record:
        records.append(
            {
                "key": "best_record",
                "label": "Best record",
                "emoji": "🏆",
                "team": best_record["name"],
                "owner_name": best_record.get("owner_name", ""),
                "opponent": "",
                "season": None,
                "week": None,
                "value": f"{best_record['wins']}-{best_record['losses']}"
                + (f"-{best_record['ties']}" if best_record["ties"] else ""),
                "detail": period,
                "is_me": best_record["is_me"],
            }
        )
    if most_points:
        records.append(
            {
                "key": "most_points",
                "label": "Most points",
                "emoji": "📈",
                "team": most_points["name"],
                "owner_name": most_points.get("owner_name", ""),
                "opponent": "",
                "season": None,
                "week": None,
                "value": f"{most_points['pf']:.2f}",
                "detail": f"{period} total",
                "is_me": most_points["is_me"],
            }
        )
    return records


def _head_to_head(
    my_games: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for game in my_games:
        opponent_key = (
            game.get("opponent_owner_id")
            or game.get("opponent_id")
            or game.get("opponent")
        )
        row = rows.setdefault(
            opponent_key,
            {
                "opponent_id": game.get("opponent_id", ""),
                "opponent": game["opponent"],
                "opponent_owner_name": game.get("opponent_owner_name", ""),
                "wins": 0,
                "losses": 0,
                "ties": 0,
                "games": 0,
                "points_for": 0.0,
                "points_against": 0.0,
                "last_week": 0,
                "last_season": 0,
                "last_result": "",
            },
        )
        row["games"] += 1
        row["points_for"] += game["points"]
        row["points_against"] += game["opponent_points"]
        if game["result"] == "W":
            row["wins"] += 1
        elif game["result"] == "L":
            row["losses"] += 1
        else:
            row["ties"] += 1
        game_key = (game.get("season") or 0, game["week"])
        row_key = (row.get("last_season") or 0, row["last_week"])
        if game_key >= row_key:
            row["last_week"] = game["week"]
            row["last_season"] = game.get("season") or 0
            row["last_result"] = game["result"]
    return sorted(
        rows.values(),
        key=lambda row: (-row["games"], row["opponent"].casefold()),
    )


def _all_time_standings(
    games: list[dict[str, Any]],
    owners: dict[str, dict[str, str]],
    me_owner_id: str,
) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = {}
    for game in games:
        key = str(game.get("owner_id") or game.get("roster_id") or "")
        if not key:
            continue
        bucket = buckets.setdefault(
            key,
            {
                "roster_id": key,
                "name": "",
                "owner_name": "",
                "wins": 0,
                "losses": 0,
                "ties": 0,
                "pf": 0.0,
                "pa": 0.0,
                "games": 0,
            },
        )
        bucket["name"] = (
            _owner_label(owners, key, game.get("owner_name") or game["team"])
            or game["team"]
        )
        bucket["owner_name"] = game.get("owner_name") or bucket["name"]
        bucket["games"] += 1
        bucket["pf"] += game["points"]
        bucket["pa"] += game["opponent_points"]
        if game["result"] == "W":
            bucket["wins"] += 1
        elif game["result"] == "L":
            bucket["losses"] += 1
        else:
            bucket["ties"] += 1

    rows = []
    for bucket in buckets.values():
        games_played = bucket["games"]
        rows.append(
            {
                **bucket,
                "win_pct": (
                    (bucket["wins"] + 0.5 * bucket["ties"]) / games_played
                    if games_played
                    else 0.0
                ),
                "is_me": bool(me_owner_id)
                and str(bucket["roster_id"]) == str(me_owner_id),
            }
        )
    rows.sort(key=lambda row: (row["win_pct"], row["pf"]), reverse=True)
    for index, row in enumerate(rows, 1):
        row["rank"] = index
    return rows


def _all_time_summary(
    games: list[dict[str, Any]],
    me_owner_id: str,
    award_count: int,
) -> dict[str, Any]:
    my_games = [game for game in games if game["is_me"]]
    wins = sum(1 for game in my_games if game["result"] == "W")
    losses = sum(1 for game in my_games if game["result"] == "L")
    ties = sum(1 for game in my_games if game["result"] == "T")
    best = max(my_games, key=lambda game: game["points"], default=None)
    worst = min(my_games, key=lambda game: game["points"], default=None)
    return {
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "games": len(my_games),
        "pf": sum(game["points"] for game in my_games),
        "pa": sum(game["opponent_points"] for game in my_games),
        "best": best,
        "worst": worst,
        "award_count": award_count,
        "seasons": sorted(
            {
                int(game["season"])
                for game in my_games
                if game.get("season")
            }
        ),
    }


def _all_time_awards(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_week: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for game in games:
        key = (int(game.get("season") or 0), int(game["week"]))
        by_week.setdefault(key, []).append(game)
    awards = []
    for (season, week), week_games in sorted(by_week.items()):
        for award in _weekly_awards(week, week_games):
            award["season"] = season
            awards.append(award)
    return awards


def _history_bases(
    client: SleeperClient,
    current_league: dict[str, Any],
) -> list[dict[str, Any]]:
    bases: list[dict[str, Any]] = []
    seen: set[str] = set()
    previous = str(current_league.get("previous_league_id") or "")
    while previous and previous not in seen and len(bases) < 10:
        seen.add(previous)
        try:
            base = _load_base(
                client, previous, ttl=_HISTORY_CACHE_TTL_S
            )
        except Exception as exc:  # noqa: BLE001 - keep the current season usable
            log.warning("Previous league %s unavailable: %s", previous, exc)
            break
        bases.append(base)
        previous = str(base["league"].get("previous_league_id") or "")
    return bases


def _load_history_weeks(
    bases: list[dict[str, Any]],
) -> dict[str, dict[int, list[dict[str, Any]]]]:
    tasks = []
    for base in bases:
        league = base["league"]
        league_id = str(league.get("league_id") or "")
        completed = _completed_weeks(league)
        if league_id and completed:
            tasks.append((league_id, completed))
    if not tasks:
        return {}

    def load(task):
        league_id, completed = task
        try:
            with SleeperClient() as client:
                return league_id, {
                    week: _load_week(
                        client,
                        league_id,
                        week,
                        ttl=_HISTORY_CACHE_TTL_S,
                    )
                    for week in range(1, completed + 1)
                }
        except Exception as exc:  # noqa: BLE001
            log.warning("History weeks unavailable for %s: %s", league_id, exc)
            return league_id, {}

    with ThreadPoolExecutor(max_workers=min(3, len(tasks))) as pool:
        return dict(pool.map(load, tasks))


def _history_games(
    bases: list[dict[str, Any]],
    weeks_by_league: dict[str, dict[int, list[dict[str, Any]]]],
    players: dict[str, dict[str, str]],
    me_owner_id: str,
) -> list[dict[str, Any]]:
    games: list[dict[str, Any]] = []
    for base in bases:
        league = base["league"]
        league_id = str(league.get("league_id") or "")
        roster = _roster_for_owner(base["rosters"], me_owner_id)
        me_id = str((roster or {}).get("roster_id") or "")
        rosters_by_id = {
            str(item.get("roster_id") or ""): item
            for item in base["rosters"]
        }
        games.extend(
            _game_log(
                weeks_by_league.get(league_id, {}),
                base["names"],
                players,
                me_id,
                season=_season(league, {}),
                owners=base.get("owners"),
                rosters_by_id=rosters_by_id,
            )
        )
    return games


def _championships(
    client: SleeperClient,
    bases: list[dict[str, Any]],
    me_owner_id: str,
) -> list[dict[str, Any]]:
    rows = []
    for base in bases:
        league = base["league"]
        league_id = str(league.get("league_id") or "")
        if not league_id:
            continue
        try:
            bracket = client.winners_bracket(league_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("Winners bracket unavailable for %s: %s", league_id, exc)
            continue
        final = next(
            (
                match
                for match in bracket
                if isinstance(match, dict)
                and match.get("p") == 1
                and match.get("w") is not None
            ),
            None,
        )
        if not final:
            continue
        champion_id = str(final.get("w"))
        roster = next(
            (
                item
                for item in base["rosters"]
                if str(item.get("roster_id") or "") == champion_id
            ),
            None,
        )
        owner_id = str((roster or {}).get("owner_id") or "")
        champion = base["names"].get(champion_id, f"Team {champion_id}")
        rows.append(
            {
                "season": _season(league, {}),
                "league_id": league_id,
                "champion": champion,
                "owner_name": _owner_label(
                    base.get("owners"), owner_id, champion
                ),
                "is_me": bool(me_owner_id)
                and owner_id == str(me_owner_id),
            }
        )
    return sorted(rows, key=lambda row: row["season"] or 0, reverse=True)


def get_record_book(
    league_id: str,
    *,
    roster_id: str | int | None = None,
    team_name: str | None = None,
    include_history: bool = False,
) -> dict[str, Any]:
    """Season awards, record book and the logged-in team's head-to-head history."""
    with SleeperClient() as client:
        base = _load_base(client, league_id)
        league = base["league"]
        try:
            state = client.nfl_state()
        except Exception as exc:  # noqa: BLE001
            log.warning("NFL state unavailable for record book: %s", exc)
            state = {}
        completed = _completed_weeks(league)
        weeks = {
            week: _load_week(client, league_id, week)
            for week in range(1, completed + 1)
        }
        players: dict[str, dict[str, str]] = {}
        try:
            players = ensure_player_cache(client, max_age_s=PLAYERS_INJURY_MAX_AGE_S)
        except Exception as exc:  # noqa: BLE001
            log.warning("Player cache unavailable for record book: %s", exc)
        me = _resolve_roster(base["rosters"], roster_id, team_name, base["names"])
        me_id = str(me.get("roster_id") or "")
        me_owner_id = str(me.get("owner_id") or "")
        season = _season(league, state)
        playoff_teams, _ = _playoff_config(league, len(base["rosters"]))
        standings, _power = _standings_rows(
            league,
            base["rosters"],
            base["names"],
            weeks,
            players,
            me_id,
            playoff_teams,
        )
        rosters_by_id = {
            str(roster.get("roster_id") or ""): roster
            for roster in base["rosters"]
        }
        games = _game_log(
            weeks,
            base["names"],
            players,
            me_id,
            season=season,
            owners=base.get("owners"),
            rosters_by_id=rosters_by_id,
        )
        my_games = [game for game in games if game["is_me"]]
        latest_week = max(weeks) if weeks else 0
        latest_games = [game for game in games if game["week"] == latest_week]
        weekly_awards = _weekly_awards(latest_week, latest_games)
        all_awards = []
        for week in sorted(weeks):
            all_awards.extend(
                _weekly_awards(week, [game for game in games if game["week"] == week])
            )
        my_awards = [award for award in all_awards if award["is_me"]]
        my_row = next((row for row in standings if row["is_me"]), None)
        my_best = max(my_games, key=lambda game: game["points"], default=None)
        my_worst = min(my_games, key=lambda game: game["points"], default=None)
        page = {
            "scope": "all" if include_history else "season",
            "season": season,
            "weeks_played": completed,
            "latest_week": latest_week,
            "weekly_awards": weekly_awards,
            "my_awards": my_awards,
            "my_award_count": len(my_awards),
            "records": _season_records(games, standings),
            "my": my_row,
            "my_best": my_best,
            "my_worst": my_worst,
            "head_to_head": _head_to_head(my_games),
            "all_time": None,
        }

        if include_history:
            history_bases = _history_bases(client, league)
            history_weeks = _load_history_weeks(history_bases)
            history_games = _history_games(
                history_bases, history_weeks, players, me_owner_id
            )
            all_games = games + history_games
            all_owners: dict[str, dict[str, str]] = dict(base.get("owners") or {})
            for history_base in history_bases:
                all_owners.update(history_base.get("owners") or {})
            all_time_standings = _all_time_standings(
                all_games, all_owners, me_owner_id
            )
            all_time_awards = _all_time_awards(all_games)
            my_all_time_awards = [
                award for award in all_time_awards if award["is_me"]
            ]
            all_seasons = sorted(
                {
                    int(item["league"].get("season") or 0)
                    for item in [base] + history_bases
                    if int(item["league"].get("season") or 0)
                }
            )
            page["head_to_head"] = _head_to_head(
                [game for game in all_games if game["is_me"]]
            )
            page["all_time"] = {
                "seasons": all_seasons,
                "records": _season_records(
                    all_games, all_time_standings, all_time=True
                ),
                "my": _all_time_summary(
                    all_games, me_owner_id, len(my_all_time_awards)
                ),
                "my_awards": my_all_time_awards,
                "championships": _championships(
                    client, [base] + history_bases, me_owner_id
                ),
                "games_played": len(all_games) // 2,
            }
        return page


def _win_pct(state: dict[str, Any]) -> float:
    games = state["wins"] + state["losses"] + state["ties"]
    return (state["wins"] + 0.5 * state["ties"]) / games if games else 0.0


def _simulate_playoffs(
    standings: list[dict[str, Any]],
    schedule: list[tuple[int, str, str]],
    playoff_teams: int,
    *,
    simulations: int = 2000,
    seed: str = "ligafunk",
) -> dict[str, Any]:
    """Monte-Carlo simulation of the remaining regular season."""
    if not standings:
        return {"teams": {}, "simulations": simulations}
    rng = random.Random(seed)
    teams = [str(row["roster_id"]) for row in standings]
    base = {
        str(row["roster_id"]): {
            "wins": int(row["wins"]),
            "losses": int(row["losses"]),
            "ties": int(row["ties"]),
            "pf": float(row["pf"]),
        }
        for row in standings
    }
    strength = {
        str(row["roster_id"]): max(1.0, float(row["ppg"])) for row in standings
    }
    results = {
        rid: {
            "playoff": 0,
            "seeds": {index: 0 for index in range(1, len(teams) + 1)},
            "seed_sum": 0,
        }
        for rid in teams
    }

    for _ in range(simulations):
        state = {rid: dict(values) for rid, values in base.items()}
        for _week, team_a, team_b in schedule:
            if team_a not in state or team_b not in state:
                continue
            score_a = strength[team_a] + rng.gauss(0, 18)
            score_b = strength[team_b] + rng.gauss(0, 18)
            state[team_a]["pf"] += score_a
            state[team_b]["pf"] += score_b
            if score_a > score_b:
                state[team_a]["wins"] += 1
                state[team_b]["losses"] += 1
            elif score_b > score_a:
                state[team_b]["wins"] += 1
                state[team_a]["losses"] += 1
            else:
                state[team_a]["ties"] += 1
                state[team_b]["ties"] += 1

        ranked = sorted(
            teams,
            key=lambda rid: (_win_pct(state[rid]), state[rid]["pf"]),
            reverse=True,
        )
        for index, rid in enumerate(ranked, 1):
            results[rid]["seeds"][index] += 1
            results[rid]["seed_sum"] += index
            if index <= playoff_teams:
                results[rid]["playoff"] += 1

    for rid, result in results.items():
        result["playoff_prob"] = result["playoff"] / simulations
        result["avg_seed"] = result["seed_sum"] / simulations
    return {"teams": results, "simulations": simulations}


def get_playoff_odds(
    league_id: str,
    *,
    roster_id: str | int | None = None,
    team_name: str | None = None,
    simulations: int = 2000,
) -> dict[str, Any]:
    """Playoff probabilities and remaining schedule for the logged-in team."""
    with SleeperClient() as client:
        base = _load_base(client, league_id)
        league = base["league"]
        try:
            state = client.nfl_state()
        except Exception as exc:  # noqa: BLE001
            log.warning("NFL state unavailable for playoff odds: %s", exc)
            state = {}
        current_week = _current_week(league, state)
        completed = _completed_weeks(league)
        playoff_teams, playoff_start = _playoff_config(league, len(base["rosters"]))
        weeks = {
            week: _load_week(client, league_id, week)
            for week in range(1, completed + 1)
        }
        players: dict[str, dict[str, str]] = {}
        try:
            players = ensure_player_cache(client, max_age_s=PLAYERS_INJURY_MAX_AGE_S)
        except Exception as exc:  # noqa: BLE001
            log.warning("Player cache unavailable for playoff odds: %s", exc)
        me = _resolve_roster(base["rosters"], roster_id, team_name, base["names"])
        me_id = str(me.get("roster_id") or "")
        standings, _power = _standings_rows(
            league,
            base["rosters"],
            base["names"],
            weeks,
            players,
            me_id,
            playoff_teams,
        )

        remaining_weeks = list(range(current_week, playoff_start)) if current_week < playoff_start else []
        remaining = {
            week: _load_week(client, league_id, week) for week in remaining_weeks
        }
        schedule: list[tuple[int, str, str]] = []
        for week, rows in remaining.items():
            for team_a, team_b in pair_matchups(rows):
                if team_b is None:
                    continue
                schedule.append(
                    (
                        week,
                        str(team_a.get("roster_id") or ""),
                        str(team_b.get("roster_id") or ""),
                    )
                )

        result = _simulate_playoffs(
            standings,
            schedule,
            playoff_teams,
            simulations=simulations,
            seed=f"{league_id}:{current_week}:{playoff_start}",
        )
        by_id = {str(row["roster_id"]): row for row in standings}
        league_rows = []
        for row in standings:
            simulation = result["teams"].get(str(row["roster_id"]), {})
            seed_counts = simulation.get("seeds", {})
            league_rows.append(
                {
                    **row,
                    "playoff_prob": simulation.get("playoff_prob", 0.0),
                    "avg_seed": simulation.get("avg_seed", row["rank"]),
                    "seed_distribution": [
                        {
                            "seed": index,
                            "count": count,
                            "pct": count / simulations,
                        }
                        for index, count in sorted(seed_counts.items())
                    ],
                }
            )
        my = next((row for row in league_rows if row["is_me"]), None)
        my_remaining = []
        strength = {rid: max(1.0, row["ppg"]) for rid, row in by_id.items()}
        for week, rows in remaining.items():
            for team_a, team_b in pair_matchups(rows):
                if team_b is None:
                    continue
                if str(team_a.get("roster_id")) == me_id:
                    opponent = team_b
                elif str(team_b.get("roster_id")) == me_id:
                    opponent = team_a
                else:
                    continue
                opp_id = str(opponent.get("roster_id") or "")
                opp_row = by_id.get(opp_id)
                my_remaining.append(
                    {
                        "week": week,
                        "opponent": base["names"].get(opp_id, f"Team {opp_id}"),
                        "opponent_record": (
                            f"{opp_row['wins']}-{opp_row['losses']}"
                            + (f"-{opp_row['ties']}" if opp_row and opp_row["ties"] else "")
                            if opp_row
                            else "—"
                        ),
                        "win_prob": _win_probability(
                            strength.get(me_id, 1.0),
                            strength.get(opp_id, 1.0),
                            sigma=18.0,
                        ),
                    }
                )
        return {
            "season": _season(league, state),
            "current_week": current_week,
            "playoff_week_start": playoff_start,
            "playoff_teams": playoff_teams,
            "remaining_weeks": remaining_weeks,
            "simulations": simulations,
            "my": my,
            "my_remaining": my_remaining,
            "league_rows": league_rows,
            "season_complete": not remaining_weeks,
            "schedule_available": bool(schedule),
            "note": (
                "No remaining matchups were available; odds are based on the current table."
                if not schedule
                else ""
            ),
        }
