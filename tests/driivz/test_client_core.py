"""Tests for DriivzDriverClient login and request plumbing."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import aiohttp
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.mer.driivz.client import DriivzDriverClient, extract_csrf
from custom_components.mer.driivz.exceptions import (
    ApiError,
    AuthError,
    DriivzConnectionError,
    RateLimitError,
)
from tests.helpers import load_fixture, load_json_fixture

BASE = "https://driver.uk.mer.eco"
LOGIN_URL = f"{BASE}/login"
MAP_URL = f"{BASE}/findCharger"
WALLET_PATH = "billingFacade/findCustomerDetailWalletByCustomerId"
WALLET_URL = f"{BASE}/{WALLET_PATH}"


@pytest.fixture
def aioclient_mock() -> AiohttpClientMocker:
    return AiohttpClientMocker()


@pytest.fixture
async def session(aioclient_mock: AiohttpClientMocker) -> AsyncIterator[aiohttp.ClientSession]:
    s = aioclient_mock.create_session(asyncio.get_running_loop())
    yield s
    await s.close()


@pytest.fixture
def client(session: aiohttp.ClientSession) -> DriivzDriverClient:
    return DriivzDriverClient(session, "user@example.com", "secret")


def mock_login(m: AiohttpClientMocker, *, success: bool = True) -> None:
    m.get(LOGIN_URL, text=load_fixture("login.html"))
    name = "login_success.json" if success else "login_failure.json"
    m.post(LOGIN_URL, json=load_json_fixture(name))
    if success:
        m.get(MAP_URL, text=load_fixture("map.html"))


def _login_posts(m: AiohttpClientMocker) -> list[tuple[str, Any, Any, Any]]:
    return [call for call in m.mock_calls if call[0] == "POST" and str(call[1]) == LOGIN_URL]


def _responses_in_order(
    *specs: dict[str, Any],
) -> Callable[[str, Any, Any], Awaitable[AiohttpClientMockResponse]]:
    """Return a side_effect that replays `specs` in order, then repeats the last one.

    `AiohttpClientMocker` matchers are not consumed, so a URL that must answer differently
    across successive calls (e.g. 403 then success on re-login) needs this instead of two
    separate registrations.
    """
    calls_made = 0

    async def side_effect(method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        nonlocal calls_made
        index = min(calls_made, len(specs) - 1)
        calls_made += 1
        return AiohttpClientMockResponse(method=method, url=url, **specs[index])

    return side_effect


def test_extract_csrf() -> None:
    assert extract_csrf(load_fixture("login.html")) == "4236635a-aea4-47ba-a1e6-64c6d5988dff"
    assert extract_csrf('<meta content="abc" name="_csrf">') == "abc"
    assert extract_csrf("<html></html>") is None


async def test_login_posts_form_with_csrf_and_headers(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_login(aioclient_mock)
    await client.login()
    _, _, post_data, post_headers = _login_posts(aioclient_mock)[0]
    assert client.logged_in is True
    assert post_data == {
        "username": "user@example.com",
        "password": "secret",
        "_spring_security_remember_me": "true",
        "_csrf": "4236635a-aea4-47ba-a1e6-64c6d5988dff",
    }
    assert post_headers["X-CSRF-TOKEN"] == "4236635a-aea4-47ba-a1e6-64c6d5988dff"
    assert post_headers["X-APP-TYPE"] == "WEB"
    assert post_headers["X-Ajax-call"] == "true"
    assert post_headers["X-JSON-TYPES"] == "None"
    # token is refreshed from the map page after login
    assert client._csrf == "38724756-f55f-4ce6-af0f-13b795f84006"


async def test_login_failure_raises_auth_error(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_login(aioclient_mock, success=False)
    with pytest.raises(AuthError) as excinfo:
        await client.login()
    assert excinfo.value.error_type == "BAD_CREDENTIALS"
    assert client.logged_in is False


async def test_login_without_csrf_meta_is_connection_error(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(LOGIN_URL, text="<html>maintenance</html>")
    with pytest.raises(DriivzConnectionError):
        await client.login()


async def test_request_returns_data_and_tracks_rate_limit(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_login(aioclient_mock)
    aioclient_mock.post(
        WALLET_URL,
        json=load_json_fixture("wallet.json"),
        headers={"X-Rate-Limit-Remaining": "7"},
    )
    await client.login()
    data = await client._request("POST", WALLET_PATH, data={})
    assert data["customerDetailId"] == 123456
    assert client.rate_limit_remaining == 7


async def test_request_maps_success_false_to_api_error(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_login(aioclient_mock)
    aioclient_mock.post(WALLET_URL, json=load_json_fixture("error_insufficient_permissions.json"))
    await client.login()
    with pytest.raises(ApiError) as excinfo:
        await client._request("POST", WALLET_PATH, data={})
    assert excinfo.value.error_type == "INSUFFICIENT_PERMISSIONS"
    assert "85A05724" in (excinfo.value.message_key or "")


async def test_request_relogins_once_on_403(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_login(aioclient_mock)
    aioclient_mock.post(
        WALLET_URL,
        side_effect=_responses_in_order(
            {"status": 403},
            {"json": load_json_fixture("wallet.json")},
        ),
    )
    await client.login()
    data = await client._request("POST", WALLET_PATH, data={})
    assert data["id"] == 228000
    assert len(_login_posts(aioclient_mock)) == 2


async def test_request_gives_up_after_second_auth_failure(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_login(aioclient_mock)
    aioclient_mock.post(WALLET_URL, status=403)
    await client.login()
    with pytest.raises(AuthError):
        await client._request("POST", WALLET_PATH, data={})


async def test_request_html_page_is_auth_error_when_not_logged_in(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(WALLET_URL, text="<html>login</html>")
    with pytest.raises(AuthError):
        await client._request("POST", WALLET_PATH, data={})


async def test_request_429_is_rate_limit_error(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_login(aioclient_mock)
    aioclient_mock.post(WALLET_URL, status=429)
    await client.login()
    with pytest.raises(RateLimitError):
        await client._request("POST", WALLET_PATH, data={})


async def test_request_network_error_is_connection_error(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_login(aioclient_mock)
    aioclient_mock.post(WALLET_URL, exc=aiohttp.ClientConnectionError("boom"))
    await client.login()
    with pytest.raises(DriivzConnectionError):
        await client._request("POST", WALLET_PATH, data={})


async def test_json_body_sets_json_kwarg(
    client: DriivzDriverClient, aioclient_mock: AiohttpClientMocker
) -> None:
    url = f"{BASE}/stationFacade/findStationsByIds"
    mock_login(aioclient_mock)
    aioclient_mock.post(url, json=load_json_fixture("stations_by_ids.json"))
    await client.login()
    await client._request("POST", "stationFacade/findStationsByIds", json={"filterByIds": [1]})
    calls = [
        call for call in aioclient_mock.mock_calls if call[0] == "POST" and str(call[1]) == url
    ]
    # The mocker records whichever of `data=`/`json=` was actually passed (`data = data or json`).
    # Since we never pass `data=`, seeing the real (non-stringified) payload here proves it went
    # out as a JSON body rather than being form-encoded through `_form`.
    assert calls[-1][2] == {"filterByIds": [1]}


def test_form_stringifies_values() -> None:
    assert DriivzDriverClient._form({"stationSocketId": 11243, "x": "y", "n": None}) == {
        "stationSocketId": "11243",
        "x": "y",
    }
