"""Smoke-render the trade-advisor template without network or database data."""

import unittest

import app as app_module
from recapcore.trade import evaluate_offer, propose_gap_trades, render_trade_report, suggest_counters
from tests.test_trade_logic import board_fixture


def page_fixture():
    return {
        "week": 9,
        "my_team": "My Team",
        "my_roster_id": "1",
        "my_players": [
            {
                "pid": "r3",
                "name": "My RB Bench",
                "pos": "RB",
                "team": "TST",
                "injury": "Q",
                "practice": "LP",
                "depth": "2",
                "starter": False,
                "reserve": False,
            }
        ],
        "other_teams": [
            {
                "roster_id": "2",
                "name": "Partner",
                "players": [
                    {
                        "pid": "w6",
                        "name": "Partner WR Bench",
                        "pos": "WR",
                        "team": "TST",
                        "injury": "",
                        "practice": "",
                        "depth": "3",
                        "starter": False,
                        "reserve": False,
                    }
                ],
            }
        ],
        "recent_trades": [
            {
                "transaction_id": "t1",
                "partner": "Partner",
                "multi_team": False,
                "give": [{"pid": "r3", "name": "My RB Bench"}],
                "get": [{"pid": "w6", "name": "Partner WR Bench"}],
                "picks_sent": [],
                "picks_received": ["2027 R1"],
                "created": "2026-10-22",
                "week": 8,
            }
        ],
    }


class TradeTemplateTests(unittest.TestCase):
    def render(self, page=None, result=None, mode=None):
        team = {"name": "My Team"}
        league = {"name": "Test League", "league_code": "TRADECODE1"}
        with app_module.app.test_request_context("/l/TRADECODE1/trade"):
            return app_module.app.jinja_env.get_template("trade.html").render(
                team=team,
                league=league,
                page=page,
                result=result,
                mode=mode,
                week=9,
                csrf_token="a" * 64,
            )

    def test_form_renders(self):
        html = self.render(page=page_fixture())
        self.assertIn("Evaluate an offer", html)
        self.assertIn("Find trades for my gaps", html)
        self.assertIn("Recent completed trades", html)
        self.assertIn("name=\"give\"", html)
        self.assertIn("name=\"get\"", html)

    def test_evaluate_renders(self):
        board = board_fixture()
        result = evaluate_offer(
            board,
            my_roster_id="1",
            partner_roster_id="2",
            give_ids=["r3"],
            get_ids=["w6"],
        )
        result["counters"] = suggest_counters(
            board,
            my_roster_id="1",
            partner_roster_id="2",
            give_ids=["r3"],
            get_ids=["w6"],
        )
        result["my_team"] = "My Team"
        result["report_text"] = render_trade_report(result)
        html = self.render(result=result, mode="evaluate")
        self.assertIn("Counter ideas", html)
        self.assertIn("Accept", html)
        self.assertIn("You receive", html)

    def test_gaps_renders(self):
        board = board_fixture()
        result = {
            "mode": "gaps",
            "league_name": "Test League",
            "week": 9,
            "my_team": "My Team",
            "my_roster_id": "1",
            "proposals": propose_gap_trades(board, "1"),
        }
        result["report_text"] = render_trade_report(result)
        html = self.render(result=result, mode="gaps")
        self.assertIn("Trades for your roster gaps", html)
        self.assertIn("Partner", html)
        self.assertIn("You receive", html)


if __name__ == "__main__":
    unittest.main()
