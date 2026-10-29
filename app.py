"""
Sleeper Web — League recap and team management platform.

Run:  python app.py            (dev, port 5000)
      gunicorn --timeout 180 app:app   (prod)
"""
import os
import json
import hmac
import re
import secrets
import sqlite3
import time
import requests
from xml.sax.saxutils import escape as xml_escape
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from flask import (Flask, render_template, request, redirect,
                   url_for, flash, abort, jsonify, send_file,
                   make_response, session as flask_session, g)
from flask_talisman import Talisman
from werkzeug.security import check_password_hash
from werkzeug.utils import safe_join
import logging

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("sleeper-web")

# Security headers via Talisman.
# script-src intentionally has no 'unsafe-inline': all JavaScript lives in
# static/js/app.js and uses data-* attributes, so injected HTML cannot execute.
csp = {
    'default-src': "'self'",
    'img-src': "'self' data:",
    'style-src': "'self'",
    'script-src': "'self'",
    'connect-src': "'self'",
    'font-src': "'self'",
    'object-src': "'none'",
    'base-uri': "'self'",
    'form-action': "'self'",
    'frame-ancestors': "'none'",
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
                      password_needs_rehash, generate_league_code,
                      create_session, get_session_team, delete_session,
                      login_attempts, record_login_failure,
                      clear_login_failures)
from recapcore.options import (
    STYLE_META as RECAP_STYLES,
    THEME_META as RECAP_THEMES,
    normalize_style,
    normalize_theme,
)

app.secret_key = SECRET_KEY
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# The __Host- prefix prevents a compromised sibling subdomain from planting a
# cookie for this host. It requires Secure + Path=/ and forbids a Domain.
app.config["SESSION_COOKIE_NAME"] = "__Host-ligafunk" if IS_PRODUCTION else "ligafunk_session"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=SESSION_LIFETIME_HOURS)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5 MB upload limit

# Optional Host-header allowlist, e.g. TRUSTED_HOSTS=sleeper.example.org.
_trusted_hosts = [h.strip() for h in os.environ.get("TRUSTED_HOSTS", "").split(",") if h.strip()]
if _trusted_hosts:
    app.config["TRUSTED_HOSTS"] = _trusted_hosts


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


ADMIN_SESSION_SECONDS = max(1, _env_int("ADMIN_SESSION_HOURS", 8)) * 3600
MAX_LOGIN_ATTEMPTS = 10
LOGIN_WINDOW_MINUTES = 15
MAX_PASSWORD_LENGTH = 256
# Cap stored recap text so a single account cannot fill the (ephemeral) disk.
MAX_RECAP_LENGTH = 50_000
# League codes are generated as 12 upper-case URL-safe characters, but older
# imported leagues may use any 6-64 character code from this alphabet.
_LEAGUE_CODE_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_LEAGUE_CODE_INPUT_MAX = 500

if not os.environ.get("SECRET_KEY"):
    log.warning("SECRET_KEY is not set; sessions will be invalidated on restart. "
                "Set it in the hosting environment.")
if IS_PRODUCTION and ADMIN_PASSWORD and not ADMIN_PASSWORD_HASH:
    log.error("ADMIN_PASSWORD is not accepted in production; set ADMIN_PASSWORD_HASH.")
    ADMIN_ENABLED = False


def _client_key() -> str:
    """Best-effort client identity for rate limiting (ProxyFix gives the real IP)."""
    return request.remote_addr or "unknown"


def _verify_admin_password(candidate: str) -> bool:
    """Check the submitted password against the configured hash or plaintext.

    Returns False when no admin secret is configured.
    """
    if not candidate or len(candidate) > MAX_PASSWORD_LENGTH:
        return False
    if ADMIN_PASSWORD_HASH:
        try:
            return check_password_hash(ADMIN_PASSWORD_HASH, candidate)
        except (ValueError, TypeError):
            log.error("ADMIN_PASSWORD_HASH is not a valid Werkzeug hash")
            return False
    if ADMIN_PASSWORD and not IS_PRODUCTION:
        return hmac.compare_digest(
            candidate.encode("utf-8"), ADMIN_PASSWORD.encode("utf-8")
        )
    return False


