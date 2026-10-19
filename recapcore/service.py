"""Orchestration: fetch, recap, debug dump. All file writes stay under the project root."""

from __future__ import annotations

import json
import logging
import platform
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from recapcore import __version__
from recapcore.client import SleeperClient
from recapcore.clock import detect_window
from recapcore.config import (
    LEAGUES,
    LOG_DIR,
    MY_TEAMS,
    OUTPUT_DIR,
    PLAYERS_INJURY_MAX_AGE_S,
    SCRIPT_ROOT,
    SNAPSHOT_PATH,
    ensure_dirs,
    validate_league_key,
    validate_week,
)
from recapcore.narrate import intro_text, league_recap
from recapcore.startsit import (
    build_context,
    defense_allowed_table,
    evaluate_roster,
    last_week_league_points,
    render_league as render_startsit,
)
from recapcore.league_format import starter_slots
from recapcore.store import ensure_player_cache, fetch_snapshot, load_snapshot
from recapcore.waivers import (
    build_claims,
    candidate_ids,
    faab_state,
    render_waivers,
    roster_cap,
    rostered_ids,
    score_pool,
)

log = logging.getLogger("sleeper_recap")


def configure_logging(*, debug_file: Path | None = None) -> None:
    ensure_dirs()
    log.handlers.clear()
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    stream = logging.StreamHandler(sys.stderr)
    stream.setLevel(logging.INFO)
    stream.setFormatter(fmt)
    log.addHandler(stream)
    if debug_file is not None:
        _assert_in_dir(debug_file, LOG_DIR)
        fh = logging.FileHandler(debug_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        log.addHandler(fh)


def _assert_in_dir(path: Path, root: Path) -> None:
    resolved = path.resolve()
    root_r = root.resolve()
    if root_r not in resolved.parents and resolved.parent != root_r:
        raise ValueError("Refusing to write outside the project working directories.")


def _safe_output_name(name: str) -> Path:
    ensure_dirs()
    cleaned = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in name)[:120]
    path = (OUTPUT_DIR / cleaned).resolve()
    _assert_in_dir(path, OUTPUT_DIR)
    return path


def infer_week(snapshot: dict[str, Any] | None) -> int:
    if not snapshot:
        return 1
    weeks = []
    for block in (snapshot.get("leagues") or {}).values():
        w = (block.get("league") or {}).get("last_scored_leg") or block.get("week")
        if w:
            weeks.append(int(w))
    if weeks:
        return validate_week(min(weeks))
    requested = snapshot.get("requested_week")
    return validate_week(int(requested or 1))


def cmd_fetch(*, week: int | None = None, force_players: bool = False) -> dict[str, Any]:
    configure_logging()
    with SleeperClient() as client:
        log.info("Refreshing player catalog if cache is older than 24h.")
        players = ensure_player_cache(client, force=force_players)
        log.info("Player catalog entries: %s", len(players))
        snapshot = fetch_snapshot(client, recap_week=week)
    log.info("Snapshot written to %s", SNAPSHOT_PATH)
    return snapshot


def cmd_recap(*, week: int | None = None, league_key: str | None = None) -> list[Path]:
    configure_logging()
    snapshot = load_snapshot()
    if snapshot is None:
        snapshot = cmd_fetch(week=week)
    elif week is not None and int(snapshot.get("requested_week") or 0) != week:
        snapshot = cmd_fetch(week=week)

    with SleeperClient() as client:
        players = ensure_player_cache(client)

    recap_week = week or infer_week(snapshot)
    leagues = validate_league_key(league_key)
    written: list[Path] = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    for key in leagues:
        block = (snapshot.get("leagues") or {}).get(key)
        if not block:
            log.warning("No snapshot data for %s — run fetch first.", key)
            continue
        text = league_recap(block, players, week=int(block.get("week") or recap_week))
        path = _safe_output_name(f"{key}_week{block.get('week')}_{stamp}.txt")
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
        log.info("Wrote %s", path)
    return written


def cmd_intro(*, league_key: str | None = None) -> list[Path]:
    configure_logging()
    snapshot = load_snapshot() or cmd_fetch()
    with SleeperClient() as client:
        players = ensure_player_cache(client)  # noqa: F841 — cache warm
        written = []
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        for key in validate_league_key(league_key):
            block = (snapshot.get("leagues") or {}).get(key) or {}
            draft_id = (block.get("league") or {}).get("draft_id") or ""
            picks = client.draft_picks(str(draft_id)) if draft_id else []
            text = intro_text(block, picks)
            path = _safe_output_name(f"{key}_intro_{stamp}.txt")
            path.write_text(text, encoding="utf-8", newline="\n")
            written.append(path)
            log.info("Wrote %s", path)
        return written


