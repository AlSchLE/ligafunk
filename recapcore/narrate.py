"""NFL gameday-style recap copy. Templates + facts, phrases without replacement."""

from __future__ import annotations

import random
from typing import Any

from recapcore.analyze import (
    bench_blunders,
    pair_matchups,
    standings,
    transaction_regrets,
)
from recapcore.copy_bank import (
    BLUNDER_BULLETS,
    BLUNDER_INTROS,
    BLOWOUT,
    CLOSE,
    CLOSERS,
    DROP_LINES,
    LEAGUE_OPENERS,
    MATCH_LEADS,
    NO_BLUNDER,
    NO_MOVES,
    QUIET_STAR,
    SOLID,
    STAR_LINES,
    TRADE_LOSS,
    TRADE_WIN,
    Deck,
)
from recapcore.league_format import format_headline
from recapcore.store import player_label


class Voice:
    """One voice per generated recap so catchphrases are not reused inside it."""

    def __init__(self) -> None:
        self.opener = Deck(LEAGUE_OPENERS)
        self.lead = Deck(MATCH_LEADS)
        self.blowout = Deck(BLOWOUT)
        self.close = Deck(CLOSE)
        self.solid = Deck(SOLID)
        self.star = Deck(STAR_LINES)
        self.quiet = Deck(QUIET_STAR)
        self.bl_intro = Deck(BLUNDER_INTROS)
        self.bl_bullet = Deck(BLUNDER_BULLETS)
        self.no_bl = Deck(NO_BLUNDER)
        self.drop = Deck(DROP_LINES)
        self.trade_w = Deck(TRADE_WIN)
        self.trade_l = Deck(TRADE_LOSS)
        self.no_moves = Deck(NO_MOVES)
        self.closer = Deck(CLOSERS)


def _name(block: dict[str, Any], rid: Any) -> str:
    return (block.get("team_names") or {}).get(str(rid), f"Team {rid}")


def _fmt_record(row: dict[str, Any]) -> str:
    if row.get("ties"):
        return f"{row['wins']}-{row['losses']}-{row['ties']}"
    return f"{row['wins']}-{row['losses']}"


def _top_starter(
    rows: list[dict[str, Any]],
    players: dict[str, dict[str, str]],
) -> tuple[str, float] | None:
    scored: list[tuple[str, float]] = []
    for row in rows:
        pmap = row.get("players_points") or {}
        for pid in row.get("starters") or []:
            if not pid:
                continue
            scored.append((str(pid), float(pmap.get(str(pid), 0) or 0)))
    if not scored:
        return None
    pid, pts = max(scored, key=lambda kv: kv[1])
    return player_label(players, pid), pts


def _matchup_story(
    voice: Voice,
    a: dict[str, Any],
    b: dict[str, Any] | None,
    block: dict[str, Any],
    players: dict[str, dict[str, str]],
    roster_positions: list[str],
    *,
    week: int,
) -> str:
    lines = []
    an = _name(block, a.get("roster_id"))
    ap = float(a.get("points") or 0)
    if b is None:
        lines.append(f"{an} has a bye ({ap:.2f}). No opponent, still no banner.")
        return "\n".join(lines)

    bn = _name(block, b.get("roster_id"))
    bp = float(b.get("points") or 0)
    if ap >= bp:
        winner, loser, wp, lp, wn, ln = a, b, ap, bp, an, bn
    else:
        winner, loser, wp, lp, wn, ln = b, a, bp, ap, bn, an
    margin = abs(ap - bp)
    ctx = {
        "away": an,
        "home": bn,
        "winner": wn,
        "loser": ln,
        "wp": wp,
        "lp": lp,
        "margin": margin,
        "week": week,
    }
    lines.append(voice.lead.draw(**ctx))
    lines.append(f"{an} {ap:.2f}  —  {bp:.2f} {bn}")
    if margin >= 25:
        lines.append(voice.blowout.draw(**ctx))
    elif margin <= 8:
        lines.append(voice.close.draw(**ctx))
    else:
        lines.append(voice.solid.draw(**ctx))

    top = _top_starter([a, b], players)
    if top and top[1] >= 28:
        lines.append(voice.star.draw(name=top[0], pts=top[1]))
    elif top and top[1] >= 22:
        lines.append(voice.star.draw(name=top[0], pts=top[1]))
    elif random.random() < 0.45:
        lines.append(voice.quiet.draw())

    for row, team in ((a, an), (b, bn)):
        blunders = bench_blunders(row, roster_positions, players)
        if not blunders:
            if random.random() < 0.35:
                lines.append(voice.no_bl.draw(team=team))
            continue
        lines.append(voice.bl_intro.draw(team=team))
        for bl in blunders:
            lines.append(
                "  • "
                + voice.bl_bullet.draw(
                    bench=bl["bench_name"],
                    bpts=bl["bench_pts"],
                    starter=bl["starter_name"],
                    spts=bl["starter_pts"],
                    slot=bl.get("starter_slot") or "FLEX",
                    delta=bl["delta"],
                )
            )
    return "\n".join(lines)


