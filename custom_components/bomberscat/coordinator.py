"""Data update coordinator for bomberscat (Task 5) + event emission (Task 9).

Polls the Bombers ArcGIS FeatureServer (via ``arcgis.fetch_incidents``) on an
interval, applies the tracking filters/radius from docs/03-feature-spec.md
§2, and fires the ``bomberscat_fire_detected`` / ``bomberscat_phase_change`` /
``bomberscat_fire_resolved`` events from docs/03-feature-spec.md §4.

Deviation from the `BomberscatState` sketch in docs/04-architecture.md §5:
that sketch keeps *both* the current `incidents` dict *and* separate
`prev_fases` / `prev_act_nums` / `_snapshot` bookkeeping fields to compare
against on the next cycle (its `_emit_events` pseudocode even references
`state._snapshot` before it is ever assigned — a bug). We drop the separate
snapshot fields entirely: at the start of each cycle we take a shallow copy
of the *previous* `incidents` dict (`base_incidents`) and diff freshly
fetched rows against that copy while building the *new* `incidents` dict.
The new dict becomes next cycle's "previous" automatically. This is strictly
less state to keep in sync and cannot drift.

Error-handling semantics (docs/05-implementation-plan.md Task 5): on a fetch
failure we raise `UpdateFailed`. Home Assistant's `DataUpdateCoordinator`
only overwrites `self.data` *after* `_async_update_data` returns
successfully (see `_async_refresh` in
`homeassistant.helpers.update_coordinator`), so raising `UpdateFailed`
already guarantees the previous `BomberscatState` (and therefore all
existing entities) survives untouched — we do not need to special-case
"keep the old state" ourselves. We additionally mutate the *existing*
state's `last_error` field in place (when one exists) before raising, so
that diagnostics entities (Task 13) reading `coordinator.data.last_error`
see the new error message even though `coordinator.data` itself keeps its
old identity. `coordinator.last_update_success` (set by the base class to
`False` on `UpdateFailed`) is the canonical "is the service reachable"
signal for `binary_sensor.service_connected` (Task 13). On the very first
refresh there is no previous state to mutate, so we raise `UpdateFailed`
directly; `async_config_entry_first_refresh()` turns that into
`ConfigEntryNotReady`, which is exactly the behavior Task 5 asks for.

Event-suppression semantics (Task 9): no events are fired on the very first
successful refresh after startup/reload (`previous is None`), to avoid a
notification storm replaying every currently-active fire as "detected" on
every Home Assistant restart. In practice this only affects
`bomberscat_fire_detected`: `bomberscat_phase_change` and
`bomberscat_fire_resolved` can only fire for an incident that was *already*
tracked in a previous cycle, which is impossible on the first refresh by
construction (`base_incidents` is empty). We still suppress unconditionally
(rather than relying on that invariant) so the intent is explicit and the
code stays correct if the tracking logic ever changes.

Service-degradation semantics (Task 13, docs/04-architecture.md §9 "URL
canviada (404 persistent)"): `BomberscatState.consecutive_4xx_failures`
counts *consecutive* failures whose `ArcgisClientError.kind` is in
`_DEGRADATION_KINDS` (a 4xx response — the "schema/URL changed" signature).
It resets to 0 both on a successful refresh *and* on a failure of a
different kind (timeout/5xx/parse): a non-4xx failure in between two 404s
means the run was not actually a consecutive sequence of that specific
signature. Once the streak reaches `DEGRADED_FAILURE_THRESHOLD`, we fire
`bomberscat_service_degraded` and raise a repair issue exactly once
(`BomberscatState.degraded` gates re-firing every subsequent cycle); the
repair issue is only cleared on an actual successful refresh, regardless of
what interleaving failures reset the streak counter in between — the user
should keep seeing the issue until the service demonstrably recovers, not
just until the failure *type* changes. This bookkeeping only runs once
there is a `previous` state to mutate (same first-refresh caveat as
`last_error` above): a persistent-404 on the very first refresh instead
surfaces as a normal `ConfigEntryNotReady` setup retry.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util.dt import utcnow

from .arcgis import ArcgisClientError, fetch_incidents
from .const import (
    BOMBERS_VIEWER_URL,
    CONF_ACTIVE_PHASES,
    CONF_ALERT_RADIUS,
    CONF_MIN_VEHICLES,
    CONF_SCAN_INTERVAL,
    CONF_SUBTIPUS,
    CONF_TRACK_RADIUS,
    DEFAULT_ACTIVE_PHASES,
    DEFAULT_MIN_VEHICLES,
    DEFAULT_RESOLVED_GRACE_PERIOD_MIN,
    DEFAULT_SCAN_INTERVAL_MIN,
    DEFAULT_SUBTIPUS,
    DOMAIN,
    EVENT_FIRE_DETECTED,
    EVENT_FIRE_RESOLVED,
    EVENT_PHASE_CHANGE,
    EVENT_SERVICE_DEGRADED,
    GITHUB_ISSUES_URL,
)
from .geo import haversine_km
from .models import Fase, Incident

if TYPE_CHECKING:
    import aiohttp
    from homeassistant.core import HomeAssistant

    from . import BomberscatConfigEntry
    from .pla_alfa import PlaAlfaCoordinator

_LOGGER = logging.getLogger(__name__)

# `ArcgisClientError.kind`s that count as the "schema/URL changed" failure
# signature (docs/04-architecture.md §9, "URL canviada (404 persistent)").
# Timeout/5xx/parse failures do not count: they are transient-network or
# malformed-payload problems, not evidence the FeatureServer's address or
# schema has moved.
_DEGRADATION_KINDS = frozenset({"http_404", "http_4xx"})

# Number of *consecutive* `_DEGRADATION_KINDS` failures before we consider
# the service "degraded" (Task 13 / docs/05-implementation-plan.md Task 13).
DEGRADED_FAILURE_THRESHOLD = 3

# sensor.last_update_status (feature-spec §3.11) buckets: any
# `ArcgisClientError.kind` not listed here (including a generic 4xx that
# isn't specifically a 404, or a missing kind) normalizes to "unknown".
_ERROR_STATUS_LABELS: dict[str, str] = {
    "timeout": "timeout",
    "http_404": "http_404",
    "http_5xx": "http_5xx",
    "parse": "parse",
}


def last_update_status(state: BomberscatState) -> str:
    """`sensor.bomberscat_last_update_status` value (feature-spec §3.11).

    `"success"` when the last refresh cycle completed without error;
    otherwise `"error_<code>"`, derived from `state.last_error_kind` (itself
    copied from `ArcgisClientError.kind` — see arcgis.py) and normalized via
    `_ERROR_STATUS_LABELS` to the handful of buckets the resilience table
    actually distinguishes.
    """
    if state.last_error is None:
        return "success"
    label = _ERROR_STATUS_LABELS.get(state.last_error_kind or "", "unknown")
    return f"error_{label}"


@dataclass(frozen=True, slots=True)
class BomberscatRuntimeConfig:
    """Resolved tracking configuration for one config entry.

    Location + the two radii come from `entry.data` (Task 4's config flow
    step 1); the filters/polling-interval come from `entry.options` with
    fallbacks to the `const.py` defaults, since the options flow that sets
    them (Task 11) does not exist yet. Reading options with `.get(...,
    DEFAULT_*)` here means this coordinator works unmodified once Task 11
    lands.
    """

    home_lat: float
    home_lon: float
    track_radius_km: float
    alert_radius_km: float
    subtipus: frozenset[str]
    active_phases: frozenset[str]
    min_vehicles: int
    scan_interval_min: int

    @classmethod
    def from_entry(cls, entry: BomberscatConfigEntry) -> BomberscatRuntimeConfig:
        """Build a `BomberscatRuntimeConfig` from a config entry's data+options."""
        data = entry.data
        options = entry.options
        return cls(
            home_lat=data[CONF_LATITUDE],
            home_lon=data[CONF_LONGITUDE],
            track_radius_km=data[CONF_TRACK_RADIUS],
            alert_radius_km=data[CONF_ALERT_RADIUS],
            subtipus=frozenset(options.get(CONF_SUBTIPUS, DEFAULT_SUBTIPUS)),
            active_phases=frozenset(
                options.get(CONF_ACTIVE_PHASES, DEFAULT_ACTIVE_PHASES)
            ),
            min_vehicles=int(options.get(CONF_MIN_VEHICLES, DEFAULT_MIN_VEHICLES)),
            scan_interval_min=int(
                options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN)
            ),
        )


