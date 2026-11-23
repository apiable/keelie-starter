"""Write openapi.json, the file you register with Keelie as the product's address.

It needs no Keelie account: placeholder settings stand in, and nothing is called.
Run: uv run python export_openapi.py
"""

import json
import os
from pathlib import Path

for name in ("KEELIE_URL", "KEELIE_TOKEN_URL", "KEELIE_ISSUER", "KEELIE_RESOURCE"):
    os.environ.setdefault(name, "https://placeholder.invalid")
for name in ("KEELIE_ORG_ID", "KEELIE_SDK_CLIENT_ID", "KEELIE_SDK_CLIENT_SECRET"):
    os.environ.setdefault(name, "00000000-0000-0000-0000-000000000000")

from daylight.main import app  # noqa: E402

document = app.openapi()
# A Provider sets its own address. Keelie reads the operations, not the server list.
document["servers"] = [{"url": "https://daylight.example.com/v1"}]
Path(__file__).with_name("openapi.json").write_text(json.dumps(document, indent=2) + "\n")
