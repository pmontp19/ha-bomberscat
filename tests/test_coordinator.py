"""Tests for coordinator.py: fetch/filter/radius cycle logic (Task 5).

These tests patch `fetch_incidents` at the coordinator's import site and
drive `BomberscatDataUpdateCoordinator` directly with `async_refresh()`
(rather than `async_config_entry_first_refresh()`, which additionally
requires the config entry to be mid-setup — that flow is covered by the
setup-entry tests in `test_lifecycle.py`). Event emission (Task 9) is
covered separately in `test_events.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.bomberscat.arcgis import ArcgisClientError
from custom_components.bomberscat.const import (
    CONF_MIN_VEHICLES,
    CONF_SUBTIPUS,
    EVENT_FIRE_RESOLVED,
)
from custom_components.bomberscat.coordinator import (
    BomberscatDataUpdateCoordinator,
    BomberscatRuntimeConfig,
    _apply_incident,
    _cleanup_resolved,
    _passes_filters,
    _should_track,
)
from custom_components.bomberscat.models import Fase, Tipus
from homeassistant.core import HomeAssistant

from .conftest import HOME_LAT, HOME_LON, make_config_entry, make_incident

FAR_AWAY_LAT = HOME_LAT + 10.0  # ~1100 km north: outside any sane track radius

_DEFAULT_CFG = BomberscatRuntimeConfig(
    home_lat=HOME_LAT,
    home_lon=HOME_LON,
    track_radius_km=100,
    alert_radius_km=30,
    subtipus=frozenset({"VF"}),
    active_phases=frozenset({"Actiu"}),
    min_vehicles=0,
    scan_interval_min=5,
)


def _coordinator(hass: HomeAssistant, entry=None) -> BomberscatDataUpdateCoordinator:
    entry = entry or make_config_entry()
    return BomberscatDataUpdateCoordinator(hass, entry, MagicMock(name="session"))


def _patched_fetch(*side_effects):
    return patch(
        "custom_components.bomberscat.coordinator.fetch_incidents",
        AsyncMock(side_effect=list(side_effects)),
    )


# ---------------------------------------------------------------------------
# Full cycle: add / update / remove
# ---------------------------------------------------------------------------


async def test_first_cycle_adds_tracked_incident(hass: HomeAssistant) -> None:
    inc = make_incident("1")
    coordinator = _coordinator(hass)
    with _patched_fetch([inc]):
        await coordinator.async_refresh()

    assert coordinator.data.incidents == {"1": inc}
    assert coordinator.last_update_success is True


async def test_second_cycle_updates_existing_incident(hass: HomeAssistant) -> None:
    inc_v1 = make_incident("1", vehicles=1)
    inc_v2 = make_incident("1", vehicles=5)
    coordinator = _coordinator(hass)
    with _patched_fetch([inc_v1], [inc_v2]):
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    assert coordinator.data.incidents["1"].vehicles == 5


async def test_second_cycle_adds_second_incident_keeps_first(
    hass: HomeAssistant,
) -> None:
    inc1 = make_incident("1")
    inc2 = make_incident("2")
    coordinator = _coordinator(hass)
    with _patched_fetch([inc1], [inc2]):
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    assert set(coordinator.data.incidents) == {"1", "2"}


async def test_incident_removed_when_phase_leaves_active_set(
    hass: HomeAssistant,
) -> None:
    """Controlat is not in the default active_phases and is not Extingit."""
    tracked = make_incident("1", fase=Fase.ACTIU)
    dropped = make_incident("1", fase=Fase.CONTROLAT)
    coordinator = _coordinator(hass)
    with _patched_fetch([tracked], [dropped]):
        await coordinator.async_refresh()
        assert "1" in coordinator.data.incidents
        await coordinator.async_refresh()

    assert coordinator.data.incidents == {}


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


async def test_subtipus_filter_excludes_non_matching_type(
    hass: HomeAssistant,
) -> None:
    urban_fire = make_incident("1", tipus=Tipus.URBANA)
    coordinator = _coordinator(hass)
    with _patched_fetch([urban_fire]):
        await coordinator.async_refresh()

    assert coordinator.data.incidents == {}


async def test_subtipus_filter_can_be_widened_via_options(
    hass: HomeAssistant,
) -> None:
    urban_fire = make_incident("1", tipus=Tipus.URBANA)
    entry = make_config_entry(options={CONF_SUBTIPUS: ["VF", "VU"]})
    coordinator = _coordinator(hass, entry)
    with _patched_fetch([urban_fire]):
        await coordinator.async_refresh()

    assert "1" in coordinator.data.incidents


async def test_min_vehicles_filter(hass: HomeAssistant) -> None:
    small = make_incident("1", vehicles=1)
    entry = make_config_entry(options={CONF_MIN_VEHICLES: 2})
    coordinator = _coordinator(hass, entry)
    with _patched_fetch([small]):
        await coordinator.async_refresh()
    assert coordinator.data.incidents == {}

    reinforced = make_incident("1", vehicles=2)
    with _patched_fetch([reinforced]):
        await coordinator.async_refresh()
    assert "1" in coordinator.data.incidents


async def test_fresh_extingit_incident_is_never_tracked(hass: HomeAssistant) -> None:
    """An incident already Extingit the first time we see it is ignored:

    there is nothing to alert on and no transition to observe (see
    coordinator.py's `_passes_filters` docstring).
    """
    already_out = make_incident("1", fase=Fase.EXTINGIT)
    coordinator = _coordinator(hass)
    with _patched_fetch([already_out]):
        await coordinator.async_refresh()

    assert coordinator.data.incidents == {}
    assert coordinator.data.resolved_at == {}


# ---------------------------------------------------------------------------
# Radius filtering
# ---------------------------------------------------------------------------


async def test_incident_outside_track_radius_is_not_tracked(
    hass: HomeAssistant,
) -> None:
    far = make_incident("1", lat=FAR_AWAY_LAT, lon=HOME_LON)
    coordinator = _coordinator(hass, make_config_entry(track_radius=100.0))
    with _patched_fetch([far]):
        await coordinator.async_refresh()

    assert coordinator.data.incidents == {}


async def test_incident_inside_track_radius_is_tracked(hass: HomeAssistant) -> None:
    near = make_incident("1", lat=HOME_LAT + 0.1, lon=HOME_LON)
    coordinator = _coordinator(hass, make_config_entry(track_radius=100.0))
    with _patched_fetch([near]):
        await coordinator.async_refresh()

    assert "1" in coordinator.data.incidents


# ---------------------------------------------------------------------------
# Fetch errors keep previous state
# ---------------------------------------------------------------------------


async def test_fetch_error_keeps_previous_incidents_and_sets_last_error(
    hass: HomeAssistant,
) -> None:
    inc = make_incident("1")
    coordinator = _coordinator(hass)
    with _patched_fetch([inc]):
        await coordinator.async_refresh()
    previous_data = coordinator.data

    with _patched_fetch(ArcgisClientError("boom")):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert coordinator.data is previous_data  # same object: never reassigned
    assert coordinator.data.incidents == {"1": inc}
    assert coordinator.data.last_error == "boom"


async def test_recovers_after_fetch_error(hass: HomeAssistant) -> None:
    inc = make_incident("1")
    coordinator = _coordinator(hass)
    with _patched_fetch([inc], ArcgisClientError("boom"), [inc]):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.last_error is None


# ---------------------------------------------------------------------------
# Incremental sync (`since`)
# ---------------------------------------------------------------------------


async def test_distance_km_reflects_updated_coordinates_across_cycles(
    hass: HomeAssistant,
) -> None:
    """distance_km must not be cached by act_num: the same incident can get
    corrected/updated coordinates on a later snapshot row, and events/sensors
    should reflect the new distance, not a stale one from the first sighting.
    """
    near = make_incident("1", lat=HOME_LAT, lon=HOME_LON)
    far = make_incident("1", lat=FAR_AWAY_LAT, lon=HOME_LON)
    coordinator = _coordinator(hass)

    assert coordinator.distance_km(near) == pytest.approx(0.0, abs=1e-6)
    assert coordinator.distance_km(far) > 1000


async def test_incremental_since_passed_correctly(hass: HomeAssistant) -> None:
    first = make_incident("1")
    coordinator = _coordinator(hass)

    mock_fetch = AsyncMock(side_effect=[[first], []])
    with patch("custom_components.bomberscat.coordinator.fetch_incidents", mock_fetch):
        await coordinator.async_refresh()
        assert mock_fetch.call_args_list[0].kwargs["since"] is None

        await coordinator.async_refresh()
        assert mock_fetch.call_args_list[1].kwargs["since"] == first.data_act


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def test_passes_filters_extingit_always_passes_phase_check() -> None:
    extingit = make_incident("1", fase=Fase.EXTINGIT)
    controlat = make_incident("1", fase=Fase.CONTROLAT)
    assert _passes_filters(extingit, _DEFAULT_CFG)
    assert not _passes_filters(controlat, _DEFAULT_CFG)


def test_should_track_false_when_outside_radius_even_if_was_tracked() -> None:
    inc = make_incident("1", fase=Fase.ACTIU)
    assert not _should_track(inc, _DEFAULT_CFG, distance_km=500, was_tracked=True)
    assert _should_track(inc, _DEFAULT_CFG, distance_km=1, was_tracked=True)


def test_apply_incident_resolved_when_leaving_tracking() -> None:
    inc = make_incident("1", fase=Fase.CONTROLAT)
    base_incidents = {"1": make_incident("1", fase=Fase.ACTIU)}
    incidents = dict(base_incidents)
    resolved_at: dict[str, datetime] = {}

    events = _apply_incident(
        inc,
        _DEFAULT_CFG,
        base_incidents=base_incidents,
        incidents=incidents,
        resolved_at=resolved_at,
        distance=0.0,
        now=datetime.now(UTC),
    )

    assert incidents == {}
    assert resolved_at == {}
    assert [e[0] for e in events] == [EVENT_FIRE_RESOLVED]
    assert events[0][1]["final_fase"] == "Controlat"


def test_cleanup_resolved_removes_only_expired() -> None:
    now = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    incidents = {
        "expired": make_incident("expired"),
        "fresh": make_incident("fresh"),
    }
    resolved_at = {
        "expired": now - timedelta(minutes=61),
        "fresh": now - timedelta(minutes=5),
    }
    _cleanup_resolved(incidents, resolved_at, 60, now)

    assert set(incidents) == {"fresh"}
    assert set(resolved_at) == {"fresh"}


@pytest.mark.parametrize("act_num", ["", None])
async def test_incident_without_act_num_is_skipped(
    hass: HomeAssistant, act_num: str | None
) -> None:
    inc = make_incident("1")
    object.__setattr__(inc, "act_num", act_num)
    coordinator = _coordinator(hass)
    with _patched_fetch([inc]):
        await coordinator.async_refresh()

    assert coordinator.data.incidents == {}
