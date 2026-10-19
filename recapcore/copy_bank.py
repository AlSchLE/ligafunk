"""English recap phrases, ESPN / fantasy-blog register.

Drawn without replacement inside one recap so catchphrases do not echo.
Templates are filled with real scores, names, and margins.
"""

from __future__ import annotations

import random
from typing import Any


class Deck:
    def __init__(self, lines: list[str]) -> None:
        self._src = [x for x in lines if x]
        self._bag: list[str] = []

    def draw(self, **kwargs: Any) -> str:
        if not self._src:
            return ""
        if not self._bag:
            self._bag = self._src[:]
            random.shuffle(self._bag)
        text = self._bag.pop()
        if kwargs:
            return text.format(**kwargs)
        return text


LEAGUE_OPENERS = [
    "Week {week} is in the books. The box scores are final; the group chat is not.",
    "Sunday's over, the waiver wire is loading, and somebody locked the wrong flex.",
    "Week {week} recap: eight managers, one scoreboard, zero alibis until Tuesday.",
    "From the early window to the late slate — here's how Week {week} actually shook out.",
    "No national TV truck in this league. Still plenty of scoreboard watching.",
    "The pads are off. The points stay. Welcome to the Week {week} film session.",
    "Fantasy football, unfiltered. Week {week} without the benefit of the doubt.",
    "If you muted notifications, this is what you missed in Week {week}.",
    "Another Sunday in the books. Let's go to the film, then to the standings.",
    "Week {week}: smash spots, busts, and at least one lineup that belongs in a museum.",
]

MATCH_LEADS = [
    "{away} and {home} spent the weekend in the same scoreboard. Only one left happy.",
    "Next on the slate: {away} vs {home}. The box score does the talking.",
    "{home} drew {away}. On paper it was a game. In the app it was a verdict.",
    "Matchup watch: {away} against {home}, with no bye-week excuses available.",
    "{away} wanted a shootout. {home} wanted a win. The numbers picked a side.",
    "Set your lineups, live with them: {away} vs {home}.",
    "A full slate later, {away} and {home} finally have a result.",
    "This one was circled. {away} vs {home} is no longer theoretical.",
    "{away} walked in with a plan. {home} walked in with points.",
    "Fantasy's version of a 1 p.m. window: {away} and {home}, no commercial break.",
    "The projection models had opinions. {away} and {home} had actual football.",
    "Call it a measuring-stick game if you want. {away} vs {home} still had to play it.",
]

BLOWOUT = [
    "{margin:.0f}-point gap. That's not a game, that's a statement win.",
    "{winner} dropped {wp:.1f} and sent {loser} home early. Final margin: {margin:.1f}.",
    "Blowout. {loser} never reached the red zone of this matchup.",
    "{winner} put {wp:.1f} on the board; {loser} answered with {lp:.1f}. Microphone drop.",
    "This one was over before the late window. {winner} by {margin:.1f}.",
    "{loser} is still looking for the reset button. {winner} does not need one.",
    "If this were the NFL, the backups would have been in. Margin: {margin:.1f}.",
    "{winner} won in cruise control. {loser} won a very expensive lesson.",
    "Put a fork in it: {winner} by {margin:.1f}, {loser} searching for a pulse.",
    "{winner} smashed. {loser} busted. The standings will remember.",
    "No comeback script in the drawer. {winner} slammed the door.",
    "League-winning vibe from {winner}. League-hurting afternoon for {loser}.",
    "The kind of score that makes you hide the app. {winner} +{margin:.1f}.",
    "Trash-time points could not save {loser}. {winner} had already cashed.",
]

CLOSE = [
    "{margin:.2f} points. That's a drop, an extra point, a wrong flex.",
    "One-possession game, fantasy edition: {winner} {wp:.2f}, {loser} {lp:.2f}.",
    "Photo finish. Replay booth gives it to {winner} by {margin:.2f}.",
    "{loser} had this on a string until the last score trickled in. Short by {margin:.2f}.",
    "Monday-morning heartburn. {winner} tripped over the goal line.",
    "If {loser} starts the bench piece, this chat is a different color tonight.",
    "{margin:.2f} points — an NFL field goal, a fantasy friendship on probation.",
    "Walk-off energy. {winner} survives, {loser} stares at the bench.",
    "Too tight for trash talk. Barely enough for a W: {winner} by {margin:.2f}.",
    "This is why you don't punch out at 1:05. {winner} needed every leftover point.",
    "Coin-flip matchup, ugly landing: {winner} over {loser} by a rounding error.",
    "Start/sit decided this more than the skill players. Margin {margin:.2f} to {winner}.",
]

