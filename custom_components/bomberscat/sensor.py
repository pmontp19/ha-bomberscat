"""Sensor platform for bomberscat: aggregated wildfire sensors (Task 6+12),
plus 2 of the 3 diagnostic entities (Task 13: `last_update`,
`last_update_status`; the third, `service_connected`, is a binary_sensor).

Implements the 6 aggregated sensors of docs/03-feature-spec.md §3.2-§3.7:
`active_fires`, `nearest_fire_distance`, `nearest_fire_municipi`,
`fires_per_fase`, `fires_per_tipus`, `total_vehicles`. All are
`CoordinatorEntity` subclasses sharing one `DeviceInfo` per
docs/04-architecture.md §7 ("Bombers de Catalunya").

"Active" vs "tracked" (design note, since the spec's wording is ambiguous
about which incidents each sensor should count):

`BomberscatState.incidents` (see coordinator.py) holds two kinds of rows:
incidents whose phase is in the user's configured `active_phases`, *plus*
incidents that just turned `Extingit` and are sitting out their removal
grace period (kept around so `geo_location` entities don't flicker away
instantly). We call the first group "active" and the whole dict "tracked".

- `active_fires`'s state and `nearest_fire_*` only consider the *active*
  group: a grace-period `Extingit` fire is, definitionally, out — counting
  it as "active" or as the "nearest fire" would be misleading. This also
  matches feature-spec §3.2's own definition ("Comptador d'incendis actius
  (definits per `active_phases`)").
- `fires_per_fase`, `fires_per_tipus` and `total_vehicles` count everything
  *tracked* (the full `incidents` dict), since feature-spec §3.7 says "dels
  incidents *en seguiment*" (tracked) for `total_vehicles`, and
  `fires_per_fase`'s whole point is to show the phase breakdown, which is
  only informative if `Extingit` can show a non-zero count during its grace
  window (otherwise the `extingit` attribute would be permanently 0 and the
  sensor would carry no more information than `active_fires` split in two).

Sentinel note (`nearest_fire_distance` = `-1` when there is no active fire):
`-1` under `device_class=DISTANCE` is admittedly an odd choice for a numeric
`float` field over a nice `unit=km` scale (unlike `native_value=None`, which
HA would render as "unknown" and is the idiomatic no-data value for a
measurement). We implement `-1` as feature-spec §3.3 explicitly specifies it
byte-for-byte, but if this sensor's spec is revisited we'd recommend
`None`/unknown instead.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import BomberscatConfigEntry
from .coordinator import (
    BomberscatDataUpdateCoordinator,
    BomberscatState,
    last_update_status,
)
from .entity import device_info
from .icons import DEFAULT_FASE_ICON, DEFAULT_TIPUS_ICON, FASE_ICONS, TIPUS_ICONS
from .models import Fase, Incident, Tipus
from .pla_alfa import PlaAlfaCoordinator, PlaAlfaRisk

NO_MUNICIPI = "—"
FIRES_UNIT = "incendis"

# mdi icon per PERIL_M level (0-4), for FireRiskSensor. Not in icons.py: that
# module maps `Incident`-derived enums (`Fase`/`Tipus`), while this is keyed
# by a plain int level from a different data source (Pla Alfa, not Bombers).
_RISK_ICONS: dict[int, str] = {
    0: "mdi:shield-check",
    1: "mdi:shield-alert-outline",
    2: "mdi:shield-alert",
    3: "mdi:fire-alert",
    4: "mdi:fire",
}
_DEFAULT_RISK_ICON = "mdi:help-rhombus"


def _active_incidents(
    state: BomberscatState, active_phases: frozenset[str]
) -> list[Incident]:
    """Tracked incidents whose phase is in the user's `active_phases`.

    See the module docstring for why this excludes grace-period `Extingit`
    incidents even though they are still in `state.incidents`.
    """
    return [inc for inc in state.incidents.values() if inc.fase.value in active_phases]


def _nearest(
    incidents: list[Incident], coordinator: BomberscatDataUpdateCoordinator
) -> Incident | None:
    """The closest incident to home, or `None` if `incidents` is empty."""
    if not incidents:
        return None
    return min(incidents, key=coordinator.distance_km)


class BomberscatEntity(CoordinatorEntity[BomberscatDataUpdateCoordinator]):
    """Base for all bomberscat sensor entities: shared device + naming.

    Mirrors the `BomberscatEntity` sketch in docs/04-architecture.md §7.
    `device_info()` (shared with `binary_sensor.py` via `entity.py`) is the
    only piece factored out to a common module — the rest (unique_id
    scheme, translation_key wiring) differs enough per platform that a
    shared base class wasn't worth the coupling.
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: BomberscatDataUpdateCoordinator,
        entry: BomberscatConfigEntry,
        key: str,
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = device_info(entry)


