"""Daylight, protected by the Keelie SDK. Run with `uv run --env-file .env uvicorn daylight.main:app`.

The SDK checks the token of every request inside this process, against the issuer's key set,
and checks `sun:read` on `GET /sun`. After a good answer it records one credit, which it reports
to Keelie in batches. Nothing goes through Keelie on the way: it is not in the path of the call.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from apiable import (
    InsufficientScope,
    ProtectedResourceMetadata,
    ScopeMap,
    TokenGuard,
    TokenVerifier,
    UsageReporter,
)
from fastapi import FastAPI, Request
from fastapi.responses import Response

from daylight.keelie_auth import SdkCredential
from daylight.routes import DESCRIPTION, SUMMARY, Day, Daylight, Lat, Lon, daylight

env = os.environ
keelie = httpx.AsyncClient(
    base_url=env["KEELIE_URL"],
    auth=SdkCredential(
        env["KEELIE_TOKEN_URL"], env["KEELIE_SDK_CLIENT_ID"], env["KEELIE_SDK_CLIENT_SECRET"]
    ),
    timeout=10,
)
verifier = TokenVerifier(env["KEELIE_ISSUER"], env["KEELIE_RESOURCE"], env["KEELIE_ORG_ID"])
metadata = ProtectedResourceMetadata(
    env["KEELIE_RESOURCE"], env["KEELIE_ISSUER"], scopes_supported=["sun:read"]
)
scopes = ScopeMap({("GET", "/sun"): "sun:read"})
reporter = UsageReporter(keelie)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await reporter.start()
    yield
    await reporter.aclose()
    await keelie.aclose()


app = FastAPI(title="Daylight", version="1.0.0", lifespan=lifespan)
app.add_middleware(TokenGuard, verifier=verifier, metadata=metadata)


@app.exception_handler(InsufficientScope)
async def insufficient_scope(request: Request, exc: InsufficientScope) -> Response:
    return Response(
        status_code=403, headers={"WWW-Authenticate": metadata.challenge("insufficient_scope")}
    )


@app.get("/health", include_in_schema=False)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/sun", operation_id="getDaylight", summary=SUMMARY, description=DESCRIPTION)
async def get_sun(request: Request, lat: Lat, lon: Lon, date: Day = None) -> Daylight:
    claims = request.state.apiable_claims
    scopes.check(claims, "GET", "/sun")
    answer = daylight(date, lat, lon)
    # Only a good answer costs a credit. A 422 is free.
    reporter.record(claims.azp, "GET /sun", credits=1, consumer_id=claims.consumer_id)
    return answer
