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
| `DATABASE_PATH` | Nein | Pfad zur SQLite-Datei (Render: `/tmp/sleeper_web.db`, wird per Litestream gesichert) |
| `LITESTREAM_BUCKET` | Produktion | Privater S3-kompatibler Bucket für die SQLite-Replikation |
| `LITESTREAM_ENDPOINT` | Produktion | S3-Endpoint, z. B. `https://<ACCOUNT_ID>.r2.cloudflarestorage.com` |
| `LITESTREAM_REGION` | Produktion | `auto` bei Cloudflare R2 |
| `LITESTREAM_REPLICA_PATH` | Produktion | Objektpfad im Bucket, z. B. `ligafunk/sleeper_web.db` |
| `LITESTREAM_ACCESS_KEY_ID` | Produktion | Zugangsschlüssel des Bucket-Tokens |
| `LITESTREAM_SECRET_ACCESS_KEY` | Produktion | Geheimer Schlüssel des Bucket-Tokens |
| `SESSION_LIFETIME_HOURS` | Nein | Team-Session-Lebensdauer (Standard: 24) |
| `ADMIN_SESSION_HOURS` | Nein | Admin-Session-Lebensdauer (Standard: 8) |
| `ADMIN_PASSWORD_HASH` | Ja (Produktion) | Werkzeug-Hash des Admin-Passworts (empfohlen) |
| `ADMIN_PASSWORD` | Nur lokal | Klartext-Alternative für die Entwicklung |
| `TRUSTED_HOSTS` | Nein | Kommagetrennte Host-Allowlist gegen Host-Header-Manipulation |
| `FORCE_HTTPS` | Nein | `1` erzwingt HTTPS/HSTS/Secure-Cookies auch ohne `RENDER` |
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
In Produktion (`RENDER` oder `FORCE_HTTPS=1`) wird ausschließlich
`ADMIN_PASSWORD_HASH` akzeptiert; `ADMIN_PASSWORD` wird dort ignoriert.

## Dauerhafte Datenhaltung auf Render Free

Render Free hat kein persistentes Dateisystem. `/tmp/sleeper_web.db` und
`backups/` werden bei jedem Deploy, Restart oder Spin-down gelöscht. Die App
repliziert die SQLite-DB deshalb mit **Litestream** in ein privates,
S3-kompatibles Bucket und stellt sie beim Start automatisch wieder her.

### Einrichtung

1. Privates Bucket bei Cloudflare R2 (oder Backblaze B2) anlegen.
2. API-Token nur für dieses Bucket mit „Object Read & Write“ erzeugen.
3. In Render diese Variablen setzen:
   `LITESTREAM_BUCKET`, `LITESTREAM_ENDPOINT`, `LITESTREAM_REGION`,
   `LITESTREAM_REPLICA_PATH`, `LITESTREAM_ACCESS_KEY_ID`,
   `LITESTREAM_SECRET_ACCESS_KEY`.
4. Deployen. Beim ersten Start ohne Replica legt die App eine leere DB an.
5. Vor dem Umbau ein JSON-Backup im Admin-Panel herunterladen und nach dem
   ersten Start importieren, damit die bisherigen Ligen erhalten bleiben.
6. Danach einen Restart testen und prüfen, ob Ligen, Teams und Recaps bleiben.

Der Build lädt Litestream v0.5.17 und prüft die SHA-256-Summe. Die
Startsequenz bricht mit einer klaren Fehlermeldung ab, wenn eine
`LITESTREAM_*`-Variable fehlt.

### Verhalten

- `restore-if-db-not-exists`: Litestream holt die DB nur zurück, wenn lokal
  keine Datei existiert.
- `sync-interval: 10s`: Bei einem harten Absturz können maximal ca. 10 Sekunden
  Änderungen verloren gehen. Bei einem normalen Shutdown synchronisiert
  Litestream vorher.
- **Eine Instanz:** SQLite + Litestream nicht auf mehrere Render-Instanzen
  skalieren. Für Multi-Instanz-Betrieb ist PostgreSQL die bessere Wahl.
- `backups/` bleibt ein zusätzlicher manueller JSON-Export. Die primäre
  Persistenz ist die Litestream-Replik.
- `data/` ist nur ein Sleeper-Cache und wird bei Bedarf neu geladen.

### Sicherheit

- Bucket privat lassen; keine öffentliche URL aktivieren.
- Token nur für diesen Bucket, keine Account-weiten Rechte.
- Zugangsdaten ausschließlich als Render-Environment-Variablen.
- Die DB enthält Passwort-Hashes und Webhook-URLs; Bucket und Token entsprechend
  schützen.
- Zusätzlich regelmäßig ein JSON-Backup herunterladen. Litestream schützt gegen
  Server-Neustarts, nicht gegen logische Fehler oder versehentliches Löschen.

## Admin-Panel

