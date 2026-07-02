"""Structural check for translations (Task 14).

`strings.json` is the English source of truth for the config/options flow
and entity names; `translations/*.json` must mirror its exact key tree so
that every string looked up by Home Assistant in any of the three shipped
languages resolves to *something* (hassfest performs a similar check, but
this runs locally without pulling in the full HA test harness).

This intentionally only compares the *shape* (nested key sets), not the
values: translated strings are expected to differ from the English text,
that's the whole point.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "incendiscat"
TRANSLATIONS_DIR = COMPONENT_DIR / "translations"


def _key_tree(obj: Any) -> Any:
    """Recursively reduce a JSON value to its nested key-set shape.

    Dicts become `{key: _key_tree(value), ...}`; anything else (strings,
    including `[%key:...%]` placeholders) collapses to `None` since only
    the structure — not the translated text — is being compared.
    """
    if isinstance(obj, dict):
        return {key: _key_tree(value) for key, value in obj.items()}
    return None


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_strings_json_is_valid_json() -> None:
    _load(COMPONENT_DIR / "strings.json")


def test_all_translation_files_match_strings_json_shape() -> None:
    reference = _key_tree(_load(COMPONENT_DIR / "strings.json"))
    translation_files = sorted(TRANSLATIONS_DIR.glob("*.json"))

    assert translation_files, "expected at least one file in translations/"

    for path in translation_files:
        shape = _key_tree(_load(path))
        msg = f"{path.name} key structure diverges from strings.json"
        assert shape == reference, msg


def test_expected_languages_present() -> None:
    for lang in ("en", "ca", "es"):
        path = TRANSLATIONS_DIR / f"{lang}.json"
        assert path.is_file(), f"missing translations/{lang}.json"


def test_entity_translation_keys_cover_every_attr_translation_key_in_code() -> None:
    """Every `_attr_translation_key` literal used by sensor/binary_sensor
    entities must have a matching `entity.<platform>.<key>.name` entry."""
    strings = _load(COMPONENT_DIR / "strings.json")
    entity_section = strings["entity"]

    expected = {
        "sensor": {
            "active_fires",
            "nearest_fire_distance",
            "nearest_fire_municipi",
            "fires_per_fase",
            "fires_per_tipus",
            "total_vehicles",
            "fire_risk",
            "last_update",
            "last_update_status",
        },
        "binary_sensor": {
            "fire_nearby",
            "high_risk",
            "service_connected",
        },
    }

    for platform, keys in expected.items():
        assert platform in entity_section, f"missing entity.{platform} section"
        actual_keys = set(entity_section[platform])
        assert actual_keys == keys, (
            f"entity.{platform} keys {actual_keys} do not match "
            f"translation_key literals used in code {keys}"
        )
        for key in keys:
            assert "name" in entity_section[platform][key]
