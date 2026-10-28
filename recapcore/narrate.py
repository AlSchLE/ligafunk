"""Recap narrator: voice packs (EN/DE) combined with seasonal themes.

A recap is assembled from:
- a *style* (voice + language): nfl_reporter (English) or bromance (German)
- a *theme* overlay: none, christmas, halloween, tight_end (German flavour)

Each generation uses a fresh RNG, so two recaps for the same week read
differently. Pass ``seed`` for deterministic output (tests, previews).
"""

from __future__ import annotations

import random
from typing import Any

from recapcore import copy_bank as en
from recapcore import styles_de as de
from recapcore.analyze import (
    bench_blunders,
    pair_matchups,
    standings,
    transaction_regrets,
)
from recapcore.copy_bank import Deck
from recapcore.league_format import format_headline
from recapcore.options import (
    DEFAULT_STYLE,
    DEFAULT_THEME,
    STYLE_META,
    THEME_META,
    normalize_style,
    normalize_theme,
)
from recapcore.store import player_label
from recapcore.themes import THEME_DECKS


# ---------------------------------------------------------------------------
# Style packs
# ---------------------------------------------------------------------------

_REQUIRED_DECKS = (
    "openers",
    "match_leads",
    "blowout",
    "close",
    "solid",
    "star",
    "quiet",
    "blunder_intros",
    "blunder_bullets",
    "no_blunder",
    "drop",
    "trade_win",
    "trade_loss",
    "no_moves",
    "closers",
    "bye",
    "no_matchups",
)

_STYLE_PACKS: dict[str, dict[str, Any]] = {
    "nfl_reporter": {
        "openers": en.LEAGUE_OPENERS,
        "match_leads": en.MATCH_LEADS,
        "blowout": en.BLOWOUT,
        "close": en.CLOSE,
        "solid": en.SOLID,
        "star": en.STAR_LINES,
        "quiet": en.QUIET_STAR,
        "blunder_intros": en.BLUNDER_INTROS,
        "blunder_bullets": en.BLUNDER_BULLETS,
        "no_blunder": en.NO_BLUNDER,
        "drop": en.DROP_LINES,
        "trade_win": en.TRADE_WIN,
        "trade_loss": en.TRADE_LOSS,
        "no_moves": en.NO_MOVES,
        "closers": en.CLOSERS,
        "bye": ["{name} has a bye ({pts:.2f}). No opponent, still no banner."],
        "no_matchups": ["No matchup data for this week yet."],
        "headings": {
            "matchups": "### Matchups",
            "transactions": "### Waiver wire & trade desk (impact this week)",
            "standings": "### Standings",
        },
    },
    "bromance": {
        "openers": de.BROMANCE_OPENERS,
        "match_leads": de.BROMANCE_MATCH_LEADS,
        "blowout": de.BROMANCE_BLOWOUT,
        "close": de.BROMANCE_CLOSE,
        "solid": de.BROMANCE_SOLID,
        "star": de.BROMANCE_STAR,
        "quiet": de.BROMANCE_QUIET,
        "blunder_intros": de.BROMANCE_BLUNDER_INTROS,
        "blunder_bullets": de.BROMANCE_BLUNDER_BULLETS,
        "no_blunder": de.BROMANCE_NO_BLUNDER,
        "drop": de.BROMANCE_DROP,
        "trade_win": de.BROMANCE_TRADE_WIN,
        "trade_loss": de.BROMANCE_TRADE_LOSS,
        "no_moves": de.BROMANCE_NO_MOVES,
        "closers": de.BROMANCE_CLOSERS,
        "bye": de.BROMANCE_BYE,
        "no_matchups": de.BROMANCE_NO_MATCHUPS,
        "headings": {
            "matchups": "### Die Spiele",
            "transactions": "### Waiver Wire & Trade Desk",
            "standings": "### Tabelle",
        },
    },
}


