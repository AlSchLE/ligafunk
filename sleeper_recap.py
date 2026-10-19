"""
Sleeper Web — Recap & Start/Sit generation via Sleeper API.

Uses the local recapcore module for:
- Automatic recap generation (league_recap)
- Start/Sit advisor (evaluate_roster)
- Waiver assistant (render_waivers)
"""
from __future__ import annotations

import sys
import os
from pathlib import Path
from typing import Any

# Add local path for recapcore imports
_local_path = str(Path(__file__).resolve().parent)
if _local_path not in sys.path:
    sys.path.insert(0, _local_path)

from recapcore.client import SleeperClient
from recapcore.config import LEAGUES, MY_TEAMS
from recapcore.narrate import league_recap
from recapcore.service import cmd_fetch, cmd_waivers, cmd_startsit
from recapcore.startsit import (
    build_context,
    defense_allowed_table,
    evaluate_roster,
    last_week_league_points,
    render_league as render_startsit,
)
from recapcore.waivers import (
    build_claims,
    candidate_ids,
    faab_state,
    render_waivers,
    roster_cap,
    rostered_ids,
    score_pool,
)
from recapcore.league_format import starter_slots
from recapcore.store import ensure_player_cache, load_snapshot, fetch_snapshot


def generate_recap(league_id: str, week: int | None = None) -> str:
    """Generate a weekly recap for a league."""
    snapshot = load_snapshot()
    if snapshot is None:
        snapshot = cmd_fetch(week=week)
    elif week is not None and int(snapshot.get("requested_week") or 0) != week:
        snapshot = cmd_fetch(week=week)

    with SleeperClient() as client:
        players = ensure_player_cache(client)

    # Find league by ID
    block = None
    for key, lid in LEAGUES.items():
        if lid == league_id:
            block = (snapshot.get("leagues") or {}).get(key)
            break

    if block is None:
        # Try direct lookup
        with SleeperClient() as client:
            league_data = client.league(league_id)
            users = client.users(league_id)
            rosters = client.rosters(league_id)
            block = {
                "league": league_data,
                "users": users,
                "rosters": rosters,
                "team_names": {},
                "week": week or 1,
                "matchups": client.matchups(league_id, week or 1),
                "transactions": client.transactions(league_id, week or 1),
            }

    recap_week = week or (block.get("week") or 1)
    return league_recap(block, players, week=int(recap_week))


def generate_startsit(league_id: str, week: int | None = None) -> str:
    """Generate start/sit advice for a league."""
    snapshot = load_snapshot()
    if snapshot is None:
        snapshot = cmd_fetch(week=week)
    elif week is not None and int(snapshot.get("requested_week") or 0) != week:
        snapshot = cmd_fetch(week=week)

    with SleeperClient() as client:
        players = ensure_player_cache(client)
        ctx = build_context(client, snapshot, week=week or snapshot.get("requested_week", 1))

    # Find league by ID
    block = None
    for key, lid in LEAGUES.items():
        if lid == league_id:
            block = (snapshot.get("leagues") or {}).get(key)
            break

    if block is None:
        return "League not found in configured leagues."

    league = block.get("league") or {}
    slots = starter_slots(league.get("roster_positions") or [])
    scoring = league.get("scoring") or {}
    last_pts = last_week_league_points(block)
    names = block.get("team_names") or {}

    # Find user's team
    my_team_name = None
    for key, lid in LEAGUES.items():
        if lid == league_id:
            my_team_name = MY_TEAMS.get(key)
            break

    if my_team_name is None:
        return "No team configured for this league."

    roster = None
    team_name = my_team_name
    for row in block.get("rosters") or []:
        rid = row.get("roster_id")
        name = names.get(str(rid), f"Team {rid}")
        if name.strip().casefold() == my_team_name.strip().casefold():
            roster = row
            team_name = name
            break

    if roster is None:
        return f"Team '{my_team_name}' not found in this league."

    allowed_by, league_avg = defense_allowed_table(
        ctx["season"],
        ctx["games_by_season"].get(ctx["season"]) or [],
        ctx["week_rows"],
        players,
        scoring,
    )

    score_kwargs = dict(
        players=players,
        scoring=scoring,
        proj=ctx["proj"],
        last_week=last_pts,
        games_by_season=ctx["games_by_season"],
        week=ctx.get("week", 1),
        season=ctx["season"],
        week_rows=ctx["week_rows"],
        weather_by_home=ctx["weather_by_home"],
        allowed_by=allowed_by,
        league_avg=league_avg,
        window="weekend",
        vegas_by_team=ctx.get("vegas_by_team") or {},
        avg_implied=ctx.get("avg_implied"),
    )

    result = render_startsit(
        block,
        team_name=team_name,
        roster=roster,
        slots=slots,
        score_kwargs=score_kwargs,
    )
    return result


