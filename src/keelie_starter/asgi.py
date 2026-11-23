"""The ASGI app, for a server of your choice: `uvicorn keelie_starter.asgi:app`."""

from keelie_starter.app import create_app

app = create_app()
