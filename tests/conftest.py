import re
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from keelie_starter.app import create_app
from keelie_starter.config import Settings
from keelie_starter.db import Database
from keelie_starter.demo import DemoKeelie

PASSWORD = "correct horse battery staple"


def csrf_of(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "no csrf field on the page"
    return match.group(1)


def field_of(html: str, name: str) -> str:
    match = re.search(rf'name="{name}" value="([^"]*)"', html)
    assert match, f"no {name} field on the page"
    return match.group(1)


@pytest.fixture
def keelie() -> DemoKeelie:
    return DemoKeelie()


@pytest.fixture
def app(keelie: DemoKeelie) -> FastAPI:
    settings = Settings(keelie_mode="demo", database_path=":memory:", _env_file=None)
    return create_app(settings, keelie, Database(":memory:"))


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://site.test") as c:
        yield c


async def sign_up(client: httpx.AsyncClient, email: str = "ada@example.com") -> httpx.Response:
    page = await client.get("/signup")
    return await client.post(
        "/signup",
        data={
            "csrf": csrf_of(page.text),
            "email": email,
            "password": PASSWORD,
            "display_name": "Ada",
        },
    )


async def subscribe(client: httpx.AsyncClient, plan_id: str) -> str:
    """Choose a plan. Returns where the site sends the Consumer next."""
    page = await client.get("/plans")
    answer = await client.post("/subscribe", data={"csrf": csrf_of(page.text), "plan_id": plan_id})
    assert answer.status_code == 303
    return answer.headers["location"]
