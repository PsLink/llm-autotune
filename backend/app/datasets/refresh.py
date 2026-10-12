"""When a dataset set up here takes its next sample.

LLMBench could rebuild a profile on its own schedule, but it does not know
which campaigns are measuring on the current build, and a rebuild under one
makes every later replay run of that campaign incomparable (pinning.py). So
the profiles this platform sets up are manual-only on LLMBench, and the
supervisor asks for a build when this says one is due:

  due       `refresh_hours` after the current build, or now if there is none
  not yet   a build is queued or running; or the last one failed less than
            `refresh_hours` ago (retried at the next interval, not every tick)
  held      an unfinished campaign is pinned to the current build; the
            supervisor waits for it (pinning.holders)
"""

from datetime import UTC, datetime, timedelta
from typing import Any

IN_FLIGHT = ("pending", "running")


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def in_flight(profile: dict[str, Any]) -> bool:
    return (profile.get("last_build") or {}).get("status") in IN_FLIGHT


def due_at(refresh_hours: int, profile: dict[str, Any], now: datetime) -> datetime | None:
    """When the next sample is due; None when the dataset refreshes only on request."""
    if refresh_hours <= 0:
        return None
    built = _when((profile.get("current") or {}).get("built_at"))
    last = profile.get("last_build") or {}
    failed = (
        _when(last.get("finished_at") or last.get("created_at"))
        if last.get("status") == "failed"
        else None
    )
    since = max((t for t in (built, failed) if t is not None), default=None)
    if since is None:
        return now
    return since + timedelta(hours=refresh_hours)


def is_due(refresh_hours: int, profile: dict[str, Any], now: datetime) -> bool:
    when = due_at(refresh_hours, profile, now)
    return when is not None and not in_flight(profile) and when <= now
