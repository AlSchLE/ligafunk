"""Tests for team-scoped standings, matchup, record-book and playoff features."""

import os
import secrets
import tempfile
import time
import unittest
from unittest.mock import patch

_TMP = tempfile.TemporaryDirectory()
os.environ["DATABASE_PATH"] = os.path.join(_TMP.name, "test_team_features.db")
os.environ["SECRET_KEY"] = secrets.token_hex(32)
os.environ.pop("RENDER", None)
os.environ.pop("FORCE_HTTPS", None)

import app as app_module  # noqa: E402
import config  # noqa: E402
import database  # noqa: E402
import team_features  # noqa: E402
from database import create_session, get_db, hash_password, init_db  # noqa: E402

config.DATABASE_PATH = os.environ["DATABASE_PATH"]
database.config.DATABASE_PATH = os.environ["DATABASE_PATH"]

CSRF = "a" * 64
PASSWORD_HASH = hash_password(secrets.token_urlsafe(24))


def _standings_row(roster_id, name, wins, losses, ties, pf, pa, *, is_me=False,
                   rank=1, power_rank=1):
    games = wins + losses + ties
    return {
        "roster_id": str(roster_id),
        "name": name,
        "wins": wins,
        "losses": losses,
        "ties": ties,
        "games": games,
        "win_pct": (wins + 0.5 * ties) / games if games else 0.0,
        "pf": pf,
        "pa": pa,
        "ppg": pf / games if games else 0.0,
        "all_play_wins": 4,
        "all_play_losses": 2,
        "all_play_ties": 0,
        "all_play_pct": 0.667,
        "expected_wins": 1.4,
        "luck": 0.6,
        "last5_pct": 0.6,
        "form": ["W", "L", "W"],
        "streak": "W1",
        "waiver_remaining": 90.0,
        "is_me": is_me,
        "rank": rank,
        "playoff_spot": rank <= 2,
        "games_back": 0.0,
        "power_score": 0.712,
        "power_rank": power_rank,
    }


ROW_ME = _standings_row(1, "Alpha Team", 2, 0, 0, 215.0, 185.0,
                        is_me=True, rank=1, power_rank=1)
ROW_OTHER = _standings_row(2, "Beta Team", 0, 2, 0, 185.0, 205.0,
                           rank=2, power_rank=2)

PAGE_STANDINGS = {
    "league_name": "Test League",
    "season": 2026,
    "weeks_played": 2,
    "current_week": 3,
    "playoff_teams": 2,
    "playoff_week_start": 4,
    "games_left": 1,
    "standings": [ROW_ME, ROW_OTHER],
    "power": [ROW_ME, ROW_OTHER],
    "my": ROW_ME,
    "my_power": ROW_ME,
}

STARTERS = [
    {
        "pid": "p1",
        "slot": "QB",
        "name": "Player One",
        "pos": "QB",
        "team": "KC",
        "injury": "",
        "points": 18.2,
        "proj": 20.1,
    },
    {
        "pid": "p2",
        "slot": "RB",
        "name": "Player Two",
        "pos": "RB",
        "team": "SF",
        "injury": "Q",
        "points": 0.0,
        "proj": 12.4,
    },
]

SIDE_ME = {
    "roster_id": "1",
    "name": "Alpha Team",
    "record": "2-0",
    "points": 18.2,
    "projected": 120.5,
    "projection_source": "Sleeper projections",
    "starters": STARTERS,
    "remaining": [STARTERS[1]],
    "bench_points": 24.3,
    "bench_top": {"name": "Bench Guy", "points": 15.0},
    "is_me": True,
    "win_prob": 0.63,
}

SIDE_OTHER = {
    "roster_id": "2",
    "name": "Beta Team",
    "record": "0-2",
    "points": 0.0,
    "projected": 110.0,
    "projection_source": "Season average",
    "starters": [],
    "remaining": [],
    "bench_points": 12.0,
    "bench_top": None,
    "is_me": False,
    "win_prob": 0.37,
}

