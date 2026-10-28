"""Route-level trade-advisor tests: auth, CSRF and team isolation."""

import os
import secrets
import tempfile
import unittest
from unittest.mock import patch

_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(_TMP.name, "test_trade_routes.db")
os.environ["SECRET_KEY"] = secrets.token_hex(32)
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)

import app as app_module  # noqa: E402
from database import get_db, hash_password, init_db  # noqa: E402


_TEST_PASSWORD_HASH = hash_password(secrets.token_urlsafe(24))


PAGE = {
    "week": 9,
    "my_team": "My Team",
    "my_roster_id": "1",
    "my_players": [],
    "other_teams": [],
    "recent_trades": [],
}

RESULT = {
    "mode": "evaluate",
    "week": 9,
    "league_name": "Test League",
    "my_team": "My Team",
    "partner_team": "Partner",
    "verdict": "Fair",
}


class TradeRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO leagues (name, sleeper_league_id, league_code) VALUES (?, ?, ?)",
                ("Test League", "111111111111111111", "TRADECODE1"),
            )
            conn.execute(
                "INSERT INTO leagues (name, sleeper_league_id, league_code) VALUES (?, ?, ?)",
                ("Other League", "222222222222222222", "TRADECODE2"),
            )
            league1 = conn.execute(
                "SELECT id FROM leagues WHERE league_code = ?", ("TRADECODE1",)
            ).fetchone()["id"]
            league2 = conn.execute(
                "SELECT id FROM leagues WHERE league_code = ?", ("TRADECODE2",)
            ).fetchone()["id"]
            conn.execute(
                "INSERT INTO teams (league_id, name, owner_name, sleeper_roster_id, password_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (league1, "My Team", "Owner", "1", _TEST_PASSWORD_HASH),
            )
            conn.execute(
                "INSERT INTO teams (league_id, name, owner_name, sleeper_roster_id, password_hash) "
                "VALUES (?, ?, ?, ?, ?)",
                (league2, "Other Team", "Other", "2", _TEST_PASSWORD_HASH),
            )
            conn.commit()
            cls.team1_id = conn.execute(
                "SELECT id FROM teams WHERE league_id = ?", (league1,)
            ).fetchone()["id"]
            cls.team2_id = conn.execute(
                "SELECT id FROM teams WHERE league_id = ?", (league2,)
            ).fetchone()["id"]
        finally:
            conn.close()
        app_module.app.config.update(TESTING=True)
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        _TMP.cleanup()

    def _login(self, team_id=None):
        from database import create_session

        self.client = app_module.app.test_client()
        sid = create_session(team_id or self.team1_id)
        with self.client.session_transaction() as sess:
            sess["session_id"] = sid
            sess["_csrf_token"] = "a" * 64
        return "a" * 64

    def test_requires_login(self):
        self.client = app_module.app.test_client()
        response = self.client.get("/l/TRADECODE1/trade")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/l/TRADECODE1/login", response.headers["Location"])

    def test_authenticated_page_renders(self):
        self._login()
        with patch("sleeper_recap.trade_page_options", return_value=PAGE), patch(
            "app.render_template", return_value="ok"
        ):
            response = self.client.get("/l/TRADECODE1/trade")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_data(as_text=True), "ok")
        self.assertIn("no-store", response.headers.get("Cache-Control", ""))

    def test_post_without_csrf_is_rejected(self):
        self._login()
        response = self.client.post(
            "/l/TRADECODE1/trade/evaluate",
            data={"week": "9", "give": ["r1"], "get": ["w1"]},
        )
        self.assertEqual(response.status_code, 403)

    def test_post_with_csrf_uses_advisor(self):
        token = self._login()
        with patch(
            "sleeper_recap.generate_trade_advisor", return_value=RESULT
        ) as advisor, patch("app.render_template", return_value="ok"):
            response = self.client.post(
                "/l/TRADECODE1/trade/evaluate",
                data={
                    "_csrf_token": token,
                    "week": "9",
                    "give": ["r1"],
                    "get": ["w1"],
                },
            )
        self.assertEqual(response.status_code, 200)
        advisor.assert_called_once()
        self.assertEqual(advisor.call_args.kwargs["mode"], "evaluate")

    def test_cannot_trade_in_another_league(self):
        self._login(self.team2_id)
        response = self.client.get("/l/TRADECODE1/trade")
        self.assertEqual(response.status_code, 403)

    def test_invalid_week_redirects(self):
        token = self._login()
        response = self.client.post(
            "/l/TRADECODE1/trade/gaps",
            data={"_csrf_token": token, "week": "99"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn("/l/TRADECODE1/trade", response.headers["Location"])


if __name__ == "__main__":
    unittest.main()
