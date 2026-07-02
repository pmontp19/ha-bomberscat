"""Diagnostics support for incendiscat.

Backs the "Download diagnostics" button HA's core `diagnostics` component
adds to every config entry once this module exists (no manifest change
needed — `diagnostics` discovers `diagnostics.py` per-integration lazily).

Redaction: the only PII this integration's config entry holds is the
precise home location
(`CONF_LATITUDE`/`CONF_LONGITUDE` in `entry.data`, set by the config flow's
`LocationSelector` in `config_flow.py`) — everything else (radii, filters,
polling interval, high-risk threshold) is non-identifying. We redact both
`entry.data` and `entry.options` uniformly via `async_redact_data` rather
than hand-picking which of the two dicts the coordinates live in, since that
mapping is config-flow-version-dependent and redacting a key that happens
not to be present is a no-op.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant

from . import IncendiscatConfigEntry
from .coordinator import last_update_status

TO_REDACT = {CONF_LATITUDE, CONF_LONGITUDE}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: IncendiscatConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a incendiscat config entry."""
    coordinator = entry.runtime_data
    state = coordinator.data
    pla_alfa = coordinator.pla_alfa
    risk = pla_alfa.data

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_success": (
                state.last_success.isoformat() if state.last_success else None
            ),
            "last_error": state.last_error,
            "last_error_kind": state.last_error_kind,
            "last_update_status": last_update_status(state),
            "consecutive_failures": state.consecutive_4xx_failures,
            "degraded": state.degraded,
            "tracked_incidents": len(state.incidents),
        },
        "pla_alfa": {
            "last_update_success": pla_alfa.last_update_success,
            "has_data": risk is not None,
            "peril_m": risk.peril_m if risk is not None else None,
        },
    }