def generate_waivers(league_id: str, week: int | None = None) -> str:
    """Generate waiver wire advice for a league."""
    snapshot = load_snapshot()
    if snapshot is None:
        snapshot = cmd_fetch(week=week)
    elif week is not None and int(snapshot.get("requested_week") or 0) != week:
        snapshot = cmd_fetch(week=week)

    with SleeperClient() as client:
        players = ensure_player_cache(client)
        ctx = build_context(client, snapshot, week=week or snapshot.get("requested_week", 1))

    # Find league by ID
    block = None
    for key, lid in LEAGUES.items():
        if lid == league_id:
            block = (snapshot.get("leagues") or {}).get(key)
            break

    if block is None:
        return "League not found in configured leagues."

    league = block.get("league") or {}
    slots = starter_slots(league.get("roster_positions") or [])
    scoring = league.get("scoring") or {}
    last_pts = last_week_league_points(block)
    names = block.get("team_names") or {}

    # Find user's team
    my_team_name = None
    for key, lid in LEAGUES.items():
        if lid == league_id:
            my_team_name = MY_TEAMS.get(key)
            break

    if my_team_name is None:
        return "No team configured for this league."

    roster = None
    team_name = my_team_name
    for row in block.get("rosters") or []:
        rid = row.get("roster_id")
        name = names.get(str(rid), f"Team {rid}")
        if name.strip().casefold() == my_team_name.strip().casefold():
            roster = row
            team_name = name
            break

    if roster is None:
        return f"Team '{my_team_name}' not found in this league."

    faab = faab_state(league, roster)
    current = {str(s) for s in (roster.get("starters") or []) if s}

    allowed_by, league_avg = defense_allowed_table(
        ctx["season"],
        ctx["games_by_season"].get(ctx["season"]) or [],
        ctx["week_rows"],
        players,
        scoring,
    )

    score_kwargs = dict(
        players=players,
        scoring=scoring,
        proj=ctx["proj"],
        last_week=last_pts,
        games_by_season=ctx["games_by_season"],
        week=ctx.get("week", 1),
        season=ctx["season"],
        week_rows=ctx["week_rows"],
        weather_by_home=ctx["weather_by_home"],
        allowed_by=allowed_by,
        league_avg=league_avg,
        window="weekend",
        vegas_by_team=ctx.get("vegas_by_team") or {},
        avg_implied=ctx.get("avg_implied"),
    )

    mine = score_pool(
        [str(p) for p in (roster.get("players") or []) if p],
        score_kwargs=score_kwargs,
        current=current,
    )
    mine_active = [
        m for m in mine
        if str(m["pid"]) not in {str(x) for x in (roster.get("reserve") or []) if x}
    ]
    start = evaluate_roster(roster, slots=slots, **score_kwargs)["start"]
    fa_ids = candidate_ids(
        players,
        rostered_ids(block),
        slots,
        ctx["proj"],
        scoring,
        [],
        set(),
    )
    fa_rows = score_pool(fa_ids, score_kwargs=score_kwargs, current=set())
    playoff = int(league.get("playoff_week_start") or 15)
    claims = build_claims(
        fa_rows=fa_rows,
        mine=mine_active,
        start=start,
        cap=roster_cap(league),
        faab=faab,
        week=ctx.get("week", 1),
        playoff_week=playoff,
        injured_teams=set(),
    )

    return render_waivers(
        block,
        team_name=team_name,
        claims=claims,
        faab=faab,
        week=ctx.get("week", 1),
        alerts=[],
    )