def _parse_int(value, minimum: int = 0, maximum: int = 2**31 - 1) -> int | None:
    """Parse a bounded integer; None for anything malformed or out of range."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number < minimum or number > maximum:
        return None
    return number


def _generate_initial_password() -> str:
    """Generate a one-time team password (16 URL-safe characters)."""
    return secrets.token_urlsafe(12)


def _normalize_login_name(value) -> str:
    """Trim a username/team name and tolerate a leading @ from Sleeper handles."""
    if not isinstance(value, str):
        return ""
    name = value.strip()
    if name.startswith("@"):
        name = name[1:].strip()
    return name[:200]


# Auto-backup directory
BACKUP_DIR = Path(__file__).resolve().parent / "backups"
BACKUP_DIR.mkdir(exist_ok=True)
try:
    os.chmod(BACKUP_DIR, 0o700)
except OSError:
    pass
MAX_BACKUPS = 10

# New dated name plus the previous timestamped format, so existing local
# backups stay downloadable after the naming change.
_BACKUP_NAME_RE = re.compile(
    r"^(?:ligafunk_\d{6}|sleeper_backup_\d{8}_\d{6})\.json$"
)


def _backup_files():
    """Return known backup files, newest first."""
    candidates = list(BACKUP_DIR.glob("ligafunk_*.json"))
    candidates.extend(BACKUP_DIR.glob("sleeper_backup_*.json"))
    backups = [path for path in candidates if _BACKUP_NAME_RE.fullmatch(path.name)]
    return sorted(backups, key=lambda path: path.stat().st_mtime, reverse=True)


def _auto_backup():
    """Create a JSON backup of all data."""
    now = datetime.now()
    backup_file = BACKUP_DIR / f"ligafunk_{now:%d%m%y}.json"

    conn = get_db()
    try:
        leagues = conn.execute("SELECT * FROM leagues").fetchall()
        teams = conn.execute("SELECT * FROM teams").fetchall()
        recaps = conn.execute("SELECT * FROM recaps").fetchall()
        webhooks = conn.execute("SELECT * FROM webhooks").fetchall()
    finally:
        conn.close()

    data = {
        "leagues": [dict(l) for l in leagues],
        "teams": [dict(t) for t in teams],
        "recaps": [dict(r) for r in recaps],
        "webhooks": [dict(w) for w in webhooks],
        "timestamp": now.isoformat(),
    }

    with open(backup_file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    try:
        os.chmod(backup_file, 0o600)
    except OSError:
        pass

    # Keep only the newest backups, regardless of the filename format.
    for old_backup in _backup_files()[MAX_BACKUPS:]:
        try:
            old_backup.unlink()
        except OSError:
            pass

    log.info("Auto-backup created: %s", backup_file.name)


def _csrf_token():
    """Get or create a CSRF token for the session."""
    if "_csrf_token" not in flask_session:
        flask_session["_csrf_token"] = secrets.token_hex(32)
    return flask_session["_csrf_token"]


_CSRF_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_csrf():
    """Validate a CSRF token without ever comparing non-ASCII or malformed data."""
    token = request.form.get("_csrf_token") or request.headers.get("X-CSRF-Token") or ""
    expected = flask_session.get("_csrf_token", "")
    if not isinstance(token, str) or not _CSRF_RE.fullmatch(token):
        abort(403)
    if not isinstance(expected, str) or not _CSRF_RE.fullmatch(expected):
        abort(403)
    if not secrets.compare_digest(token, expected):
        abort(403)


def _current_path() -> str:
    """Relative path (with query) for post-login redirects."""
    return request.full_path if request.query_string else request.path


def _session_id() -> str:
    """Return the session id only if it is a sane string from our own cookie."""
    sid = flask_session.get("session_id")
    if isinstance(sid, str) and 0 < len(sid) <= 128:
        return sid
    return ""


def _get_current_team():
    """Resolve the logged-in team once per request (or None)."""
    if "current_team" not in g:
        if flask_session.get("admin_authenticated"):
            g.current_team = None
        else:
            g.current_team = get_session_team(_session_id())
    return g.current_team


@app.context_processor
def inject_csrf_token():
    return {
        "csrf_token": _csrf_token(),
        "recap_styles": RECAP_STYLES,
        "recap_themes": RECAP_THEMES,
        "current_team": _get_current_team(),
    }


def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not flask_session.get("admin_authenticated"):
            return redirect(url_for("admin_login", next=_current_path()))
        # Roles are exclusive: a team session must never carry admin rights.
        if _session_id():
            flask_session.clear()
            flash("Please log in to the admin area again.")
            return redirect(url_for("admin_login", next=_current_path()))
        # Admin sessions expire even if the browser keeps the cookie.
        auth_at = flask_session.get("admin_authenticated_at")
        if not isinstance(auth_at, int) or (time.time() - auth_at) > ADMIN_SESSION_SECONDS:
            flask_session.clear()
            flash("Admin session expired. Please log in again.")
            return redirect(url_for("admin_login", next=_current_path()))
        return f(*args, **kwargs)
    return decorated_function


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_error(e):
    log.error("Internal error: %s", e)
    return render_template("500.html"), 500


@app.after_request
def harden_response(resp):
    """Private pages must never be cached; add defense-in-depth headers."""
    path = request.path or ""
    private_suffixes = ("/dashboard", "/recap/new", "/startsit",
                        "/waivers", "/trade", "/password", "/login", "/logout")
    needs_no_store = (
        bool(flask_session.get("session_id") or flask_session.get("admin_authenticated"))
        or path.startswith("/admin")
        or "/webhook/" in path
        or (path.startswith("/l/") and any(path.endswith(s) for s in private_suffixes))
    )
    if needs_no_store:
        resp.headers["Cache-Control"] = "no-store, private, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    if path.startswith("/l/") or path.startswith("/admin"):
        resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy",
                            "geolocation=(), microphone=(), camera=(), payment=()")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    return resp


def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # Roles are exclusive: an admin session is not a team session.
        if flask_session.get("admin_authenticated"):
            return redirect(url_for("admin"))
        team = _get_current_team()
        if not team:
            flash("Please log in.")
            league_code = kwargs.get("league_code") or (request.view_args or {}).get("league_code")
            if league_code:
                return redirect(url_for("login", league_code=league_code))
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated_function


# ============================================================
# Home / Admin Auth
# ============================================================

@app.route("/")
def index():
    return render_template("index.html")


def _extract_league_code(value) -> str:
    """Extract a league code from a bare code or a pasted league link."""
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if not raw or len(raw) > _LEAGUE_CODE_INPUT_MAX:
        return ""
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in raw):
        return ""
    # Accept full links such as https://host/l/<code> and paths such as
    # /l/<code>/login. Only the code is used, never the submitted URL.
    if "/l/" in raw:
        raw = raw.split("/l/", 1)[1]
    raw = raw.split("?", 1)[0].split("#", 1)[0].strip("/").split("/", 1)[0]
    return raw if _LEAGUE_CODE_RE.fullmatch(raw) else ""


@app.route("/team-login")
def team_login_entry():
    """Send a league code or pasted league link to the team login page."""
    code = _extract_league_code(request.args.get("league_code"))
    if not code:
        flash("Enter a valid league code or league link.")
        return redirect(url_for("index"))

    conn = get_db()
    try:
        league = conn.execute(
            "SELECT league_code FROM leagues WHERE league_code = ?", (code,)
        ).fetchone()
        # Generated codes are upper-case; accept lower-case typing without
        # breaking older imported codes that really are lower-case.
        if not league and code != code.upper():
            league = conn.execute(
                "SELECT league_code FROM leagues WHERE league_code = ?",
                (code.upper(),),
            ).fetchone()
    finally:
        conn.close()

    if not league:
        flash("League not found. Check the code or link.")
        return redirect(url_for("index"))
    return redirect(url_for("login", league_code=league["league_code"]))


@app.route("/impressum")
def impressum():
    return render_template("impressum.html", impressum=IMPRESSUM,
                           impressum_configured=IMPRESSUM_CONFIGURED)


def _safe_next_url(candidate):
    """Only allow relative /admin redirect targets.

    Rejects absolute URLs, protocol-relative URLs, backslashes (browsers treat
    them as slashes), control characters and dot-segment escapes.
    """
    fallback = url_for("admin")
    if not candidate or not isinstance(candidate, str):
        return fallback
    candidate = candidate.strip()
    if len(candidate) > 2048:
        return fallback
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in candidate):
        return fallback
    lowered = candidate.lower()
    if (
        "\\" in candidate
        or not candidate.startswith("/")
        or candidate.startswith("//")
        or "%2e" in lowered
        or "%2f" in lowered
        or "%5c" in lowered
    ):
        return fallback
    parsed = urlparse(candidate)
    if parsed.scheme or parsed.netloc:
        return fallback
    segments = [s for s in parsed.path.split("/") if s]
    if any(s in (".", "..") for s in segments):
        return fallback
    if parsed.path != "/admin" and not parsed.path.startswith("/admin/"):
        return fallback
    return candidate


# ---------------------------------------------------------------------------
# Webhook validation (SSRF hardening)
# ---------------------------------------------------------------------------
# Only the official provider endpoints are allowed. The destination is checked
# when a webhook is configured and again immediately before every request, so a
# malicious backup import cannot turn the server into an SSRF proxy.
WEBHOOK_HOSTS = {
    "slack": {"hooks.slack.com"},
    "discord": {"discord.com", "discordapp.com",
                "ptb.discord.com", "canary.discord.com"},
    "telegram": {"api.telegram.org"},
}
TELEGRAM_TOKEN_RE = re.compile(r"^[A-Za-z0-9:_-]{20,128}$")
TELEGRAM_CHAT_RE = re.compile(r"^-?\d{1,20}$")


def _validate_webhook(webhook_type, raw_url):
    """Return (normalized_target, None) or (None, error_message).

    Slack/Discord targets are returned as the original HTTPS URL. Telegram
    targets are normalized to ``bot_token/chat_id``.
    """
    kind = (webhook_type or "").strip().lower()
    raw = (raw_url or "").strip()
    if kind not in WEBHOOK_HOSTS:
        return None, "Unsupported webhook type."
    if not raw or len(raw) > 500:
        return None, "Webhook URL is missing or too long."
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in raw):
        return None, "Webhook URL contains control characters."

    if kind == "telegram":
        if raw.startswith("https://api.telegram.org/"):
            parsed = urlparse(raw)
            if parsed.hostname != "api.telegram.org":
                return None, "Telegram webhook must use api.telegram.org."
            parts = [p for p in parsed.path.split("/") if p]
            if not parts or not parts[0].startswith("bot"):
                return None, "Invalid Telegram webhook URL."
            token = parts[0][3:]
            chat_id = (parse_qs(parsed.query).get("chat_id") or [""])[0]
        else:
            parts = raw.split("/")
            if len(parts) != 2:
                return None, "Telegram format must be bot_token/chat_id."
            token, chat_id = parts
        if not TELEGRAM_TOKEN_RE.fullmatch(token):
            return None, "Invalid Telegram bot token."
        if not TELEGRAM_CHAT_RE.fullmatch(chat_id):
            return None, "Invalid Telegram chat id."
        return f"{token}/{chat_id}", None

    parsed = urlparse(raw)
    if parsed.scheme != "https":
        return None, "Webhook URL must use HTTPS."
    host = (parsed.hostname or "").lower().rstrip(".")
    if host not in WEBHOOK_HOSTS[kind]:
        allowed = ", ".join(sorted(WEBHOOK_HOSTS[kind]))
        return None, f"{kind.title()} webhook host must be one of: {allowed}."
    if parsed.username or parsed.password:
        return None, "Webhook URL must not contain credentials."
    try:
        port = parsed.port
    except ValueError:
        return None, "Invalid webhook port."
    if port not in (None, 443):
        return None, "Webhook URL must use port 443."
    if parsed.fragment:
        return None, "Webhook URL must not contain a fragment."
    return raw, None


def _parse_week(value) -> int | None:
    """Parse a week number from a form value. Returns None when invalid."""
    try:
        week = int(value)
    except (TypeError, ValueError):
        return None
    return week if 1 <= week <= 22 else None


def _require_team_in_league(team, league) -> None:
    """Stop a logged-in team from reaching another league's team tools."""
    if not league:
        abort(404)
    if not team or team["league_id"] != league["id"]:
        abort(403)


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    next_url = _safe_next_url(request.form.get("next") or request.args.get("next"))

    if not ADMIN_ENABLED:
        log.error("Admin login attempted, but ADMIN_PASSWORD/ADMIN_PASSWORD_HASH is not set")
        return render_template("admin_login.html", next=next_url,
                               admin_enabled=False), 503

    if request.method == "POST":
        _validate_csrf()
        client = _client_key()
        if login_attempts("admin", client, LOGIN_WINDOW_MINUTES) >= MAX_LOGIN_ATTEMPTS:
            log.warning("Admin login rate limit hit from %s", client)
            return render_template("admin_login.html", next=next_url,
                                   admin_enabled=True, rate_limited=True), 429

        password = request.form.get("password", "")
        try:
            if _verify_admin_password(password):
                clear_login_failures("admin", client)
                old_session = _session_id()
                if old_session:
                    delete_session(old_session)
                flask_session.clear()
                flask_session["admin_authenticated"] = True
                flask_session["admin_authenticated_at"] = int(time.time())
                flash("Admin login successful.")
                return redirect(next_url)
            record_login_failure("admin", client)
            flash("Invalid password.")
            log.warning("Failed admin login attempt from %s", client)
        except Exception:
            record_login_failure("admin", client)
            log.exception("Admin login error")
            flash("Login failed. Please try again.")
    return render_template("admin_login.html", next=next_url, admin_enabled=True)


