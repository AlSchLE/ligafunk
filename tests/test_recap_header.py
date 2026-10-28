"""Keep the generated recap header compact and consistent."""

import unittest

from recapcore.narrate import league_recap


class RecapHeaderTests(unittest.TestCase):
    def test_header_rules_use_half_width(self):
        text = league_recap(
            {},
            {},
            week=7,
            style="bromance",
            theme="halloween",
            seed=1,
        )
        rules = [line for line in text.splitlines() if line and set(line) == {"="}]

        self.assertEqual(rules, ["=" * 36, "=" * 36])
        self.assertNotIn("=" * 72, text)


if __name__ == "__main__":
    unittest.main()
