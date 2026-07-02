"""Tests for entry setup/unload lifecycle and grace-period cleanup (Task 5).

Setup-entry tests exercise the real `async_setup_entry`/`async_unload_entry`
via `hass.config_entries.async_setup()`, patching `fetch_incidents` so no
network access happens. Grace-period tests drive
`IncendiscatDataUpdateCoordinator` directly (as in `test_coordinator.py`),
using the `clock` fixture to advance time deterministically instead of
sleeping for real minutes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.incendiscat.arcgis import ArcgisClientError
from custom_components.incendiscat.coordinator import IncendiscatDataUpdateCoordinator
from custom_components.incendiscat.models import Fase
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

from .conftest import FakeClock, make_config_entry, make_incident


def _coordinator(
    hass: HomeAssistant, entry=None, **kwargs
) -> IncendiscatDataUpdateCoordinator:
    entry = entry or make_config_entry()
    return IncendiscatDataUpdateCoordinator(
        hass, entry, MagicMock(name="session"), **kwargs
    )


def _patched_fetch(*side_effects):
    return patch(
        "custom_components.incendiscat.coordinator.fetch_incidents",
        AsyncMock(side_effect=list(side_effects)),
    )


# ---------------------------------------------------------------------------
# Setup entry
# ---------------------------------------------------------------------------


async def test_setup_entry_success_loads_entry_with_coordinator(
    hass: HomeAssistant,
) -> None:
    entry = make_config_entry()
    entry.add_to_hass(hass)

    with _patched_fetch([]):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, IncendiscatDataUpdateCoordinator)
    assert entry.runtime_data.data.incidents == {}


async def test_setup_entry_network_error_sets_setup_retry(
    hass: HomeAssistant,
) -> None:
    entry = make_config_entry()
    entry.add_to_hass(hass)

    with _patched_fetch(ArcgisClientError("unreachable")):
        assert await hass.config_entries.async_setup(entry.entry_id) is False
        await hass.async_block_till_done()

    # async_config_entry_first_refresh() turns the fetch failure into
    # ConfigEntryNotReady, which the config entries setup machinery turns
    # into a scheduled retry rather than a hard failure.
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_entry(hass: HomeAssistant) -> None:
    entry = make_config_entry()
    entry.add_to_hass(hass)

    with _patched_fetch([]):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()

    assert await hass.config_entries.async_unload(entry.entry_id) is True
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


# ---------------------------------------------------------------------------
# Grace-period cleanup
# ---------------------------------------------------------------------------


async def test_extingit_incident_kept_during_grace_period(
    hass: HomeAssistant, clock: FakeClock
) -> None:
    coordinator = _coordinator(hass, resolved_grace_minutes=60)
    active = make_incident("1", fase=Fase.ACTIU)
    extinguished = make_incident("1", fase=Fase.EXTINGIT)

    with _patched_fetch([], [active], [extinguished]):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    assert "1" in coordinator.data.incidents
    assert "1" in coordinator.data.resolved_at

    # Not yet past the grace period: still present on subsequent polls, as
    # long as the source view still reports the row unchanged. Under
    # full-fetch semantics (every cycle fetches the *whole* current view,
    # see coordinator.py's module docstring) a genuinely empty fetch would
    # mean "the row vanished" and prune it immediately -- so this poll
    # re-fetches `extinguished` to exercise the timer-based grace-period
    # path specifically, not the vanish-prune path.
    clock.advance(minutes=59)
    with _patched_fetch([extinguished]):
        await coordinator.async_refresh()
    assert "1" in coordinator.data.incidents


async def test_extingit_incident_removed_after_grace_period(
    hass: HomeAssistant, clock: FakeClock
) -> None:
    coordinator = _coordinator(hass, resolved_grace_minutes=60)
    active = make_incident("1", fase=Fase.ACTIU)
    extinguished = make_incident("1", fase=Fase.EXTINGIT)

    with _patched_fetch([], [active], [extinguished]):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    # Re-fetch the unchanged Extingit row (see comment in the "kept during
    # grace period" test above): removal here must come from the elapsed
    # grace timer, not from the row being absent from the fetch.
    clock.advance(minutes=61)
    with _patched_fetch([extinguished]):
        await coordinator.async_refresh()

    assert "1" not in coordinator.data.incidents
    assert "1" not in coordinator.data.resolved_at


async def test_reactivated_incident_clears_stale_grace_timer(
    hass: HomeAssistant, clock: FakeClock
) -> None:
    """Defensive edge case: an Extingit incident sitting in its grace period
    that comes back as non-Extingit (real-world unlikely, since Extingit is
    normally terminal) must not be swept away by its old timer later."""
    coordinator = _coordinator(hass, resolved_grace_minutes=60)
    active = make_incident("1", fase=Fase.ACTIU)
    extinguished = make_incident("1", fase=Fase.EXTINGIT)
    reactivated = make_incident("1", fase=Fase.ACTIU)

    with _patched_fetch([], [active], [extinguished], [reactivated]):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        assert "1" in coordinator.data.resolved_at
        await coordinator.async_refresh()

    assert "1" not in coordinator.data.resolved_at

    # `resolved_at` is already clear at this point, so the vanish-prune path
    # doesn't apply here; re-fetch the reactivated row unchanged (full-fetch
    # semantics: a genuinely empty result would mean the row vanished and
    # prune it, defeating what this test is checking) to confirm the
    # cleared timer keeps it tracked indefinitely.
    clock.advance(minutes=61)
    with _patched_fetch([reactivated]):
        await coordinator.async_refresh()

    assert "1" in coordinator.data.incidents


async def test_grace_period_is_per_incident(
    hass: HomeAssistant, clock: FakeClock
) -> None:
    coordinator = _coordinator(hass, resolved_grace_minutes=60)
    active1 = make_incident("1", fase=Fase.ACTIU)
    active2 = make_incident("2", fase=Fase.ACTIU)
    extinguished1 = make_incident("1", fase=Fase.EXTINGIT)

    # "2" is re-fetched unchanged in every subsequent cycle (full-fetch
    # semantics: an empty/missing row means "vanished from the view", not
    # "no news") so that only "1"'s Extingit grace timer is under test here.
    with _patched_fetch([], [active1, active2], [extinguished1, active2]):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    clock.advance(minutes=61)
    with _patched_fetch([extinguished1, active2]):
        await coordinator.async_refresh()

    assert coordinator.data.incidents == {"2": active2}