@dataclass(eq=False)
class BomberscatState:
    """Snapshot of tracked incidents + sync/error bookkeeping.

    `incidents` holds everything currently "shown": incidents that pass the
    tracking filters/radius, *plus* incidents that just turned `Extingit`
    and are sitting out their removal grace period (`resolved_at`) so that
    future `geo_location` entities (Task 7) don't flicker away the instant a
    fire is put out.

    Equality is overridden (`eq=False` + manual `__eq__`) rather than using
    the dataclass-generated one: `last_success` changes every successful
    cycle even when nothing else does, and comparing it would defeat
    `always_update=False` (docs/04-architecture.md §5) by making the
    coordinator think something changed on every single poll. We compare
    `incidents`, `last_error` and `last_error_kind` only — the fields that
    actually affect entity state (`consecutive_4xx_failures`/`degraded` are
    pure bookkeeping for the degradation event/repair-issue, read directly
    by `diagnostics.py` rather than any polled entity, so they are
    deliberately excluded here).

    `last_error_kind`/`consecutive_4xx_failures`/`degraded` (Task 13) track
    the "persistent 404" resilience case (docs/04-architecture.md §9): see
    `BomberscatDataUpdateCoordinator._async_update_data` for how they are
    updated, and `last_update_status()` above for how `last_error_kind`
    becomes `sensor.bomberscat_last_update_status`.
    """

    incidents: dict[str, Incident] = field(default_factory=dict)
    resolved_at: dict[str, datetime] = field(default_factory=dict)
    last_data_act: datetime | None = None
    last_success: datetime | None = None
    last_error: str | None = None
    last_error_kind: str | None = None
    consecutive_4xx_failures: int = 0
    degraded: bool = False

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, BomberscatState):
            return NotImplemented
        return (
            self.incidents == other.incidents
            and self.last_error == other.last_error
            and self.last_error_kind == other.last_error_kind
        )

    # Mutable dataclass with a custom __eq__: explicitly mark unhashable
    # (the default dataclass behavior for eq=True classes) rather than
    # silently inheriting object identity hashing, which would be
    # inconsistent with __eq__.
    __hash__ = None  # type: ignore[assignment]


