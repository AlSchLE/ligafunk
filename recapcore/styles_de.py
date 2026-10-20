"""German "Football Bromance" recap decks.

Same placeholder contract as copy_bank.py. Drawn without replacement inside
one recap so a single issue never repeats a phrase.
"""

from __future__ import annotations

BROMANCE_OPENERS = [
    "Week {week} ist durch. Die Box Scores sind final, die WhatsApp-Gruppe leider nicht.",
    "Sonntag vorbei, Waiver Wire lädt, und irgendwer hat wieder den falschen Flex gestartet.",
    "Week {week}: acht Manager, ein Scoreboard, null Ausreden bis Dienstag.",
    "Von der Early Window bis zum Sunday Night Game — so lief Week {week} wirklich.",
    "Kein TV-Truck, kein roter Teppich. Trotzdem wird hier jede Line gescoutet.",
    "Pads aus, Punkte bleiben. Willkommen zur Week-{week}-Film-Session.",
    "Fantasy Football, ungefiltert. Week {week}, ohne Welpenschutz.",
    "Falls du die Notifications stumm hattest: Das hast du in Week {week} verpasst.",
    "Noch ein Sonntag fürs Archiv. Ab zum Film, dann zur Tabelle.",
    "Week {week}: Smash Spots, Busts und mindestens ein Lineup fürs Museum.",
    "Der Sonntag ist vorbei, die Ausreden sind noch warm. Week {week} im Rückblick.",
    "Setz dich, Bruder. Week {week} hat ein paar Sachen zu besprechen.",
]

BROMANCE_MATCH_LEADS = [
    "{away} gegen {home} — beide im selben Scoreboard, nur einer geht glücklich raus.",
    "Nächstes Spiel: {away} vs. {home}. Der Box Score redet, nicht wir.",
    "{home} gegen {away}. Auf Papier ein Spiel, in der App ein Urteil.",
    "Matchup-Check: {away} gegen {home}, Bye-Week-Ausreden diesmal nicht inklusive.",
    "{away} wollte ein Shootout. {home} wollte den Win. Die Zahlen haben entschieden.",
    "Lineup setzen, damit leben: {away} vs. {home}.",
    "Ein voller Slate später haben {away} und {home} endlich ein Ergebnis.",
    "Dieses Spiel war markiert. {away} vs. {home} ist kein Gedankenspiel mehr.",
    "{away} kam mit Plan. {home} kam mit Punkten.",
    "Fantasy-Version der 1-Uhr-Window: {away} und {home}, ohne Werbepause.",
    "Die Projektionen hatten Meinungen. {away} und {home} hatten echtes Football.",
    "Nenn es Messlatte, wenn du willst. {away} vs. {home} musste trotzdem gespielt werden.",
]

BROMANCE_BLOWOUT = [
    "{margin:.0f} Punkte Differenz. Das ist kein Spiel, das ist eine Ansage.",
    "{winner} legt {wp:.1f} auf den Tisch und schickt {loser} früh nach Hause. Differenz: {margin:.1f}.",
    "Blowout. {loser} hat die Red Zone dieses Matchups nie gesehen.",
    "{winner} stellt {wp:.1f} hin; {loser} antwortet mit {lp:.1f}. Drop the Mic.",
    "Das war durch, bevor die Late Window lief. {winner} mit {margin:.1f}.",
    "{loser} sucht noch den Reset-Knopf. {winner} braucht keinen.",
    "Wäre das NFL, wären die Backups längst drin. Differenz: {margin:.1f}.",
    "{winner} im Cruise Control. {loser} hat eine teure Lektion gekauft.",
    "Gabel rein: {winner} mit {margin:.1f}, {loser} sucht den Puls.",
    "{winner} smash. {loser} bust. Die Tabelle wird sich erinnern.",
    "Kein Comeback-Skript im Ordner. {winner} macht die Tür zu.",
    "League-Winning-Vibe bei {winner}. League-Hurting-Nachmittag für {loser}.",
    "Die Art Score, bei der man die App weglegt. {winner} +{margin:.1f}.",
    "Garbage-Time-Punkte retten {loser} nicht. {winner} hat längst kassiert.",
]