@app.route("/admin/logout", methods=["POST"])
@admin_required
def admin_logout():
    _validate_csrf()
    flask_session.clear()
    flash("Logged out.")
    return redirect(url_for("index"))


@app.route("/admin/backup")
@admin_required
def backup():
    """Export all data as JSON download."""
    conn = get_db()
    try:
        leagues = conn.execute("SELECT * FROM leagues").fetchall()
        teams = conn.execute("SELECT * FROM teams").fetchall()
        recaps = conn.execute("SELECT * FROM recaps").fetchall()
        webhooks = conn.execute("SELECT * FROM webhooks").fetchall()
    finally:
        conn.close()

    now = datetime.now()
    data = {
        "leagues": [dict(l) for l in leagues],
        "teams": [dict(t) for t in teams],
        "recaps": [dict(r) for r in recaps],
        "webhooks": [dict(w) for w in webhooks],
        "exported_at": now.isoformat(),
    }
    response = jsonify(data)
    response.headers["Content-Disposition"] = (
        f'attachment; filename="ligafunk_{now:%d%m%y}.json"'
    )
    return response


@app.route("/admin/download-backup/<filename>")
@admin_required
def download_backup(filename):
    """Download only files this application generated, never arbitrary paths."""
    if not _BACKUP_NAME_RE.fullmatch(filename):
        abort(404)
    safe_path = safe_join(str(BACKUP_DIR), filename)
    if not safe_path:
        abort(404)
    path = Path(safe_path)
    try:
        if path.is_symlink() or not path.is_file():
            abort(404)
        resolved = path.resolve()
        if resolved.parent != BACKUP_DIR.resolve():
            abort(404)
    except OSError:
        abort(404)
    return send_file(str(resolved), as_attachment=True, download_name=filename)


# Whitelisted table columns for backup import (never derived from user input).
_BACKUP_SCHEMA = {
    "leagues": ("id", "name", "sleeper_league_id", "league_code", "created_at"),
    "teams": ("id", "league_id", "name", "owner_name", "sleeper_roster_id",
              "username", "password_hash", "created_at"),
    "recaps": ("id", "league_id", "week", "content", "created_by",
               "created_at", "style", "theme"),
    "webhooks": ("id", "league_id", "webhook_type", "webhook_url", "created_at"),
}
_LEGACY_HASH_RE = re.compile(r"^[0-9a-f]{16,128}\$[0-9a-f]{64}$")


