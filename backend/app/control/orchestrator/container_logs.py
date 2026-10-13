"""Keeping engine and policy logs, best-effort and bounded.

A container's log lives only as long as the container. Read once at the end,
it is lost whenever that one read fails: the pod was evicted, its node went
away, the worker restarted in between, or the cluster answered with an error
at that moment. So the worker saves the end of every live container's log
every few minutes (`Snapshots`), each save replacing the last, and the final
read at teardown replaces that, unless it comes back empty.
"""

from __future__ import annotations

import time


def clip(text: str, max_bytes: int) -> str:
    """The end of `text`, at most `max_bytes` of UTF-8, starting on a line."""
    data = text.encode("utf-8")
    if max_bytes <= 0 or len(data) <= max_bytes:
        return text
    end = data[-max_bytes:].decode("utf-8", errors="ignore")
    newline = end.find("\n")
    if 0 <= newline < len(end) - 1:
        end = end[newline + 1:]
    return "(earlier lines dropped)\n" + end


class Snapshots:
    """When each live container's log was last saved, so each is read once
    per interval, and the ones waiting longest go first."""

    def __init__(self, per_tick: int = 4) -> None:
        self.per_tick = per_tick
        self._saved_at: dict[tuple[str, int], float] = {}

    def due(
        self, keys: list[tuple[str, int]], interval_seconds: float
    ) -> list[tuple[str, int]]:
        """The keys whose log should be read this tick, oldest first. A key
        seen for the first time starts its clock (a container just launched
        has printed little), and keys no longer live are forgotten."""
        if interval_seconds <= 0:
            return []
        live = set(keys)
        for key in [k for k in self._saved_at if k not in live]:
            del self._saved_at[key]
        now = time.monotonic()
        for key in keys:
            self._saved_at.setdefault(key, now)
        waiting = [k for k in keys if now - self._saved_at[k] >= interval_seconds]
        waiting.sort(key=lambda k: self._saved_at[k])
        return waiting[: self.per_tick]

    def mark(self, key: tuple[str, int]) -> None:
        self._saved_at[key] = time.monotonic()
