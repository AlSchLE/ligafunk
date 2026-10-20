"""
Sleeper Web — League recap and team management platform.

Run:  python app.py            (dev, port 5000)
      gunicorn -w 2 -b 0.0.0.0:5000 app:app   (prod)
"""
import os
import json
import hmac
import secrets
import requests
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path

from flask import (Flask, render_template, request, redirect,
                   url_for, flash, abort, jsonify, send_file,
                   make_response, session as flask_session)
from flask_talisman import Talisman
from werkzeug.security import check_password_hash
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sleeper-web")

# Security headers via Talisman
csp = {
    'default-src': "'self'",
    'img-src': "'self' data:",
    'style-src': "'self' 'unsafe-inline'",
    'script-src': "'self' 'unsafe-inline'",
}

app = Flask(__name__)

# Render terminates TLS at its proxy and forwards the original scheme via
# X-Forwarded-Proto. Trust exactly one proxy hop so url_for(), secure cookies
# and the HTTPS redirect all see the real scheme. Disabled for local HTTP dev.
IS_PRODUCTION = bool(os.environ.get("RENDER")) or os.environ.get("FORCE_HTTPS") == "1"
if IS_PRODUCTION:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)

Talisman(
    app,
    content_security_policy=csp,
    force_https=IS_PRODUCTION,
    strict_transport_security=IS_PRODUCTION,
    strict_transport_security_max_age=31536000,
    strict_transport_security_include_subdomains=True,
    session_cookie_secure=IS_PRODUCTION,
    session_cookie_http_only=True,
    session_cookie_samesite='Lax',
)

from config import (DATABASE_PATH, SECRET_KEY, SESSION_LIFETIME_HOURS,
                    ADMIN_PASSWORD, ADMIN_PASSWORD_HASH, ADMIN_ENABLED,
                    IMPRESSUM, IMPRESSUM_CONFIGURED)
from database import (get_db, init_db, hash_password, verify_password,
                      generate_league_code, create_session,
                      get_session_team, delete_session)

app.secret_key = SECRET_KEY
app.config["SESSION_LIFETIME"] = timedelta(hours=SESSION_LIFETIME_HOURS)
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = 3600  # 1 hour
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB upload limit

def _verify_admin_password(candidate: str) -> bool:
    """Check the submitted password against the configured hash or plaintext.

    Returns False when no admin secret is configured.
    """
    if not candidate:
        return False
    if ADMIN_PASSWORD_HASH:
        try:
            return check_password_hash(ADMIN_PASSWORD_HASH, candidate)
        except (ValueError, TypeError):
            log.error("ADMIN_PASSWORD_HASH is not a valid Werkzeug hash")
            return False
    if ADMIN_PASSWORD:
        return hmac.compare_digest(candidate, ADMIN_PASSWORD)
    return False

# Auto-backup directory
BACKUP_DIR = Path(__file__).resolve().parent / "backups"
BACKUP_DIR.mkdir(exist_ok=True)
MAX_BACKUPS = 10


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


def _csrf_token():
    """Get or create a CSRF token for the session."""
    if "_csrf_token" not in flask_session:
        flask_session["_csrf_token"] = secrets.token_hex(32)
    return flask_session["_csrf_token"]


def _validate_csrf():
    """Validate CSRF token from request."""
    token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token")
    if not token or not secrets.compare_digest(token, flask_session.get("_csrf_token", "")):
        abort(403)


@app.context_processor
def inject_csrf_token():
    return {"csrf_token": _csrf_token()}


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not flask_session.get("admin_authenticated"):
            return redirect(url_for("admin_login", next=request.url))
        return f(*args, **kwargs)
    return decorated_function


@app.errorhandler(500)
def internal_error(e):
    log.error("Internal error: %s", e)
    return render_template("500.html"), 500


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        team = get_session_team(flask_session.get("session_id", ""))
        if not team:
            flash("Please log in.")
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated_function


# ============================================================
# Home / Admin Auth
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/impressum")
def impressum():
    return render_template("impressum.html", impressum=IMPRESSUM,
                           impressum_configured=IMPRESSUM_CONFIGURED)