def _passes_filters(inc: Incident, cfg: BomberscatRuntimeConfig) -> bool:
    """Subtipus + phase + min_vehicles filter (docs/04-architecture.md §5).

    `Extingit` is deliberately let through regardless of `active_phases` so
    that an incident already being tracked can be *observed* transitioning
    into `Extingit` (to fire `bomberscat_fire_resolved` / update
    `bomberscat_phase_change`). Whether a fresh, never-before-tracked
    `Extingit` incident should actually start being tracked is handled
    separately in `_should_track` (it should not: there is nothing to alert
    on, and no phase transition to observe).
    """
    if inc.tipus.value not in cfg.subtipus:
        return False
    if inc.vehicles < cfg.min_vehicles:
        return False
    return inc.fase.value in cfg.active_phases or inc.fase is Fase.EXTINGIT


def _should_track(
    inc: Incident,
    cfg: BomberscatRuntimeConfig,
    *,
    distance_km: float,
    was_tracked: bool,
) -> bool:
    """Whether `inc` belongs in `BomberscatState.incidents` this cycle."""
    if not _passes_filters(inc, cfg):
        return False
    if distance_km > cfg.track_radius_km:
        return False
    # A never-before-tracked incident that is already Extingit: nothing to
    # track or alert on (see _passes_filters docstring).
    return not (inc.fase is Fase.EXTINGIT and not was_tracked)


def _fire_detected_payload(
    inc: Incident, distance_km: float, cfg: BomberscatRuntimeConfig
) -> dict[str, Any]:
    """Payload for `bomberscat_fire_detected` (docs/03-feature-spec.md §4.1)."""
    return {
        "act_num": inc.act_num,
        "distance_km": round(distance_km, 1),
        "municipi": inc.municipi,
        "fase": inc.fase.value,
        "tipus": inc.tipus.value,
        "tipus_desc": inc.tipus_desc,
        "vehicles": inc.vehicles,
        "in_alert_radius": distance_km <= cfg.alert_radius_km,
        "latitude": inc.lat,
        "longitude": inc.lon,
        "url": BOMBERS_VIEWER_URL,
    }


def _phase_change_payload(
    inc: Incident, old_fase: Fase, distance_km: float
) -> dict[str, Any]:
    """Payload for `bomberscat_phase_change` (docs/03-feature-spec.md §4.3)."""
    return {
        "act_num": inc.act_num,
        "municipi": inc.municipi,
        "old_fase": old_fase.value,
        "new_fase": inc.fase.value,
        "distance_km": round(distance_km, 1),
    }


