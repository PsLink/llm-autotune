import os

import anyio
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.control.launch import MachineInfo, get_driver
from app.control.launch.base import DeploymentHandle
from app.core.auth import get_current_user
from app.db.base import get_async_session
from app.db.models import TERMINAL_RUN_STATES, Candidate, Event, Machine, Result, Run, User
from app.schemas.core import ResultOut, RunDetailOut, RunOut

router = APIRouter(prefix="/runs", tags=["runs"])

_TERMINAL = {s.value for s in TERMINAL_RUN_STATES}


@router.get("", response_model=list[RunOut])
async def list_runs(
    _: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_async_session),
    limit: int = 200,
):
    # The candidate comes along because RunOut reports which stage the run
    # belongs to, and that is read off it. Lazy-loading it during serialization
    # raises MissingGreenlet under the async session rather than fetching.
    return (
        (
            await session.execute(
                select(Run)
                .options(selectinload(Run.candidate))
                .order_by(Run.id.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )


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
    return RunDetailOut(
        **RunOut.model_validate(run).model_dump(),
        config=candidate.config,
        env_snapshot=run.env_snapshot or {},
        results=[ResultOut.model_validate(r) for r in results],
    )


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
    if run.log_path and os.path.exists(run.log_path):
        with open(run.log_path, encoding="utf-8") as f:
            return f.read()
    # The file is on the worker's disk, readable here only when the two share
    # a volume. The end of it is kept in the row.
    if run.log_tail:
        return run.log_tail
    if run.status not in _TERMINAL and run.container_name and run.machine_id:
        machine = await session.get(Machine, run.machine_id)
        if machine is not None:
            live = await anyio.to_thread.run_sync(_live_log, machine, run.container_name)
            if live:
                return live
        return "(no log yet — the engine has not started writing one)"
    return "(no captured log for this run)"


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
