"""Async client for the Bombers de Catalunya ArcGIS FeatureServer.

Wraps `aiohttp` (already a Home Assistant core dependency, per
docs/04-architecture.md §2 — `requirements: []`). Responsibilities:

1. Paginated query — walk the whole dataset via `resultOffset` until
   `exceededTransferLimit` is false.
2. Incremental sync — when `since` is given, filter to rows updated after
   that timestamp and fetch them in chronological order.
3. De-dup — the view is an append-only snapshot log: one
   `ACT_NUM_ACTUACIO` can have 2+ rows (see docs/01-data-sources.md §2). We
   collapse to one row per incident, keeping the one with the highest
   `DATA_ACT` (falling back to `EditDate` to break ties).
4. Retries — timeout/5xx get 3 retries with exponential backoff (1s/2s/4s);
   4xx errors are not retried (they usually mean the schema/URL changed).

Deviation from docs/04-architecture.md §3 (found via live query, see
tests/test_arcgis.py and the implementation report): the architecture sketch
filters/orders incremental queries by `EditDate`. Against the real
FeatureServer, `EditDate` is declared in `editFieldsInfo` but is **not** a
queryable field — it is silently dropped from `outFields=*` and using it in
`where`/`orderByFields` returns an HTTP 400 ("Invalid orderByFields"). We use
`DATA_ACT` instead, which is present on every row, already the field used
for de-dup, and works correctly for filtering/ordering.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import aiohttp

from .const import BOMBERS_LIVE_URL
from .models import Incident

_LOGGER = logging.getLogger(__name__)

PAGE_SIZE = 2000

# Hard cap on `fetch_incidents`' pagination loop: 20 pages * PAGE_SIZE rows =
# 40k rows, far above the real dataset's size. Without this, a server that
# always reports `exceededTransferLimit=true` (a live-service bug, or a
# schema change we haven't accounted for) would make the loop paginate
# forever, growing `raw_features` without bound until the process OOMs.
MAX_PAGES = 20

# Request-level timeout for every `session.get` call. Without an explicit
# timeout, aiohttp defaults to a 300s total timeout *per attempt*; combined
# with MAX_ATTEMPTS retries that is up to ~20 minutes before a hung
# connection is ever reported as a failure.
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)

# 1 initial attempt + up to 3 retries, with exponential backoff between them.
RETRY_BACKOFFS_SECONDS: tuple[float, ...] = (1, 2, 4)
MAX_ATTEMPTS = len(RETRY_BACKOFFS_SECONDS) + 1

# Body text embedded in ArcgisClientError messages (4xx responses) is
# truncated to this many characters: the raw body flows unbounded into the
# Repairs UI placeholder and diagnostics, and 4xx bodies are sometimes full
# HTML error pages.
MAX_ERROR_BODY_CHARS = 200

_SleepFn = Callable[[float], Awaitable[None]]


def _truncate_body(body: str) -> str:
    """Truncate an HTTP response body for safe embedding in an error message."""
    if len(body) <= MAX_ERROR_BODY_CHARS:
        return body
    return f"{body[:MAX_ERROR_BODY_CHARS]}…"


class ArcgisClientError(Exception):
    """Raised when the ArcGIS FeatureServer request fails unrecoverably.

    `status`/`kind` (Task 13, docs/04-architecture.md §9) let callers
    classify a failure without parsing the message string — used by
    `coordinator.py` to (a) derive `sensor.last_update_status` and (b) count
    consecutive schema/URL-change-signature failures towards
    `bomberscat_service_degraded`. `kind` is one of:

    - `"http_404"` / `"http_4xx"`: a 4xx response (no retry attempted).
      `404` gets its own bucket since it is the specific "URL changed"
      signature the resilience table calls out; other 4xx codes are less
      diagnostic on their own but still count as the same "not-a-network-
      problem" class of failure.
    - `"http_5xx"`: every retry attempt got a 5xx response.
    - `"timeout"`: every retry attempt timed out or hit a network-level
      error (connection refused, DNS, ...) — grouped together per the
      architecture doc's "Timeout / xarxa" resilience-table row.
    - `"parse"`: the response body was not valid JSON.
    - `"unknown"` (default): anything else, including errors raised without
      an explicit `kind` (e.g. plain `ArcgisClientError("boom")` in tests).
    """

    def __init__(
        self, message: str, *, status: int | None = None, kind: str = "unknown"
    ) -> None:
        super().__init__(message)
        self.status = status
        self.kind = kind


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _since_where_clause(since: datetime) -> str:
    """Build the incremental `where` clause for `DATA_ACT > since`."""
    if since.tzinfo is not None:
        since = since.astimezone(UTC).replace(tzinfo=None)
    # Truncation to whole seconds is deliberate: it slightly widens the
    # re-fetch window rather than narrowing it, and `_dedupe_features` makes
    # re-fetching an already-seen row harmless.
    timestamp = since.strftime("%Y-%m-%d %H:%M:%S")
    return f"DATA_ACT > TIMESTAMP '{timestamp}'"


async def _fetch_page(
    session: aiohttp.ClientSession,
    *,
    where: str,
    out_sr: int,
    offset: int,
    order_by: str | None,
    sleep: _SleepFn,
) -> dict[str, Any]:
    """Fetch a single page, retrying on timeout/network/5xx errors."""
    params: dict[str, Any] = {
        "where": where,
        "outFields": "*",
        "outSR": out_sr,
        "f": "geojson",
        "resultRecordCount": PAGE_SIZE,
        "resultOffset": offset,
    }
    if order_by:
        params["orderByFields"] = order_by

    query_url = f"{BOMBERS_LIVE_URL}/query"
    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            async with session.get(
                query_url, params=params, timeout=REQUEST_TIMEOUT
            ) as resp:
                if 400 <= resp.status < 500:
                    body = await resp.text()
                    kind = "http_404" if resp.status == 404 else "http_4xx"
                    raise ArcgisClientError(
                        f"ArcGIS FeatureServer client error {resp.status}: "
                        f"{_truncate_body(body)}",
                        status=resp.status,
                        kind=kind,
                    )
                if resp.status >= 500:
                    body = await resp.text()
                    raise aiohttp.ClientResponseError(
                        resp.request_info,
                        resp.history,
                        status=resp.status,
                        message=body,
                    )
                try:
                    return await resp.json(content_type=None)
                except ValueError as err:
                    # Invalid JSON body (docs/04-architecture.md §9: "Log,
                    # conserva cache") — surface immediately, like 4xx: a
                    # malformed response is not something a retry would fix.
                    raise ArcgisClientError(
                        f"ArcGIS FeatureServer returned invalid JSON: {err}",
                        kind="parse",
                    ) from err
        except ArcgisClientError:
            # 4xx / parse errors: no retry, surface immediately.
            raise
        except (TimeoutError, aiohttp.ClientError) as err:
            last_error = err
            if attempt < len(RETRY_BACKOFFS_SECONDS):
                _LOGGER.warning(
                    "ArcGIS FeatureServer request failed (attempt %d/%d): %s",
                    attempt + 1,
                    MAX_ATTEMPTS,
                    err,
                )
                await sleep(RETRY_BACKOFFS_SECONDS[attempt])
                continue

    status: int | None = None
    kind = "timeout"
    if isinstance(last_error, aiohttp.ClientResponseError) and (
        last_error.status is not None and last_error.status >= 500
    ):
        status = last_error.status
        kind = "http_5xx"
    raise ArcgisClientError(
        f"ArcGIS FeatureServer unreachable after {MAX_ATTEMPTS} attempts: {last_error}",
        status=status,
        kind=kind,
    ) from last_error


def _dedupe_features(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse the append-only snapshot log to one row per `ACT_NUM_ACTUACIO`.

    Keeps the row with the highest `DATA_ACT`, falling back to `EditDate` to
    break ties (or when `DATA_ACT` is missing).
    """
    best: dict[str, dict[str, Any]] = {}
    best_sort_key: dict[str, tuple[int, int]] = {}

    for feature in features:
        props = feature.get("properties") or {}
        act_num = props.get("ACT_NUM_ACTUACIO")
        if not act_num:
            continue
        sort_key = (props.get("DATA_ACT") or 0, props.get("EditDate") or 0)
        if act_num not in best or sort_key > best_sort_key[act_num]:
            best[act_num] = feature
            best_sort_key[act_num] = sort_key

    return [best[act_num] for act_num in sorted(best)]


