"""Tests for integration setup and unload."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.mer import async_remove_config_entry_device
from custom_components.mer.const import DOMAIN
from custom_components.mer.driivz.exceptions import AuthError, DriivzConnectionError
from tests.helpers import setup_integration


async def test_setup_and_unload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.LOADED
    mock_client.login.assert_awaited_once()
    mock_client.find_stations_by_ids.assert_awaited_with([6042, 6041])
    coordinator = mock_config_entry.runtime_data
    assert set(coordinator.data.stations) == {6042, 6041}
    assert coordinator.data.details[6042].sockets[0].name == "Left"
    assert coordinator.data.customer_id == 123456

    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_auth_error_triggers_reauth(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    mock_client.login.side_effect = AuthError("BAD_CREDENTIALS")
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"].get("source") == "reauth" for f in flows)


async def test_setup_connection_error_retries(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    mock_client.login.side_effect = DriivzConnectionError("boom")
    await setup_integration(hass, mock_config_entry)
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.xfail(reason="entities added in Task 7", strict=True)
async def test_devices_created_and_stale_station_removable(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, mock_client: MagicMock
) -> None:
    await setup_integration(hass, mock_config_entry)
    registry = dr.async_get(hass)
    site = registry.async_get_device(identifiers={(DOMAIN, "site_2877")})
    station = registry.async_get_device(identifiers={(DOMAIN, "station_6042")})
    account = registry.async_get_device(
        identifiers={(DOMAIN, f"account_{mock_config_entry.entry_id}")}
    )
    assert site is not None and site.name == "Durham County Council - Business Durham NETPark"
    assert station is not None
    assert station.name == "Business Durham - NETPark 3 - Explorer 1"
    assert station.model == "Eve Double Pro-line"
    assert station.via_device_id == site.id
    assert account is not None

    stale = registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id, identifiers={(DOMAIN, "station_999")}
    )
    assert await async_remove_config_entry_device(hass, mock_config_entry, stale) is True
    assert await async_remove_config_entry_device(hass, mock_config_entry, station) is False
    assert await async_remove_config_entry_device(hass, mock_config_entry, site) is False
