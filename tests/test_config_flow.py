"""Tests for the bomberscat config flow.

Task 4 covered step 1 (location + radii). Task 11 adds step 2 (filters,
stored as entry *options*), the options flow (edit those + the high-risk
threshold), and `async_step_reconfigure` (move the location/radii without
recreating the entry).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from custom_components.bomberscat.const import (
    CONF_ACTIVE_PHASES,
    CONF_ALERT_RADIUS,
    CONF_HIGH_RISK_THRESHOLD,
    CONF_MIN_VEHICLES,
    CONF_SCAN_INTERVAL,
    CONF_SUBTIPUS,
    CONF_TRACK_RADIUS,
    DEFAULT_ACTIVE_PHASES,
    DEFAULT_ALERT_RADIUS_KM,
    DEFAULT_MIN_VEHICLES,
    DEFAULT_SCAN_INTERVAL_MIN,
    DEFAULT_SUBTIPUS,
    DEFAULT_TRACK_RADIUS_KM,
    DOMAIN,
)
from homeassistant import config_entries
from homeassistant.const import (
    CONF_LATITUDE,
    CONF_LOCATION,
    CONF_LONGITUDE,
    CONF_RADIUS,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import make_config_entry

FILTERS_DEFAULTS = {
    CONF_SUBTIPUS: DEFAULT_SUBTIPUS,
    CONF_ACTIVE_PHASES: DEFAULT_ACTIVE_PHASES,
    CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL_MIN,
    CONF_MIN_VEHICLES: DEFAULT_MIN_VEHICLES,
}


async def _complete_user_step(
    hass: HomeAssistant,
    *,
    latitude: float = 41.5,
    longitude: float = 2.1,
    radius_m: int | None = 50_000,
    alert_radius: float = 20,
) -> dict:
    """Init the flow and submit a valid step-1 (location) form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    location: dict = {CONF_LATITUDE: latitude, CONF_LONGITUDE: longitude}
    if radius_m is not None:
        location[CONF_RADIUS] = radius_m
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_LOCATION: location, CONF_ALERT_RADIUS: alert_radius},
    )


async def test_happy_path_creates_entry(hass: HomeAssistant) -> None:
    """Full 2-step flow: location -> filters -> entry with data + options."""
    result = await _complete_user_step(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "filters"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], FILTERS_DEFAULTS
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Bombers de Catalunya"
    assert result["data"] == {
        CONF_LATITUDE: 41.5,
        CONF_LONGITUDE: 2.1,
        CONF_TRACK_RADIUS: 50.0,
        CONF_ALERT_RADIUS: 20.0,
    }
    assert result["options"] == FILTERS_DEFAULTS


async def test_filters_step_prefilled_with_spec_defaults(hass: HomeAssistant) -> None:
    """The filters form itself suggests the §2 feature-spec defaults."""
    result = await _complete_user_step(hass)
    assert result["step_id"] == "filters"

    schema = result["data_schema"].schema
    # voluptuous keys carry the `description={"suggested_value": ...}` used
    # to pre-fill the frontend form.
    suggested = {
        str(key): key.description.get("suggested_value")
        for key in schema
        if key.description
    }
    assert suggested[CONF_SUBTIPUS] == DEFAULT_SUBTIPUS
    assert suggested[CONF_ACTIVE_PHASES] == DEFAULT_ACTIVE_PHASES
    assert suggested[CONF_SCAN_INTERVAL] == DEFAULT_SCAN_INTERVAL_MIN
    assert suggested[CONF_MIN_VEHICLES] == DEFAULT_MIN_VEHICLES


async def test_missing_radius_uses_default(hass: HomeAssistant) -> None:
    """If the location dict has no radius (core#108960), fall back to default."""
    result = await _complete_user_step(hass, radius_m=None, alert_radius=10)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], FILTERS_DEFAULTS
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TRACK_RADIUS] == float(DEFAULT_TRACK_RADIUS_KM)
    assert result["data"][CONF_ALERT_RADIUS] == 10.0


async def test_alert_radius_greater_than_track_radius_shows_error(
    hass: HomeAssistant,
) -> None:
    """alert_radius > track_radius must produce a recoverable form error."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LOCATION: {
                CONF_LATITUDE: 41.5,
                CONF_LONGITUDE: 2.1,
                CONF_RADIUS: 10_000,  # 10 km track radius
            },
            CONF_ALERT_RADIUS: 30,  # > track radius
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_ALERT_RADIUS: "alert_gt_track"}

    # The flow must still be recoverable: fix the input and resubmit.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LOCATION: {
                CONF_LATITUDE: 41.5,
                CONF_LONGITUDE: 2.1,
                CONF_RADIUS: 10_000,
            },
            CONF_ALERT_RADIUS: 5,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "filters"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], FILTERS_DEFAULTS
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TRACK_RADIUS] == 10.0
    assert result["data"][CONF_ALERT_RADIUS] == 5.0


async def test_second_config_attempt_aborts(hass: HomeAssistant) -> None:
    """Single-instance integration: a second attempt aborts immediately."""
    result = await _complete_user_step(hass, alert_radius=DEFAULT_ALERT_RADIUS_KM)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], FILTERS_DEFAULTS
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY

    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert second["type"] is FlowResultType.ABORT
    assert second["reason"] == "single_instance_allowed"


# ---------------------------------------------------------------------------
# Options flow (Task 11)
# ---------------------------------------------------------------------------


async def test_options_flow_shows_current_values(hass: HomeAssistant) -> None:
    """Opening the options flow pre-fills the form with the entry's options."""
    entry = make_config_entry(
        options={
            CONF_SUBTIPUS: ["VF", "VA"],
            CONF_ACTIVE_PHASES: DEFAULT_ACTIVE_PHASES,
            CONF_SCAN_INTERVAL: 10,
            CONF_MIN_VEHICLES: 2,
            CONF_HIGH_RISK_THRESHOLD: 1,
        }
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    schema = result["data_schema"].schema
    suggested = {
        str(key): key.description.get("suggested_value")
        for key in schema
        if key.description
    }
    assert suggested[CONF_SUBTIPUS] == ["VF", "VA"]
    assert suggested[CONF_SCAN_INTERVAL] == 10
    assert suggested[CONF_MIN_VEHICLES] == 2
    assert suggested[CONF_HIGH_RISK_THRESHOLD] == 1


async def test_options_flow_updates_entry_and_reloads(hass: HomeAssistant) -> None:
    """Changing options updates entry.options and reloads the coordinator.

    Sets up a real config entry (same pattern as `tests/test_sensor.py`) so
    the options-update listener wired in `__init__.py`'s `async_setup_entry`
    is actually registered, then verifies it triggers a reload — proving
    "options change reloads the coordinator without an HA restart"
    (docs/05-implementation-plan.md Task 11 acceptance criteria) end to end.
    """
    entry = make_config_entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.bomberscat.coordinator.fetch_incidents",
        AsyncMock(return_value=[]),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()

        result = await hass.config_entries.options.async_init(entry.entry_id)

        with patch(
            "homeassistant.config_entries.ConfigEntries.async_reload",
            wraps=hass.config_entries.async_reload,
        ) as mock_reload:
            result = await hass.config_entries.options.async_configure(
                result["flow_id"],
                {
                    CONF_SUBTIPUS: DEFAULT_SUBTIPUS,
                    CONF_ACTIVE_PHASES: DEFAULT_ACTIVE_PHASES,
                    CONF_SCAN_INTERVAL: 15,
                    CONF_MIN_VEHICLES: DEFAULT_MIN_VEHICLES,
                    CONF_HIGH_RISK_THRESHOLD: 2,
                },
            )
            await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SCAN_INTERVAL] == 15
    assert entry.options[CONF_HIGH_RISK_THRESHOLD] == 2
    mock_reload.assert_called_once_with(entry.entry_id)


# ---------------------------------------------------------------------------
# Reconfigure flow (Task 11)
# ---------------------------------------------------------------------------


async def test_reconfigure_updates_location_and_radii(hass: HomeAssistant) -> None:
    """Reconfigure moves the location/radii in place, same entry_id.

    Sets up the entry first (like the options-flow test) so the update
    listener from `__init__.py` is registered — proving reconfigure causes
    exactly ONE reload (the listener's), not two (QA-wave fix: the previous
    `async_update_reload_and_abort` scheduled a second one on top).
    """
    entry = make_config_entry(track_radius=100.0, alert_radius=30.0)
    entry.add_to_hass(hass)
    original_entry_id = entry.entry_id

    with patch(
        "custom_components.bomberscat.coordinator.fetch_incidents",
        AsyncMock(return_value=[]),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()

        result = await entry.start_reconfigure_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

        with patch(
            "homeassistant.config_entries.ConfigEntries.async_reload",
            wraps=hass.config_entries.async_reload,
        ) as mock_reload:
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"],
                {
                    CONF_LOCATION: {
                        CONF_LATITUDE: 42.0,
                        CONF_LONGITUDE: 3.0,
                        CONF_RADIUS: 20_000,
                    },
                    CONF_ALERT_RADIUS: 5,
                },
            )
            await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"

    assert entry.entry_id == original_entry_id
    assert entry.data[CONF_LATITUDE] == 42.0
    assert entry.data[CONF_LONGITUDE] == 3.0
    assert entry.data[CONF_TRACK_RADIUS] == 20.0
    assert entry.data[CONF_ALERT_RADIUS] == 5.0
    mock_reload.assert_called_once_with(entry.entry_id)


async def test_reconfigure_invalid_radius_shows_error(hass: HomeAssistant) -> None:
    """alert_radius > track_radius during reconfigure is a recoverable error."""
    entry = make_config_entry(track_radius=100.0, alert_radius=30.0)
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LOCATION: {
                CONF_LATITUDE: 42.0,
                CONF_LONGITUDE: 3.0,
                CONF_RADIUS: 10_000,  # 10 km
            },
            CONF_ALERT_RADIUS: 50,  # > 10 km track radius
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {CONF_ALERT_RADIUS: "alert_gt_track"}

    # Original data untouched.
    assert entry.data[CONF_TRACK_RADIUS] == 100.0
    assert entry.data[CONF_ALERT_RADIUS] == 30.0
