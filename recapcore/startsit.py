"""Start/sit from published and actual numbers.

Recommendation = blend of Sleeper projection, this-season form, and games vs
this NFL opponent, then multiplied by (1) that defense's actual points allowed
to the position vs league average, (2) Open-Meteo weather for QB/WR/TE/K, and
(3) rest/travel from the NFL schedule. Factors are printed on each line.

OUT / IR / bye are ineligible. One .txt file per league for a Sleeper paste.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

from recapcore.client import SleeperClient
from recapcore.clock import (
    fmt_kickoff,
    game_started,
    kickoff_soon,
    window_label,
)
from recapcore.config import (
    DATA_DIR,
    PROJECTION_CACHE_MAX_AGE_S,
    STARTSIT_W_FORM,
    STARTSIT_W_PROJ,
    STARTSIT_W_VS,
)
from recapcore.league_format import player_pos_set, slot_accepts, starter_slots
from recapcore.nfl_games import (
    games_for_week,
    is_home,
    load_schedule,
    opponent_of,
    rest_and_travel,
    team_game,
    weather_for_game,
)
from recapcore.odds import league_avg_implied, load_vegas
from recapcore.store import atomic_write_json, load_json, player_label

log = logging.getLogger("sleeper_recap")

# Open-Meteo is optional. If it is unreachable, stop retrying for every home
# team in the same request and back off for a few minutes. Without this, a
# single page can spend two minutes on weather timeouts before rendering.
_WEATHER_RETRY_AFTER_S = 300
_weather_unavailable_until = 0.0

OUT_STATUSES = frozenset({"OUT", "IR", "PUP", "NA", "SUS", "SUSPENDED", "COV", "COVID", "DNR"})


def _f(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def league_points(stat_map: dict[str, Any], scoring: dict[str, Any]) -> float:
    """Apply this league's scoring keys to a Sleeper stats/projection row."""
    if not isinstance(stat_map, dict):
        return 0.0
    if scoring:
        total = 0.0
        hit = False
        for key, weight in scoring.items():
            if key.startswith(("adp", "pos_adp", "pos_rank")):
                continue
            if key in stat_map:
                total += _f(stat_map.get(key)) * _f(weight)
                hit = True
        if hit:
            return total
    return _f(stat_map.get("pts_half_ppr"))


def load_week_bundle(
    client: SleeperClient, season: int, week: int, *, projections: bool
) -> dict[str, dict[str, Any]]:
    kind = "proj" if projections else "stats"
    path = DATA_DIR / f"nfl_{kind}_{int(season)}_w{int(week)}.json"
    max_age = PROJECTION_CACHE_MAX_AGE_S if projections else (30 * 24 * 3600)
    cached = load_json(path)
    if (
        isinstance(cached, dict)
        and cached.get("rows")
        and (time.time() - float(cached.get("fetched_unix") or 0)) < max_age
    ):
        return cached["rows"]
    raw = (
        client.nfl_projections_week(season, week)
        if projections
        else client.nfl_stats_week(season, week)
    )
    rows: dict[str, dict[str, Any]] = {}
    for pid, row in (raw or {}).items():
        if not isinstance(row, dict) or str(pid).startswith("TEAM_"):
            continue
        keep = {
            k: _f(v)
            for k, v in row.items()
            if isinstance(v, (int, float)) and not str(k).startswith("adp")
        }
        if keep:
            rows[str(pid)] = keep
    atomic_write_json(path, {"fetched_unix": time.time(), "rows": rows})
    return rows


def _pos_group(positions: set[str]) -> str:
    for p in ("QB", "RB", "WR", "TE", "K", "DEF"):
        if p in positions or (p == "DEF" and "DST" in positions):
            return p
    if positions & {"DL", "DE", "DT", "LB", "ILB", "OLB", "DB", "CB", "S", "FS", "SS", "IDP"}:
        return "IDP"
    return next(iter(positions), "?")


def injury_status(info: dict[str, str] | None) -> str:
    return str((info or {}).get("inj") or "").strip()


def cannot_play(status: str, player_status: str = "", window: str = "thu") -> bool:
    if str(status or "").upper() in OUT_STATUSES:
        return True
    if window == "weekend" and str(player_status or "").lower() == "inactive":
        return True
    return False