def infer_upcoming_week(snapshot: dict[str, Any] | None) -> int:
    if not snapshot:
        return 1
    state = snapshot.get("nfl_state") or {}
    week = int(state.get("week") or state.get("display_week") or 1)
    return validate_week(week)


def cmd_startsit(
    *,
    week: int | None = None,
    league_key: str | None = None,
    all_teams: bool = False,
    window: str | None = None,
) -> list[Path]:
    """Write one start/sit text file per league (blended rec + printed factors)."""
    configure_logging()
    snapshot = load_snapshot()
    if snapshot is None:
        snapshot = cmd_fetch()
    upcoming = week or infer_upcoming_week(snapshot)
    lock_window = window or detect_window()
    with SleeperClient() as client:
        players = ensure_player_cache(client, max_age_s=PLAYERS_INJURY_MAX_AGE_S)
        ctx = build_context(client, snapshot, week=upcoming)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    written: list[Path] = []
    for key in validate_league_key(league_key):
        block = (snapshot.get("leagues") or {}).get(key)
        if not block:
            log.warning("No snapshot data for %s — run fetch first.", key)
            continue
        league = block.get("league") or {}
        slots = starter_slots(league.get("roster_positions") or [])
        scoring = league.get("scoring") or {}
        last_pts = last_week_league_points(block)
        names = block.get("team_names") or {}
        mine = "" if all_teams else (MY_TEAMS.get(key) or "").strip()
        mine_key = mine.casefold()
        allowed_by, league_avg = defense_allowed_table(
            ctx["season"],
            ctx["games_by_season"].get(ctx["season"]) or [],
            ctx["week_rows"],
            players,
            scoring,
        )
        results: list[tuple[str, dict]] = []
        for roster in block.get("rosters") or []:
            rid = roster.get("roster_id")
            name = names.get(str(rid), f"Team {rid}")
            if mine_key and name.strip().casefold() != mine_key:
                continue
            results.append(
                (
                    name,
                    evaluate_roster(
                        roster,
                        players=players,
                        slots=slots,
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
                        window=lock_window,
                        vegas_by_team=ctx.get("vegas_by_team") or {},
                        avg_implied=ctx.get("avg_implied"),
                    ),
                )
            )
        if mine and not results:
            available = ", ".join(sorted({str(n) for n in names.values()}))
            log.warning("No roster named %r in %s. Teams: %s", mine, key, available)
            continue
        text = render_startsit(
            block,
            results,
            week=upcoming,
            window=lock_window,
            vegas_note=str(ctx.get("vegas_note") or ""),
        )
        path = _safe_output_name(f"{key}_startsit_week{upcoming}_{stamp}.txt")
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
        log.info("Wrote %s", path)
    return written


