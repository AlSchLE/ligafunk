"""Tests for Sleeper team import, username login and password rotation."""

import json
import os
import re
import secrets
import tempfile
import time
import unittest
import uuid
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(_TMP.name, "test_team_import.db")
os.environ["SECRET_KEY"] = secrets.token_hex(32)
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)

import app as app_module  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402
from database import get_db, hash_password, init_db  # noqa: E402
from sleeper_recap import fetch_league_participants  # noqa: E402

# The app may already have been imported by another test module.
config.DATABASE_PATH = os.environ["DATABASE_PATH"]
database.config.DATABASE_PATH = os.environ["DATABASE_PATH"]

CSRF = "a" * 64

PARTICIPANTS = [
    {
        "roster_id": "1",
        "name": "Alpha Team",
        "owner_name": "Alice",
        "username": "alice",
    },
    {
        "roster_id": "2",
        "name": "Beta Team",
        "owner_name": "Bob",
        "username": "bob",
    },
]


class TeamImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        app_module.app.config.update(TESTING=True)
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        _TMP.cleanup()

    def setUp(self):
        self.client = app_module.app.test_client()
        self.league_code = f"IMPORT{uuid.uuid4().hex[:8].upper()}"
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO leagues (name, sleeper_league_id, league_code) VALUES (?, ?, ?)",
                ("Import League", f"9{uuid.uuid4().int % 10**17:017d}", self.league_code),
            )
            self.league_id = conn.execute(
                "SELECT id FROM leagues WHERE league_code = ?", (self.league_code,)
            ).fetchone()["id"]
            conn.commit()
        finally:
            conn.close()

    def tearDown(self):
        conn = get_db()
        try:
            conn.execute(
                "DELETE FROM sessions WHERE team_id IN "
                "(SELECT id FROM teams WHERE league_id = ?)",
                (self.league_id,),
            )
            conn.execute("DELETE FROM teams WHERE league_id = ?", (self.league_id,))
            conn.execute("DELETE FROM leagues WHERE id = ?", (self.league_id,))
            conn.commit()
        finally:
            conn.close()

    def _admin_session(self):
        with self.client.session_transaction() as sess:
            sess["admin_authenticated"] = True
            sess["admin_authenticated_at"] = int(time.time())
            sess["_csrf_token"] = CSRF

    def _team_session(self, team_id):
        self.client = app_module.app.test_client()
        sid = database.create_session(team_id)
        with self.client.session_transaction() as sess:
            sess["session_id"] = sid
            sess["_csrf_token"] = CSRF
        return sid

    def _create_team(self, name="Existing Team", username=None, roster_id=None,
                     password="existing-password-123"):
        conn = get_db()
        try:
            cursor = conn.execute(
                "INSERT INTO teams "
                "(league_id, name, owner_name, sleeper_roster_id, username, password_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (self.league_id, name, "Existing Owner", roster_id, username,
                 hash_password(password)),
            )
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()

    def test_participant_username_comes_from_sleeper_user(self):
        with patch("sleeper_recap.SleeperClient") as client_class:
            client = client_class.return_value.__enter__.return_value
            client.users.return_value = [
                {
                    "user_id": "u1",
                    "username": "alice",
                    "display_name": "Alice",
                    "metadata": {"team_name": "Alpha Team"},
                }
            ]
            client.rosters.return_value = [{"roster_id": 1, "owner_id": "u1"}]
            participants = fetch_league_participants("123456789")

        self.assertEqual(participants, [{
            "roster_id": "1",
            "name": "Alpha Team",
            "owner_name": "Alice",
            "username": "alice",
        }])

    def test_import_creates_teams_and_shows_one_time_credentials(self):
        self._admin_session()
        with patch(
            "sleeper_recap.fetch_league_participants", return_value=PARTICIPANTS
        ), patch("app._auto_backup"):
            response = self.client.post(
                "/admin",
                data={
                    "_csrf_token": CSRF,
                    "action": "import_sleeper_teams",
                    "league_id": str(self.league_id),
                },
            )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Teams created from Sleeper", html)
        self.assertIn("@alice", html)
        self.assertIn("@bob", html)
        self.assertIn("Copy all for Sleeper PM", html)

        conn = get_db()
        try:
            teams = conn.execute(
                "SELECT name, owner_name, username, password_hash FROM teams "
                "WHERE league_id = ? ORDER BY username",
                (self.league_id,),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual([t["username"] for t in teams], ["alice", "bob"])
        self.assertEqual([t["name"] for t in teams], ["Alpha Team", "Beta Team"])
        for team in teams:
            self.assertNotIn("password", team["password_hash"])

        match = re.search(r"Sleeper: @alice\nStartpasswort: (\S+)", html)
        self.assertIsNotNone(match)
        password = match.group(1)

        # The generated credentials can be used immediately.
        self.client = app_module.app.test_client()
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = CSRF
        login = self.client.post(
            f"/l/{self.league_code}/login",
            data={"_csrf_token": CSRF, "username": "alice", "password": password},
        )
        self.assertEqual(login.status_code, 302)
        self.assertIn("/dashboard", login.headers["Location"])

    def test_import_links_existing_team_and_does_not_duplicate(self):
        team_id = self._create_team(
            name="Alpha Team", username=None, roster_id="1",
            password="existing-password-123",
        )
        self._admin_session()
        with patch(
            "sleeper_recap.fetch_league_participants", return_value=PARTICIPANTS
        ), patch("app._auto_backup"):
            response = self.client.post(
                "/admin",
                data={
                    "_csrf_token": CSRF,
                    "action": "import_sleeper_teams",
                    "league_id": str(self.league_id),
                },
            )

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Linked existing teams", html)
        self.assertIn("Alpha Team", html)

        conn = get_db()
        try:
            teams = conn.execute(
                "SELECT id, username, password_hash FROM teams WHERE league_id = ?",
                (self.league_id,),
            ).fetchall()
        finally:
            conn.close()
        self.assertEqual(len(teams), 2)
        alpha = next(t for t in teams if t["id"] == team_id)
        self.assertEqual(alpha["username"], "alice")

        # The existing password was not replaced by the import.
        self.client = app_module.app.test_client()
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = CSRF
        login = self.client.post(
            f"/l/{self.league_code}/login",
            data={
                "_csrf_token": CSRF,
                "username": "alice",
                "password": "existing-password-123",
            },
        )
        self.assertEqual(login.status_code, 302)
        self.assertIn("/dashboard", login.headers["Location"])

    def test_login_page_renders_username_field(self):
        response = self.client.get(f"/l/{self.league_code}/login")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('name="username"', html)
        self.assertIn("Sleeper username", html)

    def test_login_accepts_at_prefix_and_case_insensitive_username(self):
        self._create_team(
            name="Case Team", username="CaseUser", password="case-password-123"
        )
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = CSRF
        response = self.client.post(
            f"/l/{self.league_code}/login",
            data={
                "_csrf_token": CSRF,
                "username": "@caseuser",
                "password": "case-password-123",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.headers["Location"])

    def test_team_can_login_with_legacy_team_name(self):
        self._create_team(name="Legacy Team", username="legacy_user",
                          password="legacy-password-123")
        with self.client.session_transaction() as sess:
            sess["_csrf_token"] = CSRF
        response = self.client.post(
            f"/l/{self.league_code}/login",
            data={
                "_csrf_token": CSRF,
                "username": "Legacy Team",
                "password": "legacy-password-123",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.headers["Location"])

    def test_password_change_rotates_hash_and_revokes_other_sessions(self):
        team_id = self._create_team(
            name="Password Team", username="password_user",
            password="old-password-123",
        )
        current_sid = self._team_session(team_id)
        other_sid = database.create_session(team_id)

        response = self.client.post(
            f"/l/{self.league_code}/password",
            data={
                "_csrf_token": CSRF,
                "current_password": "old-password-123",
                "new_password": "new-password-456",
                "confirm_password": "new-password-456",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/dashboard", response.headers["Location"])

        # The current browser stays signed in.
        dashboard = self.client.get(f"/l/{self.league_code}/dashboard")
        self.assertEqual(dashboard.status_code, 200)

        # The other session was revoked.
        other_client = app_module.app.test_client()
        with other_client.session_transaction() as sess:
            sess["session_id"] = other_sid
        other_dashboard = other_client.get(f"/l/{self.league_code}/dashboard")
        self.assertEqual(other_dashboard.status_code, 302)
        self.assertIn("/login", other_dashboard.headers["Location"])

        # The old password no longer works, the new one does.
        old_client = app_module.app.test_client()
        with old_client.session_transaction() as sess:
            sess["_csrf_token"] = CSRF
        old_login = old_client.post(
            f"/l/{self.league_code}/login",
            data={
                "_csrf_token": CSRF,
                "username": "password_user",
                "password": "old-password-123",
            },
        )
        self.assertEqual(old_login.status_code, 302)
        self.assertIn("/login", old_login.headers["Location"])

        new_client = app_module.app.test_client()
        with new_client.session_transaction() as sess:
            sess["_csrf_token"] = CSRF
        new_login = new_client.post(
            f"/l/{self.league_code}/login",
            data={
                "_csrf_token": CSRF,
                "username": "password_user",
                "password": "new-password-456",
            },
        )
        self.assertEqual(new_login.status_code, 302)
        self.assertIn("/dashboard", new_login.headers["Location"])

        self.assertIsNotNone(current_sid)

    def test_auto_backup_uses_dated_ligafunk_filename(self):
        fixed_now = datetime(2026, 10, 29, 14, 30, 0)
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            app_module, "BACKUP_DIR", Path(tmp)
        ), patch.object(app_module, "datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_now
            app_module._auto_backup()

            backup = Path(tmp) / "ligafunk_291026.json"
            self.assertTrue(backup.is_file())
            data = json.loads(backup.read_text(encoding="utf-8"))
            self.assertEqual(data["timestamp"], fixed_now.isoformat())
            self.assertTrue(any(
                league["league_code"] == self.league_code
                for league in data["leagues"]
            ))

    def test_admin_backup_download_has_dated_filename(self):
        self._admin_session()
        response = self.client.get("/admin/backup")

        self.assertEqual(response.status_code, 200)
        self.assertRegex(
            response.headers.get("Content-Disposition", ""),
            r'^attachment; filename="ligafunk_\d{6}\.json"$',
        )
        self.assertRegex(
            response.get_json()["exported_at"],
            r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}",
        )

    def test_download_backup_accepts_new_and_legacy_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            backup_dir = Path(tmp)
            new_backup = backup_dir / "ligafunk_291026.json"
            legacy_backup = backup_dir / "sleeper_backup_20261029_143000.json"
            new_backup.write_text('{"leagues": []}', encoding="utf-8")
            legacy_backup.write_text('{"leagues": []}', encoding="utf-8")

            self._admin_session()
            with patch.object(app_module, "BACKUP_DIR", backup_dir):
                for filename in (new_backup.name, legacy_backup.name):
                    response = self.client.get(
                        f"/admin/download-backup/{filename}"
                    )
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(filename, response.headers["Content-Disposition"])
                    self.assertEqual(response.get_json(), {"leagues": []})
                    response.close()

                missing = self.client.get(
                    "/admin/download-backup/ligafunk_010126.json"
                )
                self.assertEqual(missing.status_code, 404)
                invalid = self.client.get(
                    "/admin/download-backup/not-a-backup.json"
                )
                self.assertEqual(invalid.status_code, 404)


if __name__ == "__main__":
    unittest.main()
