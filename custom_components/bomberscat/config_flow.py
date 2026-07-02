"""Config flow for the Bombers de Catalunya (bomberscat) integration.

Step 1 (Task 4): location + the two tracking radii (§2 of
docs/03-feature-spec.md). Step 2 (Task 11, this file): subtipus/fases/
polling/min_vehicles, stored as the entry's *options* (not data) at creation
time so the options flow (also Task 11) can edit them later without
duplicating the schema/defaults. `async_step_reconfigure` (Task 11) lets the
user move the tracked location without deleting the integration.

Single instance: this integration models "my home + the fires around it",
so a single config entry per Home Assistant installation is enough for v1.
There is no natural per-account unique id to key on (unlike cloud
integrations with an API key), and letting the user create N entries for N
different locations would need per-entry device/entity naming we don't have
yet. We declare ``"single_config_entry": true`` in ``manifest.json``, which
makes Home Assistant's flow manager abort a second ``user``-sourced flow
with reason ``single_instance_allowed`` before ``async_step_user`` even
runs (it does *not* affect ``async_step_reconfigure``, which is exempt from
that check — see ``ConfigEntriesFlowManager.async_init`` in HA core).
Moving the tracked location is handled by ``async_step_reconfigure``, not by
creating a second entry.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_LATITUDE,
    CONF_LOCATION,
    CONF_LONGITUDE,
    CONF_RADIUS,
    UnitOfLength,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.util.unit_conversion import DistanceConverter

from .const import (
    CONF_ACTIVE_PHASES,
    CONF_ALERT_RADIUS,
    CONF_HIGH_RISK_THRESHOLD,
    CONF_MIN_VEHICLES,
    CONF_SCAN_INTERVAL,
    CONF_SUBTIPUS,
    CONF_TRACK_RADIUS,
    DEFAULT_ACTIVE_PHASES,
    DEFAULT_ALERT_RADIUS_KM,
    DEFAULT_HIGH_RISK_THRESHOLD,
    DEFAULT_MIN_VEHICLES,
    DEFAULT_SCAN_INTERVAL_MIN,
    DEFAULT_SUBTIPUS,
    DEFAULT_TRACK_RADIUS_KM,
    DOMAIN,
    MAX_HIGH_RISK_THRESHOLD,
    MAX_SCAN_INTERVAL_MIN,
    MAX_TRACK_RADIUS_KM,
    MIN_ALERT_RADIUS_KM,
    MIN_HIGH_RISK_THRESHOLD,
    MIN_SCAN_INTERVAL_MIN,
    MIN_TRACK_RADIUS_KM,
)

ERROR_ALERT_GT_TRACK = "alert_gt_track"

# Selector options for the two multi-select fields (§2 feature-spec). Kept as
# plain strings (not translated) — they match the raw `TAL_COD_ALARMA2` /
# `COM_FASE` values used everywhere else (coordinator filters, geo_location
# attributes), with the user-facing label supplied by translations/*.json.
_SUBTIPUS_OPTIONS = ["VF", "VA", "VU"]
_ACTIVE_PHASES_OPTIONS = ["Actiu", "Estabilitzat", "Controlat", "Extingit"]


def _build_location_schema() -> vol.Schema:
    """Build the step-1 schema: map location (with radius) + alert radius."""
    return vol.Schema(
        {
            vol.Required(CONF_LOCATION): selector.LocationSelector(
                selector.LocationSelectorConfig(radius=True)
            ),
            vol.Optional(
                CONF_ALERT_RADIUS, default=DEFAULT_ALERT_RADIUS_KM
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=MIN_ALERT_RADIUS_KM,
                    max=MAX_TRACK_RADIUS_KM,
                    step=1,
                    unit_of_measurement="km",
                    mode=selector.NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _build_filters_schema(*, include_high_risk_threshold: bool) -> vol.Schema:
    """Build the filters schema shared by step 2 and the options flow.

    `include_high_risk_threshold` is False for the initial config-flow step 2
    (it is only exposed post-setup, via the options flow — §2 feature-spec)
    and True for the options flow.
    """
    schema: dict[Any, Any] = {
        vol.Optional(CONF_SUBTIPUS, default=DEFAULT_SUBTIPUS): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=_SUBTIPUS_OPTIONS,
                multiple=True,
                mode=selector.SelectSelectorMode.LIST,
                translation_key=CONF_SUBTIPUS,
            )
        ),
        vol.Optional(
            CONF_ACTIVE_PHASES, default=DEFAULT_ACTIVE_PHASES
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=_ACTIVE_PHASES_OPTIONS,
                multiple=True,
                mode=selector.SelectSelectorMode.LIST,
                translation_key=CONF_ACTIVE_PHASES,
            )
        ),
        vol.Optional(
            CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL_MIN
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=MIN_SCAN_INTERVAL_MIN,
                max=MAX_SCAN_INTERVAL_MIN,
                step=1,
                unit_of_measurement="min",
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(
            CONF_MIN_VEHICLES, default=DEFAULT_MIN_VEHICLES
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
            )
        ),
    }
    if include_high_risk_threshold:
        schema[
            vol.Optional(CONF_HIGH_RISK_THRESHOLD, default=DEFAULT_HIGH_RISK_THRESHOLD)
        ] = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=MIN_HIGH_RISK_THRESHOLD,
                max=MAX_HIGH_RISK_THRESHOLD,
                step=1,
                mode=selector.NumberSelectorMode.BOX,
            )
        )
    return vol.Schema(schema)


def _parse_location(
    user_input: dict[str, Any],
) -> tuple[float, float, float, float, dict[str, str]]:
    """Validate the location step's raw input.

    Returns ``(latitude, longitude, track_radius_km, alert_radius_km,
    errors)``. Shared by ``async_step_user`` and ``async_step_reconfigure``
    since both show/validate the same location+radii form.
    """
    errors: dict[str, str] = {}
    location: dict[str, float] = user_input[CONF_LOCATION]
    latitude = location[CONF_LATITUDE]
    longitude = location[CONF_LONGITUDE]

    # The LocationSelector's own vol.Optional("radius") means the frontend
    # can submit the form without a radius even though we asked for one
    # (home-assistant/core#108960) — default instead of KeyError.
    radius_m = location.get(CONF_RADIUS)
    if radius_m is None:
        track_radius_km = float(DEFAULT_TRACK_RADIUS_KM)
    else:
        track_radius_km = DistanceConverter.convert(
            radius_m, UnitOfLength.METERS, UnitOfLength.KILOMETERS
        )
    # Defensive clamp: the map's radius handle has no hard min/max, so keep
    # it inside the documented tracking range (5-200 km).
    track_radius_km = min(
        max(track_radius_km, MIN_TRACK_RADIUS_KM), MAX_TRACK_RADIUS_KM
    )

    alert_radius_km = float(user_input.get(CONF_ALERT_RADIUS, DEFAULT_ALERT_RADIUS_KM))

    if alert_radius_km > track_radius_km:
        errors[CONF_ALERT_RADIUS] = ERROR_ALERT_GT_TRACK

    return latitude, longitude, track_radius_km, alert_radius_km, errors


def _default_location_suggestions(hass: Any) -> dict[str, Any]:
    """Suggested values for the location form: `hass.config` + spec defaults."""
    return {
        CONF_LOCATION: {
            CONF_LATITUDE: hass.config.latitude,
            CONF_LONGITUDE: hass.config.longitude,
            CONF_RADIUS: DEFAULT_TRACK_RADIUS_KM * 1000,
        },
        CONF_ALERT_RADIUS: DEFAULT_ALERT_RADIUS_KM,
    }


class BomberscatConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Bombers de Catalunya."""

    VERSION = 1

    def __init__(self) -> None:
        """Stash step-1 results across steps (only used within one flow run)."""
        self._location_data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: location + the two tracking radii (§2 feature-spec).

        No manual single-instance guard here: ``manifest.json``'s
        ``single_config_entry: true`` makes the flow manager abort a second
        attempt with reason ``single_instance_allowed`` before this step is
        ever reached (see the module docstring).
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            latitude, longitude, track_radius_km, alert_radius_km, errors = (
                _parse_location(user_input)
            )
            if not errors:
                self._location_data = {
                    CONF_LATITUDE: latitude,
                    CONF_LONGITUDE: longitude,
                    CONF_TRACK_RADIUS: track_radius_km,
                    CONF_ALERT_RADIUS: alert_radius_km,
                }
                return await self.async_step_filters()

        # Re-show the form: on first display, pre-fill from hass.config +
        # defaults; on a validation error, keep whatever the user submitted
        # so the flow is recoverable without retyping everything.
        suggested_values = user_input or _default_location_suggestions(self.hass)
        data_schema = self.add_suggested_values_to_schema(
            _build_location_schema(), suggested_values
        )
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    async def async_step_filters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: subtipus/fases/polling/min_vehicles (§2 feature-spec).

        Stored as the entry's *options*, not data — the options flow reuses
        the same schema/defaults to edit these later without duplication.
        """
        if user_input is not None:
            return self.async_create_entry(
                title="Bombers de Catalunya",
                data=self._location_data,
                options=user_input,
            )

        data_schema = self.add_suggested_values_to_schema(
            _build_filters_schema(include_high_risk_threshold=False),
            {
                CONF_SUBTIPUS: DEFAULT_SUBTIPUS,
                CONF_ACTIVE_PHASES: DEFAULT_ACTIVE_PHASES,
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL_MIN,
                CONF_MIN_VEHICLES: DEFAULT_MIN_VEHICLES,
            },
        )
        return self.async_show_form(step_id="filters", data_schema=data_schema)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Move the tracked location/radii without deleting the integration.

        Only `entry.data` (location + radii) is handled here — options
        (filters/polling/threshold) are edited via the options flow, per
        docs/03-feature-spec.md §2 ("Reconfiguració").
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            latitude, longitude, track_radius_km, alert_radius_km, errors = (
                _parse_location(user_input)
            )
            if not errors:
                # Plain `async_update_entry` (not `async_update_reload_and_
                # abort`): the latter unconditionally schedules its own
                # reload on top of the one `_async_update_listener`
                # (`__init__.py`) already fires for *any* data/options
                # change, which meant every reconfigure ran two full
                # unload/setup cycles back to back. `async_update_entry`
                # fires the update listener exactly once when the data
                # actually changed, so a single reload does the job.
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={
                        CONF_LATITUDE: latitude,
                        CONF_LONGITUDE: longitude,
                        CONF_TRACK_RADIUS: track_radius_km,
                        CONF_ALERT_RADIUS: alert_radius_km,
                    },
                )
                return self.async_abort(reason="reconfigure_successful")

        suggested_values = user_input or {
            CONF_LOCATION: {
                CONF_LATITUDE: entry.data[CONF_LATITUDE],
                CONF_LONGITUDE: entry.data[CONF_LONGITUDE],
                CONF_RADIUS: entry.data[CONF_TRACK_RADIUS] * 1000,
            },
            CONF_ALERT_RADIUS: entry.data[CONF_ALERT_RADIUS],
        }
        data_schema = self.add_suggested_values_to_schema(
            _build_location_schema(), suggested_values
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=data_schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> BomberscatOptionsFlow:
        """Return the options flow for this integration."""
        return BomberscatOptionsFlow()


class BomberscatOptionsFlow(OptionsFlow):
    """Edit filters/polling/min_vehicles + the high-risk threshold.

    Modern pattern (docs/04-architecture.md §8): no ``__init__`` storing
    ``config_entry`` and no ``OptionsFlowWithConfigEntry`` — ``self.config_entry``
    is provided automatically by Home Assistant.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The only options step: all filters + the high-risk threshold."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        data_schema = self.add_suggested_values_to_schema(
            _build_filters_schema(include_high_risk_threshold=True),
            {
                CONF_SUBTIPUS: DEFAULT_SUBTIPUS,
                CONF_ACTIVE_PHASES: DEFAULT_ACTIVE_PHASES,
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL_MIN,
                CONF_MIN_VEHICLES: DEFAULT_MIN_VEHICLES,
                CONF_HIGH_RISK_THRESHOLD: DEFAULT_HIGH_RISK_THRESHOLD,
                **self.config_entry.options,
            },
        )
        return self.async_show_form(step_id="init", data_schema=data_schema)
