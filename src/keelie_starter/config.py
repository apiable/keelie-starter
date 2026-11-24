"""The site's settings. This is the only module that reads the environment.

In `demo` mode the site runs with no Keelie account: an in-memory stand-in plays Keelie, and a
button plays Stripe. In `live` mode it calls Keelie with the Provider's admin credential, which
it reads here and nowhere else.
"""

from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LIVE_REQUIRED = (
    "keelie_api",
    "keelie_token_url",
    "keelie_client_id",
    "keelie_org_id",
    "keelie_product_id",
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    keelie_mode: Literal["demo", "live"] = "demo"

    # Live mode only. The Provider's admin credential: see docs/prompts/consumer-site/00-context.md
    # in Keelie. Keep this server as locked down as your own admin tools.
    keelie_api: str = ""
    keelie_token_url: str = ""
    keelie_client_id: str = ""
    keelie_client_secret: SecretStr = SecretStr("")
    keelie_org_id: str = ""
    keelie_product_id: str = ""

    # The website itself.
    site_url: str = "http://127.0.0.1:8000"
    site_name: str = "Daylight API"
    site_tagline: str = "Sunrise, sunset and day length for any place on Earth, on any date."
    accent_color: str = Field("#b45309", pattern=r"^#[0-9a-fA-F]{6}$")
    database_path: str = "site.db"

    # The version of your terms. A Consumer agrees to this exact string before paying.
    terms_version: str = "2026-11-23"

    # Your own words for each scope of your plans. Keelie names a scope; you explain it.
    scope_words: dict[str, str] = {
        "sun:read": "Read sunrise, sunset and day length for any place and date",
    }

    # An example call for the "how to use" box: a path on your API, after its address.
    api_example_path: str = "/sun?lat=60.17&lon=24.94"

    # Where the example call goes. Leave it empty when your API answers at the token's audience.
    # Set it when it does not, such as an API that runs on your own machine.
    api_base_url: str = ""

    # Demo mode only: the address shown for the API, since there is no product to read it from.
    demo_api_url: str = "https://daylight.example.com/v1"

    @model_validator(mode="after")
    def live_needs_keelie(self) -> Self:
        if self.keelie_mode == "live":
            missing = [name.upper() for name in LIVE_REQUIRED if not getattr(self, name)]
            if not self.keelie_client_secret.get_secret_value():
                missing.append("KEELIE_CLIENT_SECRET")
            if missing:
                raise ValueError(f"KEELIE_MODE=live needs {', '.join(missing)}")
        return self

    @property
    def demo(self) -> bool:
        return self.keelie_mode == "demo"

    @property
    def secure_cookies(self) -> bool:
        return self.site_url.startswith("https://")
