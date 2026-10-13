"""The campaign and run lists say whose work each row is and filter by it, and a
campaign keeps a readable record of what the platform did, even one whose
containers printed nothing."""

import itertools
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.auth import create_token
from app.db.base import Base, get_async_session
from app.db.models import Campaign, Candidate, Event, Policy, PolicySession, Run, User
from app.main import app
from app.reporting.activity import describe

_DB = itertools.count()


@pytest.fixture
async def client():
    engine = create_async_engine(
        f"sqlite+aiosqlite:///file:la{next(_DB)}?mode=memory&cache=shared&uri=true")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = override
    async with factory() as session:
        session.add(User(id=1, username="alice", password_hash="x", role="user"))
        session.add(User(id=2, username="bob", password_hash="x", role="user"))
        for cid, owner in ((1, 1), (2, 2)):
            session.add(Campaign(
                id=cid, owner_id=owner, name=f"c{cid}", engine="sglang", image="img",
                model_path="/m", served_model_name="m", search_space={}))
            session.add(Candidate(id=cid, campaign_id=cid, config_hash=f"h{cid}", config={}))
        session.add(Run(id=1, campaign_id=1, candidate_id=1, status="succeeded",
                        launch_command="docker run", log_tail="engine says hi"))
        session.add(Run(id=2, campaign_id=1, candidate_id=1, status="killed"))
        session.add(Run(id=3, campaign_id=2, candidate_id=2, status="benching"))
        session.add(Run(id=4, campaign_id=2, candidate_id=2, status="serving"))
        session.add(Run(id=5, campaign_id=2, candidate_id=2, status="killed",
                        launch_command="docker run"))
        session.add(Event(actor="alice", kind="campaign_created", campaign_id=1,
                          payload={"name": "c1"}))
        session.add(Event(actor="worker", kind="run_scheduled", campaign_id=1, run_id=2,
                          payload={"machine": "gpu-1", "gpus": [0, 1], "port": 28200,
                                   "config": {"tp": 2}}))
        session.add(Event(actor="alice", kind="stop_requested", run_id=2,
                          payload={"reason": "force stop"}))
        session.add(Event(actor="worker", kind="run_transition", campaign_id=1, run_id=2,
                          payload={"from": "launching", "to": "killed"}))
        session.add(Event(actor="worker", kind="some_future_kind", campaign_id=1,
                          payload={"x": 1}))
        session.add(Event(actor="bob", kind="campaign_created", campaign_id=2,
                          payload={"name": "c2"}))
        await session.commit()
        token = create_token(await session.get(User, 1))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t/api",
                           headers={"Authorization": f"Bearer {token}"}) as http:
        yield http, factory
    app.dependency_overrides.clear()
    await engine.dispose()


async def test_campaigns_carry_their_owner_name(client):
    http, _ = client
    rows = (await http.get("/campaigns")).json()
    assert {r["name"]: r["owner_name"] for r in rows} == {"c1": "alice", "c2": "bob"}
    assert (await http.get("/campaigns/2")).json()["owner_name"] == "bob"


async def test_runs_list_names_the_campaign_and_owner(client):
    http, _ = client
    rows = {r["id"]: r for r in (await http.get("/runs")).json()}
    assert rows[1]["campaign_name"] == "c1" and rows[1]["owner_name"] == "alice"
    assert rows[3]["campaign_name"] == "c2" and rows[3]["owner_name"] == "bob"


async def test_runs_filter_by_status_campaign_and_owner(client):
    http, _ = client

    async def ids(**params):
        return sorted(r["id"] for r in (await http.get("/runs", params=params)).json())

    assert await ids(status="killed") == [2, 5]
    # "live" is every state a run is in while it runs, serving included.
    assert await ids(status="live") == [3, 4]
    assert await ids(status=["live", "succeeded"]) == [1, 3, 4]
    assert await ids(campaign_id=1) == [1, 2]
    assert await ids(owner="bob") == [3, 4, 5]
    assert await ids(owner="bob", status="killed") == [5]
    assert await ids(owner=["alice", "bob"], campaign_id=[2]) == [3, 4, 5]


async def test_activity_reads_as_lines_and_covers_run_events(client):
    http, _ = client
    text = (await http.get("/campaigns/1/activity")).text
    lines = text.splitlines()
    assert "campaign created as 'c1'" in lines[0]
    assert "run 2 placed on gpu-1 on GPU 0,1, port 28200: tp=2" in lines[1]
    # Recorded against the run only, still part of the campaign's story.
    assert "stop requested for run 2 (force stop)" in lines[2]
    assert "run 2: launching -> stopped" in lines[3]
    # A kind this version has no words for still shows, raw.
    assert "some future kind: x=1" in lines[4]
    assert "c2" not in text
    assert (await http.get("/campaigns/99/activity")).status_code == 404


async def test_an_empty_log_says_why(client):
    http, factory = client
    assert (await http.get("/runs/1/log")).text == "engine says hi"
    never_launched = (await http.get("/runs/2/log")).text
    assert "ended before its engine was launched" in never_launched
    printed_nothing = (await http.get("/runs/5/log")).text
    assert "printed nothing" in printed_nothing and "Activity" in printed_nothing

    # A pod the cluster never placed: the log says what the scheduler said.
    async with factory() as session:
        session.add(Run(id=6, campaign_id=2, candidate_id=2, status="killed",
                        launch_command="kubectl apply", waiting_since=datetime.now(UTC)))
        session.add(Event(actor="worker", kind="run_waiting_for_cluster", campaign_id=2,
                          run_id=6, payload={"reason": "0/9 nodes are available: "
                                                       "1 Insufficient nvidia.com/gpu"}))
        await session.commit()
    never_placed = (await http.get("/runs/6/log")).text
    assert "before the cluster started its engine" in never_placed
    assert "Insufficient nvidia.com/gpu" in never_placed

    async with factory() as session:
        session.add(Policy(id=1, owner_id=1, name="p", image="p:1"))
        session.add(PolicySession(id=7, campaign_id=2, policy_id=1, status="aborted"))
        session.add(PolicySession(id=8, campaign_id=2, policy_id=1, status="failed",
                                  first_heartbeat_at=datetime.now(UTC)))
        session.add(PolicySession(id=9, campaign_id=2, policy_id=1, status="failed",
                                  env_snapshot={"death_logs": "Traceback: boom"}))
        await session.commit()
    assert "never reached the platform" in (await http.get("/policy-sessions/7/log")).text
    assert "gone before the platform could read it" in (
        await http.get("/policy-sessions/8/log")).text
    # What was read as the container died is the log, when nothing else was kept.
    assert (await http.get("/policy-sessions/9/log")).text == "Traceback: boom"


def test_describe_survives_an_odd_payload():
    event = Event(actor="worker", kind="run_scheduled", run_id=3, payload={"gpus": 5})
    assert describe(event).startswith("run scheduled")