def _valid_password_hash(value) -> bool:
    """Accept modern Werkzeug hashes and the legacy salt$sha256 format."""
    if not isinstance(value, str) or not value or len(value) > 512:
        return False
    if value.startswith(("scrypt:", "pbkdf2:")):
        return "$" in value
    return bool(_LEGACY_HASH_RE.fullmatch(value))


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
    except (ValueError, UnicodeDecodeError, RecursionError):
        flash("Import failed: the file is not valid JSON.")
        return redirect(url_for("admin"))
    if not isinstance(data, dict):
        flash("Import failed: expected a JSON object.")
        return redirect(url_for("admin"))

    counts = {}
    conn = get_db()
    try:
        for table, columns in _BACKUP_SCHEMA.items():
            rows = data.get(table)
            if rows is None:
                rows = []
            if not isinstance(rows, list):
                raise ValueError(f"'{table}' must be a list")
            placeholders = ", ".join("?" for _ in columns)
            column_list = ", ".join(columns)
            inserted = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                # Reject structural tampering instead of trusting an uploaded
                # file. Webhook URLs are validated again at send time.
                row_id = row.get("id")
                if type(row_id) is not int or not 1 <= row_id <= 2**31 - 1:
                    raise ValueError(f"a row in '{table}' has an invalid id")
                if table == "leagues":
                    if not _LEAGUE_CODE_RE.fullmatch(str(row.get("league_code") or "")):
                        raise ValueError("a league code has an invalid format")
                    if not re.fullmatch(r"[0-9]{1,32}", str(row.get("sleeper_league_id") or "")):
                        raise ValueError("a Sleeper league id is invalid")
                elif table == "teams":
                    if not _valid_password_hash(row.get("password_hash")):
                        raise ValueError("a team password hash has an invalid format")
                elif table == "recaps":
                    week = row.get("week")
                    if type(week) is not int or not 1 <= week <= 22:
                        raise ValueError("a recap week is not between 1 and 22")
                    if (not isinstance(row.get("content"), str)
                            or len(row["content"]) > MAX_RECAP_LENGTH):
                        raise ValueError("a recap has invalid content")
                elif table == "webhooks":
                    if row.get("webhook_type") not in WEBHOOK_HOSTS:
                        raise ValueError("a webhook type is invalid")
                if table != "leagues":
                    league_id = row.get("league_id")
                    if type(league_id) is not int or not conn.execute(
                        "SELECT 1 FROM leagues WHERE id = ?", (league_id,)
                    ).fetchone():
                        raise ValueError(f"a row in '{table}' references a missing league")
                values = [row.get(c) for c in columns]
                conn.execute(
                    f"INSERT OR REPLACE INTO {table} ({column_list}) VALUES ({placeholders})",
                    values,
                )
                inserted += 1
            counts[table] = inserted
        conn.commit()
    except ValueError as e:
        conn.rollback()
        flash(f"Import failed: {e}.")
        return redirect(url_for("admin"))
    except Exception:
        conn.rollback()
        log.exception("Backup import failed")
        flash("Import failed: the backup could not be restored.")
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
    import_result = None
    if request.method == "POST":
        _validate_csrf()
        action = request.form.get("action")

        if action == "add_league":
            name = request.form.get("name", "").strip()
            sleeper_league_id = request.form.get("sleeper_league_id", "").strip()
            if not name or not sleeper_league_id:
                flash("League name and Sleeper ID are required.")
                return redirect(url_for("admin"))
            if len(name) > 200 or not re.fullmatch(r"[0-9]{6,32}", sleeper_league_id):
                flash("League name or Sleeper ID is invalid.")
                return redirect(url_for("admin"))
            conn = get_db()
            try:
                # 12 URL-safe characters; verify uniqueness explicitly.
                code = None
                for _ in range(10):
                    candidate = generate_league_code()
                    exists = conn.execute(
                        "SELECT 1 FROM leagues WHERE league_code = ?", (candidate,)
                    ).fetchone()
                    if not exists:
                        code = candidate
                        break
                if not code:
                    flash("Could not allocate a unique league code. Please retry.")
                    return redirect(url_for("admin"))
                conn.execute(
                    "INSERT INTO leagues (name, sleeper_league_id, league_code) VALUES (?, ?, ?)",
                    (name, sleeper_league_id, code),
                )
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                flash("A league with this Sleeper ID already exists.")
                return redirect(url_for("admin"))
            finally:
                conn.close()
            _auto_backup()
            flash(f"League '{name}' created with code {code}")
            return redirect(url_for("admin"))

        elif action == "add_team":
            league_id = _parse_int(request.form.get("league_id"), 1, 2**31 - 1)
            name = request.form.get("name", "").strip()
            owner_name = request.form.get("owner_name", "").strip()
            username = request.form.get("username", "").strip().lstrip("@").strip()
            sleeper_roster_id = request.form.get("sleeper_roster_id", "").strip()
            password = request.form.get("password", "")
            if not name or not owner_name or not password:
                flash("Team name, owner name, and password are required.")
                return redirect(url_for("admin"))
            if league_id is None:
                flash("Invalid league.")
                return redirect(url_for("admin"))
            if len(name) > 200 or len(owner_name) > 200 or len(username) > 80:
                flash("Team, owner, or username is too long.")
                return redirect(url_for("admin"))
            if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in username):
                flash("Sleeper username contains invalid characters.")
                return redirect(url_for("admin"))
            if not 10 <= len(password) <= MAX_PASSWORD_LENGTH:
                flash("Team password must be 10-256 characters long.")
                return redirect(url_for("admin"))
            if sleeper_roster_id and (
                not re.fullmatch(r"[0-9]{1,3}", sleeper_roster_id)
                or not 1 <= int(sleeper_roster_id) <= 100
            ):
                flash("Sleeper roster ID must be a number between 1 and 100.")
                return redirect(url_for("admin"))
            conn = get_db()
            try:
                if not conn.execute("SELECT 1 FROM leagues WHERE id = ?", (league_id,)).fetchone():
                    flash("Invalid league.")
                    return redirect(url_for("admin"))
                duplicate = conn.execute(
                    "SELECT 1 FROM teams WHERE league_id = ? AND name = ? COLLATE NOCASE",
                    (league_id, name),
                ).fetchone()
                if duplicate:
                    flash("A team with this name already exists in this league.")
                    return redirect(url_for("admin"))
                if username and conn.execute(
                    "SELECT 1 FROM teams WHERE league_id = ? AND username = ? COLLATE NOCASE",
                    (league_id, username),
                ).fetchone():
                    flash("This Sleeper username is already assigned to another team.")
                    return redirect(url_for("admin"))
                if sleeper_roster_id and conn.execute(
                    "SELECT 1 FROM teams WHERE league_id = ? AND sleeper_roster_id = ?",
                    (league_id, sleeper_roster_id),
                ).fetchone():
                    flash("This Sleeper roster ID is already assigned to another team.")
                    return redirect(url_for("admin"))
                conn.execute(
                    "INSERT INTO teams "
                    "(league_id, name, owner_name, sleeper_roster_id, username, password_hash) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (league_id, name, owner_name, sleeper_roster_id or None,
                     username or None, hash_password(password)),
                )
                conn.commit()
            finally:
                conn.close()
            _auto_backup()
            flash(f"Team '{name}' added")
            return redirect(url_for("admin"))

        elif action == "import_sleeper_teams":
            league_id = _parse_int(request.form.get("league_id"), 1, 2**31 - 1)
            if league_id is None:
                flash("Invalid league.")
                return redirect(url_for("admin"))

            conn = get_db()
            try:
                league = conn.execute(
                    "SELECT * FROM leagues WHERE id = ?", (league_id,)
                ).fetchone()
            finally:
                conn.close()
            if not league:
                flash("Invalid league.")
                return redirect(url_for("admin"))

            try:
                from sleeper_recap import fetch_league_participants
                participants = fetch_league_participants(league["sleeper_league_id"])
            except Exception:
                log.exception("Could not load Sleeper participants for league %s", league_id)
                flash("Could not load participants from Sleeper. Please try again.")
                return redirect(url_for("admin"))

            if not participants:
                flash("Sleeper returned no participants for this league.")
                return redirect(url_for("admin"))

            created = []
            linked = []
            skipped = []
            conn = get_db()
            try:
                existing = conn.execute(
                    "SELECT id, name, owner_name, username, sleeper_roster_id "
                    "FROM teams WHERE league_id = ?",
                    (league_id,),
                ).fetchall()
                by_roster = {
                    str(team["sleeper_roster_id"]): team
                    for team in existing if team["sleeper_roster_id"]
                }
                by_username = {
                    str(team["username"]).casefold(): team
                    for team in existing if team["username"]
                }
                by_name = {str(team["name"]).casefold(): team for team in existing}
                name_counts = {}
                for participant in participants:
                    key = participant["name"].casefold()
                    name_counts[key] = name_counts.get(key, 0) + 1

                for participant in participants:
                    team_name = participant["name"]
                    owner_name = participant["owner_name"]
                    username = participant["username"]
                    roster_id = participant["roster_id"]
                    username_key = username.casefold() if username else ""
                    name_key = team_name.casefold()

                    # Prefer the stable Sleeper roster ID, then the unique
                    # Sleeper username, and only then a unique team name. The
                    # last fallback links teams created before this feature.
                    match = by_roster.get(roster_id) if roster_id else None
                    if match is None and username_key:
                        match = by_username.get(username_key)
                    if match is None and name_counts.get(name_key) == 1:
                        match = by_name.get(name_key)

                    if match is not None:
                        updates = {}
                        if username and not match["username"] and username_key not in by_username:
                            updates["username"] = username
                        if (roster_id and not match["sleeper_roster_id"]
                                and roster_id not in by_roster):
                            updates["sleeper_roster_id"] = roster_id
                        if updates:
                            set_clause = ", ".join(f"{column} = ?" for column in updates)
                            conn.execute(
                                f"UPDATE teams SET {set_clause} WHERE id = ?",
                                (*updates.values(), match["id"]),
                            )
                            if "username" in updates:
                                by_username[username_key] = match
                            if "sleeper_roster_id" in updates:
                                by_roster[roster_id] = match
                            linked.append(team_name)
                        else:
                            skipped.append(f"{team_name} (already exists)")
                        continue

                    if not username:
                        skipped.append(f"{team_name} (no Sleeper username)")
                        continue
                    if username_key in by_username:
                        skipped.append(f"{team_name} (username already assigned)")
                        continue
                    if name_key in by_name:
                        skipped.append(f"{team_name} (team name already exists)")
                        continue

                    password = _generate_initial_password()
                    try:
                        cursor = conn.execute(
                            "INSERT INTO teams "
                            "(league_id, name, owner_name, sleeper_roster_id, username, password_hash) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (league_id, team_name, owner_name, roster_id or None,
                             username, hash_password(password)),
                        )
                    except sqlite3.IntegrityError:
                        skipped.append(f"{team_name} (conflict)")
                        continue

                    row = {
                        "id": cursor.lastrowid,
                        "name": team_name,
                        "owner_name": owner_name,
                        "username": username,
                        "sleeper_roster_id": roster_id,
                    }
                    by_username[username_key] = row
                    by_name[name_key] = row
                    if roster_id:
                        by_roster[roster_id] = row
                    created.append({
                        "team_name": team_name,
                        "owner_name": owner_name,
                        "username": username,
                        "password": password,
                    })
                conn.commit()
            except Exception:
                conn.rollback()
                log.exception("Could not import Sleeper teams for league %s", league_id)
                flash("Could not create teams. Please try again.")
                return redirect(url_for("admin"))
            finally:
                conn.close()

            if created or linked:
                _auto_backup()

            login_url = url_for("login", league_code=league["league_code"], _external=True)
            note = "Bitte ändere dein Passwort nach dem ersten Login."
            for item in created:
                item["copy_text"] = (
                    f"Team: {item['team_name']}\n"
                    f"Sleeper: @{item['username']}\n"
                    f"Startpasswort: {item['password']}\n"
                    f"Login: {login_url}\n\n{note}"
                )
            copy_text = "\n\n".join(item["copy_text"] for item in created)

            import_result = {
                "league_name": league["name"],
                "created": created,
                "linked": linked,
                "skipped": skipped,
                "copy_text": copy_text,
            }
            # Render the admin page directly so the initial passwords are
            # visible exactly once and never stored in a cookie or database.

        elif action == "add_webhook":
            league_id = _parse_int(request.form.get("league_id"), 1, 2**31 - 1)
            webhook_url = request.form.get("webhook_url", "").strip()
            webhook_type = request.form.get("webhook_type", "").strip().lower()
            if not webhook_url or not webhook_type:
                flash("Webhook URL and type are required.")
                return redirect(url_for("admin"))
            if league_id is None:
                flash("Invalid league.")
                return redirect(url_for("admin"))
            normalized, error = _validate_webhook(webhook_type, webhook_url)
            if error:
                flash(f"Invalid webhook: {error}")
                return redirect(url_for("admin"))
            conn = get_db()
            try:
                if not conn.execute("SELECT 1 FROM leagues WHERE id = ?", (league_id,)).fetchone():
                    flash("Invalid league.")
                    return redirect(url_for("admin"))
                duplicate = conn.execute(
                    "SELECT 1 FROM webhooks WHERE league_id = ? AND webhook_type = ? AND webhook_url = ?",
                    (league_id, webhook_type, normalized),
                ).fetchone()
                if duplicate:
                    flash("This webhook is already configured for the league.")
                    return redirect(url_for("admin"))
                conn.execute(
                    "INSERT INTO webhooks (league_id, webhook_type, webhook_url) VALUES (?, ?, ?)",
                    (league_id, webhook_type, normalized),
                )
                conn.commit()
            finally:
                conn.close()
            flash("Webhook added")
            return redirect(url_for("admin"))

        elif action == "reset_team_password":
            team_id = _parse_int(request.form.get("team_id"), 1, 2**31 - 1)
            password = request.form.get("password", "")
            if team_id is None or not 10 <= len(password) <= MAX_PASSWORD_LENGTH:
                flash("Invalid team or password (10-256 characters).")
                return redirect(url_for("admin"))
            conn = get_db()
            try:
                changed = conn.execute(
                    "UPDATE teams SET password_hash = ? WHERE id = ?",
                    (hash_password(password), team_id),
                ).rowcount
                # Revoke every active session for the team immediately.
                conn.execute("DELETE FROM sessions WHERE team_id = ?", (team_id,))
                conn.commit()
            finally:
                conn.close()
            if not changed:
                flash("Team not found.")
            else:
                _auto_backup()
                flash("Team password reset. All sessions for the team were revoked.")
            return redirect(url_for("admin"))

        elif action == "delete_webhook":
            webhook_id = _parse_int(request.form.get("webhook_id"), 1, 2**31 - 1)
            if webhook_id is None:
                flash("Invalid webhook.")
                return redirect(url_for("admin"))
            conn = get_db()
            try:
                changed = conn.execute(
                    "DELETE FROM webhooks WHERE id = ?", (webhook_id,)
                ).rowcount
                conn.commit()
            finally:
                conn.close()
            if changed:
                _auto_backup()
            flash("Webhook removed." if changed else "Webhook not found.")
            return redirect(url_for("admin"))

    conn = get_db()
    try:
        leagues = conn.execute("SELECT * FROM leagues ORDER BY created_at DESC").fetchall()
        teams = conn.execute("""
            SELECT t.id, t.league_id, t.name, t.owner_name, t.sleeper_roster_id,
                   t.username, t.created_at, l.name AS league_name
            FROM teams t
            JOIN leagues l ON t.league_id = l.id
            ORDER BY l.name, t.name
        """).fetchall()
        webhooks = conn.execute("""
            SELECT w.*, l.name as league_name FROM webhooks w
            JOIN leagues l ON w.league_id = l.id
        """).fetchall()
    finally:
        conn.close()
    backups = _backup_files()[:10]

    # Never echo full webhook URLs/secrets back into the admin page.
    masked_webhooks = []
    for hook in webhooks:
        row = dict(hook)
        url = row.pop("webhook_url", "") or ""
        if hook["webhook_type"] == "telegram":
            row["masked_url"] = "Telegram chat configured"
        else:
            host = urlparse(url).hostname or "unknown host"
            row["masked_url"] = f"{host} · configured"
        masked_webhooks.append(row)

    return render_template("admin.html", leagues=leagues, teams=teams,
                           webhooks=masked_webhooks, backups=backups,
                           import_result=import_result)


