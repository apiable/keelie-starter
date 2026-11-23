# Daylight

The API behind keelie-starter's demo. It answers one question: when does the sun rise and set, at
this place, on this date? It computes the answer from NOAA's solar formulas, so it has no data
source and no third-party content.

It shows the Provider side of Keelie in one file, [`daylight/main.py`](daylight/main.py):

- The Keelie SDK checks each token **inside this process**, against the issuer's key set.
- It checks the `sun:read` scope on `GET /sun`.
- It records one credit for each good answer, and reports usage to Keelie in batches.
- Nothing goes through Keelie on the way. Keelie is not in the path of the call.

## Run it

You need a Provider in Keelie, and the SDK credential from its signup. Follow the
[SDK quickstart](https://github.com/apiable/keelie-sdk/blob/main/docs/quickstart.md), steps 1 and 2.

```bash
cd example-api
cp .env.example .env        # then fill it in
uv run --env-file .env uvicorn daylight.main:app --port 8090
```

The SDK needs https for your API and for the issuer. Test against a hosted Keelie.

## Sell it

1. Register the product in Keelie from the address of [`openapi.json`](openapi.json). Keelie reads
   it from a public `https` address only, such as this file's raw address on GitHub.
2. Set the product's `resource` to the address your Consumers call, the same as `KEELIE_RESOURCE`.
3. Make and publish plans with the scope `sun:read`.
4. Point keelie-starter at the product: `KEELIE_PRODUCT_ID` in its `.env`.

After a change to the route, run `uv run python export_openapi.py` and commit `openapi.json`.

## Check it

```bash
uv run ruff check . && uv run mypy && uv run pytest
```
