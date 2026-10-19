"""A campaign's activity log: what the platform did, from the events table.

The engine and policy logs are what the containers printed, and a container
that printed nothing, or never started, leaves nothing to read. The platform
records every step it takes anyway (a run placed, a stop asked for, a policy's
request), so this log always has the story of a campaign, even a killed one.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from app.db.models import Event


def _config(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return ""
    return " ".join(f"{k}={v}" for k, v in value.items())


def _gpus(payload: dict) -> str:
    gpus = payload.get("gpus") or []
    return f" on GPU {','.join(str(g) for g in gpus)}" if gpus else ""


def _where(payload: dict) -> str:
    machine = payload.get("machine")
    return f" on {machine}" if machine else ""


def _session(payload: dict) -> str:
    sid = payload.get("session_id")
    return f"policy session {sid}" if sid is not None else "policy session"


# Why a policy's search ended (PolicySession.search_end_reason), in words.
_END = {
    "policy_exhausted": "nothing more to try",
    "deadline": "the window ran out",
    "explicit": "asked to stop",
    "policy_error": "the policy reported an error",
    "policy_idle": "the policy went idle",
    "policy_wedged": "the policy stopped responding",
    "container_died": "the policy container exited",
}


def _transition(e: Event, p: dict) -> str:
    to = str(p.get("to", "")).replace("_", " ")
    if to == "killed":
        to = "stopped"
    return f"run {e.run_id}: {str(p.get('from', '')).replace('_', ' ')} -> {to}"


def _scheduled(e: Event, p: dict) -> str:
    text = f"run {e.run_id} placed{_where(p)}{_gpus(p)}"
    if p.get("port"):
        text += f", port {p['port']}"
    if p.get("sharing_with"):
        text += f", next to run {', '.join(str(r) for r in p['sharing_with'])}"
    config = _config(p.get("config"))
    return f"{text}: {config}" if config else text


def _stop(e: Event, p: dict) -> str:
    why = p.get("reason")
    return f"stop requested for run {e.run_id}" + (f" ({why})" if why else "")


def _ended(e: Event, p: dict) -> str:
    text = f"{_session(p)} ended: {p.get('status', '')}"
    if p.get("failure_class"):
        text += f" ({p['failure_class']})"
    if p.get("validated") is not None:
        text += f", {p['validated']} finalist(s) measured"
    return text


def _failed(e: Event, p: dict) -> str:
    text = f"{_session(p)} failed: {p.get('failure_class', '')}"
    if p.get("error"):
        text += f": {str(p['error']).splitlines()[0][:200]}"
    return text


_DESCRIBE: dict[str, Callable[[Event, dict], str]] = {
    "campaign_created": lambda e, p: f"campaign created as {p.get('name', '')!r}",
    "campaign_status_changed": lambda e, p: f"status set to {p.get('status', '')}",
    "campaign_force_started": lambda e, p: "started now, outside the schedule",
    "campaign_force_stopped": lambda e, p: f"stopped; {p.get('runs_stopped', 0)} run(s) ended",
    "campaign_done": lambda e, p: "campaign done",
    "campaign_schedule_changed": lambda e, p: f"schedule set: {p.get('summary', '')}",
    "campaign_window_opened": lambda e, p: "window opened",
    "candidate_created": lambda e, p: (
        f"config rejected: {_config(p.get('config'))}: {p['error']}" if p.get("error")
        else f"config queued: {_config(p.get('config'))}"
    ),
    "candidate_requeued": lambda e, p: (
        f"run {e.run_id} failed ({p.get('failure_class', '')}); its config is queued again"
        f" (attempt {p.get('attempt', '?')})"
    ),
    "candidate_given_up": lambda e, p: (
        f"gave up on run {e.run_id}'s config after {p.get('attempts', '?')} attempts"
    ),
    "run_scheduled": _scheduled,
    "run_placed": lambda e, p: f"run {e.run_id} got its GPUs after {p.get('waited_seconds', 0)}s",
    "run_transition": _transition,
    "stop_requested": _stop,
    "dataset_pinned": lambda e, p: (
        f"replay sample {p.get('profile', '')} pinned ({p.get('applied', '')},"
        f" {p.get('records', '?')} requests)"
    ),
    "baseline_relaunch_scheduled": lambda e, p: "production config queued as the reference",
    "policy_session_created": lambda e, p: f"{_session(p)} created",
    "policy_session_reserved": lambda e, p: f"{_session(p)} reserved{_where(p)}{_gpus(p)}",
    "policy_session_started": lambda e, p: (
        f"{_session(p)} started{_where(p)}: {p.get('image', '')}"
    ),
    "policy_session_searching": lambda e, p: f"{_session(p)}: the policy is searching",
    "policy_session_finalizing": lambda e, p: (
        f"{_session(p)}: wrapping up ({_END.get(p.get('reason', ''), p.get('reason', ''))})"
    ),
    "policy_session_validating": lambda e, p: f"{_session(p)}: measuring the finalists",
    "policy_session_ended": _ended,
    "policy_session_failed_validating": _failed,
    "policy_session_abort_requested": lambda e, p: f"abort requested for {_session(p)}",
    "policy_session_finalize_requested": lambda e, p: f"wrap-up requested for {_session(p)}",
    "policy_plan_updated": lambda e, p: (
        "policy plan: tuning " + ", ".join((p.get("plan") or {}).get("tuning") or [])
    ),
    "policy_launch_requested": lambda e, p: (
        f"policy asked for run {e.run_id}{_gpus(p)}: {_config(p.get('config'))}"
    ),
    "policy_config_refused": lambda e, p: (
        f"refused a policy config: {p.get('reason', '')} — {_config(p.get('config'))}"
    ),
    "policy_bench_requested": lambda e, p: (
        f"policy asked to benchmark run {p.get('of_launch', '?')} (run {e.run_id})"
    ),
    "policy_release_requested": lambda e, p: f"policy released run {e.run_id}",
    "policy_contenders_updated": lambda e, p: f"policy named {p.get('count', 0)} finalist(s)",
    "policy_contender_platform_launch": lambda e, p: f"finalist launched as run {e.run_id}",
    "policy_contender_validated": lambda e, p: (
        f"finalist run {e.run_id}: {p.get('status', '')}, rank {p.get('rank', '?')}"
    ),
    "policy_finalized": lambda e, p: "policy said its search is done",
    "policy_event": lambda e, p: f"policy [{p.get('level', 'info')}]: {p.get('message', '')}",
}


def describe(event: Event) -> str:
    payload = event.payload or {}
    describe_one = _DESCRIBE.get(event.kind)
    if describe_one is not None:
        try:
            return describe_one(event, payload)
        except Exception:  # noqa: BLE001 — an odd payload falls back to the raw form
            pass
    parts = [event.kind.replace("_", " ")]
    if event.run_id:
        parts.append(f"run {event.run_id}")
    if payload:
        parts.append(" ".join(f"{k}={v}" for k, v in payload.items()))
    return ": ".join([parts[0], " ".join(parts[1:])]) if len(parts) > 1 else parts[0]


def _stamp(ts: datetime | None) -> str:
    if ts is None:
        return " " * 20
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%SZ")


def _who(actor: str) -> str:
    # A policy acts as "policy:<name>"; its name is in the policy's own lines.
    return "policy" if actor.startswith("policy:") else actor[:20]


def render_activity(events: Iterable[Event]) -> str:
    """One line per event, oldest first: when, who, what."""
    lines = [f"{_stamp(e.ts)}  {_who(e.actor):<10}  {describe(e)}" for e in events]
    return "\n".join(lines)