SOLID = [
    "Workmanlike W: {winner} {wp:.1f}, {loser} {lp:.1f}, gap {margin:.1f}.",
    "{winner} controlled the matchup without turning it into a show. {loser} trails by {margin:.1f}.",
    "Not a blowout, not a thriller — {winner} did the job, {loser} never found a boom game.",
    "{wp:.1f} to {lp:.1f}. {winner} won the middle of the box score.",
    "Business trip. {winner} takes what the slate gives; {loser} left too much on the pine.",
    "Margin {margin:.1f}: enough to win, not enough to screenshot. {winner} will take it.",
    "{loser} hung around until the bench points never arrived. {winner} cashes anyway.",
    "The kind of win that builds a record in Week {week}. Quiet, then official.",
    "Floor over ceiling this time. {winner} was just more consistent than {loser}.",
    "No league-winning explosion required. {winner} stacked enough good-not-great lines.",
    "{winner} won the start/sit battle as much as the talent battle.",
    "A palatable {margin:.1f}-point cushion. {loser} was in it, never ahead of it.",
]

STAR_LINES = [
    "Player of the matchup: {name} with {pts:.1f} — the tape had a starring role.",
    "{name} ({pts:.1f}) carried the stack. Everyone else was complementary.",
    "Boom alert: {name} at {pts:.1f} from a starting slot.",
    "Stat of the night: {name} {pts:.1f}. The rest is context.",
    "{name} posted {pts:.1f} and made the projection look shy.",
    "Ceiling unlocked: {name} {pts:.1f}. That's a smash week.",
    "Without {name} ({pts:.1f}) this box score is a different movie.",
    "{name} went nuclear ({pts:.1f}). Set-and-forget paid the bill.",
    "The slate's best line in this game: {name}, {pts:.1f}, no apology.",
    "Volume met efficiency. {name} turned it into {pts:.1f}.",
]

QUIET_STAR = [
    "Nobody cleared 28. This one was won with a pile of midrange lines.",
    "No hero ball required — just enough 12-to-18-point outings to get over.",
    "Death by a thousand good-enough starts. Ugly? Effective.",
]

BLUNDER_INTROS = [
    "{team}'s start/sit desk had a long night:",
    "Lineup crime scene, {team}:",
    "{team} sat the production. The numbers, unblunted:",
    "Bench report, {team} — sit down:",
    "{team} is not a best-ball league. You can tell:",
    "If {team} had flipped one switch:",
    "{team}'s reserves filed a complaint in points:",
    "Classic Sunday problem for {team}:",
    "The flex call will live in infamy for {team}:",
    "{team} left meat on the bone:",
    "Start 'em/sit 'em autopsy, {team}:",
    "{team} discovered the bench can still score:",
]

