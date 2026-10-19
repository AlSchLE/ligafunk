"""NFL venue metadata for weather (outdoor only) and travel coasts.

Coordinates are stadium-approximate. Roofed / retractable treated as indoor:
wind and rain are ignored there.
"""

from __future__ import annotations

# team: (lat, lon, indoor, coast)  coast = W | C | E
VENUES: dict[str, tuple[float, float, bool, str]] = {
    "ARI": (33.5275, -112.2625, True, "W"),
    "ATL": (33.7553, -84.4006, True, "E"),
    "BAL": (39.2780, -76.6227, False, "E"),
    "BUF": (42.7738, -78.7870, False, "E"),
    "CAR": (35.2258, -80.8528, False, "E"),
    "CHI": (41.8623, -87.6167, False, "C"),
    "CIN": (39.0954, -84.5160, False, "C"),
    "CLE": (41.5061, -81.6995, False, "E"),
    "DAL": (32.7473, -97.0945, True, "C"),
    "DEN": (39.7439, -105.0201, False, "W"),
    "DET": (42.3400, -83.0456, True, "C"),
    "GB": (44.5013, -88.0622, False, "C"),
    "HOU": (29.6847, -95.4107, True, "C"),
    "IND": (39.7601, -86.1639, True, "C"),
    "JAX": (30.3239, -81.6373, False, "E"),
    "KC": (39.0489, -94.4839, False, "C"),
    "LAC": (33.9535, -118.3390, True, "W"),
    "LAR": (33.9535, -118.3390, True, "W"),
    "LV": (36.0909, -115.1833, True, "W"),
    "MIA": (25.9580, -80.2389, False, "E"),
    "MIN": (44.9738, -93.2577, True, "C"),
    "NE": (42.0909, -71.2643, False, "E"),
    "NO": (29.9511, -90.0812, True, "C"),
    "NYG": (40.8128, -74.0745, False, "E"),
    "NYJ": (40.8128, -74.0745, False, "E"),
    "PHI": (39.9008, -75.1675, False, "E"),
    "PIT": (40.4468, -80.0158, False, "E"),
    "SEA": (47.5952, -122.3316, False, "W"),
    "SF": (37.4030, -121.9700, False, "W"),
    "TB": (27.9759, -82.5033, False, "E"),
    "TEN": (36.1665, -86.7713, False, "C"),
    "WAS": (38.9076, -76.8645, False, "E"),
}


def venue(team: str) -> tuple[float, float, bool, str] | None:
    return VENUES.get((team or "").upper())
