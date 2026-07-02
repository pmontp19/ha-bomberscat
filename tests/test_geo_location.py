"""Tests for geo_location.py: one `geo_location` entity per tracked wildfire
(Task 7, docs/03-feature-spec.md §3.1).

Sets up a real config entry (patching `fetch_incidents` so no network access
happens) and reads the resulting `geo_location.*` entities from
`hass.states`, exercising the full `async_setup_entry` -> coordinator ->
entity-platform pipeline, matching the pattern used by
`tests/test_binary_sensor.py` / `tests/test_sensor.py`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from custom_components.incendiscat.const import BOMBERS_VIEWER_URL
from custom_components.incendiscat.models import Fase, Tipus
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import HOME_LAT, HOME_LON, FakeClock, make_config_entry, make_incident


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


def _geo_location_states(hass: HomeAssistant):
    """All `geo_location.*` states whose `source` is `incendiscat`."""
    return [
        state
        for state in hass.states.async_all("geo_location")
        if state.attributes.get("source") == "incendiscat"
    ]


def _geo_location_state_for(hass: HomeAssistant, act_num: str):
    matches = [
        state
        for state in _geo_location_states(hass)
        if state.attributes.get("act_num") == act_num
    ]
    assert len(matches) == 1, f"expected exactly one entity for {act_num}: {matches}"
    return matches[0]


# ---------------------------------------------------------------------------
# One entity per fixture incident, with the full §3.1 attribute set
# ---------------------------------------------------------------------------


async def test_one_entity_per_incident_at_setup(hass: HomeAssistant) -> None:
    inc1 = make_incident("1", lat=HOME_LAT + 0.05, lon=HOME_LON, municipi="Nearville")
    inc2 = make_incident("2", lat=HOME_LAT + 0.2, lon=HOME_LON, municipi="Farville")
    await _setup(hass, [inc1, inc2])

    states = _geo_location_states(hass)
    assert len(states) == 2
    assert {s.attributes["act_num"] for s in states} == {"1", "2"}


async def test_state_is_distance_km(hass: HomeAssistant) -> None:
    inc = make_incident("1", lat=HOME_LAT + 0.05, lon=HOME_LON)
    entry = await _setup(hass, [inc])
    coordinator = entry.runtime_data

    state = _geo_location_state_for(hass, "1")
    expected = round(coordinator.distance_km(inc), 1)
    assert float(state.state) == expected
    assert 0 < float(state.state) < 30


async def test_full_attribute_set_matches_feature_spec(hass: HomeAssistant) -> None:
    inc = make_incident(
        "262311630",
        lat=HOME_LAT + 0.05,
        lon=HOME_LON,
        fase=Fase.ACTIU,
        tipus=Tipus.FORESTAL,
        tipus_desc="Incendi vegetació forestal",
        municipi="Sant Quirze Safaja",
        vehicles=4,
    )
    await _setup(hass, [inc])

    state = _geo_location_state_for(hass, "262311630")
    attrs = state.attributes

    # docs/03-feature-spec.md §3.1's full attribute table.
    assert attrs["source"] == "incendiscat"
    assert attrs["latitude"] == round(inc.lat, 5)
    assert attrs["longitude"] == round(inc.lon, 5)
    assert attrs["act_num"] == "262311630"
    assert attrs["fase"] == "Actiu"
    assert attrs["tipus"] == "VF"
    assert attrs["tipus_desc"] == "Incendi vegetació forestal"
    assert attrs["municipi"] == "Sant Quirze Safaja"
    assert attrs["data_inici"] == inc.inici.isoformat()
    assert attrs["data_fi"] is None
    assert attrs["vehicles"] == 4
    assert attrs["situacio"] == "A"
    assert attrs["updated_at"] == inc.edit_date.isoformat()
    assert attrs["url"] == BOMBERS_VIEWER_URL

    # geo_location entity name convention (docs/04-architecture.md §7).
    assert state.name == "Foc Sant Quirze Safaja"


async def test_name_falls_back_to_act_num_without_municipi(
    hass: HomeAssistant,
) -> None:
    inc = make_incident("1", lat=HOME_LAT + 0.05, lon=HOME_LON, municipi=None)
    await _setup(hass, [inc])

    state = _geo_location_state_for(hass, "1")
    assert state.name == "Foc 1"


# ---------------------------------------------------------------------------
# Dynamic lifecycle: new incident appears, dropped incident disappears
# ---------------------------------------------------------------------------


async def test_new_incident_on_later_refresh_creates_entity(
    hass: HomeAssistant,
) -> None:
    entry = await _setup(hass, [])
    assert _geo_location_states(hass) == []

    coordinator = entry.runtime_data
    new_incident = make_incident("1", lat=HOME_LAT + 0.05, lon=HOME_LON)
    with _patched_fetch([new_incident]):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    states = _geo_location_states(hass)
    assert len(states) == 1
    assert states[0].attributes["act_num"] == "1"


async def test_incident_dropped_removes_entity_from_states(
    hass: HomeAssistant,
) -> None:
    """Fase moving out of `active_phases` (and not `Extingit`) drops the
    incident from `coordinator.data.incidents` on the very next cycle (no
    grace period — see `tests/test_binary_sensor.py`'s equivalent test)."""
    inc = make_incident("1", lat=HOME_LAT + 0.05, lon=HOME_LON, fase=Fase.ACTIU)
    entry = await _setup(hass, [inc])
    assert len(_geo_location_states(hass)) == 1

    coordinator = entry.runtime_data
    no_longer_active = make_incident(
        "1", lat=HOME_LAT + 0.05, lon=HOME_LON, fase=Fase.CONTROLAT
    )
    with _patched_fetch([no_longer_active]):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert "1" not in coordinator.data.incidents
    assert _geo_location_states(hass) == []
    assert hass.states.get("geo_location.foc_testville") is None


async def test_incident_dropped_leaves_no_registry_orphan(
    hass: HomeAssistant,
) -> None:
    """Task 7 acceptance criterion: removing a tracked incident's entity
    must not leave a disabled row behind in the entity registry."""
    inc = make_incident("1", lat=HOME_LAT + 0.05, lon=HOME_LON, fase=Fase.ACTIU)
    entry = await _setup(hass, [inc])

    registry = er.async_get(hass)
    entity_id = _geo_location_state_for(hass, "1").entity_id
    assert registry.async_get(entity_id) is not None

    coordinator = entry.runtime_data
    no_longer_active = make_incident(
        "1", lat=HOME_LAT + 0.05, lon=HOME_LON, fase=Fase.CONTROLAT
    )
    with _patched_fetch([no_longer_active]):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert registry.async_get(entity_id) is None
    assert registry.entities.get(entity_id) is None


async def test_extingit_removed_only_after_grace_period(
    hass: HomeAssistant, clock: FakeClock
) -> None:
    """The `geo_location` entity survives the `Extingit` transition until
    the coordinator's removal grace period elapses (docs/03-feature-spec.md
    §3.1 "Cicle de vida"), then disappears with no registry orphan."""
    active = make_incident("1", lat=HOME_LAT + 0.05, lon=HOME_LON, fase=Fase.ACTIU)
    extinguished = make_incident(
        "1", lat=HOME_LAT + 0.05, lon=HOME_LON, fase=Fase.EXTINGIT
    )
    entry = await _setup(hass, [active])
    coordinator = entry.runtime_data
    registry = er.async_get(hass)

    with _patched_fetch([extinguished]):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    # Still tracked (grace period): entity survives with the new fase.
    assert "1" in coordinator.data.incidents
    state = _geo_location_state_for(hass, "1")
    assert state.attributes["fase"] == "Extingit"
    entity_id = state.entity_id

    clock.advance(minutes=61)
    with _patched_fetch([extinguished]):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert "1" not in coordinator.data.incidents
    assert _geo_location_states(hass) == []
    assert registry.async_get(entity_id) is None


# ---------------------------------------------------------------------------
# In-place attribute updates (no re-creation) on incident modification
# ---------------------------------------------------------------------------


async def test_attributes_update_in_place_on_fase_change(
    hass: HomeAssistant,
) -> None:
    inc = make_incident(
        "1", lat=HOME_LAT + 0.05, lon=HOME_LON, fase=Fase.ACTIU, vehicles=2
    )
    entry = await _setup(hass, [inc])
    original_entity_id = _geo_location_state_for(hass, "1").entity_id

    coordinator = entry.runtime_data
    updated = make_incident(
        "1", lat=HOME_LAT + 0.05, lon=HOME_LON, fase=Fase.ESTABILITZAT, vehicles=6
    )
    with _patched_fetch([updated]):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    state = _geo_location_state_for(hass, "1")
    # Same entity (not removed/recreated), fresh attributes.
    assert state.entity_id == original_entity_id
    assert state.attributes["fase"] == "Estabilitzat"
    assert state.attributes["vehicles"] == 6