# ============================================================
# League Public Page (unguessable URL)
# ============================================================

@app.route("/l/<league_code>")
def league_public(league_code):
    """Public league page — no auth required."""
    conn = get_db()
    try:
        league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
        if not league:
            abort(404)

        teams = conn.execute(
            "SELECT id, name, owner_name, sleeper_roster_id FROM teams WHERE league_id = ?",
            (league["id"],),
        ).fetchall()
        recaps = conn.execute("""
            SELECT r.id, r.week, r.style, r.theme, r.created_at,
                   t.name as team_name
            FROM recaps r
            LEFT JOIN teams t ON r.created_by = t.id
            WHERE r.league_id = ? ORDER BY r.created_at DESC LIMIT 10
        """, (league["id"],)).fetchall()
        # The public page only counts integrations; never load their URLs.
        webhooks = conn.execute(
            "SELECT id FROM webhooks WHERE league_id = ?", (league["id"],)
        ).fetchall()
    finally:
        conn.close()

    return render_template("league.html", league=league, teams=teams,
                           recaps=recaps, webhooks=webhooks)


@app.route("/l/<league_code>/recap/<int:recap_id>")
def league_recap(league_code, recap_id):
    """Public recap page."""
    conn = get_db()
    try:
        league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
        if not league:
            abort(404)
        recap = conn.execute("SELECT * FROM recaps WHERE id = ? AND league_id = ?",
                             (recap_id, league["id"])).fetchone()
        # Buttons only need the display type, never the secret URL.
        webhooks = conn.execute(
            "SELECT id, webhook_type FROM webhooks WHERE league_id = ?",
            (league["id"],),
        ).fetchall()
    finally:
        conn.close()
    if not recap:
        abort(404)
    # Only a logged-in team of this league may post to its webhooks.
    viewer = _get_current_team()
    visible_webhooks = webhooks if viewer and viewer["league_id"] == league["id"] else []
    return render_template("recap.html", league=league, recap=recap,
                           webhooks=visible_webhooks, viewer_team=viewer)