_PASS_POS = frozenset({"QB", "WR", "TE"})
_KICK_POS = frozenset({"K"})


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def form_average(
    pid: str,
    season: int,
    week_rows: dict[tuple[int, int], dict[str, dict[str, Any]]],
    scoring: dict[str, Any],
    last_week_pts: float | None,
) -> float | None:
    """This-season actuals; later weeks count more. Snapshot last-week overlays stats."""
    series: list[tuple[int, float]] = []
    for (yr, wk), rows in week_rows.items():
        if yr != season:
            continue
        stats = rows.get(pid)
        if stats:
            series.append((wk, league_points(stats, scoring)))
    series.sort()
    if last_week_pts is not None:
        if series:
            last_w, _ = series[-1]
            series[-1] = (last_w, last_week_pts)
        else:
            series = [(0, last_week_pts)]
    if not series:
        return None
    num = den = 0.0
    for i, (_wk, pts) in enumerate(series):
        wt = float(i + 1)
        num += wt * pts
        den += wt
    return num / den if den else None


def vs_weighted_average(
    vs_games: list[tuple[int, int, float]],
    season: int,
) -> float | None:
    """Actual games vs this opponent. Current season counts double vs last year."""
    if not vs_games:
        return None
    num = den = 0.0
    for yr, _wk, pts in vs_games:
        wt = 2.0 if yr == season else 1.0
        num += wt * pts
        den += wt
    return num / den if den else None


def blend_points(proj: float, form: float | None, vs_avg: float | None) -> float:
    parts = [(STARTSIT_W_PROJ, proj)]
    if form is not None:
        parts.append((STARTSIT_W_FORM, form))
    if vs_avg is not None:
        parts.append((STARTSIT_W_VS, vs_avg))
    wsum = sum(w for w, _v in parts)
    if wsum <= 0:
        return proj
    return sum(w * v for w, v in parts) / wsum


def matchup_factor(allowed: list[float], league_avg: float | None) -> tuple[float, str | None]:
    """This NFL D's points allowed to the position vs league average (clipped)."""
    if not allowed or not league_avg or league_avg <= 0:
        return 1.0, None
    ratio = (sum(allowed) / len(allowed)) / league_avg
    n = len(allowed)
    cap = 0.15 if n == 1 else 0.18 if n == 2 else 0.22
    factor = _clip(ratio, 1.0 - cap, 1.0 + cap)
    if abs(factor - 1.0) < 0.005:
        return 1.0, None
    return factor, f"matchup×{factor:.2f}"


# Weather factor thresholds (km/h wind, mm precipitation, % precipitation probability)
WIND_THRESHOLD_HIGH = 40
WIND_THRESHOLD_MED = 32
WIND_THRESHOLD_LOW = 24
PRECIP_THRESHOLD_MM = 3
PRECIP_THRESHOLD_PCT = 80

# Weather factor multipliers for passing positions
PASS_WIND_FACTOR_HIGH = 0.88
PASS_WIND_FACTOR_MED = 0.92
PASS_WIND_FACTOR_LOW = 0.96
PASS_PRECIP_FACTOR = 0.95

# Weather factor multipliers for kicking positions
KICK_WIND_FACTOR_HIGH = 0.80
KICK_WIND_FACTOR_MED = 0.86
KICK_WIND_FACTOR_LOW = 0.92
KICK_PRECIP_FACTOR = 0.93

# Rest factor thresholds and multipliers
SHORT_REST_DAYS = 4
SHORT_REST_FACTOR = 0.97
ROAD_AFTER_ROAD_FACTOR = 0.98
CROSS_COUNTRY_FACTOR = 0.98

# Injury/practice factor multipliers
INJURY_D_FACTOR_THU = 0.50
INJURY_D_FACTOR_WEEKEND = 0.35
INJURY_Q_FACTOR_THU = 0.92
INJURY_Q_FACTOR_WEEKEND = 0.78
PRACTICE_DNP_FACTOR_THU = 0.88
PRACTICE_DNP_FACTOR_WEEKEND = 0.70
PRACTICE_LP_FACTOR_THU = 0.96
PRACTICE_LP_FACTOR_WEEKEND = 0.90