BLUNDER_BULLETS = [
    "{bench} {bpts:.1f} unused while the {slot} ({starter}) finished at {spts:.1f} (+{delta:.1f}).",
    "Sat {bench} ({bpts:.1f}), started {starter} in the {slot} ({spts:.1f}). That's {delta:.1f} in the wrong column.",
    "Sit: {bench} {bpts:.1f}. Start: {starter} {spts:.1f} ({slot}). Delta {delta:.1f}, unforced.",
    "The {slot} belonged to {bench} ({bpts:.1f}), not {starter} ({spts:.1f}).",
    "+{delta:.1f} parked on the pine: {bench} instead of {starter} at {slot}.",
    "{starter} in the {slot} at {spts:.1f}; {bench} from the bench at {bpts:.1f}. Wrong door.",
    "Would-have-been league-winning swing: {bench} {bpts:.1f} over {starter} ({spts:.1f}, {slot}).",
    "{bench} went off ({bpts:.1f}) in street clothes. {starter} held the {slot} at {spts:.1f}.",
    "That's a fade that backfired: {bench} {bpts:.1f} vs {starter} {spts:.1f} ({slot}).",
    "Projection lost, points won — on the bench. {bench} {bpts:.1f}, {slot} {starter} {spts:.1f}.",
    "You start {starter} ({spts:.1f}) at {slot} and watch {bench} drop {bpts:.1f}. Brutal.",
    "Fill-in production: {bench} {bpts:.1f} should have been in, not {starter} ({spts:.1f}).",
    "The streamer on the bench won: {bench} {bpts:.1f} vs {starter}'s {spts:.1f} in the {slot}.",
    "That's a smashed sit. {bench} {bpts:.1f}; {starter} ghosted the {slot} with {spts:.1f}.",
    "Box-score watching hurts: {bench} {bpts:.1f} idle, {starter} {spts:.1f} active ({slot}).",
    "Matchup-based sit went cold. {starter} {spts:.1f} at {slot}, {bench} cooking for {bpts:.1f}.",
    "Trust-the-talent crowd wins this one: {bench} {bpts:.1f} > {starter} {spts:.1f} ({slot}).",
    "The backup plan scored like a WR1. {bench} {bpts:.1f} from the bench vs {starter} {spts:.1f}.",
    "{delta:.1f} points of stubbornness: {slot} stayed with {starter} ({spts:.1f}) over {bench}.",
    "If this were best ball, {bench} ({bpts:.1f}) is in and {starter} ({spts:.1f}) is a footnote.",
    "Sunday confirmation bias: leaving {bench} at {bpts:.1f} while {starter} posted {spts:.1f} ({slot}).",
    "The waiver add you already had: {bench} {bpts:.1f}, still behind {starter} ({spts:.1f}) on the card.",
    "Coaching decision, challenged, overturned by math: {bench} {bpts:.1f} / {starter} {spts:.1f} ({slot}).",
    "Not a smash spot for {starter} ({spts:.1f}). Was a smash sit for {bench} ({bpts:.1f}).",
]

NO_BLUNDER = [
    "{team}'s lineup had no real bench scandal. Rare, and earned.",
    "{team} nailed start/sit. Small miracle, big detail.",
    "Credit {team}: the starters were the right ones this time.",
]

DROP_LINES = [
    "{team} cut {player} — {points:.1f} points later, that's a meme.",
    "Drop regret, {team}: {player} just posted {points:.1f} in a new hoodie.",
    "{player} ({points:.1f}) spent the week scoring against the manager who dumped him ({team}).",
    "Waiver-wire boomerang: {team} said goodbye to {player}, hello to {points:.1f} on someone else's roster.",
    "{team} hit drop. {player} hit {points:.1f}. The wire remembers.",
]

TRADE_WIN = [
    "Trade tape, {team}: {delta:+.1f} this week. Sent {gave}, got {got}. Early returns: green.",
    "{team} is winning the swap on a seven-day sample ({delta:+.1f}): out {gave}, in {got}.",
    "First-week trade grade for {team}: plus-{delta:.1f}. {got} showed up; {gave} did not.",
]

TRADE_LOSS = [
    "Trade tape, {team}: {delta:+.1f} this week. Sent {gave}, got {got}. Early, already loud.",
    "{team} loses the first progress report ({delta:+.1f}): gone {gave}, here {got}.",
    "Buyer's remorse watch, {team}: {got} vs {gave}, net {delta:+.1f} on the week.",
]

NO_MOVES = [
    "No drop or trade boomerangs from last week. Either discipline, or it's still early.",
    "Waiver file is quiet. This week's pain came from lineups, not transactions.",
    "Nobody's ex just went nuclear. The drama was start/sit, not add/drop.",
]

CLOSERS = [
    "Tuesday is waiver day. Ignore the film, inherit the leftovers.",
    "Same league next week. New excuses.",
    "Standings updated. Ego is a self-serve station.",
    "That's a wrap on the broadcast. See you after the waivers process.",
    "Good night from the booth. Lock those lineups before kickoff this time.",
]

AVG_NOTE = [
    "This league also plays the league-average score, so one NFL week can yield two results.",
    "Median / league-average matchup is on: two checkmarks in the record, not one.",
]
