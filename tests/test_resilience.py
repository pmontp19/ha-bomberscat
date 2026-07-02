"""Tests for Task 13: diagnostic entities + service-degradation resilience.

Sets up a real config entry (patching `fetch_incidents`, as in
`test_binary_sensor.py`/`test_sensor.py`) then drives failure/recovery
cycles via `coordinator.async_refresh()` directly, reading the resulting
`binary_sensor.service_connected` / `sensor.last_update` /
`sensor.last_update_status` entities from `hass.states`.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from custom_components.incendiscat.arcgis import ArcgisClientError
from custom_components.incendiscat.const import DOMAIN, EVENT_SERVICE_DEGRADED
from custom_components.incendiscat.coordinator import (
    IncendiscatState,
    last_update_status,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import async_capture_events

from .conftest import FakeClock, make_config_entry, make_incident


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


def _entity_id(hass: HomeAssistant, entry, platform: str, key: str) -> str:
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert entity_id is not None, f"no {platform} entity registered for key {key!r}"
    return entity_id


def _issue_id(entry) -> str:
    return f"service_degraded_{entry.entry_id}"


# ---------------------------------------------------------------------------
# service_connected on/off across ok -> fail -> ok
# ---------------------------------------------------------------------------


async def test_service_connected_reflects_last_refresh_result(
    hass: HomeAssistant,
) -> None:
    inc = make_incident("1")
    entry = await _setup(hass, [inc])
    entity_id = _entity_id(hass, entry, "binary_sensor", "service_connected")

    assert hass.states.get(entity_id).state == "on"

    coordinator = entry.runtime_data
    with _patched_fetch(ArcgisClientError("boom", kind="timeout")):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    # Off, not "unavailable": the diagnostic entity must keep reporting even
    # while the tracked service is down (see ServiceConnectedBinarySensor's
    # `available` override).
    assert hass.states.get(entity_id).state == "off"
    # Previous state kept: the fire tracked before the outage is unaffected.
    assert coordinator.data.incidents == {"1": inc}

    with _patched_fetch([inc]):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "on"


# ---------------------------------------------------------------------------
# last_update / last_update_status
# ---------------------------------------------------------------------------


async def test_last_update_status_classification(
    hass: HomeAssistant, clock: FakeClock
) -> None:
    """Each failure below is separated by a recovering success on purpose:
    `DataUpdateCoordinator._async_refresh` skips notifying listeners (and
    therefore skips `async_write_ha_state()`) when two *consecutive*
    failures leave `last_update_success` unchanged (`False` -> `False`) —
    see its early-`return` guard. That optimization is orthogonal to this
    task (our own `consecutive_4xx_failures`/`degraded` bookkeeping and the
    `incendiscat_service_degraded` event/issue are updated unconditionally
    inside `_async_update_data`, independent of whether HA also pushes an
    entity-state update — see `test_persistent_404_fires_event_once_and_
    creates_issue`), so each case here goes through a fresh success->failure
    transition to reliably observe it via `hass.states`. Full classifier
    coverage (all kinds, in any order) is in
    `test_last_update_status_classifier_covers_all_kinds` below, which calls
    `last_update_status()` directly and does not depend on this HA detail.
    """
    entry = await _setup(hass, [])
    status_id = _entity_id(hass, entry, "sensor", "last_update_status")
    update_id = _entity_id(hass, entry, "sensor", "last_update")

    assert hass.states.get(status_id).state == "success"
    first_timestamp = hass.states.get(update_id).state
    assert first_timestamp != "unknown"
    last_known_good = first_timestamp

    coordinator = entry.runtime_data
    cases = [
        (ArcgisClientError("nope", status=404, kind="http_404"), "error_http_404"),
        (ArcgisClientError("timed out", kind="timeout"), "error_timeout"),
        (ArcgisClientError("bad gw", status=502, kind="http_5xx"), "error_http_5xx"),
        (ArcgisClientError("bad json", kind="parse"), "error_parse"),
        (ArcgisClientError("teapot", status=418, kind="http_4xx"), "error_unknown"),
    ]
    for err, expected in cases:
        clock.advance(minutes=1)
        with _patched_fetch(err):
            await coordinator.async_refresh()
            await hass.async_block_till_done()
        assert hass.states.get(status_id).state == expected
        # last_update (last *successful* sync) must not move during a
        # failure: it should still show the previous successful timestamp.
        assert hass.states.get(update_id).state == last_known_good

        clock.advance(minutes=1)
        with _patched_fetch([]):
            await coordinator.async_refresh()
            await hass.async_block_till_done()
        assert hass.states.get(status_id).state == "success"
        last_known_good = hass.states.get(update_id).state

    assert last_known_good != first_timestamp


def test_last_update_status_classifier_covers_all_kinds() -> None:
    """Unit-level coverage of `last_update_status()` for every
    `ArcgisClientError.kind`, independent of coordinator/HA plumbing."""
    assert last_update_status(IncendiscatState(last_error=None)) == "success"
    cases = {
        "http_404": "error_http_404",
        "timeout": "error_timeout",
        "http_5xx": "error_http_5xx",
        "parse": "error_parse",
        "http_4xx": "error_unknown",
        "unknown": "error_unknown",
        None: "error_unknown",
    }
    for kind, expected in cases.items():
        state = IncendiscatState(last_error="boom", last_error_kind=kind)
        assert last_update_status(state) == expected


# ---------------------------------------------------------------------------
# Persistent 404-class failures -> service_degraded event + repair issue
# ---------------------------------------------------------------------------


async def test_persistent_404_fires_event_once_and_creates_issue(
    hass: HomeAssistant,
) -> None:
    entry = await _setup(hass, [])
    coordinator = entry.runtime_data
    degraded_events = async_capture_events(hass, EVENT_SERVICE_DEGRADED)
    err = ArcgisClientError("not found", status=404, kind="http_404")

    with _patched_fetch(err, err, err):
        await coordinator.async_refresh()
        assert degraded_events == []
        await coordinator.async_refresh()
        assert degraded_events == []
        await coordinator.async_refresh()

    assert len(degraded_events) == 1
    assert degraded_events[0].data["consecutive_failures"] == 3

    registry = ir.async_get(hass)
    issue = registry.async_get_issue(DOMAIN, _issue_id(entry))
    assert issue is not None
    assert issue.is_fixable is False
    assert issue.severity is ir.IssueSeverity.WARNING
    assert issue.translation_key == "service_degraded"

    # A 4th consecutive failure must not refire the event or duplicate the
    # issue ("once, not every cycle").
    with _patched_fetch(err):
        await coordinator.async_refresh()
    assert len(degraded_events) == 1
    assert registry.async_get_issue(DOMAIN, _issue_id(entry)) is not None


async def test_recovery_clears_issue_and_does_not_refire(
    hass: HomeAssistant,
) -> None:
    entry = await _setup(hass, [])
    coordinator = entry.runtime_data
    degraded_events = async_capture_events(hass, EVENT_SERVICE_DEGRADED)
    err = ArcgisClientError("not found", status=404, kind="http_404")

    with _patched_fetch(err, err, err):
        for _ in range(3):
            await coordinator.async_refresh()
    assert len(degraded_events) == 1
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, _issue_id(entry)) is not None

    with _patched_fetch([]):
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert registry.async_get_issue(DOMAIN, _issue_id(entry)) is None
    assert coordinator.data.consecutive_4xx_failures == 0
    assert coordinator.data.degraded is False

    # A fresh streak of 404s after recovery must be able to re-trigger.
    with _patched_fetch(err, err, err):
        for _ in range(3):
            await coordinator.async_refresh()

    assert len(degraded_events) == 2
    assert registry.async_get_issue(DOMAIN, _issue_id(entry)) is not None


async def test_streak_resets_on_different_failure_kind(hass: HomeAssistant) -> None:
    """A timeout in between two 404s breaks the "consecutive" streak: the
    3rd-in-a-row 404 right after must not immediately degrade the service."""
    entry = await _setup(hass, [])
    coordinator = entry.runtime_data
    degraded_events = async_capture_events(hass, EVENT_SERVICE_DEGRADED)
    err_404 = ArcgisClientError("not found", status=404, kind="http_404")
    err_timeout = ArcgisClientError("timed out", kind="timeout")

    with _patched_fetch(err_404, err_404, err_timeout, err_404, err_404):
        for _ in range(5):
            await coordinator.async_refresh()

    assert degraded_events == []
    assert coordinator.data.consecutive_4xx_failures == 2
    assert coordinator.data.degraded is False