@app.route("/l/<league_code>/history")
def league_history(league_code):
    """Public recap history."""
    conn = get_db()
    try:
        league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
        if not league:
            abort(404)
        # History only needs metadata; loading every recap body would let a
        # single large league exhaust worker memory.
        recaps = conn.execute("""
            SELECT r.id, r.week, r.style, r.theme, r.created_at,
                   t.name as team_name
            FROM recaps r
            LEFT JOIN teams t ON r.created_by = t.id
            WHERE r.league_id = ? ORDER BY r.week DESC, r.created_at DESC
            LIMIT 200
        """, (league["id"],)).fetchall()
    finally:
        conn.close()
    return render_template("history.html", league=league, recaps=recaps)


# ============================================================
# Login / Logout
# ============================================================

@app.route("/l/<league_code>/login", methods=["GET", "POST"])
def login(league_code):
    conn = get_db()
    try:
        league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
    finally:
        conn.close()
    if not league:
        abort(404)

    if request.method == "POST":
        _validate_csrf()
        client = _client_key()
        scope = f"team:{league['id']}"
        if login_attempts(scope, client, LOGIN_WINDOW_MINUTES) >= MAX_LOGIN_ATTEMPTS:
            log.warning("Team login rate limit hit for league %s from %s", league["id"], client)
            return render_template("login.html", league=league,
                                   rate_limited=True), 429

        login_name = _normalize_login_name(request.form.get("username", ""))
        password = request.form.get("password", "")
        # Older cached login forms posted a team_id; keep that path working.
        team_id = _parse_int(request.form.get("team_id", ""), 1, 2**31 - 1)
        if (not login_name and team_id is None) or not password \
                or len(password) > MAX_PASSWORD_LENGTH:
            record_login_failure(scope, client)
            flash("Invalid username or password.")
            return redirect(url_for("login", league_code=league_code))

        # Fetch the hash only for verification; it never enters template data.
        conn = get_db()
        try:
            if team_id is not None:
                team = conn.execute(
                    "SELECT id, name, owner_name, username, password_hash FROM teams "
                    "WHERE id = ? AND league_id = ?",
                    (team_id, league["id"]),
                ).fetchone()
            else:
                team = conn.execute(
                    "SELECT id, name, owner_name, username, password_hash FROM teams "
                    "WHERE league_id = ? AND username = ? COLLATE NOCASE",
                    (league["id"], login_name),
                ).fetchone()
                if not team:
                    # Backward compatibility for teams without a username.
                    team = conn.execute(
                        "SELECT id, name, owner_name, username, password_hash FROM teams "
                        "WHERE league_id = ? AND name = ? COLLATE NOCASE",
                        (league["id"], login_name),
                    ).fetchone()
        finally:
            conn.close()
        if not team or not verify_password(password, team["password_hash"]):
            record_login_failure(scope, client)
            flash("Invalid username or password.")
            return redirect(url_for("login", league_code=league_code))

        # Transparently upgrade legacy SHA-256 hashes to scrypt.
        if password_needs_rehash(team["password_hash"]):
            conn = get_db()
            try:
                conn.execute(
                    "UPDATE teams SET password_hash = ? WHERE id = ?",
                    (hash_password(password), team["id"]),
                )
                conn.commit()
            finally:
                conn.close()

        clear_login_failures(scope, client)

        # Rotate the session and drop any admin rights from this browser.
        old_session = _session_id()
        if old_session:
            delete_session(old_session)
        flask_session.clear()
        flask_session["session_id"] = create_session(team["id"])
        return redirect(url_for("team_dashboard", league_code=league_code))

    return render_template("login.html", league=league)


@app.route("/l/<league_code>/logout", methods=["POST"])
@login_required
def logout(league_code):
    _validate_csrf()
    session_id = _session_id()
    if session_id:
        delete_session(session_id)
    flask_session.clear()
    flash("Logged out.")
    return redirect(url_for("league_public", league_code=league_code))


# ============================================================
# Team Dashboard (private)
# ============================================================

@app.route("/l/<league_code>/dashboard")
@login_required
def team_dashboard(league_code):
    team = _get_current_team()
    conn = get_db()
    try:
        league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
        _require_team_in_league(team, league)
        my_recaps = conn.execute(
            "SELECT id, week, created_at FROM recaps "
            "WHERE league_id = ? AND created_by = ? "
            "ORDER BY created_at DESC LIMIT 10",
            (league["id"], team["id"])
        ).fetchall()
    finally:
        conn.close()
    return render_template("dashboard.html", team=team, league=league, recaps=my_recaps)


@app.route("/l/<league_code>/password", methods=["POST"])
@login_required
def change_team_password(league_code):
    """Let a logged-in team rotate its own password."""
    team = _get_current_team()
    conn = get_db()
    try:
        league = conn.execute(
            "SELECT * FROM leagues WHERE league_code = ?", (league_code,)
        ).fetchone()
        _require_team_in_league(team, league)
        _validate_csrf()

        current_password = request.form.get("current_password", "")
        new_password = request.form.get("new_password", "")
        confirm_password = request.form.get("confirm_password", "")
        if not current_password or len(current_password) > MAX_PASSWORD_LENGTH:
            flash("Current password is required.")
            return redirect(url_for("team_dashboard", league_code=league_code))
        if not 10 <= len(new_password) <= MAX_PASSWORD_LENGTH:
            flash("New password must be 10-256 characters long.")
            return redirect(url_for("team_dashboard", league_code=league_code))
        if new_password != confirm_password:
            flash("New passwords do not match.")
            return redirect(url_for("team_dashboard", league_code=league_code))

        row = conn.execute(
            "SELECT password_hash FROM teams WHERE id = ? AND league_id = ?",
            (team["id"], league["id"]),
        ).fetchone()
        if not row or not verify_password(current_password, row["password_hash"]):
            flash("Current password is incorrect.")
            return redirect(url_for("team_dashboard", league_code=league_code))

        conn.execute(
            "UPDATE teams SET password_hash = ? WHERE id = ?",
            (hash_password(new_password), team["id"]),
        )
        # Keep this browser signed in, but invalidate every other session.
        session_id = _session_id()
        if session_id:
            conn.execute(
                "DELETE FROM sessions WHERE team_id = ? AND id != ?",
                (team["id"], session_id),
            )
        else:
            conn.execute("DELETE FROM sessions WHERE team_id = ?", (team["id"],))
        conn.commit()
    finally:
        conn.close()

    flash("Password updated. Other devices were logged out.")
    return redirect(url_for("team_dashboard", league_code=league_code))