# Vegas factor clip range
VEGAS_CLIP_LOW = 0.90
VEGAS_CLIP_HIGH = 1.10


def weather_factor(pos: str, wx: dict[str, Any] | None) -> tuple[float, str | None]:
    """Open-Meteo forecast. Passing/kicking only; indoor and RB/DEF/IDP stay 1.00."""
    if not wx or wx.get("indoor"):
        return 1.0, None
    wind = _f(wx.get("wind"))
    precip_mm = _f(wx.get("precip_mm"))
    precip_pct = _f(wx.get("precip_pct"))
    factor = 1.0
    if pos in _PASS_POS:
        if wind >= WIND_THRESHOLD_HIGH:
            factor *= PASS_WIND_FACTOR_HIGH
        elif wind >= WIND_THRESHOLD_MED:
            factor *= PASS_WIND_FACTOR_MED
        elif wind >= WIND_THRESHOLD_LOW:
            factor *= PASS_WIND_FACTOR_LOW
        if precip_mm >= PRECIP_THRESHOLD_MM or precip_pct >= PRECIP_THRESHOLD_PCT:
            factor *= PASS_PRECIP_FACTOR
    elif pos in _KICK_POS:
        if wind >= WIND_THRESHOLD_HIGH:
            factor *= KICK_WIND_FACTOR_HIGH
        elif wind >= WIND_THRESHOLD_MED:
            factor *= KICK_WIND_FACTOR_MED
        elif wind >= WIND_THRESHOLD_LOW:
            factor *= KICK_WIND_FACTOR_LOW
        if precip_mm >= PRECIP_THRESHOLD_MM or precip_pct >= PRECIP_THRESHOLD_PCT:
            factor *= KICK_PRECIP_FACTOR
    if abs(factor - 1.0) < 0.005:
        return 1.0, None
    return factor, f"wx×{factor:.2f}"


def rest_factor(rest_ctx: dict[str, Any]) -> tuple[float, str | None]:
    """Schedule dates only: short week, road-after-road, coast-to-coast."""
    factor = 1.0
    days = rest_ctx.get("days_rest")
    if days is not None and days <= SHORT_REST_DAYS:
        factor *= SHORT_REST_FACTOR
    if rest_ctx.get("road_after_road"):
        factor *= ROAD_AFTER_ROAD_FACTOR
    if rest_ctx.get("cross_country"):
        factor *= CROSS_COUNTRY_FACTOR
    if abs(factor - 1.0) < 0.005:
        return 1.0, None
    return factor, f"rest×{factor:.2f}"


def injury_practice_factor(
    info: dict[str, str] | None,
    window: str,
) -> tuple[float, str | None]:
    """Official Sleeper injury + practice. Weekend window fades Q/D/DNP harder than Thursday."""
    info = info or {}
    status = injury_status(info).upper()
    prac = str(info.get("prac") or "").upper()
    factor = 1.0
    weekend = window == "weekend"
    if status in {"D", "DOUBTFUL"}:
        factor = INJURY_D_FACTOR_WEEKEND if weekend else INJURY_D_FACTOR_THU
    elif status in {"Q", "QUESTIONABLE"}:
        factor = INJURY_Q_FACTOR_WEEKEND if weekend else INJURY_Q_FACTOR_THU
    if "DID NOT" in prac or prac in {"DNP", "OUT"}:
        factor *= PRACTICE_DNP_FACTOR_WEEKEND if weekend else PRACTICE_DNP_FACTOR_THU
    elif "LIMITED" in prac or prac in {"LP"}:
        factor *= PRACTICE_LP_FACTOR_WEEKEND if weekend else PRACTICE_LP_FACTOR_THU
    if abs(factor - 1.0) < 0.005:
        return 1.0, None
    return factor, f"inj×{factor:.2f}"


