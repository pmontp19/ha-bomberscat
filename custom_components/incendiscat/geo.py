"""Pure geometry helpers for incendiscat.

No Home Assistant imports here on purpose: this module must be testable in
complete isolation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from math import asin, cos, radians, sin, sqrt

# Mean Earth radius (km), IUGG value. Good enough for wildfire-distance UX;
# we are not doing geodesy.
EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two WGS84 points, in kilometers."""
    lat1_r, lon1_r, lat2_r, lon2_r = (radians(v) for v in (lat1, lon1, lat2, lon2))
    dlat = lat2_r - lat1_r
    dlon = lon2_r - lon1_r
    a = sin(dlat / 2) ** 2 + cos(lat1_r) * cos(lat2_r) * sin(dlon / 2) ** 2
    # Clamp for float rounding: `a` can drift a hair above 1.0 for antipodal
    # points, which would make sqrt() raise on the a > 1 case.
    a = min(1.0, max(0.0, a))
    c = 2 * asin(sqrt(a))
    return EARTH_RADIUS_KM * c


def is_within_radius(
    lat1: float, lon1: float, lat2: float, lon2: float, radius_km: float
) -> bool:
    """Whether two WGS84 points are within `radius_km` of each other."""
    return haversine_km(lat1, lon1, lat2, lon2) <= radius_km


def filter_by_radius[T](
    items: Iterable[T],
    center_lat: float,
    center_lon: float,
    radius_km: float,
    *,
    lat_of: Callable[[T], float] = lambda item: item.lat,  # type: ignore[attr-defined]
    lon_of: Callable[[T], float] = lambda item: item.lon,  # type: ignore[attr-defined]
) -> list[T]:
    """Keep only the items within `radius_km` of `(center_lat, center_lon)`.

    Duck-typed on purpose (defaults read `.lat`/`.lon`) so it works with
    `Incident` without this module importing `models`.
    """
    return [
        item
        for item in items
        if is_within_radius(
            center_lat, center_lon, lat_of(item), lon_of(item), radius_km
        )
    ]