@app.route("/l/<league_code>/recap/new", methods=["GET", "POST"])
@login_required
def recap_new(league_code):
    team = _get_current_team()
    conn = get_db()
    try:
        league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
        _require_team_in_league(team, league)

        if request.method != "POST":
            return render_template("recap_new.html", team=team, league=league)

        _validate_csrf()
        week = _parse_week(request.form.get("week"))
        if week is None:
            flash("Please enter a valid week (1-22).")
            return redirect(url_for("recap_new", league_code=league_code))
        content = request.form.get("content", "").strip()
        style = normalize_style(request.form.get("style"))
        theme = normalize_theme(request.form.get("theme"))
        if not content:
            flash("Recap content is required.")
            return redirect(url_for("recap_new", league_code=league_code))
        if len(content) > MAX_RECAP_LENGTH:
            flash(f"Recap content is too long (max {MAX_RECAP_LENGTH} characters).")
            return redirect(url_for("recap_new", league_code=league_code))

        conn.execute(
            "INSERT INTO recaps (league_id, week, content, created_by, style, theme) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (league["id"], week, content, team["id"], style, theme)
        )
        conn.commit()
    finally:
        conn.close()

    _auto_backup()
    flash("Recap saved.")
    return redirect(url_for("team_dashboard", league_code=league_code))


# ============================================================
# Recap & Start/Sit generation
# ============================================================

@app.route("/l/<league_code>/recap/generate", methods=["POST"])
@login_required
def recap_generate(league_code):
    """Auto-generate recap for a given week."""
    _validate_csrf()
    team = _get_current_team()
    conn = get_db()
    try:
        league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
        _require_team_in_league(team, league)
    finally:
        conn.close()

    week = _parse_week(request.form.get("week"))
    if week is None:
        flash("Please enter a valid week (1-22).")
        return redirect(url_for("team_dashboard", league_code=league_code))
    style = normalize_style(request.form.get("style"))
    theme = normalize_theme(request.form.get("theme"))

    try:
        from sleeper_recap import generate_recap
        content = generate_recap(
            league["sleeper_league_id"],
            week=week,
            style=style,
            theme=theme,
        )
    except Exception:
        log.exception("Recap generation failed")
        flash("Recap generation failed. Please try again later.")
        return redirect(url_for("team_dashboard", league_code=league_code))

    if len(content) > MAX_RECAP_LENGTH:
        content = content[:MAX_RECAP_LENGTH]

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO recaps (league_id, week, content, created_by, style, theme) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (league["id"], week, content, team["id"], style, theme)
        )
        conn.commit()
    finally:
        conn.close()
    _auto_backup()
    flash("Recap generated successfully.")
    return redirect(url_for("team_dashboard", league_code=league_code))


@app.route("/l/<league_code>/startsit", methods=["POST"])
@login_required
def startsit_advice(league_code):
    """Generate start/sit advice for the team."""
    _validate_csrf()
    team = _get_current_team()
    conn = get_db()
    try:
        league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
        _require_team_in_league(team, league)
    finally:
        conn.close()

    week = _parse_week(request.form.get("week"))
    if week is None:
        flash("Please enter a valid week (1-22).")
        return redirect(url_for("team_dashboard", league_code=league_code))

    try:
        from sleeper_recap import generate_startsit
        content = generate_startsit(
            league["sleeper_league_id"],
            week=week,
            roster_id=team["sleeper_roster_id"],
            team_name=team["name"],
        )
    except Exception:
        log.exception("Start/Sit generation failed")
        flash("Start/Sit generation failed. Please try again later.")
        return redirect(url_for("team_dashboard", league_code=league_code))

    return render_template("startsit.html", team=team, league=league, content=content, week=week)


@app.route("/l/<league_code>/waivers", methods=["POST"])
@login_required
def waiver_advice(league_code):
    """Generate waiver advice for the team."""
    _validate_csrf()
    team = _get_current_team()
    conn = get_db()
    try:
        league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
        _require_team_in_league(team, league)
    finally:
        conn.close()

    week = _parse_week(request.form.get("week"))
    if week is None:
        flash("Please enter a valid week (1-22).")
        return redirect(url_for("team_dashboard", league_code=league_code))

    try:
        from sleeper_recap import generate_waivers
        content = generate_waivers(
            league["sleeper_league_id"],
            week=week,
            roster_id=team["sleeper_roster_id"],
            team_name=team["name"],
        )
    except Exception:
        log.exception("Waiver generation failed")
        flash("Waiver generation failed. Please try again later.")
        return redirect(url_for("team_dashboard", league_code=league_code))

    return render_template("waivers.html", team=team, league=league, content=content, week=week)


@app.route("/l/<league_code>/trade")
@login_required
def trade_advisor(league_code):
    """Trade advisor form: evaluate an offer or look for gap-filling trades."""
    team = _get_current_team()
    conn = get_db()
    try:
        league = conn.execute(
            "SELECT * FROM leagues WHERE league_code = ?", (league_code,)
        ).fetchone()
        _require_team_in_league(team, league)
    finally:
        conn.close()

    week = _parse_week(request.args.get("week"))
    try:
        from sleeper_recap import trade_page_options
        page = trade_page_options(
            league["sleeper_league_id"],
            week=week,
            roster_id=team["sleeper_roster_id"],
            team_name=team["name"],
        )
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("team_dashboard", league_code=league_code))
    except Exception:
        log.exception("Trade advisor page failed")
        flash("Trade advisor data could not be loaded. Please try again later.")
        return redirect(url_for("team_dashboard", league_code=league_code))

    return render_template(
        "trade.html",
        team=team,
        league=league,
        page=page,
        result=None,
        mode=None,
        week=page["week"],
    )


@app.route("/l/<league_code>/trade/evaluate", methods=["POST"])
@login_required
def trade_evaluate(league_code):
    """Evaluate a manually selected offer and suggest counter packages."""
    _validate_csrf()
    team = _get_current_team()
    conn = get_db()
    try:
        league = conn.execute(
            "SELECT * FROM leagues WHERE league_code = ?", (league_code,)
        ).fetchone()
        _require_team_in_league(team, league)
    finally:
        conn.close()

    week = _parse_week(request.form.get("week"))
    if week is None:
        flash("Please enter a valid week (1-22).")
        return redirect(url_for("trade_advisor", league_code=league_code))

    give_ids = request.form.getlist("give")
    get_ids = request.form.getlist("get")
    try:
        from sleeper_recap import generate_trade_advisor
        result = generate_trade_advisor(
            league["sleeper_league_id"],
            week=week,
            roster_id=team["sleeper_roster_id"],
            team_name=team["name"],
            mode="evaluate",
            give_ids=give_ids,
            get_ids=get_ids,
        )
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("trade_advisor", league_code=league_code, week=week))
    except Exception:
        log.exception("Trade evaluation failed")
        flash("Trade evaluation failed. Please try again later.")
        return redirect(url_for("trade_advisor", league_code=league_code, week=week))

    return render_template(
        "trade.html",
        team=team,
        league=league,
        page=None,
        result=result,
        mode="evaluate",
        week=week,
    )


