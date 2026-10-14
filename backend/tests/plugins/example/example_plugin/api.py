"""The example plugin's routes, mounted under /api/example."""

from typing import Any

from app.plugin_api import (
    Campaign,
    ExtensionRefused,
    ManagedCampaignRefused,
    Owner,
    User,
    create_managed_campaign,
    get_async_session,
    get_current_user,
    stop_managed_campaign,
)
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from example_plugin.models import CampaignLabel, Setting, TickNote

router = APIRouter(prefix="/example", tags=["example plugin"])


class LabelIn(BaseModel):
    label: str = Field(min_length=1, max_length=64)


@router.get("/ticks")
async def ticks(
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
) -> dict:
    count = await session.scalar(select(func.count()).select_from(TickNote))
    last = await session.scalar(select(TickNote).order_by(TickNote.id.desc()).limit(1))
    return {"ticks": count, "active_campaigns": last.active_campaigns if last else None}


@router.get("/settings/{key}")
async def setting(
    key: str,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
) -> dict:
    row = await session.get(Setting, key)
    if row is None:
        raise HTTPException(404, f"no setting {key!r}")
    return {"key": row.key, "value": row.value}


@router.put("/campaigns/{campaign_id}/label")
async def put_label(
    campaign_id: int,
    body: LabelIn,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
) -> dict:
    if await session.get(Campaign, campaign_id) is None:
        raise HTTPException(404, f"no campaign {campaign_id}")
    row = await session.get(CampaignLabel, campaign_id)
    if row is None:
        row = CampaignLabel(campaign_id=campaign_id, label=body.label)
        session.add(row)
    else:
        row.label = body.label
    await session.commit()
    return {"campaign_id": campaign_id, "label": row.label}


class MeasureIn(BaseModel):
    # A campaign spec: the body POST /api/campaigns takes.
    spec: dict[str, Any]
    hours: float = Field(default=2, gt=0, le=24)


@router.post("/measurements")
async def measure(
    body: MeasureIn,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
) -> dict:
    """Run a spec as a managed campaign, started now for `hours`."""
    from datetime import timedelta

    def _create(sync: Session) -> int:
        campaign = create_managed_campaign(
            sync,
            owner=Owner(plugin="example", label=f"Measurement for {user.username}",
                        link="/example/ticks"),
            owner_id=user.id,
            spec=body.spec,
            start_for=timedelta(hours=body.hours),
        )
        return campaign.id

    try:
        campaign_id = await session.run_sync(_create)
    except (ManagedCampaignRefused, ExtensionRefused) as exc:
        await session.rollback()
        raise HTTPException(422, str(exc)) from exc
    await session.commit()
    return {"campaign_id": campaign_id}


@router.post("/measurements/{campaign_id}/stop")
async def stop_measurement(
    campaign_id: int,
    session: AsyncSession = Depends(get_async_session),
    user: User = Depends(get_current_user),
) -> dict:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, f"no campaign {campaign_id}")
    done = await session.run_sync(
        lambda sync: stop_managed_campaign(
            sync, sync.get(Campaign, campaign_id), plugin="example", reason="stopped"
        )
    )
    await session.commit()
    return {"campaign_id": campaign_id, "done": done}