def _fire_resolved_payload(inc: Incident, now: datetime) -> dict[str, Any]:
    """Payload for `bomberscat_fire_resolved` (docs/03-feature-spec.md §4.2).

    `duration_min` is best-effort: `ACT_DAT_INICI` can be null on the source
    data (see docs/01-data-sources.md), in which case we report `None`
    rather than guessing.
    """
    duration_min = None
    if inc.inici is not None:
        duration_min = round((now - inc.inici).total_seconds() / 60)
    return {
        "act_num": inc.act_num,
        "municipi": inc.municipi,
        "duration_min": duration_min,
        "final_fase": inc.fase.value,
    }


def _apply_incident(
    inc: Incident,
    cfg: BomberscatRuntimeConfig,
    *,
    base_incidents: dict[str, Incident],
    incidents: dict[str, Incident],
    resolved_at: dict[str, datetime],
    distance: float,
    now: datetime,
) -> list[tuple[str, dict[str, Any]]]:
    """Fold one fetched incident into `incidents`/`resolved_at` in place.

    Returns the list of (event_type, payload) tuples this incident should
    raise this cycle (empty most of the time — most fetched rows are either
    not tracked at all, or tracked with no phase change).
    """
    was_tracked = inc.act_num in base_incidents
    old_fase = base_incidents[inc.act_num].fase if was_tracked else None
    should_track = _should_track(
        inc, cfg, distance_km=distance, was_tracked=was_tracked
    )

    if not should_track:
        if not was_tracked:
            return []
        incidents.pop(inc.act_num, None)
        resolved_at.pop(inc.act_num, None)
        return [(EVENT_FIRE_RESOLVED, _fire_resolved_payload(inc, now))]

    incidents[inc.act_num] = inc
    if inc.fase is not Fase.EXTINGIT:
        # Defensive: clears a stale grace-period timer in the (real-world
        # unlikely, since Extingit is normally terminal) case where a fire
        # sitting in its removal grace period gets reactivated to a
        # non-Extingit phase — it should keep being tracked indefinitely
        # again, not get swept away when the old timer runs out.
        resolved_at.pop(inc.act_num, None)
    if not was_tracked:
        return [(EVENT_FIRE_DETECTED, _fire_detected_payload(inc, distance, cfg))]
    if old_fase is None or old_fase == inc.fase:
        return []

    events: list[tuple[str, dict[str, Any]]] = [
        (EVENT_PHASE_CHANGE, _phase_change_payload(inc, old_fase, distance))
    ]
    if inc.fase is Fase.EXTINGIT:
        resolved_at[inc.act_num] = now
        events.append((EVENT_FIRE_RESOLVED, _fire_resolved_payload(inc, now)))
    return events


def _cleanup_resolved(
    incidents: dict[str, Incident],
    resolved_at: dict[str, datetime],
    grace_minutes: int,
    now: datetime,
) -> None:
    """Drop incidents whose removal grace period has elapsed, in place."""
    grace = timedelta(minutes=grace_minutes)
    expired = [
        act_num for act_num, resolved in resolved_at.items() if now - resolved >= grace
    ]
    for act_num in expired:
        incidents.pop(act_num, None)
        resolved_at.pop(act_num, None)


