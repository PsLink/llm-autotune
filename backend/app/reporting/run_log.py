"""A run's engine log, from wherever it is right now.

A live engine's log comes from its container; a finished one's from the copy
the worker saved (a file on the worker's disk, its end kept in the row). Read
by the human run view and by a policy session reading its own launches.
"""

import os

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.control.launch import MachineInfo, get_driver
from app.control.launch.base import DeploymentHandle
from app.db.models import TERMINAL_RUN_STATES, Event, Machine, Run, RunKind

_TERMINAL = {s.value for s in TERMINAL_RUN_STATES}


async def read_run_log(session: AsyncSession, run: Run) -> str:
    live_run = run.status not in _TERMINAL
    # A running engine's log comes from its container: what the worker saved
    # is a copy from a few minutes ago.
    if live_run and run.container_name and run.machine_id:
        machine = await session.get(Machine, run.machine_id)
        if machine is not None:
            live = await anyio.to_thread.run_sync(_live_log, machine, run.container_name)
            if live:
                return live
    if run.log_path and os.path.exists(run.log_path):
        with open(run.log_path, encoding="utf-8") as f:
            return f.read()
    # The file is on the worker's disk, readable here only when the two share
    # a volume. The end of it is kept in the row.
    if run.log_tail:
        return run.log_tail
    waiting = await _cluster_refusal(session, run)
    if live_run:
        if waiting:
            return f"(No log yet: the cluster has not started the engine. {waiting})"
        return "(no log yet — the engine has not started writing one)"
    return _why_no_log(run, waiting)


async def _cluster_refusal(session: AsyncSession, run: Run) -> str:
    """Why the cluster last refused to start this run's pod, if it ever did."""
    event = (
        await session.execute(
            select(Event)
            .where(Event.run_id == run.id, Event.kind == "run_waiting_for_cluster")
            .order_by(Event.id.desc())
            .limit(1)
        )
    ).scalars().first()
    reason = ((event.payload or {}).get("reason") if event else "") or ""
    return f"The cluster said: {reason}" if reason else ""


def _why_no_log(run: Run, waiting: str = "") -> str:
    """What an empty log means, so it does not read as a platform fault. The
    campaign's activity log (GET /campaigns/{id}/activity) has the steps either
    way."""
    if run.kind == RunKind.EXTERNAL.value:
        return (
            "(This run benchmarked an engine that another run serves, so it has no "
            "container of its own. The engine's log is under that run.)"
        )
    if not run.launch_command:
        return (
            f"(No engine log: run {run.id} ended before its engine was launched. "
            "The campaign's Activity log shows what happened.)"
        )
    if waiting and run.waiting_since is not None:
        # Still queued when it ended: the engine never ran, so it never logged.
        return (
            f"(No engine log: run {run.id} ended before the cluster started its engine. "
            f"{waiting})"
        )
    return (
        f"(No engine log was captured for run {run.id}: the engine printed nothing before "
        "it ended, or its container was already gone. The campaign's Activity log shows "
        "what happened.)"
    )


def _live_log(machine: Machine, container_name: str) -> str:
    """Best-effort read of a running engine's log, straight from its container."""
    try:
        driver = get_driver(machine.driver or "ssh_docker", cluster_id=machine.cluster_id)
        handle = DeploymentHandle(
            driver=getattr(driver, "name", ""), container_name=container_name,
            machine=MachineInfo.of(machine), endpoint_url="",
        )
        return driver.logs(handle, tail=2000) or ""
    except Exception:
        return ""

