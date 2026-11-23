import pytest
from pydantic import ValidationError

from keelie_starter.config import Settings
from keelie_starter.display import credits_text, money, per_credit


@pytest.mark.parametrize(
    ("minor", "currency", "shown"),
    [(900, "usd", "$9.00"), (123456, "eur", "€1,234.56"), (5, "sek", "0.05 SEK")],
)
def test_money(minor: int, currency: str, shown: str) -> None:
    assert money(minor, currency) == shown


@pytest.mark.parametrize(("value", "shown"), [("100000", "100,000"), ("10.50", "10.5"), ("x", "x")])
def test_credits_text(value: str, shown: str) -> None:
    assert credits_text(value) == shown


def test_per_credit_keeps_small_prices() -> None:
    assert per_credit(200, 10_000, "usd") == "$0.0002"
    assert per_credit(500, 100, "usd") == "$0.05"


def test_live_mode_says_what_is_missing() -> None:
    with pytest.raises(ValidationError) as raised:
        Settings(keelie_mode="live", _env_file=None)
    assert "KEELIE_API" in str(raised.value)
    assert "KEELIE_CLIENT_SECRET" in str(raised.value)


def test_demo_mode_needs_nothing() -> None:
    assert Settings(_env_file=None).demo
