"""Sends a token of the SDK credential with each request to Keelie.

Copied from the Keelie SDK quickstart. The SDK holds no secret of its own, so the client it is
given carries the SDK credential.
"""

import time
from collections.abc import Generator

import httpx


class SdkCredential(httpx.Auth):
    requires_response_body = True

    def __init__(self, token_url: str, client_id: str, client_secret: str) -> None:
        self._token_url = token_url
        self._form = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        self._token = ""
        self._expires_at = 0.0

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response]:
        if time.monotonic() >= self._expires_at:
            yield from self._mint()
        request.headers["Authorization"] = f"Bearer {self._token}"
        response = yield request
        if response.status_code == 401:
            yield from self._mint()
            request.headers["Authorization"] = f"Bearer {self._token}"
            yield request

    def _mint(self) -> Generator[httpx.Request, httpx.Response]:
        response = yield httpx.Request("POST", self._token_url, data=self._form)
        response.raise_for_status()
        body = response.json()
        self._token = body["access_token"]
        # Mint again before the token expires.
        lifetime = body["expires_in"]
        self._expires_at = time.monotonic() + max(lifetime - 30, lifetime / 2)
