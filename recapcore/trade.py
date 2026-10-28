"""Trade advisor: rest-of-season player values, offer checks and trade ideas.

The public Sleeper API exposes completed trades, not pending offers. This module
therefore values a manually selected offer (the normal UI flow) and can also look
at recent completed trades. Values reuse the same projections, season form,
injury data, matchup context and league scoring as the start/sit advisor.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from itertools import combinations
from statistics import mean
from typing import Any

from recapcore.league_format import player_pos_set
from recapcore.startsit import (
    build_context,
    defense_allowed_table,
    evaluate_roster,
    form_average,
    last_week_league_points,
    league_points,
    load_week_bundle,
    score_player,
)
from recapcore.store import player_label

log = logging.getLogger("sleeper_recap")

# Rest-of-season window. Week 18 is the end of the NFL regular season.
ROS_END_WEEK = 18
MAX_TRADE_ASSETS = 5
MIN_VALUE_GAIN = 2.0
VALUE_W_PROJ = 0.70
VALUE_W_FORM = 0.30

# Missing a game is costly, but an injured star is not worthless in a trade.
INJURY_VALUE_FACTORS = {
    "OUT": 0.25,
    "O": 0.25,
    "IR": 0.25,
    "PUP": 0.25,
    "NA": 0.25,
    "SUS": 0.25,
    "SUSPENDED": 0.25,
    "COV": 0.25,
    "COVID": 0.25,
    "DNR": 0.25,
    "D": 0.60,
    "DOUBTFUL": 0.60,
    "Q": 0.85,
    "QUESTIONABLE": 0.85,
}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _round(value: Any, digits: int = 1) -> float:
    return round(_f(value), digits)


def _injury_value_factor(info: dict[str, Any] | None) -> float:
    """Discount a player's remaining value for injury and practice status."""
    info = info or {}
    status = str(info.get("inj") or "").strip().upper()
    practice = str(info.get("prac") or "").strip().upper()
    factor = INJURY_VALUE_FACTORS.get(status, 1.0)
    if "DID NOT" in practice or practice in {"DNP", "OUT"}:
        factor *= 0.85
    elif "LIMITED" in practice or practice in {"LP"}:
        factor *= 0.95
    return max(0.0, min(1.0, factor))


def _season_totals(
    pid: str,
    season: int,
    week_rows: dict[tuple[int, int], dict[str, dict[str, Any]]],
    scoring: dict[str, Any],
) -> tuple[float, int]:
    total = 0.0
    games = 0
    for (year, _week), rows in week_rows.items():
        if year != season:
            continue
        stats = rows.get(pid)
        if stats:
            total += league_points(stats, scoring)
            games += 1
    return total, games


def _load_ros_projections(
    client: Any,
    *,
    season: int,
    week: int,
    scoring: dict[str, Any],
    current_proj: dict[str, dict[str, Any]],
) -> tuple[dict[str, float], int]:
    """Sum Sleeper weekly projections from the upcoming week through week 18."""
    start = max(1, int(week))
    end = max(start, ROS_END_WEEK) if start <= ROS_END_WEEK else start
    remaining_weeks = list(range(start, end + 1))
    by_week: dict[int, dict[str, dict[str, Any]]] = {}

    for current_week in remaining_weeks:
        if current_week == start and current_proj:
            rows = current_proj
        else:
            try:
                rows = load_week_bundle(
                    client, season, current_week, projections=True
                )
            except Exception as exc:
                log.warning("Trade ROS projections for week %s unavailable: %s", current_week, exc)
                continue
        if rows:
            by_week[current_week] = rows

    totals: dict[str, float] = defaultdict(float)
    for rows in by_week.values():
        for pid, row in rows.items():
            totals[str(pid)] += league_points(row, scoring)

    # Every team has one remaining game per week in this window; the value model
    # multiplies season form by the same number so the components stay comparable.
    games_left = len(by_week) or max(1, len(remaining_weeks))
    return dict(totals), games_left