class ActiveFiresSensor(BomberscatEntity, SensorEntity):
    """`sensor.bomberscat_active_fires` (docs/03-feature-spec.md §3.2)."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = FIRES_UNIT

    def __init__(
        self, coordinator: BomberscatDataUpdateCoordinator, entry: BomberscatConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "active_fires")

    @property
    def native_value(self) -> int:
        state: BomberscatState = self.coordinator.data
        return len(_active_incidents(state, self.coordinator.config.active_phases))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state: BomberscatState = self.coordinator.data
        cfg = self.coordinator.config
        active = _active_incidents(state, cfg.active_phases)
        in_alert_radius = sum(
            1
            for inc in active
            if self.coordinator.distance_km(inc) <= cfg.alert_radius_km
        )
        last_updated = state.last_success.isoformat() if state.last_success else None
        return {
            "last_updated": last_updated,
            "total_in_track_radius": len(state.incidents),
            "total_in_alert_radius": in_alert_radius,
        }


class NearestFireDistanceSensor(BomberscatEntity, SensorEntity):
    """`sensor.bomberscat_nearest_fire_distance` (feature-spec §3.3).

    See the module docstring for the `-1` sentinel design note.
    """

    _attr_device_class = SensorDeviceClass.DISTANCE
    _attr_native_unit_of_measurement = UnitOfLength.KILOMETERS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: BomberscatDataUpdateCoordinator, entry: BomberscatConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "nearest_fire_distance")

    @property
    def native_value(self) -> float:
        state: BomberscatState = self.coordinator.data
        active = _active_incidents(state, self.coordinator.config.active_phases)
        nearest = _nearest(active, self.coordinator)
        if nearest is None:
            return -1
        return round(self.coordinator.distance_km(nearest), 1)


class NearestFireMunicipiSensor(BomberscatEntity, SensorEntity):
    """`sensor.bomberscat_nearest_fire_municipi` (feature-spec §3.4)."""

    def __init__(
        self, coordinator: BomberscatDataUpdateCoordinator, entry: BomberscatConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "nearest_fire_municipi")

    @property
    def native_value(self) -> str:
        state: BomberscatState = self.coordinator.data
        active = _active_incidents(state, self.coordinator.config.active_phases)
        nearest = _nearest(active, self.coordinator)
        if nearest is None:
            return NO_MUNICIPI
        return nearest.municipi or NO_MUNICIPI


class FiresPerFaseSensor(BomberscatEntity, SensorEntity):
    """`sensor.bomberscat_fires_per_fase` (feature-spec §3.5).

    Counts *tracked* incidents (see module docstring), including
    grace-period `Extingit` ones, so the `extingit` attribute is
    meaningful.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = FIRES_UNIT

    def __init__(
        self, coordinator: BomberscatDataUpdateCoordinator, entry: BomberscatConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "fires_per_fase")

    def _counts(self) -> Counter[Fase]:
        state: BomberscatState = self.coordinator.data
        return Counter(inc.fase for inc in state.incidents.values())

    @property
    def native_value(self) -> int:
        return sum(self._counts().values())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        counts = self._counts()
        return {
            "actiu": counts[Fase.ACTIU],
            "estabilitzat": counts[Fase.ESTABILITZAT],
            "controlat": counts[Fase.CONTROLAT],
            "extingit": counts[Fase.EXTINGIT],
        }

    @property
    def icon(self) -> str:
        counts = self._counts()
        present = [fase for fase in Fase if counts[fase]]
        if not present:
            return DEFAULT_FASE_ICON
        # Most severe fase present drives the icon (Fase.severity: Actiu=3
        # .. Extingit=0), e.g. one Actiu + two Extingit shows the fire icon.
        dominant = max(present, key=lambda fase: fase.severity)
        return FASE_ICONS[dominant]


