"""Seasonal theme overlays for recaps.

A theme works with every voice: it overrides the opener/closer decks and the
section headings, and adds one flavour line to most matchups. All copy is
German, independent of the selected voice.
"""

from __future__ import annotations

THEME_DECKS: dict[str, dict] = {
    "none": {},
    "christmas": {
        "openers": [
            "Week {week} unterm Baum: Die Geschenke sind verteilt, die Rechnungen kommen später.",
            "Ho ho ho, Week {week} ist verpackt. Wer hat sich dieses Jahr selbst beschenkt?",
            "Adventskranz, Glühwein, Box Score: Week {week} im Festtagsmodus.",
            "Das Christkind war offenbar Waiver-Wire-Fan. Week {week} ist durch.",
            "Alle Jahre wieder: Week {week}, nur mit mehr Bench-Punkten und weniger Besinnlichkeit.",
            "Der 24. ist vorbei, die Tabelle bleibt. Week {week} im Feiertagsrückblick.",
        ],
        "closers": [
            "Frohe Feiertage. Möge dein Kader nächstes Jahr weniger Kohle enthalten.",
            "Bis zum nächsten Spieltag: Geschenke auspacken, Lineups checken.",
            "Stille Nacht, laute Tabelle. Bis nach den Feiertagen.",
            "Das war die Bescherung. Die Retoure kommt nächste Woche.",
            "Merrily we roll along — direkt in die Waiver-Woche.",
        ],
        "headings": {
            "matchups": "### Bescherung",
            "transactions": "### Wunschzettel & Tauschgeschäfte",
            "standings": "### Geschenkliste",
        },
        "flavor": [
            "Unterm Baum lag diesmal: {margin:.1f} Punkte Differenz.",
            "Das Christkind hat {winner} beschenkt und {loser} die Rechnung geschickt.",
            "Kein Geschenk für {loser}. {winner} hat seins schon ausgepackt.",
            "Diese Line gehört in die Krippe: {name} mit {pts:.1f}.",
            "Geschenkpapier war teurer als {loser}s Output. {winner} nimmt das Päckchen.",
            "Ho ho ho — {winner} verteilt {wp:.1f}, {loser} bekommt Kohle.",
            "Adventskalender, Türchen {week}: {winner} mit {margin:.1f} Vorsprung.",
            "Stille Nacht war das hier nicht. {winner} und {loser} haben Lärm gemacht.",
        ],
    },
    "halloween": {
        "openers": [
            "Week {week} war ein Horrorfilm. Und du warst nicht der Held.",
            "Kürbisse, Nebel, Bench Points — Halloween in der Liga.",
            "Süßes oder Saures: Week {week} hat beides geliefert.",
            "Die Geisterstunde ist vorbei, der Box Score bleibt.",
            "Willkommen zum Gruselkabinett, Week {week}. Bitte nicht schreien.",
            "Kerzen an, Bildschirm an: Week {week} hat Gruselpotenzial.",
        ],
        "closers": [
            "Bis nächste Woche. Dann wieder: Süßes oder Saures.",
            "Die Nacht ist vorbei. Die Tabelle spukt weiter.",
            "Schlaf gut. Falls du kannst.",
            "Kerzen aus, Bildschirm aus. Das Monster bleibt in der Tabelle.",
            "Bis zum nächsten Schocker. Dein Lineup ist schon bereit.",
        ],
        "headings": {
            "matchups": "### Die Geisterstunde",
            "transactions": "### Süßes oder Saures",
            "standings": "### Friedhof der Egos",
        },
        "flavor": [
            "Das war ein echter Schocker: {margin:.1f} Punkte.",
            "{loser} hat sich im Nebel verlaufen. {winner} kennt den Weg.",
            "Horror-Line: {name} mit {pts:.1f}. Gänsehaut.",
            "Aus dem Grab geholt: {winner} mit {wp:.1f}.",
            "Süßes gab's für {winner}, Saures für {loser}.",
            "Die Geisterstunde hat {loser} erwischt. {winner} überlebt mit {margin:.1f}.",
            "Im Kürbis leuchtet nur eins: {winner}.",
            "Monster-Margin: {margin:.1f}. {loser} sollte unter dem Bett nachschauen.",
        ],
    },
    "tight_end": {
        "openers": [
            "Week {week}: Tight End Week. Die Position, die deine Saison entweder rettet oder memet.",
            "TEs überall. Manche blocken, manche fangen, deiner macht wahrscheinlich nichts davon.",
            "Tight End Week, Week {week}: Wo 4 Punkte schon als Ausbruch gelten.",
            "Willkommen zur Tight-End-Week. Bitte keine Tränen auf dem Tape.",
            "Week {week} gehört den Tight Ends. Zumindest theoretisch.",
            "Alle Augen auf die TE-Position. Alle Augen rollen kurz darauf.",
        ],
        "closers": [
            "Bis nächste Woche. Und ja, dein TE ist immer noch ein Problem.",
            "Tight End Week vorbei. Die Trauer bleibt.",
            "Nächste Woche ohne TE-Ausrede. Versprochen.",
            "Die TE-Ära endet hier. Bis zum nächsten Streamer.",
            "Gute Nacht. Irgendwo blockt ein Tight End immer noch.",
        ],
        "headings": {
            "matchups": "### Die TE-Felder",
            "transactions": "### Waiver-TE-Notdienst",
            "standings": "### Tabelle der Enttäuschten",
        },
        "flavor": [
            "Tight End Spotlight: {te_name} mit {te_pts:.1f}. Ja, wirklich.",
            "Dein TE hat {te_pts:.1f} gepunktet. {winner} kann es kaum glauben.",
            "Tight End Week und {loser} hat den falschen gestartet. Klassiker.",
            "Blocking? Ja. Fantasy-Punkte? Naja. {winner} nimmt den Sieg trotzdem.",
            "{winner} gewinnt mit {margin:.1f} — und irgendwo weint ein gestreamter TE.",
            "Die TE-Position bleibt ein Minenfeld. {loser} ist reingelaufen.",
            "Kein Elite-TE, kein Problem: {winner} mit {wp:.1f}.",
            "TE-Notdienst für {loser}. {winner} hatte den besseren Streamer.",
        ],
    },
}
