"""Pytest configuration for the Mer integration tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import patch

from homeassistant.config_entries import ConfigSubentryData
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
    SUBENTRY_TYPE_SITE,
)
from tests.fake_portal import FakePortal

SITE_NAME = "Example Council - Riverside Business Park"
SITE_ID = 2877
STATION_NAMES = {
    6042: "Riverside - Bay 3 - Charger A",
    6041: "Riverside - Bay 4 - Charger B",
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading custom integrations in every test."""
    return


@pytest.fixture(autouse=True)
def instant_capability_refresh() -> Generator[None]:
    """Capability re-reads normally wait a few seconds to batch status changes."""
    with patch("custom_components.mer.coordinator.CAPABILITY_REFRESH_DELAY_SECONDS", 0):
        yield


def site_subentry(station_ids: list[int]) -> ConfigSubentryData:
    """The Riverside site subentry monitoring the given chargers, as the flow stores it."""
    return ConfigSubentryData(
        data={CONF_SITE_ID: SITE_ID, CONF_SITE_NAME: SITE_NAME, CONF_STATION_IDS: station_ids},
        subentry_type=SUBENTRY_TYPE_SITE,
        title=SITE_NAME,
        unique_id=f"site_{SITE_ID}",
    )


def make_config_entry(
    station_ids: list[int], username: str = "user@example.com"
) -> MockConfigEntry:
    """An account entry; the Riverside site subentry is present when station ids are given."""
    return MockConfigEntry(
        domain=DOMAIN,
        version=3,
        title=username,
        unique_id=username,
        data={
            CONF_USERNAME: username,
            CONF_PASSWORD: "secret",
            CONF_BASE_URL: DEFAULT_BASE_URL,
        },
        options={CONF_SCAN_INTERVAL: 60},
        subentries_data=[site_subentry(station_ids)] if station_ids else [],
    )


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """A configured account monitoring Charger A and 2."""
    return make_config_entry([6042, 6041])


@pytest.fixture
def portal() -> Generator[FakePortal]:
    """The portal every client created in the test talks to."""
    fake = FakePortal()
    with patch("custom_components.mer.api.DriivzDriverClient", return_value=fake):
        yield fake
