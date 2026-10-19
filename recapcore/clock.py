"""Europe/Berlin lock windows for Thursday evening and Saturday evening / Sunday morning."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    BERLIN = ZoneInfo("Europe/Berlin")
    NY = ZoneInfo("America/New_York")
except Exception:
    # Windows without the tzdata package: CEST/EDT offsets used in NFL season.
    BERLIN = timezone(timedelta(hours=2), name="CEST")
    NY = timezone(timedelta(hours=-4), name="EDT")

WINDOWS = ("auto", "thu", "weekend")


def now_berlin() -> datetime:
    return datetime.now(BERLIN)


def detect_window(now: datetime | None = None) -> str:
    """thu = Thursday evening DE (before TNF). weekend = Sat evening / Sunday morning DE."""
    now = now or now_berlin()
    if now.tzinfo is None:
        now = now.replace(tzinfo=BERLIN)
    else:
        now = now.astimezone(BERLIN)
    wd, hour = now.weekday(), now.hour
    if wd == 3 and hour >= 16:
        return "thu"
    if wd == 5 and hour >= 17:
        return "weekend"
    if wd == 6 and hour < 19:
        return "weekend"
    if wd == 3:
        return "thu"
    if wd >= 5:
        return "weekend"
    if wd >= 2:
        return "thu"
    return "weekend"


def window_label(window: str, now: datetime | None = None) -> str:
    now = now or now_berlin()
    stamp = now.astimezone(BERLIN).strftime("%Y-%m-%d %H:%M")
    if window == "thu":
        when = "Thursday evening (Europe/Berlin) — lock TNF now; Sunday injuries still preliminary"
    else:
        when = "Saturday evening / Sunday morning (Europe/Berlin) — lock the Sunday slate"
    return f"{when} · local {stamp}"


def kickoff_eastern(game: dict) -> datetime | None:
    """Kickoff in US Eastern. Prefers an ISO start field; else infers from the game date."""
    raw = str(game.get("start") or game.get("kickoff") or "").strip()
    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(NY)
        except ValueError:
            pass
    day = str(game.get("date") or "")[:10]
    if len(day) != 10:
        return None
    try:
        y, m, d = (int(x) for x in day.split("-"))
    except ValueError:
        return None
    wd = datetime(y, m, d).weekday()
    hour, minute = (20, 15) if wd in {0, 3} else (13, 0)
    return datetime(y, m, d, hour, minute, tzinfo=NY)


def kickoff_berlin(game: dict) -> datetime | None:
    eastern = kickoff_eastern(game)
    return eastern.astimezone(BERLIN) if eastern else None


def game_started(game: dict, now: datetime | None = None) -> bool:
    now = now or now_berlin()
    ko = kickoff_berlin(game)
    if ko is None:
        return str(game.get("status") or "").lower() in {"complete", "closed", "in_game", "live"}
    status = str(game.get("status") or "").lower()
    if status in {"complete", "closed"}:
        return True
    return now >= ko


def kickoff_soon(game: dict, now: datetime | None = None, hours: float = 6.0) -> bool:
    now = now or now_berlin()
    ko = kickoff_berlin(game)
    if ko is None or game_started(game, now):
        return False
    return timedelta(0) <= (ko - now) <= timedelta(hours=hours)


def fmt_kickoff(game: dict) -> str:
    ko = kickoff_berlin(game)
    if not ko:
        return str(game.get("date") or "?")
    return ko.strftime("%a %H:%M") + " Berlin"
