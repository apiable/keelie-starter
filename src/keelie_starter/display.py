"""Formatting for the pages. Display only: nothing here feeds a calculation or a call to Keelie."""

from decimal import Decimal, InvalidOperation

CURRENCY_SIGNS = {"usd": "$", "eur": "€", "gbp": "£"}


def money(minor: int, currency: str) -> str:
    """Integer minor units, such as 4900 usd, shown as $49.00."""
    sign = CURRENCY_SIGNS.get(currency.lower(), "")
    text = f"{minor // 100:,}.{minor % 100:02d}"
    return f"{sign}{text}" if sign else f"{text} {currency.upper()}"


def credits_text(value: str | int) -> str:
    """A credit amount from Keelie's decimal string, with thousands separators and no float."""
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return str(value)
    if number == number.to_integral():
        return f"{int(number):,}"
    return f"{number.normalize():,f}"


def per_credit(price: int, credits: int, currency: str) -> str:
    """The price of one credit, rounded for display only."""
    each = Decimal(price) / Decimal(credits) / 100
    sign = CURRENCY_SIGNS.get(currency.lower(), "")
    shown = each.quantize(Decimal("0.01")) if each >= Decimal("0.01") else each.normalize()
    return f"{sign}{shown:f}" if sign else f"{shown:f} {currency.upper()}"
