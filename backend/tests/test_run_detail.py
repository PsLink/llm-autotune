"""A run's details carry the engine command its config renders to: on
Kubernetes the launch command is a whole manifest, and the command a serving
deployment needs is inside it."""

import itertools
import shlex

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.auth import create_token
from app.db.base import Base, get_async_session
from app.db.models import Campaign, Candidate, Run, User
from app.main import app

_DB = itertools.count()


@pytest.fixture
async def client():
    engine = create_async_engine(
        f"sqlite+aiosqlite:///file:rd{next(_DB)}?mode=memory&cache=shared&uri=true")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = override
    async with factory() as session:
        session.add(User(id=1, username="u", password_hash="x", role="user"))
        session.add(Campaign(
            id=1, owner_id=1, name="c", engine="sglang", image="lmsysorg/sglang:v1",
            model_path="/data/models/qwen", served_model_name="qwen",
            search_space={"grid": {"tp_size": [2]}}))
        session.add(Candidate(id=1, campaign_id=1, config_hash="h", config={"tp_size": 2,
                                                           "mem_fraction_static": 0.85}))
        session.add(Run(id=1, campaign_id=1, candidate_id=1, service_port=28201))
        await session.commit()
        token = create_token(await session.get(User, 1))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t/api",
                           headers={"Authorization": f"Bearer {token}"}) as http:
        yield http
    app.dependency_overrides.clear()
    await engine.dispose()


async def test_a_run_shows_the_engine_command_for_its_config(client):
    detail = (await client.get("/runs/1")).json()
    argv = shlex.split(detail["serve_command"])
    assert argv[:3] == ["python3", "-m", "sglang.launch_server"]
    assert argv[argv.index("--tp-size") + 1] == "2"
    assert argv[argv.index("--mem-fraction-static") + 1] == "0.85"
    assert argv[argv.index("--port") + 1] == "28201"
    assert detail["image"] == "lmsysorg/sglang:v1"
    assert detail["model_path"] == "/data/models/qwen"
