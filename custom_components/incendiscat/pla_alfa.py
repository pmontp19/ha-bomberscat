"""Async client for the Pla Alfa (Agents Rurals) fire-risk ArcGIS services.

Implements the "lookup del `CODIMUNI` de `zone.home`" strategy from
docs/01-data-sources.md §3 ("Estratègia d'ús"): rather than downloading the
whole municipal/comarcal polygon layers and doing the point-in-polygon test
client-side, we push the test to the server with a single ArcGIS
`geometry=lon,lat&geometryType=esriGeometryPoint&spatialRel=
esriSpatialRelIntersects` query per FeatureServer (`returnGeometry=false`
since we only need the attributes) — confirmed working against the live
service (see the query in `fetch_risk`'s docstring).

Three independent queries make up one `PlaAlfaRisk`:

1. `Pla_Alfa_Municipal_Avui_FL_2_view` -> `PERIL_M` (0-4), `NOMMUNI`,
   `NOMCOMAR` for today. This is the "primary" query: if it returns no
   feature (point outside any municipality polygon, e.g. `zone.home` sits
   over the sea or outside Catalonia), `fetch_risk` raises
   `ArcgisClientError` — there is nothing meaningful to report.
2. `pla_alfa_municipal_dema_FL_VW` -> `PERIL_M` for tomorrow
   (`perill_dema`).
3. `Pla_Alfa_Comarcal_Avui_FL_VW` -> `DATA`/`HORA` vigencia for the
   comarca's map.

Queries 2 and 3 are best-effort: if either comes back empty we still return
a `PlaAlfaRisk` with `perill_dema`/`data_vigencia`/`hora_vigencia` set to
`None` rather than failing the whole fetch, since the municipal "avui" level
(query 1) is the one piece of data the two entities in sensor.py/
binary_sensor.py actually need to compute their state.

Retry/backoff: we reuse `arcgis.py`'s `ArcgisClientError` and backoff
schedule (`RETRY_BACKOFFS_SECONDS`/`MAX_ATTEMPTS`) rather than duplicating
those *values*, but `arcgis.py`'s own retry loop (`_fetch_page`) is private
and hard-wired to the Bombers paginated-query shape (`resultOffset`,
`exceededTransferLimit`, GeoJSON), so it is not reusable as-is for a
single-shot point query against a different set of FeatureServers. We
therefore have our own small retry loop (`_fetch_query`) here, built on the
imported constants/exception so both clients agree on what counts as a
retryable failure and how long to wait.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.const import CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .arcgis import (
    MAX_ATTEMPTS,
    REQUEST_TIMEOUT,
    RETRY_BACKOFFS_SECONDS,
    ArcgisClientError,
)
from .const import (
    DOMAIN,
    PLA_ALFA_COM_AVUI_URL,
    PLA_ALFA_MUNI_AVUI_URL,
    PLA_ALFA_MUNI_DEMA_URL,
    PLA_ALFA_SCAN_INTERVAL_HOURS,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import IncendiscatConfigEntry

_LOGGER = logging.getLogger(__name__)

_SleepFn = Callable[[float], Awaitable[None]]

# docs/01-data-sources.md §3 "Escala PERIL_M / PERILL" + feature-spec §3.8.
# Wording matches Interior's own "Nivells del Pla Alfa" legend
# (interior.gencat.cat/.../pla-alfa/index.html), not the color-derived guess
# this used to have -- there is no "no risk" level 0 in the real scale.
NIVELL_TEXTS: dict[int, str] = {
    0: "Baix",
    1: "Moderat",
    2: "Alt",
    3: "Molt alt",
    4: "Extrem",
}
_UNKNOWN_NIVELL_TEXT = "Desconegut"


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _nivell_text(level: int) -> str:
    """`nivell_text` for a `PERIL_M`/`PERILL` value.

    The live service has been observed returning values outside the
    documented 0-4 range on the comarcal layer — fall back to
    `_UNKNOWN_NIVELL_TEXT` rather than raising, since this is a display
    label, not something we branch on.
    """
    return NIVELL_TEXTS.get(level, _UNKNOWN_NIVELL_TEXT)


def _parse_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_epoch_ms_date(value: Any) -> str | None:
    """Parse an Esri epoch-ms `DATA` field into an ISO `YYYY-MM-DD` string."""
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(int(value) / 1000, tz=UTC).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        _LOGGER.warning("Could not parse Pla Alfa DATA value %r", value)
        return None


@dataclass(frozen=True, slots=True)
class PlaAlfaRisk:
    """Resolved Pla Alfa fire-risk for one point (feature-spec §3.8)."""

    peril_m: int
    nivell_text: str
    municipi: str | None
    comarca: str | None
    perill_dema: int | None
    data_vigencia: str | None
    hora_vigencia: str | None


def _point_query_params(lat: float, lon: float, out_fields: str) -> dict[str, Any]:
    return {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "spatialRel": "esriSpatialRelIntersects",
        "inSR": 4326,
        "outFields": out_fields,
        "returnGeometry": "false",
        "f": "json",
    }


async def _fetch_query(
    session: aiohttp.ClientSession,
    feature_server_url: str,
    params: dict[str, Any],
    *,
    sleep: _SleepFn,
) -> dict[str, Any]:
    """Run one ArcGIS `/query` request, retrying timeout/5xx like arcgis.py."""
    query_url = f"{feature_server_url}/query"
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            async with session.get(
                query_url, params=params, timeout=REQUEST_TIMEOUT
            ) as resp:
                if 400 <= resp.status < 500:
                    body = await resp.text()
                    raise ArcgisClientError(
                        f"Pla Alfa FeatureServer client error {resp.status}: {body}"
                    )
                if resp.status >= 500:
                    body = await resp.text()
                    raise aiohttp.ClientResponseError(
                        resp.request_info,
                        resp.history,
                        status=resp.status,
                        message=body,
                    )
                return await resp.json(content_type=None)
        except ArcgisClientError:
            raise
        except (TimeoutError, aiohttp.ClientError) as err:
            last_error = err
            if attempt < len(RETRY_BACKOFFS_SECONDS):
                _LOGGER.warning(
                    "Pla Alfa FeatureServer request failed (attempt %d/%d): %s",
                    attempt + 1,
                    MAX_ATTEMPTS,
                    err,
                )
                await sleep(RETRY_BACKOFFS_SECONDS[attempt])
                continue

    raise ArcgisClientError(
        f"Pla Alfa FeatureServer unreachable after {MAX_ATTEMPTS} attempts: "
        f"{last_error}"
    ) from last_error


async def _query_first_attributes(
    session: aiohttp.ClientSession,
    feature_server_url: str,
    lat: float,
    lon: float,
    out_fields: str,
    *,
    sleep: _SleepFn,
) -> dict[str, Any] | None:
    """The `attributes` dict of the first feature intersecting the point.

    `None` if no polygon in the layer contains the point (e.g. the point is
    outside Catalonia, or the layer for tomorrow is temporarily empty before
    the daily 00:00 refresh).
    """
    params = _point_query_params(lat, lon, out_fields)
    data = await _fetch_query(session, feature_server_url, params, sleep=sleep)
    features = data.get("features") or []
    if not features:
        return None
    return features[0].get("attributes") or {}


async def fetch_risk(
    session: aiohttp.ClientSession,
    lat: float,
    lon: float,
    *,
    sleep: _SleepFn = _default_sleep,
) -> PlaAlfaRisk:
    """Fetch today's (+tomorrow's) Pla Alfa fire-risk level for `(lat, lon)`.

    Live query verified manually, e.g.:

        GET Pla_Alfa_Municipal_Avui_FL_2_view/FeatureServer/0/query
            ?geometry=2.1734,41.3851&geometryType=esriGeometryPoint
            &spatialRel=esriSpatialRelIntersects&inSR=4326
            &outFields=CODIMUNI,NOMMUNI,NOMCOMAR,PERIL_M
            &returnGeometry=false&f=json

    Raises `ArcgisClientError` if the municipal "avui" query fails or the
    point does not intersect any municipality (see module docstring for why
    the "demà"/comarcal queries are best-effort instead).
    """
    muni_avui = await _query_first_attributes(
        session,
        PLA_ALFA_MUNI_AVUI_URL,
        lat,
        lon,
        "CODIMUNI,NOMMUNI,NOMCOMAR,PERIL_M",
        sleep=sleep,
    )
    if muni_avui is None:
        # Deliberately generic: this message is surfaced via UpdateFailed and
        # logged at ERROR by DataUpdateCoordinator, which would otherwise put
        # the user's precise home coordinates (PII) into home-assistant.log.
        _LOGGER.debug("Pla Alfa: no municipality polygon intersects (%s, %s)", lat, lon)
        raise ArcgisClientError(
            "Pla Alfa: home location is outside any Pla Alfa municipality polygon"
        )

    raw_peril_m = muni_avui.get("PERIL_M")
    peril_m = _parse_int(raw_peril_m)
    if peril_m is None:
        _LOGGER.warning(
            "Pla Alfa: municipal PERIL_M missing/unparseable (%r), defaulting to 0",
            raw_peril_m,
        )
        peril_m = 0
    municipi = muni_avui.get("NOMMUNI")
    comarca = muni_avui.get("NOMCOMAR")

    perill_dema: int | None = None
    try:
        dema = await _query_first_attributes(
            session, PLA_ALFA_MUNI_DEMA_URL, lat, lon, "PERIL_M", sleep=sleep
        )
        if dema is not None:
            parsed_dema = _parse_int(dema.get("PERIL_M"))
            # Tomorrow's forecast is only published around 14:30h; before that
            # the layer returns a `5` sentinel (out of the documented 0-4
            # scale) for every municipality, and the official viewer leaves
            # those unpainted with "s'actualitzarà a les 14:30h". Treat any
            # out-of-range value as "not published yet" rather than surfacing a
            # bogus level-5 attribute.
            if parsed_dema is not None and parsed_dema in NIVELL_TEXTS:
                perill_dema = parsed_dema
    except ArcgisClientError as err:
        _LOGGER.warning("Pla Alfa: could not fetch tomorrow's risk level: %s", err)

    data_vigencia: str | None = None
    hora_vigencia: str | None = None
    try:
        comarcal = await _query_first_attributes(
            session, PLA_ALFA_COM_AVUI_URL, lat, lon, "PERILL,DATA,HORA", sleep=sleep
        )
        if comarcal is not None:
            data_vigencia = _parse_epoch_ms_date(comarcal.get("DATA"))
            hora_vigencia = comarcal.get("HORA")
    except ArcgisClientError as err:
        _LOGGER.warning("Pla Alfa: could not fetch vigencia date/time: %s", err)

    return PlaAlfaRisk(
        peril_m=peril_m,
        nivell_text=_nivell_text(peril_m),
        municipi=municipi,
        comarca=comarca,
        perill_dema=perill_dema,
        data_vigencia=data_vigencia,
        hora_vigencia=hora_vigencia,
    )


class PlaAlfaCoordinator(DataUpdateCoordinator[PlaAlfaRisk]):
    """Polls Pla Alfa for `zone.home`'s fire-risk level.

    Deliberately independent from `IncendiscatDataUpdateCoordinator`
    (acceptance criterion: "Pla Alfa caigut no afecta el polling
    d'incendis"): separate coordinator instance,
    separate `update_interval` (see `PLA_ALFA_SCAN_INTERVAL_HOURS`), and a
    failure here only ever raises `UpdateFailed`/`ConfigEntryNotReady` for
    *this* coordinator — it never touches the Bombers coordinator's state or
    prevents its polling. `__init__.py` additionally makes sure a failed
    first refresh of *this* coordinator does not abort the whole config
    entry setup, since fire monitoring (not fire-risk) is the integration's
    core value.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: IncendiscatConfigEntry,
        session: aiohttp.ClientSession,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.entry_id}_pla_alfa",
            update_interval=timedelta(hours=PLA_ALFA_SCAN_INTERVAL_HOURS),
        )
        self._session = session
        self._lat: float = entry.data[CONF_LATITUDE]
        self._lon: float = entry.data[CONF_LONGITUDE]

    async def _async_update_data(self) -> PlaAlfaRisk:
        try:
            return await fetch_risk(self._session, self._lat, self._lon)
        except ArcgisClientError as err:
            raise UpdateFailed(str(err)) from err
