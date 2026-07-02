"""Tests for binary_sensor.py: `binary_sensor.fire_nearby` (Task 8).

Sets up a real config entry (patching `fetch_incidents` so no network access
happens) and reads the resulting `binary_sensor.<...>_fire_nearby` entity
from `hass.states`, exercising the full `async_setup_entry` -> coordinator ->
entity-platform pipeline rather than instantiating the entity class
directly.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from custom_components.incendiscat.models import Fase
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .conftest import HOME_LAT, HOME_LON, make_config_entry, make_incident

# Entity naming: `_attr_translation_key = "fire_nearby"` resolves via
# `translations/en.json`'s "entity.binary_sensor.fire_nearby.name" (Task 14)
# to "Fire nearby", combined with the device name ("Incendis Catalunya")
# through `has_entity_name = True` into this object_id.
ENTITY_ID = "binary_sensor.incendis_catalunya_fire_nearby"

# ~11 km north of home: inside a 30 km alert radius, inside a 100 km track radius.
INSIDE_ALERT_LAT = HOME_LAT + 0.1
# ~55 km north of home: inside the (100 km) track radius, outside the
# (30 km) alert radius — proves the alert/track distinction.
INSIDE_TRACK_ONLY_LAT = HOME_LAT + 0.5


def _patched_fetch(*side_effects):
    return patch(
        "custom_components.incendiscat.coordinator.fetch_incidents",
        AsyncMock(side_effect=list(side_effects)),
    )


async def _setup(hass: HomeAssistant, *fetch_results, entry=None):
    entry = entry or make_config_entry()
    entry.add_to_hass(hass)
    with _patched_fetch(*fetch_results):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    return entry


# ---------------------------------------------------------------------------
# Basic on/off + alert vs. track radius
# ---------------------------------------------------------------------------


async def test_off_when_no_incidents(hass: HomeAssistant) -> None:
    await _setup(hass, [])

    state = hass.states.get(ENTITY_ID)
    assert state is not None
    assert state.state == "off"
    assert state.attributes.get("nearest_act_num") is None


async def test_on_with_nearest_attributes_when_incident_in_alert_radius(
    hass: HomeAssistant,
) -> None:
    inc = make_incident(
        "1",
        lat=INSIDE_ALERT_LAT,
        lon=HOME_LON,
        fase=Fase.ACTIU,
        municipi="Testville",
        vehicles=4,
    )
    await _setup(hass, [inc])

    state = hass.states.get(ENTITY_ID)
    assert state.state == "on"
    assert state.attributes["nearest_act_num"] == "1"
    assert state.attributes["nearest_municipi"] == "Testville"
    assert state.attributes["nearest_fase"] == "Actiu"
    assert isinstance(state.attributes["nearest_distance_km"], float)
    assert 0 < state.attributes["nearest_distance_km"] < 30


async def test_off_when_incident_inside_track_radius_but_outside_alert_radius(
    hass: HomeAssistant,
) -> None:
    """Proves the fire_nearby uses alert_radius, not track_radius."""
    inc = make_incident(
        "1",
        lat=INSIDE_TRACK_ONLY_LAT,
        lon=HOME_LON,
        fase=Fase.ACTIU,
    )
    entry = make_config_entry(track_radius=100.0, alert_radius=30.0)
    await _setup(hass, [inc], entry=entry)

    state = hass.states.get(ENTITY_ID)
    assert state.state == "off"
    assert state.attributes.get("nearest_act_num") is None


# ---------------------------------------------------------------------------
# Multiple incidents -> nearest wins
# ---------------------------------------------------------------------------


async def test_nearest_attributes_point_to_closest_incident(
    hass: HomeAssistant,
) -> None:
    far = make_incident(
        "far",
        lat=HOME_LAT + 0.2,
        lon=HOME_LON,
        fase=Fase.ACTIU,
        municipi="Farville",
    )
    near = make_incident(
        "near",
        lat=HOME_LAT + 0.05,
        lon=HOME_LON,
        fase=Fase.ACTIU,
        municipi="Nearville",
    )
    await _setup(hass, [far, near])

    state = hass.states.get(ENTITY_ID)
    assert state.state == "on"
    assert state.attributes["nearest_act_num"] == "near"
    assert state.attributes["nearest_municipi"] == "Nearville"


# ---------------------------------------------------------------------------
# Extingit-in-grace-period does NOT keep fire_nearby on (documented decision)
# ---------------------------------------------------------------------------


async def test_off_when_only_incident_is_extingit_in_grace_period(
    hass: HomeAssistant,
) -> None:
    """An Extingit incident within the alert radius, still sitting in its
    removal grace period (so it still has a geo_location entity / is still
    present in coordinator.data.incidents), must NOT keep fire_nearby on:
    only phases in active_phases count for alerting (see binary_sensor.py
    module docstring for the rationale)."""
    active = make_incident("1", lat=INSIDE_ALERT_LAT, lon=HOME_LON, fase=Fase.ACTIU)
    extinguished = make_incident(
        "1", lat=INSIDE_ALERT_LAT, lon=HOME_LON, fase=Fase.EXTINGIT
    )
    entry = make_config_entry()
    entry.add_to_hass(hass)
    with _patched_fetch([], [active], [extinguished]):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()
        coordinator = entry.runtime_data
        await coordinator.async_refresh()  # active -> on
        await hass.async_block_till_done()
        state = hass.states.get(ENTITY_ID)
        assert state.state == "on"

        await coordinator.async_refresh()  # extinguished, still in grace period
        await hass.async_block_till_done()

    # Still tracked (grace period) but no longer alerting.
    assert "1" in coordinator.data.incidents
    assert "1" in coordinator.data.resolved_at
    state = hass.states.get(ENTITY_ID)
    assert state.state == "off"
    assert state.attributes.get("nearest_act_num") is None


# ---------------------------------------------------------------------------
# Flips off after the incident disappears on a later refresh
# ---------------------------------------------------------------------------


async def test_flips_to_off_after_incident_disappears(hass: HomeAssistant) -> None:
    """Second refresh moves the incident's phase out of `active_phases`
    (same act_num/location, so the coordinator's per-act_num distance cache
    is irrelevant here) — it drops out of `coordinator.data.incidents`
    entirely, and `fire_nearby` must follow it to `off`."""
    inc = make_incident("1", lat=INSIDE_ALERT_LAT, lon=HOME_LON, fase=Fase.ACTIU)
    entry = make_config_entry()
    entry.add_to_hass(hass)
    with _patched_fetch([inc]):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()

    state = hass.states.get(ENTITY_ID)
    assert state.state == "on"

    coordinator = entry.runtime_data
    no_longer_active = make_incident(
        "1", lat=INSIDE_ALERT_LAT, lon=HOME_LON, fase=Fase.CONTROLAT
    )
    with _patched_fetch([no_longer_active]):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert "1" not in coordinator.data.incidents
    state = hass.states.get(ENTITY_ID)
    assert state.state == "off"
    assert state.attributes.get("nearest_act_num") is None
