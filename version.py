"""Ligafunk version and release notes."""

__version__ = "1.1.0"

# Changelog — latest first. Maintained in the repo for the /info page.
CHANGELOG = [
    {
        "version": "1.1.0",
        "date": "2026-11-18",
        "changes": [
            "Added team standings, matchup and record book with playoff tools.",
            "Added a trade advisor with offer checks and gap proposals.",
            "Added persistent dark / light theme toggle.",
            "Added a re-designed Sleeper-inspired UI (records, recap pages).",
            "Added direct team login on the homepage (no league code entry).",
            "Added recap style packs with seasonal themes.",
            "Hardened auth, sessions, webhooks, CSP and backups (secrets to env vars, CSRF on all forms).",
            "Improved layout legibility (primary button, recap separators, placeholdifiers).",
            "Added local-first SQLite persistence with Litestream.",
            "Waiver recommendations filtered to the positions the team actually uses (no kicker for DQDPV-FU).",
            "Team login now requires team code first, then username + password (two login cycles).",
        ],
    },
    {
        "version": "1.0.0",
        "date": "2026-11-18",
        "changes": [
            "Waiver recommendations filtered to the positions the team actually uses (no kicker for DQDPV-FU).",
            "Team login now requires team code first, then username + password (two login cycles).",
        ],
    },
]

VERSION = __version__
