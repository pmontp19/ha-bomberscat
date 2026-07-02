"""Constants for the Bombers de Catalunya (bomberscat) integration.

Endpoint URLs are taken from docs/01-data-sources.md §9 ("Endpoints públics
definitius"). Config defaults are taken from docs/03-feature-spec.md §2.
"""

DOMAIN = "bomberscat"

# ---------------------------------------------------------------------------
# ArcGIS FeatureServer endpoints (all served from the same AGO organization)
# ---------------------------------------------------------------------------

_ARCGIS_BASE = "https://services7.arcgis.com/ZCqVt1fRXwwK6GF4/arcgis/rest/services"

# Bombers (live wildfire incidents, points).
BOMBERS_LIVE_URL = (
    f"{_ARCGIS_BASE}/ACTUACIONS_URGENTS_online_PRO_AMB_FASE_VIEW/FeatureServer/0"
)

# Pla Alfa (Agents Rurals) — daily fire-risk level by municipality/comarca,
# polygons, for today and tomorrow.
PLA_ALFA_MUNI_AVUI_URL = (
    f"{_ARCGIS_BASE}/Pla_Alfa_Municipal_Avui_FL_2_view/FeatureServer/0"
)
PLA_ALFA_COM_AVUI_URL = f"{_ARCGIS_BASE}/Pla_Alfa_Comarcal_Avui_FL_VW/FeatureServer/1"
PLA_ALFA_MUNI_DEMA_URL = f"{_ARCGIS_BASE}/pla_alfa_municipal_dema_FL_VW/FeatureServer/5"
PLA_ALFA_COM_DEMA_URL = f"{_ARCGIS_BASE}/Pla_Alfa_Comarcal_Dema_FL_VW/FeatureServer/4"
PLA_ALFA_TANC_AVUI_URL = f"{_ARCGIS_BASE}/tancaments_pla_alfa_avui_VW/FeatureServer/2"
PLA_ALFA_TANC_DEMA_URL = f"{_ARCGIS_BASE}/tancaments_pla_alfa_dema_VW/FeatureServer/2"

# Dades obertes (Socrata) — validated historical wildfire data, no coordinates,
# used for optional historical/statistics sensors (out of scope for v1).
DO_HISTORIC_2011_2024_URL = (
    "https://analisi.transparenciacatalunya.cat/resource/bks7-dkfd.json"
)
DO_HISTORIC_ANTERIOR_URL = (
    "https://analisi.transparenciacatalunya.cat/resource/crs7-idxi.json"
)
DO_HISTORIC_ACTUAL_URL = (
    "https://analisi.transparenciacatalunya.cat/resource/9r29-e8ha.json"
)

# Official Bombers incident viewer (ArcGIS Experience Builder app). There is
# no documented per-incident deep link — the viewer is a single-page map app
# with no incident-id query parameter, so events/entities all point at the
# viewer root and let the user locate the fire on the map themselves
# (docs/03-feature-spec.md §4, §5).
BOMBERS_VIEWER_URL = (
    "https://experience.arcgis.com/experience/f6172fd2d6974bc0a8c51e3a6bc2a735"
)

# ---------------------------------------------------------------------------
# Events fired on hass.bus (docs/03-feature-spec.md §4)
# ---------------------------------------------------------------------------

EVENT_FIRE_DETECTED = "bomberscat_fire_detected"
EVENT_FIRE_RESOLVED = "bomberscat_fire_resolved"
EVENT_PHASE_CHANGE = "bomberscat_phase_change"

# Fired once (not every cycle) when the FeatureServer has failed with a
# schema/URL-change signature (persistent 4xx/404) `DEGRADED_FAILURE_THRESHOLD`
# times in a row — see coordinator.py and docs/04-architecture.md §9.
EVENT_SERVICE_DEGRADED = "bomberscat_service_degraded"

# Kept in sync manually with manifest.json's `issue_tracker`: used as the
# repair issue's `learn_more_url` (Task 13) so a persistently-degraded
# FeatureServer points the user at somewhere to report it.
GITHUB_ISSUES_URL = "https://github.com/pmontp19/ha-bomberscat/issues"

# ---------------------------------------------------------------------------
# Config flow / options keys
# ---------------------------------------------------------------------------

CONF_TRACK_RADIUS = "track_radius"
CONF_ALERT_RADIUS = "alert_radius"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_SUBTIPUS = "subtipus"
CONF_ACTIVE_PHASES = "active_phases"
CONF_MIN_VEHICLES = "min_vehicles"
CONF_HIGH_RISK_THRESHOLD = "high_risk_threshold"

# ---------------------------------------------------------------------------
# Config defaults (docs/03-feature-spec.md §2)
# ---------------------------------------------------------------------------

# Tracking radius (km): which fires are tracked (geo_location + counters).
DEFAULT_TRACK_RADIUS_KM = 100
MIN_TRACK_RADIUS_KM = 5
MAX_TRACK_RADIUS_KM = 200

# Alert radius (km): which fires trigger binary_sensor.fire_nearby / events.
DEFAULT_ALERT_RADIUS_KM = 30
MIN_ALERT_RADIUS_KM = 1

# Polling interval (minutes) for the Bombers live feed.
DEFAULT_SCAN_INTERVAL_MIN = 5
MIN_SCAN_INTERVAL_MIN = 1
MAX_SCAN_INTERVAL_MIN = 60

# Subtipus filter (TAL_COD_ALARMA2): VF (forestal) / VA (agrícola) / VU (urbana).
DEFAULT_SUBTIPUS = ["VF"]

# Phases (COM_FASE) considered "active" for tracking/counting purposes.
DEFAULT_ACTIVE_PHASES = ["Actiu", "Estabilitzat"]

# Minimum number of assigned vehicles (ACT_NUM_VEH) to consider an incident.
DEFAULT_MIN_VEHICLES = 0

# Pla Alfa risk level (PERIL_M, 0-4) threshold for binary_sensor.high_risk.
DEFAULT_HIGH_RISK_THRESHOLD = 3
MIN_HIGH_RISK_THRESHOLD = 0
MAX_HIGH_RISK_THRESHOLD = 4

# Grace period (minutes) before a resolved (Extingit) fire's geo_location
# entity is removed, to avoid flickering in the entity registry.
DEFAULT_RESOLVED_GRACE_PERIOD_MIN = 60

# Pla Alfa polling interval (Task 10). docs/03-feature-spec.md §3.8 suggests
# scheduled refreshes at 00:30/09:45 local (just after the official 00:00/
# 09:30 updates), which would need `async_track_time_change` instead of the
# coordinator's plain `update_interval`. We use a simple fixed interval
# instead (acceptable per docs/05-implementation-plan.md Task 10): Pla Alfa
# only changes once or twice a day, so polling every 6h means picking up
# each change within at most ~6h — good enough for a "don't light a fire
# today" sensor, and far simpler/more robust than time-of-day scheduling
# (no missed-wakeup edge cases across HA restarts).
PLA_ALFA_SCAN_INTERVAL_HOURS = 6
