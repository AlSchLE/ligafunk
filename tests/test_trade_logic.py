"""Pure trade-advisor tests; no Sleeper network calls."""

import unittest

from recapcore.trade import (
    _injury_value_factor,
    evaluate_offer,
    propose_gap_trades,
    render_trade_report,
    suggest_counters,
)


def asset(
    pid,
    name,
    pos,
    value,
    *,
    roster_id="1",
    lineup_status="starter",
    injury="",
    injury_factor=1.0,
    eligible=True,
):
    return {
        "pid": pid,
        "name": name,
        "pos": pos,
        "positions": [pos],
        "team": "TST",
        "roster_id": roster_id,
        "roster_name": f"Team {roster_id}",
        "lineup_status": lineup_status,
        "slot": "RB" if lineup_status == "starter" else "BN",
        "injury": injury,
        "practice": "",
        "depth": "",
        "eligible": eligible,
        "bye": False,
        "opp": "OPP",
        "home": True,
        "week_proj": value,
        "week_rec": value,
        "facts": [],
        "adj": [],
        "currently_started": lineup_status == "starter",
        "season_points": value * 4,
        "games_played": 4,
        "form_ppg": value,
        "ros_proj": value * 8,
        "games_left": 8,
        "injury_factor": injury_factor,
        "value": value,
    }


def board_fixture():
    my_players = [
        asset("q1", "My QB", "QB", 90.0),
        asset("r1", "My RB1", "RB", 100.0),
        asset("r2", "My RB2", "RB", 60.0),
        asset("r3", "My RB Bench", "RB", 40.0, lineup_status="bench"),
        asset("w1", "My WR1", "WR", 50.0),
        asset("w2", "My WR2", "WR", 40.0),
        asset("w3", "My WR Bench", "WR", 20.0, lineup_status="bench"),
        asset("t1", "My TE", "TE", 30.0),
    ]
    partner_players = [
        asset("q2", "Partner QB", "QB", 80.0, roster_id="2"),
        asset("q3", "Partner QB Bench", "QB", 70.0, roster_id="2", lineup_status="bench"),
        asset("r4", "Partner RB", "RB", 70.0, roster_id="2"),
        asset("r5", "Partner RB Bench", "RB", 50.0, roster_id="2", lineup_status="bench"),
        asset("w4", "Partner WR1", "WR", 90.0, roster_id="2"),
        asset("w5", "Partner WR2", "WR", 80.0, roster_id="2"),
        asset("w6", "Partner WR Bench", "WR", 50.0, roster_id="2", lineup_status="bench"),
        asset("t2", "Partner TE", "TE", 20.0, roster_id="2"),
    ]
    return {
        "league_name": "Test League",
        "week": 9,
        "pos_avg_value": {"QB": 85.0, "RB": 80.0, "WR": 70.0, "TE": 25.0},
        "rosters": {
            "1": {"roster_id": "1", "name": "My Team", "players": my_players},
            "2": {"roster_id": "2", "name": "Partner", "players": partner_players},
        },
        "players": {row["pid"]: row for row in my_players + partner_players},
    }


class TradeLogicTests(unittest.TestCase):
    def test_injury_value_factor(self):
        self.assertEqual(_injury_value_factor({"inj": ""}), 1.0)
        self.assertEqual(_injury_value_factor({"inj": "OUT"}), 0.25)
        self.assertLess(_injury_value_factor({"inj": "Q"}), 1.0)

    def test_evaluate_offer_surplus_for_need(self):
        board = board_fixture()
        result = evaluate_offer(
            board,
            my_roster_id="1",
            partner_roster_id="2",
            give_ids=["r3"],
            get_ids=["w6"],
        )
        self.assertEqual(result["verdict"], "Accept")
        self.assertGreater(result["delta"], 0)
        self.assertEqual(result["my_needs_top"][0][0], "WR")
        self.assertEqual(result["give"][0]["pid"], "r3")
        self.assertEqual(result["get"][0]["pid"], "w6")

    def test_evaluate_rejects_cross_roster_assets(self):
        board = board_fixture()
        with self.assertRaises(ValueError):
            evaluate_offer(
                board,
                my_roster_id="1",
                partner_roster_id="2",
                give_ids=["w4"],
                get_ids=["w6"],
            )
        with self.assertRaises(ValueError):
            evaluate_offer(
                board,
                my_roster_id="1",
                partner_roster_id="2",
                give_ids=["r3", "r3"],
                get_ids=["w6"],
            )

    def test_counters_replace_an_overpay(self):
        board = board_fixture()
        result = evaluate_offer(
            board,
            my_roster_id="1",
            partner_roster_id="2",
            give_ids=["r1"],
            get_ids=["w6"],
        )
        self.assertEqual(result["verdict"], "Decline")
        counters = suggest_counters(
            board,
            my_roster_id="1",
            partner_roster_id="2",
            give_ids=["r1"],
            get_ids=["w6"],
        )
        self.assertTrue(counters)
        self.assertTrue(any(counter["delta"] > result["delta"] for counter in counters))
        self.assertTrue(any("Swap" in counter["label"] or "Ask for" in counter["label"] for counter in counters))

    def test_gap_trade_uses_bench_surplus(self):
        board = board_fixture()
        proposals = propose_gap_trades(board, "1")
        self.assertTrue(proposals)
        self.assertTrue(
            any(
                "w6" in {row["pid"] for row in proposal["get"]}
                and "r3" in {row["pid"] for row in proposal["give"]}
                for proposal in proposals
            )
        )

    def test_recent_trade_direction_and_picks(self):
        from sleeper_recap import _recent_trades

        block = {
            "team_names": {"1": "My Team", "2": "Partner"},
            "transactions": [
                {
                    "transaction_id": "tx1",
                    "type": "trade",
                    "roster_ids": [1, 2],
                    "adds": {"a": 2, "b": 1},
                    "drops": {"a": 1, "b": 2},
                    "created": 1760000000000,
                    "leg": 8,
                    "draft_picks": [
                        {
                            "season": "2027",
                            "round": 1,
                            "previous_owner_id": 1,
                            "owner_id": 2,
                        }
                    ],
                }
            ],
            "transactions_prev": [],
        }
        players = {"a": {"n": "Incoming"}, "b": {"n": "Outgoing"}}
        rows = _recent_trades(block, "1", players)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["partner"], "Partner")
        self.assertEqual([row["pid"] for row in rows[0]["give"]], ["a"])
        self.assertEqual([row["pid"] for row in rows[0]["get"]], ["b"])
        self.assertEqual(rows[0]["picks_sent"], ["2027 R1"])

    def test_report_is_plain_text(self):
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
        text = render_trade_report(result)
        self.assertIn("TRADE ADVISOR", text)
        self.assertIn("YOU SEND", text)
        self.assertIn("COUNTER IDEAS", text)


if __name__ == "__main__":
    unittest.main()
