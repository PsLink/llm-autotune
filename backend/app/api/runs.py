import os
import shlex

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.control.launch import MachineInfo, get_driver
from app.control.launch.base import DeploymentHandle, LaunchSpec
from app.core.auth import get_current_user
from app.db.base import get_async_session
from app.db.models import (
    TERMINAL_RUN_STATES,
    Campaign,
    Candidate,
    Event,
    Machine,
    Result,
    Run,
    RunKind,
    RunStatus,
    User,
    is_baseline_config,
)
from app.schemas.core import ResultOut, RunDetailOut, RunOut

router = APIRouter(prefix="/runs", tags=["runs"])

_TERMINAL = {s.value for s in TERMINAL_RUN_STATES}


@router.get("", response_model=list[RunOut])
async def list_runs(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
    limit: int = 200,
    status_in: list[str] | None = Query(
        None, alias="status",
        description="Run statuses to keep; `live` stands for every state a run is still in "
        "while it runs. Repeat the parameter for several.",
    ),
    campaign_id: list[int] | None = Query(None, description="Repeat for several campaigns."),
    owner: list[str] | None = Query(
        None, description="Usernames of the campaign owners. Repeat for several."
    ),
):
    query = select(Run).join(Campaign, Campaign.id == Run.campaign_id)
    if status_in:
        wanted = set(status_in)
        if "live" in wanted:
            wanted.discard("live")
            wanted |= {s.value for s in RunStatus} - _TERMINAL
        query = query.where(Run.status.in_(wanted))
    if campaign_id:
        query = query.where(Run.campaign_id.in_(campaign_id))
    if owner:
        query = query.join(User, User.id == Campaign.owner_id).where(User.username.in_(owner))
    # The candidate comes along because RunOut reports which stage the run
    # belongs to, and that is read off it. Lazy-loading it during serialization
    # raises MissingGreenlet under the async session rather than fetching.
    runs = (
        await session.execute(
            query.options(selectinload(Run.candidate)).order_by(Run.id.desc()).limit(limit)
        )
    ).scalars().all()
    ids = {run.campaign_id for run in runs}
    names = {
        row.id: (row.name, row.username or "")
        for row in (
            await session.execute(
                select(Campaign.id, Campaign.name, User.username)
                .outerjoin(User, User.id == Campaign.owner_id)
                .where(Campaign.id.in_(ids))
            )
        ).all()
    } if ids else {}
    out = []
    for run in runs:
        row = RunOut.model_validate(run)
        row.campaign_name, row.owner_name = names.get(run.campaign_id, ("", ""))
        out.append(row)
    return out


@router.get("/{run_id}", response_model=RunDetailOut)
async def get_run(
    run_id: int,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    run = await session.get(Run, run_id, options=[selectinload(Run.candidate)])
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such run")
    candidate = await session.get_one(Candidate, run.candidate_id)
    results = (
        (await session.execute(select(Result).where(Result.run_id == run_id).order_by(Result.id)))
        .scalars()
        .all()
    )
    campaign = await session.get_one(Campaign, run.campaign_id)
    return RunDetailOut(
        **RunOut.model_validate(run).model_dump(),
        config=candidate.config,
        env_snapshot=run.env_snapshot or {},
        results=[ResultOut.model_validate(r) for r in results],
        serve_command=_serve_command(run, candidate, campaign),
        image=campaign.image,
        model_path=campaign.model_path,
    )


def _serve_command(run: Run, candidate: Candidate, campaign: Campaign) -> str:
    """The engine command the run's config renders to, single-node."""
    from app.control.engines import get_adapter

    if is_baseline_config(candidate.config):
        return ""
    try:
        spec = LaunchSpec(
            run_id=run.id,
            machine=MachineInfo(name="", host=""),
            engine=campaign.engine,
            image=campaign.image,
            model_path=campaign.model_path,
            served_model_name=campaign.served_model_name,
            engine_args=candidate.config,
            port=run.service_port or campaign.service_port,
        )
        return shlex.join(get_adapter(campaign.engine).build_command(spec))
    except Exception:  # noqa: BLE001 — a display aid; the run detail never fails on it
        return ""


@router.post("/{run_id}/stop")
async def stop_run(
    run_id: int,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    """Request a stop. The worker honors it on its next tick (teardown +
    machine release) — the API never touches resources directly."""
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such run")
    if run.status in _TERMINAL:
        raise HTTPException(status.HTTP_409_CONFLICT, f"run already {run.status}")
    session.add(
        Event(actor=user.username, kind="stop_requested", run_id=run.id,
              campaign_id=run.campaign_id, payload={})
    )
    await session.commit()
    return {"ok": True, "note": "stop requested; the worker applies it within one tick"}


@router.get("/{run_id}/log", response_class=PlainTextResponse)
async def get_run_log(
    run_id: int,
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
):
    run = await session.get(Run, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such run")
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
