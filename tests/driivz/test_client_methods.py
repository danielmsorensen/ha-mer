"""Tests for the typed DriivzDriverClient methods.

Translated from an `aioresponses`-based spec (see the Task 4 brief's amendment) to
`AiohttpClientMocker`, following the pattern in tests/driivz/test_client_core.py.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.mer.driivz.client import DriivzDriverClient
from custom_components.mer.driivz.exceptions import ApiError
from custom_components.mer.driivz.models import Bounds
from tests.helpers import load_fixture, load_json_fixture

BASE = "https://driver.uk.mer.eco"
LOGIN_URL = f"{BASE}/login"
MAP_URL = f"{BASE}/findCharger"


def url(path: str) -> str:
    return f"{BASE}/{path}"


@pytest.fixture
def aioclient_mock() -> AiohttpClientMocker:
    return AiohttpClientMocker()


@pytest.fixture
async def session(aioclient_mock: AiohttpClientMocker) -> AsyncIterator[aiohttp.ClientSession]:
    s = aioclient_mock.create_session(asyncio.get_running_loop())
    yield s
    await s.close()


@pytest.fixture
async def client(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker
) -> DriivzDriverClient:
    aioclient_mock.get(LOGIN_URL, text=load_fixture("login.html"))
    aioclient_mock.post(LOGIN_URL, json=load_json_fixture("login_success.json"))
    aioclient_mock.get(MAP_URL, text=load_fixture("map.html"))
    c = DriivzDriverClient(session, "user@example.com", "secret")
    await c.login()
    return c


def _calls(m: AiohttpClientMocker, method: str, path: str) -> list[tuple[str, Any, Any, Any]]:
    """Return recorded calls to `path`, matched by exact (query-less) URL."""
    target = url(path)
    return [call for call in m.mock_calls if call[0] == method and str(call[1]) == target]


def _responses_in_order(
    *specs: dict[str, Any],
) -> Callable[[str, Any, Any], Awaitable[AiohttpClientMockResponse]]:
    """Return a side_effect that replays `specs` in order, then repeats the last one.

    `AiohttpClientMocker` matchers are not consumed, so a URL that must answer differently
    across successive calls (e.g. idle then charging) needs this instead of two separate
    registrations.
    """
    calls_made = 0

    async def side_effect(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        nonlocal calls_made
        index = min(calls_made, len(specs) - 1)
        calls_made += 1
        return AiohttpClientMockResponse(method=method, url=url, **specs[index])

    return side_effect


async def test_find_sites_in_bounds(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    path = "stationFacade/findSitesInBounds"
    aioclient_mock.post(url(path), json=load_json_fixture("sites_in_bounds.json"))
    sites = await client.find_sites_in_bounds(Bounds.UK)
    calls = _calls(aioclient_mock, "POST", path)
    assert [s.id for s in sites] == [2877, 3796, 1359]
    # The recorded body is the real (non-stringified) payload, which proves it went out as a
    # JSON body rather than being form-encoded through `_form` (see test_json_body_sets_json_kwarg
    # in test_client_core.py for the same reasoning).
    assert calls[0][2] == {"filterByBounds": Bounds.UK.to_dict(), "filterByIsManaged": True}


async def test_find_stations_in_bounds(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    path = "stationFacade/findStationsInBounds"
    aioclient_mock.post(url(path), json=load_json_fixture("stations_in_bounds.json"))
    stations = await client.find_stations_in_bounds(Bounds.around(54.67, -1.45))
    assert [s.id for s in stations] == [6042, 6041, 17886]
    assert stations[1].status == "CHARGING"


async def test_find_stations_by_ids(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    path = "stationFacade/findStationsByIds"
    aioclient_mock.post(url(path), json=load_json_fixture("stations_by_ids.json"))
    stations = await client.find_stations_by_ids([6042, 6041])
    calls = _calls(aioclient_mock, "POST", path)
    # Non-stringified ints in the recorded body confirm this went out as JSON, not form data.
    assert calls[0][2] == {"filterByIds": [6042, 6041]}
    assert stations[0].sockets[0].status == "AVAILABLE"
    assert stations[1].sockets[0].status == "CHARGING"


async def test_find_stations_by_ids_empty_makes_no_request(
    session: aiohttp.ClientSession, aioclient_mock: AiohttpClientMocker
) -> None:
    # `find_stations_by_ids([])` short-circuits before any call to `_request`, so it needs no
    # prior login. Building the client fresh (unauthenticated) here, instead of using the shared
    # already-logged-in `client` fixture, lets this assert a truly empty call list.
    fresh_client = DriivzDriverClient(session, "user@example.com", "secret")
    assert await fresh_client.find_stations_by_ids([]) == []
    assert aioclient_mock.mock_calls == []


async def test_find_station_by_id(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    path = "stationFacade/findStationById"
    aioclient_mock.get(url(path), json=load_json_fixture("station_6042.json"))
    station = await client.find_station_by_id(6042, billing_plan_id=3465)
    calls = [
        call
        for call in aioclient_mock.mock_calls
        if call[0] == "GET" and call[1].path == f"/{path}"
    ]
    assert calls[-1][1].query == {"stationId": "6042", "billingPlanId": "3465"}
    assert station.sockets[0].name == "Left"


async def test_find_station_by_id_missing_is_api_error(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    path = "stationFacade/findStationById"
    aioclient_mock.get(url(path), json={"errors": [], "success": True})
    with pytest.raises(ApiError) as excinfo:
        await client.find_station_by_id(1)
    assert excinfo.value.error_type == "STATION_NOT_FOUND"


async def test_last_active_socket_idle_and_charging(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    path = "stationFacade/findLastActiveChargeSocket"
    aioclient_mock.post(
        url(path),
        side_effect=_responses_in_order(
            {"json": load_json_fixture("last_active_idle.json")},
            {"json": load_json_fixture("last_active_charging.json")},
        ),
    )
    assert await client.find_last_active_charge_socket() is None
    active = await client.find_last_active_charge_socket()
    calls = _calls(aioclient_mock, "POST", path)
    assert active is not None
    # The real findLastActiveChargeSocket payload is for socket 11242, unlike the other
    # transaction fixtures which were captured against socket 11241 -- the request's own
    # socket id is irrelevant to those, so they were left alone.
    assert active.id == 11242
    assert active.station_id == 6041
    assert active.status == "CHARGING"
    # The call sends an empty form dict (`data={}`), not JSON. The mocker records `data or json`
    # and an empty dict is falsy, so it collapses to the same `None` a bodyless call would
    # produce — this can't distinguish "empty form body" from "no body" any more strongly here.
    assert calls[0][2] is None


async def test_transaction_start_time_and_estimate(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    start_path = "stationFacade/findCurrentTransactionStartTime"
    est_path = "stationFacade/findCurrentTransactionBillingChargingEstimation"
    aioclient_mock.post(url(start_path), json=load_json_fixture("transaction_start_time.json"))
    aioclient_mock.post(url(est_path), json=load_json_fixture("transaction_estimate.json"))
    transaction = await client.find_current_transaction(11241)
    estimate = await client.find_current_transaction_estimate(11241)
    start_calls = _calls(aioclient_mock, "POST", start_path)
    est_calls = _calls(aioclient_mock, "POST", est_path)
    assert transaction is not None
    assert transaction.transaction_id == 9088676
    assert transaction.elapsed == timedelta(milliseconds=953622)
    # The fixture's own socket id is irrelevant to the request; the form body is what matters.
    assert start_calls[0][2] == {"stationSocketId": "11241"}
    assert est_calls[0][2] == {"socketId": "11241"}
    assert estimate is not None
    assert estimate.energy_kwh == 1.606
    assert estimate.cost == 0


async def test_estimate_without_data_is_none(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    est_path = "stationFacade/findCurrentTransactionBillingChargingEstimation"
    aioclient_mock.post(url(est_path), json={"errors": [], "success": True})
    assert await client.find_current_transaction_estimate(1) is None


async def test_start_charge_pending_ok(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    path = "stationFacade/startChargeNow"
    aioclient_mock.post(url(path), json=load_json_fixture("start_charge_pending.json"))
    await client.start_charge(11243)
    calls = _calls(aioclient_mock, "POST", path)
    assert calls[0][2] == {"stationSocketId": "11243"}


async def test_start_charge_rejected_raises(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    path = "stationFacade/startChargeNow"
    aioclient_mock.post(url(path), json=load_json_fixture("start_charge_rejected.json"))
    with pytest.raises(ApiError) as excinfo:
        await client.start_charge(11243)
    assert excinfo.value.error_type == "REJECTED"


async def test_start_charge_envelope_error_raises(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    path = "stationFacade/startChargeNow"
    aioclient_mock.post(url(path), json=load_json_fixture("error_insufficient_permissions.json"))
    with pytest.raises(ApiError) as excinfo:
        await client.start_charge(11243)
    assert excinfo.value.error_type == "INSUFFICIENT_PERMISSIONS"


async def test_stop_charge(client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker) -> None:
    path = "stationFacade/stopCharge"
    aioclient_mock.post(
        url(path),
        side_effect=_responses_in_order(
            {"json": {"errors": [], "success": True}},
            {"json": load_json_fixture("start_charge_rejected.json")},
        ),
    )
    await client.stop_charge(11241)
    with pytest.raises(ApiError):
        await client.stop_charge(11241)
    calls = _calls(aioclient_mock, "POST", path)
    assert calls[0][2] == {"stationSocketId": "11241"}


async def test_find_wallet(client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker) -> None:
    path = "billingFacade/findCustomerDetailWalletByCustomerId"
    aioclient_mock.post(url(path), json=load_json_fixture("wallet.json"))
    wallet = await client.find_wallet()
    assert wallet.customer_id == 123456
    assert wallet.balance == 12.5


async def test_find_transactions_sorted_newest_first(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    path = "customerFacade/findDriverChargeTransactionLogByView"
    raw = load_json_fixture("transactions.json")
    raw["data"].reverse()  # oldest first from the server
    start = datetime(2026, 8, 17, tzinfo=UTC)
    end = datetime(2026, 9, 17, tzinfo=UTC)
    aioclient_mock.post(url(path), json=raw)
    txs = await client.find_transactions(123456, start, end)
    calls = _calls(aioclient_mock, "POST", path)
    assert [t.id for t in txs] == [9084600, 9075809]
    # Non-stringified ints in the recorded body confirm this went out as JSON, not form data.
    assert calls[0][2] == {
        "filterByStartedOnFrom": int(start.timestamp() * 1000),
        "filterByStartedOnTo": int(end.timestamp() * 1000),
        "filterByMemberId": 123456,
    }