class BomberscatDataUpdateCoordinator(DataUpdateCoordinator[BomberscatState]):
    """Polls the Bombers FeatureServer and maintains `BomberscatState`."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: BomberscatConfigEntry,
        session: aiohttp.ClientSession,
        *,
        resolved_grace_minutes: int = DEFAULT_RESOLVED_GRACE_PERIOD_MIN,
    ) -> None:
        self.config = BomberscatRuntimeConfig.from_entry(entry)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(minutes=self.config.scan_interval_min),
            # BomberscatState.__eq__ ignores volatile timestamps, so this
            # only suppresses listener callbacks when nothing entities care
            # about actually changed (see BomberscatState's docstring).
            always_update=False,
        )
        self._session = session
        self._resolved_grace_minutes = resolved_grace_minutes
        # Companion Pla Alfa coordinator, attached by `async_setup_entry`
        # right after both coordinators are built. Declared here (QA-wave
        # fix) instead of being a dynamic attribute; TYPE_CHECKING-only
        # import avoids a circular import with pla_alfa.py.
        self.pla_alfa: PlaAlfaCoordinator | None = None
        # One repair issue per config entry (Task 13): stable across
        # reloads within the same entry so a second `async_create_issue`
        # call while already degraded just updates it in place rather than
        # duplicating.
        self._degraded_issue_id = f"service_degraded_{entry.entry_id}"

    def distance_km(self, inc: Incident) -> float:
        """Distance in km from home to `inc`.

        Deliberately uncached: although a given `act_num` usually keeps the
        same coordinates across snapshot rows, it is not guaranteed (a
        corrected/updated location on a later row) -- caching by `act_num`
        would then serve a stale distance in events/sensors while
        `geo_location` shows the new coordinates. `haversine_km` is cheap
        trig, so recomputing every time is the simplest correct option.
        """
        return haversine_km(
            self.config.home_lat, self.config.home_lon, inc.lat, inc.lon
        )

    async def _async_update_data(self) -> BomberscatState:
        previous = self.data
        is_first_refresh = previous is None
        cfg = self.config
        since = previous.last_data_act if previous else None

        try:
            fetched = await fetch_incidents(self._session, since=since)
        except ArcgisClientError as err:
            if previous is not None:
                # Mutate in place: self.data keeps this same object (HA does
                # not reassign `self.data` when `_async_update_data` raises),
                # so diagnostics entities reading coordinator.data.last_error
                # still see the fresh message.
                previous.last_error = str(err)
                previous.last_error_kind = err.kind
                if err.kind in _DEGRADATION_KINDS:
                    previous.consecutive_4xx_failures += 1
                    if (
                        not previous.degraded
                        and previous.consecutive_4xx_failures
                        >= DEGRADED_FAILURE_THRESHOLD
                    ):
                        previous.degraded = True
                        self._mark_service_degraded(
                            err, previous.consecutive_4xx_failures
                        )
                else:
                    # Not the "URL/schema changed" signature: a timeout or
                    # 5xx in between two 404s means the failures were not
                    # actually consecutive occurrences of *that* signature.
                    previous.consecutive_4xx_failures = 0
            raise UpdateFailed(str(err)) from err

        base_incidents = dict(previous.incidents) if previous else {}
        incidents = dict(base_incidents)
        resolved_at = dict(previous.resolved_at) if previous else {}
        now = utcnow()
        events: list[tuple[str, dict[str, Any]]] = []

        for inc in fetched:
            if not inc.act_num:
                continue
            events.extend(
                _apply_incident(
                    inc,
                    cfg,
                    base_incidents=base_incidents,
                    incidents=incidents,
                    resolved_at=resolved_at,
                    distance=self.distance_km(inc),
                    now=now,
                )
            )

        _cleanup_resolved(incidents, resolved_at, self._resolved_grace_minutes, now)

        last_data_act = previous.last_data_act if previous else None
        for inc in fetched:
            if inc.data_act is not None and (
                last_data_act is None or inc.data_act > last_data_act
            ):
                last_data_act = inc.data_act

        if not is_first_refresh:
            for event_type, payload in events:
                self.hass.bus.async_fire(event_type, payload)

        if previous is not None and previous.degraded:
            self._clear_service_degraded()

        return BomberscatState(
            incidents=incidents,
            resolved_at=resolved_at,
            last_data_act=last_data_act,
            last_success=now,
            last_error=None,
            last_error_kind=None,
            consecutive_4xx_failures=0,
            degraded=False,
        )

    def _mark_service_degraded(self, err: ArcgisClientError, count: int) -> None:
        """Fire `bomberscat_service_degraded` once + raise a repair issue.

        Only called the cycle `consecutive_4xx_failures` first reaches
        `DEGRADED_FAILURE_THRESHOLD` (the `not previous.degraded` guard at
        the call site keeps this to a single firing per degradation episode,
        per docs/05-implementation-plan.md Task 13: "once, not every
        cycle"). docs/04-architecture.md §9, "URL canviada (404 persistent)".
        """
        _LOGGER.warning(
            "Bombers FeatureServer degraded: %d consecutive schema/URL-change"
            " failures (%s)",
            count,
            err,
        )
        self.hass.bus.async_fire(
            EVENT_SERVICE_DEGRADED,
            {"consecutive_failures": count, "last_error": str(err)},
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self._degraded_issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="service_degraded",
            translation_placeholders={"error": str(err)},
            learn_more_url=GITHUB_ISSUES_URL,
        )

    def _clear_service_degraded(self) -> None:
        """Clear the repair issue on recovery (a successful refresh)."""
        ir.async_delete_issue(self.hass, DOMAIN, self._degraded_issue_id)