BROMANCE_CLOSE = [
    "{margin:.2f} Punkte. Das ist ein Drop, ein Extra Point, ein falscher Flex.",
    "One-Possession-Game, Fantasy-Edition: {winner} {wp:.2f}, {loser} {lp:.2f}.",
    "Photo Finish. Der Replay Booth spricht {winner} den Sieg zu: {margin:.2f}.",
    "{loser} hatte das am Faden, bis der letzte Score reinkroch. {margin:.2f} zu wenig.",
    "Montagmorgen-Sodbrennen. {winner} stolpert über die Goal Line.",
    "Startet {loser} den Bench-Piece, ist der Chat heute anders drauf.",
    "{margin:.2f} Punkte — ein NFL-Field-Goal, eine Fantasy-Freundschaft auf Bewährung.",
    "Walk-Off-Energie. {winner} überlebt, {loser} starrt auf die Bank.",
    "Zu eng für Trash Talk. Gerade genug für ein W: {winner} mit {margin:.2f}.",
    "Deshalb checkt man nicht um 13:05 aus. {winner} brauchte jeden Restpunkt.",
    "Coin-Flip-Matchup, hässliche Landung: {winner} vor {loser} um einen Rundungsfehler.",
    "Start/Sit hat das mehr entschieden als die Skill Player. {margin:.2f} für {winner}.",
]

BROMANCE_SOLID = [
    "Arbeitsieg: {winner} {wp:.1f}, {loser} {lp:.1f}, Abstand {margin:.1f}.",
    "{winner} kontrolliert das Matchup, ohne eine Show draus zu machen. {loser} hinten dran: {margin:.1f}.",
    "Kein Blowout, kein Thriller — {winner} erledigt den Job, {loser} findet kein Boom-Game.",
    "{wp:.1f} zu {lp:.1f}. {winner} gewinnt die Mitte des Box Scores.",
    "Business Trip. {winner} nimmt, was der Slate gibt; {loser} lässt zu viel auf der Bank.",
    "Differenz {margin:.1f}: genug zum Gewinnen, nicht genug für den Screenshot. {winner} nimmt's.",
    "{loser} hängt dran, bis die Bench-Punkte nicht kommen. {winner} casht trotzdem.",
    "Die Art Win, die in Week {week} ein Record baut. Leise, dann offiziell.",
    "Floor over Ceiling. {winner} war einfach konstanter als {loser}.",
    "Keine League-Winning-Explosion nötig. {winner} stapelt genug solide Lines.",
    "{winner} gewinnt das Start/Sit-Duell genauso wie das Talent-Duell.",
    "Ein verträgliches {margin:.1f}-Polster. {loser} war drin, aber nie vorne.",
]

BROMANCE_STAR = [
    "Player of the Matchup: {name} mit {pts:.1f} — das Tape hatte eine Hauptrolle.",
    "{name} ({pts:.1f}) trägt den Stack. Alle anderen sind Supporting Cast.",
    "Boom Alert: {name} mit {pts:.1f} aus der Startformation.",
    "Stat of the Night: {name}, {pts:.1f}. Der Rest ist Kontext.",
    "{name} legt {pts:.1f} hin und macht die Projektion lächerlich.",
    "Ceiling unlocked: {name}, {pts:.1f}. Das ist eine Smash Week.",
    "Ohne {name} ({pts:.1f}) ist dieser Box Score ein anderer Film.",
    "{name} geht nuklear ({pts:.1f}). Set-and-forget hat die Rechnung bezahlt.",
    "Die beste Line des Slates in diesem Spiel: {name}, {pts:.1f}, keine Entschuldigung.",
    "Volume trifft Efficiency. {name} macht {pts:.1f} daraus.",
]

