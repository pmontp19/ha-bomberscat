"""Tests for coordinator.py: fetch/filter/radius cycle logic (Task 5).

These tests patch `fetch_incidents` at the coordinator's import site and
drive `IncendiscatDataUpdateCoordinator` directly with `async_refresh()`
(rather than `async_config_entry_first_refresh()`, which additionally
requires the config entry to be mid-setup — that flow is covered by the
setup-entry tests in `test_lifecycle.py`). Event emission (Task 9) is
covered separately in `test_events.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.incendiscat.arcgis import ArcgisClientError
from custom_components.incendiscat.const import (
    CONF_ACTIVE_PHASES,
    CONF_MIN_VEHICLES,
    CONF_SUBTIPUS,
    EVENT_FIRE_DETECTED,
    EVENT_FIRE_RESOLVED,
)
from custom_components.incendiscat.coordinator import (
    IncendiscatDataUpdateCoordinator,
    IncendiscatRuntimeConfig,
    _apply_incident,
    _cleanup_resolved,
    _passes_filters,
    _prune_vanished,
    _should_track,
)
from custom_components.incendiscat.models import Fase, Tipus
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_capture_events

from .conftest import HOME_LAT, HOME_LON, make_config_entry, make_incident

FAR_AWAY_LAT = HOME_LAT + 10.0  # ~1100 km north: outside any sane track radius

_DEFAULT_CFG = IncendiscatRuntimeConfig(
    home_lat=HOME_LAT,
    home_lon=HOME_LON,
    track_radius_km=100,
    alert_radius_km=30,
    subtipus=frozenset({"VF"}),
    active_phases=frozenset({"Actiu"}),
    min_vehicles=0,
    scan_interval_min=5,
)


def _coordinator(hass: HomeAssistant, entry=None) -> IncendiscatDataUpdateCoordinator:
    entry = entry or make_config_entry()
    return IncendiscatDataUpdateCoordinator(hass, entry, MagicMock(name="session"))


def _patched_fetch(*side_effects):
    return patch(
        "custom_components.incendiscat.coordinator.fetch_incidents",
        AsyncMock(side_effect=list(side_effects)),
    )


# ---------------------------------------------------------------------------
# Full cycle: add / update / remove
# ---------------------------------------------------------------------------


async def test_first_cycle_adds_tracked_incident(hass: HomeAssistant) -> None:
    inc = make_incident("1")
    coordinator = _coordinator(hass)
    with _patched_fetch([inc]):
        await coordinator.async_refresh()

    assert coordinator.data.incidents == {"1": inc}
    assert coordinator.last_update_success is True


async def test_second_cycle_updates_existing_incident(hass: HomeAssistant) -> None:
    inc_v1 = make_incident("1", vehicles=1)
    inc_v2 = make_incident("1", vehicles=5)
    coordinator = _coordinator(hass)
    with _patched_fetch([inc_v1], [inc_v2]):
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    assert coordinator.data.incidents["1"].vehicles == 5


async def test_second_cycle_missing_first_incident_is_pruned_not_kept(
    hass: HomeAssistant,
) -> None:
    """Deliberately changed under full-fetch semantics (see module
    docstring): each cycle's `fetched` batch is treated as the *complete*
    current view, so an act_num absent from it (here "1", on the second
    fetch) has vanished from the source and gets resolved/pruned rather
    than assumed unchanged. Only "2" (present in the second fetch) survives.
    """
    inc1 = make_incident("1")
    inc2 = make_incident("2")
    coordinator = _coordinator(hass)
    with _patched_fetch([inc1], [inc2]):
        await coordinator.async_refresh()
        await coordinator.async_refresh()

    assert set(coordinator.data.incidents) == {"2"}


async def test_incident_removed_when_phase_leaves_active_set(
    hass: HomeAssistant,
) -> None:
    """Controlat is not in the default active_phases and is not Extingit."""
    tracked = make_incident("1", fase=Fase.ACTIU)
    dropped = make_incident("1", fase=Fase.CONTROLAT)
    coordinator = _coordinator(hass)
    with _patched_fetch([tracked], [dropped]):
        await coordinator.async_refresh()
        assert "1" in coordinator.data.incidents
        await coordinator.async_refresh()

    assert coordinator.data.incidents == {}


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------


async def test_subtipus_filter_excludes_non_matching_type(
    hass: HomeAssistant,
) -> None:
    urban_fire = make_incident("1", tipus=Tipus.URBANA)
    coordinator = _coordinator(hass)
    with _patched_fetch([urban_fire]):
        await coordinator.async_refresh()

    assert coordinator.data.incidents == {}


async def test_subtipus_filter_can_be_widened_via_options(
    hass: HomeAssistant,
) -> None:
    """New (lowercase-slug) stored form: `["vf", "vu"]`."""
    urban_fire = make_incident("1", tipus=Tipus.URBANA)
    entry = make_config_entry(options={CONF_SUBTIPUS: ["vf", "vu"]})
    coordinator = _coordinator(hass, entry)
    with _patched_fetch([urban_fire]):
        await coordinator.async_refresh()

    assert "1" in coordinator.data.incidents


async def test_subtipus_filter_widened_via_legacy_capitalized_options(
    hass: HomeAssistant,
) -> None:
    """Legacy (pre-lowercase-slug) stored form: `["VF", "VU"]`.

    Entries created before options started storing lowercase slugs have
    the domain value itself in `entry.options` -- `IncendiscatRuntimeConfig
    .from_entry`'s normalization must still resolve these to the same
    domain set as the new form, so old entries keep working unmigrated.
    """
    urban_fire = make_incident("1", tipus=Tipus.URBANA)
    entry = make_config_entry(options={CONF_SUBTIPUS: ["VF", "VU"]})
    coordinator = _coordinator(hass, entry)
    with _patched_fetch([urban_fire]):
        await coordinator.async_refresh()

    assert "1" in coordinator.data.incidents


async def test_min_vehicles_filter(hass: HomeAssistant) -> None:
    small = make_incident("1", vehicles=1)
    entry = make_config_entry(options={CONF_MIN_VEHICLES: 2})
    coordinator = _coordinator(hass, entry)
    with _patched_fetch([small]):
        await coordinator.async_refresh()
    assert coordinator.data.incidents == {}

    reinforced = make_incident("1", vehicles=2)
    with _patched_fetch([reinforced]):
        await coordinator.async_refresh()
    assert "1" in coordinator.data.incidents


async def test_fresh_extingit_incident_is_never_tracked(hass: HomeAssistant) -> None:
    """An incident already Extingit the first time we see it is ignored:

    there is nothing to alert on and no transition to observe (see
    coordinator.py's `_passes_filters` docstring).
    """
    already_out = make_incident("1", fase=Fase.EXTINGIT)
    coordinator = _coordinator(hass)
    with _patched_fetch([already_out]):
        await coordinator.async_refresh()

    assert coordinator.data.incidents == {}
    assert coordinator.data.resolved_at == {}


# ---------------------------------------------------------------------------
# Radius filtering
# ---------------------------------------------------------------------------


async def test_incident_outside_track_radius_is_not_tracked(
    hass: HomeAssistant,
) -> None:
    far = make_incident("1", lat=FAR_AWAY_LAT, lon=HOME_LON)
    coordinator = _coordinator(hass, make_config_entry(track_radius=100.0))
    with _patched_fetch([far]):
        await coordinator.async_refresh()

    assert coordinator.data.incidents == {}


async def test_incident_inside_track_radius_is_tracked(hass: HomeAssistant) -> None:
    near = make_incident("1", lat=HOME_LAT + 0.1, lon=HOME_LON)
    coordinator = _coordinator(hass, make_config_entry(track_radius=100.0))
    with _patched_fetch([near]):
        await coordinator.async_refresh()

    assert "1" in coordinator.data.incidents


# ---------------------------------------------------------------------------
# Fetch errors keep previous state
# ---------------------------------------------------------------------------


async def test_fetch_error_keeps_previous_incidents_and_sets_last_error(
    hass: HomeAssistant,
) -> None:
    inc = make_incident("1")
    coordinator = _coordinator(hass)
    with _patched_fetch([inc]):
        await coordinator.async_refresh()
    previous_data = coordinator.data

    with _patched_fetch(ArcgisClientError("boom")):
        await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert coordinator.data is previous_data  # same object: never reassigned
    assert coordinator.data.incidents == {"1": inc}
    assert coordinator.data.last_error == "boom"


async def test_recovers_after_fetch_error(hass: HomeAssistant) -> None:
    inc = make_incident("1")
    coordinator = _coordinator(hass)
    with _patched_fetch([inc], ArcgisClientError("boom"), [inc]):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        assert coordinator.last_update_success is False
        await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.last_error is None


# ---------------------------------------------------------------------------
# Full fetch every cycle (`since` is always None)
# ---------------------------------------------------------------------------


async def test_distance_km_reflects_updated_coordinates_across_cycles(
    hass: HomeAssistant,
) -> None:
    """distance_km must not be cached by act_num: the same incident can get
    corrected/updated coordinates on a later snapshot row, and events/sensors
    should reflect the new distance, not a stale one from the first sighting.
    """
    near = make_incident("1", lat=HOME_LAT, lon=HOME_LON)
    far = make_incident("1", lat=FAR_AWAY_LAT, lon=HOME_LON)
    coordinator = _coordinator(hass)

    assert coordinator.distance_km(near) == pytest.approx(0.0, abs=1e-6)
    assert coordinator.distance_km(far) > 1000


async def test_fetch_called_with_since_none_every_cycle(hass: HomeAssistant) -> None:
    """The view enforces a ~4-day retention window keyed on `DATA_ACT`
    (verified live, 2026-07-02): a row that ages out simply vanishes, so an
    incremental `since` cursor can never observe a deletion. We therefore
    fetch the whole dataset (`since=None`) every cycle and reconcile by
    pruning act_nums absent from the result (see `_prune_vanished`)."""
    first = make_incident("1")
    coordinator = _coordinator(hass)

    mock_fetch = AsyncMock(side_effect=[[first], [first]])
    with patch("custom_components.incendiscat.coordinator.fetch_incidents", mock_fetch):
        await coordinator.async_refresh()
        assert mock_fetch.call_args_list[0].kwargs["since"] is None

        await coordinator.async_refresh()
        assert mock_fetch.call_args_list[1].kwargs["since"] is None


# ---------------------------------------------------------------------------
# Full-fetch reconciliation: pruning incidents absent from the fetched batch
# ---------------------------------------------------------------------------


async def test_active_incident_absent_from_next_fetch_is_resolved_and_pruned(
    hass: HomeAssistant,
) -> None:
    """An act_num that was tracked and simply stops appearing in a full
    fetch has vanished from the source view (retention window, or any other
    reason) -- there is no other signal we will ever get that it is gone.
    It must be resolved (using its last-known fase) and removed."""
    resolved = async_capture_events(hass, EVENT_FIRE_RESOLVED)
    active = make_incident("1", fase=Fase.ACTIU)
    coordinator = _coordinator(hass)

    with _patched_fetch([active], []):
        await coordinator.async_refresh()
        assert "1" in coordinator.data.incidents
        await coordinator.async_refresh()

    assert coordinator.data.incidents == {}
    assert len(resolved) == 1
    assert resolved[0].data["act_num"] == "1"
    assert resolved[0].data["final_fase"] == "Actiu"


async def test_extingit_in_grace_period_absent_from_fetch_is_pruned_once(
    hass: HomeAssistant,
) -> None:
    """An Extingit incident sitting out its removal grace period already
    fired `incendiscat_fire_resolved` when it turned Extingit. If it then
    vanishes from a full fetch (e.g. it aged out of the retention window
    before the grace period elapsed), it must be pruned WITHOUT firing a
    second `incendiscat_fire_resolved`."""
    resolved = async_capture_events(hass, EVENT_FIRE_RESOLVED)
    active = make_incident("1", fase=Fase.ACTIU)
    extinguished = make_incident("1", fase=Fase.EXTINGIT)
    coordinator = _coordinator(hass)

    with _patched_fetch([active], [extinguished], []):
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        assert "1" in coordinator.data.resolved_at
        await coordinator.async_refresh()

    assert coordinator.data.incidents == {}
    assert coordinator.data.resolved_at == {}
    assert len(resolved) == 1  # only the Actiu -> Extingit transition, not the vanish


async def test_incident_reappearing_after_vanishing_fires_detected_again(
    hass: HomeAssistant,
) -> None:
    """Chosen semantics for the reappear edge case (transient service
    flakiness, or a genuinely reopened act_num): once an act_num is pruned
    for being absent from a full fetch, the coordinator has no memory that
    it ever existed. If it reappears later it is treated as brand new, so
    `incendiscat_fire_detected` fires again. This is the simplest option that
    is still correct -- suppressing it would mean carrying "ghost" state
    forward indefinitely, exactly what full-fetch pruning exists to avoid.
    """
    detected = async_capture_events(hass, EVENT_FIRE_DETECTED)
    inc = make_incident("1", fase=Fase.ACTIU)
    coordinator = _coordinator(hass)

    with _patched_fetch([inc], [], [inc]):
        await coordinator.async_refresh()  # baseline (suppressed, first refresh)
        await coordinator.async_refresh()  # vanished -> resolved + pruned
        await coordinator.async_refresh()  # reappears -> detected again

    assert len(detected) == 1
    assert "1" in coordinator.data.incidents


def test_prune_vanished_removes_absent_act_nums_and_resolves_active_ones() -> None:
    base_incidents = {
        "1": make_incident("1", fase=Fase.ACTIU),
        "2": make_incident("2", fase=Fase.EXTINGIT),
    }
    incidents = dict(base_incidents)
    resolved_at = {"2": datetime.now(UTC)}

    events = _prune_vanished(
        base_incidents,
        fetched_act_nums=set(),
        incidents=incidents,
        resolved_at=resolved_at,
        now=datetime.now(UTC),
    )

    assert incidents == {}
    assert resolved_at == {}
    # "1" was still active and not yet resolved -> gets a resolved event;
    # "2" already fired resolved when it turned Extingit -> no duplicate.
    assert [e[0] for e in events] == [EVENT_FIRE_RESOLVED]
    assert events[0][1]["act_num"] == "1"


def test_prune_vanished_keeps_act_nums_still_present_in_fetch() -> None:
    base_incidents = {"1": make_incident("1", fase=Fase.ACTIU)}
    incidents = dict(base_incidents)
    resolved_at: dict[str, datetime] = {}

    events = _prune_vanished(
        base_incidents,
        fetched_act_nums={"1"},
        incidents=incidents,
        resolved_at=resolved_at,
        now=datetime.now(UTC),
    )

    assert incidents == {"1": base_incidents["1"]}
    assert events == []


# ---------------------------------------------------------------------------
# Pure helper functions
# ---------------------------------------------------------------------------


def test_passes_filters_extingit_always_passes_phase_check() -> None:
    extingit = make_incident("1", fase=Fase.EXTINGIT)
    controlat = make_incident("1", fase=Fase.CONTROLAT)
    assert _passes_filters(extingit, _DEFAULT_CFG)
    assert not _passes_filters(controlat, _DEFAULT_CFG)


def test_should_track_false_when_outside_radius_even_if_was_tracked() -> None:
    inc = make_incident("1", fase=Fase.ACTIU)
    assert not _should_track(inc, _DEFAULT_CFG, distance_km=500, was_tracked=True)
    assert _should_track(inc, _DEFAULT_CFG, distance_km=1, was_tracked=True)


def test_apply_incident_resolved_when_leaving_tracking() -> None:
    inc = make_incident("1", fase=Fase.CONTROLAT)
    base_incidents = {"1": make_incident("1", fase=Fase.ACTIU)}
    incidents = dict(base_incidents)
    resolved_at: dict[str, datetime] = {}

    events = _apply_incident(
        inc,
        _DEFAULT_CFG,
        base_incidents=base_incidents,
        incidents=incidents,
        resolved_at=resolved_at,
        distance=0.0,
        now=datetime.now(UTC),
    )

    assert incidents == {}
    assert resolved_at == {}
    assert [e[0] for e in events] == [EVENT_FIRE_RESOLVED]
    assert events[0][1]["final_fase"] == "Controlat"


def test_cleanup_resolved_removes_only_expired() -> None:
    now = datetime(2026, 7, 2, 12, 0, tzinfo=UTC)
    incidents = {
        "expired": make_incident("expired"),
        "fresh": make_incident("fresh"),
    }
    resolved_at = {
        "expired": now - timedelta(minutes=61),
        "fresh": now - timedelta(minutes=5),
    }
    _cleanup_resolved(incidents, resolved_at, 60, now)

    assert set(incidents) == {"fresh"}
    assert set(resolved_at) == {"fresh"}


@pytest.mark.parametrize("act_num", ["", None])
async def test_incident_without_act_num_is_skipped(
    hass: HomeAssistant, act_num: str | None
) -> None:
    inc = make_incident("1")
    object.__setattr__(inc, "act_num", act_num)
    coordinator = _coordinator(hass)
    with _patched_fetch([inc]):
        await coordinator.async_refresh()

    assert coordinator.data.incidents == {}


# ---------------------------------------------------------------------------
# IncendiscatRuntimeConfig.from_entry: stored-slug -> domain-value normalization
# ---------------------------------------------------------------------------
#
# `entry.options[CONF_SUBTIPUS]`/`[CONF_ACTIVE_PHASES]` store lowercase slugs
# ("vf", "actiu", ...) because the config-flow SelectSelector's option value
# doubles as a hassfest-validated translation key. `from_entry` must resolve
# both that new form and the legacy (pre-change) capitalized-domain-value
# form back to the same `Tipus`/`Fase` domain set, since that is what every
# downstream filter (`_passes_filters`, `sensor.py`, `binary_sensor.py`)
# compares against.


def test_from_entry_normalizes_new_lowercase_slug_options() -> None:
    entry = make_config_entry(
        options={
            CONF_SUBTIPUS: ["vf", "vu"],
            CONF_ACTIVE_PHASES: ["actiu", "controlat"],
        }
    )
    cfg = IncendiscatRuntimeConfig.from_entry(entry)

    assert cfg.subtipus == {"VF", "VU"}
    assert cfg.active_phases == {"Actiu", "Controlat"}


def test_from_entry_normalizes_legacy_capitalized_options() -> None:
    """Entries created before options started storing lowercase slugs kept
    the domain value itself (e.g. "VF", "Actiu") in `entry.options` --
    `from_entry` must still resolve these to the same domain set as the new
    lowercase-slug form, so old entries keep filtering correctly unmigrated.
    """
    entry = make_config_entry(
        options={
            CONF_SUBTIPUS: ["VF", "VU"],
            CONF_ACTIVE_PHASES: ["Actiu", "Controlat"],
        }
    )
    cfg = IncendiscatRuntimeConfig.from_entry(entry)

    assert cfg.subtipus == {"VF", "VU"}
    assert cfg.active_phases == {"Actiu", "Controlat"}


def test_from_entry_uses_defaults_when_options_unset() -> None:
    """Fresh installs / entries with no options at all fall back to
    `const.DEFAULT_SUBTIPUS`/`DEFAULT_ACTIVE_PHASES` (stored, lowercase-slug
    form) and normalize them the same way as explicit options."""
    entry = make_config_entry()
    cfg = IncendiscatRuntimeConfig.from_entry(entry)

    assert cfg.subtipus == {"VF"}
    assert cfg.active_phases == {"Actiu", "Estabilitzat"}