def vegas_factor(
    pos: str,
    team: str,
    opp: str | None,
    vegas_by_team: dict[str, dict[str, Any]] | None,
    avg_implied: float | None,
) -> tuple[float, str | None]:
    """Implied team total vs slate average. DST uses the opponent's implied points."""
    if not vegas_by_team or not avg_implied:
        return 1.0, None
    if pos == "DEF":
        row = vegas_by_team.get((opp or "").upper()) or {}
        implied = row.get("implied")
        if implied is None:
            return 1.0, None
        ratio = avg_implied / float(implied) if implied else 1.0
    else:
        row = vegas_by_team.get((team or "").upper()) or {}
        implied = row.get("implied")
        if implied is None:
            return 1.0, None
        ratio = float(implied) / avg_implied
    factor = _clip(ratio, VEGAS_CLIP_LOW, VEGAS_CLIP_HIGH)
    if abs(factor - 1.0) < 0.005:
        return 1.0, None
    return factor, f"vegas×{factor:.2f}"


def usage_bits(
    pid: str,
    season: int,
    week_rows: dict[tuple[int, int], dict[str, dict[str, Any]]],
) -> str:
    series = [
        (wk, rows.get(pid) or {})
        for (yr, wk), rows in week_rows.items()
        if yr == season and pid in rows
    ]
    if not series:
        return ""
    _wk, stats = max(series, key=lambda x: x[0])
    parts: list[str] = []
    tgt = _f(stats.get("rec_tgt"))
    att = _f(stats.get("rush_att"))
    rec = _f(stats.get("rec"))
    snp = _f(stats.get("off_snp"))
    tm = _f(stats.get("tm_off_snp"))
    if tgt:
        parts.append(f"{tgt:.0f} tgt")
    if att:
        parts.append(f"{att:.0f} rush")
    if rec and not tgt:
        parts.append(f"{rec:.0f} rec")
    if snp and tm:
        parts.append(f"{100.0 * snp / tm:.0f}% sn")
    return ", ".join(parts)


def vs_opponent_games(
    pid: str,
    opp: str,
    team: str,
    games_by_season: dict[int, list[dict[str, Any]]],
    week_rows: dict[tuple[int, int], dict[str, dict[str, Any]]],
    scoring: dict[str, Any],
) -> list[tuple[int, int, float]]:
    """Actual league-scored games vs that NFL opponent. (season, week, pts)."""
    out: list[tuple[int, int, float]] = []
    if not opp or not team:
        return out
    for (season, week), rows in week_rows.items():
        stats = rows.get(pid)
        if not stats:
            continue
        game = team_game(games_by_season.get(season) or [], week, team)
        if not game or opponent_of(game, team) != opp:
            continue
        out.append((season, week, league_points(stats, scoring)))
    out.sort()
    return out


def defense_allowed_table(
    season: int,
    games: list[dict[str, Any]],
    week_rows: dict[tuple[int, int], dict[str, dict[str, Any]]],
    players: dict[str, dict[str, str]],
    scoring: dict[str, Any],
) -> tuple[dict[tuple[str, str], list[float]], dict[str, float]]:
    """Per-defense weekly points allowed to each position, plus league averages."""
    weekly: dict[tuple[str, str, int], float] = defaultdict(float)
    for (yr, week), rows in week_rows.items():
        if yr != season:
            continue
        for pid, stats in rows.items():
            info = players.get(pid) or {}
            offense = (info.get("t") or "").upper()
            if not offense:
                continue
            g = team_game(games, week, offense)
            if not g:
                continue
            opp = opponent_of(g, offense)
            if not opp:
                continue
            pos = _pos_group(player_pos_set(info, pid))
            weekly[(opp, pos, week)] += league_points(stats, scoring)
    buckets: dict[tuple[str, str], dict[int, float]] = defaultdict(dict)
    pos_weeks: dict[str, list[float]] = defaultdict(list)
    for (opp, pos, week), pts in weekly.items():
        buckets[(opp, pos)][week] = pts
        pos_weeks[pos].append(pts)
    allowed_by = {key: [week_map[w] for w in sorted(week_map)] for key, week_map in buckets.items()}
    league_avg = {pos: (sum(vals) / len(vals)) for pos, vals in pos_weeks.items() if vals}
    return allowed_by, league_avg