BROMANCE_QUIET = [
    "Niemand über 28. Das hier wurde mit einem Haufen Midrange-Lines gewonnen.",
    "Keine Hero Ball nötig — nur genug 12-bis-18-Punkte-Auftritte.",
    "Tod durch tausend gute-genug Starts. Hässlich? Effektiv.",
    "Kein Ausreißer nach oben, kein Totalausfall. Kollektives Schulterzucken.",
]

BROMANCE_BLUNDER_INTROS = [
    "{team}s Start/Sit-Desk hatte eine lange Nacht:",
    "Lineup-Tatort, {team}:",
    "{team} hat die Produktion gesetzt. Die Zahlen, ungeschönt:",
    "Bench Report, {team} — setz dich:",
    "{team} ist keine Best-Ball-Liga. Merkt man:",
    "Hätte {team} einen Schalter umgelegt:",
    "{team}s Reserve hat Beschwerde in Punkten eingereicht:",
    "Klassisches Sonntagsproblem bei {team}:",
    "Der Flex-Call wird bei {team} in die Geschichte eingehen:",
    "{team} hat Fleisch am Knochen gelassen:",
    "Start-'em/Sit-'em-Autopsie, {team}:",
    "{team} hat entdeckt, dass die Bank auch scoren kann:",
]

BROMANCE_BLUNDER_BULLETS = [
    "{bench} {bpts:.1f} ungenutzt, während der {slot} ({starter}) bei {spts:.1f} landet (+{delta:.1f}).",
    "{bench} ({bpts:.1f}) gesetzt, {starter} im {slot} gestartet ({spts:.1f}). Das sind {delta:.1f} in der falschen Spalte.",
    "Sit: {bench} {bpts:.1f}. Start: {starter} {spts:.1f} ({slot}). Delta {delta:.1f}, unerzwungen.",
    "Der {slot} gehörte {bench} ({bpts:.1f}), nicht {starter} ({spts:.1f}).",
    "+{delta:.1f} auf der Bank geparkt: {bench} statt {starter} im {slot}.",
    "{starter} im {slot} bei {spts:.1f}; {bench} von der Bank bei {bpts:.1f}. Falsche Tür.",
    "Wäre-League-Winning-Swing: {bench} {bpts:.1f} über {starter} ({spts:.1f}, {slot}).",
    "{bench} geht steil ({bpts:.1f}) in Straßenklamotten. {starter} hält den {slot} bei {spts:.1f}.",
    "Das ist ein Fade, der zurückgefeuert hat: {bench} {bpts:.1f} vs. {starter} {spts:.1f} ({slot}).",
    "Projektion verloren, Punkte gewonnen — auf der Bank. {bench} {bpts:.1f}, {slot} {starter} {spts:.1f}.",
    "Du startest {starter} ({spts:.1f}) im {slot} und schaust zu, wie {bench} {bpts:.1f} droppt. Brutal.",
    "Fill-in-Produktion: {bench} {bpts:.1f} hätte rein gehört, nicht {starter} ({spts:.1f}).",
    "Der Streamer auf der Bank hat gewonnen: {bench} {bpts:.1f} vs. {starter} {spts:.1f} im {slot}.",
    "Das ist ein smashed Sit. {bench} {bpts:.1f}; {starter} ghostet den {slot} mit {spts:.1f}.",
    "Box-Score-Watching tut weh: {bench} {bpts:.1f} idle, {starter} {spts:.1f} aktiv ({slot}).",
    "Matchup-basierter Sit ging kalt. {starter} {spts:.1f} im {slot}, {bench} kocht für {bpts:.1f}.",
    "Trust-the-Talent gewinnt hier: {bench} {bpts:.1f} > {starter} {spts:.1f} ({slot}).",
    "Der Backup-Plan scored wie ein WR1. {bench} {bpts:.1f} von der Bank vs. {starter} {spts:.1f}.",
    "{delta:.1f} Punkte Sturheit: {slot} blieb bei {starter} ({spts:.1f}) statt {bench}.",
    "Wäre das Best Ball, wäre {bench} ({bpts:.1f}) drin und {starter} ({spts:.1f}) eine Fußnote.",
    "Sunday Confirmation Bias: {bench} bei {bpts:.1f} gelassen, während {starter} {spts:.1f} postet ({slot}).",
    "Der Waiver-Add, den du schon hattest: {bench} {bpts:.1f}, trotzdem hinter {starter} ({spts:.1f}) auf dem Zettel.",
    "Coaching-Entscheidung, angefochten, durch Mathe kassiert: {bench} {bpts:.1f} / {starter} {spts:.1f} ({slot}).",
    "Kein Smash Spot für {starter} ({spts:.1f}). Dafür ein Smash Sit für {bench} ({bpts:.1f}).",
]

