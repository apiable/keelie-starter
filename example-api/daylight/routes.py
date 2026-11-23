"""The one operation Daylight sells: its parameters, its answer, and the work. No SDK here, so the
tests run without a Keelie account."""

from datetime import UTC, date, datetime
from typing import Annotated, Literal

from fastapi import HTTPException, Query
from pydantic import BaseModel

from daylight.sun import sun_day


class Daylight(BaseModel):
    date: date
    latitude: float
    longitude: float
    sunrise: datetime | None
    sunset: datetime | None
    solar_noon: datetime
    day_length_seconds: int
    polar: Literal["day", "night"] | None


def daylight(day: date | None, lat: float, lon: float) -> Daylight:
    day = day or datetime.now(UTC).date()
    try:
        sun = sun_day(day, lat, lon)
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return Daylight(
        date=day,
        latitude=lat,
        longitude=lon,
        sunrise=sun.sunrise,
        sunset=sun.sunset,
        solar_noon=sun.solar_noon,
        day_length_seconds=int(sun.day_length.total_seconds()),
        polar=sun.polar,
    )


Lat = Annotated[float, Query(ge=-90, le=90, description="Latitude in degrees, north positive.")]
Lon = Annotated[float, Query(ge=-180, le=180, description="Longitude in degrees, east positive.")]
Day = Annotated[
    date | None, Query(description="The UTC date, as YYYY-MM-DD. Leave it out for today.")
]

SUMMARY = "Get sunrise, sunset and day length"
DESCRIPTION = (
    "Sunrise, sunset and solar noon in UTC for one place on one date, and the length of the day. "
    "Inside the polar circles, `polar` says when the sun does not rise or does not set."
)