def optimize_lineup(
    candidates: list[dict[str, Any]],
    slots: list[str],
    current: list[str] | None = None,
) -> list[dict[str, Any]]:
    by_pid = {c["pid"]: c for c in candidates}
    current = current or []
    filled: list[dict[str, Any] | None] = [None] * len(slots)
    used: set[str] = set()
    for idx, slot in enumerate(slots):
        pid = current[idx] if idx < len(current) else ""
        cand = by_pid.get(pid)
        if (
            cand
            and cand.get("kickoff_locked")
            and cand.get("eligible")
            and slot_accepts(slot, cand["positions"])
        ):
            filled[idx] = {**cand, "slot": slot, "frozen": True}
            used.add(pid)
    remaining = [c for c in candidates if c["eligible"] and c["pid"] not in used]
    for idx, slot in enumerate(slots):
        if filled[idx] is not None:
            continue
        pool = [c for c in remaining if slot_accepts(slot, c["positions"])]
        if not pool:
            continue
        pick = max(pool, key=lambda c: c["rec"])
        remaining.remove(pick)
        filled[idx] = {**pick, "slot": slot}
    return [row for row in filled if row]


def _compute_factors(
    *,
    pos: str,
    team: str,
    opp: str | None,
    allowed: list[float],
    league_avg: dict[str, float],
    wx: dict[str, Any] | None,
    rest_ctx: dict[str, Any],
    info: dict[str, str],
    window: str,
    vegas_by_team: dict[str, dict[str, Any]] | None,
    avg_implied: float | None,
) -> tuple[float, list[str]]:
    """Compute all adjustment factors and their tags."""
    m_factor, m_tag = matchup_factor(allowed, league_avg.get(pos))
    w_factor, w_tag = weather_factor(pos, wx)
    r_factor, r_tag = rest_factor(rest_ctx)
    i_factor, i_tag = injury_practice_factor(info, window)
    v_factor, v_tag = vegas_factor(pos, team, opp, vegas_by_team, avg_implied)
    combined = m_factor * w_factor * r_factor * i_factor * v_factor
    tags = [tag for tag in (m_tag, w_tag, r_tag, i_tag, v_tag) if tag]
    return combined, tags


def _build_facts(
    *,
    pid: str,
    info: dict[str, str],
    status: str,
    last_pts: float | None,
    form: float | None,
    vs_games: list[tuple[int, int, float]],
    opp: str | None,
    pos: str,
    allowed: list[float],
    vegas_by_team: dict[str, dict[str, Any]] | None,
    team: str,
    rest_ctx: dict[str, Any],
    wx: dict[str, Any] | None,
    game: dict[str, Any] | None,
    bye: bool,
    locked: bool,
    soon: bool,
    season: int,
    week_rows: dict[tuple[int, int], dict[str, dict[str, Any]]],
) -> list[str]:
    """Build the facts list for a player."""
    facts = []
    if status:
        body = (info.get("ib") or "").strip()
        facts.append(f"injury {status}" + (f" ({body})" if body else ""))
    prac = (info.get("prac") or "").strip()
    if prac:
        facts.append(f"practice {prac}")
    if last_pts is not None:
        facts.append(f"last week {last_pts:.1f}")
    usage = usage_bits(pid, season, week_rows)
    if usage:
        facts.append(usage)
    if form is not None and (last_pts is None or abs(form - last_pts) >= 0.05):
        facts.append(f"form {form:.1f}")
    if vs_games:
        bits = ", ".join(f"{s}w{w}={p:.1f}" for s, w, p in vs_games[-4:])
        facts.append(f"vs {opp} ({len(vs_games)}g): {bits}")
    if allowed:
        facts.append(
            f"{opp} allowed to {pos} this year: " + ", ".join(f"{x:.1f}" for x in allowed[-4:])
        )
    vrow = (vegas_by_team or {}).get(team) if team else None
    if vrow and vrow.get("implied") is not None:
        facts.append(
            f"vegas implied {vrow['implied']:.1f} (spread {vrow.get('spread'):+.1f}, tot {vrow.get('total')})"
        )
    if rest_ctx.get("days_rest") is not None:
        facts.append(f"{rest_ctx['days_rest']}d rest")
    if rest_ctx.get("road_after_road"):
        facts.append("road after road")
    if rest_ctx.get("cross_country"):
        facts.append("cross-country")
    if wx:
        if wx.get("indoor"):
            facts.append("indoor")
        elif wx.get("temp_c") is not None:
            facts.append(
                f"wx {wx.get('temp_c'):.0f}C wind {wx.get('wind', 0):.0f}km/h "
                f"precip {wx.get('precip_pct', 0):.0f}%/{_f(wx.get('precip_mm')):.1f}mm"
            )
        else:
            facts.append(
                f"wx wind {wx.get('wind', 0):.0f}km/h "
                f"precip {wx.get('precip_pct', 0):.0f}%/{_f(wx.get('precip_mm')):.1f}mm"
            )
    if game:
        facts.append(fmt_kickoff(game))
    if bye:
        facts.append("BYE")
    if locked:
        facts.append("KICKOFF LOCKED")
    elif soon:
        facts.append("lock before kickoff")
    return facts


