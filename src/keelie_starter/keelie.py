"""The one module that calls Keelie. Every page uses it. The browser never does.

Built from prompt 01 of Keelie's consumer-site guide, against the contract `openapi/keelie.json`.
It holds the Provider's admin credential, so it never logs a header, a token or a body: only the
method, the path, the status and Keelie's `request_id`.
"""

import asyncio
import logging
import time
import uuid
from typing import Protocol

import httpx
from pydantic import BaseModel

from keelie_starter.models import (
    Credential,
    CredentialCreated,
    Org,
    Payment,
    Plan,
    PlanPage,
    Product,
    Subject,
    Subscription,
    TokenInfo,
)

# `info.version` of the contract. Pinned, so a newer Keelie cannot change what we get.
API_VERSION = "2026-10-08"
PLAN_CACHE_SECONDS = 300
TOKEN_INFO_CACHE_SECONDS = 3600

log = logging.getLogger("keelie_starter.keelie")

type Json = dict[str, object]


def action_key(namespace: uuid.UUID, *parts: str) -> str:
    """An Idempotency-Key made from the action, so a retry or a double click sends the same one."""
    return str(uuid.uuid5(namespace, ":".join(parts)))


class KeelieError(Exception):
    """One RFC 9457 problem from Keelie. Branch on `code`, show `detail`, log `request_id`."""

    def __init__(self, status: int, code: str, detail: str | None, request_id: str | None) -> None:
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code
        self.detail = detail or "Something went wrong. Please try again."
        self.request_id = request_id


class KeelieApi(Protocol):
    """What the site needs from Keelie. `KeelieClient` calls Keelie; `DemoKeelie` plays it."""

    async def create_subject(self, display_name: str, external_ref: str, key: str) -> Subject: ...
    async def get_subject(self, subject_id: str) -> Subject: ...
    async def list_plans(self, product_id: str) -> list[Plan]: ...
    async def get_plan(self, plan_id: str) -> Plan: ...
    async def create_subscription(
        self, subject_id: str, plan_id: str, key: str
    ) -> Subscription: ...
    async def get_subscription(self, subscription_id: str) -> Subscription: ...
    async def pay(self, subscription_id: str, consent_ref: str, key: str) -> Payment: ...
    async def top_up(
        self, subscription_id: str, credits: int, save_card: bool, key: str
    ) -> Payment: ...
    async def create_credential(
        self, subscription_id: str, key: str
    ) -> tuple[CredentialCreated, bool]: ...
    async def get_credential(self, credential_id: str) -> Credential: ...
    async def revoke_credential(self, credential_id: str) -> None: ...
    async def token_info(self, org_id: str, product_id: str) -> TokenInfo: ...
    async def aclose(self) -> None: ...


def _problem(response: httpx.Response) -> KeelieError:
    try:
        body = response.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    code = str(body.get("type") or "").rsplit("/", 1)[-1] or f"http-{response.status_code}"
    request_id = body.get("request_id") or response.headers.get("X-Request-Id")
    detail = body.get("detail")
    return KeelieError(
        response.status_code,
        code,
        str(detail) if detail else None,
        str(request_id) if request_id else None,
    )


