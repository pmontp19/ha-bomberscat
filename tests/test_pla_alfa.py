"""Tests for pla_alfa.py: point-in-polygon query params, parsing, retries."""

from __future__ import annotations

import re

import aiohttp
import pytest
from aioresponses import CallbackResult, aioresponses
from custom_components.incendiscat import arcgis
from custom_components.incendiscat.arcgis import (
    MAX_ATTEMPTS,
    REQUEST_TIMEOUT,
    RETRY_BACKOFFS_SECONDS,
)
from custom_components.incendiscat.const import (
    PLA_ALFA_COM_AVUI_URL,
    PLA_ALFA_MUNI_AVUI_URL,
    PLA_ALFA_MUNI_DEMA_URL,
)
from custom_components.incendiscat.pla_alfa import (
    ArcgisClientError,
    PlaAlfaRisk,
    _nivell_text,
    fetch_risk,
)

LAT = 41.3851
LON = 2.1734

MUNI_URL_PATTERN = re.compile(re.escape(f"{PLA_ALFA_MUNI_AVUI_URL}/query") + r".*")
DEMA_URL_PATTERN = re.compile(re.escape(f"{PLA_ALFA_MUNI_DEMA_URL}/query") + r".*")
COM_URL_PATTERN = re.compile(re.escape(f"{PLA_ALFA_COM_AVUI_URL}/query") + r".*")


async def _noop_sleep(_seconds: float) -> None:
    return None


def _muni_payload(
    peril_m: int = 0, nommuni: str = "Barcelona", nomcomar: str = "Barcelonès"
):
    return {
        "features": [
            {
                "attributes": {
                    "CODIMUNI": "080193",
                    "NOMMUNI": nommuni,
                    "NOMCOMAR": nomcomar,
                    "PERIL_M": peril_m,
                }
            }
        ]
    }


def _dema_payload(peril_m: int = 1):
    return {"features": [{"attributes": {"PERIL_M": peril_m}}]}


def _comarcal_payload(perill: int = 0, data: int = 1782982736798, hora: str = "9:30"):
    return {
        "features": [{"attributes": {"PERILL": perill, "DATA": data, "HORA": hora}}]
    }


def _empty_payload():
    return {"features": []}


# ---------------------------------------------------------------------------
# `ArcgisClientError` is the same exception type as arcgis.py's (imported,
# not redefined) -- a sanity check that our "reuse, don't copy" claim holds.
# ---------------------------------------------------------------------------


def test_pla_alfa_reuses_arcgis_client_error_type() -> None:
    assert ArcgisClientError is arcgis.ArcgisClientError


# ---------------------------------------------------------------------------
# Level texts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("level", "text"),
    [
        (0, "Baix"),
        (1, "Moderat"),
        (2, "Alt"),
        (3, "Molt alt"),
        (4, "Extrem"),
    ],
)
def test_nivell_text_known_levels(level: int, text: str) -> None:
    assert _nivell_text(level) == text


def test_nivell_text_unknown_level_falls_back() -> None:
    # The live comarcal service has been observed returning values outside
    # the documented 0-4 range (see pla_alfa.py's module docstring).
    assert _nivell_text(5) == "Desconegut"


# ---------------------------------------------------------------------------
# Query params (point-in-polygon)
# ---------------------------------------------------------------------------


async def test_fetch_risk_sends_point_in_polygon_query() -> None:
    captured: dict = {}

    def muni_callback(url, **kwargs):
        captured["geometry"] = url.query.get("geometry")
        captured["geometryType"] = url.query.get("geometryType")
        captured["spatialRel"] = url.query.get("spatialRel")
        captured["inSR"] = url.query.get("inSR")
        captured["outFields"] = url.query.get("outFields")
        captured["returnGeometry"] = url.query.get("returnGeometry")
        captured["f"] = url.query.get("f")
        return CallbackResult(status=200, payload=_muni_payload())

    with aioresponses() as mocked:
        mocked.get(MUNI_URL_PATTERN, callback=muni_callback)
        mocked.get(DEMA_URL_PATTERN, payload=_dema_payload())
        mocked.get(COM_URL_PATTERN, payload=_comarcal_payload())
        async with aiohttp.ClientSession() as session:
            await fetch_risk(session, LAT, LON, sleep=_noop_sleep)

    assert captured["geometry"] == f"{LON},{LAT}"
    assert captured["geometryType"] == "esriGeometryPoint"
    assert captured["spatialRel"] == "esriSpatialRelIntersects"
    assert captured["inSR"] == "4326"
    assert captured["outFields"] == "CODIMUNI,NOMMUNI,NOMCOMAR,PERIL_M"
    assert captured["returnGeometry"] == "false"
    assert captured["f"] == "json"