def score_player(
    pid: str,
    *,
    players: dict[str, dict[str, str]],
    scoring: dict[str, Any],
    proj: dict[str, dict[str, Any]],
    last_week: dict[str, float],
    games_by_season: dict[int, list[dict[str, Any]]],
    week: int,
    season: int,
    week_rows: dict[tuple[int, int], dict[str, dict[str, Any]]],
    weather_by_home: dict[str, Any],
    allowed_by: dict[tuple[str, str], list[float]],
    league_avg: dict[str, float],
    currently_started: bool = False,
    window: str = "thu",
    vegas_by_team: dict[str, dict[str, Any]] | None = None,
    avg_implied: float | None = None,
    now: Any = None,
) -> dict[str, Any]:
    info = players.get(pid) or {}
    positions = player_pos_set(info, pid)
    pos = _pos_group(positions)
    team = (info.get("t") or "").upper()
    if pid.isalpha() and 2 <= len(pid) <= 4:
        team = pid.upper()
    slate = games_by_season.get(season) or []
    game = team_game(slate, week, team) if team else None
    opp = opponent_of(game, team) if game else None
    status = injury_status(info)
    bye = game is None
    locked = bool(game and game_started(game, now))
    soon = bool(game and kickoff_soon(game, now))
    proj_pts = league_points(proj.get(pid) or {}, scoring)
    eligible = (not cannot_play(status, info.get("st") or "", window)) and (not bye)
    rest_ctx = rest_and_travel(slate, week, team) if team else {}
    wx = weather_by_home.get((game or {}).get("home") or "") if game else None
    vs_games = vs_opponent_games(pid, opp or "", team, games_by_season, week_rows, scoring)
    allowed = allowed_by.get((opp or "", pos), []) if opp else []
    last_pts = last_week.get(pid)
    form = form_average(pid, season, week_rows, scoring, last_pts)
    vs_avg = vs_weighted_average(vs_games, season)
    factor, adj = _compute_factors(
        pos=pos, team=team, opp=opp, allowed=allowed, league_avg=league_avg,
        wx=wx, rest_ctx=rest_ctx, info=info, window=window,
        vegas_by_team=vegas_by_team, avg_implied=avg_implied,
    )
    rec_pts = blend_points(proj_pts, form, vs_avg) * factor
    facts = _build_facts(
        pid=pid, info=info, status=status, last_pts=last_pts, form=form,
        vs_games=vs_games, opp=opp, pos=pos, allowed=allowed,
        vegas_by_team=vegas_by_team, team=team, rest_ctx=rest_ctx,
        wx=wx, game=game, bye=bye, locked=locked, soon=soon,
        season=season, week_rows=week_rows,
    )
    return {
        "pid": pid,
        "name": player_label(players, pid),
        "positions": positions,
        "pos": pos,
        "opp": opp,
        "home": bool(game and is_home(game, team)),
        "proj": proj_pts,
        "rec": rec_pts,
        "adj": adj,
        "eligible": eligible,
        "facts": facts,
        "currently_started": currently_started,
        "kickoff_locked": locked,
        "kickoff_soon": soon,
        "game": game,
        "injury": status,
        "team": team,
    }


