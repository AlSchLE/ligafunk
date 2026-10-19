"""Pure analysis on cached snapshot data. No network I/O."""

from __future__ import annotations

from typing import Any

from recapcore.config import (
    BENCH_BLUNDER_MIN_DELTA,
    DROP_REGRET_MIN_POINTS,
    MAX_BLUNDERS_PER_TEAM,
    TRADE_REGRET_MIN_DELTA,
)
from recapcore.league_format import player_pos_set, slot_accepts, starter_slots
from recapcore.store import player_label, points_total


def bench_blunders(
    row: dict[str, Any],
    roster_positions: list[str],
    players: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Bench players who outscored a starter in a slot they were eligible for in *this* league."""
    starters = row.get("starters") or []
    starter_pts = row.get("starters_points") or []
    points_map: dict[str, float] = row.get("players_points") or {}
    slots = starter_slots(roster_positions)
    starter_set = {str(s) for s in starters if s}
    bench_ids = [p for p in (row.get("players") or []) if str(p) not in starter_set]

    starter_rows = []
    for idx, pid in enumerate(starters):
        if not pid:
            continue
        slot = slots[idx] if idx < len(slots) else "FLEX"
        pts = starter_pts[idx] if idx < len(starter_pts) else float(points_map.get(str(pid), 0) or 0)
        pos = player_pos_set(players.get(str(pid)), str(pid))
        starter_rows.append(
            {"pid": str(pid), "slot": slot, "pts": float(pts or 0), "pos": pos}
        )

    found: list[dict[str, Any]] = []
    for bid in bench_ids:
        bpts = float(points_map.get(str(bid), 0) or 0)
        if bpts < BENCH_BLUNDER_MIN_DELTA:
            continue
        bpos = player_pos_set(players.get(str(bid)), str(bid))
        compatible = [s for s in starter_rows if slot_accepts(s["slot"], bpos)]
        if not compatible:
            continue
        worst = min(compatible, key=lambda s: s["pts"])
        delta = bpts - worst["pts"]
        if delta < BENCH_BLUNDER_MIN_DELTA:
            continue
        found.append(
            {
                "bench_id": str(bid),
                "bench_name": player_label(players, str(bid)),
                "bench_pts": bpts,
                "starter_id": worst["pid"],
                "starter_name": player_label(players, worst["pid"]),
                "starter_slot": worst["slot"],
                "starter_pts": worst["pts"],
                "delta": delta,
            }
        )
    found.sort(key=lambda x: x["delta"], reverse=True)
    return found[:MAX_BLUNDERS_PER_TEAM]


def pair_matchups(rows: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    buckets: dict[Any, list[dict[str, Any]]] = {}
    for row in rows:
        mid = row.get("matchup_id")
        buckets.setdefault(mid, []).append(row)
    pairs = []
    for mid, group in sorted(buckets.items(), key=lambda kv: (kv[0] is None, kv[0] or 0)):
        if mid is None:
            for row in group:
                pairs.append((row, None))
            continue
        if len(group) == 1:
            pairs.append((group[0], None))
        elif len(group) == 2:
            pairs.append((group[0], group[1]))
        elif len(group) >= 3:
            # Sleeper supports 3+ team matchups — pair each team against the next
            for i in range(len(group)):
                a = group[i]
                b = group[(i + 1) % len(group)]
                pairs.append((a, b))
    return pairs


def standings(league_block: dict[str, Any]) -> list[dict[str, Any]]:
    names = league_block.get("team_names") or {}
    rows = []
    for roster in league_block.get("rosters") or []:
        rid = roster.get("roster_id")
        st = roster.get("settings") or {}
        wins = int(st.get("wins") or 0)
        losses = int(st.get("losses") or 0)
        ties = int(st.get("ties") or 0)
        games = wins + losses + ties
        win_pct = (wins + 0.5 * ties) / games if games else 0.0
        pf = points_total(st)
        rows.append(
            {
                "roster_id": rid,
                "name": names.get(str(rid), f"Team {rid}"),
                "wins": wins,
                "losses": losses,
                "ties": ties,
                "win_pct": win_pct,
                "pf": pf,
            }
        )
    rows.sort(key=lambda r: (r["win_pct"], r["pf"]), reverse=True)
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


def _points_by_player(matchups: list[dict[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = {}
    for row in matchups:
        for pid, pts in (row.get("players_points") or {}).items():
            out[str(pid)] = float(pts or 0)
    return out


def transaction_regrets(
    league_block: dict[str, Any],
    players: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Drops/trades from the previous week that mattered in the recap week."""
    names = league_block.get("team_names") or {}
    week_pts = _points_by_player(league_block.get("matchups") or [])
    regrets: list[dict[str, Any]] = []

    for txn in league_block.get("transactions_prev") or []:
        ttype = txn.get("type")
        drops = txn.get("drops") or {}
        adds = txn.get("adds") or {}
        if ttype in {"waiver", "free_agent"}:
            for pid, rid in drops.items():
                pts = week_pts.get(str(pid), 0.0)
                if pts < DROP_REGRET_MIN_POINTS:
                    continue
                regrets.append(
                    {
                        "kind": "drop",
                        "roster_id": rid,
                        "team": names.get(str(rid), f"Team {rid}"),
                        "player": player_label(players, str(pid)),
                        "points": pts,
                    }
                )
        if ttype == "trade":
            # Compare recap-week scoring of players leaving vs arriving per roster.
            by_roster: dict[int, dict[str, list[tuple[str, float]]]] = {}
            for pid, rid in drops.items():
                try:
                    rid_int = int(rid)
                except (TypeError, ValueError):
                    continue
                by_roster.setdefault(rid_int, {"out": [], "in": []})["out"].append(
                    (str(pid), week_pts.get(str(pid), 0.0))
                )
            for pid, rid in adds.items():
                try:
                    rid_int = int(rid)
                except (TypeError, ValueError):
                    continue
                by_roster.setdefault(rid_int, {"out": [], "in": []})["in"].append(
                    (str(pid), week_pts.get(str(pid), 0.0))
                )
            for rid, sides in by_roster.items():
                out_pts = sum(p for _, p in sides["out"])
                in_pts = sum(p for _, p in sides["in"])
                delta = in_pts - out_pts
                if abs(delta) < TRADE_REGRET_MIN_DELTA:
                    continue
                regrets.append(
                    {
                        "kind": "trade",
                        "roster_id": rid,
                        "team": names.get(str(rid), f"Team {rid}"),
                        "delta": delta,
                        "gave": ", ".join(player_label(players, p) + f" ({pts:.1f})" for p, pts in sides["out"]) or "—",
                        "got": ", ".join(player_label(players, p) + f" ({pts:.1f})" for p, pts in sides["in"]) or "—",
                    }
                )
    regrets.sort(key=lambda r: r.get("points") or abs(r.get("delta") or 0), reverse=True)
    return regrets[:12]


def lineup_snapshot_text(
    league_block: dict[str, Any],
    players: dict[str, dict[str, str]],
    *,
    current: bool,
) -> list[str]:
    """Starter lists labeled with this league's roster slots."""
    names = league_block.get("team_names") or {}
    slots = starter_slots((league_block.get("league") or {}).get("roster_positions") or [])
    lines = []

    def _bit(pid: str, slot: str, score: float | None) -> str:
        label = player_label(players, pid) if pid else "—"
        if score is None:
            return f"{slot} {label}"
        return f"{slot} {label} {float(score):.1f}"

    if current:
        for roster in league_block.get("rosters") or []:
            rid = roster.get("roster_id")
            starters = roster.get("starters") or []
            bits = []
            for i, slot in enumerate(slots):
                pid = starters[i] if i < len(starters) else ""
                bits.append(_bit(str(pid) if pid else "", slot, None))
            lines.append(f"{names.get(str(rid), rid)}: " + " | ".join(bits))
        return lines

    for row in league_block.get("matchups") or []:
        rid = row.get("roster_id")
        starters = row.get("starters") or []
        pts = row.get("starters_points") or []
        pmap = row.get("players_points") or {}
        bits = []
        for i, slot in enumerate(slots):
            pid = starters[i] if i < len(starters) else ""
            if pid and i < len(pts):
                score = pts[i]
            elif pid:
                score = pmap.get(str(pid), 0)
            else:
                score = 0.0
            bits.append(_bit(str(pid) if pid else "", slot, float(score or 0)))
        lines.append(f"{names.get(str(rid), rid)} ({float(row.get('points') or 0):.2f}): " + " | ".join(bits))
    return lines
