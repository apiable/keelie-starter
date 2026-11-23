"""The HTTP client for Keelie, against a fake transport. No network."""

import json

import httpx
import pytest

from keelie_starter.keelie import API_VERSION, KeelieClient, KeelieError

TOKEN_URL = "https://keelie.test/realms/keelie/protocol/openid-connect/token"
PLAN = {
    "id": "p1",
    "product_id": "prod",
    "name": "Free",
    "status": "published",
    "currency": "usd",
    "pricing": {"fee": 0, "credits_per_period": 10},
    "scopes": [{"scope": "sun:read", "capability_ids": []}],
}


class Keelie:
    """A scripted Keelie: a queue of answers for each path, and a log of what was sent."""

    def __init__(self) -> None:
        self.answers: dict[str, list[httpx.Response]] = {}
        self.sent: list[httpx.Request] = []
        self.mints = 0

    def on(self, path: str, *answers: httpx.Response) -> None:
        self.answers.setdefault(path, []).extend(answers)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            self.mints += 1
            return httpx.Response(
                200, json={"access_token": f"token-{self.mints}", "expires_in": 300}
            )
        self.sent.append(request)
        queue = self.answers.get(request.url.path, [])
        return queue.pop(0) if queue else httpx.Response(404)


@pytest.fixture
def keelie() -> Keelie:
    return Keelie()


@pytest.fixture
def client(keelie: Keelie) -> KeelieClient:
    return KeelieClient(
        "https://keelie.test",
        TOKEN_URL,
        "admin",
        "admin-secret",
        transport=httpx.MockTransport(keelie),
        retry_wait=0,
    )


def problem(status: int, code: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={
            "type": f"https://keelie.io/problems/{code}",
            "detail": "Readable words.",
            "request_id": "req-1",
        },
        headers={"Content-Type": "application/problem+json"},
    )


async def test_every_call_pins_the_version_and_carries_the_key(
    client: KeelieClient, keelie: Keelie
) -> None:
    keelie.on(
        "/subjects",
        httpx.Response(201, json={"id": "s1", "display_name": "Ada", "subscriptions": []}),
    )
    subject = await client.create_subject("Ada", "user-1", "key-1")
    assert subject.id == "s1"
    (sent,) = keelie.sent
    assert sent.headers["X-API-Version"] == API_VERSION
    assert sent.headers["Idempotency-Key"] == "key-1"
    assert sent.headers["Authorization"] == "Bearer token-1"
    assert json.loads(sent.content) == {"display_name": "Ada", "external_ref": "user-1"}


async def test_a_401_mints_again_once(client: KeelieClient, keelie: Keelie) -> None:
    keelie.on("/plans/p1", httpx.Response(401), httpx.Response(200, json=PLAN))
    plan = await client.get_plan("p1")
    assert plan.name == "Free"
    assert keelie.mints == 2
    assert keelie.sent[-1].headers["Authorization"] == "Bearer token-2"


async def test_a_5xx_is_retried_with_the_same_key(client: KeelieClient, keelie: Keelie) -> None:
    created = {
        "id": "sub",
        "subject_id": "s1",
        "plan_id": "p1",
        "status": "active",
        "credit_balance": "10",
    }
    keelie.on("/subscriptions", httpx.Response(503), httpx.Response(201, json=created))
    subscription = await client.create_subscription("s1", "p1", "key-2")
    assert subscription.status == "active"
    assert [r.headers["Idempotency-Key"] for r in keelie.sent] == ["key-2", "key-2"]


async def test_a_write_without_a_key_is_not_retried(client: KeelieClient, keelie: Keelie) -> None:
    keelie.on("/credentials/c1:revoke", httpx.Response(503), httpx.Response(200))
    with pytest.raises(KeelieError):
        await client.revoke_credential("c1")
    assert len(keelie.sent) == 1


async def test_a_problem_is_read_by_its_type(client: KeelieClient, keelie: Keelie) -> None:
    keelie.on("/subscriptions/x:pay", problem(409, "nothing-to-pay"))
    with pytest.raises(KeelieError) as raised:
        await client.pay("x", "2026-11-23", "key-3")
    assert raised.value.code == "nothing-to-pay"
    assert raised.value.detail == "Readable words."
    assert raised.value.request_id == "req-1"


async def test_the_plans_of_a_product_are_read_page_by_page(
    client: KeelieClient, keelie: Keelie
) -> None:
    keelie.on(
        "/products/prod/plans",
        httpx.Response(200, json={"items": [PLAN], "next_cursor": "c2"}),
        httpx.Response(200, json={"items": [PLAN | {"id": "p2"}], "next_cursor": None}),
    )
    plans = await client.list_plans("prod")
    assert [p.id for p in plans] == ["p1", "p2"]
    assert keelie.sent[1].url.params["cursor"] == "c2"


async def test_the_token_address_comes_from_the_issuer_and_the_product(
    client: KeelieClient, keelie: Keelie
) -> None:
    issuer = "https://acme.keelie.test/realms/org-1"
    keelie.on(
        "/orgs/org-1", httpx.Response(200, json={"id": "org-1", "name": "Acme", "issuer": issuer})
    )
    keelie.on(
        "/products/prod",
        httpx.Response(
            200, json={"id": "prod", "name": "Daylight", "resource": "https://api.acme.test/v1"}
        ),
    )
    keelie.on(
        "/realms/org-1/.well-known/openid-configuration",
        httpx.Response(200, json={"token_endpoint": f"{issuer}/protocol/openid-connect/token"}),
    )
    info = await client.token_info("org-1", "prod")
    assert info.token_endpoint == f"{issuer}/protocol/openid-connect/token"
    assert info.resource == "https://api.acme.test/v1"
    # Cached for an hour: a second page reads nothing.
    await client.token_info("org-1", "prod")
    assert len(keelie.sent) == 3


async def test_a_product_with_no_address_gives_no_token_info(
    client: KeelieClient, keelie: Keelie
) -> None:
    keelie.on(
        "/orgs/org-1",
        httpx.Response(200, json={"id": "org-1", "name": "Acme", "issuer": "https://i"}),
    )
    keelie.on(
        "/products/prod",
        httpx.Response(200, json={"id": "prod", "name": "Daylight", "resource": None}),
    )
    with pytest.raises(KeelieError) as raised:
        await client.token_info("org-1", "prod")
    assert raised.value.code == "product-has-no-address"