def _safe_next_url(candidate):
    """Only allow same-site relative redirect targets."""
    fallback = url_for("admin")
    if not candidate:
        return fallback
    if not candidate.startswith("/") or candidate.startswith("//"):
        return fallback
    return candidate


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    next_url = _safe_next_url(request.form.get("next") or request.args.get("next"))

    if not ADMIN_ENABLED:
        log.error("Admin login attempted, but ADMIN_PASSWORD/ADMIN_PASSWORD_HASH is not set")
        return render_template("admin_login.html", next=next_url,
                               admin_enabled=False), 503

    if request.method == "POST":
        _validate_csrf()
        password = request.form.get("password", "")
        try:
            if _verify_admin_password(password):
                flask_session.clear()
                flask_session["admin_authenticated"] = True
                flask_session["session_id"] = secrets.token_urlsafe(32)
                flash("Admin login successful.")
                return redirect(next_url)
            flash("Invalid password.")
            log.warning("Failed admin login attempt from %s", request.remote_addr)
        except Exception as e:
            log.error("Admin login error: %s", e)
            flash("Login failed. Please try again.")
    return render_template("admin_login.html", next=next_url, admin_enabled=True)


@app.route("/admin/logout")
def admin_logout():
    flask_session.pop("admin_authenticated", None)
    flash("Logged out.")
    return redirect(url_for("index"))


@app.route("/admin/backup")
@admin_required
def backup():
    """Export all data as JSON download."""
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
        "exported_at": datetime.now().isoformat(),
    }
    return jsonify(data)


@app.route("/admin/download-backup/<path:filename>")
@admin_required
def download_backup(filename):
    """Download a backup file from the backups directory."""
    from flask import safe_join
    safe_path = safe_join(str(BACKUP_DIR), filename)
    if not safe_path or not os.path.isfile(safe_path):
        abort(404)
    return send_file(safe_path, as_attachment=True)


# Whitelisted table columns for backup import (never derived from user input).
_BACKUP_SCHEMA = {
    "leagues": ("id", "name", "sleeper_league_id", "league_code", "created_at"),
    "teams": ("id", "league_id", "name", "owner_name", "sleeper_roster_id",
              "password_hash", "created_at"),
    "recaps": ("id", "league_id", "week", "content", "created_by", "created_at"),
    "webhooks": ("id", "league_id", "webhook_type", "webhook_url", "created_at"),
}


@app.route("/admin/import", methods=["POST"])
@admin_required
def import_backup():
    """Restore leagues, teams, recaps and webhooks from a JSON backup."""
    _validate_csrf()

    upload = request.files.get("backup_file")
    if not upload or not upload.filename:
        flash("No backup file selected.")
        return redirect(url_for("admin"))

    try:
        data = json.load(upload.stream)
    except (ValueError, UnicodeDecodeError):
        flash("Import failed: the file is not valid JSON.")
        return redirect(url_for("admin"))
    if not isinstance(data, dict):
        flash("Import failed: expected a JSON object.")
        return redirect(url_for("admin"))

    counts = {}
    conn = get_db()
    try:
        for table, columns in _BACKUP_SCHEMA.items():
            rows = data.get(table) or []
            if not isinstance(rows, list):
                raise ValueError(f"'{table}' must be a list")
            placeholders = ", ".join("?" for _ in columns)
            column_list = ", ".join(columns)
            inserted = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                values = [row.get(c) for c in columns]
                conn.execute(
                    f"INSERT OR REPLACE INTO {table} ({column_list}) VALUES ({placeholders})",
                    values,
                )
                inserted += 1
            counts[table] = inserted
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error("Backup import failed: %s", e)
        flash(f"Import failed: {e}")
        return redirect(url_for("admin"))
    finally:
        conn.close()

    _auto_backup()
    summary = ", ".join(f"{n} {table}" for table, n in counts.items())
    flash(f"Backup imported: {summary}")
    return redirect(url_for("admin"))


# ============================================================
# Admin (protected)
# ============================================================

@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin():
    if request.method == "POST":
        _validate_csrf()
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
        _validate_csrf()
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
        _validate_csrf()
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
# Recap & Start/Sit generation
# ============================================================

@app.route("/l/<league_code>/recap/generate", methods=["POST"])
@login_required
def recap_generate(league_code):
    """Auto-generate recap for a given week."""
    _validate_csrf()
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
    _validate_csrf()
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
    _validate_csrf()
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
    _validate_csrf()
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
# Initialize DB on startup
# ============================================================

with app.app_context():
    init_db()


if __name__ == "__main__":
    debug = os.environ.get("FLASK_DEBUG") == "1"
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=debug)
