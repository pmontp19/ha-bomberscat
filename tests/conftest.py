"""Shared pytest fixtures for incendiscat tests."""

from datetime import UTC, datetime, timedelta

import pytest
from custom_components.incendiscat.const import (
    CONF_ALERT_RADIUS,
    CONF_TRACK_RADIUS,
    DOMAIN,
)
from custom_components.incendiscat.models import Fase, Incident, Tipus
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from pytest_homeassistant_custom_component.common import MockConfigEntry

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for every test automatically.

    Required by pytest-homeassistant-custom-component so that Home Assistant
    picks up custom_components/incendiscat during tests.
    """
    return enable_custom_integrations


# Home reference point shared by coordinator/lifecycle/event tests (same
# coordinates as tests/test_geo.py's BARCELONA constant).
HOME_LAT = 41.3851
HOME_LON = 2.1734


def make_config_entry(
    *,
    track_radius: float = 100.0,
    alert_radius: float = 30.0,
    home_lat: float = HOME_LAT,
    home_lon: float = HOME_LON,
    options: dict | None = None,
) -> MockConfigEntry:
    """Build a `MockConfigEntry` for the incendiscat domain with sane defaults."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_LATITUDE: home_lat,
            CONF_LONGITUDE: home_lon,
            CONF_TRACK_RADIUS: track_radius,
            CONF_ALERT_RADIUS: alert_radius,
        },
        options=options or {},
    )


def make_incident(
    act_num: str,
    *,
    lat: float = HOME_LAT,
    lon: float = HOME_LON,
    fase: Fase = Fase.ACTIU,
    tipus: Tipus = Tipus.FORESTAL,
    tipus_desc: str = "Incendi vegetació forestal",
    municipi: str | None = "Testville",
    vehicles: int = 2,
    inici: datetime | None = None,
    data_act: datetime | None = None,
) -> Incident:
    """Build an `Incident` directly (bypassing GeoJSON parsing) for tests
    that exercise coordinator logic rather than `arcgis.py`/`models.py`.
    """
    now = datetime.now(UTC)
    return Incident(
        act_num=act_num,
        lat=lat,
        lon=lon,
        fase=fase,
        tipus=tipus,
        tipus_desc=tipus_desc,
        municipi=municipi,
        inici=inici if inici is not None else now,
        fi=None,
        vehicles=vehicles,
        situacio="A",
        edit_date=now,
        creation_date=now,
        data_act=data_act if data_act is not None else now,
    )


class FakeClock:
    """A controllable stand-in for `homeassistant.util.dt.utcnow`.

    Grace-period cleanup (`_cleanup_resolved`) and `duration_min` in
    `incendiscat_fire_resolved` payloads depend on wall-clock time. Rather
    than sleeping for real minutes (or fighting `freezegun` across many
    `async_refresh()` calls), tests advance this clock explicitly between
    cycles.
    """

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    """Patch `coordinator.utcnow` with a `FakeClock`, starting at a fixed time."""
    fake = FakeClock(datetime(2026, 7, 2, 12, 0, tzinfo=UTC))
    monkeypatch.setattr("custom_components.incendiscat.coordinator.utcnow", fake)
    return fake