class KeelieClient:
    """Keelie over HTTP, with the Provider's admin credential."""

    def __init__(
        self,
        api: str,
        token_url: str,
        client_id: str,
        client_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_wait: float = 0.5,
    ) -> None:
        self._http = httpx.AsyncClient(base_url=api.rstrip("/"), timeout=20, transport=transport)
        self._token_url = token_url
        self._auth = (client_id, client_secret)
        self._retry_wait = retry_wait
        self._token: str | None = None
        self._token_expires = 0.0
        self._token_lock = asyncio.Lock()
        self._plans: dict[str, tuple[float, Plan]] = {}
        self._token_info: dict[tuple[str, str], tuple[float, TokenInfo]] = {}

    async def aclose(self) -> None:
        await self._http.aclose()

    # The Provider's token. One mint, kept in memory, renewed 60 s before it expires.

    async def _bearer(self, *, renew: bool = False) -> str:
        async with self._token_lock:
            if renew or self._token is None or time.monotonic() > self._token_expires - 60:
                # The token URL is OAuth's, not Keelie's: no X-API-Version here.
                answer = await self._http.post(
                    self._token_url, auth=self._auth, data={"grant_type": "client_credentials"}
                )
                if answer.status_code != 200:
                    log.error("token mint failed: %s", answer.status_code)
                    raise KeelieError(answer.status_code, "token-mint-failed", None, None)
                minted = answer.json()
                self._token = str(minted["access_token"])
                self._token_expires = time.monotonic() + int(minted["expires_in"])
                log.info("token minted, lives %ss", minted["expires_in"])
            return self._token

    async def call(
        self,
        method: str,
        path: str,
        *,
        body: Json | None = None,
        key: str | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[Json | None, bool]:
        """One call to Keelie. Returns the body and whether Keelie replayed an earlier answer.

        A GET, or a write with an Idempotency-Key, is retried on a network error, a 5xx and a
        429. A write without a key is never retried: it could run twice.
        """
        renewed = False
        attempt = 0
        retryable = method == "GET" or key is not None
        while True:
            attempt += 1
            headers = {
                "Authorization": f"Bearer {await self._bearer(renew=renewed and attempt == 1)}",
                "X-API-Version": API_VERSION,
            }
            if key is not None:
                headers["Idempotency-Key"] = key
            try:
                answer = await self._http.request(
                    method, path, json=body, params=params, headers=headers
                )
            except httpx.TransportError:
                log.warning("%s %s: network error, attempt %s", method, path, attempt)
                if retryable and attempt < 3:
                    await asyncio.sleep(self._retry_wait * attempt)
                    continue
                raise KeelieError(
                    503, "network-error", "Keelie did not answer. Please try again.", None
                ) from None

            request_id = answer.headers.get("X-Request-Id")
            log.info("%s %s -> %s request_id=%s", method, path, answer.status_code, request_id)

            if answer.status_code == 401 and not renewed:
                renewed = True
                attempt = 0
                continue
            if answer.status_code == 429 and attempt < 3:
                wait = min(float(answer.headers.get("Retry-After", "1") or 1), 5.0)
                await asyncio.sleep(wait * self._retry_wait / 0.5)
                continue
            if answer.status_code == 409 and attempt < 3:
                problem = _problem(answer)
                if problem.code == "idempotency-key-in-flight":
                    await asyncio.sleep(self._retry_wait * 2)
                    continue
                raise problem
            if answer.status_code >= 500 and retryable and attempt < 3:
                await asyncio.sleep(self._retry_wait * attempt)
                continue
            if answer.is_success:
                replayed = answer.headers.get("Idempotency-Replayed", "").lower() == "true"
                data = answer.json() if answer.content else None
                return (data if isinstance(data, dict) else None), replayed
            raise _problem(answer)

    async def _read[M: BaseModel](
        self,
        model: type[M],
        method: str,
        path: str,
        *,
        body: Json | None = None,
        key: str | None = None,
    ) -> M:
        data, _ = await self.call(method, path, body=body, key=key)
        if data is None:
            raise KeelieError(502, "empty-answer", None, None)
        return model.model_validate(data)

    # The calls the website makes, one for each operation in the contract.

    async def create_subject(self, display_name: str, external_ref: str, key: str) -> Subject:
        payload: Json = {"display_name": display_name, "external_ref": external_ref}
        return await self._read(Subject, "POST", "/subjects", body=payload, key=key)

    async def get_subject(self, subject_id: str) -> Subject:
        return await self._read(Subject, "GET", f"/subjects/{subject_id}")

    async def list_plans(self, product_id: str) -> list[Plan]:
        plans: list[Plan] = []
        cursor: str | None = None
        while True:
            params = {"limit": "100"} | ({"cursor": cursor} if cursor else {})
            data, _ = await self.call("GET", f"/products/{product_id}/plans", params=params)
            page = PlanPage.model_validate(data)
            plans.extend(page.items)
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        now = time.monotonic()
        for plan in plans:
            self._plans[plan.id] = (now, plan)
        return plans

    async def get_plan(self, plan_id: str) -> Plan:
        cached = self._plans.get(plan_id)
        if cached and time.monotonic() - cached[0] < PLAN_CACHE_SECONDS:
            return cached[1]
        plan = await self._read(Plan, "GET", f"/plans/{plan_id}")
        self._plans[plan_id] = (time.monotonic(), plan)
        return plan

    async def create_subscription(self, subject_id: str, plan_id: str, key: str) -> Subscription:
        payload: Json = {"subject_id": subject_id, "plan_id": plan_id}
        return await self._read(Subscription, "POST", "/subscriptions", body=payload, key=key)

    async def get_subscription(self, subscription_id: str) -> Subscription:
        return await self._read(Subscription, "GET", f"/subscriptions/{subscription_id}")

    async def pay(self, subscription_id: str, consent_ref: str, key: str) -> Payment:
        # The colon is part of the path, as the contract writes it. Not encoded.
        path = f"/subscriptions/{subscription_id}:pay"
        return await self._read(Payment, "POST", path, body={"consent_ref": consent_ref}, key=key)

    async def top_up(
        self, subscription_id: str, credits: int, save_card: bool, key: str
    ) -> Payment:
        payload: Json = {"credits": credits}
        if save_card:
            payload["save_for_auto_top_up"] = True
        path = f"/subscriptions/{subscription_id}:top-up"
        return await self._read(Payment, "POST", path, body=payload, key=key)

    async def create_credential(
        self, subscription_id: str, key: str
    ) -> tuple[CredentialCreated, bool]:
        payload: Json = {"subscription_id": subscription_id}
        data, replayed = await self.call("POST", "/credentials", body=payload, key=key)
        return CredentialCreated.model_validate(data), replayed

    async def get_credential(self, credential_id: str) -> Credential:
        return await self._read(Credential, "GET", f"/credentials/{credential_id}")

    async def revoke_credential(self, credential_id: str) -> None:
        await self.call("POST", f"/credentials/{credential_id}:revoke")

    async def token_info(self, org_id: str, product_id: str) -> TokenInfo:
        """Where the Provider's Consumers mint, and the audience their tokens carry (prompt 05).

        Not KEELIE_TOKEN_URL: that address is for the Provider's own credential. The issuer moves
        when the Provider switches to its own auth domain, so it is read, not configured.
        """
        cached = self._token_info.get((org_id, product_id))
        if cached and time.monotonic() - cached[0] < TOKEN_INFO_CACHE_SECONDS:
            return cached[1]
        org = await self._read(Org, "GET", f"/orgs/{org_id}")
        product = await self._read(Product, "GET", f"/products/{product_id}")
        if not product.resource:
            # A setup step for the Provider: PATCH /products/{productId} with its address.
            raise KeelieError(
                409, "product-has-no-address", "This API isn't ready for credentials yet.", None
            )
        discovery = await self._http.get(
            f"{org.issuer.rstrip('/')}/.well-known/openid-configuration"
        )
        if discovery.status_code != 200:
            raise KeelieError(502, "issuer-unreachable", None, None)
        info = TokenInfo(
            issuer=org.issuer,
            token_endpoint=str(discovery.json()["token_endpoint"]),
            resource=product.resource,
        )
        self._token_info[(org_id, product_id)] = (time.monotonic(), info)
        return info
