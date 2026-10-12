"""Deleting a campaign: one that never ran goes, with its candidates; one with
runs keeps its results and stays."""

from itertools import count

import httpx
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.auth import create_token
from app.db.base import Base, get_async_session
from app.db.models import Campaign, Candidate, Event, Run, User
from app.main import app

_counter = count()
BODY = {"name": "c", "engine": "sglang", "image": "img", "model_path": "/m",
        "served_model_name": "m", "search_space": {"grid": {"tp_size": [1, 2]}}}


@pytest_asyncio.fixture
async def client():
    name = f"campaign_delete_{next(_counter)}"
    engine = create_async_engine(f"sqlite+aiosqlite:///file:{name}?mode=memory&cache=shared&uri=true")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _session
    async with factory() as session:
        session.add(User(id=1, username="owner", password_hash="x", role="user"))
        session.add(User(id=2, username="other", password_hash="x", role="user"))
        await session.commit()
        tokens = {u: create_token(await session.get(User, u)) for u in (1, 2)}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://t/api") as http:
        http.headers["Authorization"] = f"Bearer {tokens[1]}"
        http.other = {"Authorization": f"Bearer {tokens[2]}"}
        http.db = factory
        yield http
    app.dependency_overrides.clear()
    await engine.dispose()


async def test_a_campaign_that_never_ran_can_be_deleted(client):
    created = (await client.post("/campaigns", json=BODY)).json()
    assert (await client.delete(f"/campaigns/{created['id']}", headers=client.other)
            ).status_code == 403
    resp = await client.delete(f"/campaigns/{created['id']}")
    assert resp.status_code == 200, resp.text
    assert (await client.get(f"/campaigns/{created['id']}")).status_code == 404
    async with client.db() as session:
        assert not (await session.execute(select(Candidate))).scalars().all()
        # The audit log keeps its lines, no longer tied to the campaign.
        kinds = [e.kind for e in (await session.execute(select(Event))).scalars()]
        assert "campaign_deleted" in kinds


async def test_a_campaign_with_runs_is_kept(client):
    created = (await client.post("/campaigns", json=BODY)).json()
    async with client.db() as session:
        session.add(Candidate(id=99, campaign_id=created["id"], config={}, config_hash="h"))
        session.add(Run(campaign_id=created["id"], candidate_id=99))
        await session.commit()
    resp = await client.delete(f"/campaigns/{created['id']}")
    assert resp.status_code == 409
    assert "has run" in resp.json()["detail"]
    async with client.db() as session:
        assert await session.get(Campaign, created["id"]) is not None


async def test_a_stopped_campaign_can_be_marked_done(client):
    created = (await client.post("/campaigns", json=BODY)).json()
    cid = created["id"]
    assert (await client.put(f"/campaigns/{cid}/status", json={"status": "active"})
            ).status_code == 200
    running = await client.put(f"/campaigns/{cid}/status", json={"status": "done"})
    assert running.status_code == 409
    assert "pause or stop it first" in running.json()["detail"]
    await client.put(f"/campaigns/{cid}/status", json={"status": "paused"})
    done = await client.put(f"/campaigns/{cid}/status", json={"status": "done"})
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "done"
