"""Tuesday waiver assistant: who to add, who to drop, how much FAAB to bid."""

from __future__ import annotations

from typing import Any

from recapcore.league_format import player_pos_set, slot_accepts
from recapcore.startsit import (
    cannot_play,
    injury_status,
    league_points,
    score_player,
)
from recapcore.store import player_label

REPLACE_MIN_DELTA = 0.8


def rostered_ids(block: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for roster in block.get("rosters") or []:
        for pid in roster.get("players") or []:
            if pid:
                out.add(str(pid))
        for pid in roster.get("reserve") or []:
            if pid:
                out.add(str(pid))
    return out


def roster_cap(league: dict[str, Any]) -> int:
    positions = league.get("roster_positions") or []
    return len([p for p in positions if p not in {"IR", "TAXI"}])


def faab_state(league: dict[str, Any], roster: dict[str, Any]) -> dict[str, Any]:
    """Sleeper: waiver_type 2 = FAAB. remaining = start - used."""
    start = league.get("waiver_budget")
    used = (roster.get("settings") or {}).get("waiver_budget_used")
    wtype = league.get("waiver_type")
    try:
        start_i = int(start) if start is not None else 100
    except (TypeError, ValueError):
        start_i = 100
    try:
        used_i = int(used or 0)
    except (TypeError, ValueError):
        used_i = 0
    try:
        wtype_i = int(wtype) if wtype is not None else 2
    except (TypeError, ValueError):
        wtype_i = 2
    is_faab = wtype_i == 2
    remaining = max(0, start_i - used_i)
    return {
        "is_faab": is_faab,
        "start": start_i,
        "used": used_i,
        "remaining": remaining,
        "priority": (roster.get("settings") or {}).get("waiver_position"),
        "waiver_type": wtype,
    }


def faab_bid(
    *,
    delta: float,
    remaining: int,
    start_budget: int,
    week: int,
    playoff_week: int,
    handcuff: bool,
) -> dict[str, Any]:
    """Bid as a share of *remaining* FAAB. Leaves a reserve until late season."""
    if remaining <= 0:
        return {"bid": 0, "max_bid": 0, "why": "no FAAB left — wait for free agency after waivers"}
    try:
        weeks_left = max(1, int(playoff_week or 15) - int(week or 1))
    except (TypeError, ValueError):
        weeks_left = 8
    reserve = 0 if weeks_left <= 2 else max(1, int(round(start_budget * 0.08)))
    spendable = remaining if remaining <= reserve else remaining - reserve
    spendable = max(1, spendable)
    if handcuff:
        pct, why = 0.22, "handcuff for your injured/questionable starter"
    elif delta >= 8:
        pct, why = 0.28, "large rec upgrade vs the drop"
    elif delta >= 4:
        pct, why = 0.14, "clear starter-level upgrade"
    elif delta >= 1.5:
        pct, why = 0.06, "modest upgrade"
    else:
        pct, why = 0.02, "stash / speculative"
    bid = int(round(spendable * pct))
    bid = max(1, min(bid, remaining))
    if remaining > 1 and bid >= remaining and weeks_left > 2:
        bid = remaining - 1
    max_bid = min(remaining, max(bid, int(round(spendable * min(0.40, pct * 1.35)))))
    if remaining > 1 and max_bid >= remaining and weeks_left > 2:
        max_bid = remaining - 1
    return {"bid": bid, "max_bid": max_bid, "why": why, "pct_remaining": bid / remaining}


def _my_injured_teams(
    scored: list[dict[str, Any]],
) -> set[str]:
    hot = set()
    for row in scored:
        st = str(row.get("injury") or "").upper()
        if st in {"Q", "QUESTIONABLE", "D", "DOUBTFUL", "OUT"} and row.get("team"):
            hot.add(str(row["team"]).upper())
        if row.get("eligible") is False and row.get("team"):
            hot.add(str(row["team"]).upper())
    return hot


def candidate_ids(
    players: dict[str, dict[str, str]],
    rostered: set[str],
    slots: list[str],
    proj: dict[str, dict[str, Any]],
    scoring: dict[str, Any],
    trending: list[str],
    handcuff_teams: set[str],
) -> list[str]:
    ranked: list[tuple[float, str]] = []
    extra: list[str] = []
    for pid, info in players.items():
        if pid in rostered:
            continue
        if cannot_play(injury_status(info)):
            continue
        team = (info.get("t") or "").upper()
        if pid.isalpha() and 2 <= len(pid) <= 4:
            team = pid.upper()
        if not team:
            continue
        positions = player_pos_set(info, pid)
        if not any(slot_accepts(slot, positions) for slot in slots):
            continue
        pts = league_points(proj.get(pid) or {}, scoring)
        dco = info.get("dco") or ""
        is_backup = dco in {"1", "2"} or pid in trending or team in handcuff_teams
        if pts < 1.5 and not is_backup:
            continue
        ranked.append((pts, pid))
        if is_backup and pts < 1.5:
            extra.append(pid)
    ranked.sort(reverse=True)
    ordered: list[str] = []
    seen: set[str] = set()
    for pid in trending + extra + [p for _pts, p in ranked[:70]]:
        if pid not in seen and pid not in rostered:
            seen.add(pid)
            ordered.append(pid)
        if len(ordered) >= 80:
            break
    return ordered


def pick_drop(
    fa: dict[str, Any],
    mine: list[dict[str, Any]],
    start_ids: set[str],
    cap: int,
) -> dict[str, Any] | None:
    """Drop the weakest rostered player the FA could replace, else the weakest bench."""
    if len(mine) < cap:
        return None
    same = [
        m
        for m in mine
        if m.get("eligible") and (m["positions"] & fa["positions"] or m.get("pos") == fa.get("pos"))
    ]
    bench_same = [m for m in same if m["pid"] not in start_ids]
    pool = bench_same or same or [m for m in mine if m.get("eligible")]
    if not pool:
        pool = mine
    return min(pool, key=lambda m: m.get("rec") or 0.0)


def build_claims(
    *,
    fa_rows: list[dict[str, Any]],
    mine: list[dict[str, Any]],
    start: list[dict[str, Any]],
    cap: int,
    faab: dict[str, Any],
    week: int,
    playoff_week: int,
    injured_teams: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    start_ids = {r["pid"] for r in start}
    claims: list[dict[str, Any]] = []
    for fa in fa_rows:
        if not fa.get("eligible"):
            continue
        drop = pick_drop(fa, mine, start_ids, cap)
        drop_rec = float(drop["rec"]) if drop else 0.0
        delta = float(fa["rec"]) - drop_rec
        if drop and delta < REPLACE_MIN_DELTA:
            continue
        handcuff = bool(
            fa.get("team")
            and fa.get("pos")
            and (str(fa["team"]).upper(), fa.get("pos")) in injured_teams
        )
        bid = (
            faab_bid(
                delta=max(delta, 0.0),
                remaining=int(faab["remaining"]),
                start_budget=int(faab["start"]),
                week=week,
                playoff_week=playoff_week,
                handcuff=handcuff,
            )
            if faab.get("is_faab")
            else None
        )
        claims.append(
            {
                "add": fa,
                "drop": drop,
                "delta": delta,
                "handcuff": handcuff,
                "bid": bid,
            }
        )
    claims.sort(key=lambda c: (c["handcuff"], c["delta"]), reverse=True)
    # One drop target per add; skip duplicate drops except first.
    seen_drop: set[str] = set()
    unique: list[dict[str, Any]] = []
    for c in claims:
        did = (c["drop"] or {}).get("pid") or ""
        if did and did in seen_drop:
            continue
        if did:
            seen_drop.add(did)
        unique.append(c)
        if len(unique) >= 8:
            break
    return unique


def render_waivers(
    block: dict[str, Any],
    *,
    team_name: str,
    claims: list[dict[str, Any]],
    faab: dict[str, Any],
    week: int,
    alerts: list[str],
) -> str:
    title = (block.get("league") or {}).get("name") or "League"
    lines = [
        f"WAIVERS — {title} — WEEK {week} — {team_name}",
        "Tuesday evening: add / drop / FAAB. rec uses the same formula as start/sit.",
    ]
    if faab.get("is_faab"):
        lines.append(
            f"FAAB: ${faab['remaining']} left of ${faab['start']} "
            f"(used ${faab['used']}). Bids are a share of *remaining* budget; "
            f"~8% of original is held back until late season."
        )
    else:
        lines.append(
            f"Rolling / standard waivers (priority {faab.get('priority') or '?'}). No FAAB in this league."
        )
    lines.append("")
    if alerts:
        lines.append("HANDCUFF / INJURY")
        for a in alerts:
            lines.append(f"  {a}")
        lines.append("")
    if not claims:
        lines.append("No add that beats a drop by 0.8 rec. Hold FAAB.")
        return "\n".join(lines)
    lines.append("CLAIM ORDER")
    for i, c in enumerate(claims, 1):
        add, drop = c["add"], c["drop"]
        tag = " HANDCUFF" if c["handcuff"] else ""
        lines.append(f"{i}.{tag} ADD  {add['name']:<32} rec {add['rec']:.1f} (proj {add['proj']:.1f})")
        extra = " · ".join(add.get("facts") or [])[:180]
        if extra:
            lines.append(f"      {extra}")
        if drop:
            where = "START" if drop.get("currently_started") else "BN"
            lines.append(
                f"      DROP {drop['name']:<32} rec {drop['rec']:.1f} ({where})  "
                f"upgrade {c['delta']:+.1f}"
            )
        else:
            lines.append("      DROP none (roster spot open)")
        if c.get("bid"):
            b = c["bid"]
            lines.append(
                f"      BID  ${b['bid']}  (do not exceed ${b['max_bid']}) of ${faab['remaining']} left — {b['why']}"
            )
        elif not faab.get("is_faab"):
            lines.append(f"      PRIORITY claim #{faab.get('priority') or '?'}")
        lines.append("")
    return "\n".join(lines)


def score_pool(
    ids: list[str],
    *,
    score_kwargs: dict[str, Any],
    current: set[str],
) -> list[dict[str, Any]]:
    rows = []
    for pid in ids:
        row = score_player(pid, currently_started=pid in current, **score_kwargs)
        info = (score_kwargs.get("players") or {}).get(pid) or {}
        team = (info.get("t") or "").upper()
        if pid.isalpha() and 2 <= len(pid) <= 4:
            team = pid.upper()
        row["team"] = team
        row["injury"] = injury_status(info)
        row["dco"] = info.get("dco") or ""
        rows.append(row)
    return rows
