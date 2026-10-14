"""Managed campaigns: how a plugin runs its work on the platform.

A plugin that needs something measured (one config on one machine, or a
policy searching a space) does not build campaign rows or drive the
scheduler. It hands the platform a campaign spec, the same body
`POST /api/campaigns` takes and a campaign's YAML export holds, and the
platform creates the campaign by the same rules as any other, records who
owns it, and runs it like any other: the machine queue, leases, windows,
benchmarks and results are the platform's.

What the plugin keeps is the campaign id. It reads progress from the
platform's own rows (runs, results, the campaign's status), and closes the
campaign through `stop_managed_campaign` when its work is over.

The owner (plugin, label, link) is what keeps these campaigns from crowding
the campaign list: the list hides owned campaigns unless asked, and a
managed campaign's page links back to the page it belongs to.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import plugins
from app.campaign_rules import campaign_errors
from app.control.orchestrator.lifecycle import as_utc
from app.db.models import (
    TERMINAL_RUN_STATES,
    Campaign,
    CampaignOwner,
    CampaignStatus,
    Event,
    Run,
)
from app.schemas.core import CampaignCreate


@dataclass(frozen=True)
class Owner:
    """Who runs a managed campaign, and what for."""

    plugin: str
    # What the campaign is for, as the owner names it ("Submission #42").
    label: str = ""
    # The owner's page for it, an app path ("/submissions/42").
    link: str = ""


class ManagedCampaignRefused(ValueError):
    """The spec breaks a rule every campaign must satisfy; the message says
    which. Nothing was created."""


# The workload descriptions that become benchmarks on LLMBench when a person
# creates a campaign. A managed campaign names its benchmarks by slug: making
# one is a call to LLMBench, which does not belong in a worker tick.
_NOT_FOR_MANAGED = ("benchmark_spec", "verify_benchmark_spec")


def create_managed_campaign(
    session: Session,
    *,
    owner: Owner,
    owner_id: int,
    spec: CampaignCreate | dict[str, Any],
    start_for: timedelta | None = None,
    at: datetime | None = None,
) -> Campaign:
    """Create a campaign that `owner.plugin` runs, by the rules every campaign
    follows, and record its owner. Returns the campaign, flushed (it has an id)
    but not committed: it commits with the caller's transaction.

    `start_for` starts it now and holds it open that long, as Force start
    does; without it the campaign is created the way the API creates one
    (DRAFT, or SCHEDULED when it carries a nightly schedule).

    Raises ManagedCampaignRefused when the spec breaks a rule, and
    ExtensionRefused when a plugin refuses the spec's `extensions`."""
    body = spec if isinstance(spec, CampaignCreate) else CampaignCreate.model_validate(spec)
    given = [f for f in _NOT_FOR_MANAGED if getattr(body, f) is not None]
    if given:
        raise ManagedCampaignRefused(
            f"{', '.join(given)}: a managed campaign names its benchmarks by slug"
        )
    errors = campaign_errors(session, body)
    if errors:
        raise ManagedCampaignRefused("; ".join(errors))

    from app.control.orchestrator import schedule as sched

    campaign = Campaign(
        owner_id=owner_id,
        **body.model_dump(exclude={"extensions", *_NOT_FOR_MANAGED}),
    )
    if start_for is not None:
        moment = at or datetime.now(UTC)
        campaign.status = CampaignStatus.ACTIVE.value
        campaign.window_start = moment
        campaign.window_end = moment + start_for
        campaign.override_until = moment + start_for
    elif sched.from_campaign(campaign) is not None:
        campaign.status = CampaignStatus.SCHEDULED.value
    session.add(campaign)
    session.flush()
    plugins.create_campaign_extensions(session, campaign, body.extensions)
    session.add(
        CampaignOwner(
            campaign_id=campaign.id,
            plugin=owner.plugin,
            label=owner.label[:200],
            link=owner.link[:300],
        )
    )
    session.add(
        Event(
            actor=f"plugin:{owner.plugin}",
            kind="campaign_created",
            campaign_id=campaign.id,
            payload={"name": campaign.name, "owner": owner.plugin, "label": owner.label},
        )
    )
    session.flush()
    return campaign


def stop_managed_campaign(
    session: Session, campaign: Campaign, *, plugin: str, reason: str = ""
) -> bool:
    """Close a managed campaign: no new runs, and its window shut now, so the
    platform ends whatever is still running the way it ends any campaign's
    runs at the end of a window (their results so far stay). Returns True once
    the campaign is DONE, which is when nothing of it is still running; call
    it again on a later tick until then. Idempotent."""
    if campaign.status == CampaignStatus.DONE.value:
        return True
    moment = datetime.now(UTC)
    campaign.override_until = None
    if campaign.window_end is None or as_utc(campaign.window_end) > moment:
        campaign.window_end = moment
    live = session.scalar(
        select(func.count(Run.id)).where(
            Run.campaign_id == campaign.id,
            Run.status.not_in([s.value for s in TERMINAL_RUN_STATES]),
        )
    )
    if live:
        return False
    campaign.status = CampaignStatus.DONE.value
    session.add(
        Event(
            actor=f"plugin:{plugin}",
            kind="campaign_done",
            campaign_id=campaign.id,
            payload={"reason": reason} if reason else {},
        )
    )
    return True


def owners_of(session: Session, campaign_ids: Iterable[int]) -> dict[int, Owner]:
    """The owner of each of these campaigns that a plugin runs."""
    ids = list(campaign_ids)
    if not ids:
        return {}
    rows = session.scalars(select(CampaignOwner).where(CampaignOwner.campaign_id.in_(ids))).all()
    return {r.campaign_id: Owner(plugin=r.plugin, label=r.label, link=r.link) for r in rows}


def record_event(
    session: Session,
    kind: str,
    *,
    plugin: str,
    campaign_id: int | None = None,
    run_id: int | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Add to the platform's event log, attributed to the plugin. The log is
    what a campaign's Activity tab and the reports read."""
    session.add(
        Event(
            actor=f"plugin:{plugin}",
            kind=kind,
            campaign_id=campaign_id,
            run_id=run_id,
            payload=payload or {},
        )
    )
