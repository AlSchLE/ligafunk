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
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add local path for recapcore imports
_local_path = str(Path(__file__).resolve().parent)
if _local_path not in sys.path:
    sys.path.insert(0, _local_path)

from recapcore.client import SleeperClient
from recapcore.clock import detect_window
from recapcore.config import LEAGUES, PLAYERS_INJURY_MAX_AGE_S
from recapcore.narrate import league_recap
from recapcore.options import (
    DEFAULT_STYLE,
    DEFAULT_THEME,
    STYLE_META,
    THEME_META,
    normalize_style,
    normalize_theme,
)
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
from recapcore.league_format import player_pos_set, starter_slots
from recapcore.store import (
    ensure_player_cache,
    fetch_snapshot,
    load_snapshot,
    player_label,
)
from recapcore.trade import (
    build_trade_board,
    evaluate_offer,
    propose_gap_trades,
    render_trade_report,
    suggest_counters,
)

log = logging.getLogger("sleeper-web")

# Rosters, injuries and last_scored_leg move during game days. A snapshot older
# than this is refetched before advice is generated; the expensive per-week
# stat files keep their own longer cache.
SNAPSHOT_MAX_AGE_S = 15 * 60


def _snapshot_age_seconds(snapshot: dict[str, Any]) -> float | None:
    """Age of a stored snapshot, or None when fetched_at is missing/invalid."""
    raw = snapshot.get("fetched_at")
    try:
        fetched = datetime.fromisoformat(str(raw))
    except (TypeError, ValueError):
        return None
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - fetched).total_seconds())


def _load_fresh_snapshot(week: int | None) -> dict[str, Any]:
    """Reuse the snapshot only when it is for this week and still fresh."""
    snapshot = load_snapshot()
    if snapshot is None:
        return cmd_fetch(week=week)
    try:
        same_week = week is None or int(snapshot.get("requested_week") or 0) == int(week)
    except (TypeError, ValueError):
        same_week = False
    age = _snapshot_age_seconds(snapshot)
    if not same_week or age is None or age > SNAPSHOT_MAX_AGE_S:
        return cmd_fetch(week=week)
    return snapshot


def _upcoming_week(snapshot: dict[str, Any], week: int | None) -> int:
    """Resolve the week being advised, bounded to the NFL regular season."""
    if week is not None:
        value = int(week)
    else:
        state = snapshot.get("nfl_state") or {}
        try:
            value = int(
                state.get("week")
                or state.get("display_week")
                or snapshot.get("requested_week")
                or 1
            )
        except (TypeError, ValueError):
            value = 1
    return max(1, min(22, value))


def generate_recap(
    league_id: str,
    week: int | None = None,
    style: str = DEFAULT_STYLE,
    theme: str = DEFAULT_THEME,
) -> str:
    """Generate a weekly recap for a league in the selected voice and theme."""
    style = normalize_style(style)
    theme = normalize_theme(theme)
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
    return league_recap(
        block,
        players,
        week=int(recap_week),
        style=style,
        theme=theme,
    )