def cmd_waivers(*, week: int | None = None, league_key: str | None = None) -> list[Path]:
    """Tuesday waiver sheet: add, drop, FAAB bid from remaining budget."""
    configure_logging()
    snapshot = cmd_fetch(week=week)
    upcoming = week or infer_upcoming_week(snapshot)
    with SleeperClient() as client:
        players = ensure_player_cache(client, max_age_s=PLAYERS_INJURY_MAX_AGE_S)
        ctx = build_context(client, snapshot, week=upcoming)
        trending_raw = []
        try:
            trending_raw = client.nfl_trending("add")
        except Exception as exc:
            log.warning("Trending adds unavailable: %s", exc)
    trending = [
        str(row.get("player_id") or "")
        for row in trending_raw
        if isinstance(row, dict) and row.get("player_id")
    ]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    written: list[Path] = []
    lock_window = "thu"
    for key in validate_league_key(league_key):
        block = (snapshot.get("leagues") or {}).get(key)
        if not block:
            log.warning("No snapshot data for %s", key)
            continue
        league = block.get("league") or {}
        slots = starter_slots(league.get("roster_positions") or [])
        scoring = league.get("scoring") or {}
        last_pts = last_week_league_points(block)
        names = block.get("team_names") or {}
        mine_name = (MY_TEAMS.get(key) or "").strip()
        mine_key = mine_name.casefold()
        allowed_by, league_avg = defense_allowed_table(
            ctx["season"],
            ctx["games_by_season"].get(ctx["season"]) or [],
            ctx["week_rows"],
            players,
            scoring,
        )
        roster = None
        team_name = mine_name
        for row in block.get("rosters") or []:
            rid = row.get("roster_id")
            name = names.get(str(rid), f"Team {rid}")
            if name.strip().casefold() == mine_key:
                roster = row
                team_name = name
                break
        if roster is None:
            log.warning("No roster named %r in %s", mine_name, key)
            continue
        faab = faab_state(league, roster)
        current = {str(s) for s in (roster.get("starters") or []) if s}
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
            window=lock_window,
            vegas_by_team=ctx.get("vegas_by_team") or {},
            avg_implied=ctx.get("avg_implied"),
        )
        mine = score_pool(
            [str(p) for p in (roster.get("players") or []) if p],
            score_kwargs=score_kwargs,
            current=current,
        )
        mine_active = [
            m
            for m in mine
            if str(m["pid"]) not in {str(x) for x in (roster.get("reserve") or []) if x}
        ]
        start = evaluate_roster(
            roster,
            slots=slots,
            **score_kwargs,
        )["start"]
        injured_keys = {
            (str(m.get("team") or "").upper(), m.get("pos"))
            for m in mine
            if str(m.get("injury") or "").upper()
            in {"Q", "QUESTIONABLE", "D", "DOUBTFUL", "OUT"}
            or not m.get("eligible")
        }
        injured_keys = {k for k in injured_keys if k[0] and k[1]}
        fa_ids = candidate_ids(
            players,
            rostered_ids(block),
            slots,
            ctx["proj"],
            scoring,
            trending,
            {t for t, _p in injured_keys},
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
            if pos_key in injured_keys and (fa.get("dco") in {"1", "2"} or fa["rec"] >= 4):
                alerts.append(
                    f"{fa['name']} is free and is a same-position backup for a Q/injured player "
                    f"on your roster (rec {fa['rec']:.1f})"
                )
        alerts = alerts[:6]
        text = render_waivers(
            block,
            team_name=team_name,
            claims=claims,
            faab=faab,
            week=upcoming,
            alerts=alerts,
        )
        path = _safe_output_name(f"{key}_waivers_week{upcoming}_{stamp}.txt")
        path.write_text(text, encoding="utf-8", newline="\n")
        written.append(path)
        log.info("Wrote %s", path)
    return written


def cmd_debug(*, week: int | None = None) -> Path:
    ensure_dirs()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = (LOG_DIR / f"debug_{stamp}.log").resolve()
    _assert_in_dir(path, LOG_DIR)
    configure_logging(debug_file=path)

    report: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "version": __version__,
        "python": sys.version,
        "platform": platform.platform(),
        "executable": sys.executable,
        "project_root": str(SCRIPT_ROOT),
        "leagues": LEAGUES,
        "errors": [],
    }
    try:
        snapshot = load_snapshot()
        if snapshot is None or week is not None:
            snapshot = cmd_fetch(week=week)
        report["snapshot_fetched_at"] = snapshot.get("fetched_at")
        report["nfl_state"] = snapshot.get("nfl_state")
        report["requested_week"] = snapshot.get("requested_week")
        summaries = {}
        for key, block in (snapshot.get("leagues") or {}).items():
            summaries[key] = {
                "name": (block.get("league") or {}).get("name"),
                "week": block.get("week"),
                "prev_week": block.get("prev_week"),
                "matchups": len(block.get("matchups") or []),
                "matchups_prev": len(block.get("matchups_prev") or []),
                "transactions": len(block.get("transactions") or []),
                "transactions_prev": len(block.get("transactions_prev") or []),
                "rosters": len(block.get("rosters") or []),
                "last_scored_leg": (block.get("league") or {}).get("last_scored_leg"),
                "format": (block.get("league") or {}).get("format_headline"),
                "lineup": (block.get("league") or {}).get("lineup"),
            }
        report["leagues_summary"] = summaries
        report["snapshot_path"] = str(SNAPSHOT_PATH)
        report["snapshot_bytes"] = SNAPSHOT_PATH.stat().st_size if SNAPSHOT_PATH.is_file() else 0
    except Exception as exc:
        report["errors"].append(
            {
                "type": type(exc).__name__,
                "message": str(exc),
                "trace": traceback.format_exc(),
            }
        )
        log.exception("Debug collection failed")

    # Human-readable first, JSON second — easy to paste into chat.
    lines = [
        f"Sleeper Recap debug log v{__version__}",
        f"UTC {report['generated_at']}",
        f"Python {report['python']}",
        f"Platform {report['platform']}",
        "",
        json.dumps(report, ensure_ascii=False, indent=2),
        "",
        "Paste this file into Cursor if something went wrong.",
        "It contains public league IDs and team names, not passwords.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    log.info("Debug log: %s", path)
    return path
