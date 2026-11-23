"""Sunrise, sunset and solar noon, computed from the date and the place. No data source.

The formulas are NOAA's "General Solar Position Calculations". They are accurate to a minute or
two between the polar circles, which is enough for a demo. They are not for navigation.
"""

import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Literal

# The sun's centre is 0.833 degrees below the horizon at sunrise: refraction and its radius.
ZENITH = math.radians(90.833)


@dataclass(frozen=True)
class SunDay:
    solar_noon: datetime
    sunrise: datetime | None
    sunset: datetime | None
    day_length: timedelta
    polar: Literal["day", "night"] | None


def sun_day(day: date, latitude: float, longitude: float) -> SunDay:
    """The sun on one UTC date, at one place. Latitude north and longitude east are positive."""
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        raise ValueError("latitude must be in [-90, 90] and longitude in [-180, 180]")

    # The fractional year, in radians, at noon of the date.
    gamma = 2 * math.pi / 365 * (day.timetuple().tm_yday - 1)
    equation_of_time = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    declination = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )

    midnight = datetime.combine(day, time(0), tzinfo=UTC)

    def at(minutes: float) -> datetime:
        return (midnight + timedelta(minutes=minutes)).replace(microsecond=0)

    noon_minutes = 720 - 4 * longitude - equation_of_time
    if abs(latitude) == 90:
        # At a pole the sun stays up while the declination is on the pole's side.
        cos_hour_angle = -2.0 if latitude * declination > 0 else 2.0
    else:
        phi = math.radians(latitude)
        cos_hour_angle = math.cos(ZENITH) / (math.cos(phi) * math.cos(declination)) - (
            math.tan(phi) * math.tan(declination)
        )

    if cos_hour_angle > 1:
        return SunDay(at(noon_minutes), None, None, timedelta(0), "night")
    if cos_hour_angle < -1:
        return SunDay(at(noon_minutes), None, None, timedelta(days=1), "day")

    hour_angle = math.degrees(math.acos(cos_hour_angle))
    sunrise = at(noon_minutes - 4 * hour_angle)
    sunset = at(noon_minutes + 4 * hour_angle)
    return SunDay(at(noon_minutes), sunrise, sunset, sunset - sunrise, None)
