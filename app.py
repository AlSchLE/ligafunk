"""
Sleeper Web — League recap and team management platform.

Run:  python app.py            (dev, port 5000)
      gunicorn -w 2 -b 0.0.0.0:5000 app:app   (prod)
"""
import os
import json
import hashlib
import secrets
import requests
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (Flask, render_template, request, redirect,
                   url_for, flash, abort, jsonify, send_file,
                   make_response, session as flask_session)
import subprocess
import tempfile
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sleeper-web")

# Auto-backup directory
BACKUP_DIR = Path(__file__).resolve().parent / "backups"
BACKUP_DIR.mkdir(exist_ok=True)
MAX_BACKUPS = 10  # Keep only last 10 backups


def _auto_backup():
    """Create a JSON backup of all data."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = BACKUP_DIR / f"sleeper_backup_{timestamp}.json"

    conn = get_db()
    leagues = conn.execute("SELECT * FROM leagues").fetchall()
    teams = conn.execute("SELECT * FROM teams").fetchall()
    recaps = conn.execute("SELECT * FROM recaps").fetchall()
    webhooks = conn.execute("SELECT * FROM webhooks").fetchall()
    conn.close()

    data = {
        "leagues": [dict(l) for l in leagues],
        "teams": [dict(t) for t in teams],
        "recaps": [dict(r) for r in recaps],
        "webhooks": [dict(w) for w in webhooks],
        "timestamp": datetime.now().isoformat(),
    }

    with open(backup_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)

    # Cleanup old backups
    backups = sorted(BACKUP_DIR.glob("sleeper_backup_*.json"))
    while len(backups) > MAX_BACKUPS:
        backups[0].unlink()
        backups = backups[1:]

    log.info("Auto-backup created: %s", backup_file.name)

from config import DATABASE_PATH, SECRET_KEY, SESSION_LIFETIME_HOURS
from database import (get_db, init_db, hash_password, verify_password,
                      generate_league_code, create_session,
                      get_session_team, delete_session)

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["SESSION_LIFETIME"] = timedelta(hours=SESSION_LIFETIME_HOURS)


# ============================================================
# Authentication
# ============================================================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        team = get_session_team(flask_session.get("session_id", ""))
        if not team:
            flash("Please log in.")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


@app.context_processor
def inject_team():
    team = get_session_team(flask_session.get("session_id", ""))
    return {"current_team": team}


# ============================================================
# Home / Admin
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/admin/backup")
def backup():
    """Export all data as JSON."""
    conn = get_db()
    leagues = conn.execute("SELECT * FROM leagues").fetchall()
    teams = conn.execute("SELECT * FROM teams").fetchall()
    recaps = conn.execute("SELECT * FROM recaps").fetchall()
    webhooks = conn.execute("SELECT * FROM webhooks").fetchall()
    conn.close()

    data = {
        "leagues": [dict(l) for l in leagues],
        "teams": [dict(t) for t in teams],
        "recaps": [dict(r) for r in recaps],
        "webhooks": [dict(w) for w in webhooks],
    }
    return jsonify(data)


@app.route("/admin/import", methods=["POST"])
def import_backup():
    """Import data from JSON backup."""
    if "backup_file" not in request.files:
        flash("No file uploaded.")
        return redirect(url_for("admin"))
    file = request.files["backup_file"]
    if not file.filename.endswith(".json"):
        flash("Please upload a .json file.")
        return redirect(url_for("admin"))
    try:
        data = json.loads(file.read().decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        flash("Invalid JSON file.")
        return redirect(url_for("admin"))

    conn = get_db()
    cursor = conn.cursor()

    # Import leagues
    for l in data.get("leagues", []):
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO leagues (id, name, sleeper_league_id, league_code) VALUES (?, ?, ?, ?)",
                (l["id"], l["name"], l["sleeper_league_id"], l["league_code"])
            )
        except Exception as e:
            log.warning("Import league failed: %s", e)

    # Import teams
    for t in data.get("teams", []):
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO teams (id, league_id, name, owner_name, sleeper_roster_id, password_hash) VALUES (?, ?, ?, ?, ?, ?)",
                (t["id"], t["league_id"], t["name"], t["owner_name"], t.get("sleeper_roster_id"), t["password_hash"])
            )
        except Exception as e:
            log.warning("Import team failed: %s", e)

    # Import recaps
    for r in data.get("recaps", []):
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO recaps (id, league_id, week, content, created_by) VALUES (?, ?, ?, ?, ?)",
                (r["id"], r["league_id"], r["week"], r["content"], r.get("created_by"))
            )
        except Exception as e:
            log.warning("Import recap failed: %s", e)

    # Import webhooks
    for w in data.get("webhooks", []):
        try:
            cursor.execute(
                "INSERT OR IGNORE INTO webhooks (id, league_id, webhook_type, webhook_url) VALUES (?, ?, ?, ?)",
                (w["id"], w["league_id"], w["webhook_type"], w["webhook_url"])
            )
        except Exception as e:
            log.warning("Import webhook failed: %s", e)

    conn.commit()
    conn.close()
    flash("Backup imported successfully.")
    return redirect(url_for("admin"))


@app.route("/admin/download-backup/<filename>")
def download_backup(filename):
    """Download a backup file."""
    from flask import safe_join
    safe_path = safe_join(str(BACKUP_DIR), filename)
    if not safe_path or not os.path.isfile(safe_path):
        abort(404)
    return send_file(safe_path, as_attachment=True)


@app.route("/admin", methods=["GET", "POST"])
def admin():
    if request.method == "POST":
        action = request.form.get("action")

        if action == "add_league":
            name = request.form.get("name", "").strip()
            sleeper_league_id = request.form.get("sleeper_league_id", "").strip()
            if not name or not sleeper_league_id:
                flash("League name and Sleeper ID are required.")
                return redirect(url_for("admin"))
            conn = get_db()
            code = generate_league_code()
            conn.execute(
                "INSERT INTO leagues (name, sleeper_league_id, league_code) VALUES (?, ?, ?)",
                (name, sleeper_league_id, code)
            )
            conn.commit()
            conn.close()
            _auto_backup()
            flash(f"League '{name}' created with code {code}")
            return redirect(url_for("admin"))

        elif action == "add_team":
            league_id = int(request.form.get("league_id"))
            name = request.form.get("name", "").strip()
            owner_name = request.form.get("owner_name", "").strip()
            sleeper_roster_id = request.form.get("sleeper_roster_id", "").strip()
            password = request.form.get("password", "")
            if not name or not owner_name or not password:
                flash("Team name, owner name, and password are required.")
                return redirect(url_for("admin"))
            conn = get_db()
            conn.execute(
                "INSERT INTO teams (league_id, name, owner_name, sleeper_roster_id, password_hash) VALUES (?, ?, ?, ?, ?)",
                (league_id, name, owner_name, sleeper_roster_id or None, hash_password(password))
            )
            conn.commit()
            conn.close()
            _auto_backup()
            flash(f"Team '{name}' added")
            return redirect(url_for("admin"))

        elif action == "add_webhook":
            league_id = int(request.form.get("league_id"))
            webhook_url = request.form.get("webhook_url", "").strip()
            webhook_type = request.form.get("webhook_type", "").strip()
            if not webhook_url or not webhook_type:
                flash("Webhook URL and type are required.")
                return redirect(url_for("admin"))
            conn = get_db()
            conn.execute(
                "INSERT OR REPLACE INTO webhooks (league_id, webhook_type, webhook_url) VALUES (?, ?, ?)",
                (league_id, webhook_type, webhook_url)
            )
            conn.commit()
            conn.close()
            flash("Webhook added")
            return redirect(url_for("admin"))

    conn = get_db()
    leagues = conn.execute("SELECT * FROM leagues ORDER BY created_at DESC").fetchall()
    webhooks = conn.execute("""
        SELECT w.*, l.name as league_name FROM webhooks w
        JOIN leagues l ON w.league_id = l.id
    """).fetchall()
    backups = sorted(BACKUP_DIR.glob("sleeper_backup_*.json"), reverse=True)[:10]
    conn.close()
    return render_template("admin.html", leagues=leagues, webhooks=webhooks, backups=backups)


# ============================================================
# League Public Page (unguessable URL)
# ============================================================

@app.route("/l/<league_code>")
def league_public(league_code):
    """Public league page — no auth required."""
    conn = get_db()
    league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
    if not league:
        abort(404)

    teams = conn.execute("SELECT * FROM teams WHERE league_id = ?", (league["id"],)).fetchall()
    recaps = conn.execute("""
        SELECT r.*, t.name as team_name FROM recaps r
        LEFT JOIN teams t ON r.created_by = t.id
        WHERE r.league_id = ? ORDER BY r.created_at DESC
    """, (league["id"],)).fetchall()
    webhooks = conn.execute("SELECT * FROM webhooks WHERE league_id = ?", (league["id"],)).fetchall()
    conn.close()

    return render_template("league.html", league=league, teams=teams,
                           recaps=recaps, webhooks=webhooks)


@app.route("/l/<league_code>/recap/<int:recap_id>")
def league_recap(league_code, recap_id):
    """Public recap page."""
    conn = get_db()
    league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
    if not league:
        abort(404)
    recap = conn.execute("SELECT * FROM recaps WHERE id = ? AND league_id = ?",
                         (recap_id, league["id"])).fetchone()
    conn.close()
    if not recap:
        abort(404)
    return render_template("recap.html", league=league, recap=recap)


@app.route("/l/<league_code>/history")
def league_history(league_code):
    """Public recap history."""
    conn = get_db()
    league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
    if not league:
        abort(404)
    recaps = conn.execute("""
        SELECT r.*, t.name as team_name FROM recaps r
        LEFT JOIN teams t ON r.created_by = t.id
        WHERE r.league_id = ? ORDER BY r.week DESC, r.created_at DESC
    """, (league["id"],)).fetchall()
    conn.close()
    return render_template("history.html", league=league, recaps=recaps)


# ============================================================
# Login / Logout
# ============================================================

@app.route("/l/<league_code>/login", methods=["GET", "POST"])
def login(league_code):
    conn = get_db()
    league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
    if not league:
        abort(404)
    teams = conn.execute("SELECT * FROM teams WHERE league_id = ?", (league["id"],)).fetchall()
    conn.close()

    if request.method == "POST":
        team_id = int(request.form.get("team_id"))
        password = request.form.get("password", "")

        team = next((t for t in teams if t["id"] == team_id), None)
        if not team or not verify_password(password, team["password_hash"]):
            flash("Invalid password.")
            return redirect(url_for("login", league_code=league_code))

        flask_session["session_id"] = create_session(team_id)
        return redirect(url_for("team_dashboard", league_code=league_code))

    return render_template("login.html", league=league, teams=teams)


@app.route("/l/<league_code>/logout")
def logout(league_code):
    flask_session.pop("session_id", None)
    flash("Logged out.")
    return redirect(url_for("league_public", league_code=league_code))


# ============================================================
# Team Dashboard (private)
# ============================================================

@app.route("/l/<league_code>/dashboard")
@login_required
def team_dashboard(league_code):
    team = get_session_team(flask_session.get("session_id", ""))
    conn = get_db()
    league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
    my_recaps = conn.execute(
        "SELECT * FROM recaps WHERE league_id = ? AND created_by = ? ORDER BY created_at DESC",
        (league["id"], team["id"])
    ).fetchall()
    conn.close()
    return render_template("dashboard.html", team=team, league=league, recaps=my_recaps)


@app.route("/l/<league_code>/recap/new", methods=["GET", "POST"])
@login_required
def recap_new(league_code):
    team = get_session_team(flask_session.get("session_id", ""))
    conn = get_db()
    league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
    if not league:
        abort(404)

    if request.method == "POST":
        week = int(request.form.get("week", 0))
        content = request.form.get("content", "").strip()
        if not content:
            flash("Recap content is required.")
            return redirect(url_for("recap_new", league_code=league_code))

        conn.execute(
            "INSERT INTO recaps (league_id, week, content, created_by) VALUES (?, ?, ?, ?)",
            (league["id"], week, content, team["id"])
        )
        conn.commit()
        conn.close()
        _auto_backup()
        flash("Recap saved.")
        return redirect(url_for("team_dashboard", league_code=league_code))

    conn.close()
    return render_template("recap_new.html", team=team, league=league)


# ============================================================
# Distribution (WhatsApp, Signal, Webhook, Email, RSS)
# ============================================================

@app.route("/l/<league_code>/share/<int:recap_id>")
def share_recap(league_code, recap_id):
    """Share links for a recap."""
    conn = get_db()
    league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
    if not league:
        abort(404)
    recap = conn.execute("SELECT * FROM recaps WHERE id = ? AND league_id = ?",
                         (recap_id, league["id"])).fetchone()
    conn.close()
    if not recap:
        abort(404)

    recap_url = url_for("league_recap", league_code=league_code,
                        recap_id=recap_id, _external=True)
    recap_text = f"{league['name']} — Week {recap['week']} Recap\n{recap_url}"

    # WhatsApp link (official wa.me)
    wa_text = requests.utils.quote(recap_text)
    whatsapp_url = f"https://wa.me/?text={wa_text}"

    return render_template("share.html", league=league, recap=recap,
                           recap_url=recap_url, recap_text=recap_text,
                           whatsapp_url=whatsapp_url)


@app.route("/l/<league_code>/webhook/<int:recap_id>", methods=["POST"])
def webhook_post(league_code, recap_id):
    """Post recap to configured webhooks (Slack/Discord/Telegram)."""
    conn = get_db()
    league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
    if not league:
        abort(404)
    recap = conn.execute("SELECT * FROM recaps WHERE id = ? AND league_id = ?",
                         (recap_id, league["id"])).fetchone()
    webhooks = conn.execute("SELECT * FROM webhooks WHERE league_id = ?", (league["id"],)).fetchall()
    conn.close()
    if not recap:
        abort(404)

    results = []
    for hook in webhooks:
        try:
            if hook["webhook_type"] == "slack":
                payload = {"text": recap["content"]}
                resp = requests.post(hook["webhook_url"], json=payload, timeout=10)
            elif hook["webhook_type"] == "discord":
                payload = {"content": recap["content"]}
                resp = requests.post(hook["webhook_url"], json=payload, timeout=10)
            elif hook["webhook_type"] == "telegram":
                # Telegram bot API
                bot_token = hook["webhook_url"].split("/")[-1]
                chat_id = hook["webhook_url"].split("/")[-2]
                api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                payload = {"chat_id": chat_id, "text": recap["content"]}
                resp = requests.post(api_url, json=payload, timeout=10)
            else:
                resp = None
            results.append({"type": hook["webhook_type"], "status": resp.status_code if resp else "skipped"})
        except Exception as e:
            results.append({"type": hook["webhook_type"], "status": "error", "error": str(e)})

    return jsonify({"results": results})


# ============================================================
# RSS Feed
# ============================================================

@app.route("/l/<league_code>/feed.rss")
def league_rss(league_code):
    """RSS feed for a league."""
    conn = get_db()
    league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
    if not league:
        abort(404)
    recaps = conn.execute(
        "SELECT * FROM recaps WHERE league_id = ? ORDER BY created_at DESC LIMIT 50",
        (league["id"],)
    ).fetchall()
    conn.close()

    base_url = url_for("league_public", league_code=league_code, _external=True)

    rss_items = []
    for recap in recaps:
        recap_url = url_for("league_recap", league_code=league_code,
                            recap_id=recap["id"], _external=True)
        rss_items.append(f"""
        <item>
            <title>Week {recap['week']} Recap</title>
            <link>{recap_url}</link>
            <guid>{recap_url}</guid>
            <pubDate>{recap['created_at']}</pubDate>
            <description>{recap['content'][:200]}...</description>
        </item>""")

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
    <channel>
        <title>{league['name']} — Recap Feed</title>
        <link>{base_url}</link>
        <description>Weekly recaps for {league['name']}</description>
        {''.join(rss_items)}
    </channel>
</rss>"""

    response = make_response(rss)
    response.headers["Content-Type"] = "application/rss+xml"
    return response