1. Öffne http://localhost:5000/admin
2. Erstelle eine Liga mit Name und Sleeper League ID
3. Füge Teams mit Passwort hinzu (mindestens 10 Zeichen)
4. (Optional) Webhooks für Slack/Discord/Telegram konfigurieren

Im Admin-Panel lassen sich Team-Passwörter zurücksetzen (dabei werden alle
aktiven Sessions des Teams sofort ungültig) und Webhooks wieder entfernen.

## Liga-URL

Jede Liga erhält einen unguessbaren 12-stelligen Code, z.B.:
`http://localhost:5000/l/ABC123XY9DEF`

Diese URL kann geteilt werden — ohne Login kann man Recaps lesen.

## Team-Login

1. Auf der Startseite den Liga-Code oder den kompletten Liga-Link eingeben
   (alternativ auf der Liga-Seite auf "Team Login" klicken)
2. Team auswählen und Passwort eingeben
3. Zugriff auf privates Dashboard

## Start/Sit und Waivers: Datenbasis

- **Liga-Snapshot:** Roster, Matchups und `last_scored_leg` werden höchstens
  15 Minuten wiederverwendet. Bei einer anderen Woche oder älterem Snapshot
  wird neu geladen.
- **Spieler/Verletzungen:** Der Sleeper-Spielerkatalog wird für Empfehlungen
  höchstens 3 Stunden gecacht.
- **Vergangene Wochen:** Für alle abgeschlossenen Wochen (`1` bis
  `last_scored_leg`) werden die Sleeper-Stats geladen und mit den
  ligaspezifischen Scoring-Regeln verrechnet. Zusätzlich werden die Wochen
  15–18 der Vorsaison für den Vergleich gegen den kommenden Gegner genutzt.
- **Kommende Woche:** Sleeper-Projektionen (6 Stunden Cache),
  NFL-Spielplan, Open-Meteo-Wetter (6 Stunden) und – falls verfügbar –
  Vegas-Implied-Totals (3 Stunden).
- **Form:** nach Wochen gewichteter Durchschnitt der Saisonleistung (spätere
  Wochen zählen mehr); die letzte gespielte Woche wird mit den tatsächlichen
  Matchup-Punkten überschrieben.
- **Empfehlung:** 50 % Projektion, 25 % Form, 15 % Leistung gegen den kommenden
  Gegner. Fehlende Quellen werden neu gewichtet. Danach folgen Multiplikatoren
  für Gegnerstärke gegen die Position, Wetter, Rest/Travel, Verletzung/Practice
  und Vegas. OUT/IR/Bye sind nicht startbar; die Aufstellung wird nach Slots
  und Empfehlung optimiert.
- **Waivers:** Derselbe Score für Roster und Free Agents, ergänzt um
  Trending-Adds und Depth-Chart-Backups für verletzte/questionable Spieler.
  Der schwächste passende Drop wird gesucht; FAAB-Gebote richten sich nach
  Upgrade, Restbudget, Wochen bis zu den Playoffs und Handcuff-Bedarf.

## Sicherheit

- Team-Passwörter werden mit **scrypt** gespeichert. Alte SHA-256-Hashes werden
  beim nächsten erfolgreichen Login automatisch aktualisiert.
- Admin- und Team-Sessions schließen sich gegenseitig aus: eine Anmeldung in
  der jeweils anderen Rolle löscht die vorherige Session serverseitig.
- Login-Versuche sind auf 10 Fehlversuche pro 15 Minuten und IP begrenzt
  (Admin und Team getrennt).
- Admin-Sessions laufen nach 8 Stunden ab; Team-Sessions nach 24 Stunden.
- Webhooks akzeptieren nur HTTPS-Endpunkte der offiziellen Anbieter
  (`hooks.slack.com`, `discord.com`, `api.telegram.org`), ohne Redirects,
  Zugangsdaten oder abweichende Ports. Das verhindert SSRF.
- Backup-Downloads sind auf exakt die vom System erzeugten Dateinamen
  beschränkt; private Seiten senden `Cache-Control: no-store`.
- Produktion: HTTPS-Erzwingung, HSTS, `Secure`/`HttpOnly`/`SameSite=Lax`-Cookies
  und das `__Host-`-Cookie-Präfix. Die CSP verbietet Inline-JavaScript und
  Inline-Styles.
- Backups enthalten Passwort-Hashes. Nur selbst erzeugte Backup-Dateien
  importieren und die Download-Verzeichnisse nicht öffentlich freigeben.

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
├── litestream.yml      # SQLite-Replikation nach R2/B2
├── templates/          # HTML-Templates
├── static/            # CSS/JS
├── requirements.txt   # Python-Abhängigkeiten
├── Dockerfile          # Docker-Image
├── docker-compose.yml  # Docker Compose
└── Start-Gui.bat      # Windows-Starter
```