BROMANCE_NO_BLUNDER = [
    "{team}s Lineup hatte keinen echten Bank-Skandal. Selten und verdient.",
    "{team} hat Start/Sit getroffen. Kleines Wunder, großes Detail.",
    "Credit {team}: Die Starter waren diesmal die richtigen.",
    "{team}: keine Bench-Punkte verschwendet. Notiz für die Chronik.",
]

BROMANCE_DROP = [
    "{team} hat {player} gecuttet — {points:.1f} Punkte später ist das ein Meme.",
    "Drop-Regret, {team}: {player} postet gerade {points:.1f} im neuen Hoodie.",
    "{player} ({points:.1f}) hat die Woche gegen den Manager gescort, der ihn gedumpt hat ({team}).",
    "Waiver-Wire-Boomerang: {team} sagt Tschüss zu {player}, hallo zu {points:.1f} auf einem anderen Roster.",
    "{team} drückt Drop. {player} drückt {points:.1f}. Die Wire erinnert sich.",
]

BROMANCE_TRADE_WIN = [
    "Trade-Tape, {team}: {delta:+.1f} diese Woche. {gave} raus, {got} rein. Erste Bilanz: grün.",
    "{team} gewinnt den Swap auf Sieben-Tage-Sample ({delta:+.1f}): out {gave}, in {got}.",
    "Erste Trade-Note für {team}: plus {delta:.1f}. {got} war da; {gave} nicht.",
]

BROMANCE_TRADE_LOSS = [
    "Trade-Tape, {team}: {delta:+.1f} diese Woche. {gave} raus, {got} rein. Früh, aber laut.",
    "{team} verliert den ersten Fortschrittsbericht ({delta:+.1f}): weg {gave}, hier {got}.",
    "Buyer's-Remorse-Watch, {team}: {got} gegen {gave}, netto {delta:+.1f} auf die Woche.",
]

BROMANCE_NO_MOVES = [
    "Keine Drop- oder Trade-Boomerangs aus der letzten Woche. Entweder Disziplin oder noch zu früh.",
    "Waiver-Akte ist ruhig. Der Schmerz dieser Woche kam aus den Lineups, nicht aus Transaktionen.",
    "Niemandes Ex ist gerade nuklear gegangen. Das Drama war Start/Sit, nicht Add/Drop.",
]

BROMANCE_CLOSERS = [
    "Dienstag ist Waiver-Tag. Ignorier den Film, erbe die Reste.",
    "Nächste Woche, gleiche Liga. Neue Ausreden.",
    "Tabelle aktualisiert. Ego ist Selbstbedienung.",
    "Das war's aus der Booth. Bis nach der Waiver-Auswertung.",
    "Gute Nacht aus dem Studio. Diesmal Lineups vor Kickoff locken.",
]

BROMANCE_BYE = [
    "{name} hat Bye ({pts:.2f}). Kein Gegner, aber auch kein Freifahrtschein.",
    "Bye Week für {name} ({pts:.2f}). Die Bank darf sich ausruhen.",
]

BROMANCE_NO_MATCHUPS = [
    "Für diese Woche gibt es noch keine Matchup-Daten. Entweder Bye oder der Slate lädt noch.",
]