class FiresPerTipusSensor(BomberscatEntity, SensorEntity):
    """`sensor.bomberscat_fires_per_tipus` (feature-spec §3.6). Tracked, not
    just active — see `FiresPerFaseSensor`'s docstring."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = FIRES_UNIT

    def __init__(
        self, coordinator: BomberscatDataUpdateCoordinator, entry: BomberscatConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "fires_per_tipus")

    def _counts(self) -> Counter[Tipus]:
        state: BomberscatState = self.coordinator.data
        return Counter(inc.tipus for inc in state.incidents.values())

    @property
    def native_value(self) -> int:
        return sum(self._counts().values())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        counts = self._counts()
        return {
            "vf": counts[Tipus.FORESTAL],
            "va": counts[Tipus.AGRICOLA],
            "vu": counts[Tipus.URBANA],
        }

    @property
    def icon(self) -> str:
        counts = self._counts()
        if not any(counts.values()):
            return DEFAULT_TIPUS_ICON
        dominant, _ = counts.most_common(1)[0]
        return TIPUS_ICONS[dominant]


class TotalVehiclesSensor(BomberscatEntity, SensorEntity):
    """`sensor.bomberscat_total_vehicles` (feature-spec §3.7): Σ `ACT_NUM_VEH`
    over *tracked* incidents (see module docstring)."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: BomberscatDataUpdateCoordinator, entry: BomberscatConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "total_vehicles")

    @property
    def native_value(self) -> int:
        state: BomberscatState = self.coordinator.data
        return sum(inc.vehicles for inc in state.incidents.values())


class FireRiskSensor(CoordinatorEntity[PlaAlfaCoordinator], SensorEntity):
    """`sensor.bomberscat_fire_risk` (feature-spec §3.8, Task 10).

    Backed by `PlaAlfaCoordinator`, not `BomberscatDataUpdateCoordinator` —
    hence it does not subclass `BomberscatEntity` (typed for the latter) —
    but shares the same `DeviceInfo` (see `entity.device_info`) so it shows
    up under the same "Bombers de Catalunya" device.

    Availability follows `CoordinatorEntity`'s default (`coordinator
    .last_update_success`): when Pla Alfa is down (including a failed first
    refresh — see `__init__.py`'s `async_setup_entry`), this entity reports
    `unavailable` while the Bombers-backed sensors keep working normally
    (independent coordinators/independent failure domains, Task 10's
    acceptance criterion).
    """

    _attr_has_entity_name = True
    _attr_translation_key = "fire_risk"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: PlaAlfaCoordinator, entry: BomberscatConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_fire_risk"
        self._attr_device_info = device_info(entry)

    @property
    def native_value(self) -> int | None:
        risk: PlaAlfaRisk | None = self.coordinator.data
        return risk.peril_m if risk is not None else None

    @property
    def icon(self) -> str:
        risk: PlaAlfaRisk | None = self.coordinator.data
        if risk is None:
            return _DEFAULT_RISK_ICON
        return _RISK_ICONS.get(risk.peril_m, _DEFAULT_RISK_ICON)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        risk: PlaAlfaRisk | None = self.coordinator.data
        if risk is None:
            return None
        return {
            "nivell_text": risk.nivell_text,
            "comarca": risk.comarca,
            "municipi": risk.municipi,
            "data_vigencia": risk.data_vigencia,
            "hora_vigencia": risk.hora_vigencia,
            "perill_dema": risk.perill_dema,
        }


class LastUpdateSensor(BomberscatEntity, SensorEntity):
    """`sensor.bomberscat_last_update` (feature-spec §3.11, Task 13):
    timestamp of the last *successful* sync.

    `available` is overridden to always be `True` for the same reason as
    `ServiceConnectedBinarySensor` (binary_sensor.py): this diagnostic
    entity's job is to show the last known-good sync time even while the
    service is currently down, not to disappear at that exact moment.
    """

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: BomberscatDataUpdateCoordinator, entry: BomberscatConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "last_update")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> datetime | None:
        state: BomberscatState = self.coordinator.data
        return state.last_success


class LastUpdateStatusSensor(BomberscatEntity, SensorEntity):
    """`sensor.bomberscat_last_update_status` (feature-spec §3.11, Task 13):
    `"success"` or `"error_<code>"` — see `coordinator.last_update_status()`
    for the classification. `available` always `True`, same rationale as
    `LastUpdateSensor` above.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: BomberscatDataUpdateCoordinator, entry: BomberscatConfigEntry
    ) -> None:
        super().__init__(coordinator, entry, "last_update_status")

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self) -> str:
        state: BomberscatState = self.coordinator.data
        return last_update_status(state)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BomberscatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up bomberscat sensors from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            ActiveFiresSensor(coordinator, entry),
            NearestFireDistanceSensor(coordinator, entry),
            NearestFireMunicipiSensor(coordinator, entry),
            FiresPerFaseSensor(coordinator, entry),
            FiresPerTipusSensor(coordinator, entry),
            TotalVehiclesSensor(coordinator, entry),
            FireRiskSensor(coordinator.pla_alfa, entry),
            LastUpdateSensor(coordinator, entry),
            LastUpdateStatusSensor(coordinator, entry),
        ]
    )
