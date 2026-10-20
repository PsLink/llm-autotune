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

GAP_MARK = "[... later lines dropped; the end of the log follows ...]"
HEAD_LINES = 800


def clip(text: str, max_bytes: int) -> str:
    """`text` in at most `max_bytes` of UTF-8: its first quarter and its end,
    each cut on a line. The start of an engine's log is where it says what it
    resolved and enabled; the end is what it was doing last — both matter, the
    middle (steady-state batch lines) least."""
    data = text.encode("utf-8")
    if max_bytes <= 0 or len(data) <= max_bytes:
        return text
    head = data[: max_bytes // 4].decode("utf-8", errors="ignore")
    cut = head.rfind("\n")
    head = head[: cut + 1] if cut > 0 else head
    room = max_bytes - len(head.encode("utf-8")) - len(GAP_MARK) - 2
    end = data[-max(room, 0):].decode("utf-8", errors="ignore") if room > 0 else ""
    newline = end.find("\n")
    if 0 <= newline < len(end) - 1:
        end = end[newline + 1:]
    return f"{head}{GAP_MARK}\n{end}"


def read_text(path: str | None) -> str:
    """A saved log, or "" when there is none (or it cannot be read)."""
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def with_head(previous: str, latest: str, head_lines: int = HEAD_LINES) -> str:
    """A log read again: `latest` is only its last lines, so once a container
    has logged more than that its start is gone from the read. Keep the start
    from the earlier save — the first `head_lines` lines — ahead of the latest
    end. A `latest` that still begins where the earlier save began is the whole
    log, and is kept as it is."""
    if not previous.strip():
        return latest
    head = previous.split(GAP_MARK, 1)[0] if GAP_MARK in previous else previous
    head_kept = head.splitlines()[:head_lines]
    if not head_kept:
        return latest
    first = head_kept[0]
    if latest.startswith(first) or latest.splitlines()[:1] == [first]:
        return latest
    return "\n".join(head_kept) + f"\n{GAP_MARK}\n" + latest


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
