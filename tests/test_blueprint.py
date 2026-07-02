"""Tests for `blueprints/automation/incendiscat_fire_notification.yaml` (Task 15).

Two layers of validation:

1. Structural: the blueprint declares exactly the inputs required by
   `docs/03-feature-spec.md` §5, with the selectors/defaults documented there.
2. Behavioural: the blueprint is loaded through Home Assistant's real
   blueprint machinery (`homeassistant.components.blueprint`), substituted
   with concrete inputs, installed as an actual `automation` entity, and
   exercised by firing `incendiscat_fire_detected` / `_resolved` /
   `_phase_change` events on `hass.bus` — then we assert on the resulting
   calls to the `notify_service` action, the same way a real installation
   would be verified manually (per the task's "Test manual amb events
   simulats" verification note).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml as pyyaml
from homeassistant.components.automation.config import (
    AUTOMATION_BLUEPRINT_SCHEMA,
    PLATFORM_SCHEMA,
)
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import yaml as yaml_util
from pytest_homeassistant_custom_component.common import async_mock_service

BLUEPRINT_PATH = str(
    Path(__file__).resolve().parent.parent
    / "blueprints"
    / "automation"
    / "incendiscat_fire_notification.yaml"
)

EXPECTED_INPUTS = {
    "notify_service",
    "minimum_fase",
    "minimum_vehicles",
    "maximum_distance",
    "critical_alert",
    "include_resolved",
    "include_phase_changes",
    "open_map_url",
}


# ---------------------------------------------------------------------------
# 1. Structural checks — a permissive-enough YAML parse to inspect `input:`
#    without needing a full HA runtime (fast, no `hass` fixture required).
# ---------------------------------------------------------------------------


def _load_raw() -> dict[str, Any]:
    """Parse the blueprint with a loader that tolerates the `!input` tag.

    `!input` is an HA-specific YAML tag; plain `yaml.safe_load` doesn't know
    it, so register a tolerant multi-constructor that turns any unknown tag
    into a plain string/marker instead of raising.
    """

    class _TolerantLoader(pyyaml.SafeLoader):
        pass

    def _construct_unknown(
        loader: pyyaml.SafeLoader, tag_suffix: str, node: Any
    ) -> Any:
        if isinstance(node, pyyaml.ScalarNode):
            return loader.construct_scalar(node)
        if isinstance(node, pyyaml.SequenceNode):
            return loader.construct_sequence(node)
        return loader.construct_mapping(node)

    _TolerantLoader.add_multi_constructor("!", _construct_unknown)
    with open(BLUEPRINT_PATH, encoding="utf-8") as handle:
        return pyyaml.load(handle, Loader=_TolerantLoader)


def test_blueprint_metadata() -> None:
    """The blueprint declares the domain/name/source_url required by T15."""
    raw = _load_raw()
    meta = raw["blueprint"]
    assert meta["domain"] == "automation"
    assert "Incendis Catalunya" in meta["name"]
    assert meta["source_url"].startswith(
        "https://github.com/pmontp19/ha-incendiscat/blob/main/"
    )
    assert "homeassistant" in meta
    assert "min_version" in meta["homeassistant"]


def test_blueprint_declares_all_required_inputs() -> None:
    """Every input from feature-spec §5 is present with the right selector."""
    raw = _load_raw()
    inputs = raw["blueprint"]["input"]
    assert set(inputs.keys()) >= EXPECTED_INPUTS

    assert inputs["notify_service"]["default"] == "notify.notify"
    assert "text" in inputs["notify_service"]["selector"]

    fase_options = inputs["minimum_fase"]["selector"]["select"]["options"]
    assert fase_options == ["Actiu", "Estabilitzat", "Controlat", "Extingit"]
    assert inputs["minimum_fase"]["default"] == "Actiu"

    assert inputs["minimum_vehicles"]["default"] == 0
    assert "number" in inputs["minimum_vehicles"]["selector"]

    assert inputs["maximum_distance"]["default"] == 0
    assert "number" in inputs["maximum_distance"]["selector"]

    assert inputs["critical_alert"]["default"] is False
    assert "boolean" in inputs["critical_alert"]["selector"]

    assert inputs["include_resolved"]["default"] is False
    assert inputs["include_phase_changes"]["default"] is False

    map_options = inputs["open_map_url"]["selector"]["select"]["options"]
    assert map_options == ["incendiscat", "google_maps", "osm"]
    assert inputs["open_map_url"]["default"] == "incendiscat"


def test_blueprint_triggers_on_all_three_events() -> None:
    """All 3 lifecycle events are wired as triggers (filtering happens in actions)."""
    raw = _load_raw()
    event_types = {t["event_type"] for t in raw["triggers"]}
    assert event_types == {
        "incendiscat_fire_detected",
        "incendiscat_fire_resolved",
        "incendiscat_phase_change",
    }


# ---------------------------------------------------------------------------
# 2. Full HA schema validation, via the real blueprint substitution pipeline.
# ---------------------------------------------------------------------------


def _substitute(user_inputs: dict[str, Any]) -> dict[str, Any]:
    data = yaml_util.load_yaml(BLUEPRINT_PATH)
    blueprint = Blueprint(
        data,
        path=BLUEPRINT_PATH,
        expected_domain="automation",
        schema=AUTOMATION_BLUEPRINT_SCHEMA,
    )
    inputs = BlueprintInputs(
        blueprint,
        {"use_blueprint": {"path": BLUEPRINT_PATH, "input": user_inputs}},
    )
    inputs.validate()
    return inputs.async_substitute()


@pytest.mark.parametrize(
    "user_inputs",
    [
        {"notify_service": "notify.notify"},
        {
            "notify_service": "notify.mobile_app_test",
            "minimum_fase": "Extingit",
            "minimum_vehicles": 3,
            "maximum_distance": 15,
            "critical_alert": True,
            "include_resolved": True,
            "include_phase_changes": True,
            "open_map_url": "google_maps",
        },
        {"notify_service": "notify.notify", "open_map_url": "osm"},
    ],
)
async def test_blueprint_produces_valid_automation_config(
    hass: HomeAssistant, user_inputs: dict[str, Any]
) -> None:
    """The blueprint, substituted with any valid inputs, is a valid automation."""
    config = _substitute(user_inputs)
    validated = PLATFORM_SCHEMA(config)
    assert validated["triggers"]
    assert validated["actions"]


# ---------------------------------------------------------------------------
# 3. Behavioural: install as a real automation, fire events, assert on the
#    resulting notify service calls.
# ---------------------------------------------------------------------------


async def _install_automation(
    hass: HomeAssistant, *, alias: str, user_inputs: dict[str, Any]
) -> list[dict[str, Any]]:
    """Install the blueprint as a live automation and return captured calls."""

    def _copy_blueprint() -> None:
        blueprint_dir = Path(
            hass.config.path("blueprints", "automation", "incendiscat")
        )
        blueprint_dir.mkdir(parents=True, exist_ok=True)
        dest = blueprint_dir / "notification.yaml"
        dest.write_text(
            Path(BLUEPRINT_PATH).read_text(encoding="utf-8"), encoding="utf-8"
        )

    await hass.async_add_executor_job(_copy_blueprint)

    domain, service = user_inputs.get("notify_service", "notify.notify").split(".", 1)
    calls = async_mock_service(hass, domain, service)

    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": [
                {
                    "alias": alias,
                    "use_blueprint": {
                        "path": "incendiscat/notification.yaml",
                        "input": user_inputs,
                    },
                }
            ]
        },
    )
    await hass.async_block_till_done()
    return calls


DETECTED_PAYLOAD = {
    "act_num": "2026-001",
    "distance_km": 12.3,
    "municipi": "Sant Quirze Safaja",
    "fase": "Actiu",
    "tipus": "VF",
    "tipus_desc": "Incendi vegetació forestal",
    "vehicles": 4,
    "in_alert_radius": True,
    "latitude": 41.7,
    "longitude": 2.2,
    "url": "https://experience.arcgis.com/experience/f6172fd2d6974bc0a8c51e3a6bc2a735",
}


async def test_fire_detected_notifies_with_default_inputs(hass: HomeAssistant) -> None:
    """Default inputs: an Actiu fire inside the alert radius triggers a notify call."""
    calls = await _install_automation(
        hass, alias="detected_default", user_inputs={"notify_service": "notify.notify"}
    )
    hass.bus.async_fire("incendiscat_fire_detected", DETECTED_PAYLOAD)
    await hass.async_block_till_done()

    assert len(calls) == 1
    data = calls[0].data
    assert "Sant Quirze Safaja" in data["title"]
    assert "12.3" in data["title"]
    assert "🔥" in data["title"]
    assert "Actiu" in data["message"]
    assert "4 vehicles" in data["message"]
    assert data["data"]["url"] == DETECTED_PAYLOAD["url"]


async def test_fire_detected_filtered_out_by_minimum_vehicles(
    hass: HomeAssistant,
) -> None:
    """A fire below `minimum_vehicles` must not trigger a notification."""
    calls = await _install_automation(
        hass,
        alias="detected_min_vehicles",
        user_inputs={"notify_service": "notify.notify", "minimum_vehicles": 10},
    )
    hass.bus.async_fire("incendiscat_fire_detected", DETECTED_PAYLOAD)
    await hass.async_block_till_done()
    assert calls == []


async def test_fire_detected_filtered_out_by_maximum_distance(
    hass: HomeAssistant,
) -> None:
    """`maximum_distance` > 0 overrides `in_alert_radius` and filters by km."""
    calls = await _install_automation(
        hass,
        alias="detected_max_distance",
        user_inputs={"notify_service": "notify.notify", "maximum_distance": 5},
    )
    hass.bus.async_fire("incendiscat_fire_detected", DETECTED_PAYLOAD)
    await hass.async_block_till_done()
    assert calls == []


async def test_fire_resolved_ignored_by_default(hass: HomeAssistant) -> None:
    """`include_resolved` defaults to false: resolved events produce no call."""
    calls = await _install_automation(
        hass, alias="resolved_default", user_inputs={"notify_service": "notify.notify"}
    )
    hass.bus.async_fire(
        "incendiscat_fire_resolved",
        {
            "act_num": "2026-001",
            "municipi": "Sant Quirze Safaja",
            "duration_min": 90,
            "final_fase": "Extingit",
        },
    )
    await hass.async_block_till_done()
    assert calls == []


async def test_fire_resolved_notifies_when_enabled(hass: HomeAssistant) -> None:
    """With `include_resolved: true`, resolved events produce a notify call."""
    calls = await _install_automation(
        hass,
        alias="resolved_enabled",
        user_inputs={"notify_service": "notify.notify", "include_resolved": True},
    )
    hass.bus.async_fire(
        "incendiscat_fire_resolved",
        {
            "act_num": "2026-001",
            "municipi": "Sant Quirze Safaja",
            "duration_min": 90,
            "final_fase": "Extingit",
        },
    )
    await hass.async_block_till_done()
    assert len(calls) == 1
    data = calls[0].data
    assert "Sant Quirze Safaja" in data["title"]
    assert "90 min" in data["message"]


async def test_phase_change_notifies_when_enabled(hass: HomeAssistant) -> None:
    """With `include_phase_changes: true`, phase-change events produce a notify call."""
    calls = await _install_automation(
        hass,
        alias="phase_change_enabled",
        user_inputs={
            "notify_service": "notify.notify",
            "include_phase_changes": True,
            # Controlat has lower severity than the default `minimum_fase`
            # (Actiu); relax it so this phase-change is not filtered out.
            "minimum_fase": "Extingit",
        },
    )
    hass.bus.async_fire(
        "incendiscat_phase_change",
        {
            "act_num": "2026-001",
            "municipi": "Sant Quirze Safaja",
            "old_fase": "Actiu",
            "new_fase": "Controlat",
            "distance_km": 8.0,
        },
    )
    await hass.async_block_till_done()
    assert len(calls) == 1
    data = calls[0].data
    assert "Actiu → Controlat" in data["title"]
    assert "8.0" in data["message"]


async def test_critical_alert_sets_push_payload(hass: HomeAssistant) -> None:
    """`critical_alert: true` adds the iOS/Android critical-notification payload."""
    calls = await _install_automation(
        hass,
        alias="critical_alert",
        user_inputs={"notify_service": "notify.notify", "critical_alert": True},
    )
    hass.bus.async_fire("incendiscat_fire_detected", DETECTED_PAYLOAD)
    await hass.async_block_till_done()

    assert len(calls) == 1
    push = calls[0].data["data"]["push"]
    assert push["interruption-level"] == "critical"
    assert calls[0].data["data"]["priority"] == "high"