@app.route("/l/<league_code>/trade/gaps", methods=["POST"])
@login_required
def trade_gaps(league_code):
    """Propose trades based on weak or injured positions on my roster."""
    _validate_csrf()
    team = _get_current_team()
    conn = get_db()
    try:
        league = conn.execute(
            "SELECT * FROM leagues WHERE league_code = ?", (league_code,)
        ).fetchone()
        _require_team_in_league(team, league)
    finally:
        conn.close()

    week = _parse_week(request.form.get("week"))
    if week is None:
        flash("Please enter a valid week (1-22).")
        return redirect(url_for("trade_advisor", league_code=league_code))

    try:
        from sleeper_recap import generate_trade_advisor
        result = generate_trade_advisor(
            league["sleeper_league_id"],
            week=week,
            roster_id=team["sleeper_roster_id"],
            team_name=team["name"],
            mode="gaps",
        )
    except ValueError as e:
        flash(str(e))
        return redirect(url_for("trade_advisor", league_code=league_code, week=week))
    except Exception:
        log.exception("Trade proposal generation failed")
        flash("Trade proposal generation failed. Please try again later.")
        return redirect(url_for("trade_advisor", league_code=league_code, week=week))

    return render_template(
        "trade.html",
        team=team,
        league=league,
        page=None,
        result=result,
        mode="gaps",
        week=week,
    )


# ============================================================
# Distribution (WhatsApp, Signal, Webhook, Email, RSS)
# ============================================================

@app.route("/l/<league_code>/share/<int:recap_id>")
def share_recap(league_code, recap_id):
    """Share links for a recap."""
    conn = get_db()
    try:
        league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
        if not league:
            abort(404)
        recap = conn.execute("SELECT * FROM recaps WHERE id = ? AND league_id = ?",
                             (recap_id, league["id"])).fetchone()
        webhooks = conn.execute(
            "SELECT id, webhook_type FROM webhooks WHERE league_id = ?",
            (league["id"],),
        ).fetchall()
    finally:
        conn.close()
    if not recap:
        abort(404)

    recap_url = url_for("league_recap", league_code=league_code,
                        recap_id=recap_id, _external=True)
    recap_text = f"{league['name']} — Week {recap['week']} Recap\n{recap_url}"

    # WhatsApp link (official wa.me)
    wa_text = requests.utils.quote(recap_text)
    whatsapp_url = f"https://wa.me/?text={wa_text}"

    viewer = _get_current_team()
    visible_webhooks = webhooks if viewer and viewer["league_id"] == league["id"] else []
    return render_template("share.html", league=league, recap=recap,
                           recap_url=recap_url, recap_text=recap_text,
                           whatsapp_url=whatsapp_url, webhooks=visible_webhooks,
                           viewer_team=viewer)


@app.route("/l/<league_code>/webhook/<int:recap_id>", methods=["POST"])
def webhook_post(league_code, recap_id):
    """Post a recap to the league's webhooks.

    Requires a team login for this league. Every target is re-validated here so
    that data imported from a backup file can never become an SSRF primitive.
    """
    team = _get_current_team()
    if not team:
        return jsonify({"error": "Team login required."}), 401
    _validate_csrf()

    conn = get_db()
    try:
        league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
        if not league:
            return jsonify({"error": "Not found."}), 404
        if team["league_id"] != league["id"]:
            return jsonify({"error": "Forbidden."}), 403
        recap = conn.execute("SELECT * FROM recaps WHERE id = ? AND league_id = ?",
                             (recap_id, league["id"])).fetchone()
        webhooks = conn.execute("SELECT * FROM webhooks WHERE league_id = ?",
                                (league["id"],)).fetchall()
    finally:
        conn.close()
    if not recap:
        return jsonify({"error": "Not found."}), 404

    # Reuse the rate-limit table to stop a logged-in team from spamming the
    # league's chat channels (10 posts per 5 minutes per team/IP).
    rate_scope = f"webhook:{team['id']}"
    rate_client = _client_key()
    if login_attempts(rate_scope, rate_client, 5) >= 10:
        return jsonify({"error": "Too many posts. Please wait a few minutes."}), 429
    record_login_failure(rate_scope, rate_client)

    limits = {"slack": 3500, "discord": 2000, "telegram": 4000}
    results = []
    seen = set()
    for hook in webhooks[:10]:
        kind = (hook["webhook_type"] or "").strip().lower()
        target, error = _validate_webhook(kind, hook["webhook_url"])
        if error:
            log.warning("Skipping invalid %s webhook: %s", kind, error)
            results.append({"type": kind or "unknown", "status": "skipped"})
            continue
        if (kind, target) in seen:
            continue
        seen.add((kind, target))
        if kind not in limits:
            results.append({"type": kind or "unknown", "status": "skipped"})
            continue

        text = (recap["content"] or "")[:limits[kind]]
        try:
            if kind == "telegram":
                token, chat_id = target.split("/", 1)
                api_url = f"https://api.telegram.org/bot{token}/sendMessage"
                payload = {"chat_id": chat_id, "text": text}
            elif kind == "slack":
                api_url = target
                payload = {"text": text}
            else:  # discord
                api_url = target
                payload = {"content": text}

            # No redirects and no response-body reads: the only host contacted
            # is the allowlisted provider, and the response cannot be reflected.
            resp = requests.post(api_url, json=payload, timeout=10,
                                 allow_redirects=False, stream=True,
                                 proxies={"http": None, "https": None})
            status = resp.status_code
            resp.close()
            results.append({"type": kind, "status": status})
        except Exception:
            # Never log the exception text: requests includes the full webhook
            # URL (and therefore the provider secret) in connection errors.
            log.warning("Webhook delivery failed (%s)", kind)
            results.append({"type": kind, "status": "error"})

    return jsonify({"results": results})


# ============================================================
# RSS Feed
# ============================================================

@app.route("/l/<league_code>/feed.rss")
def league_rss(league_code):
    """RSS feed for a league."""
    conn = get_db()
    try:
        league = conn.execute("SELECT * FROM leagues WHERE league_code = ?", (league_code,)).fetchone()
        if not league:
            abort(404)
        recaps = conn.execute(
            "SELECT id, week, substr(content, 1, 500) AS content, "
            "created_at, style, theme FROM recaps "
            "WHERE league_id = ? ORDER BY created_at DESC LIMIT 50",
            (league["id"],)
        ).fetchall()
    finally:
        conn.close()

    base_url = url_for("league_public", league_code=league_code, _external=True)

    rss_items = []
    for recap in recaps:
        recap_url = url_for("league_recap", league_code=league_code,
                            recap_id=recap["id"], _external=True)
        style_key = recap["style"] or "nfl_reporter"
        theme_key = recap["theme"] or "none"
        title = f"Week {recap['week']} Recap"
        if style_key in RECAP_STYLES:
            title += f" · {RECAP_STYLES[style_key]['name']}"
        if theme_key != "none" and theme_key in RECAP_THEMES:
            title += f" · {RECAP_THEMES[theme_key]['name']}"
        rss_items.append(f"""
        <item>
            <title>{xml_escape(title)}</title>
            <link>{xml_escape(recap_url)}</link>
            <guid>{xml_escape(recap_url)}</guid>
            <pubDate>{xml_escape(str(recap['created_at'] or ''))}</pubDate>
            <description>{xml_escape((recap['content'] or '')[:200])}...</description>
        </item>""")

    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
    <channel>
        <title>{xml_escape(league['name'])} — Recap Feed</title>
        <link>{xml_escape(base_url)}</link>
        <description>{xml_escape('Weekly recaps for ' + league['name'])}</description>
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