def _roster_status(roster: dict[str, Any], result: dict[str, Any]) -> dict[str, tuple[str, str]]:
    """Map player id -> (status, slot) for starters, bench, out and IR players."""
    status: dict[str, tuple[str, str]] = {}
    for row in result.get("start") or []:
        status[str(row.get("pid"))] = ("starter", str(row.get("slot") or "starter"))
    for row in result.get("bench") or []:
        status[str(row.get("pid"))] = ("bench", "BN")
    for row in result.get("out") or []:
        status[str(row.get("pid"))] = ("out", "OUT")
    for pid in roster.get("reserve") or []:
        if pid:
            status[str(pid)] = ("ir", "IR")
    return status


def build_trade_board(
    client: Any,
    snapshot: dict[str, Any],
    block: dict[str, Any],
    *,
    week: int,
    players: dict[str, dict[str, str]],
    base_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build rest-of-season values and positional context for every roster."""
    ctx = base_ctx or build_context(client, snapshot, week=week)
    league = block.get("league") or {}
    scoring = league.get("scoring") or {}
    slots = [str(s) for s in (league.get("roster_positions") or []) if str(s) not in {"BN", "IR", "TAXI"}]
    season = int(ctx.get("season") or 0)
    week_rows = ctx.get("week_rows") or {}
    games_by_season = ctx.get("games_by_season") or {}
    last_pts = last_week_league_points(block)

    allowed_by, league_avg = defense_allowed_table(
        season,
        games_by_season.get(season) or [],
        week_rows,
        players,
        scoring,
    )
    ros_proj, games_left = _load_ros_projections(
        client,
        season=season,
        week=week,
        scoring=scoring,
        current_proj=ctx.get("proj") or {},
    )
    score_kwargs = dict(
        players=players,
        scoring=scoring,
        proj=ctx.get("proj") or {},
        last_week=last_pts,
        games_by_season=games_by_season,
        week=week,
        season=season,
        week_rows=week_rows,
        weather_by_home=ctx.get("weather_by_home") or {},
        allowed_by=allowed_by,
        league_avg=league_avg,
        window="thu",
        vegas_by_team=ctx.get("vegas_by_team") or {},
        avg_implied=ctx.get("avg_implied"),
    )

    rosters: dict[str, dict[str, Any]] = {}
    all_players: dict[str, dict[str, Any]] = {}
    team_names = block.get("team_names") or {}

    for roster in block.get("rosters") or []:
        rid = str(roster.get("roster_id") or "")
        if not rid:
            continue
        result = evaluate_roster(roster, slots=slots, **score_kwargs)
        status = _roster_status(roster, result)
        scored = {str(row.get("pid")): row for row in result.get("scored") or []}
        reserve = {str(pid) for pid in (roster.get("reserve") or []) if pid}
        roster_ids = [str(pid) for pid in (roster.get("players") or []) if pid]
        roster_ids.extend(pid for pid in reserve if pid not in roster_ids)

        rows: list[dict[str, Any]] = []
        for pid in roster_ids:
            row = scored.get(pid)
            if row is None:
                try:
                    row = score_player(pid, currently_started=False, **score_kwargs)
                except Exception as exc:
                    log.warning("Could not score trade asset %s: %s", pid, exc)
                    continue
            info = players.get(pid) or {}
            season_points, games_played = _season_totals(pid, season, week_rows, scoring)
            form = form_average(pid, season, week_rows, scoring, last_pts.get(pid))
            form_value = form * games_left if form is not None else None
            ros_value = _f(ros_proj.get(pid))
            if form_value is not None and ros_value > 0:
                value = VALUE_W_PROJ * ros_value + VALUE_W_FORM * form_value
            elif form_value is not None:
                value = form_value
            else:
                value = ros_value
            injury_factor = _injury_value_factor(info)
            value = max(0.0, value * injury_factor)
            lineup_status, slot = status.get(pid, ("bench", "BN"))
            if pid in reserve and lineup_status not in {"starter", "bench", "out"}:
                lineup_status, slot = "ir", "IR"

            positions = sorted(player_pos_set(info, pid))
            asset = {
                "pid": pid,
                "name": str(info.get("n") or player_label(players, pid)),
                "pos": row.get("pos") or (positions[0] if positions else "?"),
                "positions": positions,
                "team": str(row.get("team") or info.get("t") or "").upper(),
                "roster_id": rid,
                "roster_name": team_names.get(rid, f"Team {rid}"),
                "lineup_status": lineup_status,
                "slot": slot,
                "injury": str(row.get("injury") or ""),
                "practice": str(info.get("prac") or ""),
                "depth": str(info.get("dco") or ""),
                "eligible": bool(row.get("eligible")),
                "bye": not bool(row.get("game")),
                "opp": row.get("opp"),
                "home": bool(row.get("home")),
                "week_proj": _round(row.get("proj")),
                "week_rec": _round(row.get("rec")),
                "facts": list(row.get("facts") or []),
                "adj": list(row.get("adj") or []),
                "currently_started": bool(row.get("currently_started")),
                "season_points": _round(season_points),
                "games_played": games_played,
                "form_ppg": _round(form) if form is not None else None,
                "ros_proj": _round(ros_value),
                "games_left": games_left,
                "injury_factor": _round(injury_factor, 2),
                "value": _round(value),
            }
            rows.append(asset)
            all_players[pid] = asset

        rows.sort(key=lambda item: (item["value"], item["week_rec"]), reverse=True)
        rosters[rid] = {
            "roster_id": rid,
            "name": team_names.get(rid, f"Team {rid}"),
            "roster": roster,
            "result": result,
            "players": rows,
            "by_pid": {item["pid"]: item for item in rows},
        }

    # League-average starter value per position. This is the baseline for needs.
    pos_values: dict[str, list[float]] = defaultdict(list)
    for team in rosters.values():
        for player in team["players"]:
            if player["lineup_status"] == "starter":
                pos_values[player["pos"]].append(_f(player["value"]))
    pos_avg = {pos: mean(vals) for pos, vals in pos_values.items() if vals}

    return {
        "league": league,
        "league_name": str(league.get("name") or "League"),
        "week": int(week),
        "season": season,
        "scoring": scoring,
        "slots": slots,
        "games_left": games_left,
        "rosters": rosters,
        "players": all_players,
        "pos_avg_value": pos_avg,
    }


def _team_need_scores(board: dict[str, Any], roster_id: str) -> dict[str, float]:
    team = board["rosters"].get(str(roster_id)) or {}
    rows = team.get("players") or []
    pos_avg = board.get("pos_avg_value") or {}
    needs: dict[str, float] = {}
    for pos, average in pos_avg.items():
        starters = [
            p for p in rows
            if p["lineup_status"] == "starter" and p["pos"] == pos
        ]
        if not starters:
            base = average * 0.75
        else:
            base = average - mean([_f(p["value"]) for p in starters])
        hurt = sum(
            1 for p in starters
            if _f(p.get("injury_factor"), 1.0) < 0.85 or not p.get("eligible")
        )
        needs[pos] = round(base + average * 0.35 * hurt, 2)
    return needs


def _team_surplus_scores(board: dict[str, Any], roster_id: str) -> dict[str, float]:
    team = board["rosters"].get(str(roster_id)) or {}
    rows = team.get("players") or []
    pos_avg = board.get("pos_avg_value") or {}
    surplus: dict[str, float] = {}
    for pos, average in pos_avg.items():
        starters = [
            p for p in rows
            if p["lineup_status"] == "starter" and p["pos"] == pos
        ]
        bench = [
            p for p in rows
            if p["lineup_status"] == "bench" and p["pos"] == pos and _f(p["value"]) > 0
        ]
        starter_quality = mean([_f(p["value"]) for p in starters]) if starters else 0.0
        bench_quality = sum(_f(p["value"]) for p in bench)
        surplus[pos] = round(starter_quality - average + 0.25 * bench_quality, 2)
    return surplus


def _asset_ids(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row["pid"]) for row in rows]


def _verdict(delta: float) -> tuple[str, str]:
    if delta >= 8:
        return "Accept", "clear win for your team"
    if delta >= 3:
        return "Lean accept", "small but useful gain"
    if delta > -3:
        return "Fair", "close enough to negotiate"
    if delta > -8:
        return "Lean decline", "you lose meaningful value"
    return "Decline", "clearly unfavorable"


def _fit_summary(
    board: dict[str, Any],
    my_roster_id: str,
    partner_roster_id: str,
    incoming: list[dict[str, Any]],
    outgoing: list[dict[str, Any]],
) -> dict[str, float]:
    my_needs = _team_need_scores(board, my_roster_id)
    my_surplus = _team_surplus_scores(board, my_roster_id)
    their_needs = _team_need_scores(board, partner_roster_id)
    their_surplus = _team_surplus_scores(board, partner_roster_id)
    my_fit = sum(my_needs.get(p["pos"], 0.0) for p in incoming) + sum(
        max(0.0, my_surplus.get(p["pos"], 0.0)) for p in outgoing
    )
    their_fit = sum(their_needs.get(p["pos"], 0.0) for p in outgoing) + sum(
        max(0.0, their_surplus.get(p["pos"], 0.0)) for p in incoming
    )
    return {
        "my_fit": round(my_fit, 2),
        "their_fit": round(their_fit, 2),
        "my_needs": my_needs,
        "my_surplus": my_surplus,
        "their_needs": their_needs,
        "their_surplus": their_surplus,
    }


def evaluate_offer(
    board: dict[str, Any],
    *,
    my_roster_id: str,
    partner_roster_id: str,
    give_ids: list[str],
    get_ids: list[str],
) -> dict[str, Any]:
    """Evaluate one offer from the logged-in team's point of view."""
    my_rid = str(my_roster_id)
    partner_rid = str(partner_roster_id)
    give_ids = [str(pid) for pid in give_ids if pid]
    get_ids = [str(pid) for pid in get_ids if pid]
    if not give_ids or not get_ids:
        raise ValueError("Select at least one player on each side.")
    if len(give_ids) > MAX_TRADE_ASSETS or len(get_ids) > MAX_TRADE_ASSETS:
        raise ValueError(f"Select at most {MAX_TRADE_ASSETS} players per side.")
    if len(set(give_ids)) != len(give_ids) or len(set(get_ids)) != len(get_ids):
        raise ValueError("A player cannot appear twice in the same offer.")
    if set(give_ids) & set(get_ids):
        raise ValueError("A player cannot be on both sides of the offer.")
    if my_rid == partner_rid:
        raise ValueError("Choose a trade partner from another team.")
    if my_rid not in board["rosters"] or partner_rid not in board["rosters"]:
        raise ValueError("The selected team is not part of this league.")
    if any(pid not in board["players"] for pid in give_ids + get_ids):
        raise ValueError("One or more selected players are no longer on a roster.")
    if any(str(board["players"][pid]["roster_id"]) != my_rid for pid in give_ids):
        raise ValueError("You can only send players from your own roster.")
    if any(str(board["players"][pid]["roster_id"]) != partner_rid for pid in get_ids):
        raise ValueError("You can only receive players from one other roster.")

    give = [board["players"][pid] for pid in give_ids]
    get = [board["players"][pid] for pid in get_ids]
    my_total = sum(_f(row["value"]) for row in get)
    their_total = sum(_f(row["value"]) for row in give)
    delta = my_total - their_total
    verdict, verdict_note = _verdict(delta)
    fit = _fit_summary(board, my_rid, partner_rid, get, give)

    warnings: list[str] = []
    for row in get:
        if _f(row.get("injury_factor"), 1.0) < 0.85:
            warnings.append(f"{row['name']} is discounted for {row.get('injury') or 'an injury'}.")
        if row.get("lineup_status") == "ir":
            warnings.append(f"{row['name']} is currently on IR.")
    for row in give:
        if row.get("lineup_status") == "starter" and fit["my_needs"].get(row["pos"], 0.0) > 0:
            warnings.append(f"You are giving up a starter at a position of need: {row['pos']}.")
            break

    return {
        "mode": "evaluate",
        "league_name": board["league_name"],
        "week": board["week"],
        "my_team": board["rosters"][my_rid]["name"],
        "partner_team": board["rosters"][partner_rid]["name"],
        "my_roster_id": my_rid,
        "partner_roster_id": partner_rid,
        "give": give,
        "get": get,
        "give_value": _round(their_total),
        "get_value": _round(my_total),
        "delta": _round(delta),
        "value_ratio": _round(my_total / their_total, 2) if their_total > 0 else None,
        "verdict": verdict,
        "verdict_note": verdict_note,
        "warnings": warnings,
        "my_needs": fit["my_needs"],
        "my_needs_top": sorted(fit["my_needs"].items(), key=lambda item: item[1], reverse=True)[:5],
        "my_surplus": fit["my_surplus"],
        "their_needs": fit["their_needs"],
        "their_needs_top": sorted(fit["their_needs"].items(), key=lambda item: item[1], reverse=True)[:5],
        "their_surplus": fit["their_surplus"],
        "my_fit": fit["my_fit"],
        "their_fit": fit["their_fit"],
    }


def _variant(
    board: dict[str, Any],
    *,
    my_roster_id: str,
    partner_roster_id: str,
    give_ids: list[str],
    get_ids: list[str],
    label: str,
    note: str,
) -> dict[str, Any] | None:
    try:
        result = evaluate_offer(
            board,
            my_roster_id=my_roster_id,
            partner_roster_id=partner_roster_id,
            give_ids=give_ids,
            get_ids=get_ids,
        )
    except ValueError:
        return None
    result["label"] = label
    result["note"] = note
    return result


def suggest_counters(
    board: dict[str, Any],
    *,
    my_roster_id: str,
    partner_roster_id: str,
    give_ids: list[str],
    get_ids: list[str],
    limit: int = 4,
) -> list[dict[str, Any]]:
    """Suggest concrete counter packages that improve the offer for my team."""
    my_rid = str(my_roster_id)
    partner_rid = str(partner_roster_id)
    base_give = [str(pid) for pid in give_ids if pid]
    base_get = [str(pid) for pid in get_ids if pid]
    base = evaluate_offer(
        board,
        my_roster_id=my_rid,
        partner_roster_id=partner_rid,
        give_ids=base_give,
        get_ids=base_get,
    )
    my_team = board["rosters"][my_rid]
    partner = board["rosters"][partner_rid]
    my_needs = _team_need_scores(board, my_rid)
    sweeteners = [
        row for row in partner["players"]
        if row["lineup_status"] == "bench"
        and row["pid"] not in base_get
        and row["pid"] not in base_give
        and _f(row["value"]) > 0
    ]

    variants: list[dict[str, Any]] = []
    if base["delta"] >= 0:
        variants.append(
            _variant(
                board,
                my_roster_id=my_rid,
                partner_roster_id=partner_rid,
                give_ids=base_give,
                get_ids=base_get,
                label="Accept as is",
                note="The offer is already favorable on rest-of-season value.",
            )
        )
    else:
        # 1) Remove the least valuable outgoing player.
        if len(base_give) > 1:
            lowest = min(
                (board["players"][pid] for pid in base_give),
                key=lambda row: _f(row["value"]),
            )
            variants.append(
                _variant(
                    board,
                    my_roster_id=my_rid,
                    partner_roster_id=partner_rid,
                    give_ids=[pid for pid in base_give if pid != lowest["pid"]],
                    get_ids=base_get,
                    label=f"Remove {lowest['name']}",
                    note="Keeps the return intact while reducing what you pay.",
                )
            )

        # 2) Replace an outgoing player with a cheaper bench option at the same position.
        for outgoing in sorted(
            (board["players"][pid] for pid in base_give),
            key=lambda row: _f(row["value"]),
        ):
            replacements = [
                row for row in my_team["players"]
                if row["lineup_status"] == "bench"
                and row["pos"] == outgoing["pos"]
                and row["pid"] not in base_give
                and row["pid"] not in base_get
                and _f(row["value"]) < _f(outgoing["value"])
            ]
            if not replacements:
                continue
            replacement = min(replacements, key=lambda row: _f(row["value"]))
            variants.append(
                _variant(
                    board,
                    my_roster_id=my_rid,
                    partner_roster_id=partner_rid,
                    give_ids=[
                        replacement["pid"] if pid == outgoing["pid"] else pid
                        for pid in base_give
                    ],
                    get_ids=base_get,
                    label=f"Swap {outgoing['name']} → {replacement['name']}",
                    note="Same position, lower rest-of-season cost for you.",
                )
            )
            break

        # 3) Ask for a partner bench piece as a sweetener.
        if sweeteners:
            sweetener = max(
                sweeteners,
                key=lambda row: (
                    my_needs.get(row["pos"], 0.0) * _f(row["value"]),
                    -_f(row["value"]),
                ),
            )
            variants.append(
                _variant(
                    board,
                    my_roster_id=my_rid,
                    partner_roster_id=partner_rid,
                    give_ids=base_give,
                    get_ids=base_get + [sweetener["pid"]],
                    label=f"Ask for {sweetener['name']}",
                    note="Add a bench piece from the partner to close the value gap.",
                )
            )

        # 4) Upgrade one incoming player to a better same-position target.
        for incoming in sorted(
            (board["players"][pid] for pid in base_get),
            key=lambda row: _f(row["value"]),
        ):
            upgrades = [
                row for row in partner["players"]
                if row["pos"] == incoming["pos"]
                and row["lineup_status"] in {"starter", "bench"}
                and row["pid"] not in base_get
                and row["pid"] not in base_give
                and _f(row["value"]) > _f(incoming["value"])
            ]
            if not upgrades:
                continue
            upgrade = max(upgrades, key=lambda row: _f(row["value"]))
            variants.append(
                _variant(
                    board,
                    my_roster_id=my_rid,
                    partner_roster_id=partner_rid,
                    give_ids=base_give,
                    get_ids=[
                        upgrade["pid"] if pid == incoming["pid"] else pid
                        for pid in base_get
                    ],
                    label=f"Upgrade {incoming['name']} → {upgrade['name']}",
                    note="Ask for the better same-position asset instead.",
                )
            )
            break

        # 5) Combined: remove the cheapest outgoing player and add a sweetener.
        if len(base_give) > 1 and sweeteners:
            lowest = min(
                (board["players"][pid] for pid in base_give),
                key=lambda row: _f(row["value"]),
            )
            sweetener = min(sweeteners, key=lambda row: _f(row["value"]))
            variants.append(
                _variant(
                    board,
                    my_roster_id=my_rid,
                    partner_roster_id=partner_rid,
                    give_ids=[pid for pid in base_give if pid != lowest["pid"]],
                    get_ids=base_get + [sweetener["pid"]],
                    label=f"Remove {lowest['name']} + add {sweetener['name']}",
                    note="A larger counter that moves both sides at once.",
                )
            )

    if base["delta"] >= 0 and sweeteners:
        # Only ask for a small extra piece; a huge sweetener is not a realistic counter.
        cap = max(5.0, 0.15 * _f(base.get("get_value")))
        cheap = min(
            (
                row for row in sweeteners
                if row["pid"] not in base_get and _f(row["value"]) <= cap
            ),
            key=lambda row: _f(row["value"]),
            default=None,
        )
        if cheap:
            variants.append(
                _variant(
                    board,
                    my_roster_id=my_rid,
                    partner_roster_id=partner_rid,
                    give_ids=base_give,
                    get_ids=base_get + [cheap["pid"]],
                    label=f"Ask for {cheap['name']} too",
                    note="You can still win the deal with this small sweetener added.",
                )
            )

    unique: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    for variant in variants:
        if not variant:
            continue
        key = (tuple(_asset_ids(variant["give"])), tuple(_asset_ids(variant["get"])))
        if key in seen:
            continue
        seen.add(key)
        unique.append(variant)
    unique.sort(
        key=lambda row: (row["label"] == "Accept as is", _f(row["delta"]), row["label"]),
        reverse=True,
    )
    return unique[:limit]


def _proposal_reason(
    board: dict[str, Any],
    *,
    my_roster_id: str,
    partner_roster_id: str,
    incoming: list[dict[str, Any]],
    outgoing: list[dict[str, Any]],
    my_needs: dict[str, float],
    their_needs: dict[str, float],
) -> str:
    partner = board["rosters"][partner_roster_id]
    pieces: list[str] = []
    if incoming:
        target = max(incoming, key=lambda row: my_needs.get(row["pos"], 0.0))
        if my_needs.get(target["pos"], 0.0) > 0:
            pieces.append(
                f"Fills your {target['pos']} gap ({target['name']}, {target['lineup_status']} for {partner['name']})"
            )
    if outgoing:
        give = max(outgoing, key=lambda row: their_needs.get(row["pos"], 0.0))
        if their_needs.get(give["pos"], 0.0) > 0:
            pieces.append(
                f"Gives {partner['name']} a {give['pos']} upgrade ({give['name']})"
            )
    if not pieces:
        pieces.append("A value-neutral reshuffle that improves positional fit")
    return ". ".join(pieces) + "."


def propose_gap_trades(
    board: dict[str, Any],
    my_roster_id: str,
    *,
    limit: int = 6,
) -> list[dict[str, Any]]:
    """Build mutually sensible trade ideas from my needs and partner surpluses."""
    my_rid = str(my_roster_id)
    if my_rid not in board["rosters"]:
        return []
    my_team = board["rosters"][my_rid]
    my_needs = _team_need_scores(board, my_rid)
    my_surplus = _team_surplus_scores(board, my_rid)
    proposals: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()

    for partner_rid, partner in board["rosters"].items():
        if partner_rid == my_rid:
            continue
        their_needs = _team_need_scores(board, partner_rid)
        their_surplus = _team_surplus_scores(board, partner_rid)

        targets = [
            row for row in partner["players"]
            if _f(row["value"]) >= 3
            and my_needs.get(row["pos"], 0.0) > 0
            and row["lineup_status"] in {"starter", "bench"}
        ]
        targets.sort(
            key=lambda row: (
                row["lineup_status"] == "bench",
                my_needs.get(row["pos"], 0.0) * _f(row["value"])
                * (0.55 if row["lineup_status"] == "starter" else 1.0),
                -_f(row["value"]),
            ),
            reverse=True,
        )

        outgoing = [
            row for row in my_team["players"]
            if _f(row["value"]) >= 3
            and their_needs.get(row["pos"], 0.0) > 0
            and row["lineup_status"] in {"starter", "bench"}
            and not (
                row["lineup_status"] == "starter"
                and my_surplus.get(row["pos"], 0.0) <= 0
            )
        ]
        outgoing.sort(
            key=lambda row: (
                their_needs.get(row["pos"], 0.0) * _f(row["value"])
                * (1.15 if row["lineup_status"] == "bench" else 1.0),
                -_f(row["value"]),
            ),
            reverse=True,
        )
        if not targets or not outgoing:
            continue

        target_packages = list(combinations(targets[:4], 1)) + list(combinations(targets[:4], 2))
        outgoing_packages = list(combinations(outgoing[:4], 1)) + list(combinations(outgoing[:4], 2))
        for incoming in target_packages:
            for sent in outgoing_packages:
                if len(incoming) + len(sent) > 4:
                    continue
                incoming_value = sum(_f(row["value"]) for row in incoming)
                outgoing_value = sum(_f(row["value"]) for row in sent)
                my_gain = incoming_value - outgoing_value
                if my_gain < MIN_VALUE_GAIN:
                    continue
                # Do not propose a lopsided rip-off. A trade can win on fit, but
                # the value sent must stay in the same neighborhood.
                if incoming_value > 0 and outgoing_value / incoming_value < 0.75:
                    continue
                if my_gain > 10.0:
                    continue
                my_fit = sum(my_needs.get(row["pos"], 0.0) for row in incoming) + sum(
                    max(0.0, my_surplus.get(row["pos"], 0.0)) for row in sent
                )
                their_fit = sum(their_needs.get(row["pos"], 0.0) for row in sent) + sum(
                    max(0.0, their_surplus.get(row["pos"], 0.0)) for row in incoming
                )
                their_gain = -my_gain
                if their_gain < -8 and their_fit < 1:
                    continue
                score = my_gain + 0.25 * my_fit + 0.25 * their_fit
                key = (
                    tuple(sorted(str(row["pid"]) for row in incoming)),
                    tuple(sorted(str(row["pid"]) for row in sent)),
                )
                if key in seen:
                    continue
                seen.add(key)
                proposals.append(
                    {
                        "my_team": my_team["name"],
                        "partner_team": partner["name"],
                        "my_roster_id": my_rid,
                        "partner_roster_id": partner_rid,
                        "get": list(incoming),
                        "give": list(sent),
                        "get_value": _round(incoming_value),
                        "give_value": _round(outgoing_value),
                        "my_gain": _round(my_gain),
                        "their_gain": _round(their_gain),
                        "my_fit": _round(my_fit),
                        "their_fit": _round(their_fit),
                        "score": _round(score, 2),
                        "label": (
                            "Win-win fit"
                            if their_fit >= 0 and their_gain >= -1
                            else "Fit-based / aggressive"
                        ),
                        "reason": _proposal_reason(
                            board,
                            my_roster_id=my_rid,
                            partner_roster_id=partner_rid,
                            incoming=list(incoming),
                            outgoing=list(sent),
                            my_needs=my_needs,
                            their_needs=their_needs,
                        ),
                    }
                )

    proposals.sort(key=lambda row: (row["score"], row["my_gain"]), reverse=True)
    # Keep variety: at most three proposals per partner (1-for-1, 2-for-1, 2-for-2).
    selected: list[dict[str, Any]] = []
    per_partner: dict[str, int] = defaultdict(int)
    for proposal in proposals:
        partner_rid = proposal["partner_roster_id"]
        if per_partner[partner_rid] >= 3:
            continue
        selected.append(proposal)
        per_partner[partner_rid] += 1
        if len(selected) >= limit:
            break
    return selected


def render_trade_report(result: dict[str, Any]) -> str:
    """Plain-text report for copy, WhatsApp and e-mail share buttons."""
    title = f"TRADE ADVISOR — {result.get('league_name') or 'League'} — WEEK {result.get('week')}"
    if result.get("mode") == "gaps":
        lines = [
            title,
            f"{result.get('my_team') or 'Your team'} — trade targets for roster gaps",
            "Value = 70% rest-of-season projection + 30% current-season form, after injury discount.",
            "",
        ]
        proposals = result.get("proposals") or []
        if not proposals:
            lines.append("No viable trade found. Your leaguemates may not have a surplus at your positions of need.")
            return "\n".join(lines)
        for index, proposal in enumerate(proposals, 1):
            lines.append(f"{index}. {proposal['partner_team']} — {proposal['label']}")
            lines.append(
                "   GET  " + ", ".join(
                    f"{row['name']} ({row['pos']}, {row['value']:.1f})"
                    for row in proposal["get"]
                )
            )
            lines.append(
                "   GIVE " + ", ".join(
                    f"{row['name']} ({row['pos']}, {row['value']:.1f})"
                    for row in proposal["give"]
                )
            )
            lines.append(
                f"   Value: {proposal['my_gain']:+.1f} for you, "
                f"{proposal['their_gain']:+.1f} for them · {proposal['label']}"
            )
            lines.append(f"   {proposal['reason']}")
            lines.append("")
        return "\n".join(lines)

    lines = [
        title,
        f"{result.get('my_team') or 'Your team'} ↔ {result.get('partner_team') or 'Partner'}",
        f"VERDICT: {result.get('verdict')} — {result.get('verdict_note')}",
        f"Value: you receive {result.get('get_value', 0):.1f}, you send {result.get('give_value', 0):.1f} "
        f"(delta {result.get('delta', 0):+.1f})",
        "Value = 70% rest-of-season projection + 30% current-season form, after injury discount.",
        "",
        "YOU SEND",
    ]
    for row in result.get("give") or []:
        lines.append(_asset_line(row))
    lines.append("YOU RECEIVE")
    for row in result.get("get") or []:
        lines.append(_asset_line(row))
    if result.get("warnings"):
        lines.append("")
        lines.append("WATCH OUT")
        lines.extend(f"  {warning}" for warning in result["warnings"])
    counters = result.get("counters") or []
    if counters:
        lines.append("")
        lines.append("COUNTER IDEAS")
        for counter in counters:
            lines.append(
                f"  {counter['label']} — delta {counter['delta']:+.1f} "
                f"({counter['verdict']}): {counter['note']}"
            )
    return "\n".join(lines)


def _asset_line(row: dict[str, Any]) -> str:
    injury = f" · {row['injury']}" if row.get("injury") else ""
    practice = f" · practice {row['practice']}" if row.get("practice") else ""
    form = f"{row['form_ppg']:.1f}/g" if row.get("form_ppg") is not None else "no form"
    return (
        f"  {row['name']} ({row['pos']}/{row['team']}) — "
        f"ROS {row['value']:.1f} · season {row['season_points']:.1f} · {form} · "
        f"{row['lineup_status']}{injury}{practice}"
    )