def _resolve_web_team(
    block: dict[str, Any],
    *,
    roster_id: str | int | None = None,
    team_name: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Pick the roster for the logged-in web team (roster id first, then name)."""
    names = block.get("team_names") or {}
    rosters = block.get("rosters") or []
    if roster_id not in (None, ""):
        for row in rosters:
            rid = row.get("roster_id")
            if str(rid) == str(roster_id):
                return row, names.get(str(rid), f"Team {rid}")
    if team_name:
        wanted = team_name.strip().casefold()
        for row in rosters:
            rid = row.get("roster_id")
            name = names.get(str(rid), f"Team {rid}")
            if name.strip().casefold() == wanted:
                return row, name
    return None, None


def generate_startsit(
    league_id: str,
    week: int | None = None,
    *,
    roster_id: str | int | None = None,
    team_name: str | None = None,
) -> str:
    """Generate start/sit advice for exactly one web team."""
    snapshot = _load_fresh_snapshot(week)
    upcoming = _upcoming_week(snapshot, week)
    window = detect_window()
    with SleeperClient() as client:
        players = ensure_player_cache(client, max_age_s=PLAYERS_INJURY_MAX_AGE_S)
        ctx = build_context(client, snapshot, week=upcoming)

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

    # Never fall back to a different configured team: that would leak another
    # team's lineup advice to the logged-in user.
    roster, resolved_name = _resolve_web_team(
        block, roster_id=roster_id, team_name=team_name
    )
    if roster is None:
        return ("Your team could not be matched to a roster in this Sleeper "
                "league. Ask the admin to set your Sleeper roster ID.")
    team_name = resolved_name or "Team"

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
        week=upcoming,
        season=ctx["season"],
        week_rows=ctx["week_rows"],
        weather_by_home=ctx["weather_by_home"],
        allowed_by=allowed_by,
        league_avg=league_avg,
        window=window,
        vegas_by_team=ctx.get("vegas_by_team") or {},
        avg_implied=ctx.get("avg_implied"),
    )

    result = evaluate_roster(roster, slots=slots, **score_kwargs)
    return render_startsit(
        block,
        [(team_name, result)],
        week=upcoming,
        window=window,
        vegas_note=str(ctx.get("vegas_note") or ""),
    )


def generate_waivers(
    league_id: str,
    week: int | None = None,
    *,
    roster_id: str | int | None = None,
    team_name: str | None = None,
) -> str:
    """Generate waiver advice for exactly one web team."""
    snapshot = _load_fresh_snapshot(week)
    upcoming = _upcoming_week(snapshot, week)
    with SleeperClient() as client:
        players = ensure_player_cache(client, max_age_s=PLAYERS_INJURY_MAX_AGE_S)
        ctx = build_context(client, snapshot, week=upcoming)
        trending_raw: list[dict[str, Any]] = []
        try:
            trending_raw = client.nfl_trending("add")
        except Exception as exc:
            log.warning("Trending adds unavailable: %s", exc)
    trending = [
        str(row.get("player_id") or "")
        for row in trending_raw
        if isinstance(row, dict) and row.get("player_id")
    ]

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

    # Never fall back to a different configured team: that would leak another
    # team's waiver advice to the logged-in user.
    roster, resolved_name = _resolve_web_team(
        block, roster_id=roster_id, team_name=team_name
    )
    if roster is None:
        return ("Your team could not be matched to a roster in this Sleeper "
                "league. Ask the admin to set your Sleeper roster ID.")
    team_name = resolved_name or "Team"

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
        week=upcoming,
        season=ctx["season"],
        week_rows=ctx["week_rows"],
        weather_by_home=ctx["weather_by_home"],
        allowed_by=allowed_by,
        league_avg=league_avg,
        window="thu",
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
    injured_keys = {
        (str(m.get("team") or "").upper(), m.get("pos"))
        for m in mine
        if m.get("opp")
        and (
            str(m.get("injury") or "").upper()
            in {"Q", "QUESTIONABLE", "D", "DOUBTFUL", "OUT"}
            or not m.get("eligible")
        )
    }
    injured_keys = {key for key in injured_keys if key[0] and key[1]}

    start = evaluate_roster(roster, slots=slots, **score_kwargs)["start"]
    fa_ids = candidate_ids(
        players,
        rostered_ids(block),
        slots,
        ctx["proj"],
        scoring,
        trending,
        {team for team, _pos in injured_keys},
    )
    fa_rows = score_pool(fa_ids, score_kwargs=score_kwargs, current=set())
    playoff = int(league.get("playoff_week_start") or 15)
    claims = build_claims(
        fa_rows=fa_rows,
        mine=mine_active,
        start=start,
        cap=roster_cap(league),
        faab=faab,
        week=upcoming,
        playoff_week=playoff,
        injured_teams=injured_keys,
    )

    alerts: list[str] = []
    for fa in fa_rows:
        pos_key = (str(fa.get("team") or "").upper(), fa.get("pos"))
        if (
            pos_key in injured_keys
            and fa["rec"] >= 1.0
            and (fa.get("dco") in {"1", "2"} or fa["rec"] >= 4)
        ):
            alerts.append(
                f"{fa['name']} is free and is a same-position backup for a "
                f"Q/injured player on your roster (rec {fa['rec']:.1f})"
            )
    alerts = alerts[:6]

    return render_waivers(
        block,
        team_name=team_name,
        claims=claims,
        faab=faab,
        week=upcoming,
        alerts=alerts,
    )


# ============================================================
# Trade advisor
# ============================================================

def _trade_block(snapshot: dict[str, Any], league_id: str) -> dict[str, Any] | None:
    for key, lid in LEAGUES.items():
        if lid == league_id:
            return (snapshot.get("leagues") or {}).get(key)
    return None


def _unique_ids(values: Any) -> list[str]:
    ids: list[str] = []
    for value in values or []:
        pid = str(value or "")
        if pid and pid not in ids:
            ids.append(pid)
    return ids


def _trade_name(players: dict[str, dict[str, str]], pid: str) -> str:
    info = players.get(str(pid)) or {}
    return str(info.get("n") or player_label(players, pid))


def _trade_player_option(
    players: dict[str, dict[str, str]],
    pid: str,
    *,
    starters: set[str],
    reserve: set[str],
) -> dict[str, Any]:
    info = players.get(pid) or {}
    positions = sorted(player_pos_set(info, pid))
    return {
        "pid": pid,
        "name": str(info.get("n") or player_label(players, pid)),
        "pos": "/".join(positions) or "?",
        "team": str(info.get("t") or "").upper(),
        "injury": str(info.get("inj") or ""),
        "practice": str(info.get("prac") or ""),
        "depth": str(info.get("dco") or ""),
        "starter": pid in starters,
        "reserve": pid in reserve,
    }


def _recent_trades(
    block: dict[str, Any],
    my_roster_id: str,
    players: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Completed trades involving this team. Pending offers are not public API data."""
    names = block.get("team_names") or {}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for txn in (block.get("transactions") or []) + (block.get("transactions_prev") or []):
        if not isinstance(txn, dict) or txn.get("type") != "trade":
            continue
        roster_ids = {str(value) for value in txn.get("roster_ids") or [] if value}
        if my_roster_id not in roster_ids:
            continue
        txn_id = str(txn.get("transaction_id") or "")
        if txn_id and txn_id in seen:
            continue
        if txn_id:
            seen.add(txn_id)

        adds = txn.get("adds") or {}
        drops = txn.get("drops") or {}
        give_ids: list[str] = []
        get_ids: list[str] = []
        for pid, to_roster in adds.items():
            pid = str(pid)
            from_roster = drops.get(pid)
            if str(to_roster) == my_roster_id:
                get_ids.append(pid)
            elif from_roster is not None and str(from_roster) == my_roster_id:
                give_ids.append(pid)
            elif from_roster is None and str(to_roster) != my_roster_id:
                # Older snapshots or incomplete trade payloads: use the receiving side.
                give_ids.append(pid)

        partners = sorted(rid for rid in roster_ids if rid != my_roster_id)
        if len(partners) == 1:
            partner_name = names.get(partners[0], f"Team {partners[0]}")
        elif partners:
            partner_name = "Multiple teams"
        else:
            partner_name = "Unknown team"

        picks_sent: list[str] = []
        picks_received: list[str] = []
        for pick in txn.get("draft_picks") or []:
            if not isinstance(pick, dict):
                continue
            label = f"{pick.get('season') or '?'} R{pick.get('round') or '?'}"
            previous = str(pick.get("previous_owner_id") or "")
            owner = str(pick.get("owner_id") or "")
            if previous == my_roster_id:
                picks_sent.append(label)
            elif owner == my_roster_id:
                picks_received.append(label)

        created = txn.get("created")
        try:
            created_label = datetime.fromtimestamp(
                int(created) / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%d")
        except (TypeError, ValueError, OSError):
            created_label = ""

        rows.append(
            {
                "transaction_id": txn_id,
                "partner": partner_name,
                "multi_team": len(partners) > 1,
                "give": [
                    {"pid": pid, "name": _trade_name(players, pid)}
                    for pid in _unique_ids(give_ids)
                ],
                "get": [
                    {"pid": pid, "name": _trade_name(players, pid)}
                    for pid in _unique_ids(get_ids)
                ],
                "picks_sent": picks_sent,
                "picks_received": picks_received,
                "created": created_label,
                "week": txn.get("leg") or "",
            }
        )
    rows.sort(key=lambda row: (row["created"], row["transaction_id"]), reverse=True)
    return rows[:6]


def trade_page_options(
    league_id: str,
    week: int | None = None,
    *,
    roster_id: str | int | None = None,
    team_name: str | None = None,
) -> dict[str, Any]:
    """Rosters and recent trades for the trade-advisor form (no projections)."""
    snapshot = _load_fresh_snapshot(week)
    upcoming = _upcoming_week(snapshot, week)
    with SleeperClient() as client:
        players = ensure_player_cache(client, max_age_s=PLAYERS_INJURY_MAX_AGE_S)

    block = _trade_block(snapshot, league_id)
    if block is None:
        raise ValueError("League not found in configured leagues.")
    roster, resolved_name = _resolve_web_team(
        block, roster_id=roster_id, team_name=team_name
    )
    if roster is None:
        raise ValueError(
            "Your team could not be matched to a roster in this Sleeper league. "
            "Ask the admin to set your Sleeper roster ID."
        )

    my_rid = str(roster.get("roster_id") or "")
    names = block.get("team_names") or {}
    mine_starters = {str(pid) for pid in (roster.get("starters") or []) if pid}
    mine_reserve = {str(pid) for pid in (roster.get("reserve") or []) if pid}
    mine_ids = _unique_ids(
        list(roster.get("players") or []) + list(roster.get("reserve") or [])
    )
    mine = [
        _trade_player_option(
            players, pid, starters=mine_starters, reserve=mine_reserve
        )
        for pid in mine_ids
    ]
    mine.sort(key=lambda row: (not row["starter"], row["reserve"], row["pos"], row["name"]))

    other_teams: list[dict[str, Any]] = []
    for other in block.get("rosters") or []:
        rid = str(other.get("roster_id") or "")
        if not rid or rid == my_rid:
            continue
        starters = {str(pid) for pid in (other.get("starters") or []) if pid}
        reserve = {str(pid) for pid in (other.get("reserve") or []) if pid}
        ids = _unique_ids(
            list(other.get("players") or []) + list(other.get("reserve") or [])
        )
        options = [
            _trade_player_option(
                players, pid, starters=starters, reserve=reserve
            )
            for pid in ids
        ]
        options.sort(key=lambda row: (not row["starter"], row["reserve"], row["pos"], row["name"]))
        other_teams.append(
            {
                "roster_id": rid,
                "name": names.get(rid, f"Team {rid}"),
                "players": options,
            }
        )
    other_teams.sort(key=lambda row: row["name"].casefold())

    return {
        "week": upcoming,
        "my_team": resolved_name or "Team",
        "my_roster_id": my_rid,
        "my_players": mine,
        "other_teams": other_teams,
        "recent_trades": _recent_trades(block, my_rid, players),
    }


def _trade_board_for_web(
    league_id: str,
    week: int | None,
    *,
    roster_id: str | int | None,
    team_name: str | None,
) -> tuple[dict[str, Any], str, str]:
    snapshot = _load_fresh_snapshot(week)
    upcoming = _upcoming_week(snapshot, week)
    block = _trade_block(snapshot, league_id)
    if block is None:
        raise ValueError("League not found in configured leagues.")
    roster, resolved_name = _resolve_web_team(
        block, roster_id=roster_id, team_name=team_name
    )
    if roster is None:
        raise ValueError(
            "Your team could not be matched to a roster in this Sleeper league. "
            "Ask the admin to set your Sleeper roster ID."
        )
    my_rid = str(roster.get("roster_id") or "")
    with SleeperClient() as client:
        players = ensure_player_cache(client, max_age_s=PLAYERS_INJURY_MAX_AGE_S)
        ctx = build_context(client, snapshot, week=upcoming)
        board = build_trade_board(
            client,
            snapshot,
            block,
            week=upcoming,
            players=players,
            base_ctx=ctx,
        )
    if my_rid not in board["rosters"]:
        raise ValueError("Your roster is not present in the league snapshot.")
    return board, my_rid, resolved_name or "Team"


def generate_trade_advisor(
    league_id: str,
    week: int | None = None,
    *,
    roster_id: str | int | None = None,
    team_name: str | None = None,
    mode: str = "evaluate",
    give_ids: list[str] | None = None,
    get_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Evaluate a manual offer or propose trades that fill roster gaps."""
    board, my_rid, my_name = _trade_board_for_web(
        league_id, week, roster_id=roster_id, team_name=team_name
    )
    if mode == "gaps":
        proposals = propose_gap_trades(board, my_rid)
        result = {
            "mode": "gaps",
            "league_name": board["league_name"],
            "week": board["week"],
            "my_team": my_name,
            "my_roster_id": my_rid,
            "proposals": proposals,
        }
        result["report_text"] = render_trade_report(result)
        return result

    give_ids = _unique_ids(give_ids)
    get_ids = _unique_ids(get_ids)
    if not give_ids or not get_ids:
        raise ValueError("Select at least one player on each side of the offer.")
    if any(pid not in board["players"] for pid in give_ids + get_ids):
        raise ValueError("One or more selected players are no longer on a roster.")
    partner_ids = {
        str(board["players"][pid]["roster_id"])
        for pid in get_ids
        if pid in board["players"]
    }
    if len(partner_ids) != 1:
        raise ValueError("Select the players you receive from exactly one other team.")
    partner_rid = next(iter(partner_ids))
    if partner_rid == my_rid:
        raise ValueError("You cannot receive players from your own roster.")

    result = evaluate_offer(
        board,
        my_roster_id=my_rid,
        partner_roster_id=partner_rid,
        give_ids=give_ids,
        get_ids=get_ids,
    )
    result["counters"] = suggest_counters(
        board,
        my_roster_id=my_rid,
        partner_roster_id=partner_rid,
        give_ids=give_ids,
        get_ids=get_ids,
    )
    result["my_team"] = my_name
    result["report_text"] = render_trade_report(result)
    return result
