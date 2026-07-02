"""Tests for the Pla Alfa-backed entities (Task 10): `sensor.fire_risk` and
`binary_sensor.high_risk`.

Sets up a real config entry (`hass.config_entries.async_setup`), patching
`fetch_incidents` (Bombers) and `fetch_risk` (Pla Alfa) independently at
their respective coordinators' import sites -- same pattern as
`tests/test_sensor.py`/`tests/test_binary_sensor.py`. Entity ids are
resolved via the entity registry by unique_id (not guessed from
`translation_key`/`device_class` fallback naming), since `high_risk` shares
`fire_nearby`'s `SAFETY` device class and both live on the same device,
which would otherwise make their HA-generated friendly names collide.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from custom_components.incendiscat.arcgis import ArcgisClientError
from custom_components.incendiscat.arcgis import ArcgisClientError as BombersError
from custom_components.incendiscat.const import CONF_HIGH_RISK_THRESHOLD, DOMAIN
from custom_components.incendiscat.pla_alfa import PlaAlfaRisk
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .conftest import make_config_entry, make_incident

RISK_ALT = PlaAlfaRisk(
    peril_m=3,
    nivell_text="Alt",
    municipi="Testville",
    comarca="Testcomarca",
    perill_dema=4,
    data_vigencia="2026-07-02",
    hora_vigencia="9:30",
)
RISK_BAIX = PlaAlfaRisk(
    peril_m=1,
    nivell_text="Baix",
    municipi="Testville",
    comarca="Testcomarca",
    perill_dema=1,
    data_vigencia="2026-07-02",
    hora_vigencia="0:00",
)


def _patched_incidents(*side_effects):
    return patch(
        "custom_components.incendiscat.coordinator.fetch_incidents",
        AsyncMock(side_effect=list(side_effects) or [[]]),
    )


def _patched_risk(*side_effects):
    return patch(
        "custom_components.incendiscat.pla_alfa.fetch_risk",
        AsyncMock(side_effect=list(side_effects)),
    )


async def _setup(hass: HomeAssistant, entry=None):
    entry = entry or make_config_entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is True
    await hass.async_block_till_done()
    return entry


def _entity_id(hass: HomeAssistant, entry, platform: str, key: str) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert entity_id is not None, f"no {platform} entity registered for key {key!r}"
    return entity_id


def _state(hass: HomeAssistant, entry, platform: str, key: str):
    return hass.states.get(_entity_id(hass, entry, platform, key))


# ---------------------------------------------------------------------------
# fire_risk sensor: state + attributes
# ---------------------------------------------------------------------------


async def test_fire_risk_state_and_attributes(hass: HomeAssistant) -> None:
    with _patched_incidents([]), _patched_risk(RISK_ALT):
        entry = await _setup(hass)

    state = _state(hass, entry, "sensor", "fire_risk")
    assert state is not None
    assert state.state == "3"
    assert state.attributes["nivell_text"] == "Alt"
    assert state.attributes["comarca"] == "Testcomarca"
    assert state.attributes["municipi"] == "Testville"
    assert state.attributes["data_vigencia"] == "2026-07-02"
    assert state.attributes["hora_vigencia"] == "9:30"
    assert state.attributes["perill_dema"] == 4


async def test_fire_risk_reflects_low_level(hass: HomeAssistant) -> None:
    with _patched_incidents([]), _patched_risk(RISK_BAIX):
        entry = await _setup(hass)

    state = _state(hass, entry, "sensor", "fire_risk")
    assert state.state == "1"
    assert state.attributes["nivell_text"] == "Baix"


# ---------------------------------------------------------------------------
# high_risk binary sensor: on/off around threshold
# ---------------------------------------------------------------------------


async def test_high_risk_off_below_default_threshold(hass: HomeAssistant) -> None:
    with _patched_incidents([]), _patched_risk(RISK_BAIX):
        entry = await _setup(hass)

    state = _state(hass, entry, "binary_sensor", "high_risk")
    assert state is not None
    assert state.state == "off"


async def test_high_risk_on_at_default_threshold(hass: HomeAssistant) -> None:
    with _patched_incidents([]), _patched_risk(RISK_ALT):
        entry = await _setup(hass)

    state = _state(hass, entry, "binary_sensor", "high_risk")
    assert state.state == "on"
    assert state.attributes["threshold"] == 3


async def test_high_risk_respects_custom_threshold_option(hass: HomeAssistant) -> None:
    # peril_m=3 ("Alt") is below a custom threshold of 4 ("Extrem").
    entry = make_config_entry(options={CONF_HIGH_RISK_THRESHOLD: 4})
    with _patched_incidents([]), _patched_risk(RISK_ALT):
        entry = await _setup(hass, entry=entry)

    state = _state(hass, entry, "binary_sensor", "high_risk")
    assert state.state == "off"
    assert state.attributes["threshold"] == 4


# ---------------------------------------------------------------------------
# Independence: Pla Alfa down does not affect Bombers, and vice versa
# ---------------------------------------------------------------------------


async def test_pla_alfa_failure_leaves_risk_entities_unavailable(
    hass: HomeAssistant,
) -> None:
    with (
        _patched_incidents([]),
        _patched_risk(ArcgisClientError("Pla Alfa unreachable")),
    ):
        entry = await _setup(hass)

    assert entry.state is ConfigEntryState.LOADED

    risk_state = _state(hass, entry, "sensor", "fire_risk")
    assert risk_state.state == "unavailable"

    high_risk_state = _state(hass, entry, "binary_sensor", "high_risk")
    assert high_risk_state.state == "unavailable"


async def test_pla_alfa_failure_does_not_affect_fires_sensors(
    hass: HomeAssistant,
) -> None:
    incident = make_incident("1")
    with (
        _patched_incidents([incident]),
        _patched_risk(ArcgisClientError("Pla Alfa unreachable")),
    ):
        entry = await _setup(hass)

    assert entry.state is ConfigEntryState.LOADED

    active_fires = _state(hass, entry, "sensor", "active_fires")
    assert active_fires is not None
    assert active_fires.state == "1"

    risk_state = _state(hass, entry, "sensor", "fire_risk")
    assert risk_state.state == "unavailable"


async def test_bombers_ok_pla_alfa_down_entry_still_loads(hass: HomeAssistant) -> None:
    """Bombers' first refresh succeeds; Pla Alfa's fails outright -- the
    whole config entry must still set up (fire monitoring is core value,
    Pla Alfa is best-effort)."""
    with (
        _patched_incidents([]),
        _patched_risk(ArcgisClientError("Pla Alfa unreachable")),
    ):
        entry = await _setup(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.data.incidents == {}
    assert entry.runtime_data.pla_alfa.data is None
    assert entry.runtime_data.pla_alfa.last_update_success is False


async def test_bombers_down_pla_alfa_ok_entry_fails_setup_retry(
    hass: HomeAssistant,
) -> None:
    """The reverse: Bombers is the core value, so *its* failure must still
    abort setup (Task 5 behavior), independent of Pla Alfa's own health."""
    with (
        _patched_incidents(BombersError("unreachable")),
        _patched_risk(RISK_ALT),
    ):
        entry = make_config_entry()
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id) is False
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY
