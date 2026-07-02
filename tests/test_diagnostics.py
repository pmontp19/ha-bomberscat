"""Tests for diagnostics.py (Task 13): redacted config-entry diagnostics."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from custom_components.incendiscat.arcgis import ArcgisClientError
from custom_components.incendiscat.diagnostics import (
    async_get_config_entry_diagnostics,
)
from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant

from .conftest import make_config_entry, make_incident


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
    return entry


async def test_diagnostics_redacts_home_coordinates(hass: HomeAssistant) -> None:
    entry = await _setup(hass, [])

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["entry"]["data"]["latitude"] == REDACTED
    assert diagnostics["entry"]["data"]["longitude"] == REDACTED
    # Non-PII config (radii) survives untouched.
    assert diagnostics["entry"]["data"]["track_radius"] == 100.0
    assert diagnostics["entry"]["data"]["alert_radius"] == 30.0


async def test_diagnostics_shape_reflects_healthy_coordinator(
    hass: HomeAssistant,
) -> None:
    inc = make_incident("1")
    entry = await _setup(hass, [inc])

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    coord = diagnostics["coordinator"]

    assert coord["last_update_success"] is True
    assert coord["last_update_status"] == "success"
    assert coord["last_error"] is None
    assert coord["last_error_kind"] is None
    assert coord["consecutive_failures"] == 0
    assert coord["degraded"] is False
    assert coord["tracked_incidents"] == 1
    assert coord["last_success"] is not None
    assert "pla_alfa" in diagnostics
    assert "last_update_success" in diagnostics["pla_alfa"]


async def test_diagnostics_reflects_error_state(hass: HomeAssistant) -> None:
    entry = await _setup(hass, [])
    coordinator = entry.runtime_data
    err = ArcgisClientError("not found", status=404, kind="http_404")

    with _patched_fetch(err):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    coord = diagnostics["coordinator"]

    assert coord["last_update_success"] is False
    assert coord["last_error"] == "not found"
    assert coord["last_error_kind"] == "http_404"
    assert coord["last_update_status"] == "error_http_404"
    assert coord["consecutive_failures"] == 1
    assert coord["degraded"] is False
