# Ligafunk — League Recap & Team Management Platform

Fantasy-Sport-Plattform für Sleeper-Ligen mit Recaps, Team-Verwaltung und Multi-Channel-Distribution.

## Features

- **Liga-Verwaltung**: Ligen mit unguessbarer URL erstellen und verwalten
- **Team-Authentifizierung**: Liga-Code + Team-Passwort
- **Recaps**: Wöchentliche Recaps pro Liga, öffentlich einsehbar
- **Distribution**: WhatsApp, Signal, Slack, Discord, Telegram, E-Mail, RSS
- **Fantasy-Sport Theme**: Modernes, responsives Design
- **Recap-Styles**: NFL-Reporter (EN) oder Football Bromance (DE), kombinierbar mit Saison-Themes

## Recap-Styles & Themes

Im Team-Dashboard lassen sich **Stil** und **Theme** frei kombinieren:

| Stil | Sprache | Ton |
|---|---|---|
| `nfl_reporter` | Englisch | Broadcast-/ESPN-Register, Box Score, Film Session |
| `bromance` | Deutsch | Lockerer Buddy-Trash-Talk, Memes, Bench-Roasts |

| Theme | Wirkung |
|---|---|
| `none` | Normaler Recap ohne saisonale Einfärbung |
| `christmas` | Bescherung, Wunschzettel, Christkind |
| `halloween` | Geisterstunde, Horror-Lines, Süßes oder Saures |
| `tight_end` | Tight-End-Spotlight, Streamer, TE-Notdienst |

Ein Theme überschreibt Opener, Closer und Abschnittsüberschriften und streut
eine thematische Zeile in die meisten Matchups. Jede Generierung nutzt ein
frisches Zufalls-Deck: derselbe Spieltag liest sich beim nächsten Mal anders.
Stil und Theme werden pro Recap gespeichert und auf der Recap-, Liga- und
History-Seite als Badge angezeigt.

## Quick Start

### Lokal (Development)

```powershell
cd Sleeper_Web
pip install -r requirements.txt
python app.py
```

Öffne http://localhost:5000 im Browser.

### Docker (Production)

```powershell
docker-compose up -d
```

## Konfiguration & Geheimnisse

Im Repository stehen **keine** Passwörter, Adressen oder E-Mail-Adressen.
Alles wird über Umgebungsvariablen gesetzt (lokal über eine `.env`-Datei,
siehe `.env.example`).

| Variable | Pflicht | Beschreibung |
|---|---|---|
| `SECRET_KEY` | Produktion | Zufälliger Schlüssel zum Signieren der Session-Cookies |
| `DATABASE_PATH` | Nein | Pfad zur SQLite-Datei (Render: `/tmp/sleeper_web.db`) |
| `ADMIN_PASSWORD_HASH` | Ja (Produktion) | Werkzeug-Hash des Admin-Passworts (empfohlen) |
| `ADMIN_PASSWORD` | Nur lokal | Klartext-Alternative für die Entwicklung |
| `IMPRESSUM_NAME` | Ja (Impressum) | Name des Anbieters |
| `IMPRESSUM_STREET` | Ja (Impressum) | Straße und Hausnummer |
| `IMPRESSUM_CITY` | Ja (Impressum) | PLZ und Ort |
| `IMPRESSUM_EMAIL` | Ja (Impressum) | Kontakt-E-Mail |

Admin-Hash erzeugen:

```powershell
python -c "from werkzeug.security import generate_password_hash; print(generate_password_hash('DEIN-PASSWORT'))"
```

Ist weder `ADMIN_PASSWORD_HASH` noch `ADMIN_PASSWORD` gesetzt, ist der
Admin-Login deaktiviert (HTTP 503). Es gibt keinen eingebauten Fallback.

## Admin-Panel

1. Öffne http://localhost:5000/admin
2. Erstelle eine Liga mit Name und Sleeper League ID
3. Füge Teams mit Passwort hinzu
4. (Optional) Webhooks für Slack/Discord/Telegram konfigurieren

## Liga-URL

Jede Liga erhält einen unguessbaren Code, z.B.:
`http://localhost:5000/l/ABC123XY`

Diese URL kann geteilt werden — ohne Login kann man Recaps lesen.

## Team-Login

1. Auf der Liga-Seite auf "Team Login" klicken
2. Team auswählen und Passwort eingeben
3. Zugriff auf privates Dashboard

## Distribution

| Kanal | Methode |
|---|---|
| WhatsApp | Offizieller wa.me-Link |
| Signal | Kopier-Button |
| Slack | Webhook-URL |
| Discord | Webhook-URL |
| Telegram | Bot Token + Chat ID |
| E-Mail | mailto: Link |
| RSS | /l/<code>/feed.rss |

## Projektstruktur

```
Sleeper_Web/
├── app.py              # Flask-Anwendung
├── config.py           # Konfiguration
├── database.py         # SQLite-Datenbank
├── templates/          # HTML-Templates
├── static/            # CSS/JS
├── requirements.txt   # Python-Abhängigkeiten
├── Dockerfile          # Docker-Image
├── docker-compose.yml  # Docker Compose
└── Start-Gui.bat      # Windows-Starter
```
