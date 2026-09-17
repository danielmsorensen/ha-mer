"""Tests for DriivzDriverClient login and request plumbing."""

from __future__ import annotations

import aiohttp
from aioresponses import aioresponses
import pytest
from yarl import URL

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
async def session():
    async with aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar()) as s:
        yield s


@pytest.fixture
def client(session: aiohttp.ClientSession) -> DriivzDriverClient:
    return DriivzDriverClient(session, "user@example.com", "secret")


def mock_login(m: aioresponses, *, success: bool = True) -> None:
    m.get(LOGIN_URL, body=load_fixture("login.html"), content_type="text/html")
    name = "login_success.json" if success else "login_failure.json"
    m.post(LOGIN_URL, payload=load_json_fixture(name))
    if success:
        m.get(MAP_URL, body=load_fixture("map.html"), content_type="text/html")


def test_extract_csrf() -> None:
    assert extract_csrf(load_fixture("login.html")) == "4236635a-aea4-47ba-a1e6-64c6d5988dff"
    assert extract_csrf('<meta content="abc" name="_csrf">') == "abc"
    assert extract_csrf("<html></html>") is None


async def test_login_posts_form_with_csrf_and_headers(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        mock_login(m)
        await client.login()
        post = m.requests[("POST", URL(LOGIN_URL))][0]
    assert client.logged_in is True
    assert post.kwargs["data"] == {
        "username": "user@example.com",
        "password": "secret",
        "_spring_security_remember_me": "true",
        "_csrf": "4236635a-aea4-47ba-a1e6-64c6d5988dff",
    }
    headers = post.kwargs["headers"]
    assert headers["X-CSRF-TOKEN"] == "4236635a-aea4-47ba-a1e6-64c6d5988dff"
    assert headers["X-APP-TYPE"] == "WEB"
    assert headers["X-Ajax-call"] == "true"
    assert headers["X-JSON-TYPES"] == "None"
    # token is refreshed from the map page after login
    assert client._csrf == "38724756-f55f-4ce6-af0f-13b795f84006"


async def test_login_failure_raises_auth_error(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        mock_login(m, success=False)
        with pytest.raises(AuthError) as excinfo:
            await client.login()
    assert excinfo.value.error_type == "BAD_CREDENTIALS"
    assert client.logged_in is False


async def test_login_without_csrf_meta_is_connection_error(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        m.get(LOGIN_URL, body="<html>maintenance</html>", content_type="text/html")
        with pytest.raises(DriivzConnectionError):
            await client.login()


async def test_request_returns_data_and_tracks_rate_limit(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        mock_login(m)
        m.post(
            WALLET_URL,
            payload=load_json_fixture("wallet.json"),
            headers={"X-Rate-Limit-Remaining": "7"},
        )
        await client.login()
        data = await client._request("POST", WALLET_PATH, data={})
    assert data["customerDetailId"] == 123456
    assert client.rate_limit_remaining == 7


async def test_request_maps_success_false_to_api_error(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        mock_login(m)
        m.post(WALLET_URL, payload=load_json_fixture("error_insufficient_permissions.json"))
        await client.login()
        with pytest.raises(ApiError) as excinfo:
            await client._request("POST", WALLET_PATH, data={})
    assert excinfo.value.error_type == "INSUFFICIENT_PERMISSIONS"
    assert "85A05724" in (excinfo.value.message_key or "")


async def test_request_relogins_once_on_403(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        mock_login(m)
        m.post(WALLET_URL, status=403)
        mock_login(m)  # second login
        m.post(WALLET_URL, payload=load_json_fixture("wallet.json"))
        await client.login()
        data = await client._request("POST", WALLET_PATH, data={})
        login_posts = m.requests[("POST", URL(LOGIN_URL))]
    assert data["id"] == 228000
    assert len(login_posts) == 2


async def test_request_gives_up_after_second_auth_failure(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        mock_login(m)
        m.post(WALLET_URL, status=403)
        mock_login(m)
        m.post(WALLET_URL, status=403)
        await client.login()
        with pytest.raises(AuthError):
            await client._request("POST", WALLET_PATH, data={})


async def test_request_html_page_is_auth_error_when_not_logged_in(
    client: DriivzDriverClient,
) -> None:
    with aioresponses() as m:
        m.post(WALLET_URL, body="<html>login</html>", content_type="text/html")
        with pytest.raises(AuthError):
            await client._request("POST", WALLET_PATH, data={})


async def test_request_429_is_rate_limit_error(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        mock_login(m)
        m.post(WALLET_URL, status=429)
        await client.login()
        with pytest.raises(RateLimitError):
            await client._request("POST", WALLET_PATH, data={})


async def test_request_network_error_is_connection_error(client: DriivzDriverClient) -> None:
    with aioresponses() as m:
        mock_login(m)
        m.post(WALLET_URL, exception=aiohttp.ClientConnectionError("boom"))
        await client.login()
        with pytest.raises(DriivzConnectionError):
            await client._request("POST", WALLET_PATH, data={})


async def test_json_body_sets_json_kwarg(client: DriivzDriverClient) -> None:
    url = f"{BASE}/stationFacade/findStationsByIds"
    with aioresponses() as m:
        mock_login(m)
        m.post(url, payload=load_json_fixture("stations_by_ids.json"))
        await client.login()
        await client._request("POST", "stationFacade/findStationsByIds", json={"filterByIds": [1]})
        call = m.requests[("POST", URL(url))][0]
    assert call.kwargs["json"] == {"filterByIds": [1]}
    assert call.kwargs["data"] is None


def test_form_stringifies_values() -> None:
    assert DriivzDriverClient._form({"stationSocketId": 11243, "x": "y", "n": None}) == {
        "stationSocketId": "11243",
        "x": "y",
    }
