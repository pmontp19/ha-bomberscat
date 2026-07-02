"""Tests for the 6 aggregated sensors (Task 6 + Task 12).

Sets up a real config entry (`hass.config_entries.async_setup`) with
`fetch_incidents` patched at the coordinator's import site (same pattern as
`tests/test_lifecycle.py`/`tests/test_coordinator.py`), then asserts on
`hass.states` for each `sensor.*` entity. Entity ids are resolved via the
entity registry (by unique_id) rather than guessed, since no
`translations/*.json` entries exist yet for entity `translation_key`s
(Task 14) and HA's fallback name-from-key slugging isn't this task's
concern to pin down.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from custom_components.incendiscat.const import CONF_ACTIVE_PHASES, DOMAIN
from custom_components.incendiscat.models import Fase, Tipus
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import HOME_LAT, HOME_LON, make_config_entry, make_incident

SENSOR_KEYS = [
    "active_fires",
    "nearest_fire_distance",
    "nearest_fire_municipi",
    "fires_per_fase",
    "fires_per_tipus",
    "total_vehicles",
]


def _patched_fetch(*side_effects):
    return patch(
        "custom_components.incendiscat.coordinator.fetch_incidents",
        AsyncMock(side_effect=list(side_effects)),
    )


async def _setup(hass: HomeAssistant, entry=None):
    entry = entry or make_config_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    return entry


def _entity_id(hass: HomeAssistant, entry, key: str) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert entity_id is not None, f"no entity registered for key {key!r}"
    return entity_id


def _state(hass: HomeAssistant, entry, key: str):
    return hass.states.get(_entity_id(hass, entry, key))


# ---------------------------------------------------------------------------
# All 6 sensors exist
# ---------------------------------------------------------------------------


async def test_all_six_sensors_present(hass: HomeAssistant) -> None:
    with _patched_fetch([]):
        entry = await _setup(hass)

    for key in SENSOR_KEYS:
        state = _state(hass, entry, key)
        assert state is not None, f"missing sensor for {key!r}"


async def test_sensors_share_one_device(hass: HomeAssistant) -> None:
    with _patched_fetch([]):
        entry = await _setup(hass)

    registry = er.async_get(hass)
    device_ids = {
        registry.async_get(_entity_id(hass, entry, key)).device_id
        for key in SENSOR_KEYS
    }
    assert len(device_ids) == 1
    assert None not in device_ids


# ---------------------------------------------------------------------------
# Empty data
# ---------------------------------------------------------------------------


async def test_empty_data_defaults(hass: HomeAssistant) -> None:
    with _patched_fetch([]):
        entry = await _setup(hass)

    assert _state(hass, entry, "active_fires").state == "0"
    assert _state(hass, entry, "nearest_fire_distance").state == "-1"
    assert _state(hass, entry, "nearest_fire_municipi").state == "—"
    assert _state(hass, entry, "fires_per_fase").state == "0"
    assert _state(hass, entry, "fires_per_tipus").state == "0"
    assert _state(hass, entry, "total_vehicles").state == "0"

    fase_attrs = _state(hass, entry, "fires_per_fase").attributes
    assert fase_attrs["actiu"] == 0
    assert fase_attrs["estabilitzat"] == 0
    assert fase_attrs["controlat"] == 0
    assert fase_attrs["extingit"] == 0

    tipus_attrs = _state(hass, entry, "fires_per_tipus").attributes
    assert tipus_attrs["vf"] == 0
    assert tipus_attrs["va"] == 0
    assert tipus_attrs["vu"] == 0


# ---------------------------------------------------------------------------
# active_fires + nearest_* with data
# ---------------------------------------------------------------------------


async def test_active_fires_counts_and_attributes(hass: HomeAssistant) -> None:
    # Within alert radius (~11 km).
    near = make_incident(
        "1", lat=HOME_LAT + 0.1, lon=HOME_LON, fase=Fase.ACTIU, vehicles=3
    )
    # Within track radius (100 km) but outside alert radius (30 km): ~55 km.
    far = make_incident(
        "2", lat=HOME_LAT + 0.5, lon=HOME_LON, fase=Fase.ESTABILITZAT, vehicles=2
    )
    entry = make_config_entry(track_radius=100.0, alert_radius=30.0)
    with _patched_fetch([near, far]):
        entry = await _setup(hass, entry)

    active = _state(hass, entry, "active_fires")
    assert active.state == "2"
    assert active.attributes["total_in_track_radius"] == 2
    assert active.attributes["total_in_alert_radius"] == 1
    assert active.attributes["last_updated"] is not None

    # Nearest is "1" (~11 km, closer than "2"'s ~55 km).
    distance = float(_state(hass, entry, "nearest_fire_distance").state)
    assert 0 < distance < 20
    assert _state(hass, entry, "nearest_fire_municipi").state == "Testville"


async def test_nearest_fire_distance_is_smallest(hass: HomeAssistant) -> None:
    close = make_incident("1", lat=HOME_LAT + 0.05, lon=HOME_LON, fase=Fase.ACTIU)
    far = make_incident("2", lat=HOME_LAT + 0.5, lon=HOME_LON, fase=Fase.ACTIU)
    with _patched_fetch([close, far]):
        entry = await _setup(hass)

    distance = float(_state(hass, entry, "nearest_fire_distance").state)
    assert 0 <= distance < 20  # close is ~5.5 km, far is ~55 km


async def test_active_fires_excludes_grace_period_extingit(
    hass: HomeAssistant,
) -> None:
    """A fire that just turned Extingit is still in `state.incidents` during
    its grace period, but must not count as "active" (module docstring)."""
    active = make_incident("1", fase=Fase.ACTIU)
    extinguished = make_incident("1", fase=Fase.EXTINGIT)
    with _patched_fetch([], [active], [extinguished]):
        entry = await _setup(hass)
        await entry.runtime_data.async_refresh()
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    # Still tracked (grace period) but no longer "active".
    assert "1" in entry.runtime_data.data.incidents
    assert _state(hass, entry, "active_fires").state == "0"
    assert _state(hass, entry, "nearest_fire_distance").state == "-1"
    assert _state(hass, entry, "nearest_fire_municipi").state == "—"
    # fires_per_fase / total_vehicles still count it (tracked, not active).
    assert _state(hass, entry, "fires_per_fase").state == "1"
    assert _state(hass, entry, "fires_per_fase").attributes["extingit"] == 1
    assert _state(hass, entry, "total_vehicles").state != "0"


# ---------------------------------------------------------------------------
# fires_per_fase / fires_per_tipus attribute correctness
# ---------------------------------------------------------------------------


async def test_fires_per_fase_attributes(hass: HomeAssistant) -> None:
    entry = make_config_entry(
        options={CONF_ACTIVE_PHASES: ["Actiu", "Estabilitzat", "Controlat"]}
    )
    incidents = [
        make_incident("1", fase=Fase.ACTIU),
        make_incident("2", fase=Fase.ACTIU),
        make_incident("3", fase=Fase.ESTABILITZAT),
        make_incident("4", fase=Fase.CONTROLAT),
    ]
    with _patched_fetch(incidents):
        entry = await _setup(hass, entry)

    state = _state(hass, entry, "fires_per_fase")
    assert state.state == "4"
    assert state.attributes["actiu"] == 2
    assert state.attributes["estabilitzat"] == 1
    assert state.attributes["controlat"] == 1
    assert state.attributes["extingit"] == 0
    # Most severe present is Actiu -> mdi:fire.
    assert state.attributes["icon"] == "mdi:fire"


async def test_fires_per_tipus_attributes(hass: HomeAssistant) -> None:
    entry = make_config_entry(options={"subtipus": ["VF", "VA", "VU"]})
    incidents = [
        make_incident("1", tipus=Tipus.FORESTAL),
        make_incident("2", tipus=Tipus.FORESTAL),
        make_incident("3", tipus=Tipus.AGRICOLA),
        make_incident("4", tipus=Tipus.URBANA),
    ]
    with _patched_fetch(incidents):
        entry = await _setup(hass, entry)

    state = _state(hass, entry, "fires_per_tipus")
    assert state.state == "4"
    assert state.attributes["vf"] == 2
    assert state.attributes["va"] == 1
    assert state.attributes["vu"] == 1


async def test_total_vehicles_sums_tracked_incidents(hass: HomeAssistant) -> None:
    incidents = [
        make_incident("1", vehicles=3),
        make_incident("2", vehicles=5),
    ]
    with _patched_fetch(incidents):
        entry = await _setup(hass)

    assert _state(hass, entry, "total_vehicles").state == "8"


# ---------------------------------------------------------------------------
# Update propagation after a second refresh
# ---------------------------------------------------------------------------


async def test_state_updates_after_second_refresh(hass: HomeAssistant) -> None:
    """Second fetch keeps "1" (unchanged) and adds "2": under full-fetch
    reconciliation (coordinator.py's module docstring -- every cycle fetches
    the whole current view and prunes act_nums absent from it) an act_num
    missing from a later batch is treated as vanished/resolved rather than
    "unchanged since last cursor", so "1" must still be present here for
    this test to actually exercise "a second incident got added", not a
    prune."""
    first = make_incident("1", fase=Fase.ACTIU, vehicles=1)
    second_new = make_incident("2", fase=Fase.ACTIU, vehicles=4)
    with _patched_fetch([first], [first, second_new]):
        entry = await _setup(hass)
        assert _state(hass, entry, "active_fires").state == "1"
        assert _state(hass, entry, "total_vehicles").state == "1"

        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

    assert _state(hass, entry, "active_fires").state == "2"
    assert _state(hass, entry, "total_vehicles").state == "5"
