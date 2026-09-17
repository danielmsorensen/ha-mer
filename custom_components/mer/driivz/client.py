"""Async client for a Driivz driver portal (Mer UK)."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
import contextlib
from datetime import datetime
import json as jsonlib
import logging
import re
from typing import Any

import aiohttp

from .const import (
    AUTH_ERROR_TYPES,
    DEFAULT_BASE_URL,
    HEADER_AJAX,
    HEADER_APP_TYPE,
    HEADER_CSRF,
    HEADER_JSON_TYPES,
    HEADER_RATE_LIMIT_REMAINING,
    OPERATION_PENDING,
    PATH_FIND_SITES_IN_BOUNDS,
    PATH_FIND_STATION_BY_ID,
    PATH_FIND_STATIONS_BY_IDS,
    PATH_FIND_STATIONS_IN_BOUNDS,
    PATH_LAST_ACTIVE_SOCKET,
    PATH_LOGIN,
    PATH_MAP,
    PATH_START_CHARGE,
    PATH_STOP_CHARGE,
    PATH_TRANSACTION_ESTIMATE,
    PATH_TRANSACTION_START_TIME,
    PATH_TRANSACTIONS,
    PATH_WALLET,
)
from .exceptions import ApiError, AuthError, DriivzConnectionError, RateLimitError
from .models import (
    ActiveTransaction,
    Bounds,
    SessionEstimate,
    Site,
    Socket,
    Station,
    Transaction,
    Wallet,
)

_LOGGER = logging.getLogger(__name__)

_CSRF_META_RE = re.compile(r"<meta\s+[^>]*name=[\"']_csrf[\"'][^>]*>", re.IGNORECASE)
_CONTENT_RE = re.compile(r"content=[\"']([^\"']+)[\"']", re.IGNORECASE)


def extract_csrf(html: str) -> str | None:
    """Return the `_csrf` meta token from a portal HTML page."""
    match = _CSRF_META_RE.search(html)
    if not match:
        return None
    content = _CONTENT_RE.search(match.group(0))
    return content.group(1) if content else None


def _error_type(payload: Mapping[str, Any]) -> str | None:
    errors = payload.get("errors") or []
    if errors and isinstance(errors[0], Mapping) and errors[0].get("errorType"):
        return str(errors[0]["errorType"])
    if payload.get("errorType"):
        return str(payload["errorType"])
    return None


def _message_key(payload: Mapping[str, Any]) -> str | None:
    errors = payload.get("errors") or []
    if errors and isinstance(errors[0], Mapping) and errors[0].get("messageKey"):
        return str(errors[0]["messageKey"])
    return None


def _to_ms(value: datetime) -> int:
    return int(value.timestamp() * 1000)


class DriivzDriverClient:
    """Talks to the driver portal's JSON facades using a cookie session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        base_url: str = DEFAULT_BASE_URL,
    ) -> None:
        self._session = session
        self._username = username
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._csrf: str | None = None
        self._logged_in = False
        self._login_lock = asyncio.Lock()
        self._rate_limit_remaining: int | None = None

    @property
    def logged_in(self) -> bool:
        return self._logged_in

    @property
    def rate_limit_remaining(self) -> int | None:
        return self._rate_limit_remaining

    # ----- plumbing -----------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self._base_url}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        headers = {
            HEADER_APP_TYPE: "WEB",
            HEADER_JSON_TYPES: "None",
            HEADER_AJAX: "true",
            "Accept": "application/json, text/javascript, */*",
        }
        if self._csrf:
            headers[HEADER_CSRF] = self._csrf
        return headers

    @staticmethod
    def _form(values: Mapping[str, Any]) -> dict[str, str]:
        """aiohttp form fields must be strings; drop None values."""
        return {key: str(value) for key, value in values.items() if value is not None}

    def _track_rate_limit(self, response: aiohttp.ClientResponse) -> None:
        value = response.headers.get(HEADER_RATE_LIMIT_REMAINING)
        if value is None:
            return
        with contextlib.suppress(ValueError):
            self._rate_limit_remaining = int(value)

    async def _fetch_csrf(self, path: str) -> str:
        try:
            async with self._session.get(self._url(path), headers=self._headers()) as response:
                self._track_rate_limit(response)
                if response.status >= 400:
                    raise DriivzConnectionError(f"HTTP {response.status} fetching {path}")
                html = await response.text()
        except aiohttp.ClientError as err:
            raise DriivzConnectionError(str(err)) from err
        token = extract_csrf(html)
        if not token:
            raise DriivzConnectionError(f"No CSRF token found on {path}")
        self._csrf = token
        return token

    async def _send(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        data: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """Perform one HTTP call and return the parsed JSON envelope."""
        try:
            async with self._session.request(
                method,
                self._url(path),
                json=json,
                data=None if data is None else self._form(data),
                params=params,
                headers=self._headers(),
            ) as response:
                self._track_rate_limit(response)
                if response.status == 429:
                    raise RateLimitError("Rate limited by portal")
                if response.status in (401, 403):
                    raise AuthError(f"HTTP_{response.status}")
                if response.status >= 400:
                    raise DriivzConnectionError(f"HTTP {response.status} for {path}")
                text = await response.text()
        except aiohttp.ClientError as err:
            raise DriivzConnectionError(str(err)) from err
        try:
            payload = jsonlib.loads(text)
        except ValueError as err:
            if "<html" in text.lower():
                raise AuthError("LOGIN_PAGE") from err
            raise DriivzConnectionError(f"Non-JSON response from {path}") from err
        if not isinstance(payload, dict):
            raise DriivzConnectionError(f"Unexpected response shape from {path}")
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        data: Mapping[str, Any] | None = None,
        params: Mapping[str, str] | None = None,
    ) -> Any:
        """Call a facade, unwrap the envelope, re-login once if the session expired."""
        for attempt in (0, 1):
            try:
                payload = await self._send(method, path, json=json, data=data, params=params)
            except AuthError:
                if attempt == 1 or not self._logged_in:
                    raise
                _LOGGER.debug("Session rejected for %s, logging in again", path)
                await self.login()
                continue
            if payload.get("success", False):
                return payload.get("data")
            error_type = _error_type(payload) or "UNKNOWN_ERROR"
            if error_type in AUTH_ERROR_TYPES:
                if attempt == 1 or not self._logged_in:
                    raise AuthError(error_type)
                await self.login()
                continue
            raise ApiError(error_type, _message_key(payload))
        raise AuthError("LOGIN_FAILED")  # pragma: no cover

    # ----- authentication ------------------------------------------------

    async def login(self) -> None:
        """Authenticate with username/password and prime the CSRF token."""
        async with self._login_lock:
            self._logged_in = False
            token = await self._fetch_csrf(PATH_LOGIN)
            payload = await self._send(
                "POST",
                PATH_LOGIN,
                data={
                    "username": self._username,
                    "password": self._password,
                    "_spring_security_remember_me": "true",
                    "_csrf": token,
                },
            )
            if not payload.get("success", False):
                raise AuthError(_error_type(payload) or "LOGIN_FAILED")
            self._logged_in = True
            await self._fetch_csrf(PATH_MAP)

    # ----- stations and sites -------------------------------------------

    async def find_sites_in_bounds(self, bounds: Bounds) -> list[Site]:
        data = await self._request(
            "POST",
            PATH_FIND_SITES_IN_BOUNDS,
            json={"filterByBounds": bounds.to_dict(), "filterByIsManaged": True},
        )
        return [Site.from_dict(item) for item in data or []]

    async def find_stations_in_bounds(self, bounds: Bounds) -> list[Station]:
        data = await self._request(
            "POST",
            PATH_FIND_STATIONS_IN_BOUNDS,
            json={"filterByBounds": bounds.to_dict(), "filterByIsManaged": True},
        )
        return [Station.from_dict(item) for item in data or []]

    async def find_stations_by_ids(self, ids: Iterable[int]) -> list[Station]:
        id_list = [int(i) for i in ids]
        if not id_list:
            return []
        data = await self._request("POST", PATH_FIND_STATIONS_BY_IDS, json={"filterByIds": id_list})
        return [Station.from_dict(item) for item in data or []]

    async def find_station_by_id(
        self, station_id: int, billing_plan_id: int | None = None
    ) -> Station:
        params = {"stationId": str(station_id)}
        if billing_plan_id is not None:
            params["billingPlanId"] = str(billing_plan_id)
        data = await self._request("GET", PATH_FIND_STATION_BY_ID, params=params)
        if not isinstance(data, Mapping):
            raise ApiError("STATION_NOT_FOUND")
        return Station.from_dict(data)

    # ----- charging session ---------------------------------------------

    async def find_last_active_charge_socket(self) -> Socket | None:
        data = await self._request("POST", PATH_LAST_ACTIVE_SOCKET, data={})
        if isinstance(data, Mapping) and data.get("id") is not None:
            return Socket.from_dict(data)
        return None

    async def find_current_transaction(self, socket_id: int) -> ActiveTransaction | None:
        """The running transaction on this socket, or None when the payload is empty."""
        data = await self._request(
            "POST", PATH_TRANSACTION_START_TIME, data={"stationSocketId": socket_id}
        )
        return ActiveTransaction.from_dict(data) if isinstance(data, Mapping) else None

    async def find_current_transaction_estimate(self, socket_id: int) -> SessionEstimate | None:
        data = await self._request("POST", PATH_TRANSACTION_ESTIMATE, data={"socketId": socket_id})
        if isinstance(data, Mapping):
            return SessionEstimate.from_dict(data)
        return None

    async def start_charge(self, socket_id: int) -> None:
        """Ask the portal to start charging; the charger then waits for the cable."""
        data = await self._request("POST", PATH_START_CHARGE, data={"stationSocketId": socket_id})
        status = data.get("operationStatus") if isinstance(data, Mapping) else None
        if status != OPERATION_PENDING:
            raise ApiError(str(status) if status else "START_CHARGE_REJECTED")

    async def stop_charge(self, socket_id: int) -> None:
        data = await self._request("POST", PATH_STOP_CHARGE, data={"stationSocketId": socket_id})
        status = data.get("operationStatus") if isinstance(data, Mapping) else None
        if status is not None and status != OPERATION_PENDING:
            raise ApiError(str(status))

    # ----- account -------------------------------------------------------

    async def find_wallet(self) -> Wallet:
        data = await self._request("POST", PATH_WALLET, data={})
        if not isinstance(data, Mapping):
            raise ApiError("WALLET_NOT_FOUND")
        return Wallet.from_dict(data)

    async def find_transactions(
        self, customer_id: int, start: datetime, end: datetime
    ) -> list[Transaction]:
        data = await self._request(
            "POST",
            PATH_TRANSACTIONS,
            json={
                "filterByStartedOnFrom": _to_ms(start),
                "filterByStartedOnTo": _to_ms(end),
                "filterByMemberId": customer_id,
            },
        )
        transactions = [Transaction.from_dict(item) for item in data or []]
        transactions.sort(
            key=lambda t: t.started_at.timestamp() if t.started_at else 0.0, reverse=True
        )
        return transactions
