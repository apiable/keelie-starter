from datetime import UTC, date, datetime, timedelta

import pytest

from daylight.routes import daylight
from daylight.sun import sun_day

MINUTES = timedelta(minutes=3)


def near(actual: datetime | None, expected: datetime) -> bool:
    return actual is not None and abs(actual - expected) <= MINUTES


def test_helsinki_at_midsummer() -> None:
    # Published times for Helsinki on 2026-06-21: sunrise 03:54 and sunset 22:50, EEST (UTC+3).
    sun = sun_day(date(2026, 6, 21), 60.1699, 24.9384)
    assert near(sun.sunrise, datetime(2026, 6, 21, 0, 54, tzinfo=UTC))
    assert near(sun.sunset, datetime(2026, 6, 21, 19, 50, tzinfo=UTC))
    assert sun.polar is None


def test_a_day_on_the_equator_is_about_twelve_hours() -> None:
    sun = sun_day(date(2026, 3, 20), 0.0, 0.0)
    assert abs(sun.day_length - timedelta(hours=12, minutes=7)) <= MINUTES


@pytest.mark.parametrize(
    ("day", "polar", "length"),
    [(date(2026, 6, 21), "day", timedelta(days=1)), (date(2026, 12, 21), "night", timedelta(0))],
)
def test_beyond_the_polar_circle(day: date, polar: str, length: timedelta) -> None:
    sun = sun_day(day, 69.6496, 18.9560)  # Tromsø
    assert sun.polar == polar
    assert sun.sunrise is None and sun.sunset is None
    assert sun.day_length == length


def test_the_north_pole() -> None:
    assert sun_day(date(2026, 6, 21), 90.0, 0.0).polar == "day"
    assert sun_day(date(2026, 12, 21), 90.0, 0.0).polar == "night"


def test_the_answer_model() -> None:
    answer = daylight(date(2026, 6, 21), 60.1699, 24.9384)
    assert answer.day_length_seconds > 18 * 3600
    assert answer.polar is None
