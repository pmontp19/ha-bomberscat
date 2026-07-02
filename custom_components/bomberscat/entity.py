"""Shared `DeviceInfo` helper for bomberscat entities (QA-wave fix).

`sensor.py` and `binary_sensor.py` entities all belong to the integration's
single "Bombers de Catalunya" service device (`geo_location` is the
exception — it models external per-incident events, not a sub-component of
the service device, per `geo_location.py`'s module docstring, so it has no
`DeviceInfo` at all). Before this module existed, the exact same `DeviceInfo`
literal was duplicated 4x: once as `sensor.py`'s `_device_info()` helper and
three more times inline in `binary_sensor.py`. Any drift between those
copies (e.g. a typo in one of the four `name=`/`model=` strings) would have
registered *two* separate devices for the same config entry instead of one.
Both platforms now import `device_info()` from here.
"""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo

from . import BomberscatConfigEntry
from .const import DOMAIN


def device_info(entry: BomberscatConfigEntry) -> DeviceInfo:
    """Shared `DeviceInfo` for every bomberscat entity (one device per entry)."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Bombers de Catalunya",
        manufacturer="Generalitat de Catalunya",
        model="Incendis forestals",
        entry_type=DeviceEntryType.SERVICE,
    )
