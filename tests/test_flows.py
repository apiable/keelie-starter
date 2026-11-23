"""The three ways a Consumer buys, end to end, against the demo stand-in for Keelie."""

import logging
import re

import httpx
import pytest

from keelie_starter.demo import DemoKeelie, demo_plans
from tests.conftest import PASSWORD, csrf_of, field_of, sign_up, subscribe

FREE, PRO, PAYG = (plan.id for plan in demo_plans())


def subscription_id_of(location: str) -> str:
    match = re.search(r"/subscriptions/([^/?]+)", location)
    assert match
    return match.group(1)


async def test_the_home_page_shows_the_plans(client: httpx.AsyncClient) -> None:
    page = await client.get("/")
    assert page.status_code == 200
    assert "Daylight API" in page.text
    assert "Pay as you go" in page.text
    assert "Demo mode" in page.text
    assert "keelie-starter" in page.text


async def test_signup_makes_one_subject(client: httpx.AsyncClient, keelie: DemoKeelie) -> None:
    answer = await sign_up(client)
    assert answer.status_code == 303
    assert answer.headers["location"] == "/plans"
    assert len(keelie.subjects) == 1


async def test_the_free_path(client: httpx.AsyncClient, keelie: DemoKeelie) -> None:
    await sign_up(client)
    location = await subscribe(client, FREE)
    sid = subscription_id_of(location)
    assert keelie.subscriptions[sid].status == "active"

    page = await client.get(f"/subscriptions/{sid}/credentials")
    assert "You have no credentials yet" in page.text
    form = {"csrf": csrf_of(page.text), "n": field_of(page.text, "n")}

    made = await client.post(f"/subscriptions/{sid}/credentials", data=form)
    assert made.status_code == 200
    assert "Copy the secret now" in made.text
    assert made.headers["cache-control"] == "no-store"
    assert re.search(r"demo-secret-[\w-]+", made.text)

    # A double click sends the same n: the same key, the same credential, and no second secret.
    again = await client.post(f"/subscriptions/{sid}/credentials", data=form)
    assert "This credential was made already" in again.text
    assert "demo-secret-" not in again.text
    assert len(keelie.credentials) == 1

    (credential_id,) = keelie.credentials
    listed = await client.get(f"/subscriptions/{sid}/credentials")
    revoked = await client.post(
        f"/credentials/{credential_id}/revoke", data={"csrf": csrf_of(listed.text)}
    )
    assert revoked.status_code == 303
    assert keelie.credentials[credential_id].revoked_at is not None
    assert "revoked" in (await client.get(f"/subscriptions/{sid}/credentials")).text


async def test_the_paid_path(client: httpx.AsyncClient, keelie: DemoKeelie) -> None:
    await sign_up(client)
    location = await subscribe(client, PRO)
    assert location.endswith("/pay")
    sid = subscription_id_of(location)
    assert keelie.subscriptions[sid].status == "past_due"

    page = await client.get(location)
    unticked = await client.post(location, data={"csrf": csrf_of(page.text)})
    assert unticked.status_code == 422

    checkout = await client.post(
        location, data={"csrf": csrf_of(page.text), "agree": field_of(page.text, "agree")}
    )
    assert checkout.status_code == 200
    assert "$9.00" in checkout.text
    assert keelie.subscriptions[sid].status == "past_due", "not paid until Stripe says yes"

    # A second click on "Continue" gets the same payment, not a second one.
    again = await client.post(
        location, data={"csrf": csrf_of(page.text), "agree": field_of(page.text, "agree")}
    )
    assert again.status_code == 200
    assert len(keelie._payment_replies) == 1

    confirmed = await client.post(
        f"/demo/subscriptions/{sid}/confirm-payment",
        data={
            "csrf": csrf_of(checkout.text),
            "return_path": field_of(checkout.text, "return_path"),
        },
    )
    assert confirmed.status_code == 303
    state = await client.get(f"/subscriptions/{sid}/state?kind=pay")
    assert state.json() == {"done": True, "status": "active", "balance": "100,000"}


async def test_the_packs_path(client: httpx.AsyncClient, keelie: DemoKeelie) -> None:
    await sign_up(client)
    location = await subscribe(client, PAYG)
    assert location.endswith("/top-up")
    sid = subscription_id_of(location)

    page = await client.get(location)
    n = field_of(page.text, "n")
    checkout = await client.post(
        location, data={"csrf": csrf_of(page.text), "credits": "10000", "n": n}
    )
    assert checkout.status_code == 200
    assert "$2.00" in checkout.text

    state_url = f"/subscriptions/{sid}/state?kind=top-up&n={n}"
    assert (await client.get(state_url)).json()["done"] is False
    await client.post(
        f"/demo/subscriptions/{sid}/confirm-payment",
        data={
            "csrf": csrf_of(checkout.text),
            "return_path": field_of(checkout.text, "return_path"),
        },
    )
    assert (await client.get(state_url)).json() == {
        "done": True,
        "status": "active",
        "balance": "10,000",
    }


async def test_a_pack_the_plan_does_not_sell_is_refused(client: httpx.AsyncClient) -> None:
    await sign_up(client)
    location = await subscribe(client, PAYG)
    page = await client.get(location)
    answer = await client.post(
        location, data={"csrf": csrf_of(page.text), "credits": "7", "n": "0"}
    )
    assert answer.status_code == 404


async def test_choosing_a_plan_twice_keeps_one_subscription(
    client: httpx.AsyncClient, keelie: DemoKeelie
) -> None:
    await sign_up(client)
    first = await subscribe(client, FREE)
    second = await subscribe(client, FREE)
    assert subscription_id_of(first) == subscription_id_of(second)
    assert len(keelie.subscriptions) == 1


async def test_another_users_subscription_is_not_found(
    app: object, client: httpx.AsyncClient
) -> None:
    await sign_up(client, "ada@example.com")
    sid = subscription_id_of(await subscribe(client, FREE))
    await client.post("/logout", data={"csrf": csrf_of((await client.get("/")).text)})

    await sign_up(client, "eve@example.com")
    for path in (f"/subscriptions/{sid}", f"/subscriptions/{sid}/credentials"):
        assert (await client.get(path)).status_code == 404


async def test_a_wrong_csrf_token_is_refused(client: httpx.AsyncClient) -> None:
    await sign_up(client)
    answer = await client.post("/subscribe", data={"csrf": "forged", "plan_id": FREE})
    assert answer.status_code == 404


async def test_pages_need_a_login(client: httpx.AsyncClient) -> None:
    answer = await client.get("/plans")
    assert answer.status_code == 303
    assert answer.headers["location"] == "/login"


@pytest.mark.parametrize(("password", "status"), [(PASSWORD, 303), ("wrong password!", 401)])
async def test_login(client: httpx.AsyncClient, password: str, status: int) -> None:
    await sign_up(client)
    await client.post("/logout", data={"csrf": csrf_of((await client.get("/")).text)})
    page = await client.get("/login")
    answer = await client.post(
        "/login",
        data={"csrf": csrf_of(page.text), "email": "ada@example.com", "password": password},
    )
    assert answer.status_code == status


async def test_no_secret_reaches_the_log(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    await sign_up(client)
    sid = subscription_id_of(await subscribe(client, FREE))
    page = await client.get(f"/subscriptions/{sid}/credentials")
    made = await client.post(
        f"/subscriptions/{sid}/credentials",
        data={"csrf": csrf_of(page.text), "n": field_of(page.text, "n")},
    )
    secret = re.search(r"demo-secret-[\w-]+", made.text)
    assert secret
    assert secret.group(0) not in caplog.text
    assert PASSWORD not in caplog.text