def evaluate_roster(
    roster: dict[str, Any],
    *,
    players: dict[str, dict[str, str]],
    slots: list[str],
    scoring: dict[str, Any],
    proj: dict[str, dict[str, Any]],
    last_week: dict[str, float],
    games_by_season: dict[int, list[dict[str, Any]]],
    week: int,
    season: int,
    week_rows: dict[tuple[int, int], dict[str, dict[str, Any]]],
    weather_by_home: dict[str, Any],
    allowed_by: dict[tuple[str, str], list[float]],
    league_avg: dict[str, float],
    window: str = "thu",
    vegas_by_team: dict[str, dict[str, Any]] | None = None,
    avg_implied: float | None = None,
    now: Any = None,
) -> dict[str, Any]:
    reserve = {str(x) for x in (roster.get("reserve") or []) if x}
    ids = [str(p) for p in (roster.get("players") or []) if p and str(p) not in reserve]
    current = [str(s) for s in (roster.get("starters") or [])]
    scored: list[dict[str, Any]] = []
    kwargs = dict(
        players=players,
        scoring=scoring,
        proj=proj,
        last_week=last_week,
        games_by_season=games_by_season,
        week=week,
        season=season,
        week_rows=week_rows,
        weather_by_home=weather_by_home,
        allowed_by=allowed_by,
        league_avg=league_avg,
        window=window,
        vegas_by_team=vegas_by_team,
        avg_implied=avg_implied,
        now=now,
    )
    for pid in ids:
        scored.append(score_player(pid, currently_started=pid in current, **kwargs))
    rec = optimize_lineup(scored, slots, current)
    rec_ids = {r["pid"] for r in rec}
    bench = [c for c in scored if c["pid"] not in rec_ids and c["eligible"]]
    bench.sort(key=lambda c: c["rec"], reverse=True)
    return {
        "start": rec,
        "bench": bench[:5],
        "out": [c for c in scored if not c["eligible"]],
        "scored": scored,
    }


def render_league(
    block: dict[str, Any],
    roster_results: list[tuple[str, dict[str, Any]]],
    *,
    week: int,
    window: str = "thu",
    vegas_note: str = "",
) -> str:
    title = (block.get("league") or {}).get("name") or "League"
    lines = [
        f"START/SIT — {title} — WEEK {week}",
        window_label(window),
        "rec = blend(50% Sleeper proj, 25% form, 15% vs opp) × matchup × weather × rest × injury/practice × vegas.",
        "Injury/practice from the Sleeper feed. Weekend window fades Q/D/DNP harder than Thursday evening.",
        "Weather = Open-Meteo (QB/WR/TE/K). Rest = schedule dates. OUT/IR/bye/inactive (weekend) are not started.",
    ]
    if vegas_note:
        lines.append(vegas_note)
    lines.append("")
    for team_name, result in roster_results:
        lines.append(f"=== {team_name} ===")
        soon = [r for r in (result.get("start") or []) if r.get("kickoff_soon") or r.get("kickoff_locked")]
        q_rows = [
            r
            for r in (result.get("start") or []) + (result.get("bench") or [])
            if str(r.get("injury") or "").upper() in {"Q", "QUESTIONABLE", "D", "DOUBTFUL"}
        ]
        if soon or q_rows:
            lines.append("LOCK BEFORE KICKOFF")
            for row in soon:
                state = "already locked" if row.get("kickoff_locked") else "kickoff soon"
                lines.append(f"  {row['name']}: {state} · {row.get('injury') or 'active'}")
            for row in q_rows:
                if row in soon:
                    continue
                lines.append(f"  {row['name']}: {row.get('injury')} — confirm before kickoff")
        lines.append("START")
        for row in result["start"]:
            loc = "home vs" if row.get("home") else "at"
            opp = row.get("opp") or "BYE"
            flag = ""
            if row.get("kickoff_locked"):
                flag = "  [KICKOFF LOCKED]"
            elif not row.get("currently_started"):
                flag = "  [not currently locked]"
            adj = f"  [{' '.join(row['adj'])}]" if row.get("adj") else ""
            extra = f"  | {' · '.join(row['facts'])}" if row["facts"] else ""
            lines.append(
                f"  {row['slot']:<9} {row['name']:<28} rec {row['rec']:.1f} (proj {row['proj']:.1f})  {loc} {opp}{flag}{adj}{extra}"
            )
        if result["bench"]:
            lines.append("BENCH (higher rec than these did not fit a remaining slot)")
            for row in result["bench"][:4]:
                adj = f"  [{' '.join(row['adj'])}]" if row.get("adj") else ""
                extra = f"  | {' · '.join(row['facts'])}" if row["facts"] else ""
                lines.append(f"  {row['name']:<28} rec {row['rec']:.1f} (proj {row['proj']:.1f}){adj}{extra}")
        flagged = [c for c in result["out"] if c["facts"]]
        if flagged:
            lines.append("OUT / BYE")
            for row in flagged[:8]:
                lines.append(f"  {row['name']}: {', '.join(row['facts'])}")
        swaps = [
            r
            for r in result["start"]
            if not r.get("currently_started") and not r.get("kickoff_locked")
        ]
        if swaps:
            lines.append("Not in the current lock: " + ", ".join(s["name"] for s in swaps))
        lines.append("")
    return "\n".join(lines)


