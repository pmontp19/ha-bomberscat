"""Tests for event emission (Task 9): fire_detected/phase_change/resolved.

Uses `pytest_homeassistant_custom_component.common.async_capture_events` to
capture events fired on `hass.bus`, and the `clock` fixture (conftest.py) to
control `duration_min` deterministically.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.incendiscat.const import (
    BOMBERS_VIEWER_URL,
    CONF_MIN_VEHICLES,
    EVENT_FIRE_DETECTED,
    EVENT_FIRE_RESOLVED,
    EVENT_PHASE_CHANGE,
)
from custom_components.incendiscat.coordinator import IncendiscatDataUpdateCoordinator
from custom_components.incendiscat.models import Fase
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_capture_events

from .conftest import HOME_LAT, HOME_LON, FakeClock, make_config_entry, make_incident


def _coordinator(hass: HomeAssistant, entry=None) -> IncendiscatDataUpdateCoordinator:
    entry = entry or make_config_entry()
    return IncendiscatDataUpdateCoordinator(hass, entry, MagicMock(name="session"))


def _patched_fetch(*side_effects):
    return patch(
        "custom_components.incendiscat.coordinator.fetch_incidents",
        AsyncMock(side_effect=list(side_effects)),
    )


# ---------------------------------------------------------------------------
# No events on the very first refresh
# ---------------------------------------------------------------------------


async def test_no_events_fired_on_first_refresh(hass: HomeAssistant) -> None:
    detected = async_capture_events(hass, EVENT_FIRE_DETECTED)
    inc = make_incident("1", fase=Fase.ACTIU)
    coordinator = _coordinator(hass)

    with _patched_fetch([inc]):
        await coordinator.async_refresh()

    # The incident is tracked (baseline), but nothing was "detected".
    assert "1" in coordinator.data.incidents
    assert detected == []


# ---------------------------------------------------------------------------
# fire_detected
# ---------------------------------------------------------------------------


async def test_fire_detected_payload_and_alert_radius_true(
    hass: HomeAssistant,
) -> None:
    detected = async_capture_events(hass, EVENT_FIRE_DETECTED)
    entry = make_config_entry(track_radius=100.0, alert_radius=30.0)
    coordinator = _coordinator(hass, entry)
    # Within alert radius (~11 km): 0.1 deg lat.
    inc = make_incident(
        "1",
        lat=HOME_LAT + 0.1,
        lon=HOME_LON,
        fase=Fase.ACTIU,
        municipi="Testville",
        vehicles=3,
    )

    with _patched_fetch([], [inc]):
        await coordinator.async_refresh()  # baseline, no events
        await coordinator.async_refresh()  # inc appears -> detected

    assert len(detected) == 1
    payload = detected[0].data
    assert payload["act_num"] == "1"
    assert payload["municipi"] == "Testville"
    assert payload["fase"] == "Actiu"
    assert payload["tipus"] == "VF"
    assert payload["vehicles"] == 3
    assert payload["in_alert_radius"] is True
    assert payload["latitude"] == inc.lat
    assert payload["longitude"] == inc.lon
    assert payload["url"] == BOMBERS_VIEWER_URL
    assert isinstance(payload["distance_km"], float)


async def test_fire_detected_in_alert_radius_false_when_outside_alert(
    hass: HomeAssistant,
) -> None:
    detected = async_capture_events(hass, EVENT_FIRE_DETECTED)
    entry = make_config_entry(track_radius=100.0, alert_radius=10.0)
    coordinator = _coordinator(hass, entry)
    # ~55 km away: inside track radius (100 km) but outside alert (10 km).
    inc = make_incident("1", lat=HOME_LAT + 0.5, lon=HOME_LON)

    with _patched_fetch([], [inc]):
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    assert len(detected) == 1
    assert detected[0].data["in_alert_radius"] is False


# ---------------------------------------------------------------------------
# phase_change
# ---------------------------------------------------------------------------


async def test_phase_change_event(hass: HomeAssistant) -> None:
    phase_changes = async_capture_events(hass, EVENT_PHASE_CHANGE)
    coordinator = _coordinator(hass)
    active = make_incident("1", fase=Fase.ACTIU)
    stabilized = make_incident("1", fase=Fase.ESTABILITZAT)

    with _patched_fetch([], [active], [stabilized]):
        await coordinator.async_refresh()  # baseline
        await coordinator.async_refresh()  # detected (not asserted here)
        await coordinator.async_refresh()  # phase change

    assert len(phase_changes) == 1
    payload = phase_changes[0].data
    assert payload["act_num"] == "1"
    assert payload["old_fase"] == "Actiu"
    assert payload["new_fase"] == "Estabilitzat"
    assert "distance_km" in payload


# ---------------------------------------------------------------------------
# fire_resolved
# ---------------------------------------------------------------------------


async def test_fire_resolved_on_extingit_also_fires_phase_change(
    hass: HomeAssistant, clock: FakeClock
) -> None:
    resolved = async_capture_events(hass, EVENT_FIRE_RESOLVED)
    phase_changes = async_capture_events(hass, EVENT_PHASE_CHANGE)
    coordinator = _coordinator(hass)
    active = make_incident("1", fase=Fase.ACTIU, inici=clock.now, municipi="Testville")
    extinguished = make_incident(
        "1", fase=Fase.EXTINGIT, inici=active.inici, municipi="Testville"
    )

    with _patched_fetch([], [active], [extinguished]):
        await coordinator.async_refresh()  # baseline
        await coordinator.async_refresh()  # detected
        clock.advance(minutes=187)
        await coordinator.async_refresh()  # extinguished

    assert len(resolved) == 1
    payload = resolved[0].data
    assert payload["act_num"] == "1"
    assert payload["municipi"] == "Testville"
    assert payload["final_fase"] == "Extingit"
    assert payload["duration_min"] == 187
    assert len(phase_changes) == 1  # Actiu -> Extingit is still a phase change

    # Extingit incidents stay tracked during the grace period.
    assert "1" in coordinator.data.incidents
    assert "1" in coordinator.data.resolved_at


async def test_fire_resolved_when_leaving_active_phase_without_extingit(
    hass: HomeAssistant,
) -> None:
    """Controlat isn't Extingit, but it's also not in default active_phases:
    the incident falls out of tracking and should still be reported
    resolved (docs/03-feature-spec.md §4.2's "... o surt del radi" is
    generalized here to "no longer tracked", see coordinator.py docstring).
    """
    resolved = async_capture_events(hass, EVENT_FIRE_RESOLVED)
    phase_changes = async_capture_events(hass, EVENT_PHASE_CHANGE)
    coordinator = _coordinator(hass)
    active = make_incident("1", fase=Fase.ACTIU)
    controlled = make_incident("1", fase=Fase.CONTROLAT)

    with _patched_fetch([], [active], [controlled]):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    assert len(resolved) == 1
    assert resolved[0].data["final_fase"] == "Controlat"
    assert phase_changes == []
    assert "1" not in coordinator.data.incidents


async def test_fire_resolved_when_dropping_below_min_vehicles(
    hass: HomeAssistant,
) -> None:
    """An already-tracked fire's location never changes for a given
    `act_num` (see `Incident`/`arcgis.py`), so within a single coordinator
    run "leaves the track radius" can't be reproduced by moving a fire —
    only a config/options reload (Task 11) changes the radius, which resets
    the coordinator entirely. What *can* happen mid-run is the incident
    itself falling out of the (static) filters, e.g. `min_vehicles`; the
    resulting code path (was tracked, now fails a filter, same phase) is
    exactly the generalized "no longer tracked -> resolved" branch that
    "leaves radius" would also hit (see coordinator.py's `_apply_incident`).
    """
    resolved = async_capture_events(hass, EVENT_FIRE_RESOLVED)
    entry = make_config_entry(options={CONF_MIN_VEHICLES: 2})
    coordinator = _coordinator(hass, entry)
    active = make_incident("1", fase=Fase.ACTIU, vehicles=3)
    stood_down = make_incident("1", fase=Fase.ACTIU, vehicles=1)

    with _patched_fetch([], [active], [stood_down]):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    assert len(resolved) == 1
    assert resolved[0].data["final_fase"] == "Actiu"
    assert "1" not in coordinator.data.incidents


# ---------------------------------------------------------------------------
# No duplicate events
# ---------------------------------------------------------------------------


async def test_no_duplicate_detected_event_when_incident_refetched_unchanged(
    hass: HomeAssistant,
) -> None:
    detected = async_capture_events(hass, EVENT_FIRE_DETECTED)
    phase_changes = async_capture_events(hass, EVENT_PHASE_CHANGE)
    coordinator = _coordinator(hass)
    inc = make_incident("1", fase=Fase.ACTIU)

    with _patched_fetch([], [inc], [inc]):
        await coordinator.async_refresh()  # baseline
        await coordinator.async_refresh()  # detected once
        await coordinator.async_refresh()  # re-fetched unchanged: no event

    assert len(detected) == 1
    assert phase_changes == []
