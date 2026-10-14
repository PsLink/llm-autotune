"""Managed campaigns: a plugin's work, run by the platform's own rules.

A plugin hands the platform a campaign spec; the platform checks it like any
campaign, records who owns it, and closes it only once nothing of it is
running. These are the guarantees a plugin builds on.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.control.managed import (
    ManagedCampaignRefused,
    Owner,
    create_managed_campaign,
    owners_of,
    record_event,
    stop_managed_campaign,
)
from app.db.base import Base
from app.db.models import Campaign, CampaignStatus, Candidate, Event, Run, RunStatus, User

SPEC = {
    "name": "one config",
    "engine": "sglang",
    "image": "img",
    "model_path": "/models/m",
    "served_model_name": "m",
    "search_space": {"base": {"tp": 2}},
}
OWNER = Owner(plugin="example", label="Request #4", link="/requests/4")


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as s:
        s.add(User(id=1, username="u", password_hash="x"))
        s.commit()
        yield s


def test_a_spec_becomes_a_campaign_with_its_owner_on_record(session):
    campaign = create_managed_campaign(session, owner=OWNER, owner_id=1, spec=SPEC)
    session.commit()

    assert campaign.id and campaign.status == CampaignStatus.DRAFT.value
    assert campaign.search_space == {"base": {"tp": 2}}
    assert owners_of(session, [campaign.id]) == {campaign.id: OWNER}
    created = session.scalars(select(Event).where(Event.campaign_id == campaign.id)).one()
    assert (created.kind, created.actor) == ("campaign_created", "plugin:example")


def test_start_for_opens_a_window_now_the_way_force_start_does(session):
    moment = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)
    campaign = create_managed_campaign(
        session, owner=OWNER, owner_id=1, spec=SPEC, start_for=timedelta(hours=3), at=moment
    )
    assert campaign.status == CampaignStatus.ACTIVE.value
    assert campaign.window_start == moment
    assert campaign.window_end == campaign.override_until == moment + timedelta(hours=3)


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"engine": "trt"}, "engine must be sglang|vllm"),
        ({"search_space": {"range": {"c": {"min": 8, "max": 2, "step": 2}}}}, "below min"),
        ({"verify_top_k": 2}, "verify_top_k needs verify_benchmark_slug"),
        ({"policy_id": 99}, "no policy with id 99"),
        ({"benchmark_spec": {"kind": "random"}}, "names its benchmarks by slug"),
    ],
)
def test_a_spec_that_breaks_a_rule_is_refused_and_nothing_is_made(session, change, says):
    with pytest.raises(ManagedCampaignRefused, match=says):
        create_managed_campaign(session, owner=OWNER, owner_id=1, spec={**SPEC, **change})
    session.rollback()
    assert session.scalars(select(Campaign)).all() == []


def test_stopping_waits_until_nothing_is_running(session):
    campaign = create_managed_campaign(
        session, owner=OWNER, owner_id=1, spec=SPEC, start_for=timedelta(hours=3)
    )
    session.add(Candidate(id=1, campaign_id=campaign.id, config_hash="h", config={"tp": 2}))
    run = Run(id=1, campaign_id=campaign.id, candidate_id=1, status=RunStatus.BENCHING.value)
    session.add(run)
    session.flush()

    assert stop_managed_campaign(session, campaign, plugin="example") is False
    # The window is shut now, so the platform ends the live run as it ends
    # any campaign's at the end of a window; the campaign is not done yet.
    assert campaign.status == CampaignStatus.ACTIVE.value
    assert campaign.window_end <= datetime.now(UTC) and campaign.override_until is None

    run.status = RunStatus.KILLED.value
    assert stop_managed_campaign(session, campaign, plugin="example", reason="aborted") is True
    assert campaign.status == CampaignStatus.DONE.value
    assert stop_managed_campaign(session, campaign, plugin="example") is True  # idempotent
    done = session.scalars(select(Event).where(Event.kind == "campaign_done")).all()
    assert [(e.actor, e.payload) for e in done] == [("plugin:example", {"reason": "aborted"})]


def test_a_plugin_writes_to_the_event_log_under_its_own_name(session):
    record_event(session, "request_started", plugin="example", payload={"request": 4})
    event = session.scalars(select(Event)).one()
    assert (event.actor, event.kind, event.payload) == (
        "plugin:example", "request_started", {"request": 4})
