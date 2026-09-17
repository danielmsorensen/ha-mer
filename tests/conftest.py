"""Pytest configuration for the Mer integration tests."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer.const import (
    CONF_BASE_URL,
    CONF_SCAN_INTERVAL,
    CONF_SITE_ID,
    CONF_SITE_NAME,
    CONF_STATION_IDS,
    DEFAULT_BASE_URL,
    DOMAIN,
)
from custom_components.mer.driivz.models import (
    SessionEstimate,
    Site,
    Socket,
    Transaction,
    Wallet,
)
from tests.helpers import load_json_fixture, station_from_fixture, stations_from_fixture

SITE_NAME = "Durham County Council - Business Durham NETPark"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom integrations in every test."""
    return


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A configured entry monitoring Explorer 1 and 2."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"Mer - {SITE_NAME}",
        unique_id="user@example.com",
        data={
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "secret",
            CONF_BASE_URL: DEFAULT_BASE_URL,
        },
        options={
            CONF_SITE_ID: 2877,
            CONF_SITE_NAME: SITE_NAME,
            CONF_STATION_IDS: [6042, 6041],
            CONF_SCAN_INTERVAL: 60,
        },
    )


def _details() -> dict[int, object]:
    return {
        6042: station_from_fixture("station_6042.json"),
        6041: station_from_fixture("station_6041.json"),
        17886: station_from_fixture("station_17886.json"),
    }


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """Patch the Driivz client class so create_client returns this mock."""
    details = _details()
    with patch("custom_components.mer.api.DriivzDriverClient") as cls:
        client = cls.return_value
        client.rate_limit_remaining = 9
        client.logged_in = True
        client.login = AsyncMock()
        client.find_sites_in_bounds = AsyncMock(
            return_value=[
                Site.from_dict(s) for s in load_json_fixture("sites_in_bounds.json")["data"]
            ]
        )
        client.find_stations_in_bounds = AsyncMock(
            return_value=stations_from_fixture("stations_in_bounds.json")
        )
        client.find_stations_by_ids = AsyncMock(
            return_value=stations_from_fixture("stations_by_ids.json")
        )
        client.find_station_by_id = AsyncMock(
            side_effect=lambda station_id, billing_plan_id=None: details[station_id]
        )
        client.find_last_active_charge_socket = AsyncMock(return_value=None)
        client.find_current_transaction_start_time = AsyncMock(
            return_value=datetime(2026, 9, 16, 10, 34, 26, tzinfo=UTC)
        )
        client.find_current_transaction_estimate = AsyncMock(
            return_value=SessionEstimate(energy_kwh=12.345, cost=0.0, currency="GBP")
        )
        client.start_charge = AsyncMock()
        client.stop_charge = AsyncMock()
        client.find_wallet = AsyncMock(
            return_value=Wallet.from_dict(load_json_fixture("wallet.json")["data"])
        )
        client.find_transactions = AsyncMock(
            return_value=[
                Transaction.from_dict(t) for t in load_json_fixture("transactions.json")["data"]
            ]
        )
        yield client


@pytest.fixture
def charging_socket() -> Socket:
    """The socket DTO returned while a charge is running."""
    return Socket.from_dict(load_json_fixture("last_active_charging.json")["data"])