# ---------------------------------------------------------------------------
# Parsing / assembly
# ---------------------------------------------------------------------------


async def test_fetch_risk_assembles_full_result() -> None:
    with aioresponses() as mocked:
        mocked.get(MUNI_URL_PATTERN, payload=_muni_payload(peril_m=3))
        mocked.get(DEMA_URL_PATTERN, payload=_dema_payload(peril_m=4))
        mocked.get(
            COM_URL_PATTERN,
            payload=_comarcal_payload(perill=3, data=1782982736798, hora="9:30"),
        )
        async with aiohttp.ClientSession() as session:
            risk = await fetch_risk(session, LAT, LON, sleep=_noop_sleep)

    assert risk == PlaAlfaRisk(
        peril_m=3,
        nivell_text="Molt alt",
        municipi="Barcelona",
        comarca="Barcelonès",
        perill_dema=4,
        data_vigencia="2026-07-02",
        hora_vigencia="9:30",
    )


async def test_fetch_risk_dema_and_comarcal_best_effort_when_empty() -> None:
    """No feature in the demà/comarcal layers -> those fields are None, but
    the fetch still succeeds since the municipal "avui" query is all that's
    required."""
    with aioresponses() as mocked:
        mocked.get(MUNI_URL_PATTERN, payload=_muni_payload(peril_m=0))
        mocked.get(DEMA_URL_PATTERN, payload=_empty_payload())
        mocked.get(COM_URL_PATTERN, payload=_empty_payload())
        async with aiohttp.ClientSession() as session:
            risk = await fetch_risk(session, LAT, LON, sleep=_noop_sleep)

    assert risk.peril_m == 0
    assert risk.nivell_text == "Baix"
    assert risk.perill_dema is None
    assert risk.data_vigencia is None
    assert risk.hora_vigencia is None


async def test_fetch_risk_no_municipality_raises() -> None:
    """The point does not intersect any municipality polygon (e.g. outside
    Catalonia) -> ArcgisClientError, since there is nothing to report."""
    with aioresponses() as mocked:
        mocked.get(MUNI_URL_PATTERN, payload=_empty_payload())
        async with aiohttp.ClientSession() as session:
            with pytest.raises(ArcgisClientError):
                await fetch_risk(session, LAT, LON, sleep=_noop_sleep)


async def test_fetch_risk_survives_dema_query_failure() -> None:
    """A failing "demà" query (e.g. persistent 5xx) does not fail the whole
    fetch -- it is best-effort (see module docstring)."""

    def dema_callback(url, **kwargs):
        return CallbackResult(status=503, body="upstream error")

    with aioresponses() as mocked:
        mocked.get(MUNI_URL_PATTERN, payload=_muni_payload(peril_m=1))
        mocked.get(DEMA_URL_PATTERN, callback=dema_callback, repeat=True)
        mocked.get(COM_URL_PATTERN, payload=_comarcal_payload())
        async with aiohttp.ClientSession() as session:
            risk = await fetch_risk(session, LAT, LON, sleep=_noop_sleep)

    assert risk.peril_m == 1
    assert risk.perill_dema is None


async def test_fetch_risk_survives_comarcal_query_failure() -> None:
    def comarcal_callback(url, **kwargs):
        return CallbackResult(status=503, body="upstream error")

    with aioresponses() as mocked:
        mocked.get(MUNI_URL_PATTERN, payload=_muni_payload(peril_m=2))
        mocked.get(DEMA_URL_PATTERN, payload=_dema_payload())
        mocked.get(COM_URL_PATTERN, callback=comarcal_callback, repeat=True)
        async with aiohttp.ClientSession() as session:
            risk = await fetch_risk(session, LAT, LON, sleep=_noop_sleep)

    assert risk.peril_m == 2
    assert risk.data_vigencia is None
    assert risk.hora_vigencia is None


# ---------------------------------------------------------------------------
# Retries / errors (municipal "avui" query -- the one that must succeed)
# ---------------------------------------------------------------------------


