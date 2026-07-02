"""mdi icon mappings for incendiscat entities (docs/04-architecture.md §7).

Kept as plain dicts (no HA imports) so they stay trivially testable and
reusable from any platform module (`sensor.py` today; `geo_location.py` /
`binary_sensor.py` may want the same mapping later).
"""

from __future__ import annotations

from .models import Fase, Tipus

# docs/04-architecture.md §7 "Icones segons fase".
FASE_ICONS: dict[Fase, str] = {
    Fase.ACTIU: "mdi:fire",
    Fase.ESTABILITZAT: "mdi:fire-alert",
    Fase.CONTROLAT: "mdi:fire-off",
    Fase.EXTINGIT: "mdi:fire-extinguisher",
}

# Not in the architecture doc's table, but useful for `fires_per_tipus`'s
# icon (mirrors the fase table's "show an icon for the dominant bucket"
# pattern) and for any future per-incident use (e.g. geo_location).
TIPUS_ICONS: dict[Tipus, str] = {
    Tipus.FORESTAL: "mdi:pine-tree-fire",
    Tipus.AGRICOLA: "mdi:tractor",
    Tipus.URBANA: "mdi:home-fire",
}

# Fallback when there is nothing tracked to derive a "dominant" icon from.
DEFAULT_FASE_ICON = "mdi:fire-off"
DEFAULT_TIPUS_ICON = "mdi:fire"