# ============================================================
# Recap & Start/Sit generation
# ============================================================

@app.route("/l/<league_code>/recap/generate", methods=["POST"])
@login_required
def recap_generate(league_code):
    """Auto-generate recap for a given week."""
    team = get_session_team(flask_session.get("session_id", ""))
    conn = get_db()
    league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
    if not league:
        abort(404)
    conn.close()

    week = int(request.form.get("week", 0))

    try:
        from sleeper_recap import generate_recap
        content = generate_recap(league["sleeper_league_id"], week=week)
    except Exception as e:
        flash(f"Recap generation failed: {str(e)}")
        return redirect(url_for("team_dashboard", league_code=league_code))

    conn = get_db()
    conn.execute(
        "INSERT INTO recaps (league_id, week, content, created_by) VALUES (?, ?, ?, ?)",
        (league["id"], week, content, team["id"])
    )
    conn.commit()
    conn.close()
    _auto_backup()
    flash("Recap generated successfully.")
    return redirect(url_for("team_dashboard", league_code=league_code))


@app.route("/l/<league_code>/startsit", methods=["POST"])
@login_required
def startsit_advice(league_code):
    """Generate start/sit advice for the team."""
    team = get_session_team(flask_session.get("session_id", ""))
    conn = get_db()
    league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
    if not league:
        abort(404)
    conn.close()

    week = int(request.form.get("week", 0))

    try:
        from sleeper_recap import generate_startsit
        content = generate_startsit(league["sleeper_league_id"], week=week)
    except Exception as e:
        flash(f"Start/Sit generation failed: {str(e)}")
        return redirect(url_for("team_dashboard", league_code=league_code))

    return render_template("startsit.html", team=team, league=league, content=content, week=week)


@app.route("/l/<league_code>/waivers", methods=["POST"])
@login_required
def waiver_advice(league_code):
    """Generate waiver advice for the team."""
    team = get_session_team(flask_session.get("session_id", ""))
    conn = get_db()
    league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
    if not league:
        abort(404)
    conn.close()

    week = int(request.form.get("week", 0))

    try:
        from sleeper_recap import generate_waivers
        content = generate_waivers(league["sleeper_league_id"], week=week)
    except Exception as e:
        flash(f"Waiver generation failed: {str(e)}")
        return redirect(url_for("team_dashboard", league_code=league_code))

    return render_template("waivers.html", team=team, league=league, content=content, week=week)


# ============================================================
# Initialize DB on startup
# ============================================================

with app.app_context():
    init_db()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