def last_week_league_points(block: dict[str, Any]) -> dict[str, float]:
    """Actual points from the last scored matchup (league scoring)."""
    rows = block.get("matchups") or []
    if not any((r.get("players_points") or {}) for r in rows):
        rows = block.get("matchups_prev") or []
    out: dict[str, float] = {}
    for row in rows:
        for pid, pts in (row.get("players_points") or {}).items():
            out[str(pid)] = _f(pts)
    return out


def build_context(
    client: SleeperClient,
    snapshot: dict[str, Any],
    *,
    week: int,
    include_vegas: bool = True,
) -> dict[str, Any]:
    global _weather_unavailable_until
    state = snapshot.get("nfl_state") or {}
    season = int(state.get("season") or state.get("league_season") or 2026)
    prior = int(state.get("previous_season") or (season - 1))
    last_scored = 1
    for block in (snapshot.get("leagues") or {}).values():
        last_scored = max(last_scored, int((block.get("league") or {}).get("last_scored_leg") or 1))
    games_cur = load_schedule(client, season)
    games_prior: list[dict[str, Any]] = []
    try:
        games_prior = load_schedule(client, prior)
    except Exception:
        log.warning("Prior-season schedule unavailable")
    log.info("Loading Sleeper projections for week %s", week)
    proj = load_week_bundle(client, season, week, projections=True)
    week_rows: dict[tuple[int, int], dict[str, dict[str, Any]]] = {}
    for w in range(1, last_scored + 1):
        week_rows[(season, w)] = load_week_bundle(client, season, w, projections=False)
    for w in range(max(1, 15), 19):
        try:
            week_rows[(prior, w)] = load_week_bundle(client, prior, w, projections=False)
        except Exception:
            log.warning("Prior-season week %s stats unavailable", w)
            break
    weather_by_home: dict[str, Any] = {}
    weather_available = time.time() >= _weather_unavailable_until
    for game in games_for_week(games_cur, week):
        home = game.get("home") or ""
        if home in weather_by_home:
            continue
        if not weather_available:
            weather_by_home[home] = None
            continue
        try:
            weather_by_home[home] = weather_for_game(client, game)
        except Exception as exc:
            log.warning("Weather unavailable for %s: %s", home, exc)
            weather_by_home[home] = None
            _weather_unavailable_until = time.time() + _WEATHER_RETRY_AFTER_S
            weather_available = False
    vegas_by_team: dict[str, dict[str, Any]] = {}
    vegas_note = "Vegas implied totals: not loaded."
    if include_vegas:
        try:
            vegas_by_team = load_vegas(client)
        except Exception as exc:
            log.warning("Vegas unavailable: %s", exc)
            vegas_by_team = {}
        if vegas_by_team:
            src = next(iter(vegas_by_team.values())).get("src") or "odds"
            vegas_note = f"Vegas implied totals from {src} (spread + game total). DST uses opponent implied."
            # Apply vegas start times without mutating the original games list
            games_cur = [
                {**game, "start": vegas_by_team.get(game.get("home") or "", {}).get("start", game.get("start"))}
                if vegas_by_team.get(game.get("home") or "", {}).get("start") and not game.get("start")
                else game
                for game in games_cur
            ]
    return {
        "season": season,
        "games_by_season": {season: games_cur, prior: games_prior},
        "proj": proj,
        "week_rows": week_rows,
        "weather_by_home": weather_by_home,
        "vegas_by_team": vegas_by_team,
        "avg_implied": league_avg_implied(vegas_by_team),
        "vegas_note": vegas_note,
    }