def league_recap(block: dict[str, Any], players: dict[str, dict[str, str]], *, week: int) -> str:
    voice = Voice()
    league = block.get("league") or {}
    title = league.get("name") or "League"
    positions = league.get("roster_positions") or []
    pairs = pair_matchups(block.get("matchups") or [])
    table = standings(block)

    parts = [
        "=" * 72,
        f"🎙️  {title}  —  WEEK {week} RECAP",
        voice.opener.draw(week=week),
        "=" * 72,
        "",
        "### Matchups",
        "",
    ]
    if not pairs:
        parts.append("No matchup data for this week yet.")
    else:
        for i, (a, b) in enumerate(pairs, start=1):
            parts.append(f"— Game {i} —")
            parts.append(_matchup_story(voice, a, b, block, players, positions, week=week))
            parts.append("")

    regrets = transaction_regrets(block, players)
    parts.append("### Waiver wire & trade desk (impact this week)")
    if not regrets:
        parts.append(voice.no_moves.draw())
    else:
        for r in regrets:
            if r["kind"] == "drop":
                parts.append(
                    "  • "
                    + voice.drop.draw(team=r["team"], player=r["player"], points=r["points"])
                )
            elif r["delta"] > 0:
                parts.append(
                    "  • "
                    + voice.trade_w.draw(team=r["team"], delta=r["delta"], gave=r["gave"], got=r["got"])
                )
            else:
                parts.append(
                    "  • "
                    + voice.trade_l.draw(team=r["team"], delta=r["delta"], gave=r["gave"], got=r["got"])
                )
    parts.append("")
    parts.append("### Standings")
    for row in table:
        parts.append(f"  {row['rank']}. {row['name']}  {_fmt_record(row)}  PF {row['pf']:.2f}")
    parts.append("")
    parts.append(voice.closer.draw())
    return "\n".join(parts)


def intro_text(block: dict[str, Any], draft_picks: list[dict[str, Any]]) -> str:
    league = block.get("league") or {}
    names = block.get("team_names") or {}
    title = league.get("name") or "League"
    round1 = sorted(
        [p for p in draft_picks if p.get("round") == 1],
        key=lambda p: p.get("pick_no") or 0,
    )
    pick_lines = []
    for p in round1:
        team = names.get(str(p.get("roster_id")), "Unknown team")
        meta = p.get("metadata") or {}
        pname = f"{meta.get('first_name', '')} {meta.get('last_name', '')}".strip() or "?"
        pos = meta.get("position", "")
        pick_lines.append(f"Pick {p.get('pick_no')}: {team} takes {pname} ({pos})")
    draft_block = "\n".join(pick_lines) if pick_lines else "Draft data not in yet."
    fmt = format_headline(league)
    if "K" in (league.get("lineup") or "").split(" / "):
        extra = "Kicker slot included — someone will sit the wrong one in Week 1."
    else:
        extra = "No kicker, two FLEX spots: that's where the first sins happen."
    return (
        f"{title}\n{fmt}\n{extra}\n\n"
        f"Round 1, pick by pick:\n{draft_block}\n\n"
        f"Season's open. Don't sleep on waivers.\n"
    )