class Voice:
    """One voice per generated recap so catchphrases are not reused inside it."""

    def __init__(
        self,
        style: str = DEFAULT_STYLE,
        theme: str = DEFAULT_THEME,
        rng: random.Random | None = None,
    ) -> None:
        self.style_id = normalize_style(style)
        self.theme_id = normalize_theme(theme)
        self.rng = rng or random.Random()

        pack = _STYLE_PACKS[self.style_id]
        theme_deck = THEME_DECKS.get(self.theme_id) or {}

        self.openers = Deck(theme_deck.get("openers") or pack["openers"], self.rng)
        self.closers = Deck(theme_deck.get("closers") or pack["closers"], self.rng)
        self.lead = Deck(pack["match_leads"], self.rng)
        self.blowout = Deck(pack["blowout"], self.rng)
        self.close = Deck(pack["close"], self.rng)
        self.solid = Deck(pack["solid"], self.rng)
        self.star = Deck(pack["star"], self.rng)
        self.quiet = Deck(pack["quiet"], self.rng)
        self.bl_intro = Deck(pack["blunder_intros"], self.rng)
        self.bl_bullet = Deck(pack["blunder_bullets"], self.rng)
        self.no_bl = Deck(pack["no_blunder"], self.rng)
        self.drop = Deck(pack["drop"], self.rng)
        self.trade_w = Deck(pack["trade_win"], self.rng)
        self.trade_l = Deck(pack["trade_loss"], self.rng)
        self.no_moves = Deck(pack["no_moves"], self.rng)
        self.bye = Deck(pack["bye"], self.rng)
        self.no_matchups = Deck(pack["no_matchups"], self.rng)
        self.flavor = Deck(theme_deck.get("flavor") or [], self.rng)

        self.headings = dict(pack["headings"])
        self.headings.update(theme_deck.get("headings") or {})

    @property
    def has_theme(self) -> bool:
        return self.theme_id != "none"

    @property
    def style_meta(self) -> dict[str, str]:
        return STYLE_META[self.style_id]

    @property
    def theme_meta(self) -> dict[str, str]:
        return THEME_META[self.theme_id]

    def banner(self) -> str:
        text = self.style_meta["emoji"]
        if self.has_theme:
            text += self.theme_meta["emoji"]
        return text


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


def _top_te(
    rows: list[dict[str, Any]],
    players: dict[str, dict[str, str]],
) -> tuple[str, float] | None:
    """Best starting tight end across both rosters (for the TE theme)."""
    scored: list[tuple[str, float]] = []
    for row in rows:
        pmap = row.get("players_points") or {}
        for pid in row.get("starters") or []:
            if not pid:
                continue
            info = players.get(str(pid)) or {}
            if (info.get("p") or "").upper() != "TE":
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
        lines.append(voice.bye.draw(name=an, pts=ap))
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
    if top and top[1] >= 22:
        lines.append(voice.star.draw(name=top[0], pts=top[1]))
    elif voice.rng.random() < 0.45:
        lines.append(voice.quiet.draw())

    # Seasonal flavour: one themed line for most matchups.
    if voice.has_theme and voice.rng.random() < 0.7:
        te = _top_te([a, b], players)
        lines.append(
            voice.flavor.draw(
                **ctx,
                name=top[0] if top else "niemand",
                pts=top[1] if top else 0.0,
                te_name=te[0] if te else "kein TE",
                te_pts=te[1] if te else 0.0,
            )
        )

    for row, team in ((a, an), (b, bn)):
        blunders = bench_blunders(row, roster_positions, players)
        if not blunders:
            if voice.rng.random() < 0.35:
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


def league_recap(
    block: dict[str, Any],
    players: dict[str, dict[str, str]],
    *,
    week: int,
    style: str = DEFAULT_STYLE,
    theme: str = DEFAULT_THEME,
    seed: Any | None = None,
) -> str:
    rng = random.Random(seed) if seed is not None else random.Random()
    voice = Voice(style, theme, rng)

    league = block.get("league") or {}
    title = league.get("name") or "League"
    positions = league.get("roster_positions") or []
    pairs = pair_matchups(block.get("matchups") or [])
    table = standings(block)

    title_line = f"{voice.banner()}  {title}  —  WEEK {week} RECAP"
    if voice.has_theme:
        title_line += f"  ·  {voice.theme_meta['name']}"

    parts = [
        "=" * 36,
        title_line,
        voice.openers.draw(week=week),
        "=" * 36,
        "",
        voice.headings["matchups"],
        "",
    ]
    if not pairs:
        parts.append(voice.no_matchups.draw())
    else:
        for i, (a, b) in enumerate(pairs, start=1):
            parts.append(f"— Game {i} —")
            parts.append(_matchup_story(voice, a, b, block, players, positions, week=week))
            parts.append("")

    regrets = transaction_regrets(block, players)
    parts.append(voice.headings["transactions"])
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
    parts.append(voice.headings["standings"])
    for row in table:
        parts.append(f"  {row['rank']}. {row['name']}  {_fmt_record(row)}  PF {row['pf']:.2f}")
    parts.append("")
    parts.append(voice.closers.draw())
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
