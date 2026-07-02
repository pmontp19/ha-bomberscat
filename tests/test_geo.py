"""Tests for the pure geometry helpers in geo.py."""

from dataclasses import dataclass

from custom_components.incendiscat.geo import (
    EARTH_RADIUS_KM,
    filter_by_radius,
    haversine_km,
    is_within_radius,
)

# Reference distance verified independently with two formulas (haversine and
# the spherical law of cosines) using city-center coordinates:
#   Barcelona (41.3851, 2.1734) <-> Girona (41.9794, 2.8214) ~= 85.22 km.
BARCELONA = (41.3851, 2.1734)
GIRONA = (41.9794, 2.8214)
BARCELONA_GIRONA_KM = 85.22


def test_haversine_known_distance_barcelona_girona() -> None:
    distance = haversine_km(*BARCELONA, *GIRONA)
    assert abs(distance - BARCELONA_GIRONA_KM) < 0.1


def test_haversine_is_symmetric() -> None:
    a = haversine_km(*BARCELONA, *GIRONA)
    b = haversine_km(*GIRONA, *BARCELONA)
    assert abs(a - b) < 1e-9


def test_haversine_same_point_is_zero() -> None:
    assert haversine_km(*BARCELONA, *BARCELONA) == 0.0


def test_haversine_antipodes() -> None:
    # Antipodal points are exactly half the great-circle circumference apart.
    distance = haversine_km(0.0, 0.0, 0.0, 180.0)
    expected = EARTH_RADIUS_KM * 3.141592653589793
    assert abs(distance - expected) < 0.1
    assert abs(distance - 20015.1) < 0.5


def test_haversine_antipodes_arbitrary_point() -> None:
    distance = haversine_km(41.0, 2.0, -41.0, -178.0)
    assert abs(distance - 20015.1) < 0.5


def test_is_within_radius_true_and_false() -> None:
    assert is_within_radius(*BARCELONA, *GIRONA, radius_km=100)
    assert not is_within_radius(*BARCELONA, *GIRONA, radius_km=50)


def test_is_within_radius_edge_equals_radius() -> None:
    distance = haversine_km(*BARCELONA, *GIRONA)
    assert is_within_radius(*BARCELONA, *GIRONA, radius_km=distance)
    assert not is_within_radius(*BARCELONA, *GIRONA, radius_km=distance - 0.01)


@dataclass
class _Point:
    lat: float
    lon: float
    label: str


def test_filter_by_radius_default_attrs() -> None:
    points = [
        _Point(*BARCELONA, "barcelona"),
        _Point(*GIRONA, "girona"),
        _Point(0.0, 0.0, "null_island"),
    ]
    kept = filter_by_radius(points, *BARCELONA, radius_km=100)
    assert {p.label for p in kept} == {"barcelona", "girona"}


def test_filter_by_radius_empty_input() -> None:
    assert filter_by_radius([], *BARCELONA, radius_km=100) == []


def test_filter_by_radius_zero_radius_keeps_only_center() -> None:
    points = [_Point(*BARCELONA, "barcelona"), _Point(*GIRONA, "girona")]
    kept = filter_by_radius(points, *BARCELONA, radius_km=0)
    assert [p.label for p in kept] == ["barcelona"]


def test_filter_by_radius_custom_accessors() -> None:
    rows = [
        {"latitude": BARCELONA[0], "longitude": BARCELONA[1], "label": "barcelona"},
        {"latitude": GIRONA[0], "longitude": GIRONA[1], "label": "girona"},
    ]
    kept = filter_by_radius(
        rows,
        *BARCELONA,
        radius_km=10,
        lat_of=lambda r: r["latitude"],
        lon_of=lambda r: r["longitude"],
    )
    assert [r["label"] for r in kept] == ["barcelona"]