async def fetch_incidents(
    session: aiohttp.ClientSession,
    since: datetime | None = None,
    out_sr: int = 4326,
    *,
    sleep: _SleepFn = _default_sleep,
) -> list[Incident]:
    """Fetch all incidents new/modified since `since` (or the whole dataset).

    Paginates via `resultOffset` until `exceededTransferLimit` is false, then
    de-dups the append-only snapshot log before converting to `Incident`.
    """
    where = "1=1" if since is None else _since_where_clause(since)
    order_by = "DATA_ACT ASC" if since is not None else None

    offset = 0
    raw_features: list[dict[str, Any]] = []
    for _page in range(MAX_PAGES):
        data = await _fetch_page(
            session,
            where=where,
            out_sr=out_sr,
            offset=offset,
            order_by=order_by,
            sleep=sleep,
        )
        raw_features.extend(data.get("features", []))
        exceeded = (data.get("properties") or {}).get("exceededTransferLimit", False)
        if not exceeded:
            break
        offset += PAGE_SIZE
    else:
        # Every one of MAX_PAGES pages reported exceededTransferLimit=true:
        # either the dataset has genuinely exploded far beyond anything seen
        # in practice, or the server is stuck always reporting the flag as
        # true. Either way, continuing to paginate is not safe (unbounded
        # memory growth) -- surface it as an error instead of looping
        # forever.
        raise ArcgisClientError(
            f"ArcGIS FeatureServer still reports exceededTransferLimit after "
            f"{MAX_PAGES} pages ({MAX_PAGES * PAGE_SIZE} rows); aborting to "
            "avoid unbounded pagination",
            kind="unknown",
        )

    return [Incident.from_feature(f) for f in _dedupe_features(raw_features)]
