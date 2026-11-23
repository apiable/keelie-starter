"""`uv run keelie-starter`: the site on http://127.0.0.1:8000, in demo mode unless .env says live."""

import argparse
import logging

import uvicorn

from keelie_starter.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Keelie starter website.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    uvicorn.run(create_app(), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
