"""Sport branding based on Sleeper League ID."""

SPORT_BRANDS = {
    "soccer": {
        "name": "Soccer",
        "icon": "⚽",
        "primary": "#006400",
        "primary_light": "#008000",
        "accent": "#ffffff",
        "tagline": "Fantasy Soccer League",
    },
    "football": {
        "name": "Football",
        "icon": "🏈",
        "primary": "#1a472a",
        "primary_light": "#2d5a3f",
        "accent": "#c9a227",
        "tagline": "Fantasy Football League",
    },
    "baseball": {
        "name": "Baseball",
        "icon": "⚾",
        "primary": "#003087",
        "primary_light": "#0057b7",
        "accent": "#ff0000",
        "tagline": "Fantasy Baseball League",
    },
    "basketball": {
        "name": "Basketball",
        "icon": "🏀",
        "primary": "#ff6600",
        "primary_light": "#ff8533",
        "accent": "#000000",
        "tagline": "Fantasy Basketball League",
    },
    "hockey": {
        "name": "Hockey",
        "icon": "🏒",
        "primary": "#000080",
        "primary_light": "#0000cc",
        "accent": "#ff0000",
        "tagline": "Fantasy Hockey League",
    },
}

# League ID to sport mapping (customize with your own league IDs)
LEAGUE_SPORT_MAP = {
    "1389332038909198336": "soccer",      # Saxy Fantasy Land
    "1387730345675210752": "football",     # NTS NFL Fantasy League 26/27
}


def get_sport(league_id: str) -> dict:
    """Get sport branding for a Sleeper League ID."""
    sport_key = LEAGUE_SPORT_MAP.get(league_id, "football")
    return SPORT_BRANDS[sport_key]