PAGE_MATCHUP = {
    "week": 3,
    "current_week": 3,
    "completed_week": 2,
    "is_final": False,
    "my_pair": {
        "matchup_id": 1,
        "team_a": SIDE_ME,
        "team_b": SIDE_OTHER,
        "is_me": True,
    },
    "other_pairs": [],
    "pairs": [],
    "projection_note": True,
}

AWARD = {
    "key": "highest",
    "label": "Highest score",
    "emoji": "🚀",
    "week": 2,
    "team": "Alpha Team",
    "opponent": "Beta Team",
    "value": "120.50",
    "detail": "vs Beta Team",
    "is_me": True,
}

GAME = {
    "week": 2,
    "points": 120.5,
    "opponent": "Beta Team",
}

PAGE_RECORDS = {
    "scope": "season",
    "season": 2026,
    "weeks_played": 2,
    "latest_week": 2,
    "weekly_awards": [AWARD],
    "my_awards": [AWARD],
    "my_award_count": 1,
    "records": [
        {
            "key": "highest",
            "label": "Highest score",
            "emoji": "🚀",
            "team": "Alpha Team",
            "opponent": "Beta Team",
            "week": 2,
            "value": "120.50",
            "detail": "Week 2 vs Beta Team",
            "is_me": True,
        }
    ],
    "my": ROW_ME,
    "my_best": GAME,
    "my_worst": GAME,
    "head_to_head": [
        {
            "opponent_id": "2",
            "opponent": "Beta Team",
            "wins": 1,
            "losses": 1,
            "ties": 0,
            "games": 2,
            "points_for": 215.0,
            "points_against": 205.0,
            "last_week": 2,
            "last_result": "L",
        }
    ],
    "all_time": None,
}

PAGE_RECORDS_ALL = {
    **PAGE_RECORDS,
    "scope": "all",
    "all_time": {
        "seasons": [2025, 2026],
        "games_played": 30,
        "records": [
            {
                "key": "highest",
                "label": "Highest score",
                "emoji": "🚀",
                "team": "Old Team Name",
                "owner_name": "alice",
                "opponent": "Beta Team",
                "season": 2025,
                "week": 3,
                "value": "155.20",
                "detail": "Week 3 vs Beta Team",
                "is_me": True,
            }
        ],
        "my": {
            "wins": 18,
            "losses": 10,
            "ties": 0,
            "games": 28,
            "pf": 3200.5,
            "pa": 3000.2,
            "award_count": 4,
            "seasons": [2025, 2026],
            "best": {"season": 2025, "week": 3, "points": 155.2},
            "worst": {"season": 2026, "week": 1, "points": 70.1},
        },
        "my_awards": [
            {
                "key": "highest",
                "label": "Highest score",
                "emoji": "🚀",
                "week": 3,
                "season": 2025,
                "team": "Old Team Name",
                "opponent": "Beta Team",
                "value": "155.20",
                "detail": "vs Beta Team",
                "is_me": True,
            }
        ],
        "championships": [
            {
                "season": 2025,
                "champion": "Old Team Name",
                "owner_name": "alice",
                "is_me": True,
            }
        ],
    },
}

PLAYOFF_ME = {
    **ROW_ME,
    "playoff_prob": 0.82,
    "avg_seed": 1.4,
    "seed_distribution": [
        {"seed": 1, "count": 1200, "pct": 0.6},
        {"seed": 2, "count": 440, "pct": 0.22},
    ],
}
PLAYOFF_OTHER = {
    **ROW_OTHER,
    "playoff_prob": 0.18,
    "avg_seed": 1.8,
    "seed_distribution": [
        {"seed": 1, "count": 800, "pct": 0.4},
        {"seed": 2, "count": 1560, "pct": 0.78},
    ],
}

PAGE_PLAYOFFS = {
    "season": 2026,
    "current_week": 3,
    "playoff_week_start": 4,
    "playoff_teams": 2,
    "remaining_weeks": [3],
    "simulations": 2000,
    "my": PLAYOFF_ME,
    "my_remaining": [
        {
            "week": 3,
            "opponent": "Beta Team",
            "opponent_record": "0-2",
            "win_prob": 0.63,
        }
    ],
    "league_rows": [PLAYOFF_ME, PLAYOFF_OTHER],
    "season_complete": False,
    "schedule_available": True,
    "note": "",
}


class TeamFeatureRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO leagues (name, sleeper_league_id, league_code) VALUES (?, ?, ?)",
                ("Feature League", "333333333333333333", "FEATCODE1"),
            )
            conn.execute(
                "INSERT INTO leagues (name, sleeper_league_id, league_code) VALUES (?, ?, ?)",
                ("Other Feature League", "444444444444444444", "FEATCODE2"),
            )
            league1 = conn.execute(
                "SELECT id FROM leagues WHERE league_code = ?", ("FEATCODE1",)
            ).fetchone()["id"]
            league2 = conn.execute(
                "SELECT id FROM leagues WHERE league_code = ?", ("FEATCODE2",)
            ).fetchone()["id"]
            conn.execute(
                "INSERT INTO teams (league_id, name, owner_name, sleeper_roster_id, username, password_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (league1, "Alpha Team", "Alice", "1", "alice-feature", PASSWORD_HASH),
            )
            conn.execute(
                "INSERT INTO teams (league_id, name, owner_name, sleeper_roster_id, username, password_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (league2, "Other Team", "Bob", "2", "bob-feature", PASSWORD_HASH),
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

    @classmethod
    def tearDownClass(cls):
        _TMP.cleanup()

    def _login(self, team_id=None):
        self.client = app_module.app.test_client()
        sid = create_session(team_id or self.team1_id)
        with self.client.session_transaction() as sess:
            sess["session_id"] = sid
            sess["_csrf_token"] = CSRF
        return CSRF

    def test_requires_login_for_all_feature_pages(self):
        for path in ("standings", "matchup", "records", "playoffs"):
            self.client = app_module.app.test_client()
            response = self.client.get(f"/l/FEATCODE1/{path}")
            self.assertEqual(response.status_code, 302)
            self.assertIn("/l/FEATCODE1/login", response.headers["Location"])

    def test_authenticated_pages_render(self):
        self._login()
        with patch("team_features.get_standings", return_value=PAGE_STANDINGS), \
                patch("team_features.get_matchup_center", return_value=PAGE_MATCHUP), \
                patch("team_features.get_record_book", return_value=PAGE_RECORDS), \
                patch("team_features.get_playoff_odds", return_value=PAGE_PLAYOFFS):
            standings = self.client.get("/l/FEATCODE1/standings")
            matchup = self.client.get("/l/FEATCODE1/matchup")
            records = self.client.get("/l/FEATCODE1/records")
            playoffs = self.client.get("/l/FEATCODE1/playoffs")

        self.assertEqual(standings.status_code, 200)
        self.assertIn("Standings &amp; power", standings.get_data(as_text=True))
        self.assertIn("Alpha Team", standings.get_data(as_text=True))
        self.assertEqual(matchup.status_code, 200)
        self.assertIn("Your matchup", matchup.get_data(as_text=True))
        self.assertIn("63% win chance", matchup.get_data(as_text=True))
        self.assertEqual(records.status_code, 200)
        self.assertIn("Record book &amp; awards", records.get_data(as_text=True))
        self.assertIn("Highest score", records.get_data(as_text=True))
        self.assertEqual(playoffs.status_code, 200)
        self.assertIn("Playoff odds", playoffs.get_data(as_text=True))
        self.assertIn("82.0%", playoffs.get_data(as_text=True))

    def test_all_time_scope_requests_history_and_uses_owner_names(self):
        self._login()
        with patch(
            "team_features.get_record_book", return_value=PAGE_RECORDS_ALL
        ) as book:
            response = self.client.get("/l/FEATCODE1/records?scope=all")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(book.call_args.kwargs["include_history"])
        html = response.get_data(as_text=True)
        self.assertIn("All-time record book", html)
        self.assertIn("Old Team Name", html)
        self.assertIn("Champions", html)
        self.assertIn("alice", html)

    def test_cannot_view_another_leagues_feature_page(self):
        self._login(self.team2_id)
        response = self.client.get("/l/FEATCODE1/standings")
        self.assertEqual(response.status_code, 403)

    def test_invalid_week_redirects_to_matchup(self):
        self._login()
        response = self.client.get("/l/FEATCODE1/matchup?week=99")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/l/FEATCODE1/matchup", response.headers["Location"])

    def test_dashboard_shows_feature_cards(self):
        self._login()
        response = self.client.get("/l/FEATCODE1/dashboard")
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        for label in ("Standings &amp; power", "Matchup center", "Record book", "Playoff odds"):
            self.assertIn(label, html)


class TeamFeatureCalculationTests(unittest.TestCase):
    def test_standings_all_play_and_power(self):
        league = {"settings": {"waiver_budget": 100}}
        rosters = [
            {
                "roster_id": "1",
                "settings": {
                    "wins": 2, "losses": 0, "ties": 0,
                    "fpts": 215, "fpts_decimal": 0,
                    "fpts_against": 185, "fpts_against_decimal": 0,
                    "waiver_budget_used": 10,
                },
            },
            {
                "roster_id": "2",
                "settings": {
                    "wins": 1, "losses": 1, "ties": 0,
                    "fpts": 185, "fpts_decimal": 0,
                    "fpts_against": 205, "fpts_against_decimal": 0,
                },
            },
            {
                "roster_id": "3",
                "settings": {
                    "wins": 1, "losses": 1, "ties": 0,
                    "fpts": 195, "fpts_decimal": 0,
                    "fpts_against": 195, "fpts_against_decimal": 0,
                },
            },
            {
                "roster_id": "4",
                "settings": {
                    "wins": 0, "losses": 2, "ties": 0,
                    "fpts": 185, "fpts_decimal": 0,
                    "fpts_against": 195, "fpts_against_decimal": 0,
                },
            },
        ]
        names = {"1": "Alpha", "2": "Beta", "3": "Gamma", "4": "Delta"}

        def row(rid, mid, points):
            return {
                "roster_id": rid,
                "matchup_id": mid,
                "points": points,
                "starters": [],
                "starters_points": [],
                "players": [],
                "players_points": {},
            }

        weeks = {
            1: [row("1", 1, 110), row("2", 1, 90),
                row("3", 2, 100), row("4", 2, 95)],
            2: [row("1", 1, 105), row("2", 1, 95),
                row("3", 2, 95), row("4", 2, 90)],
        }

        standings, power = team_features._standings_rows(
            league, rosters, names, weeks, {}, "1", 2
        )
        me = next(row for row in standings if row["is_me"])
        self.assertEqual(me["wins"], 2)
        self.assertEqual(me["all_play_wins"], 6)
        self.assertAlmostEqual(me["expected_wins"], 2.0)
        self.assertAlmostEqual(me["luck"], 0.0)
        self.assertEqual(me["waiver_remaining"], 90.0)
        self.assertEqual(power[0]["roster_id"], "1")

    def test_all_time_standings_follow_owner_across_renames(self):
        games = [
            {
                "season": 2025, "week": 1, "roster_id": "1",
                "owner_id": "u1", "owner_name": "alice",
                "team": "Old Team Name", "opponent": "Beta",
                "points": 100.0, "opponent_points": 90.0, "result": "W",
            },
            {
                "season": 2026, "week": 1, "roster_id": "7",
                "owner_id": "u1", "owner_name": "alice",
                "team": "Renamed Team", "opponent": "Gamma",
                "points": 110.0, "opponent_points": 100.0, "result": "W",
            },
            {
                "season": 2025, "week": 1, "roster_id": "2",
                "owner_id": "u2", "owner_name": "bob",
                "team": "Beta", "opponent": "Old Team Name",
                "points": 90.0, "opponent_points": 100.0, "result": "L",
            },
        ]
        owners = {
            "u1": {"username": "alice", "display_name": "Alice"},
            "u2": {"username": "bob", "display_name": "Bob"},
        }
        rows = team_features._all_time_standings(games, owners, "u1")
        alice = next(row for row in rows if row["is_me"])
        self.assertEqual(alice["wins"], 2)
        self.assertEqual(alice["pf"], 210.0)
        self.assertEqual(alice["name"], "alice")

    def test_win_probability_is_symmetric_and_clamped(self):
        self.assertAlmostEqual(team_features._win_probability(100, 100), 0.5)
        self.assertGreater(team_features._win_probability(130, 100), 0.5)
        self.assertLess(team_features._win_probability(100, 130), 0.5)
        self.assertGreaterEqual(team_features._win_probability(1000, 0), 0.05)
        self.assertLessEqual(team_features._win_probability(1000, 0), 0.95)

    def test_weekly_awards_cover_league_storylines(self):
        games = [
            {
                "week": 2, "team": "Alpha", "opponent": "Beta",
                "points": 120.0, "opponent_points": 100.0, "result": "W",
                "abs_margin": 20.0, "combined": 220.0,
                "bench_points": 30.0, "bench_player": "Bench A", "is_me": True,
            },
            {
                "week": 2, "team": "Beta", "opponent": "Alpha",
                "points": 100.0, "opponent_points": 120.0, "result": "L",
                "abs_margin": 20.0, "combined": 220.0,
                "bench_points": 10.0, "bench_player": "Bench B", "is_me": False,
            },
            {
                "week": 2, "team": "Gamma", "opponent": "Delta",
                "points": 90.0, "opponent_points": 89.0, "result": "W",
                "abs_margin": 1.0, "combined": 179.0,
                "bench_points": 12.0, "bench_player": "Bench C", "is_me": False,
            },
            {
                "week": 2, "team": "Delta", "opponent": "Gamma",
                "points": 89.0, "opponent_points": 90.0, "result": "L",
                "abs_margin": 1.0, "combined": 179.0,
                "bench_points": 5.0, "bench_player": "Bench D", "is_me": False,
            },
        ]
        awards = team_features._weekly_awards(2, games)
        labels = {award["label"] for award in awards}
        self.assertIn("Highest score", labels)
        self.assertIn("Biggest blowout", labels)
        self.assertIn("Bench MVP", labels)
        self.assertIn("Bad beat", labels)
        highest = next(award for award in awards if award["label"] == "Highest score")
        self.assertEqual(highest["team"], "Alpha")
        self.assertTrue(highest["is_me"])

    def test_head_to_head_aggregates_opponents(self):
        games = [
            {
                "week": 1, "opponent_id": "2", "opponent": "Beta",
                "points": 110.0, "opponent_points": 100.0, "result": "W",
            },
            {
                "week": 2, "opponent_id": "2", "opponent": "Beta",
                "points": 95.0, "opponent_points": 105.0, "result": "L",
            },
            {
                "week": 3, "opponent_id": "3", "opponent": "Gamma",
                "points": 120.0, "opponent_points": 90.0, "result": "W",
            },
        ]
        rows = team_features._head_to_head(games)
        beta = next(row for row in rows if row["opponent"] == "Beta")
        self.assertEqual((beta["wins"], beta["losses"], beta["games"]), (1, 1, 2))
        self.assertEqual(beta["last_result"], "L")

    def test_playoff_simulation_is_deterministic_and_conserves_seeds(self):
        standings = [
            {"roster_id": "1", "wins": 5, "losses": 1, "ties": 0, "pf": 700, "ppg": 116.7},
            {"roster_id": "2", "wins": 4, "losses": 2, "ties": 0, "pf": 680, "ppg": 113.3},
            {"roster_id": "3", "wins": 2, "losses": 4, "ties": 0, "pf": 620, "ppg": 103.3},
            {"roster_id": "4", "wins": 1, "losses": 5, "ties": 0, "pf": 600, "ppg": 100.0},
        ]
        schedule = [(7, "1", "2"), (7, "3", "4"), (8, "1", "3"), (8, "2", "4")]
        first = team_features._simulate_playoffs(
            standings, schedule, 2, simulations=300, seed="test-seed"
        )
        second = team_features._simulate_playoffs(
            standings, schedule, 2, simulations=300, seed="test-seed"
        )
        self.assertEqual(first, second)
        self.assertEqual(
            sum(team["playoff"] for team in first["teams"].values()),
            300 * 2,
        )
        for team in first["teams"].values():
            self.assertGreaterEqual(team["avg_seed"], 1.0)
            self.assertLessEqual(team["avg_seed"], 4.0)


if __name__ == "__main__":
    unittest.main()
