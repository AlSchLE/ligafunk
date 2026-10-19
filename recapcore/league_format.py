"""Per-league scoring + roster-slot rules.

Points on matchups already come from Sleeper using each league's scoring.
This module only interprets *which* slots exist and how to talk about format,
so Saxy (no K/IDP) and NTS (K + IDP_FLEX, INT -2, vs league average) stay distinct.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

BENCH_SLOTS = frozenset({"BN", "IR", "TAXI"})

FLEX_ELIGIBLE = frozenset({"RB", "WR", "TE"})
SUPER_FLEX_ELIGIBLE = frozenset({"QB", "RB", "WR", "TE"})
REC_FLEX_ELIGIBLE = frozenset({"WR", "TE"})
WRRB_FLEX_ELIGIBLE = frozenset({"WR", "RB"})
IDP_ELIGIBLE = frozenset(
    {
        "DL",
        "LB",
        "DB",
        "IDP",
        "DE",
        "DT",
        "NT",
        "ILB",
        "OLB",
        "MLB",
        "CB",
        "S",
        "FS",
        "SS",
        "NB",
    }
)
DST_ELIGIBLE = frozenset({"DEF", "DST"})
K_ELIGIBLE = frozenset({"K"})

LEAGUE_TYPE_LABEL = {
    0: "Redraft",
    1: "Keeper",
    2: "Dynasty",
}


def starter_slots(roster_positions: list[str]) -> list[str]:
    return [p for p in roster_positions if p not in BENCH_SLOTS]


def lineup_recipe(roster_positions: list[str]) -> str:
    slots = starter_slots(roster_positions)
    counts = Counter(slots)
    # Preserve first-seen slot order (Sleeper starter list order).
    seen: list[str] = []
    for slot in slots:
        if slot not in seen:
            seen.append(slot)
    parts = []
    for slot in seen:
        n = counts[slot]
        parts.append(f"{n} {slot}" if n > 1 else slot)
    return " / ".join(parts) if parts else "—"


def player_pos_set(info: dict[str, str] | None, player_id: str | None = None) -> set[str]:
    positions: set[str] = set()
    if info:
        if info.get("p"):
            positions.add(str(info["p"]).upper())
        for token in str(info.get("fp") or "").split(","):
            token = token.strip().upper()
            if token:
                positions.add(token)
    pid = str(player_id or "")
    # Team DST ids are three-letter codes in Sleeper matchups.
    if pid.isalpha() and 2 <= len(pid) <= 4:
        positions.add("DEF")
    return {p for p in positions if p}


def slot_accepts(slot: str, positions: set[str]) -> bool:
    slot = (slot or "").upper()
    if slot in BENCH_SLOTS or not slot:
        return False
    if slot in positions:
        return True
    if slot in {"FLEX"}:
        return bool(positions & FLEX_ELIGIBLE)
    if slot in {"SUPER_FLEX", "Q/W/R/T"}:
        return bool(positions & SUPER_FLEX_ELIGIBLE)
    if slot in {"REC_FLEX"}:
        return bool(positions & REC_FLEX_ELIGIBLE)
    if slot in {"WRRB_FLEX"}:
        return bool(positions & WRRB_FLEX_ELIGIBLE)
    if slot in {"IDP_FLEX", "IDP"}:
        return bool(positions & IDP_ELIGIBLE) or any(p.startswith("IDP") for p in positions)
    if slot == "DEF":
        return bool(positions & DST_ELIGIBLE)
    if slot == "K":
        return bool(positions & K_ELIGIBLE)
    if slot in IDP_ELIGIBLE:
        return bool(positions & IDP_ELIGIBLE)
    return False


def _f(settings: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        return float(settings.get(key) if settings.get(key) is not None else default)
    except (TypeError, ValueError):
        return default


def summarize_format(league: dict[str, Any]) -> dict[str, Any]:
    """Compact, recap-friendly view of scoring + lineup. Points stay Sleeper-scored."""
    positions = list(league.get("roster_positions") or [])
    scoring = league.get("scoring") or {}
    rec = _f(scoring, "rec")
    if rec >= 0.99:
        ppr = "PPR"
    elif rec > 0:
        ppr = f"{rec:g} PPR"
    else:
        ppr = "standard (no PPR)"

    slots = starter_slots(positions)
    has_k = "K" in slots
    has_idp = any(s in IDP_ELIGIBLE or s in {"IDP_FLEX", "IDP"} for s in slots)
    has_def = "DEF" in slots
    league_type = league.get("league_type")
    type_label = LEAGUE_TYPE_LABEL.get(int(league_type), "Custom") if league_type is not None else "Custom"

    bits = [ppr, type_label, lineup_recipe(positions)]
    bits.append(f"Pass-TD {_f(scoring, 'pass_td'):g}")
    bits.append(f"INT {_f(scoring, 'pass_int'):g}")
    bits.append(f"Rush/Rec-TD {_f(scoring, 'rush_td'):g}/{_f(scoring, 'rec_td'):g}")
    if has_k:
        extra_k = []
        if _f(scoring, "fgm_yds_over_30"):
            extra_k.append(f"FG yards over 30: {_f(scoring, 'fgm_yds_over_30'):g}/yd")
        bits.append("Kicker" + (f" ({', '.join(extra_k)})" if extra_k else ""))
    if has_idp:
        idp_bits = []
        for key, label in (
            ("idp_tkl_solo", "solo tkl"),
            ("idp_tkl_ast", "ast"),
            ("idp_sack", "sack"),
            ("idp_int", "INT"),
            ("idp_ff", "FF"),
            ("idp_tkl_loss", "TFL"),
        ):
            val = _f(scoring, key)
            if val:
                idp_bits.append(f"{label} {val:g}")
        bits.append("IDP" + (f" ({', '.join(idp_bits)})" if idp_bits else ""))
    if has_def:
        bits.append("team DEF")
    if league.get("league_average_match"):
        bits.append("also vs league average")

    return {
        "ppr": ppr,
        "type_label": type_label,
        "lineup": lineup_recipe(positions),
        "starter_slots": slots,
        "has_kicker": has_k,
        "has_idp": has_idp,
        "has_def": has_def,
        "headline": " · ".join(bits),
    }


def format_headline(league: dict[str, Any]) -> str:
    return str(summarize_format(league).get("headline") or "")
