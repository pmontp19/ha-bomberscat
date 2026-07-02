"""The Incendis Catalunya (incendiscat) integration.

Monitors real-time wildfire activity in Catalonia via the Catalan Fire
Department (Bombers) public ArcGIS FeatureServer, plus wildfire-risk data
from the Pla Alfa service.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN
from .coordinator import IncendiscatDataUpdateCoordinator
from .pla_alfa import PlaAlfaCoordinator

_LOGGER = logging.getLogger(__name__)

# entry.runtime_data alias (docs/04-architecture.md §5, "runtime-data" rule):
# the coordinator lives on the config entry itself, not hass.data[DOMAIN].
#
# runtime_data strategy: rather than reshaping `runtime_data` into a
# 2-coordinator container (which would ripple into every existing reader —
# sensor.py/binary_sensor.py/geo_location.py and 4+ test files), we keep
# `entry.runtime_data` exactly as-is (the Bombers coordinator) and attach
# the Pla Alfa coordinator to it as a plain instance attribute,
# `coordinator.pla_alfa`, set once in `async_setup_entry` below before
# entities are created. This is the "smallest diff" option:
# `IncendiscatDataUpdateCoordinator` is a regular (non-slotted) class, so
# this is a normal, if untyped-on-the-class, attribute — sensor.py
# /binary_sensor.py read it as `entry.runtime_data.pla_alfa`.
type IncendiscatConfigEntry = ConfigEntry[IncendiscatDataUpdateCoordinator]

PLATFORMS: tuple[Platform, ...] = (
    Platform.BINARY_SENSOR,
    Platform.GEO_LOCATION,
    Platform.SENSOR,
)

__all__ = ["DOMAIN", "IncendiscatConfigEntry"]


async def async_setup_entry(hass: HomeAssistant, entry: IncendiscatConfigEntry) -> bool:
    """Set up incendiscat from a config entry.

    `async_config_entry_first_refresh()` runs the first poll synchronously
    and raises `ConfigEntryNotReady` on failure (network error, FeatureServer
    down, ...), which is exactly the "fail setup cleanly, let HA retry
    later" behavior expected — we do not need to catch anything here.

    The Pla Alfa coordinator is set up the same way, *except* a
    failed first refresh must not abort the whole entry: fire monitoring is
    the integration's core value and Pla Alfa is a best-effort bonus, so we
    catch `ConfigEntryNotReady` from it, log, and continue — `fire_risk`/
    `high_risk` simply come up `unavailable` (their `CoordinatorEntity`
    `available` follows `coordinator.last_update_success`, which is `False`
    here) until the next successful poll, `PLA_ALFA_SCAN_INTERVAL_HOURS`
    later. Polling still resumes on that schedule because
    `DataUpdateCoordinator` reschedules its next refresh as soon as its
    first listener (the `fire_risk`/`high_risk` entities, added below by
    `async_forward_entry_setups`) subscribes, regardless of whether the
    *previous* refresh succeeded.
    """
    session = async_get_clientsession(hass)
    coordinator = IncendiscatDataUpdateCoordinator(hass, entry, session)
    await coordinator.async_config_entry_first_refresh()

    pla_alfa_coordinator = PlaAlfaCoordinator(hass, entry, session)
    try:
        await pla_alfa_coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady as err:
        _LOGGER.warning(
            "Pla Alfa fire-risk data unavailable on startup (%s); "
            "fire_risk/high_risk will show as unavailable until the next "
            "successful poll. Wildfire monitoring is unaffected.",
            err,
        )
    coordinator.pla_alfa = pla_alfa_coordinator

    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_update_listener(
    hass: HomeAssistant, entry: IncendiscatConfigEntry
) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(
    hass: HomeAssistant, entry: IncendiscatConfigEntry
) -> bool:
    """Unload a incendiscat config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
