"""Geolocation platform for bomberscat (Task 7): one entity per tracked wildfire.

Implements `geo_location.bomberscat_<act_num>` (docs/03-feature-spec.md
§3.1): state = distance in km from `zone.home` to the incident, with the
full attribute set from the §3.1 table. Unlike the aggregate `sensor`/
`binary_sensor` platforms (one static entity created once at setup),
`geo_location` entities come and go dynamically as
`coordinator.data.incidents` gains/loses act_nums — an incident starts being
tracked (new fire, or an old one re-entering the radius/filters) or stops
(coordinator's `_cleanup_resolved()` drops it after the `Extingit` grace
period, or it moves out of the tracking radius/filters) on every poll.

Dynamic add/remove strategy: a single "manager" closure registered in
`async_setup_entry` diffs the act_nums it already has an entity for against
`coordinator.data.incidents.keys()` on every coordinator refresh
(`coordinator.async_add_listener`), creating entities for new act_nums and
removing entities for ones that vanished. Each entity is *also* a
`CoordinatorEntity` in its own right, so its state/attributes refresh in
place when its own incident is merely modified (e.g. a `fase` change) —
the manager only has to care about entities appearing/disappearing.

Registry-orphan strategy (docs/05-implementation-plan.md Task 7 acceptance
criterion: "sense entitats òrfenes al registre"): we set `unique_id` (as
directed by docs/04-architecture.md §7's `BomberscatFireLocation` sketch),
which means `async_add_entities` registers each entity in the entity
registry. Merely calling `Entity.async_remove(force_remove=True)` does
**not** delete that registry entry (Home Assistant's default behavior when
a config entry unloads is to keep disabled entities registered so they can
come back) — it would leave one permanently-disabled registry row behind
per extinguished fire forever. Core's feed-based `geo_location` platforms
that use real (non-config-entry-scoped) unique_ids solve this the same way
we do here: explicitly call `entity_registry.async_remove(self.entity_id)`
in `async_will_remove_from_hass()` *before* removing the entity, so no
registry row survives an incident's removal (see e.g. `gdacs`/
`geonetnz_quakes` in Home Assistant core, which do exactly this — as
opposed to `usgs_earthquakes_feed`/`nsw_rural_fire_service_feed`, which skip
the registry cleanup and rely on `unique_id`-less entities elsewhere in
their platform to avoid the issue; we cannot do that here since Task 7
requires a stable `unique_id`).
"""

from __future__ import annotations

import logging

from homeassistant.components.geo_location import GeolocationEvent
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BomberscatConfigEntry
from .const import BOMBERS_VIEWER_URL
from .coordinator import BomberscatDataUpdateCoordinator, BomberscatState
from .models import Incident

_LOGGER = logging.getLogger(__name__)

SOURCE = "bomberscat"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BomberscatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up bomberscat geo_location entities from a config entry.

    Creates one `BomberscatFireLocation` per act_num already in
    `coordinator.data.incidents` (populated by
    `async_config_entry_first_refresh()` before platforms are forwarded, see
    `__init__.py`), then keeps that set in sync with every later coordinator
    refresh for the lifetime of the entry (see module docstring).
    """
    coordinator: BomberscatDataUpdateCoordinator = entry.runtime_data
    known: dict[str, BomberscatFireLocation] = {}

    @callback
    def _sync_entities() -> None:
        current = set(coordinator.data.incidents)
        new_act_nums = current - known.keys()
        stale_act_nums = known.keys() - current

        if new_act_nums:
            new_entities = [
                BomberscatFireLocation(coordinator, act_num) for act_num in new_act_nums
            ]
            known.update((entity.act_num, entity) for entity in new_entities)
            async_add_entities(new_entities)

        for act_num in stale_act_nums:
            entity = known.pop(act_num)
            hass.async_create_task(entity.async_remove(force_remove=True))

    entry.async_on_unload(coordinator.async_add_listener(_sync_entities))
    _sync_entities()


class BomberscatFireLocation(CoordinatorEntity[BomberscatState], GeolocationEvent):
    """A single tracked wildfire, rendered as a `geo_location` entity.

    Not attached to the "Bombers de Catalunya" `DeviceInfo` (unlike the
    `sensor`/`binary_sensor` entities): each instance represents an external
    event (a specific fire), not a sub-component of the integration's
    service device, mirroring docs/04-architecture.md §7's sketch and how
    Home Assistant's own feed-based `geo_location` platforms (`gdacs`,
    `geonetnz_quakes`, ...) model their entries.
    """

    _attr_should_poll = False
    _attr_source = SOURCE
    _attr_icon = "mdi:fire"
    _attr_unit_of_measurement = UnitOfLength.KILOMETERS

    def __init__(
        self, coordinator: BomberscatDataUpdateCoordinator, act_num: str
    ) -> None:
        super().__init__(coordinator)
        self.act_num = act_num
        self._attr_unique_id = f"bomberscat_{act_num}"
        incident = coordinator.data.incidents[act_num]
        self._update_from_incident(incident)

    def _update_from_incident(self, incident: Incident) -> None:
        """Refresh name/position/state/attributes from a fresh `Incident`."""
        self._attr_name = f"Foc {incident.municipi or incident.act_num}"
        self._attr_latitude = incident.lat
        self._attr_longitude = incident.lon
        self._attr_distance = self.coordinator.distance_km(incident)
        self._attr_extra_state_attributes = {
            "source": SOURCE,
            "act_num": incident.act_num,
            "fase": incident.fase.value,
            "tipus": incident.tipus.value,
            "tipus_desc": incident.tipus_desc,
            "municipi": incident.municipi,
            "data_inici": incident.inici.isoformat() if incident.inici else None,
            "data_fi": incident.fi.isoformat() if incident.fi else None,
            "vehicles": incident.vehicles,
            "situacio": incident.situacio,
            # docs/03-feature-spec.md §3.1 and docs/04-architecture.md §7's
            # sketch both specify `EditDate` (not the internal `DATA_ACT`
            # snapshot cursor `coordinator.py` uses for incremental sync) as
            # the source field for `updated_at`.
            "updated_at": (
                incident.edit_date.isoformat() if incident.edit_date else None
            ),
            "url": BOMBERS_VIEWER_URL,
        }

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh in place when this incident is modified (e.g. fase change).

        The manager in `async_setup_entry` handles this act_num disappearing
        entirely; if it is still present, just re-render from the latest
        `Incident`.
        """
        incident = self.coordinator.data.incidents.get(self.act_num)
        if incident is not None:
            self._update_from_incident(incident)
        super()._handle_coordinator_update()

    async def async_will_remove_from_hass(self) -> None:
        """Remove this entity's entry from the entity registry, if any.

        See the module docstring's "Registry-orphan strategy" section: this
        is what makes `async_remove(force_remove=True)` in the manager
        actually leave no trace, instead of an orphaned disabled registry
        row per resolved fire.
        """
        await super().async_will_remove_from_hass()
        registry = er.async_get(self.hass)
        if self.entity_id in registry.entities:
            registry.async_remove(self.entity_id)