async def test_5xx_retries_three_times_then_raises() -> None:
    call_count = 0

    def callback(url, **kwargs):
        nonlocal call_count
        call_count += 1
        return CallbackResult(status=503, body="upstream error")

    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    with aioresponses() as mocked:
        mocked.get(MUNI_URL_PATTERN, callback=callback, repeat=True)
        async with aiohttp.ClientSession() as session:
            with pytest.raises(ArcgisClientError):
                await fetch_risk(session, LAT, LON, sleep=fake_sleep)

    assert call_count == MAX_ATTEMPTS == 4
    assert sleeps == list(RETRY_BACKOFFS_SECONDS) == [1, 2, 4]


async def test_5xx_succeeds_after_transient_failures() -> None:
    call_count = 0

    def callback(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return CallbackResult(status=502, body="bad gateway")
        return CallbackResult(status=200, payload=_muni_payload())

    with aioresponses() as mocked:
        mocked.get(MUNI_URL_PATTERN, callback=callback, repeat=True)
        mocked.get(DEMA_URL_PATTERN, payload=_empty_payload())
        mocked.get(COM_URL_PATTERN, payload=_empty_payload())
        async with aiohttp.ClientSession() as session:
            risk = await fetch_risk(session, LAT, LON, sleep=_noop_sleep)

    assert risk.peril_m == 0
    assert call_count == 3


async def test_4xx_raises_immediately_without_retry() -> None:
    call_count = 0
    sleeps: list[float] = []

    def callback(url, **kwargs):
        nonlocal call_count
        call_count += 1
        return CallbackResult(status=404, body="not found")

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    with aioresponses() as mocked:
        mocked.get(MUNI_URL_PATTERN, callback=callback, repeat=True)
        async with aiohttp.ClientSession() as session:
            with pytest.raises(ArcgisClientError):
                await fetch_risk(session, LAT, LON, sleep=fake_sleep)

    assert call_count == 1
    assert sleeps == []


# ---------------------------------------------------------------------------
# Request timeout
# ---------------------------------------------------------------------------


async def test_fetch_query_passes_explicit_timeout() -> None:
    """Every session.get call must pass an explicit timeout so a hung
    connection cannot stall for aiohttp's 300s-per-attempt default."""
    captured_kwargs: dict = {}
    real_get = aiohttp.ClientSession.get

    def spy_get(self, url, **kwargs):
        captured_kwargs.update(kwargs)
        return real_get(self, url, **kwargs)

    with aioresponses() as mocked:
        mocked.get(MUNI_URL_PATTERN, payload=_muni_payload())
        mocked.get(DEMA_URL_PATTERN, payload=_dema_payload())
        mocked.get(COM_URL_PATTERN, payload=_comarcal_payload())
        async with aiohttp.ClientSession() as session:
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(aiohttp.ClientSession, "get", spy_get)
                await fetch_risk(session, LAT, LON, sleep=_noop_sleep)

    assert captured_kwargs.get("timeout") is REQUEST_TIMEOUT


# ---------------------------------------------------------------------------
# No PII (home coordinates) in error messages
# ---------------------------------------------------------------------------


async def test_no_municipality_error_message_has_no_coordinate_digits() -> None:
    """The "no polygon intersects" error is logged at ERROR by
    DataUpdateCoordinator; it must not embed the user's precise home
    coordinates (PII) in home-assistant.log."""
    with aioresponses() as mocked:
        mocked.get(MUNI_URL_PATTERN, payload=_empty_payload())
        async with aiohttp.ClientSession() as session:
            with pytest.raises(ArcgisClientError) as exc_info:
                await fetch_risk(session, LAT, LON, sleep=_noop_sleep)

    message = str(exc_info.value)
    assert not any(char.isdigit() for char in message)


# ---------------------------------------------------------------------------
# PERIL_M missing -> warning logged, still defaults to 0
# ---------------------------------------------------------------------------


async def test_missing_peril_m_logs_warning_and_defaults_to_zero(
    caplog: pytest.LogCaptureFixture,
) -> None:
    payload = {
        "features": [
            {
                "attributes": {
                    "CODIMUNI": "080193",
                    "NOMMUNI": "Barcelona",
                    "NOMCOMAR": "Barcelonès",
                    "PERIL_M": None,
                }
            }
        ]
    }
    with aioresponses() as mocked:
        mocked.get(MUNI_URL_PATTERN, payload=payload)
        mocked.get(DEMA_URL_PATTERN, payload=_empty_payload())
        mocked.get(COM_URL_PATTERN, payload=_empty_payload())
        async with aiohttp.ClientSession() as session:
            with caplog.at_level("WARNING"):
                risk = await fetch_risk(session, LAT, LON, sleep=_noop_sleep)

    assert risk.peril_m == 0
    assert any("PERIL_M" in record.message for record in caplog.records)
