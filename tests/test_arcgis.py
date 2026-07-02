"""Tests for arcgis.py: pagination, incremental sync, dedup, retries."""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import aiohttp
import pytest
from aioresponses import CallbackResult, aioresponses
from custom_components.bomberscat.arcgis import (
    MAX_ATTEMPTS,
    RETRY_BACKOFFS_SECONDS,
    ArcgisClientError,
    _dedupe_features,
    _since_where_clause,
    fetch_incidents,
)
from custom_components.bomberscat.const import BOMBERS_LIVE_URL
from custom_components.bomberscat.models import Fase, Tipus

FIXTURES_DIR = Path(__file__).parent / "fixtures"
QUERY_URL_PATTERN = re.compile(re.escape(f"{BOMBERS_LIVE_URL}/query") + r".*")


def _load(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


async def _noop_sleep(_seconds: float) -> None:
    return None


# ---------------------------------------------------------------------------
# Basic parsing
# ---------------------------------------------------------------------------


async def test_fetch_incidents_parses_sample_fixture() -> None:
    sample = _load("featureserver_sample.json")
    raw_act_nums = {f["properties"]["ACT_NUM_ACTUACIO"] for f in sample["features"]}

    with aioresponses() as mocked:
        mocked.get(QUERY_URL_PATTERN, payload=sample)
        async with aiohttp.ClientSession() as session:
            incidents = await fetch_incidents(session)

    # De-dup collapses repeated ACT_NUM_ACTUACIO rows to one per incident.
    assert {i.act_num for i in incidents} == raw_act_nums
    assert len(incidents) == len(raw_act_nums) < len(sample["features"])
    for inc in incidents:
        assert isinstance(inc.lat, float)
        assert isinstance(inc.lon, float)


async def test_fetch_incidents_empty_fixture() -> None:
    empty = _load("featureserver_empty.json")
    with aioresponses() as mocked:
        mocked.get(QUERY_URL_PATTERN, payload=empty)
        async with aiohttp.ClientSession() as session:
            incidents = await fetch_incidents(session)
    assert incidents == []


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


async def test_fetch_incidents_paginates_across_two_pages() -> None:
    page1 = _load("featureserver_page1.json")
    page2 = _load("featureserver_page2.json")
    offsets_seen: list[str] = []

    def callback(url, **kwargs):
        offset = url.query.get("resultOffset", "0")
        offsets_seen.append(offset)
        payload = page1 if offset == "0" else page2
        return CallbackResult(status=200, payload=payload)

    with aioresponses() as mocked:
        mocked.get(QUERY_URL_PATTERN, callback=callback, repeat=True)
        async with aiohttp.ClientSession() as session:
            incidents = await fetch_incidents(session)

    assert offsets_seen == ["0", "2000"]
    assert {i.act_num for i in incidents} == {"100000001", "100000002"}
    by_act_num = {i.act_num: i for i in incidents}
    assert by_act_num["100000001"].tipus == Tipus.FORESTAL
    assert by_act_num["100000002"].tipus == Tipus.URBANA


# ---------------------------------------------------------------------------
# De-dup
# ---------------------------------------------------------------------------


async def test_dedup_keeps_row_with_max_data_act() -> None:
    duplicates = _load("featureserver_duplicates.json")
    with aioresponses() as mocked:
        mocked.get(QUERY_URL_PATTERN, payload=duplicates)
        async with aiohttp.ClientSession() as session:
            incidents = await fetch_incidents(session)

    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.act_num == "999000001"
    # The row with the higher DATA_ACT (1782310000000) wins.
    assert inc.fase == Fase.ACTIU
    assert inc.situacio == "A"
    assert inc.vehicles == 4
    assert inc.municipi == "Test Nou"
    assert inc.data_act == datetime.fromtimestamp(1782310000000 / 1000, tz=UTC)


def test_dedup_helper_prefers_higher_data_act_directly() -> None:
    older = {"properties": {"ACT_NUM_ACTUACIO": "1", "DATA_ACT": 100}}
    newer = {"properties": {"ACT_NUM_ACTUACIO": "1", "DATA_ACT": 200}}
    result = _dedupe_features([older, newer])
    assert result == [newer]
    result_reversed = _dedupe_features([newer, older])
    assert result_reversed == [newer]


def test_dedup_helper_falls_back_to_edit_date_on_tie() -> None:
    a = {"properties": {"ACT_NUM_ACTUACIO": "1", "DATA_ACT": 100, "EditDate": 50}}
    b = {"properties": {"ACT_NUM_ACTUACIO": "1", "DATA_ACT": 100, "EditDate": 999}}
    result = _dedupe_features([a, b])
    assert result == [b]


# ---------------------------------------------------------------------------
# Incremental sync
# ---------------------------------------------------------------------------


def test_since_where_clause_format() -> None:
    since = datetime(2026, 7, 1, 12, 30, 0, tzinfo=UTC)
    assert _since_where_clause(since) == "DATA_ACT > TIMESTAMP '2026-07-01 12:30:00'"


def test_since_where_clause_naive_datetime() -> None:
    since = datetime(2026, 7, 1, 12, 30, 0)
    assert _since_where_clause(since) == "DATA_ACT > TIMESTAMP '2026-07-01 12:30:00'"


async def test_fetch_incidents_incremental_sends_expected_query() -> None:
    empty = _load("featureserver_empty.json")
    captured: dict = {}

    def callback(url, **kwargs):
        captured["where"] = url.query.get("where")
        captured["orderByFields"] = url.query.get("orderByFields")
        return CallbackResult(status=200, payload=empty)

    since = datetime(2026, 7, 1, 0, 0, 0, tzinfo=UTC)
    with aioresponses() as mocked:
        mocked.get(QUERY_URL_PATTERN, callback=callback)
        async with aiohttp.ClientSession() as session:
            await fetch_incidents(session, since=since)

    assert captured["where"] == "DATA_ACT > TIMESTAMP '2026-07-01 00:00:00'"
    assert captured["orderByFields"] == "DATA_ACT ASC"


async def test_fetch_incidents_full_sync_query() -> None:
    empty = _load("featureserver_empty.json")
    captured: dict = {}

    def callback(url, **kwargs):
        captured["where"] = url.query.get("where")
        captured["orderByFields"] = url.query.get("orderByFields")
        return CallbackResult(status=200, payload=empty)

    with aioresponses() as mocked:
        mocked.get(QUERY_URL_PATTERN, callback=callback)
        async with aiohttp.ClientSession() as session:
            await fetch_incidents(session)

    assert captured["where"] == "1=1"
    assert captured["orderByFields"] is None


# ---------------------------------------------------------------------------
# Retries / errors
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
        mocked.get(QUERY_URL_PATTERN, callback=callback, repeat=True)
        async with aiohttp.ClientSession() as session:
            with pytest.raises(ArcgisClientError):
                await fetch_incidents(session, sleep=fake_sleep)

    assert call_count == MAX_ATTEMPTS == 4
    assert sleeps == list(RETRY_BACKOFFS_SECONDS) == [1, 2, 4]


async def test_5xx_succeeds_after_transient_failures() -> None:
    sample = _load("featureserver_empty.json")
    call_count = 0

    def callback(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            return CallbackResult(status=502, body="bad gateway")
        return CallbackResult(status=200, payload=sample)

    with aioresponses() as mocked:
        mocked.get(QUERY_URL_PATTERN, callback=callback, repeat=True)
        async with aiohttp.ClientSession() as session:
            incidents = await fetch_incidents(session, sleep=_noop_sleep)

    assert incidents == []
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
        mocked.get(QUERY_URL_PATTERN, callback=callback, repeat=True)
        async with aiohttp.ClientSession() as session:
            with pytest.raises(ArcgisClientError):
                await fetch_incidents(session, sleep=fake_sleep)

    assert call_count == 1
    assert sleeps == []


# ---------------------------------------------------------------------------
# ArcgisClientError.status/.kind classification (Task 13)
# ---------------------------------------------------------------------------


async def test_404_error_carries_http_404_kind_and_status() -> None:
    with aioresponses() as mocked:
        mocked.get(QUERY_URL_PATTERN, status=404, body="not found", repeat=True)
        async with aiohttp.ClientSession() as session:
            with pytest.raises(ArcgisClientError) as exc_info:
                await fetch_incidents(session, sleep=_noop_sleep)

    assert exc_info.value.kind == "http_404"
    assert exc_info.value.status == 404


async def test_other_4xx_error_carries_http_4xx_kind() -> None:
    with aioresponses() as mocked:
        mocked.get(QUERY_URL_PATTERN, status=400, body="bad request", repeat=True)
        async with aiohttp.ClientSession() as session:
            with pytest.raises(ArcgisClientError) as exc_info:
                await fetch_incidents(session, sleep=_noop_sleep)

    assert exc_info.value.kind == "http_4xx"
    assert exc_info.value.status == 400


async def test_exhausted_5xx_retries_carry_http_5xx_kind_and_status() -> None:
    with aioresponses() as mocked:
        mocked.get(QUERY_URL_PATTERN, status=503, body="upstream error", repeat=True)
        async with aiohttp.ClientSession() as session:
            with pytest.raises(ArcgisClientError) as exc_info:
                await fetch_incidents(session, sleep=_noop_sleep)

    assert exc_info.value.kind == "http_5xx"
    assert exc_info.value.status == 503


async def test_network_error_carries_timeout_kind() -> None:
    with aioresponses() as mocked:
        mocked.get(
            QUERY_URL_PATTERN,
            exception=aiohttp.ClientConnectionError("boom"),
            repeat=True,
        )
        async with aiohttp.ClientSession() as session:
            with pytest.raises(ArcgisClientError) as exc_info:
                await fetch_incidents(session, sleep=_noop_sleep)

    assert exc_info.value.kind == "timeout"
    assert exc_info.value.status is None


async def test_invalid_json_carries_parse_kind_without_retry() -> None:
    call_count = 0

    def callback(url, **kwargs):
        nonlocal call_count
        call_count += 1
        return CallbackResult(status=200, body="not valid json{")

    with aioresponses() as mocked:
        mocked.get(QUERY_URL_PATTERN, callback=callback, repeat=True)
        async with aiohttp.ClientSession() as session:
            with pytest.raises(ArcgisClientError) as exc_info:
                await fetch_incidents(session, sleep=_noop_sleep)

    assert exc_info.value.kind == "parse"
    assert call_count == 1
