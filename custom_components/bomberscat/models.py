"""Domain models for bomberscat: `Incident`, `Fase`, `Tipus`.

No Home Assistant imports here on purpose: this module must be testable in
complete isolation (see docs/05-implementation-plan.md, Task 2).

Field names on the raw GeoJSON `properties` dict come from the Bombers
FeatureServer view (see docs/01-data-sources.md §2). `from_feature()` is
deliberately tolerant of missing/null fields: the FeatureServer is not an
official/versioned API and its schema can drift (docs/04-architecture.md §9).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

_LOGGER = logging.getLogger(__name__)


class Fase(str, Enum):  # noqa: UP042 -- str Enum per docs/04-architecture.md §4
    """Bombers operational phase (`COM_FASE`).

    `COM_FASE` can be `null` on the source data. The official Bombers webmap
    renders that as "Actiu" (its legend only has Actiu/Estabilitzat/
    Controlat/Extingit — there is no "unknown" bucket), so we mirror that
    behavior here rather than inventing a `SENSE_FASE` value.
    """

    ACTIU = "Actiu"
    ESTABILITZAT = "Estabilitzat"
    CONTROLAT = "Controlat"
    EXTINGIT = "Extingit"

    @property
    def severity(self) -> int:
        """Severity ranking, higher = more critical (3..0)."""
        return {
            Fase.ACTIU: 3,
            Fase.ESTABILITZAT: 2,
            Fase.CONTROLAT: 1,
            Fase.EXTINGIT: 0,
        }[self]


class Tipus(str, Enum):  # noqa: UP042 -- str Enum per docs/04-architecture.md §4
    """Wildfire subtype (`TAL_COD_ALARMA2`)."""

    FORESTAL = "VF"
    AGRICOLA = "VA"
    URBANA = "VU"


def _parse_fase(raw: Any) -> Fase:
    """Parse `COM_FASE`; null or unrecognized values map to `Fase.ACTIU`.

    The raw value is `.strip()`-ed before matching against `Fase`'s members:
    the live FeatureServer has been observed padding this field (e.g.
    `"Estabilitzat "`), which would otherwise silently misclassify as
    `Fase.ACTIU` and suppress `bomberscat_phase_change` events. Casing is
    left as-is (not casefolded) -- an unexpected-case value still falls back
    to `Fase.ACTIU` with a warning, same as any other unrecognized value.
    """
    if not raw:
        return Fase.ACTIU
    if isinstance(raw, str):
        raw = raw.strip()
    try:
        return Fase(raw)
    except ValueError:
        _LOGGER.warning("Unknown COM_FASE value %r, defaulting to Actiu", raw)
        return Fase.ACTIU


def _parse_tipus(raw: Any) -> Tipus:
    """Parse `TAL_COD_ALARMA2`; null or unrecognized values map to `Tipus.FORESTAL`.

    See `_parse_fase` for why the raw value is `.strip()`-ed first.
    """
    if not raw:
        return Tipus.FORESTAL
    if isinstance(raw, str):
        raw = raw.strip()
    try:
        return Tipus(raw)
    except ValueError:
        _LOGGER.warning("Unknown TAL_COD_ALARMA2 value %r, defaulting to VF", raw)
        return Tipus.FORESTAL


def _parse_timestamp(value: Any) -> datetime | None:
    """Parse an Esri epoch-ms timestamp into a UTC-aware `datetime`."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError):
        _LOGGER.warning("Could not parse timestamp %r", value)
        return None


def _parse_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True, slots=True)
class Incident:
    """A single wildfire incident, deduped to its current known state."""

    act_num: str
    lat: float
    lon: float
    fase: Fase
    tipus: Tipus
    tipus_desc: str
    municipi: str | None
    inici: datetime | None
    fi: datetime | None
    vehicles: int
    situacio: str | None
    edit_date: datetime | None
    creation_date: datetime | None
    # Raw ACT_DAT/DATA_ACT snapshot timestamp. Not in docs/04-architecture.md's
    # model sketch, but required by arcgis.py to dedup the append-only
    # snapshot log (docs/01-data-sources.md §2): keep the row with the
    # highest `data_act` per `act_num`.
    data_act: datetime | None = None

    @classmethod
    def from_feature(cls, feature: dict[str, Any]) -> Incident:
        """Build an `Incident` from a raw GeoJSON feature.

        Tolerant to missing `properties`/`geometry` and to null/absent
        individual fields: none of this should ever raise.
        """
        feature = feature or {}
        props: dict[str, Any] = feature.get("properties") or {}
        geometry: dict[str, Any] = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        lon = coordinates[0] if len(coordinates) > 0 else None
        lat = coordinates[1] if len(coordinates) > 1 else None

        parsed_lat = _parse_float(lat)
        parsed_lon = _parse_float(lon)
        if parsed_lat is None or parsed_lon is None:
            _LOGGER.warning(
                "Missing/unparseable coordinates for ACT_NUM_ACTUACIO %r"
                " (geometry=%r), defaulting to (0.0, 0.0)",
                props.get("ACT_NUM_ACTUACIO"),
                geometry,
            )

        return cls(
            act_num=str(props.get("ACT_NUM_ACTUACIO") or ""),
            lat=parsed_lat or 0.0,
            lon=parsed_lon or 0.0,
            fase=_parse_fase(props.get("COM_FASE")),
            tipus=_parse_tipus(props.get("TAL_COD_ALARMA2")),
            tipus_desc=props.get("TAL_DESC_ALARMA2") or "",
            municipi=props.get("MUNICIPI_SIG") or props.get("MUNICIPI_DPX"),
            inici=_parse_timestamp(props.get("ACT_DAT_INICI")),
            fi=_parse_timestamp(props.get("ACT_DAT_FI")),
            vehicles=_parse_int(props.get("ACT_NUM_VEH")),
            situacio=props.get("ACT_SITUACIO"),
            edit_date=_parse_timestamp(props.get("EditDate")),
            creation_date=_parse_timestamp(props.get("CreationDate")),
            data_act=_parse_timestamp(props.get("DATA_ACT")),
        )
